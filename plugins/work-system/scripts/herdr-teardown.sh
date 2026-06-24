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
#   close-tab <tab-id>          Run `herdr tab close <tab-id>` (Scenario A).
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
marker_dir() { printf '%s/.cache/work-system/herdr' "$HOME"; }
marker_file() {
  local pane="${1:-${HERDR_PANE_ID:-}}"
  [ -n "$pane" ] || return 1
  pane="${pane//\//_}"   # sanitize: never build a path component from a raw id
  printf '%s/self-close-%s' "$(marker_dir)" "$pane"
}

require_herdr() { command -v herdr >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }; }

# Extract a tab_id from `herdr pane list` JSON on stdin.
#   argv: <target-cwd> [--first] [--exclude <tab>]   (cwd matched by realpath)
# Prints the tab of the first pane whose realpath(cwd) == realpath(target); with
# --first, falls back to the first pane whose tab != --exclude. Empty on no match
# or malformed JSON.
extract_tab='import sys, json, os
args = sys.argv[1:]
target = os.path.realpath(args[0].rstrip("/")) if args else ""
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
for p in panes:
    cwd = p.get("cwd") or ""
    if cwd and os.path.realpath(cwd.rstrip("/")) == target:
        print(p.get("tab_id") or "")
        sys.exit(0)
if fallback:
    for p in panes:
        t = p.get("tab_id") or ""
        if t and t != exclude:
            print(t)
            sys.exit(0)'

# Extract the tab_id of the pane whose pane_id == argv[0]. Empty on no match.
extract_pane_tab='import sys, json
pid = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    sys.exit(0)
for p in panes:
    if p.get("pane_id") == pid:
        print(p.get("tab_id") or "")
        sys.exit(0)'

# Extract the agent_status of the pane whose pane_id == argv[0]. Empty if gone.
extract_status='import sys, json
pid = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    sys.exit(0)
for p in panes:
    if p.get("pane_id") == pid:
        print(p.get("agent_status") or "")
        sys.exit(0)'

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
  json="$(pane_list "$ws")"
  [ -n "$json" ] || { echo "herdr pane list returned nothing" >&2; return 1; }
  tab="$(printf '%s' "$json" | python3 -c "$extract_tab" "$target" "$@" 2>/dev/null || true)"
  [ -n "$tab" ] || { echo "no tab matched: $target" >&2; return 1; }
  printf '%s\n' "$tab"
}

cmd="${1:-}"
case "$cmd" in
  worktree-tab)
    [ $# -eq 3 ] || { echo "usage: ${0##*/} worktree-tab <workspace> <worktree-path>" >&2; exit 2; }
    lookup_tab "$2" "$3"
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
    [ $# -eq 2 ] || { echo "usage: ${0##*/} close-tab <tab-id>" >&2; exit 2; }
    require_herdr
    herdr tab close "$2"
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
    # Internal (detached): poll until the pane leaves the 'working' state, then
    # inject /exit. Bounded so a never-idle pane can't hang; fire-and-forget.
    pane="${2:-}"; ws="${3:-}"
    [ -n "$pane" ] || exit 0
    command -v herdr   >/dev/null 2>&1 || exit 0
    command -v python3 >/dev/null 2>&1 || exit 0
    i=0
    while [ $i -lt 40 ]; do
      st="$(pane_list "$ws" | python3 -c "$extract_status" "$pane" 2>/dev/null || true)"
      case "$st" in idle|done|"") break ;; esac
      sleep 0.5 2>/dev/null || true
      i=$((i + 1))
    done
    bash "$0" inject-exit "$pane" >/dev/null 2>&1 || true
    exit 0
    ;;
  arm-self-close)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} arm-self-close <tab-id>" >&2; exit 2; }
    mf="$(marker_file)" || { echo "HERDR_PANE_ID not set — cannot arm self-close" >&2; exit 1; }
    mkdir -p "$(marker_dir)"
    printf '%s %s\n' "$(date +%s 2>/dev/null || echo 0)" "$2" > "$mf"
    echo "armed self-close for pane ${HERDR_PANE_ID} → tab $2" >&2
    ;;
  on-session-end)
    # Drain the hook's JSON stdin so Claude's write never SIGPIPEs us; skip when
    # attached to a tty (manual run). Never let anything abort the exit.
    [ -t 0 ] || cat >/dev/null 2>&1 || true
    mf="$(marker_file 2>/dev/null)" || exit 0
    [ -f "$mf" ] || exit 0
    read -r stamp tab < "$mf" 2>/dev/null || { rm -f "$mf" 2>/dev/null || true; exit 0; }
    rm -f "$mf" 2>/dev/null || true
    [ -n "$tab" ] || exit 0
    # Honor only a fresh marker: a stale one (the user never performed the clean
    # exit, then exits much later, or a herdr restart reused this pane id) must
    # not close whatever tab now holds the recorded id.
    now="$(date +%s 2>/dev/null || echo 0)"
    case "$stamp" in ''|*[!0-9]*) stamp=0 ;; esac
    if [ "$now" -gt 0 ] && [ "$stamp" -gt 0 ] && [ $((now - stamp)) -gt "$MARKER_TTL" ]; then
      exit 0
    fi
    command -v herdr >/dev/null 2>&1 && herdr tab close "$tab" >/dev/null 2>&1 || true
    exit 0
    ;;
  *)
    echo "usage: ${0##*/} {worktree-tab|own-tab|main-tab|close-tab|focus-tab|inject-exit|self-exit|arm-self-close|on-session-end} ..." >&2
    exit 2
    ;;
esac
