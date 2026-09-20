#!/usr/bin/env bash
# insights-handoff.sh — work-system's ONE bridge to the optional `insights` plugin.
#
# The lifecycle producers (/close's pre-teardown report, a worker's terminal-gate
# handoff report) must not each carry their own copy of "where is insights, is it
# usable, has this task already been reported". That prose drifts; this script is
# the single source of truth for it. It NEVER writes a report file itself and
# never duplicates insights' validation or redaction — every write goes through
# insights.py, which owns the `insights.report/v1` contract (see that plugin's
# docs/REPORT-CONTRACT.md).
#
# insights is DETECTED, never required (skill-composition rule: plugins stay
# independently installable). "Not installed" is a normal outcome that leaves
# every existing work-system flow unchanged — which is why it gets its own exit
# code, distinct from "installed but unusable". Collapsing the two would let a
# broken python3 or a corrupt store look like an uninstalled plugin and silently
# drop reports.
#
# CWD-safe: every path is explicit and the script never `cd`s outside a subshell
# (cwd-safety rule).
#
# ## Why `prepare` exists
#
# The callers used to run probe → reported → skeleton themselves and paste the
# task name and branch into the command line. Two problems, one fix:
#   * A repo-derived name reaches a shell as TEXT. Refnames and task filenames
#     may contain `$(…)` and backticks, and double quotes do not suppress command
#     substitution — so a crafted branch name executed during a routine /close.
#     `prepare` takes a DIRECTORY and derives the name itself, so nothing
#     repo-authored is ever pasted into a command by the model.
#   * The same four-step protocol was restated in two SKILLs. One subcommand that
#     answers skip / absent / unusable / draft keeps the decision in one place.
#
# Subcommands
#   probe
#       Report whether insights can be used from here, and where its helper and
#       report contract live. Always exits 0 — the answer is in `status=`, because
#       "absent" is not an error for a caller that only wants to know. Callers read
#       `contract=` instead of spelling a path: `<plugin-root>/../insights/…` is
#       only correct in the dev layout, and in the marketplace cache
#       (…/<plugin>/<version>/) it silently points at nothing.
#   prepare <handoff|close|manual> --caller <skill> --lane <dir> [--project-dir DIR]
#           [--pr <n>] [--status <task-status>] [--resolve-from <file>]
#       --resolve-from names a file holding `task-status.sh` output; the bridge
#       reads `task_name=`/`task_branch=` from it instead of resolving the lane
#       itself. /close needs this: when the worktree is already gone (a retried
#       teardown) the lane is the MAIN checkout sitting on `main`, where
#       task-status.sh resolves an EMPTY task name — the idempotency lookup was
#       then skipped, every retry drafted another report, and each one was stored
#       without a task_name so no later lookup could ever find it. Passing the
#       name as a FILE keeps it out of any command line: a refname may legally
#       contain `$(…)`, which a shell expands before this script sees an argument.
#       Everything a producer needs, in one call: resolve insights, derive the
#       lane's task name and branch via task-status.sh, look up what this project
#       already has for that task, and — unless there is nothing to do — leave a
#       contract-complete draft on disk with the observed facts filled in.
#       Prints `action=skip|draft` plus the facts behind it.
#   reported <task-name> [--trigger T] [--project-dir DIR]
#       The primitive behind `prepare`'s lookup, kept addressable for callers that
#       only want to know what exists.
#   write <draft-file> [--project-dir DIR]
#       Store a finished draft.
#   note-file
#       Create an empty, private note file and print its path. The /close
#       fallback note must land somewhere `archive-task.sh --note-file` accepts,
#       and that is NOT "your scratchpad": on macOS the session scratchpad
#       (/private/tmp/claude-…) is a different tree from $TMPDIR (/var/folders/…),
#       so a note written where the skill said was refused by the script — in the
#       one path that exists for when everything else already failed. Creating it
#       here means the two cannot disagree: both sides use ${TMPDIR:-/tmp}.
#   redact <file> [--in-place]
#       Run insights' own credential redaction over a plain text file, printing
#       the redacted text, or rewriting the file when --in-place is given.
#       --in-place exists so the caller does not have to perform a
#       redirect-then-rename ritual by hand at the one moment when that file is
#       the last surviving copy of the observation: a `> file.tmp && mv` written
#       out as prose loses the note whenever any step of it goes wrong. For the one thing that is NOT a report: the compact
#       summary /close preserves in the archived task file when a write failed.
#       That archive can be committed and pushed, and "the model was told not to
#       paste a secret" is not a boundary. Redaction lives in insights (one set of
#       patterns); this only exposes it.
#
# Exit codes — THIS script's namespace. insights.py's codes are MAPPED into it,
# never relayed raw: its 2 (usage) and 3 (ID collision) would otherwise collide
# with 2 (bad argv here) and 3 (absent), and a collision would read as "insights
# is not installed, skip silently".
#   0  answered / stored / unchanged
#   1  the draft was rejected — nothing saved
#   2  usage error in THIS script's arguments
#   3  insights is not installed — nothing to do, not a failure
#   4  insights is installed but unusable, or storage failed — must be surfaced
#   5  a DIFFERENT report is already stored under this report_id — never overwritten
#
# Nothing here decides anything about the task: a report is never evidence that a
# task is merged, done, or safe to tear down, and a failed report never becomes a
# cleanup gate. The callers own those decisions.

set -uo pipefail

EXIT_OK=0; EXIT_INVALID=1; EXIT_USAGE=2; EXIT_ABSENT=3; EXIT_UNUSABLE=4; EXIT_COLLISION=5

# Where this script lives. `${0%/*}` is NOT enough: invoked through $PATH or as a
# bare name it yields the filename, and every sibling path built from it would be
# relative to the CALLER's cwd (same trap documented in agent-registry.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || SCRIPT_DIR=""
# shellcheck source=lib-bounded.sh
[ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/lib-bounded.sh" ] && . "$SCRIPT_DIR/lib-bounded.sh"
# shellcheck source=lib-stat.sh
[ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/lib-stat.sh" ] && . "$SCRIPT_DIR/lib-stat.sh"

# Temp files hold helper stderr and skeleton drafts. Clean them on ANY exit,
# including a signal: a draft is unredacted until insights.py touches it, and a
# /close that is interrupted must not leave one behind.
TMPFILES=()
cleanup() { [ ${#TMPFILES[@]} -gt 0 ] && rm -f "${TMPFILES[@]}" 2>/dev/null; return 0; }
trap cleanup EXIT INT TERM HUP

# Returns the path in MKTEMP_OUT, NOT on stdout. Every caller used
# `f="$(mktemp_tracked)"`, and a command substitution runs in a SUBSHELL — so the
# `TMPFILES+=` landed in a child that exited immediately and the trap above was
# cleaning an array that was always empty. Verified: the parent's array stayed at
# length 0. A global out-parameter is ugly; a cleanup trap that silently does
# nothing is worse.
MKTEMP_OUT=""
mktemp_tracked() {
  MKTEMP_OUT="$(mktemp "${TMPDIR:-/tmp}/insights-handoff.XXXXXX")" || return 1
  chmod 600 "$MKTEMP_OUT" 2>/dev/null || true
  TMPFILES+=("$MKTEMP_OUT")
}

# Drop one path from the tracked set. `TMPFILES=("${TMPFILES[@]/$p}")` looks like
# removal but is per-element SUBSTRING substitution: the entry becomes an empty
# string that stays in the array, and any other path containing this one as a
# substring is silently mangled into a different path the trap would then delete.
untrack() {
  local keep=() t
  for t in ${TMPFILES[@]+"${TMPFILES[@]}"}; do
    [ "$t" = "$1" ] || keep+=("$t")
  done
  TMPFILES=(${keep[@]+"${keep[@]}"})
}

# A local helper call that wedges must not hang a /close. Without lib-bounded.sh
# (an incomplete install) run unbounded rather than refusing: losing the time
# bound is a smaller failure than losing the report.
bounded() {
  if declare -f run_bounded >/dev/null 2>&1; then run_bounded 20 "$@"; else "$@"; fi
}

die_usage() { echo "$*" >&2; exit "$EXIT_USAGE"; }


# --------------------------------------------------------------- locating insights
#
# Mirror of pr-flow's lib-work-system.sh pointed the other way. The layers are
# ordered by ACCURACY, not convenience, and both this file's directory and
# $CLAUDE_PLUGIN_ROOT are used as anchors: a script invoked outside a skill's
# `${CLAUDE_PLUGIN_ROOT}` expansion (a hook, an absolute-path call) would
# otherwise resolve nothing and report a working install as "no insights".
#
# KEEP IN SYNC with plugins/pr-flow/scripts/lib-work-system.sh. The two cannot
# share a file — that would make one plugin depend on the other and break the
# independent-installability rule this locator exists to serve — so
# test_locator_sync.py pins the version comparator identical in both. Fix a
# version-ordering bug in BOTH or the test fails.
insights_find() {
  local rel="$1" root t
  [ -n "$rel" ] || return 0

  # 1. Dev layout (this repo): plugins/work-system and plugins/insights are siblings.
  for root in "${SCRIPT_DIR:+$SCRIPT_DIR/..}" "${CLAUDE_PLUGIN_ROOT:-}"; do
    [ -n "$root" ] || continue
    t="$root/../insights/$rel"
    [ -f "$t" ] && { printf '%s\n' "$t"; return 0; }
  done

  # 2. Marketplace layout via Claude Code's installed-plugins manifest, which
  #    lists only INSTALLED versions. The version cache is never pruned, so a
  #    newest-cached glob would keep executing a version the user rolled back
  #    from. Entries are in insertion order across scopes, so pick the HIGHEST
  #    version rather than the first record.
  if command -v python3 >/dev/null 2>&1; then
    t="$(INS_REL="$rel" python3 - <<'PY' 2>/dev/null
import json, os
p = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
try:
    plugins = json.load(open(p))["plugins"]
except Exception:
    raise SystemExit
def vkey(v):
    # SemVer precedence: a prerelease ranks BELOW its own release, and build
    # metadata does not participate. Splitting the whole string on [.-+] would
    # sort 1.0.0-rc.1 above 1.0.0.
    v = str(v).split("+", 1)[0]
    core, _, pre = v.partition("-")
    nums = [int(x) if x.isdigit() else -1 for x in core.split(".")]
    if not pre:
        return (nums, 1, [])
    return (nums, 0, [(0, int(x)) if x.isdigit() else (1, x) for x in pre.split(".")])
best = None
for key, entries in plugins.items():
    if key.split("@", 1)[0] != "insights":
        continue
    for e in entries or []:
        ip = e.get("installPath") or ""
        if ip and (best is None or vkey(e.get("version", "")) > best[0]):
            best = (vkey(e.get("version", "")), ip)
if best:
    print(os.path.join(best[1], os.environ["INS_REL"]))
PY
)"
    [ -n "$t" ] && [ -f "$t" ] && { printf '%s\n' "$t"; return 0; }
  fi

  # 3. Fallback (manifest missing/unparsable): newest cached insights version.
  #    Paths differ only in the version segment, so a line-wise `sort -V` orders
  #    them. Heuristic — after a rollback this can pick a newer-than-enabled
  #    version; layer 2 is the accurate one.
  for root in "${CLAUDE_PLUGIN_ROOT:-}" "${SCRIPT_DIR:+$SCRIPT_DIR/..}"; do
    [ -n "$root" ] || continue
    t="$(printf '%s\n' "$root"/../../insights/*/"$rel" 2>/dev/null | sort -V | tail -1)"
    [ -n "$t" ] && [ -f "$t" ] && { printf '%s\n' "$t"; return 0; }
  done
  return 0
}

HELPER=""; HELPER_STATUS=""; HELPER_REASON=""

# The contract ships beside the helper in every layout, so derive it from the
# resolved path rather than rebuilding the plugin root a second way. One place:
# both `probe` and `prepare` emit it, and two copies of a path expression is how
# they end up disagreeing.
emit_contract() {
  [ -n "$HELPER" ] || return 0
  local contract="${HELPER%/scripts/insights.py}/docs/REPORT-CONTRACT.md"
  [ -f "$contract" ] && printf 'contract=%s\n' "$contract"
  return 0
}

# Locate insights. This does NOT run it: `probe` adds a liveness call, but every
# other subcommand makes a real helper call within milliseconds anyway and maps a
# non-zero exit to `unusable` itself — a second python3 spawn per subcommand only
# doubled the cost to learn the same thing.
resolve_helper() {
  [ -n "$HELPER_STATUS" ] && return 0
  HELPER="$(insights_find scripts/insights.py)"
  if [ -z "$HELPER" ]; then
    HELPER_STATUS="absent"
    HELPER_REASON="the insights plugin is not installed (optional)"
  elif ! command -v python3 >/dev/null 2>&1; then
    HELPER_STATUS="unusable"
    HELPER_REASON="found $HELPER but python3 is not on PATH"
  else
    HELPER_STATUS="ok"
    HELPER_REASON=""
  fi
  return 0
}

# Exit early for the two non-ok states, with the code that tells them apart. Both
# go to STDERR: stdout belongs to the subcommand's payload, and a caller
# redirecting it must never capture a status line instead.
require_helper() {
  resolve_helper
  case "$HELPER_STATUS" in
    ok) return 0 ;;
    absent) printf 'status=absent\nreason=%s\n'   "$HELPER_REASON" >&2; exit "$EXIT_ABSENT" ;;
    *)      printf 'status=unusable\nreason=%s\n' "$HELPER_REASON" >&2; exit "$EXIT_UNUSABLE" ;;
  esac
}

# Run the insights helper, keeping its stdout PURE: stderr goes to a temp file
# rather than being folded into the captured output, so a python warning can
# never end up inside the JSON a caller parses. Sets HELPER_OUT/HELPER_ERR.
HELPER_OUT=""; HELPER_ERR=""
call_helper() {
  local errf rc=0
  mktemp_tracked || { HELPER_ERR="could not create a temp file"; return 1; }
  errf="$MKTEMP_OUT"
  HELPER_OUT="$(bounded python3 "$HELPER" "$@" 2>"$errf")" || rc=$?
  HELPER_ERR="$(tr '\n' ' ' < "$errf")"
  rm -f "$errf"; untrack "$errf"
  return "$rc"
}

unusable() {
  printf 'status=unusable\nreason=%s\n' "${HELPER_ERR:-$1}" >&2
  exit "$EXIT_UNUSABLE"
}

TRIGGERS="handoff close manual"
# The report schema's task_status enum. `--pr` and `--trigger` are guarded and
# this reaches the same report, so leaving it unchecked only moved the rejection
# to `write`, where it reads as a bad draft rather than a bad call.
STATUSES="in_progress blocked completed aborted unknown"
in_set() {
  local needle="$1" t; shift
  for t in $@; do [ "$needle" = "$t" ] && return 0; done
  return 1
}
valid_trigger() { in_set "$1" $TRIGGERS; }

# ------------------------------------------------------------------------- probe
cmd_probe() {
  [ $# -eq 0 ] || die_usage "usage: ${0##*/} probe"
  resolve_helper
  if [ "$HELPER_STATUS" = ok ]; then
    # `store` is the cheapest call that proves the helper RUNS. It deliberately
    # does not pre-flight the store's permissions: those are enforced at write
    # time, and a probe duplicating the check would be a second, drifting copy of
    # insights' storage rules. `status=ok` means "insights is usable", never
    # "this write will succeed" — only the write's own exit code says that.
    if ! call_helper store >/dev/null; then
      HELPER_STATUS="unusable"
      HELPER_REASON="${HELPER_ERR:-insights.py store failed}"
    fi
  fi
  printf 'status=%s\n' "$HELPER_STATUS"
  printf 'available=%s\n' "$([ "$HELPER_STATUS" = ok ] && echo yes || echo no)"
  printf 'helper=%s\n' "$HELPER"
  emit_contract
  [ -n "$HELPER_REASON" ] && printf 'reason=%s\n' "$HELPER_REASON"
  return "$EXIT_OK"
}

# ---------------------------------------------------------------------- reported
list_reports() {   # <task> [<trigger>] [<project-dir>] -> HELPER_OUT holds the JSON
  local task="$1" trigger="${2:-}" project_dir="${3:-}"
  # --here pins the lookup to THIS checkout: task names are unique per project at
  # best, and a same-named task in another repo must not make /close believe this
  # one was already reported.
  local args=(list --here --task "$task" --json)
  [ -n "$project_dir" ] && args+=(--project-dir "$project_dir")
  [ -n "$trigger" ] && args+=(--trigger "$trigger")
  call_helper "${args[@]}"
}

cmd_reported() {
  local task="" trigger="" project_dir=""
  [ $# -ge 1 ] && { task="$1"; shift; }
  [ -n "$task" ] || die_usage "usage: ${0##*/} reported <task-name> [--trigger T] [--project-dir DIR]"
  while [ $# -gt 0 ]; do
    case "$1" in
      --trigger)     [ $# -ge 2 ] || die_usage "--trigger needs a value";     trigger="$2";     shift 2 ;;
      --project-dir) [ $# -ge 2 ] || die_usage "--project-dir needs a value"; project_dir="$2"; shift 2 ;;
      *) die_usage "unknown option: $1" ;;
    esac
  done
  # Validate here rather than letting argparse reject it downstream: an unknown
  # trigger came out as "insights is installed but unusable", which is a lie about
  # the plugin and sends the caller looking in the wrong place.
  [ -z "$trigger" ] || valid_trigger "$trigger" || die_usage "--trigger must be one of: $TRIGGERS"
  require_helper

  list_reports "$task" "$trigger" "$project_dir" || unusable "insights.py list failed"
  emit_reports
}

# Parse the helper's list JSON into key=value lines.
emit_reports() {
  printf '%s' "$HELPER_OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("status=ok")
print("reports=%d" % len(d["reports"]))
# Malformed files are reported, never silently skipped — but the count is
# STORE-GLOBAL: insights counts unreadable files before any project/task filter
# can be applied to them, because a file that will not parse has no task to
# filter on. Named accordingly so a caller cannot read it as "this task has
# malformed reports".
print("malformed_store=%d" % len(d["malformed"]))
for r in d["reports"]:
    print("report=%s trigger=%s task_status=%s recorded_at=%s"
          % (r["report_id"], r["report_trigger"], r["task_status"], r["recorded_at"]))
' || { echo "could not parse insights list output" >&2; exit "$EXIT_UNUSABLE"; }
}

# ----------------------------------------------------------------------- prepare
cmd_prepare() {
  local trigger="" caller="" lane="" project_dir="" pr="" status="" resolve_from=""
  [ $# -ge 1 ] && { trigger="$1"; shift; }
  valid_trigger "$trigger" \
    || die_usage "usage: ${0##*/} prepare <$(echo $TRIGGERS | tr ' ' '|')> --caller <skill> --lane <dir> [...]"
  while [ $# -gt 0 ]; do
    case "$1" in
      --caller)      [ $# -ge 2 ] || die_usage "--caller needs a value";      caller="$2";      shift 2 ;;
      --lane)        [ $# -ge 2 ] || die_usage "--lane needs a value";        lane="$2";        shift 2 ;;
      --project-dir) [ $# -ge 2 ] || die_usage "--project-dir needs a value"; project_dir="$2"; shift 2 ;;
      --pr)          [ $# -ge 2 ] || die_usage "--pr needs a value";          pr="$2";          shift 2 ;;
      --status)      [ $# -ge 2 ] || die_usage "--status needs a value";      status="$2";      shift 2 ;;
      --resolve-from) [ $# -ge 2 ] || die_usage "--resolve-from needs a value"; resolve_from="$2"; shift 2 ;;
      *) die_usage "unknown option: $1" ;;
    esac
  done
  [ -n "$caller" ] || die_usage "--caller is required (the skill producing this report)"
  [ -n "$lane" ] || die_usage "--lane is required (the worktree or repo the task lives in)"
  [ -z "$status" ] || in_set "$status" $STATUSES || die_usage "--status must be one of: $STATUSES"
  [ -d "$lane" ] || die_usage "--lane is not a directory: $lane"
  # A bare number only. The value reaches a JSON field and a report; anything
  # else is a caller bug, and accepting it would put unvalidated text in a fact.
  case "$pr" in ''|*[!0-9]*) [ -z "$pr" ] || die_usage "--pr expects a bare number, got: $pr" ;; esac

  require_helper
  # `status=ok` means the helper is usable, and it is printed HERE — after
  # require_helper, before any payload. It used to lead the output, which read as
  # "everything below succeeded" while the lane resolution and the store lookup
  # could still fail underneath it.
  printf 'status=ok\n'
  # The SKILLs read field semantics from the report contract and must not derive
  # that path from work-system's own root (correct only in a dev checkout).
  # `prepare` is the only command they run, so it carries the path.
  emit_contract

  # Derive the lane's identity HERE rather than taking it as an argument: a task
  # name or refname may contain shell metacharacters, and a value the model pastes
  # into a command line is executed before this script ever sees it. The subshell
  # `cd` is scoped (cwd-safety rule) and task-status.sh reads the current branch.
  local task="" branch="" main_branch="" resolved resolve_rc=0
  if [ -n "$resolve_from" ]; then
    [ -f "$resolve_from" ] || die_usage "--resolve-from is not a file: $resolve_from"
    [ -L "$resolve_from" ] && die_usage "--resolve-from must not be a symlink: $resolve_from"
    # Bounded read: this is a key=value dump, not a document.
    resolved="$(head -c 65536 "$resolve_from")" || resolve_rc=$?
  else
    resolved="$( ( cd "$lane" 2>/dev/null && bash "$SCRIPT_DIR/task-status.sh" resolve ) 2>/dev/null )" || resolve_rc=$?
  fi
  task="$(printf '%s\n' "$resolved" | sed -n 's/^task_name=//p' | head -1)"
  branch="$(printf '%s\n' "$resolved" | sed -n 's/^task_branch=//p' | head -1)"
  # task-status.sh already resolved the repo's default branch. Re-deriving it
  # here (upstream → origin/HEAD → main → master) was a second, more fragile
  # copy of a question this output had already answered.
  main_branch="$(printf '%s\n' "$resolved" | sed -n 's/^main_branch=//p' | head -1)"
  printf 'task=%s\n' "$task"
  printf 'branch=%s\n' "$branch"
  # "The helper failed" and "this lane genuinely has no task" both produced an
  # empty name, and the idempotency lookup below silently skipped — so a
  # transient git error looked like a task that had never been reported.
  if [ "$resolve_rc" -ne 0 ] || [ -z "$task" ]; then
    printf 'task_resolution=%s\n' \
      "$([ "$resolve_rc" -ne 0 ] && echo failed || echo none)"
    # Without a name there is no idempotency key AND no way to find the stored
    # report later, so a `close` here would duplicate on every retry and file
    # each copy anonymously. Refuse rather than produce that quietly; the caller
    # can supply the name it already has via --resolve-from.
    if [ "$trigger" = close ]; then
      printf 'action=blocked\nreason=no task name for this lane, so a close report could neither be deduplicated nor found again — pass --resolve-from with task-status.sh output\n'
      return "$EXIT_OK"
    fi
  fi

  # The lane's first commit off the default branch: a stored close report OLDER
  # than that belongs to an earlier task that reused the name (the archive's
  # -2/-3 suffixes exist because names DO get reused). Without it the skip was
  # permanent and a new task could never get its own close report.
  #
  # Only `close` consumes this, and it costs a git log per call — so don't run it
  # for a handoff or a manual report that will never look at the answer.
  #
  # Formatted as real UTC `…Z`, matching a report's `recorded_at` EXACTLY.
  # `--date=format:` renders in the COMMIT's timezone, so appending a literal `Z`
  # produced a stamp that looked UTC and was hours off; `format-local:` with
  # TZ=UTC is what converts. (`%cI` has the same problem with its offset.)
  # `--max-count` applies BEFORE `--reverse`, so the oldest commit is `tail -1`.
  local lane_since=""
  if [ "$trigger" = close ] && [ -n "$main_branch" ]; then
    if ( cd "$lane" 2>/dev/null && git rev-parse --verify --quiet "$main_branch" >/dev/null ); then
      lane_since="$( ( cd "$lane" 2>/dev/null \
        && TZ=UTC git log --format=%cd --date=format-local:%Y-%m-%dT%H:%M:%SZ "$main_branch..HEAD" 2>/dev/null | tail -1 ) )" || lane_since=""
    fi
  fi
  # No resolvable lane start means we cannot rule out that an older report
  # belongs to a namesake — so DON'T filter. Idempotency is the safer default: a
  # missed duplicate is a stray record, a missed skip re-reports every retry.
  #
  # But SAY SO. This is not a rare corner: on the documented retry path the
  # worktree is already gone, the lane IS the main checkout sitting on the
  # default branch, and `main..HEAD` is empty — so the filter is unavailable
  # precisely where a reused task name would be mistaken for this one. The caller
  # must fall back to judging `report_recorded_at`, which it can only do if it
  # knows the mechanism did not.
  if [ "$trigger" = close ]; then
    printf 'namesake_filter=%s\n' "$([ -n "$lane_since" ] && echo applied || echo unavailable)"
  fi

  local related="" existing_close="" existing_close_at=""
  if [ -n "$task" ]; then
    list_reports "$task" "" "$project_dir" || unusable "insights.py list failed"
    # ONE parse of the list JSON, emitting the display lines AND the decision.
    # Two separate python passes over the same rows read them with different key
    # assumptions and could disagree about what "the close report" was.
    local parsed
    parsed="$(printf '%s' "$HELPER_OUT" | LANE_SINCE="$lane_since" python3 -c '
import json, os, sys

# Schema cap on work.related_reports; linking more makes the draft invalid, and
# a report rejected for an over-long link list is a lost retrospective.
# Mirrors the list cap in insights.py. Duplicated as a literal on purpose: the
# bridge must not import the helper, and exceeding the cap makes the draft
# invalid — a report lost to an over-long link list is worse than a link dropped
# here. (No apostrophes: this python is inside a single-quoted shell string.)
MAX_RELATED = 50
since = os.environ.get("LANE_SINCE") or ""
d = json.load(sys.stdin)
rows = d["reports"]
print("reports=%d" % len(rows))
# STORE-GLOBAL: insights counts unreadable files before any project/task filter
# can apply to them, because a file that will not parse has no task to filter on.
print("malformed_store=%d" % len(d["malformed"]))

def belongs(r):
    # Same lexicographic-is-chronological compare as above, valid only because
    # both sides are UTC "...Z" strings of the same shape.
    return not (since and r["recorded_at"] < since)

best = None
related = []
for r in rows:
    print("report=%s trigger=%s task_status=%s recorded_at=%s%s"
          % (r["report_id"], r["report_trigger"], r["task_status"], r["recorded_at"],
             "" if belongs(r) else " namesake=yes"))
    # A report from an older task that merely reused the name is NOT this task
    # history, so it is reported but not linked: related_reports is a claim of
    # relation, and linking a stranger makes the claim false.
    if belongs(r):
        related.append(r["report_id"])
    if r["report_trigger"] == "close" and belongs(r):
        if best is None or r["recorded_at"] > best["recorded_at"]:
            best = r
if len(related) > MAX_RELATED:
    print("related_dropped=%d" % (len(related) - MAX_RELATED))
    related = related[-MAX_RELATED:]
print("related=%s" % " ".join(related))
if best:
    print("close=%s %s" % (best["report_id"], best["recorded_at"]))
' 2>/dev/null)" || unusable "could not parse insights list output"
    printf '%s\n' "$parsed" | grep -v '^related=\|^close=' || true
    related="$(printf '%s\n' "$parsed" | sed -n 's/^related=//p' | head -1)"
    local close_row
    close_row="$(printf '%s\n' "$parsed" | sed -n 's/^close=//p' | head -1)"
    existing_close="${close_row%% *}"
    existing_close_at="${close_row#* }"
  else
    printf 'reports=0\nmalformed_store=unknown\n'
  fi

  # The ONLY automatic skip: a close whose close report is already stored. This
  # makes a retried teardown idempotent using state that already exists.
  #
  # Two limits the caller must know, because this check cannot close them:
  #  * It is check-then-write, not atomic, and the store enforces no
  #    (task, trigger) uniqueness — two concurrent closes could both write.
  #  * Reports are matched by task NAME, and names get reused over time (the
  #    archive's -2/-3 suffixes exist for exactly that). `recorded_at` above is
  #    what tells an old namesake from this task's own report.
  # Neither is worth a lock or a second identity: a duplicate report is a
  # harmless extra record, and inventing a run id was ruled out by design.
  if [ "$trigger" = close ] && [ -n "$existing_close" ]; then
    printf 'action=skip\nreason=a close report for this task is already stored\nreport=%s\nreport_recorded_at=%s\n' \
      "$existing_close" "$existing_close_at"
    return "$EXIT_OK"
  fi

  local args=(skeleton --trigger "$trigger")
  [ -n "$project_dir" ] && args+=(--project-dir "$project_dir")
  call_helper "${args[@]}" || unusable "insights.py skeleton failed"

  # The draft path is always ours. An earlier `--out` let the caller name it,
  # with none of the name/location/symlink guards this script applies elsewhere —
  # a redirection that truncates whatever it is pointed at. Nothing needed it.
  local draft
  mktemp_tracked || { echo "could not create a draft file" >&2; exit "$EXIT_UNUSABLE"; }
  draft="$MKTEMP_OUT"

  # Overlay the lifecycle facts work-system genuinely observed, each with the
  # source it came from. insights.py's own skeleton derives task hints from the
  # CWD, which is wrong for /close (usually the main repo, branch `main`) — the
  # worktree it is about to delete is the thing being reported on. Fields left
  # out stay as the skeleton had them (an unknown with an empty reason), so they
  # still fail validation by name until the reporting model fills them.
  printf '%s' "$HELPER_OUT" | INS_CALLER="$caller" INS_TASK="$task" INS_BRANCH="$branch" \
    INS_PR="$pr" INS_STATUS="$status" INS_RELATED="$related" python3 -c '
import json, os, sys

d = json.load(sys.stdin)
caller = os.environ["INS_CALLER"]
# Provenance, not decoration: these values came from work-system running its own
# task helper, so that is what the source says — down to WHICH command produced
# the PR number, because "the source names a command that never ran" is exactly
# the dishonesty this report format exists to prevent.
via = "task-status.sh resolve via work-system:%s" % caller
# prepare runs `task-status.sh resolve`, which does NOT look up a PR: the number
# arrives as the caller --pr argument.
# (No apostrophes here — this python is embedded in a single-quoted shell string.)
pr_via = "--pr argument supplied by work-system:%s" % caller

def fact(field, value, source):
    if value:
        d["work"][field] = {"value": value, "source": source}

fact("task_name", os.environ["INS_TASK"], via)
fact("branch",    os.environ["INS_BRANCH"], via)
fact("pr",        os.environ["INS_PR"], pr_via)

status = os.environ["INS_STATUS"]
if status:
    d["task_status"] = status

# Linking is a claim of relation, not proof of a duplicate: an earlier manual or
# handoff report about the same task is referenced, never merged or replaced.
related = [r for r in os.environ["INS_RELATED"].split() if r]
if related:
    seen = list(d["work"].get("related_reports") or [])
    d["work"]["related_reports"] = seen + [r for r in related if r not in seen]

json.dump(d, sys.stdout, indent=2, ensure_ascii=False)
sys.stdout.write("\n")
' > "$draft" || { echo "could not overlay lifecycle facts onto the insights skeleton" >&2; exit "$EXIT_UNUSABLE"; }
  chmod 600 "$draft" 2>/dev/null || true
  # Hand ownership over only now that the draft is actually built. Untracking it
  # up front meant a failed overlay left an unredacted file behind, because the
  # cleanup trap had already been told to ignore it.
  untrack "$draft"

  printf 'action=draft\ndraft=%s\n' "$draft"
  [ -n "$related" ] && printf 'related=%s\n' "$related"
  return "$EXIT_OK"
}

# ------------------------------------------------------------------------- write
#
# The draft holds report text (user input), so it travels as a FILE — never a
# heredoc or a command line, where a line equal to a terminator would run as
# shell commands.
cmd_write() {
  local draft="" project_dir=""
  [ $# -ge 1 ] && { draft="$1"; shift; }
  [ -n "$draft" ] || die_usage "usage: ${0##*/} write <draft-file> [--project-dir DIR]"
  while [ $# -gt 0 ]; do
    case "$1" in
      --project-dir) [ $# -ge 2 ] || die_usage "--project-dir needs a value"; project_dir="$2"; shift 2 ;;
      *) die_usage "unknown option: $1" ;;
    esac
  done
  # `-f` follows symlinks, so refuse one explicitly — the same class of
  # caller-supplied path archive-task.sh refuses for --note-file, and the two
  # must not disagree about it.
  [ -e "$draft" ] || die_usage "no draft file at $draft"
  [ -L "$draft" ] && die_usage "draft must not be a symlink: $draft"
  [ -f "$draft" ] || die_usage "draft must be a regular file: $draft"
  require_helper

  local args=(write "$draft")
  [ -n "$project_dir" ] && args+=(--project-dir "$project_dir")
  local rc=0
  bounded python3 "$HELPER" "${args[@]}" || rc=$?
  # Map the helper's namespace into this one. Relaying it raw made its 2 (usage)
  # and 3 (ID collision) indistinguishable from this script's 2 (bad argv) and
  # 3 (insights absent) — so an unsaved report could read as "nothing to do".
  case "$rc" in
    0) return "$EXIT_OK" ;;
    1) return "$EXIT_INVALID" ;;                 # invalid draft, nothing saved
    2) echo "insights.py refused the draft (see its message above)" >&2
       return "$EXIT_INVALID" ;;                 # e.g. oversize input — still "fix the draft"
    3) return "$EXIT_COLLISION" ;;               # different content under this id
    124) echo "insights.py write timed out — nothing confirmed stored" >&2
       return "$EXIT_UNUSABLE" ;;
    *) return "$EXIT_UNUSABLE" ;;                # 4 = storage failure, and anything unforeseen
  esac
}

# --------------------------------------------------------------------- note-file
cmd_note_file() {
  [ $# -eq 0 ] || die_usage "usage: ${0##*/} note-file"
  # `note-` prefix and ${TMPDIR:-/tmp} location are exactly what archive-task.sh
  # enforces. Untracked on purpose: the caller writes into it and hands it to the
  # archive step, so it must outlive this process.
  local f
  f="$(mktemp "${TMPDIR:-/tmp}/note-insights.XXXXXX")" || {
    echo "could not create a note file under ${TMPDIR:-/tmp}" >&2
    return "$EXIT_UNUSABLE"
  }
  chmod 600 "$f" 2>/dev/null || true
  printf 'note=%s\n' "$f"
  return "$EXIT_OK"
}

# ------------------------------------------------------------------------ redact
cmd_redact() {
  local file="" in_place=no
  [ $# -ge 1 ] && { file="$1"; shift; }
  while [ $# -gt 0 ]; do
    case "$1" in
      --in-place) in_place=yes; shift ;;
      *) die_usage "unknown option: $1" ;;
    esac
  done
  [ -n "$file" ] || die_usage "usage: ${0##*/} redact <file> [--in-place]"
  [ -e "$file" ] || die_usage "no file at $file"
  [ -L "$file" ] && die_usage "file must not be a symlink: $file"
  [ -f "$file" ] || die_usage "file must be a regular file: $file"
  require_helper
  local rc=0
  if [ "$in_place" = yes ]; then
    # Redact into a temp file and rename over the original only on success: a
    # failed pass must leave the ORIGINAL note intact, never a truncated one.
    local out pre post
    mktemp_tracked || { echo "could not create a temp file" >&2; return "$EXIT_UNUSABLE"; }
    out="$MKTEMP_OUT"
    # Capture the helper's status DIRECTLY. Taking `rc=$?` after a closed `if`
    # read the status of the `if` statement itself — always 0 — so the
    # invalid-vs-unusable mapping below was dead code and every failure came back
    # as "insights installed but unusable".
    pre="$(stat_field inode "$file")"
    rc=0
    bounded python3 "$HELPER" redact "$file" > "$out" || rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "redaction failed — $file is unchanged" >&2
      case "$rc" in 1|2) return "$EXIT_INVALID" ;; *) return "$EXIT_UNUSABLE" ;; esac
    fi
    # The rename targets a path, and the note we just read may no longer be the
    # file sitting there. Same check-to-use gap as archive-task.sh's note open.
    post="$(stat_field inode "$file")"
    if [ -z "$pre" ] || [ "$pre" != "$post" ]; then
      echo "$file changed while it was being redacted — refusing to overwrite it" >&2
      return "$EXIT_UNUSABLE"
    fi
    # Replace by ATOMIC RENAME from the note's OWN directory. The two obvious
    # alternatives are both wrong here:
    #   * `cat "$out" > "$file"` truncates the target before writing, so a write
    #     that fails part-way (full disk, quota) destroys the note while the error
    #     below still claims it is unchanged — and `>` re-resolves the path, which
    #     reopens the check-to-use gap the inode comparison just closed and would
    #     follow a symlink planted in that window.
    #   * `mv` from ${TMPDIR:-/tmp} can cross a filesystem boundary, which turns
    #     into copy+unlink: a new inode, and the target's owner/mode replaced.
    # A sibling temp file plus `mv` is same-filesystem (so a real rename),
    # atomic (no truncated intermediate state), and rename does NOT follow a
    # symlink at the destination — it replaces the name itself.
    local dest_dir sib
    dest_dir="$(dirname "$file")"
    sib="$(mktemp "$dest_dir/.note-redacted.XXXXXX")" || {
      echo "could not stage the redacted note next to $file — it is unchanged" >&2
      return "$EXIT_UNUSABLE"
    }
    TMPFILES+=("$sib")
    chmod 600 "$sib" 2>/dev/null || true
    if cat "$out" > "$sib" && mv "$sib" "$file"; then
      untrack "$sib"
      rm -f "$out"; untrack "$out"
      return "$EXIT_OK"
    fi
    rm -f "$sib"; untrack "$sib"
    echo "redacted copy could not replace $file — the note is unchanged" >&2
    return "$EXIT_UNUSABLE"
  fi
  bounded python3 "$HELPER" redact "$file" || rc=$?
  case "$rc" in
    0) return "$EXIT_OK" ;;
    1) return "$EXIT_INVALID" ;;
    # Same mapping as cmd_write: the helper's usage exit (unreadable or oversize
    # input) is a problem with what we handed it, not a broken plugin.
    2) return "$EXIT_INVALID" ;;
    *) return "$EXIT_UNUSABLE" ;;
  esac
}

case "${1:-}" in
  probe)     shift; cmd_probe "$@" ;;
  note-file) shift; cmd_note_file "$@" ;;
  prepare)  shift; cmd_prepare "$@" ;;
  reported) shift; cmd_reported "$@" ;;
  write)    shift; cmd_write "$@" ;;
  redact)   shift; cmd_redact "$@" ;;
  ""|-h|--help|help)
    # Bounded by the header's own end, like the sibling scripts — not a line
    # range that silently truncates as the header grows.
    sed -n '2,/^$/p' "${BASH_SOURCE[0]:-$0}" | sed 's/^# \{0,1\}//'
    exit "$EXIT_USAGE" ;;
  *) die_usage "unknown subcommand: $1" ;;
esac
