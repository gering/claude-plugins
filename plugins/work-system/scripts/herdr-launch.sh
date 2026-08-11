#!/usr/bin/env bash
# herdr-launch.sh — open a worker session for a work-system task inside herdr.
#
# Two modes share one precondition/JSON-parse/output contract:
#
#   launch  (/kickoff, /adopt) — open a background tab running the chosen worker.
#                         herdr changed HOW that is done in 0.7.5, so the launcher
#                         feature-detects the contract (see "launch API" below) and
#                         speaks whichever this herdr offers:
#
#                         LEGACY (0.7.0-0.7.4) — herdr places the agent itself:
#                           agent start <name> --workspace <ws> --cwd <dir> --no-focus
#                                              -- <worker-argv>
#                           pane move <pane> --new-tab --label <label> --no-focus
#                         The worker is the tab's ROOT pane, so a later clean /exit
#                         ends the pane and herdr closes the tab.
#
#                         MODERN (0.7.5+, incl. 0.8) — `agent start` requires an
#                         ALREADY-OPEN pane at an interactive shell prompt, so the
#                         final tab is created FIRST and the worker started in its
#                         root pane; nothing is moved afterwards:
#                           tab create --workspace <ws> --cwd <dir> --label <label>
#                                      --no-focus
#                           agent start <name> --kind <kind> --pane <pane> -- <args>
#                         The worker therefore runs INSIDE a shell pane: a later
#                         /exit drops back to that shell and the tab survives, so
#                         /close's marker + SessionEnd hook (not agent exit) are the
#                         normal teardown path.
#
#                         Both paths keep the same stdout contract, and both avoid
#                         the shell-startup keystroke race (see the kickoff knowledge
#                         entry): legacy by exec'ing argv, modern by letting herdr's
#                         own `agent start` readiness handling drive the pane — its
#                         `agent_pane_busy` rejection is retried, bounded.
#                         The worker argv is resolved from an agent SELECTOR via
#                         agent-registry.sh (claude/codex/grok/kimi × model); with no
#                         selector it stays the legacy `claude … /work-system:continue`. The
#                         registry owns every per-CLI launch detail INCLUDING how the
#                         worker reaches modern herdr (`herdr_mode=agent-start` hands
#                         the argv tail to `--kind`; `herdr_mode=pane-run` sends the
#                         registry's `argv_shell=` as one `pane run` command for
#                         wrappers that cannot be projected onto a native kind), so
#                         this script stays CLI-agnostic.
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
#                    (--fable/--opus/--codex/--sol/--grok/--kimi), a name
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
# launch adds ONE fail-closed outcome, exit 0 with `blocked=unverified` first:
#   blocked=unverified
#   pane=<pane or empty>
#   tab=<tab or empty>
#   agent=<resolved agent>
# It means a tab exists and a worker MAY be running in it, but the launch could
# not be confirmed (readiness timeout, an unrecognized herdr error, a pane-id
# mismatch, or a rollback that could not be verified). Callers must branch on
# `blocked` BEFORE `moved`, tell the user to inspect that tab, and must NOT launch
# again, print the manual worker command, or save a project default.
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

# The four herdr-call stderr-capture tempfiles below are each cleaned up with an
# explicit `rm -f` on their own success/failure branches, but a SIGINT/SIGTERM
# between their `mktemp` and that `rm -f` would otherwise leak the file — matching
# the existing convention in herdr-tab-glyph.sh. `${var:-}` tolerates a variable
# that isn't assigned yet (or on a code path that never used it) under `set -u`.
trap 'rm -f "${start_err:-}" "${move_err:-}" "${create_err:-}" "${run_err:-}" "${close_err:-}"' EXIT

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

# bounded_run <seconds> <cmd...> — run cmd under a hard time bound, so a wedged
# herdr can never hang a launch. Prints cmd's stdout; returns cmd's exit code, or
# 124 if the bound killed it. Uses timeout/gtimeout when present, else a detached
# killer that escalates SIGTERM → SIGKILL (a process ignoring SIGTERM must not
# block us either). The killer's fds go to /dev/null: this runs inside a command
# substitution, and a background job holding the captured pipe would make $(…)
# block until it exits. agent-registry.sh carries the same primitive for its CLI
# probes; kept local here so the launcher has no cross-script runtime dependency.
bounded_run() {
  local secs="$1"; shift
  local rc=0
  if command -v timeout >/dev/null 2>&1; then
    timeout  -k 1 "$secs" "$@" || rc=$?
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout -k 1 "$secs" "$@" || rc=$?
  else
    local tmp; tmp="$(mktemp)"
    "$@" >"$tmp" 2>&1 &
    local pid=$!
    ( sleep "$secs"; kill "$pid" 2>/dev/null; sleep 2; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
    local killer=$!
    if wait "$pid" 2>/dev/null; then rc=0; else rc=$?; fi
    kill "$killer" 2>/dev/null || true
    cat "$tmp"; rm -f "$tmp"
  fi
  case "$rc" in 137|143) rc=124 ;; esac
  return "$rc"
}

# ---- launch API detection --------------------------------------------------
# Which `agent start` contract this herdr speaks, read ONLY from its --help (no
# process is started to find out) and NEVER from a version string: 0.7.x spans
# BOTH contracts (the change landed in 0.7.5), so a version compare would route
# half of that range to the wrong path.
#   modern  --kind AND --pane offered  → tab create first, start into that pane
#   legacy  --workspace AND --cwd      → herdr places the agent, then pane move
#   unknown neither                    → fail BEFORE creating or starting anything
# $WORK_SYSTEM_HERDR_API forces the answer (tests, and an escape hatch if a future
# herdr's help text stops naming its flags).
detect_launch_api() {
  [ -n "${WORK_SYSTEM_HERDR_API:-}" ] && { printf '%s\n' "$WORK_SYSTEM_HERDR_API"; return 0; }
  local help
  help="$(bounded_run 10 herdr agent start --help 2>&1 || true)"
  if printf '%s' "$help" | grep -q -- '--kind' && printf '%s' "$help" | grep -q -- '--pane'; then
    printf 'modern\n'
  elif printf '%s' "$help" | grep -q -- '--workspace' && printf '%s' "$help" | grep -q -- '--cwd'; then
    printf 'legacy\n'
  else
    printf 'unknown\n'
  fi
}

# Bounds for the modern path. All overridable so tests stay hermetic and fast.
START_TIMEOUT_MS="${WORK_SYSTEM_HERDR_START_TIMEOUT_MS:-30000}"  # herdr's own readiness wait
BUSY_TRIES="${WORK_SYSTEM_HERDR_BUSY_TRIES:-20}"                 # agent_pane_busy retries
READY_TRIES="${WORK_SYSTEM_HERDR_READY_TRIES:-20}"               # shell-prompt wait (pane-run)
DETECT_TRIES="${WORK_SYSTEM_HERDR_DETECT_TRIES:-60}"             # wrapper detection polls
RETRY_DELAY="${WORK_SYSTEM_HERDR_RETRY_DELAY:-0.5}"              # seconds between polls

# JSON extractors. null / missing / malformed all yield empty, and a stray
# traceback can never reach the user's terminal.
#   launch: agent start → result.agent.pane_id, then move → the created tab id.
extract_agent_pane='import sys, json
try:
    print(json.load(sys.stdin)["result"]["agent"]["pane_id"] or "")
except Exception:
    pass'
#   modern launch: the detected agent kind of one pane (`pane get`), i.e. what
#   herdr believes is running there. Empty for a plain shell pane.
extract_pane_agent='import sys, json
try:
    print(json.load(sys.stdin)["result"]["pane"].get("agent") or "")
except Exception:
    pass'
#   modern launch (pane-run): is the pane back at its own shell prompt, i.e. safe
#   to type a command into? True when the foreground process group IS the shell —
#   during rc-file startup (direnv, sops, ssh-add …) a child holds it instead, and
#   text sent then can be eaten. `unknown` when the answer cannot be read at all
#   (older herdr without process-info), which the caller degrades on rather than
#   blocking a launch forever.
extract_shell_ready='import sys, json
try:
    pi = json.load(sys.stdin)["result"]["process_info"]
    fg, shell = pi.get("foreground_process_group_id"), pi.get("shell_pid")
    print("ready" if (fg is not None and fg == shell) else "busy")
except Exception:
    print("unknown")'
extract_moved_tab='import sys, json
try:
    print(json.load(sys.stdin)["result"]["move_result"]["created_tab"]["tab_id"] or "")
except Exception:
    pass'
#   tab create (BOTH the modern launch and resume) → the new tab's root pane id and
#   its tab id in ONE pass, pipe-delimited as `<pane>|<tab>`. Verified against herdr
#   0.8: `result.root_pane.pane_id` + `result.tab.tab_id`. The fallbacks are
#   deliberate and each covers a real shape — a flat `result.tab_id` (what earlier
#   herdr returned, still exercised by the tests) and the tab id echoed under the
#   root pane. A single `|` is ALWAYS printed, so the caller splits on the first `|`
#   — an empty pane with a present tab yields pane="" (which then fails the
#   non-empty-pane guard) instead of the tab being mis-read as the pane. herdr ids
#   are `wN:pM`/`wN:tM` and never contain `|`. A partial response therefore still
#   surrenders whatever id IS there, which is what lets the caller clean up a tab
#   that came back without a usable pane instead of orphaning it.
extract_root_pane_tab='import sys, json
try:
    r = json.load(sys.stdin)["result"]
    root = r.get("root_pane") or {}
    pane = root.get("pane_id") or (r.get("pane") or {}).get("pane_id") or r.get("pane_id") or ""
    tab = (r.get("tab") or {}).get("tab_id") or r.get("tab_id") or root.get("tab_id") or ""
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

# stale_ws_check <workspace-id> — read a message on stdin, print 1 if $ws names
# the failing placement target, else 0. Bounded-token match (not a bare
# substring — ws=w1 must not match a message naming w12) with the "not
# found"/"placement" keyword required in the SAME clause as the token (split on
# ;/./,), not merely present anywhere else in the message — a fixed character
# window is not enough: "workspace w1 is healthy; agent placement is
# unavailable" puts the keyword within any reasonable window of a short
# sentence's token even though the two halves are unrelated clauses.
# Case-insensitive ("Not Found" from herdr must still match). python3's
# re.escape covers every ERE metacharacter — a hand-rolled sed/grep escape
# class is exactly the kind of thing that quietly misses one (`+ ? ( ) { } |`
# are the ones a naive `. [ \ * ^ $` class leaves live).
stale_ws_check='import sys, re
ws = sys.argv[1] if len(sys.argv) > 1 else ""
msg = sys.stdin.read()
if not ws:
    print(0); sys.exit(0)
tok_re = re.compile(r"(?<![A-Za-z0-9_:.-])" + re.escape(ws) + r"(?![A-Za-z0-9_:.-])")
kw_re = re.compile(r"not\s*found|placement", re.IGNORECASE)
hit = any(tok_re.search(clause) and kw_re.search(clause) for clause in re.split(r"[;.,]", msg))
print(1 if hit else 0)'

# herdr_diag <raw-stderr> <workspace-id> <ws-relevant 0|1> — turn a captured herdr
# stderr blob into one-or-two diagnostic lines: herdr's error.code/message when
# the blob parses as the JSON error schema, else the raw text relayed verbatim
# (trimmed). Prints nothing for an empty blob. Control/escape bytes are stripped
# TWICE — once from the raw blob, once from the code/message pulled out of it —
# because a JSON ``-style escape is still plain printable text before
# python3's json.load decodes it into a real ESC byte; stripping only the raw
# blob lets a compromised herdr's escaped control sequence survive decoding and
# reach the terminal, defeating the whole point of the filter.
# <ws-relevant> gates the stale-$HERDR_WORKSPACE_ID hint: pass 1 only for calls
# that actually send `--workspace $ws` (agent start, tab create) — for calls that
# don't (pane move, pane run) an agent_placement_not_found can't be a workspace-id
# problem, so the hint would misattribute the cause. $ws itself is sanitized
# before it's ever interpolated into the printed hint (it comes from
# $HERDR_WORKSPACE_ID, an environment value this script doesn't control).
# Never more than one hint line, so the diagnostic doesn't nag.
herdr_diag() {
  local raw ws ws_relevant clean parsed code msg line stale ws_hit ws_clean
  raw="$1"
  ws="$2"
  ws_relevant="${3:-1}"
  [ -n "$raw" ] || return 0
  clean="$(printf '%s' "$raw" | tr -dc '[:print:]\t\n')"
  parsed="$(printf '%s' "$clean" | python3 -c "$extract_herdr_error" 2>/dev/null || true)"
  if [ -n "$parsed" ]; then
    code="$(printf '%s' "${parsed%%|*}" | tr -dc '[:print:]\t\n')"
    msg="$(printf '%s' "${parsed#*|}" | tr -dc '[:print:]\t\n')"
    line="herdr error"
    [ -n "$code" ] && line="$line code=$code"
    [ -n "$msg" ] && line="$line: $msg"
  else
    code=""
    msg="$clean"
    line="herdr error: $(printf '%s' "$clean" | tr '\n' ' ' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
  stale=0
  if [ "$ws_relevant" = 1 ]; then
    case "$code" in
      agent_placement_not_found) stale=1 ;;
    esac
    if [ "$stale" != 1 ] && [ -n "$ws" ]; then
      ws_hit="$(printf '%s' "$msg" | python3 -c "$stale_ws_check" "$ws" 2>/dev/null || echo 0)"
      [ "$ws_hit" = 1 ] && stale=1
    fi
  fi
  if [ "$stale" = 1 ]; then
    ws_clean="$(printf '%s' "$ws" | tr -dc '[:print:]\t\n')"
    line="$line
HERDR_WORKSPACE_ID=$ws_clean is not a valid workspace on this herdr server (likely a stale id after a herdr restart/handoff) — relaunch from a live session or start the worker manually."
  fi
  printf '%s\n' "$line"
}

# ---- modern-launch outcome helpers ----------------------------------------
# These read $workspace / $pane / $tab / $agent_name from the launch scope; they
# exist to keep the three outcome shapes (success, rolled-back failure, fail-closed
# unverified) in ONE place instead of repeated inline at every error site.

# classify_start_failure <rc> <stderr-blob> → busy | definitive | ambiguous
#   busy       herdr refused because the pane's shell was not at a prompt yet. This
#              is THE failure right after `tab create` (a login shell running
#              direnv/sops/ssh-add takes seconds), so it is retried — bounded, and
#              ONLY for this exact code. Every other error retries nothing.
#   definitive herdr rejected the call before touching the pane, so nothing was
#              started and the created tab can be rolled back: a usage error (exit
#              2, plain text — "unsupported interactive agent kind", "missing
#              required --pane", "unknown option: …") or a no-such-pane/placement
#              error code.
#   ambiguous  anything else, INCLUDING a readiness timeout: the worker may be
#              coming up in that pane. Never rolled back, never retried — reported
#              as blocked=unverified so the user inspects the tab.
classify_start_failure() {
  local rc="$1" blob="$2" code
  [ "$rc" = 2 ] && { printf 'definitive\n'; return 0; }
  code="$(printf '%s' "$blob" | tr -dc '[:print:]\t\n' | python3 -c "$extract_herdr_error" 2>/dev/null || true)"
  code="${code%%|*}"
  case "$code" in
    agent_pane_busy) printf 'busy\n' ;;
    agent_pane_not_found|agent_target_not_found|agent_placement_not_found|pane_not_found)
      printf 'definitive\n' ;;
    *) printf 'ambiguous\n' ;;
  esac
}

# rollback_tab <tab-id> → closed | unverified. Undo a tab THIS run created, exactly
# once. Delegates to herdr-teardown.sh's `close-tab`, the single source of truth for
# close-then-confirm (it closes once, never re-issues — a recycled tab id would
# otherwise kill an unrelated live tab — and polls until the tab is gone). Nothing
# to close counts as `closed`; anything we cannot confirm is `unverified`, so an
# unconfirmed rollback is never reported to the user as a clean failure.
rollback_tab() {
  local t="${1:-}" td state
  [ -n "$t" ] || { printf 'closed\n'; return 0; }
  td="${0%/*}/herdr-teardown.sh"
  [ -f "$td" ] || { printf 'unverified\n'; return 0; }
  state="$(bash "$td" close-tab "$t" "$workspace" 2>/dev/null || echo unverified)"
  case "$state" in closed) printf 'closed\n' ;; *) printf 'unverified\n' ;; esac
}

# emit_unverified — the fail-closed launch outcome (exit 0, `blocked` FIRST).
emit_unverified() {
  printf 'blocked=unverified\npane=%s\ntab=%s\nagent=%s\n' "${pane:-}" "${tab:-}" "${agent_name:-}"
}

# fail_definitive <message…> — a failure we KNOW left no worker running: report it,
# roll the created tab back, exit 1 so the caller shows the manual block. If the
# rollback cannot be CONFIRMED, the outcome is downgraded to blocked=unverified —
# a tab that may still exist (possibly with something in it) must not be reported
# as a clean failure.
fail_definitive() {
  printf '%s\n' "$*" >&2
  local rb; rb="$(rollback_tab "${tab:-}")"
  if [ "$rb" = closed ]; then
    [ -n "${tab:-}" ] && printf 'rolled back the tab created for this launch (%s)\n' "$tab" >&2
    exit 1
  fi
  printf 'could not confirm that the tab created for this launch (%s) was closed — check it by hand\n' "${tab:-?}" >&2
  emit_unverified
  exit 0
}

# pane_agent_kind <pane> — what herdr currently detects in that pane ("" = nothing).
pane_agent_kind() {
  herdr pane get "$1" 2>/dev/null | python3 -c "$extract_pane_agent" 2>/dev/null || true
}

# pane_has_marker <pane> <token> — is the wrapper's seed-failure marker on screen?
# `--source visible` is the snapshot that actually holds a settled pane's text; the
# default `recent` returns nothing once output has stopped (verified live).
pane_has_marker() {
  herdr pane read "$1" --source visible --lines 200 --format text 2>/dev/null \
    | grep -qF -- "$2"
}

# wait_shell_ready <pane> → ready | busy | unknown. Bounded wait for the pane to be
# back at ITS OWN shell prompt before typing into it (the `pane run` path has no
# herdr-side readiness check the way `agent start` does). `unknown` means herdr
# cannot answer at all (no process-info) — the caller degrades and proceeds rather
# than blocking a launch forever, since the detection step still gates success.
wait_shell_ready() {
  local p="$1" i=0 st=unknown
  while [ "$i" -lt "$READY_TRIES" ]; do
    st="$(herdr pane process-info --pane "$p" 2>/dev/null | python3 -c "$extract_shell_ready" 2>/dev/null || echo unknown)"
    case "$st" in
      ready|unknown) printf '%s\n' "$st"; return 0 ;;
    esac
    i=$((i + 1))
    sleep "$RETRY_DELAY" 2>/dev/null || true
  done
  printf 'busy\n'
}

case "$mode" in
  launch)
    # Build the worker argv. The registry is the single source of truth for the
    # per-CLI launch shape; herdr-launch stays argv-exec (no shell-typing race)
    # and CLI-agnostic — it just execs whatever argv the registry resolves.
    #   no selector → legacy path: claude on the user's default model.
    #   a selector  → resolve it (claude/codex/grok/kimi, per model). exit 2 on an
    #                 unknown selector; exit 3 (with note) if the CLI is not
    #                 available, so the caller shows a clear "run: … login".
    worker_argv=()
    agent_name="claude"
    note=""
    pane=""
    tab=""
    argv_shell=""
    # Modern-herdr transport, declared by the registry (never inferred here). The
    # no-selector legacy default IS a canonical `claude` invocation, so it carries
    # the same agent-start/claude contract.
    herdr_mode="agent-start"
    herdr_kind="claude"
    herdr_marker=""
    if [ -z "$selector" ]; then
      # Plugin-qualified: a CC built-in/alias `/continue` shadows the skill.
      worker_argv=(claude -n "$session" "/work-system:continue")
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
          argv=*)         worker_argv+=("${line#argv=}") ;;
          argv_shell=*)   argv_shell="${line#argv_shell=}" ;;
          name=*)         agent_name="${line#name=}" ;;
          note=*)         note="${line#note=}" ;;
          herdr_mode=*)   herdr_mode="${line#herdr_mode=}" ;;
          herdr_kind=*)   herdr_kind="${line#herdr_kind=}" ;;
          herdr_marker=*) herdr_marker="${line#herdr_marker=}" ;;
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

    # Which contract does this herdr speak? Decided BEFORE anything is created or
    # started, so an unrecognizable herdr stops here instead of guessing and
    # leaving a blank tab behind.
    api="$(detect_launch_api)"
    case "$api" in
      modern|legacy) : ;;
      *)
        echo "cannot identify this herdr's 'agent start' contract: its --help offers neither --kind/--pane (0.7.5+) nor --workspace/--cwd (0.7.0-0.7.4)" >&2
        echo "nothing was created and no worker was started — start it by hand" >&2
        exit 1
        ;;
    esac

    if [ "$api" = legacy ]; then
      # ---- LEGACY (herdr 0.7.0-0.7.4): herdr places the agent, we move it ----
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
        diag="$(herdr_diag "$(cat "$start_err")" "$workspace" 1)"
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
        diag="$(herdr_diag "$(cat "$move_err")" "$workspace" 0)"
        rm -f "$move_err"
        [ -n "$diag" ] && printf '%s\n' "$diag" >&2
        echo "herdr pane move --new-tab failed for pane $pane (worker still running; no dedicated tab)" >&2
        printf 'pane=%s\ntab=\nmoved=no\nagent=%s\n' "$pane" "$agent_name"
      fi
      exit 0
    fi

    # ---- MODERN (herdr 0.7.5+): create the tab, then start into its pane ----
    # Validate the transport FIRST — an entry this launcher cannot start must fail
    # before `tab create`, or every attempt leaves a blank tab behind.
    native_argv=()
    case "$herdr_mode" in
      agent-start)
        if [ -z "$herdr_kind" ]; then
          echo "agent $agent_name declares herdr_mode=agent-start without a herdr_kind — refusing to launch (nothing was created)" >&2
          exit 1
        fi
        # `--kind` names herdr's canonical executable, so the resolved argv must
        # START with exactly that word and we drop exactly that word. No rebuilding:
        # every remaining argument keeps its order and boundaries (the one-word
        # bootstrap prompt for codex/grok, the one-word /work-system:continue for
        # claude), which is why this is a check and not a transformation.
        if [ "${worker_argv[0]}" != "$herdr_kind" ]; then
          echo "agent $agent_name resolves to '${worker_argv[0]}' but declares herdr kind '$herdr_kind' — refusing to launch (nothing was created)" >&2
          exit 1
        fi
        [ "${#worker_argv[@]}" -gt 1 ] && native_argv=("${worker_argv[@]:1}")
        ;;
      pane-run)
        if [ -z "$herdr_kind" ] || [ -z "$argv_shell" ]; then
          echo "agent $agent_name declares herdr_mode=pane-run but is missing herdr_kind/argv_shell — refusing to launch (nothing was created)" >&2
          exit 1
        fi
        ;;
      *)
        echo "agent $agent_name declares an unknown herdr transport '${herdr_mode:-<empty>}' — refusing to launch (nothing was created)" >&2
        exit 1
        ;;
    esac

    # Create the FINAL tab up front (background, glyph-stamped label) and read back
    # its root pane. From here on any failure has a tab to account for.
    create_err="$(mktemp)"
    create_json="$(herdr tab create --workspace "$workspace" --cwd "$worktree" \
      --label "$tab_label" --no-focus 2>"$create_err" || true)"
    pane_tab="$(printf '%s' "$create_json" | python3 -c "$extract_root_pane_tab" 2>/dev/null || true)"
    pane="${pane_tab%%|*}"
    tab="${pane_tab#*|}"
    if [ -z "$pane" ]; then
      # No pane → nothing can be started here. A tab id that DID parse belongs to a
      # real tab that would be orphaned, so fail_definitive closes it (once, verified).
      diag="$(herdr_diag "$(cat "$create_err")" "$workspace" 1)"
      rm -f "$create_err"
      [ -n "$diag" ] && printf '%s\n' "$diag" >&2
      fail_definitive "herdr tab create did not return a root pane id"
    fi
    rm -f "$create_err"

    if [ "$herdr_mode" = agent-start ]; then
      # ---- native worker: hand the argv TAIL to --kind, unchanged ----
      attempt=0
      while : ; do
        start_rc=0
        start_err="$(mktemp)"
        if [ "${#native_argv[@]}" -gt 0 ]; then
          start_json="$(herdr agent start "$label" --kind "$herdr_kind" --pane "$pane" \
            --timeout "$START_TIMEOUT_MS" -- "${native_argv[@]}" 2>"$start_err")" || start_rc=$?
        else
          start_json="$(herdr agent start "$label" --kind "$herdr_kind" --pane "$pane" \
            --timeout "$START_TIMEOUT_MS" 2>"$start_err")" || start_rc=$?
        fi
        start_blob="$(cat "$start_err")"
        rm -f "$start_err"
        [ "$start_rc" = 0 ] && break

        class="$(classify_start_failure "$start_rc" "$start_blob")"
        # Retry ONLY the "shell not ready yet" rejection, bounded. Everything else
        # falls straight through — a retry loop over an unrelated error is how a
        # launcher ends up starting two workers.
        if [ "$class" = busy ] && [ "$attempt" -lt "$BUSY_TRIES" ]; then
          attempt=$((attempt + 1))
          sleep "$RETRY_DELAY" 2>/dev/null || true
          continue
        fi

        # This call never sends --workspace, so a placement error here cannot be a
        # stale-workspace problem: ws_relevant=0 keeps that hint off.
        diag="$(herdr_diag "$start_blob" "$workspace" 0)"
        [ -n "$diag" ] && printf '%s\n' "$diag" >&2
        if [ "$class" = busy ]; then
          fail_definitive "pane $pane never reached an interactive shell prompt (herdr kept reporting agent_pane_busy over $BUSY_TRIES retries) — nothing was started"
        fi
        if [ "$class" = definitive ]; then
          fail_definitive "herdr agent start --kind $herdr_kind was rejected before starting anything in pane $pane"
        fi
        echo "herdr agent start --kind $herdr_kind did not confirm the worker in pane $pane — it may still be coming up, so the tab was left alone" >&2
        emit_unverified
        exit 0
      done

      # A successful modern start must report back the SAME pane we asked for.
      # Anything else (missing, malformed, a different id) means we cannot say what
      # ran where — and since herdr claimed success, something may well be running
      # in that tab, so this is NOT a rollback case.
      started_pane="$(printf '%s' "$start_json" | python3 -c "$extract_agent_pane" 2>/dev/null || true)"
      if [ "$started_pane" != "$pane" ]; then
        echo "herdr agent start reported success for pane '${started_pane:-<none>}' but the worker was requested in $pane — cannot confirm the launch" >&2
        emit_unverified
        exit 0
      fi
      # `moved=yes` = the verified worker is in its own dedicated tab. On this path
      # no physical move happened (the tab was created first); the key is kept for
      # caller compatibility.
      printf 'pane=%s\ntab=%s\nmoved=yes\nagent=%s\n' "$pane" "$tab" "$agent_name"
      exit 0
    fi

    # ---- wrapper worker (herdr_mode=pane-run) ----
    # The registry's argv_shell is sent as EXACTLY ONE `pane run` command word: no
    # eval, no re-quoting, no ${array[*]} — the registry already quoted it for a
    # POSIX shell, and re-quoting a script carrying `;`/`exec` is how a paste ends
    # up replacing the wrong shell.
    ready="$(wait_shell_ready "$pane")"
    if [ "$ready" = busy ]; then
      fail_definitive "pane $pane never reached its shell prompt within the readiness window — nothing was typed into it"
    fi
    run_rc=0
    run_err="$(mktemp)"
    herdr pane run "$pane" "$argv_shell" >/dev/null 2>"$run_err" || run_rc=$?
    run_blob="$(cat "$run_err")"
    rm -f "$run_err"
    if [ "$run_rc" != 0 ]; then
      diag="$(herdr_diag "$run_blob" "$workspace" 0)"
      [ -n "$diag" ] && printf '%s\n' "$diag" >&2
      if [ "$(classify_start_failure "$run_rc" "$run_blob")" = definitive ]; then
        fail_definitive "herdr pane run could not deliver the $agent_name wrapper to pane $pane"
      fi
      echo "herdr pane run returned an error for pane $pane but may still have delivered the command — the tab was left alone" >&2
      emit_unverified
      exit 0
    fi

    # Bounded detection. Success is ONLY herdr recognizing the expected worker in
    # this exact pane; a timeout, a different kind, or an unreadable state is
    # reported as unverified — never as success, and never as a reason to start a
    # second one. The wrapper's seed-failure marker is the one signal that IS
    # definitive: it proves the worker exited without starting the task.
    i=0
    while [ "$i" -lt "$DETECT_TRIES" ]; do
      detected="$(pane_agent_kind "$pane")"
      if [ "$detected" = "$herdr_kind" ]; then
        printf 'pane=%s\ntab=%s\nmoved=yes\nagent=%s\n' "$pane" "$tab" "$agent_name"
        exit 0
      fi
      if [ -n "$herdr_marker" ] && pane_has_marker "$pane" "$herdr_marker"; then
        fail_definitive "the $agent_name worker's seed phase failed in pane $pane (marker: $herdr_marker) — TASK.md was not started"
      fi
      if [ -n "$detected" ]; then
        echo "herdr detects '$detected' in pane $pane, not the expected '$herdr_kind' — cannot confirm the launch" >&2
        emit_unverified
        exit 0
      fi
      i=$((i + 1))
      sleep "$RETRY_DELAY" 2>/dev/null || true
    done
    echo "the $agent_name worker did not become detectable as '$herdr_kind' in pane $pane within the detection window — it may still be starting, so the tab was left alone" >&2
    emit_unverified
    exit 0
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
      if [ -n "$tab" ]; then
        close_err="$(mktemp)"
        if ! herdr tab close "$tab" >/dev/null 2>"$close_err"; then
          close_diag="$(herdr_diag "$(cat "$close_err")" "$workspace" 0)"
          [ -n "$close_diag" ] && printf 'herdr tab close cleanup for orphaned tab %s also failed: %s\n' "$tab" "$close_diag" >&2
        fi
        rm -f "$close_err"
      fi
      diag="$(herdr_diag "$(cat "$create_err")" "$workspace" 1)"
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
      diag="$(herdr_diag "$(cat "$run_err")" "$workspace" 0)"
      [ -n "$diag" ] && printf '%s\n' "$diag" >&2
      echo "herdr pane run could not start 'claude -c' in $pane (tab is open; run it by hand)" >&2
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
