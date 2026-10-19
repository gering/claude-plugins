#!/usr/bin/env bash
# landing.sh — the "landing in progress" marker a lane's /land leaves for the
# Manager, and the one place that knows how to read it back.
#
# Why: a Manager-side close or merge cannot otherwise tell that the worker is
# mid-/land, and once the worktree is gone (a bare `gh pr merge --delete-branch`,
# a manual `git worktree remove`) nothing in the lane survives to say whose tab
# it was. So the marker lives OUTSIDE the worktree, in the repo's git common dir
# (`<common>/work-system/landing/<task>`), which a worktree removal never touches,
# and it names the worker by herdr pane + agent-session UUID — an identity that
# does not depend on the pane's cwd still existing.
#
# The marker is written by the worker and is DATA, never authority: it can make
# a Manager ask where it would not have asked, and it names a tab that is only
# offered once herdr confirms the same pane still hosts the same session.
#
# Subcommands
#   begin --pr <n>
#       Run by /land step 1 inside the lane. Writes the marker for this lane's
#       task (phase=landing). Prints recorded=yes|no, marker=, reason=.
#       Best-effort: always exit 0 (a missing marker never blocks a /land).
#   phase <task> handoff [--repo <main>]
#       /land is done with the lane (it is handing the close over). The marker
#       stays so a Manager-side close can still find the worker's tab, but no
#       longer reads as "landing in progress". Prints updated=yes|no.
#   end <task> [--repo <main>]
#       Remove the marker (/land stopped, or a close finished). Prints
#       removed=yes|no. Never fails on a missing marker.
#   status <task> [--repo <main>]
#       Prints landing=none|live|stale|unverified|invalid, and with a marker:
#       phase=, pr=, branch=, worktree=, worktree_state=present|gone,
#       started_at=, kicker_session=, kicker_source=kickoff|backfill,
#       kicker_record=absent|present (any .ws-kicker at begin, even one too
#       broken to copy; empty on a marker from before 1.24.0), and for a live
#       worker worker_pane=,
#       worker_tab=, worker_cwd=match|unknown|mismatch.
#         live       herdr lists the recorded pane with the recorded session
#         stale      herdr's populated agent list has no such pane+session
#         unverified not in herdr, no worker recorded, or herdr unreadable
#         invalid    the file exists but is not a well-formed marker (or a
#                    symlink): treat like unverified, never as none
#       worker_tab is herdr's live answer for that pane, never the file's.
#   lane <task> [--repo <main>]
#       worktree=<path> worktree_state=present|gone leftover=yes|no — THE one
#       "is this lane torn down?" predicate (close-request.sh and the sweep ask
#       it too). The path is the marker's worktree= when a valid marker exists
#       (an /adopt-ed lane's directory may not be named after its task), else
#       <repo>/.claude/worktrees/<task>. gone = git no longer lists it, or lists
#       it as prunable; a leftover directory does not make a lane present, it
#       is reported as leftover=yes.
#   gone-check <task> --pr <n> [--repo <main>]
#       For /land step 4 when the lane may have been torn down under it:
#       worktree_state=present|gone, then (gone only) pr_state=, pr_number=,
#       head_branch_match=yes|no|unknown, merge_sha=, branch=,
#       branch_tip=none|same|behind|ahead|diverged|unreadable (a local branch
#       that survived the worktree, against the PR head), and the decision:
#       verdict=handoff, or verdict=stop with stop=not-merged|branch-ahead|
#       branch-unreadable|not-by-name. The skill branches on verdict/stop only.
#
# --repo defaults to the main checkout of the cwd. A /land whose worktree is
# gone has no usable cwd, so it passes the main-repo path it kept from step 1.
#
# A marker's worktree= must be a direct child of <repo>/.claude/worktrees/, or
# the marker is invalid. Its tab is only ever used once THAT path is gone (see
# /close step 7), so a marker naming a live lane cannot aim the Manager at it.
#
# Exit codes: 0 answered, 2 usage, 1 no repository (not for `begin`).
# CWD safety: `begin` reads its lane from the cwd on purpose (it runs inside
# it); every other git call is `git -C`; gh runs in a subshell `cd`.
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# ha_list / _ha_bounded / $HA_CALL_TIMEOUT_SECS / $HERDR_MATCH_PRELUDE.
. "$SCRIPT_DIR/herdr-agent.sh"

TASK_RE='^[A-Za-z0-9_][A-Za-z0-9._-]*$'

usage() {
  echo "usage: ${0##*/} {begin --pr <n> | phase <task> handoff | end <task> | status <task> | lane <task> | gone-check <task> --pr <n>} [--repo <main>]" >&2
  exit 2
}

valid_task() { printf '%s' "$1" | grep -Eq "$TASK_RE"; }
valid_pr()   { case "$1" in ''|*[!0-9]*) return 1 ;; esac; }
now_utc()    { date -u +%Y-%m-%dT%H:%M:%SZ; }
realpath_py() { python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }

# Canonical main checkout: --repo (must BE a main checkout) or the cwd's.
REPO=""
resolve_repo() {
  local want="${1:-}" m
  if [ -n "$want" ]; then
    [ -d "$want" ] || return 1
    m="$( (cd "$want" && bash "$SCRIPT_DIR/main-repo-path.sh" path) 2>/dev/null)" || return 1
    [ -n "$m" ] || return 1
    [ "$(realpath_py "$m")" = "$(realpath_py "$want")" ] || return 1
  else
    m="$(bash "$SCRIPT_DIR/main-repo-path.sh" path 2>/dev/null)" || return 1
    [ -n "$m" ] || return 1
  fi
  REPO="$(realpath_py "$m")"
}

marker_dir() {
  local common
  common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  [ -n "$common" ] || return 1
  printf '%s/work-system/landing' "$common"
}

# Strict parse of a marker file → normalized key=value lines, or nothing.
PY_PARSE='import os, re, sys
path, task, wtdir = sys.argv[1], sys.argv[2], sys.argv[3]
KEYS = {"version": r"1", "task": r"[A-Za-z0-9_][A-Za-z0-9._-]*",
        "branch": r"[^\s]{1,255}", "worktree": r"/[^\t\n\x00]{1,4095}",
        "pr": r"[0-9]{1,9}", "phase": r"landing|handoff",
        "started_at": r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z",
        "worker_pane": r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}",
        "worker_session": r"[0-9a-fA-F-]{8,64}",
        "kicker_session": r"[0-9a-fA-F-]{8,64}",
        "kicker_record": r"absent|present",
        "kicker_source": r"kickoff|backfill"}
REQUIRED = ("version", "task", "branch", "worktree", "pr", "phase", "started_at")
try:
    raw = open(path, encoding="utf-8").read()
except Exception:
    sys.exit(1)
seen = {}
for line in raw.splitlines():
    if not line.strip():
        continue
    k, sep, v = line.partition("=")
    if not sep or k not in KEYS or k in seen or not re.fullmatch(KEYS[k], v):
        sys.exit(1)
    seen[k] = v
if any(k not in seen for k in REQUIRED) or seen["task"] != task:
    sys.exit(1)
seen["worktree"] = os.path.realpath(seen["worktree"])
if os.path.dirname(seen["worktree"]) != wtdir:
    sys.exit(1)
if ("worker_pane" in seen) != ("worker_session" in seen):
    sys.exit(1)
for k in KEYS:
    if k in seen:
        print("%s=%s" % (k, seen[k]))'

# read_marker <task> → MARKER_STATE none|invalid|ok and MARKER_BODY (parsed).
MARKER_STATE=none; MARKER_BODY=""; MARKER_FILE=""
read_marker() {
  local dir
  MARKER_STATE=none; MARKER_BODY=""; MARKER_FILE=""
  dir="$(marker_dir)" || { MARKER_STATE=invalid; return 0; }
  MARKER_FILE="$dir/$1"
  if [ -L "$MARKER_FILE" ]; then MARKER_STATE=invalid; return 0; fi
  [ -e "$MARKER_FILE" ] || return 0
  if MARKER_BODY="$(python3 -c "$PY_PARSE" "$MARKER_FILE" "$1" "$(realpath_py "$REPO/.claude/worktrees")" 2>/dev/null)" && [ -n "$MARKER_BODY" ]; then
    MARKER_STATE=ok
  else
    MARKER_STATE=invalid; MARKER_BODY=""
  fi
}

field() { printf '%s\n' "$MARKER_BODY" | sed -n "s/^$1=//p"; }

# The lane's worktree path (whether or not it still exists): the valid
# marker's, else the convention. Call after read_marker.
lane_path() {
  if [ "$MARKER_STATE" = ok ]; then field worktree; else realpath_py "$REPO/.claude/worktrees/$1"; fi
}

# lane_state <path> → present|gone: present only while git lists the worktree
# and does not call it prunable (a deleted dir git has not pruned yet).
lane_state() {
  git -C "$REPO" worktree list --porcelain 2>/dev/null | python3 -c 'import os, sys
want = sys.argv[1]
state = "gone"
blocks = sys.stdin.read().split("\n\n")
for b in blocks:
    lines = b.strip().splitlines()
    if lines and lines[0].startswith("worktree ") and os.path.realpath(lines[0][9:]) == want:
        if not any(l.startswith("prunable") for l in lines[1:]):
            state = "present"
print(state)' "$1" 2>/dev/null || echo gone
}

# write_marker <body> — atomic replace, 0600, never through a symlink.
write_marker() {
  local dir tmp
  dir="$(marker_dir)" || return 1
  mkdir -p "$dir" 2>/dev/null || return 1
  [ -L "$dir" ] && return 1
  [ -L "$MARKER_FILE" ] && return 1
  tmp="$(mktemp "$dir/.landing.XXXXXX" 2>/dev/null)" || return 1
  chmod 600 "$tmp" 2>/dev/null
  if ! printf '%s\n' "$1" > "$tmp" || ! mv -f "$tmp" "$MARKER_FILE"; then
    rm -f "$tmp"; return 1
  fi
}

# Parse the shared trailing [--repo <main>] after the positional arguments.
REPO_ARG=""
take_repo() {
  REPO_ARG=""
  case "${1:-}" in
    "") ;;
    --repo) [ $# -eq 2 ] && [ -n "$2" ] || usage; REPO_ARG="$2" ;;
    *) usage ;;
  esac
}

# --- begin -------------------------------------------------------------------
cmd_begin() {
  [ "${1:-}" = "--pr" ] && [ $# -eq 2 ] && valid_pr "$2" || usage
  local pr="$2" res task branch top
  no() { echo "recorded=no"; echo "reason=$1"; exit 0; }
  [ "$(bash "$SCRIPT_DIR/main-repo-path.sh" linked 2>/dev/null)" = linked ] || no not-a-lane
  resolve_repo "" || no no-main-repo
  res="$(bash "$SCRIPT_DIR/task-status.sh" resolve 2>/dev/null)" || no unresolved
  task="$(printf '%s\n' "$res" | sed -n 's/^task_name=//p')"
  branch="$(printf '%s\n' "$res" | sed -n 's/^task_branch=//p')"
  valid_task "$task" && [ -n "$branch" ] || no unresolved
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || no unresolved
  top="$(realpath_py "$top")"

  local body
  body="version=1
task=$task
branch=$branch
worktree=$top
pr=$pr
phase=landing
started_at=$(now_utc)"

  # The worker: this pane and the session it hosts, read from herdr (not env
  # alone — the session UUID is what survives a cwd that no longer exists).
  if [ "${HERDR_ENV:-}" = "1" ] && [ -n "${HERDR_PANE_ID:-}" ]; then
    local json who
    if json="$(ha_get "$HERDR_PANE_ID")"; then
      who="$(printf '%s' "$json" | python3 -c 'import json, re, sys
try:
    a = json.load(sys.stdin)["result"]
    if isinstance(a.get("agent"), dict) and "pane_id" not in a:
        a = a["agent"]
    s = (a.get("agent_session") or {}).get("value") or ""
    p = a.get("pane_id") or ""
except Exception:
    sys.exit(0)
if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}", p) and re.fullmatch(r"[0-9a-fA-F-]{8,64}", s):
    print("worker_pane=" + p)
    print("worker_session=" + s)' 2>/dev/null)"
      [ -n "$who" ] && body="$body
$who"
    fi
  fi
  # Whether the lane had a record at all: a gone-worktree hand-over may send
  # unasked to a sole root agent only when it never had one (like /close's
  # delegate_unasked), never when a broken or rejected record existed.
  if [ -e "$top/.ws-kicker" ] || [ -L "$top/.ws-kicker" ]; then
    body="$body
kicker_record=present"
  else
    body="$body
kicker_record=absent"
  fi
  # The Manager that kicked the lane off, so a /land whose worktree vanished
  # can still recognize it among the agents at the repo root.
  if [ -f "$top/.ws-kicker" ] && [ ! -L "$top/.ws-kicker" ]; then
    local ks src
    # Exactly one agent_session= line, or nothing is copied (a record manager.sh
    # would reject as malformed must not reach the marker either).
    ks="$(sed -n 's/^agent_session=//p' "$top/.ws-kicker")"
    case "$ks" in *$'\n'*) ks="" ;; esac
    if printf '%s' "$ks" | grep -Eq '^[0-9a-fA-F-]{8,64}$'; then
      src="$(sed -n 's/^source=//p' "$top/.ws-kicker")"
      [ "$src" = backfill ] || src=kickoff
      body="$body
kicker_session=$ks
kicker_source=$src"
    fi
  fi

  MARKER_FILE="$(marker_dir)/$task" || no unwritable
  write_marker "$body" || no unwritable
  echo "recorded=yes"
  echo "marker=$MARKER_FILE"
  case "$body" in *worker_pane=*) ;; *) echo "reason=no-worker-identity" ;; esac
}

# --- phase / end ---------------------------------------------------------------
cmd_phase() {
  local task="${1:-}" ph="${2:-}"
  valid_task "$task" && [ "$ph" = handoff ] || usage
  shift 2; take_repo "$@"
  resolve_repo "$REPO_ARG" || exit 1
  read_marker "$task"
  if [ "$MARKER_STATE" != ok ]; then echo "updated=no"; echo "reason=marker-$MARKER_STATE"; return 0; fi
  if write_marker "$(printf '%s\n' "$MARKER_BODY" | sed 's/^phase=.*/phase=handoff/')"; then
    echo "updated=yes"
  else
    echo "updated=no"; echo "reason=unwritable"
  fi
}

cmd_end() {
  local task="${1:-}"
  valid_task "$task" || usage
  shift; take_repo "$@"
  resolve_repo "$REPO_ARG" || exit 1
  read_marker "$task"
  if [ -e "$MARKER_FILE" ] || [ -L "$MARKER_FILE" ]; then
    if rm -f "$MARKER_FILE" 2>/dev/null; then echo "removed=yes"; else echo "removed=no"; echo "reason=unremovable"; fi
  else
    echo "removed=no"
  fi
}

# --- status ---------------------------------------------------------------------
# stdin: agent-list JSON. argv: pane, session, worktree, main root.
PY_LIVE='import json, re, sys
pane, sess, wt, main = sys.argv[1:5]
try:
    agents = json.load(sys.stdin)["result"]["agents"]
except Exception:
    agents = None
if not agents:
    print("unverified"); sys.exit(0)
root, wtdir = match_roots(main)
wt = os.path.realpath(wt)
for a in agents:
    if not isinstance(a, dict) or a.get("pane_id") != pane:
        continue
    s = a.get("agent_session") or {}
    if not isinstance(s, dict) or str(s.get("value") or "") != sess:
        break   # the pane now hosts another session: the worker is gone
    tab = a.get("tab_id") or ""
    cwd = str(a.get("cwd") or "").rstrip("/")
    if not cwd:
        where = "unknown"
    else:
        rp = os.path.realpath(cwd)
        if rp == wt or rp.startswith(wt + os.sep):
            where = "match"
        elif rp == root or os.path.isdir(rp):
            where = "mismatch"   # it sits somewhere real that is not this lane
        else:
            where = "unknown"
    print("live")
    ok = isinstance(tab, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}", tab)
    print(tab if ok else "")
    print(where)
    sys.exit(0)
print("stale")'

cmd_status() {
  local task="${1:-}"
  valid_task "$task" || usage
  shift; take_repo "$@"
  resolve_repo "$REPO_ARG" || exit 1
  read_marker "$task"
  case "$MARKER_STATE" in
    none)    echo "landing=none"; return 0 ;;
    invalid) echo "landing=invalid"; return 0 ;;
  esac
  local wt pane sess state=unverified tab="" where="" out json
  wt="$(field worktree)"; pane="$(field worker_pane)"; sess="$(field worker_session)"
  if [ -n "$pane" ] && [ "${HERDR_ENV:-}" = "1" ] && json="$(ha_list)"; then
    out="$(printf '%s' "$json" | python3 -c "$HERDR_MATCH_PRELUDE
$PY_LIVE" "$pane" "$sess" "$wt" "$REPO" 2>/dev/null)" || out=unverified
    state="${out%%$'\n'*}"
    if [ "$state" = live ]; then
      tab="$(printf '%s\n' "$out" | sed -n 2p)"; where="$(printf '%s\n' "$out" | sed -n 3p)"
    fi
    case "$state" in live|stale) ;; *) state=unverified ;; esac
  fi
  echo "landing=$state"
  local k
  for k in phase pr branch worktree started_at kicker_session kicker_source kicker_record; do
    printf '%s=%s\n' "$k" "$(field "$k")"
  done
  echo "worktree_state=$(lane_state "$wt")"
  if [ "$state" = live ]; then
    echo "worker_pane=$pane"
    echo "worker_tab=$tab"
    echo "worker_cwd=$where"
  fi
}

# --- lane / gone-check -------------------------------------------------------------
cmd_lane() {
  local task="${1:-}" wt st
  valid_task "$task" || usage
  shift; take_repo "$@"
  resolve_repo "$REPO_ARG" || exit 1
  read_marker "$task"
  wt="$(lane_path "$task")"; st="$(lane_state "$wt")"
  echo "worktree=$wt"
  echo "worktree_state=$st"
  if [ "$st" = gone ] && [ -e "$wt" ]; then echo "leftover=yes"; else echo "leftover=no"; fi
}

cmd_gone_check() {
  local task="${1:-}"
  valid_task "$task" || usage
  shift
  [ "${1:-}" = "--pr" ] && valid_pr "${2:-}" || usage
  local pr="$2"; shift 2; take_repo "$@"
  resolve_repo "$REPO_ARG" || exit 1
  read_marker "$task"
  local branch="task/$task"
  [ "$MARKER_STATE" = ok ] && branch="$(field branch)"
  if [ "$(lane_state "$(lane_path "$task")")" = present ]; then
    echo "worktree_state=present"; return 0
  fi
  echo "worktree_state=gone"
  local out state="" num="" head="" merge="" oid=""
  if command -v gh >/dev/null 2>&1; then
    out="$( (cd "$REPO" && gh pr view "$pr" --json number,state,headRefName,headRefOid,mergeCommit \
              --jq '"\(.number)|\(.state)|\(.headRefName)|\(.headRefOid)|\(.mergeCommit.oid // "")"') 2>/dev/null || true)"
    num="${out%%|*}"; out="${out#*|}"; state="${out%%|*}"; out="${out#*|}"
    head="${out%%|*}"; out="${out#*|}"; oid="${out%%|*}"; merge="${out#*|}"
    case "$num" in *[!0-9]*) num="" ;; esac
    case "$state" in OPEN|CLOSED|MERGED) ;; *) state=""; head=""; oid="" ;; esac
    case "$merge" in *[!0-9a-f]*) merge="" ;; esac
    case "$oid" in *[!0-9a-f]*) oid="" ;; esac
    echo "pr_state=${state:-unknown}"
  else
    echo "pr_state=nogh"
  fi
  echo "pr_number=$num"
  local hm=unknown
  if [ -n "$head" ]; then
    if [ "$head" = "$branch" ]; then hm=yes; else hm=no; fi
  fi
  echo "head_branch_match=$hm"
  echo "merge_sha=$merge"
  echo "branch=$branch"
  # A local branch that outlived its worktree may hold commits the PR lacks;
  # /close deletes it, so say where it stands against the merged head.
  local tip lr l r bt
  tip="$(git -C "$REPO" rev-parse --verify --quiet "refs/heads/$branch" 2>/dev/null || true)"
  if [ -z "$tip" ]; then bt=none
  elif [ "${#oid}" -ne 40 ] || ! { git -C "$REPO" cat-file -e "$oid^{commit}" 2>/dev/null \
       || GIT_TERMINAL_PROMPT=0 _ha_bounded "$HA_CALL_TIMEOUT_SECS" git -C "$REPO" fetch -q origin "$oid" 2>/dev/null; }; then
    bt=unreadable
  else
    lr="$(git -C "$REPO" rev-list --left-right --count "$tip...$oid" 2>/dev/null || true)"
    l="${lr%%[!0-9]*}"; r="${lr##*[!0-9]}"
    if [ -z "$l" ] || [ -z "$r" ]; then bt=unreadable
    elif [ "$l" = 0 ] && [ "$r" = 0 ]; then bt=same
    elif [ "$l" = 0 ]; then bt=behind
    elif [ "$r" = 0 ]; then bt=ahead
    else bt=diverged
    fi
  fi
  echo "branch_tip=$bt"
  # The decision, in one place: the same PR, merged, from this branch; nothing
  # only the surviving branch holds; and a branch /close can resolve by name.
  local stop=""
  if [ "$state" != MERGED ] || [ "$num" != "$pr" ] || [ "$hm" != yes ]; then stop=not-merged
  elif [ "$bt" = ahead ] || [ "$bt" = diverged ]; then stop=branch-ahead
  elif [ "$bt" = unreadable ]; then stop=branch-unreadable
  elif [ "$branch" != "task/$task" ] && [ "$branch" != "$task" ]; then stop=not-by-name
  fi
  if [ -n "$stop" ]; then echo "verdict=stop"; echo "stop=$stop"; else echo "verdict=handoff"; fi
}

case "${1:-}" in
  begin)      shift; cmd_begin "$@" ;;
  phase)      shift; cmd_phase "$@" ;;
  end)        shift; cmd_end "$@" ;;
  status)     shift; cmd_status "$@" ;;
  lane)       shift; cmd_lane "$@" ;;
  gone-check) shift; cmd_gone_check "$@" ;;
  *) usage ;;
esac
