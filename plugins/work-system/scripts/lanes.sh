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
# Always exits 0 for repo/herdr state: a non-repo, or a repo with no task
# worktrees, yields NO rows — empty stdout in TSV, `[]` in --json. (python3 is a
# hard dependency shared by every sibling script; its absence is out of scope for
# the exit-0 promise, same as the rest of the plugin.)
#
# CWD safety: git is addressed with `git -C "$DIR"`; both sides of every cwd
# compare are realpath-resolved (in classify_cwd / the porcelain walk); this
# script never `cd`s.
set -u

# Resolve the script's own dir via BASH_SOURCE (robust to a bare-name invocation
# like `bash lanes.sh`, where `${0%/*}` would wrongly leave `lanes.sh`), so the
# sibling `source` below always finds herdr-agent.sh.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# $HERDR_MATCH_PRELUDE (the realpath cwd match) + ha_list live in herdr-agent.sh;
# source it (side-effect free) rather than re-deriving the match here.
. "$SCRIPT_DIR/herdr-agent.sh"

FORMAT=tsv
[ "${1:-}" = "--json" ] && { FORMAT=json; shift; }
DIR="${1:-$PWD}"

# The empty result, emitted by every early-exit guard: `[]` in --json (so a
# consumer's json.loads never hits empty stdout), nothing in TSV. Always exit 0.
_empty_exit() { [ "$FORMAT" = json ] && printf '[]\n'; exit 0; }

# --- lane set + branch: `git worktree list` (authoritative) ------------------
# LANES_WORKTREES_FILE is a TEST SEAM: it injects porcelain content so the join
# logic is exercisable without a real repo/worktrees. Production reads live git.
if [ -n "${LANES_WORKTREES_FILE:-}" ]; then
  WORKTREES="$(cat "$LANES_WORKTREES_FILE")"
else
  git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1 || _empty_exit
  WORKTREES="$(git -C "$DIR" worktree list --porcelain 2>/dev/null)"
fi
[ -n "$WORKTREES" ] || _empty_exit

# The main worktree is the first porcelain entry — the backlog + worktrees dir
# live there. Strip "worktree " without field-splitting so a path with spaces
# survives.
MAIN="$(printf '%s\n' "$WORKTREES" | sed -n 's/^worktree //p' | head -1)"
[ -n "$MAIN" ] || _empty_exit

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
lanes_join='import sys, json
main = sys.argv[1]
fmt = sys.argv[2]
mode = sys.argv[3]
agents_file = sys.argv[4]
states_blob = sys.argv[5] if len(sys.argv) > 5 else ""

# realpath helpers come from the prepended $HERDR_MATCH_PRELUDE (it imports os).
def _s(v):
    # Coerce any cell value to a string: herdr fields are untrusted and may be
    # non-string JSON (e.g. a numeric agent_status), which would crash the TSV
    # scrub or type-mismatch the JSON. None -> "".
    return "" if v is None else str(v)

root, wtdir = match_roots(main)
if root is None:
    if fmt == "json":
        print("[]")
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
saw_malformed = False   # a non-dict element seen → the list is partly untrustworthy
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
            if not isinstance(a, dict):
                saw_malformed = True      # non-dict element (e.g. a bare null) — skip, never crash
                continue
            kind, key, wt = classify_cwd(a.get("cwd"), root, wtdir)
            if kind != "task":
                continue
            if wt in live:                # first agent in a worktree wins
                continue
            sess = ""
            s = a.get("agent_session")
            if isinstance(s, dict):
                sess = _s(s.get("value"))
            live[wt] = {
                "agent": _s(a.get("agent")),
                "agent_status": _s(a.get("agent_status")),
                "pane": _s(a.get("pane_id")),
                "tab": _s(a.get("tab_id")),
                "session": sess,
            }

# lane set from the porcelain: keep only worktrees classify_cwd calls "task"
# (drops the main worktree and any external/manual worktree) — the SAME shared
# classification the liveness join and herdr-tab-glyph.sh use, so the rule can
# never drift between the two consumers.
lanes = []
cur_path = None
cur_branch = ""
def flush():
    global cur_path, cur_branch
    if cur_path is not None:
        kind, key, rp = classify_cwd(cur_path, root, wtdir)
        if kind == "task":
            lanes.append((key, rp, cur_branch))
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
    # A matched lane keeps its confident values above. For an UNMATCHED lane, a
    # list that carried a malformed element can no longer be fully trusted to
    # assert "no worker here" (the junk element may have been this lane agent) —
    # fail closed to unverified, same as an unreachable/empty list.
    if mode == "unverified" or saw_malformed:
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
    # Scrub tab/CR/LF from every cell before emitting TSV: agent/agent_status/
    # pane/tab/session are UNTRUSTED herdr fields (any tool in the session can set
    # them), so an embedded tab/newline would forge extra columns/rows and a
    # consumer cut -f9/f10 would aim a herdr op at the wrong id. Mirrors the same
    # scrub herdr-tab-glyph.sh applies to untrusted label/key fields. (--json is
    # already safe — json.dumps quotes the control chars.)
    import re
    def _cell(s):
        return re.sub(r"[\t\r\n]", " ", s)
    for r in rows:
        print("\t".join(_cell(r[c]) for c in COLS))'

printf '%s' "$WORKTREES" \
  | PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$lanes_join" "$MAIN" "$FORMAT" "$LIVENESS_MODE" "$AGENTS_FILE" "$STATES"
