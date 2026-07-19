#!/usr/bin/env bash
# herdr-launch.sh — open a worker session for a work-system task inside herdr.
#
# Two modes share one precondition/JSON-parse/output contract:
#
#   launch  (/kickoff)  — spawn the chosen worker as ARGV via `agent start … --
#                         <worker-argv>`, then move it into its own background tab.
#                         The worker is the tab's ROOT pane, so a later clean /exit
#                         ends the pane and herdr closes the tab. Argv-exec
#                         structurally avoids the shell-startup keystroke race (see
#                         the kickoff knowledge entry) — a fresh interactive shell
#                         can eat the leading keystrokes of a typed command.
#                         The worker argv is resolved from an agent SELECTOR via
#                         agent-registry.sh (claude/codex/grok × model); with no
#                         selector it stays the legacy `claude … /continue`. The
#                         registry owns every per-CLI launch detail, so this script
#                         is CLI-agnostic — it just execs the resolved argv.
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
#   herdr-launch.sh launch <label> <worktree-abs-path> <workspace-id> [agent-selector] [session-name]
#   herdr-launch.sh resume <label> <worktree-abs-path> <workspace-id>
#     label          short, sidebar-friendly agent/tab name (e.g. close-herdr).
#                    Pass it PLAIN — this helper prefixes the task's state glyph
#                    (○ ● ◇ ◆ ✓, via herdr-tab-glyph.sh) itself, best-effort,
#                    onto the TAB LABEL only; the agent and session names keep
#                    the plain label (see the stamping block below).
#     worktree       absolute path to the worktree (becomes the new pane's cwd)
#     workspace-id   herdr workspace to open the tab in (e.g. $HERDR_WORKSPACE_ID)
#     agent-selector (launch only) agent-registry selector: a shorthand flag
#                    (--fable/--opus/--codex/--sol/--grok), a name
#                    (claude:opus), or a bare cli. Empty → legacy claude default.
#     session-name   (launch only) `claude -n` name; defaults to <label>
#
# On success (exit 0) prints key=value lines on stdout:
#   pane=<pane_id>   (empty on a resume that reused an already-open tab)
#   tab=<tab_id>     (launch: empty when the move into a dedicated tab failed;
#                     resume: the reused or freshly-created tab, empty only if unparsable)
#   moved=<yes|no>   (launch: no → the worker is a split in the CALLER's tab;
#                     resume: always yes — its own tab)
#   agent=<name>     (launch only) the resolved CLI×model (e.g. codex:gpt-5.6-sol,
#                     or plain `claude` for the legacy no-selector path)
# launch selector errors (nothing spawned): exit 2 = unknown selector; exit 3 =
# the CLI is not available, with stdout `unavailable=<name>` + `note=<hint>` so
# the caller can print a clear "run: … login".
# resume adds three keys so the caller never reports a resume that didn't happen:
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
# One resume-only terminal outcome, exit 0 with a single key and nothing else:
#   blocked=unverified  (the guard could NOT verify whether a tab is already open —
#                        herdr unreachable, an empty/repopulating pane list, or a pane
#                        with an unreadable cwd. Fail closed: the caller tells the user
#                        to CHECK herdr for an existing tab before reopening by hand, so
#                        no duplicate session is created.)
# On failure to launch (exit 1) prints nothing on stdout — the caller should show
# the manual instructions instead. Diagnostics always go to stderr.
set -eu

mode="${1:-}"
case "$mode" in
  launch|resume) shift ;;
  *)
    echo "usage: ${0##*/} {launch <label> <worktree> <workspace-id> [agent-selector] [session-name] | resume <label> <worktree> <workspace-id>}" >&2
    exit 1
    ;;
esac

label="${1:-}"
worktree="${2:-}"
workspace="${3:-}"
# launch-only positionals (resume ignores both — it always runs `claude -c`):
selector="${4:-}"        # agent-registry selector; empty = legacy claude default
session="${5:-$label}"   # claude -n session name (default: the label)

# Preconditions (shared). Any miss means "cannot automate" → caller falls back to
# the manual block.
command -v herdr   >/dev/null 2>&1 || { echo "herdr not on PATH" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not on PATH" >&2; exit 1; }
# launch takes an optional agent selector + session-name; resume takes neither.
[ "$mode" = launch ] && usage_tail=" [agent-selector] [session-name]" || usage_tail=""
[ -n "$label" ] && [ -n "$worktree" ] && [ -n "$workspace" ] || {
  echo "usage: ${0##*/} $mode <label> <worktree> <workspace-id>$usage_tail" >&2
  exit 1
}
[ -d "$worktree" ] || { echo "worktree dir not found: $worktree" >&2; exit 1; }

# Stamp the task's CURRENT state glyph onto the sidebar label (○ ● ◇ ◆ ✓ — the
# same mapping the [ws …] statusline renders; ws-statusline.sh is the single
# source, applied via herdr-tab-glyph.sh). Best-effort: any failure keeps the
# plain label.
#
# ONLY the TAB LABEL carries a glyph — `$label` stays plain for everything else.
# The tab label is what the sidebar renders and what `herdr-tab-glyph.sh refresh`
# keeps current as the task's state moves. The herdr *agent* name (`agent start
# <name>`) and the Claude session name are stable identities: a glyph there would
# freeze at its launch-time value (nothing refreshes them) and clutter /resume.
tab_label="$label"
glyph_helper="${0%/*}/herdr-tab-glyph.sh"
if [ -f "$glyph_helper" ]; then
  stamped="$(bash "$glyph_helper" prefix "$label" "$worktree" 2>/dev/null || true)"
  [ -n "$stamped" ] && tab_label="$stamped"
fi

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
#   error diagnostics: herdr emits {"error":{"code":…,"message":…}} on stderr for
#   every failed call above. Extract it defensively — any exception (not JSON, no
#   "error" key, …) yields nothing, never a traceback on the user's terminal.
extract_herdr_error='import sys, json
try:
    err = json.load(sys.stdin)["error"]
    print((err.get("code") or "") + "|" + (err.get("message") or ""))
except Exception:
    pass'

# herdr_diag <raw-stderr> <workspace-id> — turn a captured herdr stderr blob into
# one-or-two diagnostic lines: herdr's error.code/message when the blob parses as
# the JSON error schema, else the raw text relayed verbatim (trimmed). Prints
# nothing for an empty blob. Appends a one-line actionable hint when the error
# names an invalid placement target (the "stale $HERDR_WORKSPACE_ID after a herdr
# restart/handoff" case this repo has hit in practice) — never more than one hint
# line, so the diagnostic doesn't nag.
herdr_diag() {
  raw="$1"
  ws="$2"
  [ -n "$raw" ] || return 0
  parsed="$(printf '%s' "$raw" | python3 -c "$extract_herdr_error" 2>/dev/null || true)"
  if [ -n "$parsed" ]; then
    code="${parsed%%|*}"
    msg="${parsed#*|}"
    line="herdr error"
    [ -n "$code" ] && line="$line code=$code"
    [ -n "$msg" ] && line="$line: $msg"
  else
    code=""
    msg="$raw"
    line="herdr error: $(printf '%s' "$raw" | tr '\n' ' ' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
  case "$code" in
    agent_placement_not_found) stale=1 ;;
    *) case "$msg" in
         *"$ws"*"not found"*|*"$ws"*placement*) stale=1 ;;
         *) stale=0 ;;
       esac ;;
  esac
  [ "$stale" = 1 ] && line="$line
HERDR_WORKSPACE_ID=$ws is not a valid workspace on this herdr server (likely a stale id after a herdr restart/handoff) — relaunch from a live session or start the worker manually."
  printf '%s\n' "$line"
}

case "$mode" in
  launch)
    # Build the worker argv. The registry is the single source of truth for the
    # per-CLI launch shape; herdr-launch stays argv-exec (no shell-typing race)
    # and CLI-agnostic — it just execs whatever argv the registry resolves.
    #   no selector → legacy path: claude on the user's default model.
    #   a selector  → resolve it (claude/codex/grok, per model). exit 2 on an
    #                 unknown selector; exit 3 (with note) if the CLI is not
    #                 available, so the caller shows a clear "run: … login".
    worker_argv=()
    agent_name="claude"
    note=""
    if [ -z "$selector" ]; then
      worker_argv=(claude -n "$session" "/continue")
    else
      registry="${0%/*}/agent-registry.sh"
      [ -f "$registry" ] || { echo "agent-registry.sh not found next to herdr-launch.sh" >&2; exit 1; }
      rc=0
      # Keep resolve's stderr (it distinguishes its exit-2 causes: unknown
      # selector vs a rejected --session vs missing selector) instead of masking
      # them all as "unknown selector".
      resolve_err="$(mktemp)"
      resolve_out="$(bash "$registry" resolve "$selector" --session "$session" 2>"$resolve_err")" || rc=$?
      if [ "$rc" = 2 ]; then
        echo "agent selection failed for '$selector': $(tr '\n' ' ' < "$resolve_err")" >&2
        rm -f "$resolve_err"; exit 2
      fi
      rm -f "$resolve_err"
      while IFS= read -r line; do
        case "$line" in
          argv=*) worker_argv+=("${line#argv=}") ;;
          name=*) agent_name="${line#name=}" ;;
          note=*) note="${line#note=}" ;;
        esac
      done <<EOF_RESOLVE
$resolve_out
EOF_RESOLVE
      if [ "$rc" = 3 ]; then
        echo "agent $agent_name is not available${note:+ — $note}" >&2
        printf 'unavailable=%s\nnote=%s\n' "$agent_name" "$note"
        exit 3
      fi
      [ ${#worker_argv[@]} -gt 0 ] || { echo "agent-registry resolved no argv for $selector" >&2; exit 1; }
    fi

    # Spawn the worker as argv and read back the pane id. stderr is captured (not
    # discarded) so a failure can surface herdr's actual error.code/message instead
    # of only the generic "no pane id" guard below.
    start_err="$(mktemp)"
    start_json="$(herdr agent start "$label" --workspace "$workspace" \
      --cwd "$worktree" --no-focus -- "${worker_argv[@]}" 2>"$start_err" || true)"
    pane="$(printf '%s' "$start_json" | python3 -c "$extract_agent_pane" 2>/dev/null || true)"

    # Empty pane id → the agent did not start (broken socket / bad response).
    # Print herdr's own error first when captured — the generic message stays as
    # the last-resort fallback for a truly pane-less/malformed response.
    if [ -z "$pane" ]; then
      diag="$(herdr_diag "$(cat "$start_err")" "$workspace")"
      rm -f "$start_err"
      [ -n "$diag" ] && printf '%s\n' "$diag" >&2
      echo "herdr agent start did not return a pane id" >&2
      exit 1
    fi
    rm -f "$start_err"

    # caller's tab). If the move fails, the worker is still running — report it in
    # place rather than claiming a tab that does not exist. `agent=` tells the
    # caller which CLI×model was launched; the tab uses the glyph-stamped label.
    move_err="$(mktemp)"
    if move_json="$(herdr pane move "$pane" --new-tab --label "$tab_label" --no-focus 2>"$move_err")"; then
      tab="$(printf '%s' "$move_json" | python3 -c "$extract_moved_tab" 2>/dev/null || true)"
      rm -f "$move_err"
      printf 'pane=%s\ntab=%s\nmoved=yes\nagent=%s\n' "$pane" "$tab" "$agent_name"
    else
      diag="$(herdr_diag "$(cat "$move_err")" "$workspace")"
      rm -f "$move_err"
      [ -n "$diag" ] && printf '%s\n' "$diag" >&2
      echo "herdr pane move --new-tab failed for pane $pane (worker still running; no dedicated tab)" >&2
      printf 'pane=%s\ntab=\nmoved=no\nagent=%s\n' "$pane" "$agent_name"
    fi
    ;;

  resume)
    # Guard against a duplicate session. If a tab is ALREADY open at this worktree
    # cwd (e.g. the task was never `/exit`-ed), reopening would spawn a SECOND
    # `claude -c` on the same working tree — two sessions clobbering each other's
    # uncommitted changes. Ask the teardown helper's TRI-STATE cwd→tab lookup (the
    # single source of truth for realpath pane-cwd matching), with an empty workspace
    # so it searches all workspaces OF THIS HERDR SERVER. (A session for the same
    # worktree in a *separate* herdr server — another Ghostty tab — is invisible to
    # `herdr pane list` and cannot be deduped; accepted limitation.)
    #   <tab-id>    → reuse: focus it, start nothing new.
    #   none        → confidently no tab here → create below.
    #   unverified  → could NOT determine (herdr unreachable, an empty/repopulating pane
    #                 list, or an unreadable-cwd pane): FAIL CLOSED — do not risk a
    #                 duplicate. Emit blocked=unverified so the caller cues the user to
    #                 check herdr for an existing tab. (Also covers a missing helper.)
    teardown="${0%/*}/herdr-teardown.sh"
    state=unverified
    [ -f "$teardown" ] && state="$(bash "$teardown" worktree-tab-state "" "$worktree" 2>/dev/null || echo unverified)"
    case "$state" in
      none) : ;;   # fall through to create
      unverified)
        # FAIL CLOSED, but as a distinct outcome (exit 0 + blocked=unverified), NOT a
        # generic launch failure: the caller must cue the user to CHECK herdr for an
        # already-open tab before reopening by hand — the plain manual block would just
        # say "cd && claude -c" and risk the very duplicate this guard prevents. A
        # single key, matching the header contract (the caller branches on `blocked`).
        echo "resume: could not verify existing tabs for $worktree — not auto-creating (avoids a duplicate session)" >&2
        printf 'blocked=unverified\n'
        exit 0
        ;;
      *)
        # A tab already exists at this worktree → focus it, but do NOT assert a live
        # resume: a cwd match can't tell a live Claude from a bare shell that survived
        # a prior `/exit`. resumed is left EMPTY so the caller tells the user to run
        # `claude -c` if the tab is just a shell.
        # Re-stamp the reused tab's state glyph first: it may predate a PR state
        # change (stamped ● at kickoff, PR opened/merged meanwhile), and this path
        # otherwise never renames. Best-effort, like the launch stamp above.
        [ -f "$glyph_helper" ] && bash "$glyph_helper" refresh "$worktree" >/dev/null 2>&1 || true
        focused=yes
        herdr tab focus "$state" >/dev/null 2>&1 || focused=no
        printf 'pane=\ntab=%s\nmoved=yes\nreused=yes\nresumed=\nfocused=%s\n' "$state" "$focused"
        exit 0
        ;;
    esac

    # No existing tab — create a fresh one at the worktree and read back its root
    # (shell) pane id and tab id in one python3 pass. Split on the FIRST `|`, so an
    # empty pane id (with a present tab id) stays empty and trips the guard below,
    # rather than the tab id being mis-read as the pane id.
    create_err="$(mktemp)"
    create_json="$(herdr tab create --workspace "$workspace" \
      --cwd "$worktree" --label "$tab_label" 2>"$create_err" || true)"
    pane_tab="$(printf '%s' "$create_json" | python3 -c "$extract_root_pane_tab" 2>/dev/null || true)"
    pane="${pane_tab%%|*}"
    tab="${pane_tab#*|}"

    # Empty pane id → the tab did not open, or the response was malformed (broken
    # socket / bad JSON / pane-less result). Cannot run claude -c without a pane. If a
    # tab id WAS parsed (a pane-less/partial result from schema drift), the tab is real
    # and would be orphaned — close it before bailing so a drifted response can't leak a
    # blank tab on every resume; then the caller shows the manual block. herdr's own
    # error is printed first when captured — the generic message stays as the
    # last-resort fallback.
    if [ -z "$pane" ]; then
      [ -n "$tab" ] && herdr tab close "$tab" >/dev/null 2>&1 || true
      diag="$(herdr_diag "$(cat "$create_err")" "$workspace")"
      rm -f "$create_err"
      [ -n "$diag" ] && printf '%s\n' "$diag" >&2
      echo "herdr tab create did not return a pane id" >&2
      exit 1
    fi
    rm -f "$create_err"

    # Run `claude -c` INSIDE the shell pane — the /exit hardening (a later /exit
    # returns to the shell, keeping the tab alive). Prefix an explicit `cd <worktree>`
    # (shell-quoted): the pane is created with --cwd, but the shell's rc (direnv,
    # zoxide, an unconditional `cd` in .zshrc) can drift the cwd on startup, and
    # `claude -c` resumes the most-recent session FOR THE CURRENT cwd — so a drifted
    # cwd would silently attach to a different task's session. Re-anchoring keeps the
    # cwd→session mapping the header relies on. Report resumed=no if the send fails, so
    # the caller never claims a resume that didn't happen: the tab + shell exist, but
    # the user must run it by hand. (Catches a failed send, not `claude -c` erroring
    # later on a cwd with no prior session — the caller's wording stays tentative.)
    resumed=yes
    run_err="$(mktemp)"
    if ! herdr pane run "$pane" "cd $(printf '%q' "$worktree") && claude -c" >/dev/null 2>"$run_err"; then
      resumed=no
      diag="$(herdr_diag "$(cat "$run_err")" "$workspace")"
      echo "herdr pane run could not start 'claude -c' in $pane (tab is open; run it by hand)${diag:+ — $diag}" >&2
    fi
    rm -f "$run_err"

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
