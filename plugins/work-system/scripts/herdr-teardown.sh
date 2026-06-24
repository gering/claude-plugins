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
#       Print the tab_id of the pane whose cwd == the worktree path. Must run
#       BEFORE `git worktree remove` (afterwards the cwd points at a deleted
#       path). Exit 1 (prints nothing) if herdr is unreachable or no pane matches.
#   main-tab <workspace> <main-repo-abs-path>
#       Print the tab_id of the pane whose cwd == the main-repo path, so /close
#       can focus it before a self-close. Falls back to the workspace's first
#       pane when no cwd matches (so the user is never stranded). Exit 1 only when
#       herdr is unreachable or the workspace has no panes.
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
#   arm-self-close <tab-id>     Write the self-close marker for $HERDR_PANE_ID,
#                               recording the tab to close on clean exit.
#   on-session-end              Called by the SessionEnd hook. If a marker exists
#                               for $HERDR_PANE_ID, close the recorded tab and
#                               remove the marker; otherwise no-op. Always exit 0
#                               (fire-and-forget — never break the session exit).
#
# Lookups print `key=value`-free raw ids on stdout; diagnostics go to stderr.
set -eu

# --- marker location (single source of truth) -------------------------------
# Keyed by herdr pane id so a stray marker can only ever affect the pane that
# wrote it. Under $HOME (stable across the pane's processes) rather than $TMPDIR
# (per-process on macOS), so /close and the SessionEnd hook agree on the path.
marker_dir() { printf '%s/work-system/herdr' "${XDG_CACHE_HOME:-$HOME/.cache}"; }
marker_file() {
  local pane="${1:-${HERDR_PANE_ID:-}}"
  [ -n "$pane" ] || return 1
  printf '%s/self-close-%s' "$(marker_dir)" "$pane"
}

# Strip a single trailing slash so a worktree path with/without it still matches
# herdr's stored cwd.
norm() { printf '%s' "${1%/}"; }

# Extract the tab_id of the first pane whose cwd matches $1; with `--first`,
# fall back to the first pane's tab_id. Malformed/empty JSON yields no output.
extract_tab='import sys, json
target = sys.argv[1].rstrip("/")
fallback = len(sys.argv) > 2 and sys.argv[2] == "--first"
try:
    panes = json.load(sys.stdin)["result"]["panes"]
except Exception:
    sys.exit(0)
for p in panes:
    if (p.get("cwd") or "").rstrip("/") == target:
        print(p.get("tab_id") or "")
        sys.exit(0)
if fallback and panes:
    print(panes[0].get("tab_id") or "")'

# Look up a tab id by cwd. $1=workspace $2=target-cwd $3=optional --first.
lookup_tab() {
  command -v herdr   >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; return 1; }
  command -v python3 >/dev/null 2>&1 || { echo "python3 not on PATH" >&2; return 1; }
  local ws="$1" target; target="$(norm "$2")"; local flag="${3:-}"
  local json tab
  json="$(herdr pane list --workspace "$ws" 2>/dev/null || true)"
  [ -n "$json" ] || { echo "herdr pane list returned nothing" >&2; return 1; }
  tab="$(printf '%s' "$json" | python3 -c "$extract_tab" "$target" "$flag" 2>/dev/null || true)"
  [ -n "$tab" ] || { echo "no tab matched cwd: $target" >&2; return 1; }
  printf '%s\n' "$tab"
}

cmd="${1:-}"
case "$cmd" in
  worktree-tab)
    [ $# -eq 3 ] || { echo "usage: ${0##*/} worktree-tab <workspace> <worktree-path>" >&2; exit 2; }
    lookup_tab "$2" "$3"
    ;;
  main-tab)
    [ $# -eq 3 ] || { echo "usage: ${0##*/} main-tab <workspace> <main-repo-path>" >&2; exit 2; }
    lookup_tab "$2" "$3" --first
    ;;
  close-tab)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} close-tab <tab-id>" >&2; exit 2; }
    command -v herdr >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
    herdr tab close "$2"
    ;;
  focus-tab)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} focus-tab <tab-id>" >&2; exit 2; }
    command -v herdr >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
    herdr tab focus "$2"
    ;;
  inject-exit)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} inject-exit <pane-id>" >&2; exit 2; }
    command -v herdr >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
    # The pair that actually drives Claude's TUI to a clean exit (verified live).
    herdr pane send-text "$2" "/exit" && herdr pane send-keys "$2" Return
    ;;
  self-exit)
    # Self-close for Scenario B: arm a DETACHED injector that fires `inject-exit`
    # after this turn ends, when Claude is back at an idle prompt — the state in
    # which /exit cleanly exits (verified). This deliberately does NOT inject
    # mid-turn (delivery into a busy TUI is unreliable). `nohup … & disown` keeps
    # the injector alive past the launching turn; Claude's clean exit then
    # auto-closes its root-pane tab, with the SessionEnd hook as the backup.
    [ $# -ge 2 ] && [ $# -le 3 ] || { echo "usage: ${0##*/} self-exit <pane-id> [delay-seconds]" >&2; exit 2; }
    command -v herdr >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
    pane="$2"; delay="${3:-4}"
    nohup bash -c "sleep $delay; bash \"$0\" inject-exit \"$pane\"" >/dev/null 2>&1 &
    disown 2>/dev/null || true
    echo "self-exit armed for $pane (fires in ${delay}s, after the turn ends)" >&2
    ;;
  arm-self-close)
    [ $# -eq 2 ] || { echo "usage: ${0##*/} arm-self-close <tab-id>" >&2; exit 2; }
    mf="$(marker_file)" || { echo "HERDR_PANE_ID not set — cannot arm self-close" >&2; exit 1; }
    mkdir -p "$(marker_dir)"
    printf '%s\n' "$2" > "$mf"
    echo "armed self-close for pane ${HERDR_PANE_ID} → tab $2" >&2
    ;;
  on-session-end)
    # Drain the hook's JSON stdin so Claude's write never SIGPIPEs us; skip when
    # attached to a tty (manual run). Never let anything abort the exit.
    [ -t 0 ] || cat >/dev/null 2>&1 || true
    mf="$(marker_file 2>/dev/null)" || exit 0
    [ -f "$mf" ] || exit 0
    tab="$(cat "$mf" 2>/dev/null || true)"
    rm -f "$mf" 2>/dev/null || true
    [ -n "$tab" ] || exit 0
    command -v herdr >/dev/null 2>&1 && herdr tab close "$tab" >/dev/null 2>&1 || true
    exit 0
    ;;
  *)
    echo "usage: ${0##*/} {worktree-tab|main-tab|close-tab|focus-tab|inject-exit|self-exit|arm-self-close|on-session-end} ..." >&2
    exit 2
    ;;
esac
