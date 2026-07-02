#!/usr/bin/env bash
# herdr-teardown.sh — herdr tab teardown helpers for work-system /close.
#
# /close removes a finished task's worktree/branch/task-file; inside a herdr
# session it must also tear down that task's herdr **tab**. Two shapes:
#   - Scenario A (/close run from the MAIN session): close the worktree tab
#     directly — a different tab, so no self-kill.
#   - Scenario B (/close run from INSIDE the worktree tab): Claude cannot close
#     its own tab, only exit cleanly. So /close arms a per-pane marker, the
#     plugin's SessionEnd hook (hooks/hooks.json → `on-session-end`) reads it on
#     the clean exit and runs `herdr tab close`. The marker keeps the hook a
#     no-op for every normal exit that did NOT come through /close.
#
# This is the single source of truth for the teardown commands (cwd-matching,
# marker path, herdr calls); skills/close/SKILL.md only branches on its output,
# mirroring the herdr-launch.sh convention. All tab/pane lookups go through
# explicit ids — this script never `cd`s.
#
# Subcommands:
#   worktree-tab <workspace> <worktree-abs-path>
#       Print the tab_id of the pane whose cwd == the worktree path (compared by
#       realpath, so symlinked paths still match). Must run BEFORE
#       `git worktree remove` (afterwards the cwd points at a deleted path).
#       Exit 1 (prints nothing) if herdr is unreachable or no pane matches.
#   worktree-tab-state <workspace> <worktree-abs-path>
#       Tri-state cwd→tab lookup for /continue's reopen guard: prints <tab-id>,
#       `none` (populated list, no exact match, no subtree pane → confidently no tab),
#       or `unverified` (no tools / failed / empty-repopulating / errored list, OR a
#       pane whose cwd is a subdirectory of the worktree → caller must fail closed).
#       Always exit 0. Empty <workspace> searches ALL workspaces.
#   own-tab <workspace> <pane-id>
#       Print the tab_id of the pane with this pane id ($HERDR_PANE_ID). /close
#       compares it to the worktree tab to decide self-close (Scenario B) vs a
#       different-tab close (Scenario A) — a pane-id check, robust to an empty
#       $HERDR_TAB_ID. Exit 1 (prints nothing) if unreachable / no such pane.
#   main-tab <workspace> <main-repo-abs-path> [exclude-tab]
#       Print the tab_id of the pane whose cwd == the main-repo path, so /close
#       can focus it before a self-close. Falls back to the workspace's first
#       pane *other than* exclude-tab (the self tab) so the user is never focused
#       onto the dying tab. Exit 1 only when unreachable or no candidate pane.
#   close-tab <tab-id> [workspace]
#                               Scenario A: close the tab ONCE, then VERIFY it is
#                               gone (polls until gone; does NOT re-issue the close).
#                               Prints one of closed|still-open|unverified on stdout
#                               (always exit 0, even with herdr absent) so /close can
#                               name the tab for a manual close instead of orphaning it.
#   focus-tab <tab-id>          Run `herdr tab focus <tab-id>`.
#   inject-exit <pane-id>       Feed a clean `/exit` into a Claude TUI pane:
#                               `send-text "/exit"` then `send-keys Return`. NOTE
#                               (verified live): `herdr pane run <pane> "/exit"`
#                               does nothing to Claude's TUI (it targets a shell),
#                               and `send-keys ctrl+d` does not exit either — only
#                               this text+Return pair triggers a clean exit. When
#                               Claude is the tab's root pane (kickoff launches it
#                               via `agent start -- claude`), that clean exit also
#                               auto-closes the tab, so the SessionEnd hook is a
#                               safety net, not the primary close.
#   self-exit <pane-id> [workspace]
#                               Scenario B self-close: detach an injector that
#                               waits for this session to go idle (turn ended),
#                               then runs inject-exit — so /exit lands on an idle
#                               prompt, never mid-turn. Returns immediately.
#   arm-self-close <tab-id>     Write the self-close marker for $HERDR_PANE_ID
#                               (timestamp + tab to close on clean exit).
#   on-session-end              Called by the SessionEnd hook. If a *fresh* marker
#                               exists for $HERDR_PANE_ID, close the recorded tab
#                               and remove the marker; otherwise no-op. A marker
#                               older than the TTL is dropped without closing (the
#                               user never did the clean exit). Always exit 0
#                               (fire-and-forget — never break the session exit).
#
# Lookups print `key=value`-free raw ids on stdout; diagnostics go to stderr.
set -eu

# --- marker location (single source of truth) -------------------------------
# Keyed by herdr pane id so a stray marker can only ever affect the pane that
# wrote it. Under a FIXED $HOME/.cache (not $XDG_CACHE_HOME, which may be set in
# the /close shell but not in the SessionEnd hook's env — that divergence would
# hide the marker; not $TMPDIR, which is per-process on macOS), so /close and the
# hook always resolve the same path.
MARKER_TTL=3600   # seconds; a marker older than this is stale → never auto-close
# Fail if $HOME is empty/unset so we never write/read a rootless `/.cache/...`
# the /close shell and the hook wouldn't agree on.
marker_dir() { [ -n "${HOME:-}" ] || return 1; printf '%s/.cache/work-system/herdr' "$HOME"; }
marker_file() {
  local pane="${1:-${HERDR_PANE_ID:-}}" dir
  [ -n "$pane" ] || return 1
  dir="$(marker_dir)" || return 1
  pane="${pane//\//_}"   # sanitize: never build a path component from a raw id
  printf '%s/self-close-%s' "$dir" "$pane"
}

require_herdr() { command -v herdr >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }; }

# Extract a tab_id from `herdr pane list` JSON on stdin.
#   argv: <target-cwd> [--first] [--exclude <tab>]   (cwd matched by realpath)
# Prints the tab of the first pane whose realpath(cwd) == realpath(target), honoring
# --exclude even on that primary match; with --first, falls back to the first pane
# whose tab != --exclude. An empty/whitespace target never matches (realpath("")
# would resolve to the process cwd). Empty output on no match or malformed JSON.
extract_tab='import sys, json, os
def norm(p):
    if not p or not p.strip():
        return ""
    p2 = p.rstrip("/") or "/"   # all-slashes path stays root, never collapses to ""
    return os.path.realpath(p2)
args = sys.argv[1:]
target = norm(args[0]) if args else ""
rest = args[1:]
fallback = "--first" in rest
exclude = ""
for i, a in enumerate(rest):
    if a == "--exclude" and i + 1 < len(rest):
        exclude = rest[i + 1]
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    sys.exit(0)
if target:
    for p in panes:
        t = p.get("tab_id") or ""
        if t and t != exclude and norm(p.get("cwd") or "") == target:
            print(t)
            sys.exit(0)
if fallback:
    for p in panes:
        t = p.get("tab_id") or ""
        if t and t != exclude:
            print(t)
            sys.exit(0)'

# Tri-state cwd→tab lookup for /continue's reopen guard, in ONE pass. Prints:
#   <tab-id>     a pane's realpath(cwd) == target                     → reuse it
#   none         a POPULATED list, no exact match, no subtree pane    → confidently none
#   unverified   malformed/empty/error, an empty target, OR a pane whose cwd is a
#                SUBDIRECTORY of the worktree (a tab may have wandered in — fail closed
#                rather than risk a duplicate session)
# NOTE: norm() intentionally mirrors extract_tab above — keep the two in sync on path
# normalization. They are deliberately separate: extract_tab is exact-match-only (shared
# with /close's worktree-tab); this variant adds subtree-awareness + a tri-state, which
# /close must NOT inherit. Everything is wrapped so any error fails to `unverified`,
# never a false `none` (which would let the guard create a duplicate).
extract_tab_state='import sys, json, os
def norm(p):
    if not p or not p.strip():
        return ""
    p2 = p.rstrip("/") or "/"
    return os.path.realpath(p2)
target = norm(sys.argv[1]) if len(sys.argv) > 1 else ""
try:
    panes = json.load(sys.stdin)["result"]["panes"]
    if not panes or not target:
        print("unverified"); sys.exit(0)
    under = False
    for p in panes:
        t = p.get("tab_id") or ""
        c = norm(p.get("cwd") or "")
        if not t or not c:
            continue
        if c == target:
            print(t); sys.exit(0)
        if c.startswith(target + os.sep):
            under = True
    print("unverified" if under else "none")
except Exception:
    print("unverified")'

# Extract the tab_id of the pane whose pane_id == argv[0]. Empty pid never matches.
extract_pane_tab='import sys, json
pid = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    sys.exit(0)
if pid:
    for p in panes:
        if p.get("pane_id") == pid:
            print(p.get("tab_id") or "")
            sys.exit(0)'

# Print the agent_status of the pane whose pane_id == argv[0]; "__gone__" ONLY when a
# POPULATED list has no such pane (the pane really vanished). An empty pid, or an
# empty-but-valid panes array, prints nothing — same as a failed list call — so the
# poller keeps polling instead of mistaking a transient empty list (e.g. just after a
# herdr restart while panes repopulate) for a vanished pane and bailing without ever
# injecting /exit (the silent orphan this guards, mirroring extract_tab_present).
extract_status='import sys, json
pid = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    sys.exit(0)
if pid and panes:
    for p in panes:
        if p.get("pane_id") == pid:
            print(p.get("agent_status") or "")
            sys.exit(0)
    print("__gone__")'

# Print present|gone for whether any pane still has tab_id == argv[0]; prints
# "unverified" on malformed JSON, an empty tab arg, OR an empty panes array. The
# empty-array case matters: a transiently empty-but-valid list (e.g. just after a
# herdr restart while panes repopulate) must NOT read as "gone", or close-tab would
# falsely report a still-open tab as closed — the silent orphan this feature
# prevents. Never claim "gone" we did not actually observe. Lets /close confirm a
# teardown.
extract_tab_present='import sys, json
tab = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    print("unverified"); sys.exit(0)
if not tab or not panes:
    print("unverified"); sys.exit(0)
for p in panes:
    # herdr tab/pane ids are "wN:tM"/"wN:pM" strings (never numeric), so every id
    # comparison in this file (extract_tab, extract_pane_tab, extract_status, here)
    # compares as-is — no str() coercion needed.
    if (p.get("tab_id") or "") == tab:
        print("present"); sys.exit(0)
print("gone")'

# herdr pane list for a workspace (empty ws → unscoped). Empty string on failure.
pane_list() {
  local ws="${1:-}"
  if [ -n "$ws" ]; then herdr pane list --workspace "$ws" 2>/dev/null || true
  else herdr pane list 2>/dev/null || true; fi
}

# Look up a tab id by cwd. $1=workspace $2=target-cwd, rest passed to extract_tab
# (e.g. --first --exclude <tab>).
lookup_tab() {
  command -v herdr   >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; return 1; }
  command -v python3 >/dev/null 2>&1 || { echo "python3 not on PATH" >&2; return 1; }
  local ws="$1" target="$2"; shift 2
  local json tab
  [ -n "$target" ] || { echo "empty target path — refusing to match" >&2; return 1; }
  json="$(pane_list "$ws")"
  [ -n "$json" ] || { echo "herdr pane list returned nothing" >&2; return 1; }
  tab="$(printf '%s' "$json" | python3 -c "$extract_tab" "$target" "$@" 2>/dev/null || true)"
  [ -n "$tab" ] || { echo "no tab matched: $target" >&2; return 1; }
  printf '%s\n' "$tab"
}

# Whether a tab still has any pane. $1=workspace $2=tab-id. Echoes one of
# present|gone|unverified and ALWAYS returns 0 (callers branch on the word, and
# this runs under `set -e` inside command substitution). "unverified" means we
# could not check (herdr/python3 missing, or the list call failed) — distinct
# from "gone", so /close never reports a close it didn't actually confirm.
tab_status() {
  local ws="$1" tab="$2" json
  command -v herdr   >/dev/null 2>&1 || { echo unverified; return 0; }
  command -v python3 >/dev/null 2>&1 || { echo unverified; return 0; }
  json="$(pane_list "$ws")"
  [ -n "$json" ] || { echo unverified; return 0; }
  printf '%s' "$json" | python3 -c "$extract_tab_present" "$tab" 2>/dev/null || echo unverified
}

cmd="${1:-}"
case "$cmd" in
  worktree-tab)
    [ $# -eq 3 ] || { echo "usage: ${0##*/} worktree-tab <workspace> <worktree-path>" >&2; exit 2; }
    lookup_tab "$2" "$3"
    ;;
  worktree-tab-state)
    # Tri-state cwd→tab lookup for /continue's reopen guard. Echoes one of
    # <tab-id>|none|unverified and ALWAYS exits 0 (callers branch on the word; runs
    # under `set -e` inside command substitution). Pass an empty workspace to search
    # ALL workspaces (the reopen guard does, so a same-worktree tab in another
    # workspace is still found — worktree paths are globally unique). "unverified" (no
    # tools, list call failed, or an empty/repopulating pane list) must make the caller
    # fail CLOSED, never auto-create a duplicate session.
    [ $# -eq 3 ] || { echo "usage: ${0##*/} worktree-tab-state <workspace> <worktree-path>" >&2; exit 2; }
    # An empty target would make extract_tab match nothing and read as `none` (fail
    # open); refuse it as unverified instead.
    [ -n "$3" ] || { echo unverified; exit 0; }
    command -v herdr   >/dev/null 2>&1 || { echo unverified; exit 0; }
    command -v python3 >/dev/null 2>&1 || { echo unverified; exit 0; }
    json="$(pane_list "$2")"
    [ -n "$json" ] || { echo unverified; exit 0; }
    # One pass: <tab>|none|unverified. A malformed/empty/errored list — or a pane in a
    # subdirectory of the worktree — reads as `unverified` so the caller fails closed.
    printf '%s' "$json" | python3 -c "$extract_tab_state" "$3" 2>/dev/null || echo unverified
    ;;
  own-tab)
    [ $# -eq 3 ] || { echo "usage: ${0##*/} own-tab <workspace> <pane-id>" >&2; exit 2; }
    command -v herdr   >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
    command -v python3 >/dev/null 2>&1 || { echo "python3 not on PATH" >&2; exit 1; }
    tab="$(pane_list "$2" | python3 -c "$extract_pane_tab" "$3" 2>/dev/null || true)"
    [ -n "$tab" ] || { echo "no pane matched id: $3" >&2; exit 1; }
    printf '%s\n' "$tab"
    ;;
  main-tab)
    [ $# -ge 3 ] && [ $# -le 4 ] || { echo "usage: ${0##*/} main-tab <workspace> <main-repo-path> [exclude-tab]" >&2; exit 2; }
    if [ $# -eq 4 ] && [ -n "$4" ]; then lookup_tab "$2" "$3" --first --exclude "$4"
    else lookup_tab "$2" "$3" --first; fi
    ;;
  close-tab)
    # Scenario A: close the tab ONCE, then CONFIRM it's gone — a bare `herdr tab
    # close` can report success yet leave the tab (the orphan this whole feature
    # exists to prevent). Poll the status until `gone`, retrying the read on a
    # transient `unverified`. We deliberately do NOT re-issue the close in the loop:
    # if herdr recycled the now-closed tab id onto a fresh tab, a second `tab close`
    # would kill that unrelated live tab — and a close that genuinely didn't take is
    # surfaced as `still-open` so /close names it for a manual close anyway. Prints
    # closed|still-open|unverified and ALWAYS exits 0 (no require_herdr — that would
    # exit 1 with empty stdout, and /close's caller only branches on the three words).
    [ $# -ge 2 ] && [ $# -le 3 ] || { echo "usage: ${0##*/} close-tab <tab-id> [workspace]" >&2; exit 2; }
    tab="$2"; ws="${3:-${HERDR_WORKSPACE_ID:-}}"
    herdr tab close "$tab" >/dev/null 2>&1 || true   # best-effort (no-op if herdr absent)
    # Without both tools we can neither close nor verify — report unverified at once
    # instead of spinning the loop on a condition that can never change.
    if ! command -v herdr >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
      echo unverified; exit 0
    fi
    st=unverified
    k=0
    while [ $k -lt 5 ]; do
      st="$(tab_status "$ws" "$tab")"
      if [ "$st" = gone ]; then break; fi   # confirmed closed
      k=$((k + 1))
      # 'present' (async close in flight) or 'unverified' → wait before re-reading,
      # but not after the final read (its result is what we report).
      if [ $k -lt 5 ]; then sleep 0.3 2>/dev/null || true; fi
    done
    case "$st" in
      gone)    echo closed ;;
      present) echo still-open ;;
      *)       echo unverified ;;
    esac
    ;;
  focus-tab)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} focus-tab <tab-id>" >&2; exit 2; }
    require_herdr
    herdr tab focus "$2"
    ;;
  inject-exit)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} inject-exit <pane-id>" >&2; exit 2; }
    require_herdr
    # The pair that actually drives Claude's TUI to a clean exit (verified live).
    # If Return fails after the text was typed, dismiss the dangling slash-command
    # menu so the pane isn't left stuck mid-input.
    herdr pane send-text "$2" "/exit" || exit 1
    herdr pane send-keys "$2" Return || {
      herdr pane send-keys "$2" Escape >/dev/null 2>&1 || true
      exit 1
    }
    ;;
  self-exit)
    # Scenario B self-close. Detach an injector that waits for THIS session to go
    # idle (the turn that armed it has ended) and only then injects /exit onto the
    # idle prompt — enforcing the "never mid-turn" invariant instead of guessing
    # with a fixed timer. Args are passed positionally to the internal handler, so
    # no string is re-evaluated by a shell (an unusual pane id can't inject code).
    [ $# -ge 2 ] && [ $# -le 3 ] || { echo "usage: ${0##*/} self-exit <pane-id> [workspace]" >&2; exit 2; }
    require_herdr
    pane="$2"; ws="${3:-${HERDR_WORKSPACE_ID:-}}"
    nohup bash "$0" __delayed-inject "$pane" "$ws" >/dev/null 2>&1 &
    disown 2>/dev/null || true
    echo "self-exit armed for $pane (fires once this turn goes idle)" >&2
    ;;
  __delayed-inject)
    # Internal (detached): wait for THIS session to go idle (its turn ended), then
    # inject /exit. Inject ONLY on a confirmed idle/done — a transient `herdr pane
    # list` failure (empty output) is retried, never mistaken for idle, so /exit is
    # never delivered into a busy TUI. A vanished pane (`__gone__`) or a never-idle
    # timeout injects nothing; the armed marker + SessionEnd hook + manual Ctrl+D
    # remain the close path. Bounded so it can't hang; fire-and-forget.
    pane="${2:-}"; ws="${3:-}"
    [ -n "$pane" ] || exit 0
    command -v herdr   >/dev/null 2>&1 || exit 0
    command -v python3 >/dev/null 2>&1 || exit 0
    # ~120s window (240 × 0.5s): a closing turn that archives + commits + pushes
    # can outlast a 30s guess, and timing out injects nothing → the very idle
    # orphan we are fixing. Generous headroom is cheap; the loop exits the instant
    # the pane goes idle or vanishes.
    i=0
    while [ $i -lt 240 ]; do
      json="$(pane_list "$ws")"
      if [ -z "$json" ]; then sleep 0.5 2>/dev/null || true; i=$((i + 1)); continue; fi
      st="$(printf '%s' "$json" | python3 -c "$extract_status" "$pane" 2>/dev/null || true)"
      case "$st" in
        idle|done)
          # Turn ended → inject /exit onto the now-idle prompt (the state proven to
          # exit cleanly), exactly ONCE. No speculative re-inject: a second /exit
          # can't tell a dropped first injection from a user who reopened this tab
          # and is momentarily idle, and would kill that live session mid-use. If
          # this injection is dropped, /close's always-printed "close by hand:
          # <tab>" line + the SessionEnd hook are the backups.
          bash "$0" inject-exit "$pane" >/dev/null 2>&1 || true
          exit 0
          ;;
        __gone__)  exit 0 ;;   # pane already gone — nothing to exit
      esac
      sleep 0.5 2>/dev/null || true
      i=$((i + 1))
    done
    exit 0   # never confirmed idle → do NOT inject mid-turn; backups handle the close
    ;;
  arm-self-close)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} arm-self-close <tab-id>" >&2; exit 2; }
    mf="$(marker_file)" || { echo "cannot resolve marker path (HERDR_PANE_ID / HOME unset)" >&2; exit 1; }
    mkdir -p "$(dirname "$mf")"
    printf '%s %s\n' "$(date +%s 2>/dev/null || echo 0)" "$2" > "$mf"
    echo "armed self-close for pane ${HERDR_PANE_ID:-?} → tab $2" >&2
    ;;
  on-session-end)
    # Drain the hook's JSON stdin so Claude's write never SIGPIPEs us; skip when
    # attached to a tty (manual run). Never let anything abort the exit.
    [ -t 0 ] || cat >/dev/null 2>&1 || true
    mf="$(marker_file 2>/dev/null)" || exit 0
    [ -f "$mf" ] || exit 0
    read -r stamp tab < "$mf" 2>/dev/null || { rm -f "$mf" 2>/dev/null || true; exit 0; }
    [ -n "$tab" ] || { rm -f "$mf" 2>/dev/null || true; exit 0; }
    # Close only a VERIFIABLY FRESH marker: require a usable timestamp on both
    # sides and an in-window, non-negative age. If staleness can't be bounded
    # (date unavailable so stamp/now is 0, clock skew, or older than the TTL — the
    # user never did the clean exit, or a herdr restart reused this pane id), drop
    # it WITHOUT closing, so we never close whatever tab now holds the recorded id.
    now="$(date +%s 2>/dev/null || echo 0)"
    case "$stamp" in ''|*[!0-9]*) stamp=0 ;; esac
    case "$now"   in ''|*[!0-9]*) now=0 ;; esac
    fresh=no
    if [ "$now" -gt 0 ] && [ "$stamp" -gt 0 ]; then
      age=$((now - stamp))
      [ "$age" -ge 0 ] && [ "$age" -le "$MARKER_TTL" ] && fresh=yes
    fi
    if [ "$fresh" = yes ] && command -v herdr >/dev/null 2>&1; then
      # Close before removing the marker, with one retry, so a transient herdr
      # hiccup doesn't silently leak the finished task's tab.
      herdr tab close "$tab" >/dev/null 2>&1 || {
        sleep 0.3 2>/dev/null || true
        herdr tab close "$tab" >/dev/null 2>&1 || true
      }
    fi
    rm -f "$mf" 2>/dev/null || true
    exit 0
    ;;
  *)
    echo "usage: ${0##*/} {worktree-tab|worktree-tab-state|own-tab|main-tab|close-tab|focus-tab|inject-exit|self-exit|arm-self-close|on-session-end} ..." >&2
    exit 2
    ;;
esac
