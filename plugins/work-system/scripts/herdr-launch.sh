#!/usr/bin/env bash
# herdr-launch.sh — launch a Claude session for a work-system task inside herdr.
#
# Spawns Claude as argv (no shell, no typed keystrokes) so it never races a fresh
# interactive shell's startup — the failure mode that an earlier `pane run
# "claude …"` implementation hit when oh-my-zsh's update prompt ate the leading
# keystroke. herdr execs the `claude` binary directly, names the agent, and runs
# `/continue` as the launch prompt; the agent is then moved into its own
# background tab. Reusable by /kickoff (and later /adopt).
#
# Usage:
#   herdr-launch.sh <label> <worktree-abs-path> <workspace-id> [session-name]
#     label         short, sidebar-friendly agent/tab name (e.g. close-herdr)
#     worktree      absolute path to the worktree (becomes the new pane's cwd)
#     workspace-id  herdr workspace to open the tab in (e.g. $HERDR_WORKSPACE_ID)
#     session-name  optional `claude -n` name; defaults to <label>
#
# On success (exit 0) prints key=value lines on stdout:
#   pane=<pane_id>
#   tab=<new_tab_id>     (empty when the move into a dedicated tab failed)
#   moved=<yes|no>       (no → Claude is running as a split in the CALLER's tab)
# On failure to launch (exit 1) prints nothing on stdout — the caller should show
# the manual instructions instead. Diagnostics always go to stderr.
set -eu

label="${1:-}"
worktree="${2:-}"
workspace="${3:-}"
session="${4:-$label}"

# Preconditions. Any miss means "cannot automate" → caller falls back to manual.
command -v herdr   >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not on PATH" >&2; exit 1; }
[ -n "$label" ] && [ -n "$worktree" ] && [ -n "$workspace" ] || {
  echo "usage: ${0##*/} <label> <worktree> <workspace-id> [session-name]" >&2
  exit 1
}
[ -d "$worktree" ] || { echo "worktree dir not found: $worktree" >&2; exit 1; }

# Extract result.agent.pane_id; null / missing / malformed JSON all yield empty,
# and a stray traceback can never reach the user's terminal.
extract_pane='import sys, json
try:
    print(json.load(sys.stdin)["result"]["agent"]["pane_id"] or "")
except Exception:
    pass'
extract_tab='import sys, json
try:
    print(json.load(sys.stdin)["result"]["move_result"]["created_tab"]["tab_id"] or "")
except Exception:
    pass'

# Spawn Claude as argv and read back the pane id.
start_json="$(herdr agent start "$label" --workspace "$workspace" \
  --cwd "$worktree" --no-focus -- claude -n "$session" "/continue" 2>/dev/null || true)"
pane="$(printf '%s' "$start_json" | python3 -c "$extract_pane" 2>/dev/null || true)"

# Empty pane id → the agent did not start (broken socket / bad response).
[ -n "$pane" ] || { echo "herdr agent start did not return a pane id" >&2; exit 1; }

# Relocate the agent into its own background tab (agent start splits the caller's
# tab). If the move fails, Claude is still running — report it in place rather
# than claiming a tab that does not exist.
if move_json="$(herdr pane move "$pane" --new-tab --label "$label" --no-focus 2>/dev/null)"; then
  tab="$(printf '%s' "$move_json" | python3 -c "$extract_tab" 2>/dev/null || true)"
  printf 'pane=%s\ntab=%s\nmoved=yes\n' "$pane" "$tab"
else
  printf 'pane=%s\ntab=\nmoved=no\n' "$pane"
fi
