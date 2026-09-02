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
#       `none` (populated list, every tab pane has a readable cwd, none matches →
#       confidently no tab), or `unverified` (no tools / failed / empty / errored list,
#       or a tab pane whose cwd is unreadable → caller must fail closed). Always exit 0.
#       Empty <workspace> searches ALL workspaces.
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
#   manager-session <workspace> <main-repo-abs-path>
#       Tri-state detection of a live MANAGER session for this repo — the claude
#       agent whose cwd IS the main-repo root — so a worker /close can offer to
#       delegate the teardown to it (Scenario A from a session that survives the
#       close) instead of self-closing (Scenario B). Prints `name=<session-name>`,
#       `none` (a populated, fully readable agent list has no agent at the root) or
#       `unverified` (anything we cannot rule out, incl. TWO candidates — the skill
#       must never guess which session to message). Always exit 0. An empty
#       <workspace> searches all workspaces of the current herdr server. The name
#       is only a CANDIDATE address: the skill resolves it against ListAgents and
#       drops the offer when it is missing or ambiguous there.
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
#                               this text+Return pair triggers a clean exit. Whether
#                               that also closes the TAB depends on the herdr the
#                               worker was launched under: on legacy herdr the worker
#                               is the tab's root pane, so the exit closes the tab
#                               too; on 0.7.5+ `agent start` needs an existing pane,
#                               so the worker runs INSIDE a shell and the exit only
#                               drops back to that shell. The armed marker +
#                               SessionEnd hook is therefore the primary close, not a
#                               safety net — never assume the exit alone did it.
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

# realpath path-normalization, shared by BOTH extractors below so the reopen guard
# and /close's worktree-tab lookup can never disagree on cwd matching. Concatenated
# into each python program (they differ only in match/output logic).
norm_py='def norm(p):
    if not p or not p.strip():
        return ""
    p2 = p.rstrip("/") or "/"   # all-slashes path stays root, never collapses to ""
    return os.path.realpath(p2)'

# Extract a tab_id from `herdr pane list` JSON on stdin.
#   argv: <target-cwd> [--first] [--exclude <tab>]   (cwd matched by realpath)
# Prints the tab of the first pane whose realpath(cwd) == realpath(target), honoring
# --exclude even on that primary match; with --first, falls back to the first pane
# whose tab != --exclude. An empty/whitespace target never matches (realpath("")
# would resolve to the process cwd). Empty output on no match or malformed JSON.
extract_tab='import sys, json, os
'"$norm_py"'
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
#   <tab-id>     a pane whose realpath(cwd) == target (exact), with a tab_id → reuse it
#   none         a POPULATED list where every pane has a READABLE cwd, none matches the
#                worktree → confidently no tab here
#   unverified   malformed/empty/error list, an empty target, a pane with an
#                empty/unreadable cwd, OR a worktree-cwd pane with no tab_id (exists but
#                can't be focused) — anything we can't rule out → fail closed rather than
#                risk a duplicate `claude -c`
# EXACT-match only, like extract_tab. Subtree-matching (treat a pane in a worktree
# SUBDIR as the tab) was tried and reverted: any unrelated pane cd'd into a subdir would
# have blocked auto-reopen forever. Consequence: a task tab that itself wandered into a
# subdir is not detected — an accepted narrow gap (see the knowledge entry). cwd is
# checked BEFORE tab_id so a worktree pane momentarily missing its tab_id fails closed
# rather than being skipped. Any exception prints `unverified`, never a false `none`.
extract_tab_state='import sys, json, os
'"$norm_py"'
target = norm(sys.argv[1]) if len(sys.argv) > 1 else ""
try:
    panes = json.load(sys.stdin)["result"]["panes"]
    if not panes or not target:
        print("unverified"); sys.exit(0)
    unknown = False
    for p in panes:
        c = norm(p.get("cwd") or "")
        if not c:
            unknown = True   # unreadable cwd — cannot rule out that it is the worktree
            continue
        if c == target:
            t = p.get("tab_id") or ""
            if t:
                print(t); sys.exit(0)   # exact match with a tab → reuse
            unknown = True   # worktree pane exists but no tab_id → cannot focus, do not dup
    print("unverified" if unknown else "none")
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

# Pick THE Manager session out of `herdr agent list` (stdin). Needs match_roots /
# classify_cwd from $HERDR_MATCH_PRELUDE, prepended by the caller, so the cwd match
# is the SAME one lanes.sh and herdr-tab-glyph.sh use (exact realpath; a mere subdir
# is not the root). argv: <main-repo-path> [workspace]. Prints name=<x>|none|unverified.
# Fail-closed by construction: every branch we cannot fully rule out prints
# `unverified`, because a wrong `none` only costs the offer while a wrong `name=`
# would send a close request to a stranger session.
extract_manager='import sys, json, re, unicodedata
main = sys.argv[1] if len(sys.argv) > 1 else ""
ws = sys.argv[2] if len(sys.argv) > 2 else ""
# herdr 0.8 statuses: idle|working|blocked|done|unknown (the same vocabulary
# ha_wait validates for --until). `unknown` is DELIBERATELY absent here: it means
# herdr cannot tell, which is not a confirmed live session. Keep the two in sync by
# hand — they are different SETS of the same vocabulary, not one shared constant.
LIVE = ("idle", "working", "blocked", "done")

def session_name(a):
    # The SendMessage address is the CLAUDE SESSION name, which Claude writes into
    # the terminal title — NOT the herdr agent name (verified live: a herdr agent
    # named gcp-auth-159 hosts the session "answer-gcp-auth-questions-buchhalter-159").
    # herdr strips only its own status glyph from the title, so drop one leading
    # symbol+space here too (the working spinner: ◐/◑/✳ …). The space is required, so
    # a name that genuinely starts with punctuation (e.g. /habemus-event) keeps it; a
    # wrong strip costs only the offer (no ListAgents match), never a misdirected message.
    # NO fallback to herdr agent `name`: it is a launch label (verified live: agent
    # gcp-auth-159 hosts session answer-gcp-auth-questions-buchhalter-159), so emitting
    # it would hand the caller a string that is not an address — and a namesake in
    # ANOTHER repo could then pass the uniqueness check. No title -> no candidate.
    t = str(a.get("terminal_title_stripped") or a.get("terminal_title") or "")
    # Blank EVERY control/format char first (Cc/Cf, plus surrogates/private use):
    # newlines and tabs would forge extra output lines, and ANSI escapes or a bidi
    # override (U+202E) could make the printed name read differently than it matches.
    # A mangled name simply fails the caller ListAgents match — fail closed, as intended.
    t = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co") else c for c in t)
    # Then drop ONE leading symbol+space (the herdr/claude spinner glyph) and collapse.
    t = re.sub(r"^[^\w\s]\s+", "", t.strip())
    t = re.sub(r"\s+", " ", t).strip()
    return "" if len(t) > 200 else t

root, wtdir = match_roots(main)
try:
    agents = json.load(sys.stdin)["result"]["agents"]
except Exception:
    agents = None
# None = malformed; [] = an empty/repopulating list (same reason extract_tab_present
# refuses to read an empty list as "gone") — neither can rule out a Manager.
if root is None or not agents:
    print("unverified"); sys.exit(0)

found = []
unknown = False   # something at/near the root we could not classify → fail closed
for a in agents:
    if not isinstance(a, dict):
        unknown = True          # a junk element may have BEEN the Manager row
        continue
    if ws and str(a.get("workspace_id") or "") != ws:
        continue
    cwd = a.get("cwd")
    if not cwd or not str(cwd).strip():
        unknown = True          # unreadable cwd — cannot rule out that it is the root
        continue
    kind, key, resolved = classify_cwd(str(cwd), root, wtdir)
    if kind != "main":
        continue
    # From here on the agent SITS AT THE ROOT: anything unusable about it makes the
    # answer unverified, never a confident `none`.
    if str(a.get("agent") or "").lower() != "claude":
        unknown = True          # codex/grok/shell at the root: no SendMessage address
        continue
    if str(a.get("agent_status") or "").lower() not in LIVE:
        unknown = True          # unknown/absent status — not a confirmed live session
        continue
    n = session_name(a)
    if not n:
        unknown = True
        continue
    found.append(n)

if unknown or len(found) > 1:
    print("unverified")         # ambiguous or partly unreadable → no offer
elif found:
    print("name=" + found[0])
else:
    print("none")'

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

# Run a python extractor over `herdr pane list` for a workspace and echo its output.
# Any unavailability — herdr/python3 missing, or an empty/failed list — echoes
# `unverified` instead, so a "couldn't check" is never mistaken for a definite answer.
# ALWAYS returns 0 (callers branch on the word, under `set -e` in command subst). This
# is the single definition of that guard chain, shared by tab_status() and the
# worktree-tab-state subcommand so they can't drift on the herdr-down mapping.
pane_query() {
  local ws="$1" extractor="$2"; shift 2
  command -v herdr   >/dev/null 2>&1 || { echo unverified; return 0; }
  command -v python3 >/dev/null 2>&1 || { echo unverified; return 0; }
  local json; json="$(pane_list "$ws")"
  [ -n "$json" ] || { echo unverified; return 0; }
  printf '%s' "$json" | python3 -c "$extractor" "$@" 2>/dev/null || echo unverified
}

# Whether a tab still has any pane. $1=workspace $2=tab-id. Echoes one of
# present|gone|unverified and ALWAYS returns 0. "unverified" means we could not check
# (herdr/python3 missing, or the list call failed) — distinct from "gone", so /close
# never reports a close it didn't actually confirm.
tab_status() {
  pane_query "$1" "$extract_tab_present" "$2"
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
    # under `set -e` inside command substitution). An empty workspace searches all
    # workspaces OF THE CURRENT HERDR SERVER (a session in a separate herdr server is
    # invisible — accepted). `unverified` (no tools, failed/empty list, or an
    # unreadable-cwd pane) must make the caller fail CLOSED, never auto-create a duplicate.
    [ $# -eq 3 ] || { echo "usage: ${0##*/} worktree-tab-state <workspace> <worktree-path>" >&2; exit 2; }
    # An empty target would match nothing and read as `none` (fail open); refuse it.
    [ -n "$3" ] || { echo unverified; exit 0; }
    pane_query "$2" "$extract_tab_state" "$3"
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
  manager-session)
    # Manager detection for /close delegation. Uses `herdr agent list` (NOT pane
    # list): only the agent list tells a live claude session from a bare shell, and
    # carries the terminal title the SendMessage address is derived from. ha_list
    # (sourced from the sibling wrapper) already bounds the call and validates the
    # JSON shape, so every degrade path lands on `unverified`. Sourced INSIDE this
    # branch so the SessionEnd-hook path stays untouched. ALWAYS exits 0.
    [ $# -eq 3 ] || { echo "usage: ${0##*/} manager-session <workspace> <main-repo-path>" >&2; exit 2; }
    # An empty root would match nothing and read as `none` (fail open); refuse it.
    [ -n "$3" ] || { echo unverified; exit 0; }
    SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || SCRIPT_DIR=""
    if [ -z "$SCRIPT_DIR" ] || [ ! -f "$SCRIPT_DIR/herdr-agent.sh" ]; then echo unverified; exit 0; fi
    # shellcheck source=herdr-agent.sh
    . "$SCRIPT_DIR/herdr-agent.sh"
    # No python3 guard here: ha_list fails (code 3) when either tool is missing.
    agents_json="$(ha_list)" || { echo unverified; exit 0; }
    out="$(printf '%s' "$agents_json" | PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$extract_manager" "$3" "$2" 2>/dev/null || true)"
    [ -n "$out" ] || out=unverified
    printf '%s\n' "$out"
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
    echo "usage: ${0##*/} {worktree-tab|worktree-tab-state|own-tab|main-tab|manager-session|close-tab|focus-tab|inject-exit|self-exit|arm-self-close|on-session-end} ..." >&2
    exit 2
    ;;
esac
