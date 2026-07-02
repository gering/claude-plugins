#!/usr/bin/env bash
# herdr-launch.sh — open a Claude session for a work-system task inside herdr.
#
# Two modes share one precondition/JSON-parse/output contract:
#
#   launch  (/kickoff)  — spawn Claude as ARGV via `agent start … -- claude … /continue`,
#                         then move it into its own background tab. Claude is the
#                         tab's ROOT pane, so a later clean /exit ends the pane and
#                         herdr closes the tab. Argv-exec structurally avoids the
#                         shell-startup keystroke race (see the kickoff knowledge
#                         entry) — a fresh interactive shell can eat the leading
#                         keystrokes of a typed command.
#
#   resume  (/continue) — reopen a tab at the worktree and run `claude -c` INSIDE a
#                         shell pane, then focus it. Because Claude runs inside a
#                         shell (not as the root pane), a later /exit drops back to
#                         the shell and the TAB SURVIVES — this is the /exit
#                         hardening. Used to recover a task tab that a bare /exit
#                         closed (kickoff tabs are root-pane Claude, so /exit closes
#                         them). `claude -c` continues the most-recent session for
#                         the worktree cwd; since each worktree hosts exactly one
#                         task, the cwd already identifies the session unambiguously
#                         — no session id needs stashing at kickoff.
#
# Usage:
#   herdr-launch.sh launch <label> <worktree-abs-path> <workspace-id> [session-name]
#   herdr-launch.sh resume <label> <worktree-abs-path> <workspace-id>
#     label         short, sidebar-friendly agent/tab name (e.g. close-herdr)
#     worktree      absolute path to the worktree (becomes the new pane's cwd)
#     workspace-id  herdr workspace to open the tab in (e.g. $HERDR_WORKSPACE_ID)
#     session-name  (launch only) `claude -n` name; defaults to <label>
#
# On success (exit 0) prints key=value lines on stdout:
#   pane=<pane_id>
#   tab=<tab_id>     (launch: empty when the move into a dedicated tab failed;
#                     resume: the freshly-created tab, empty only if unparsable)
#   moved=<yes|no>   (launch: no → Claude is a split in the CALLER's tab;
#                     resume: always yes — its own freshly-created tab)
# On failure to launch (exit 1) prints nothing on stdout — the caller should show
# the manual instructions instead. Diagnostics always go to stderr.
set -eu

mode="${1:-}"
case "$mode" in
  launch|resume) shift ;;
  *)
    echo "usage: ${0##*/} {launch|resume} <label> <worktree> <workspace-id> [session-name]" >&2
    exit 1
    ;;
esac

label="${1:-}"
worktree="${2:-}"
workspace="${3:-}"
session="${4:-$label}"

# Preconditions (shared). Any miss means "cannot automate" → caller falls back to
# the manual block.
command -v herdr   >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not on PATH" >&2; exit 1; }
[ -n "$label" ] && [ -n "$worktree" ] && [ -n "$workspace" ] || {
  echo "usage: ${0##*/} $mode <label> <worktree> <workspace-id> [session-name]" >&2
  exit 1
}
[ -d "$worktree" ] || { echo "worktree dir not found: $worktree" >&2; exit 1; }

# JSON extractors. null / missing / malformed all yield empty, and a stray
# traceback can never reach the user's terminal.
#   launch: agent start → result.agent.pane_id, then move → the created tab id.
extract_agent_pane='import sys, json
try:
    print(json.load(sys.stdin)["result"]["agent"]["pane_id"] or "")
except Exception:
    pass'
extract_moved_tab='import sys, json
try:
    print(json.load(sys.stdin)["result"]["move_result"]["created_tab"]["tab_id"] or "")
except Exception:
    pass'
#   resume: tab create → the new tab's root pane id and its tab id (the tab id may
#   live at result.tab_id or, defensively, under the root pane).
extract_root_pane='import sys, json
try:
    print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"] or "")
except Exception:
    pass'
extract_created_tab='import sys, json
try:
    r = json.load(sys.stdin)["result"]
    print(r.get("tab_id") or (r.get("root_pane") or {}).get("tab_id") or "")
except Exception:
    pass'

case "$mode" in
  launch)
    # Spawn Claude as argv and read back the pane id.
    start_json="$(herdr agent start "$label" --workspace "$workspace" \
      --cwd "$worktree" --no-focus -- claude -n "$session" "/continue" 2>/dev/null || true)"
    pane="$(printf '%s' "$start_json" | python3 -c "$extract_agent_pane" 2>/dev/null || true)"

    # Empty pane id → the agent did not start (broken socket / bad response).
    [ -n "$pane" ] || { echo "herdr agent start did not return a pane id" >&2; exit 1; }

    # Relocate the agent into its own background tab (agent start splits the
    # caller's tab). If the move fails, Claude is still running — report it in
    # place rather than claiming a tab that does not exist.
    if move_json="$(herdr pane move "$pane" --new-tab --label "$label" --no-focus 2>/dev/null)"; then
      tab="$(printf '%s' "$move_json" | python3 -c "$extract_moved_tab" 2>/dev/null || true)"
      printf 'pane=%s\ntab=%s\nmoved=yes\n' "$pane" "$tab"
    else
      printf 'pane=%s\ntab=\nmoved=no\n' "$pane"
    fi
    ;;

  resume)
    # Create a fresh tab at the worktree and read back its root (shell) pane id.
    create_json="$(herdr tab create --workspace "$workspace" \
      --cwd "$worktree" --label "$label" 2>/dev/null || true)"
    pane="$(printf '%s' "$create_json" | python3 -c "$extract_root_pane" 2>/dev/null || true)"
    tab="$(printf '%s' "$create_json" | python3 -c "$extract_created_tab" 2>/dev/null || true)"

    # Empty pane id → the tab did not open (broken socket / bad response).
    [ -n "$pane" ] || { echo "herdr tab create did not return a pane id" >&2; exit 1; }

    # Run `claude -c` INSIDE the shell pane — the /exit hardening (a later /exit
    # returns to the shell, keeping the tab alive). Best-effort: the tab + shell
    # already exist, so a `pane run` hiccup still leaves a usable tab the user can
    # type `claude -c` into; we report the pane/tab either way (mirroring launch,
    # which likewise never confirms Claude actually started).
    herdr pane run "$pane" "claude -c" >/dev/null 2>&1 \
      || echo "herdr pane run could not start 'claude -c' in $pane (tab is open; run it by hand)" >&2

    # Focus the reopened tab — unlike kickoff's background launch, the user is
    # switching to it now.
    [ -n "$tab" ] && herdr tab focus "$tab" >/dev/null 2>&1 || true

    printf 'pane=%s\ntab=%s\nmoved=yes\n' "$pane" "$tab"
    ;;
esac
