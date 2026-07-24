#!/usr/bin/env bash
# lanes.sh — the Manager's lane registry: one row per ACTIVE WORKTREE of the
# repo, joining backlog STATE (ws-statusline states) with herdr LIVENESS (herdr
# agent list). A lane's identity is its WORKTREE PATH — pane/tab ids are
# liveness data that churns, never identity (see manager-worker-orchestration).
#
# Usage:  lanes.sh [--json] [<dir>]
#   <dir>   any path inside the repo (default: $PWD); the main worktree, its
#           tasks/ backlog and .claude/worktrees/ are all resolved from it.
#   --json  emit a JSON array of objects instead of tab-separated rows.
#
# Columns (TSV field order == JSON object keys):
#   task  worktree  branch  state  glyph  agent  agent_status  pane  tab  session
#     task/worktree/branch  — from `git worktree list` (the authoritative lane set)
#     state/glyph           — from `ws-statusline.sh states` (blank if the task is
#                             not in the backlog, e.g. archived or /adopt-ed)
#     agent…session         — from `herdr agent list`, joined on worktree cwd
#
# Liveness degradation (mirrors herdr-teardown's worktree-tab-state tri-state):
#   * OUTSIDE a herdr session (HERDR_ENV != 1) → liveness columns BLANK; the
#     state/git columns still render. A pure survey, never an error.
#   * INSIDE herdr but the agent list is unreachable/empty (repopulating) →
#     FAIL-CLOSED: agent_status = "unverified"; never a guessed liveness.
#   * INSIDE herdr, populated list, worktree not among the agents → confidently
#     no live worker: liveness BLANK.
#
# Always exits 0 (a non-repo, or a repo with no task worktrees, prints nothing).
#
# CWD safety: git is addressed with `git -C "$DIR"`; both sides of every cwd
# compare are realpath-resolved (in classify_cwd / the porcelain walk); this
# script never `cd`s.
set -u

SCRIPT_DIR="${0%/*}"
# $HERDR_MATCH_PRELUDE (the realpath cwd match) + ha_list live in herdr-agent.sh;
# source it (side-effect free) rather than re-deriving the match here.
. "$SCRIPT_DIR/herdr-agent.sh"

FORMAT=tsv
[ "${1:-}" = "--json" ] && { FORMAT=json; shift; }
DIR="${1:-$PWD}"

# --- lane set + branch: `git worktree list` (authoritative) ------------------
# LANES_WORKTREES_FILE is a TEST SEAM: it injects porcelain content so the join
# logic is exercisable without a real repo/worktrees. Production reads live git.
if [ -n "${LANES_WORKTREES_FILE:-}" ]; then
  WORKTREES="$(cat "$LANES_WORKTREES_FILE")"
else
  git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1 || exit 0
  WORKTREES="$(git -C "$DIR" worktree list --porcelain 2>/dev/null)"
fi
[ -n "$WORKTREES" ] || exit 0

# The main worktree is the first porcelain entry — the backlog + worktrees dir
# live there. Strip "worktree " without field-splitting so a path with spaces
# survives.
MAIN="$(printf '%s\n' "$WORKTREES" | sed -n 's/^worktree //p' | head -1)"
[ -n "$MAIN" ] || exit 0

# --- backlog state: `ws-statusline.sh states --cached` -----------------------
# --cached = pure survey (read the PR cache, never a synchronous gh call): the
# Manager view must not stall on the network. LANES_STATES_FILE is a test seam.
if [ -n "${LANES_STATES_FILE:-}" ]; then
  STATES="$(cat "$LANES_STATES_FILE")"
else
  STATES="$(bash "$SCRIPT_DIR/ws-statusline.sh" states --cached "$DIR" 2>/dev/null || true)"
fi

# --- herdr liveness ----------------------------------------------------------
# LIVENESS_MODE drives the degrade policy in the join:
#   absent      → blank liveness (we are OUTSIDE a herdr session; do not check)
#   unverified  → fail-closed (INSIDE herdr but the list was unreachable)
#   list        → parse $AGENTS_FILE (the join refines empty/malformed → unverified)
# LANES_AGENTS_FILE is a test seam that forces the `list` path with fixed JSON.
AGENTS_FILE=""
LIVENESS_MODE=absent
if [ -n "${LANES_AGENTS_FILE:-}" ]; then
  AGENTS_FILE="$LANES_AGENTS_FILE"
  LIVENESS_MODE=list
elif [ "${HERDR_ENV:-}" = "1" ]; then
  LIVENESS_MODE=unverified          # inside herdr: fail-closed until we have a list
  if agents_json="$(ha_list)"; then
    AGENTS_FILE="$(mktemp "${TMPDIR:-/tmp}/lanes-agents.XXXXXX")" || AGENTS_FILE=""
    if [ -n "$AGENTS_FILE" ]; then
      # EXIT trap (not a trailing rm): a signal between mktemp and cleanup would
      # otherwise orphan the temp file. ${AGENTS_FILE:-} keeps it safe under set -u.
      trap 'rm -f "${AGENTS_FILE:-}"' EXIT
      printf '%s' "$agents_json" > "$AGENTS_FILE"
      LIVENESS_MODE=list
    fi
  fi
fi

# --- the join ----------------------------------------------------------------
# stdin  = the worktree porcelain (the lane set + branch)
# argv   = main, format, liveness-mode, agents-file, states-blob
# The prelude (match_roots / classify_cwd) is prepended so the agent→worktree
# match is the SAME one herdr-tab-glyph.sh uses.
lanes_join='import sys, json, os
main = sys.argv[1]
fmt = sys.argv[2]
mode = sys.argv[3]
agents_file = sys.argv[4]
states_blob = sys.argv[5] if len(sys.argv) > 5 else ""

root, wtdir = match_roots(main)
if root is None:
    sys.exit(0)

# state + glyph by task name (ws-statusline states emits task\tstate\tglyph)
state_of = {}
for line in states_blob.splitlines():
    p = line.split("\t")
    if len(p) >= 3:
        state_of[p[0]] = (p[1], p[2])

# liveness by worktree realpath (first agent in a worktree wins). mode may be
# demoted to "unverified" here when the list is malformed/empty (fail-closed).
live = {}
if mode == "list" and agents_file:
    try:
        agents = json.load(open(agents_file))["result"]["agents"]
    except Exception:
        agents = None
    if agents is None:
        mode = "unverified"       # malformed → do not guess
    elif not agents:
        mode = "unverified"       # empty/repopulating list → do not guess
    else:
        for a in agents:
            cwd = a.get("cwd")
            kind, key = classify_cwd(cwd, root, wtdir)
            if kind != "task":
                continue
            wt = os.path.realpath((cwd or "").rstrip("/"))
            if wt in live:
                continue
            sess = ""
            s = a.get("agent_session")
            if isinstance(s, dict):
                sess = s.get("value") or ""
            live[wt] = {
                "agent": a.get("agent") or "",
                "agent_status": a.get("agent_status") or "",
                "pane": a.get("pane_id") or "",
                "tab": a.get("tab_id") or "",
                "session": sess,
            }

# lane set from the porcelain: keep only direct children of the worktrees dir
# (drops the main worktree and any external/manual worktree).
lanes = []
cur_path = None
cur_branch = ""
def flush():
    global cur_path, cur_branch
    if cur_path is not None:
        rp = os.path.realpath(cur_path)
        if rp != root and os.path.dirname(rp) == wtdir:
            lanes.append((os.path.basename(rp), rp, cur_branch))
    cur_path = None
    cur_branch = ""
for line in sys.stdin.read().splitlines():
    if line.startswith("worktree "):
        flush()
        cur_path = line[len("worktree "):]
    elif line.startswith("branch refs/heads/"):
        cur_branch = line[len("branch refs/heads/"):]
    elif line == "detached":
        cur_branch = ""
flush()
lanes.sort(key=lambda t: t[0])

def liveness_for(wt):
    if wt in live:
        L = live[wt]
        return (L["agent"], L["agent_status"], L["pane"], L["tab"], L["session"])
    if mode == "unverified":
        return ("", "unverified", "", "", "")
    return ("", "", "", "", "")   # absent (outside herdr) OR present-but-no-agent

COLS = ["task", "worktree", "branch", "state", "glyph",
        "agent", "agent_status", "pane", "tab", "session"]
rows = []
for task, wt, branch in lanes:
    state, glyph = state_of.get(task, ("", ""))
    agent, astatus, pane, tab, session = liveness_for(wt)
    rows.append({"task": task, "worktree": wt, "branch": branch,
                 "state": state, "glyph": glyph, "agent": agent,
                 "agent_status": astatus, "pane": pane, "tab": tab,
                 "session": session})

if fmt == "json":
    print(json.dumps(rows, ensure_ascii=False))
else:
    for r in rows:
        print("\t".join(r[c] for c in COLS))'

printf '%s' "$WORKTREES" \
  | PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$lanes_join" "$MAIN" "$FORMAT" "$LIVENESS_MODE" "$AGENTS_FILE" "$STATES"
