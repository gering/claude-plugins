#!/usr/bin/env bash
# insights-handoff.sh — work-system's ONE bridge to the optional `insights` plugin.
#
# The lifecycle producers (/close's pre-teardown report, a worker's terminal-gate
# handoff report) must not each carry their own copy of "where is insights, is it
# usable, has this task already been reported". That prose drifts; this script is
# the single source of truth for it. It NEVER writes a report file itself and
# never duplicates insights' validation — every write goes through insights.py,
# which owns the `insights.report/v1` contract (see that plugin's
# docs/REPORT-CONTRACT.md).
#
# insights is DETECTED, never required (skill-composition rule: plugins stay
# independently installable). "Not installed" is a normal outcome that leaves
# every existing work-system flow unchanged — which is why it gets its own exit
# code, distinct from "installed but unusable". Collapsing the two would let a
# broken python3 or a corrupt store look like an uninstalled plugin and silently
# drop reports the user expected.
#
# CWD-safe: every path is explicit and the script never `cd`s (cwd-safety rule).
#
# Subcommands
#   probe
#       Report whether insights can be used from here, and where its helper and
#       report contract live. Always exits 0 — the answer is in `status=`, because
#       "absent" is not an error for a caller that only wants to know. Callers read
#       `contract=` instead of spelling a path: `${CLAUDE_PLUGIN_ROOT}/../insights/…`
#       is only correct in the dev layout, and in the marketplace cache
#       (…/<plugin>/<version>/) it silently points at nothing.
#   reported <task-name> [--trigger T] [--project-dir DIR]
#       Which reports already exist for this task IN THIS PROJECT. This is the
#       idempotency key for a repeated /close or handoff: producer-owned state
#       that already exists (the store), not a new state file.
#   skeleton <trigger> --caller <skill> [--task N] [--branch B] [--pr N]
#            [--task-path P] [--status S] [--related ID]... [--project-dir DIR]
#       A contract-complete draft with the lifecycle facts work-system actually
#       observed already filled in (with their real sources). Everything the
#       reporting model must decide stays empty and still fails validation by
#       name. Prints JSON on stdout.
#   write <draft-file> [--project-dir DIR]
#       Store a finished draft. Relays insights.py's exit code verbatim
#       (0 stored/unchanged · 1 invalid · 3 ID collision · 4 storage failure),
#       so a caller can never mistake a failure for a saved report.
#
# Exit codes
#   0  answered / stored
#   1  the insights helper rejected the draft (write only; nothing saved)
#   2  usage error in THIS script's arguments
#   3  insights is not installed — nothing to do, not a failure
#   4  insights is installed but unusable, or storage failed — must be surfaced
#
# Nothing here decides anything about the task: a report is never evidence that a
# task is merged, done, or safe to tear down, and a failed report never becomes a
# cleanup gate. The callers own those decisions.

set -uo pipefail

EXIT_OK=0; EXIT_INVALID=1; EXIT_USAGE=2; EXIT_ABSENT=3; EXIT_UNUSABLE=4

# Where this script lives. `${0%/*}` is NOT enough: invoked through $PATH or as a
# bare name it yields the filename, and every sibling path built from it would be
# relative to the CALLER's cwd (same trap documented in agent-registry.sh).
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || SELF_DIR=""
[ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/lib-bounded.sh" ] && . "$SELF_DIR/lib-bounded.sh"

# A local helper call that wedges must not hang a /close. Without lib-bounded.sh
# (an incomplete install) run unbounded rather than refusing: losing the time
# bound is a smaller failure than losing the report.
bounded() {
  if declare -f run_bounded >/dev/null 2>&1; then run_bounded 20 "$@"; else "$@"; fi
}

die_usage() { echo "$*" >&2; exit "$EXIT_USAGE"; }

# --------------------------------------------------------------- locating insights
#
# Mirror of pr-flow's lib-work-system.sh, pointed the other way. The layers are
# ordered by ACCURACY, not convenience, and both this file's directory and
# $CLAUDE_PLUGIN_ROOT are used as anchors: a script invoked outside a skill's
# `${CLAUDE_PLUGIN_ROOT}` expansion (a hook, an absolute-path call) would
# otherwise resolve nothing and report a working install as "no insights".
insights_find() {
  local rel="$1" root t
  [ -n "$rel" ] || return 0

  # 1. Dev layout (this repo): plugins/work-system and plugins/insights are siblings.
  for root in "${SELF_DIR:+$SELF_DIR/..}" "${CLAUDE_PLUGIN_ROOT:-}"; do
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
  for root in "${CLAUDE_PLUGIN_ROOT:-}" "${SELF_DIR:+$SELF_DIR/..}"; do
    [ -n "$root" ] || continue
    t="$(printf '%s\n' "$root"/../../insights/*/"$rel" 2>/dev/null | sort -V | tail -1)"
    [ -n "$t" ] && [ -f "$t" ] && { printf '%s\n' "$t"; return 0; }
  done
  return 0
}

HELPER=""; HELPER_STATUS=""; HELPER_REASON=""

# Resolve insights AND confirm it actually runs. A located file is not a usable
# plugin: no python3, an import error, or a refused store all mean "installed but
# unusable" — a state the caller must warn about, not treat like an absent plugin.
resolve_helper() {
  [ -n "$HELPER_STATUS" ] && return 0
  HELPER="$(insights_find scripts/insights.py)"
  if [ -z "$HELPER" ]; then
    HELPER_STATUS="absent"
    HELPER_REASON="the insights plugin is not installed (optional)"
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    HELPER_STATUS="unusable"
    HELPER_REASON="found $HELPER but python3 is not on PATH"
    return 0
  fi
  # `store` is the cheapest call that proves the helper RUNS and can resolve its
  # store location. It deliberately does not pre-flight the store's permissions:
  # those are enforced at write time (exit 4), and a probe that duplicated the
  # check would be a second, drifting copy of insights' own storage rules. So
  # `status=ok` means "insights is usable", never "this write will succeed" —
  # only the write's own exit code says that.
  local err rc=0
  err="$(bounded python3 "$HELPER" store 2>&1 >/dev/null)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    HELPER_STATUS="unusable"
    HELPER_REASON="$(printf '%s' "${err:-insights.py store failed with exit $rc}" | tr '\n' ' ')"
    return 0
  fi
  HELPER_STATUS="ok"
  HELPER_REASON=""
}

# Exit early for the two non-ok states, with the code that tells them apart.
require_helper() {
  resolve_helper
  case "$HELPER_STATUS" in
    ok) return 0 ;;
    absent)   printf 'status=absent\nreason=%s\n' "$HELPER_REASON"; exit "$EXIT_ABSENT" ;;
    *)        printf 'status=unusable\nreason=%s\n' "$HELPER_REASON" >&2; exit "$EXIT_UNUSABLE" ;;
  esac
}

# ------------------------------------------------------------------------- probe
cmd_probe() {
  [ $# -eq 0 ] || die_usage "usage: ${0##*/} probe"
  resolve_helper
  printf 'status=%s\n' "$HELPER_STATUS"
  printf 'available=%s\n' "$([ "$HELPER_STATUS" = ok ] && echo yes || echo no)"
  printf 'helper=%s\n' "$HELPER"
  # The contract sits next to the helper in every layout, so derive it from the
  # resolved path rather than rebuilding the plugin root a second way.
  if [ -n "$HELPER" ]; then
    local contract="${HELPER%/scripts/insights.py}/docs/REPORT-CONTRACT.md"
    [ -f "$contract" ] && printf 'contract=%s\n' "$contract"
  fi
  [ -n "$HELPER_REASON" ] && printf 'reason=%s\n' "$HELPER_REASON"
  return "$EXIT_OK"
}

# ---------------------------------------------------------------------- reported
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
  require_helper

  # --here pins the lookup to THIS checkout: task names are unique per project at
  # best, and a same-named task in another repo must not make /close believe this
  # one was already reported.
  local args=(list --here --task "$task" --json)
  [ -n "$project_dir" ] && args+=(--project-dir "$project_dir")
  [ -n "$trigger" ] && args+=(--trigger "$trigger")

  local out rc=0
  out="$(bounded python3 "$HELPER" "${args[@]}" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'status=unusable\nreason=%s\n' "$(printf '%s' "$out" | tr '\n' ' ')" >&2
    exit "$EXIT_UNUSABLE"
  fi
  # Malformed files are reported, never silently skipped: "0 reports" because the
  # only existing one is unreadable is a different fact from "never reported".
  printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("status=ok")
print("reports=%d" % len(d["reports"]))
print("malformed=%d" % len(d["malformed"]))
for r in d["reports"]:
    print("report=%s trigger=%s task_status=%s recorded_at=%s"
          % (r["report_id"], r["report_trigger"], r["task_status"], r["recorded_at"]))
' || { echo "could not parse insights list output" >&2; exit "$EXIT_UNUSABLE"; }
}

# ---------------------------------------------------------------------- skeleton
#
# insights.py's own skeleton derives its task hints from the CWD. That is wrong
# for exactly the callers here: /close usually runs from the main repo (branch
# `main`), and the worktree it is about to delete is the thing being reported on.
# So work-system overlays the lifecycle facts it genuinely observed — each with
# the source it came from, never a guess. Fields left out stay whatever the
# skeleton had (an unknown with an empty reason), so they still fail validation
# by name until the reporting model fills them.
cmd_skeleton() {
  local trigger="" caller="" task="" branch="" pr="" task_path="" status="" project_dir=""
  local related=()
  [ $# -ge 1 ] && { trigger="$1"; shift; }
  case "$trigger" in
    handoff|close|manual) ;;
    *) die_usage "usage: ${0##*/} skeleton <handoff|close|manual> --caller <skill> [...]" ;;
  esac
  while [ $# -gt 0 ]; do
    case "$1" in
      --caller)      [ $# -ge 2 ] || die_usage "--caller needs a value";      caller="$2";      shift 2 ;;
      --task)        [ $# -ge 2 ] || die_usage "--task needs a value";        task="$2";        shift 2 ;;
      --branch)      [ $# -ge 2 ] || die_usage "--branch needs a value";      branch="$2";      shift 2 ;;
      --pr)          [ $# -ge 2 ] || die_usage "--pr needs a value";          pr="$2";          shift 2 ;;
      --task-path)   [ $# -ge 2 ] || die_usage "--task-path needs a value";   task_path="$2";   shift 2 ;;
      --status)      [ $# -ge 2 ] || die_usage "--status needs a value";      status="$2";      shift 2 ;;
      --related)     [ $# -ge 2 ] || die_usage "--related needs a value";     related+=("$2");  shift 2 ;;
      --project-dir) [ $# -ge 2 ] || die_usage "--project-dir needs a value"; project_dir="$2"; shift 2 ;;
      *) die_usage "unknown option: $1" ;;
    esac
  done
  [ -n "$caller" ] || die_usage "--caller is required (the skill producing this report)"
  require_helper

  local args=(skeleton --trigger "$trigger")
  [ -n "$project_dir" ] && args+=(--project-dir "$project_dir")

  local skel rc=0
  skel="$(bounded python3 "$HELPER" "${args[@]}" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'status=unusable\nreason=%s\n' "$(printf '%s' "$skel" | tr '\n' ' ')" >&2
    exit "$EXIT_UNUSABLE"
  fi

  printf '%s' "$skel" | INS_CALLER="$caller" INS_TASK="$task" INS_BRANCH="$branch" \
    INS_PR="$pr" INS_TASK_PATH="$task_path" INS_STATUS="$status" \
    INS_RELATED="$(printf '%s\n' "${related[@]+"${related[@]}"}")" python3 -c '
import json, os, sys

d = json.load(sys.stdin)
caller = os.environ["INS_CALLER"]
# Provenance, not decoration: these values came from work-system reading git and
# its own task helper, so that is what the source says. A value work-system did
# NOT observe is left as the skeleton produced it.
via = "task-status.sh via work-system:%s" % caller

def fact(field, value, source):
    if not value:
        return
    d["work"][field] = {"value": value, "source": source}

fact("task_name", os.environ["INS_TASK"], via)
fact("branch",    os.environ["INS_BRANCH"], via)
fact("task_path", os.environ["INS_TASK_PATH"], "work-system:%s" % caller)
fact("pr",        os.environ["INS_PR"], "gh pr view via work-system:%s" % caller)

status = os.environ["INS_STATUS"]
if status:
    d["task_status"] = status

# Linking is a claim of relation, not proof of a duplicate: an earlier manual or
# handoff report about the same task is referenced, never merged or replaced.
related = [r for r in os.environ["INS_RELATED"].splitlines() if r]
if related:
    seen = list(d["work"].get("related_reports") or [])
    d["work"]["related_reports"] = seen + [r for r in related if r not in seen]

json.dump(d, sys.stdout, indent=2, ensure_ascii=False)
sys.stdout.write("\n")
' || { echo "could not overlay lifecycle facts onto the insights skeleton" >&2; exit "$EXIT_UNUSABLE"; }
}

# ------------------------------------------------------------------------- write
#
# A pure passthrough on purpose. The draft holds report text (user input), so it
# travels as a FILE — never a heredoc or a command line, where a line equal to a
# terminator would run as shell commands. The exit code is relayed unchanged so
# the caller distinguishes "invalid draft" from "storage failed" and can never
# describe an unsaved report as recorded.
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
  [ -f "$draft" ] || die_usage "no draft file at $draft"
  require_helper

  local args=(write "$draft")
  [ -n "$project_dir" ] && args+=(--project-dir "$project_dir")
  local rc=0
  bounded python3 "$HELPER" "${args[@]}" || rc=$?
  # 124 = the time bound killed it. Nothing is known to be stored, and that is a
  # storage failure from the caller's side, not an invalid draft.
  [ "$rc" -eq 124 ] && { echo "insights.py write timed out — nothing confirmed stored" >&2; rc=$EXIT_UNUSABLE; }
  return "$rc"
}

case "${1:-}" in
  probe)    shift; cmd_probe "$@" ;;
  reported) shift; cmd_reported "$@" ;;
  skeleton) shift; cmd_skeleton "$@" ;;
  write)    shift; cmd_write "$@" ;;
  ""|-h|--help|help)
    sed -n '2,50p' "${BASH_SOURCE[0]:-$0}" | sed 's/^# \{0,1\}//'
    exit "$EXIT_USAGE" ;;
  *) die_usage "unknown subcommand: $1" ;;
esac
