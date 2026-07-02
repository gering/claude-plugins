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
#   pane=<pane_id>   (empty on a resume that reused an already-open tab)
#   tab=<tab_id>     (launch: empty when the move into a dedicated tab failed;
#                     resume: the reused or freshly-created tab, empty only if unparsable)
#   moved=<yes|no>   (launch: no → Claude is a split in the CALLER's tab;
#                     resume: always yes — its own tab)
# resume adds two keys so the caller never reports a resume that didn't happen:
#   reused=<yes|no>  (yes → a tab already existed at this worktree and was focused,
#                     NOT a new one — no second `claude -c` was started. Its LIVE
#                     state is NOT asserted: a cwd match can't tell a live Claude from
#                     a bare shell that survived a prior `/exit`, so the caller tells
#                     the user to run `claude -c` if it's just a shell.)
#   resumed=<yes|no> (meaningful only when reused=no: yes → `claude -c` was sent into
#                     the fresh pane; no → the tab opened but the send failed, so the
#                     caller tells the user to run it by hand. EMPTY when reused=yes —
#                     nothing fresh was started.)
#   focused=<yes|no> (no → `herdr tab focus` failed or there was no tab id to focus,
#                     so the caller must not claim the tab was brought to the front.)
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
#   resume: tab create → the new tab's root pane id and its tab id in ONE pass,
#   pipe-delimited as `<pane>|<tab>` (the tab id may live at result.tab_id or,
#   defensively, under the root pane). A single `|` is ALWAYS printed, so the caller
#   splits on the first `|` — an empty pane with a present tab yields pane="" (which
#   then fails the non-empty-pane guard) instead of the tab being mis-read as the
#   pane. herdr ids are `wN:pM`/`wN:tM` and never contain `|`.
extract_root_pane_tab='import sys, json
try:
    r = json.load(sys.stdin)["result"]
    pane = (r.get("root_pane") or {}).get("pane_id") or ""
    tab = r.get("tab_id") or (r.get("root_pane") or {}).get("tab_id") or ""
    print(pane + "|" + tab)
except Exception:
    print("|")'

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
    # Guard against a duplicate session. If a tab is ALREADY open at this worktree
    # cwd (e.g. the task was never `/exit`-ed), reopening would spawn a SECOND
    # `claude -c` on the same working tree — two sessions clobbering each other's
    # uncommitted changes. So look for an existing tab first and just focus it,
    # reusing the teardown helper's cwd→tab lookup (realpath match) — the single
    # source of truth for pane-cwd matching. Retry a few times: `worktree-tab`
    # returns empty for BOTH "no such tab" and a transiently empty `herdr pane list`
    # (e.g. just after a herdr restart while panes repopulate), and treating the
    # latter as "no tab" would fail OPEN and duplicate. Retrying converts most
    # transient misses into a hit; a genuine no-tab still ends empty and we create.
    # NOTE: the lookup is scoped to $workspace, matching how /kickoff and /close
    # operate — a still-live tab for this worktree in a DIFFERENT herdr workspace is
    # out of scope and would not be found (accepted, consistent limitation).
    teardown="${0%/*}/herdr-teardown.sh"
    existing_tab=""
    if [ -f "$teardown" ]; then
      k=0
      while [ $k -lt 3 ]; do
        existing_tab="$(bash "$teardown" worktree-tab "$workspace" "$worktree" 2>/dev/null || true)"
        [ -n "$existing_tab" ] && break
        k=$((k + 1))
        [ $k -lt 3 ] && sleep 0.3 2>/dev/null || true
      done
    fi
    if [ -n "$existing_tab" ]; then
      # Focus it, but do NOT assert a live resume: a cwd match can't tell a live
      # Claude from a bare shell that survived a prior `/exit`. resumed is left EMPTY
      # so the caller tells the user to run `claude -c` if the tab is just a shell.
      focused=yes
      herdr tab focus "$existing_tab" >/dev/null 2>&1 || focused=no
      printf 'pane=\ntab=%s\nmoved=yes\nreused=yes\nresumed=\nfocused=%s\n' "$existing_tab" "$focused"
      exit 0
    fi

    # No existing tab — create a fresh one at the worktree and read back its root
    # (shell) pane id and tab id in one python3 pass. Split on the FIRST `|`, so an
    # empty pane id (with a present tab id) stays empty and trips the guard below,
    # rather than the tab id being mis-read as the pane id.
    create_json="$(herdr tab create --workspace "$workspace" \
      --cwd "$worktree" --label "$label" 2>/dev/null || true)"
    pane_tab="$(printf '%s' "$create_json" | python3 -c "$extract_root_pane_tab" 2>/dev/null || true)"
    pane="${pane_tab%%|*}"
    tab="${pane_tab#*|}"

    # Empty pane id → the tab did not open, or the response was malformed (broken
    # socket / bad JSON / pane-less result). Cannot run claude -c without a pane.
    [ -n "$pane" ] || { echo "herdr tab create did not return a pane id" >&2; exit 1; }

    # Run `claude -c` INSIDE the shell pane — the /exit hardening (a later /exit
    # returns to the shell, keeping the tab alive). Report resumed=no if the send
    # fails, so the caller never claims a resume that didn't happen: the tab + shell
    # exist, but the user must run `claude -c` in it by hand. (This catches a failed
    # send; it can't catch `claude -c` erroring later on a cwd with no prior session
    # — the caller's wording stays appropriately tentative for that case.)
    resumed=yes
    if ! herdr pane run "$pane" "claude -c" >/dev/null 2>&1; then
      resumed=no
      echo "herdr pane run could not start 'claude -c' in $pane (tab is open; run it by hand)" >&2
    fi

    # Focus the reopened tab — unlike kickoff's background launch, the user is
    # switching to it now. Report whether the focus took, so the caller doesn't
    # claim a focus that failed.
    focused=yes
    [ -n "$tab" ] || focused=no
    if [ -n "$tab" ] && ! herdr tab focus "$tab" >/dev/null 2>&1; then focused=no; fi

    printf 'pane=%s\ntab=%s\nmoved=yes\nreused=no\nresumed=%s\nfocused=%s\n' \
      "$pane" "$tab" "$resumed" "$focused"
    ;;
esac
