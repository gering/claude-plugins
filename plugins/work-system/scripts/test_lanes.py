#!/usr/bin/env python3
"""Tests for lanes.sh — the lane registry join.

lanes.sh exposes three TEST SEAMS (env vars) so the join is exercisable without
a real repo/herdr: LANES_WORKTREES_FILE (porcelain), LANES_STATES_FILE
(task\\tstate\\tglyph), LANES_AGENTS_FILE (agent-list JSON → forces the `list`
liveness path). These drive the REAL lanes.sh end to end.

Covers the three required paths — states⨝liveness JOIN, the DEGRADED path
(outside herdr → blank liveness), the UNVERIFIED path (inside herdr, empty list
→ fail-closed) — plus: first-agent-in-a-worktree wins, main + external worktrees
are excluded, malformed list → unverified, and a backlog-less worktree gets
blank state.
"""
import json as jsonlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "lanes.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


ROOT = "/lanes-test-root"   # fake, need not exist — realpath just normalizes it
WT = f"{ROOT}/.claude/worktrees"

# main + two task worktrees + one EXTERNAL worktree (not under .claude/worktrees)
PORCELAIN = "\n".join([
    f"worktree {ROOT}",
    "HEAD aaaa",
    "branch refs/heads/main",
    "",
    f"worktree {WT}/alpha",
    "HEAD bbbb",
    "branch refs/heads/task/alpha",
    "",
    f"worktree {WT}/beta",
    "HEAD cccc",
    "branch refs/heads/task/beta",
    "",
    f"worktree {WT}/gamma",   # a worktree with NO backlog task (archived/adopted)
    "HEAD gggg",
    "branch refs/heads/task/gamma",
    "",
    f"worktree {ROOT}/external-wt",
    "HEAD dddd",
    "branch refs/heads/misc",
    "",
])

STATES = "alpha\tactive\t●\nbeta\treview\t◇\n"


def run(agents_json=None, herdr_env=False, fmt="--json", worktrees=PORCELAIN):
    """Run lanes.sh with injected worktrees/states and (optionally) an agent
    list. Returns parsed JSON rows keyed by task."""
    env = dict(os.environ)
    env.pop("HERDR_ENV", None)
    if herdr_env:
        env["HERDR_ENV"] = "1"
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)
    (d / "wt").write_text(worktrees)
    (d / "states").write_text(STATES)
    env["LANES_WORKTREES_FILE"] = str(d / "wt")
    env["LANES_STATES_FILE"] = str(d / "states")
    if agents_json is not None:
        (d / "agents").write_text(agents_json)
        env["LANES_AGENTS_FILE"] = str(d / "agents")
    else:
        env.pop("LANES_AGENTS_FILE", None)
    args = ["bash", str(SCRIPT)]
    if fmt:
        args.append(fmt)
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)
    tmp.cleanup()
    if fmt == "--json" and r.stdout.strip():
        return r, {row["task"]: row for row in jsonlib.loads(r.stdout)}
    return r, {}


# --- exclusion: only the three .claude/worktrees children are lanes -------- #
r, rows = run()
check("lane set: alpha+beta+gamma (main + external dropped)",
      set(rows) == {"alpha", "beta", "gamma"})
check("exit 0", r.returncode == 0)

# --- state join: state/glyph land regardless of liveness ------------------- #
check("state: alpha active", rows["alpha"]["state"] == "active" and rows["alpha"]["glyph"] == "●")
check("state: beta review", rows["beta"]["state"] == "review" and rows["beta"]["glyph"] == "◇")
check("branch carried", rows["alpha"]["branch"] == "task/alpha")

# --- DEGRADED (outside herdr, no agent list): liveness blank --------------- #
check("degraded: alpha agent blank", rows["alpha"]["agent"] == "")
check("degraded: alpha status blank", rows["alpha"]["agent_status"] == "")
check("degraded: alpha pane/tab/session blank",
      rows["alpha"]["pane"] == "" and rows["alpha"]["tab"] == "" and rows["alpha"]["session"] == "")

# --- JOIN: a populated list attaches liveness; first agent in a wt wins ----- #
agents = jsonlib.dumps({"result": {"agents": [
    {"agent": "claude", "agent_status": "working", "cwd": f"{WT}/alpha",
     "pane_id": "w1:p9", "tab_id": "w1:t9", "agent_session": {"value": "UUID-A"}},
    {"agent": "codex", "agent_status": "idle", "cwd": f"{WT}/alpha",
     "pane_id": "w1:pX", "tab_id": "w1:tX", "agent_session": {"value": "UUID-B"}},
]}})
r, rows = run(agents_json=agents, herdr_env=True)
check("join: alpha agent=claude (first wins)", rows["alpha"]["agent"] == "claude")
check("join: alpha status=working", rows["alpha"]["agent_status"] == "working")
check("join: alpha pane/tab from first agent",
      rows["alpha"]["pane"] == "w1:p9" and rows["alpha"]["tab"] == "w1:t9")
check("join: alpha session UUID-A", rows["alpha"]["session"] == "UUID-A")
check("join: second agent (codex) ignored", rows["alpha"]["session"] != "UUID-B")
# beta has NO agent while the list is populated → confidently blank, NOT unverified
check("join: beta blank (no agent, list populated)",
      rows["beta"]["agent"] == "" and rows["beta"]["agent_status"] == "")
check("join: beta keeps its state", rows["beta"]["state"] == "review")

# --- UNVERIFIED: inside herdr but the list is empty (repopulating) --------- #
r, rows = run(agents_json=jsonlib.dumps({"result": {"agents": []}}), herdr_env=True)
check("unverified: alpha agent_status=unverified", rows["alpha"]["agent_status"] == "unverified")
check("unverified: beta agent_status=unverified", rows["beta"]["agent_status"] == "unverified")
check("unverified: agent name still blank", rows["alpha"]["agent"] == "")
check("unverified: state still joined", rows["alpha"]["state"] == "active")

# --- UNVERIFIED: a malformed list is fail-closed, never guessed ------------ #
r, rows = run(agents_json="broken{", herdr_env=True)
check("malformed: alpha unverified", rows["alpha"]["agent_status"] == "unverified")

# --- backlog-less worktree (gamma): still a lane, blank state/glyph -------- #
r, rows = run()
check("backlog-less: gamma is a lane", "gamma" in rows)
check("backlog-less: gamma state blank", rows["gamma"]["state"] == "")
check("backlog-less: gamma glyph blank", rows["gamma"]["glyph"] == "")
check("backlog-less: gamma branch still carried", rows["gamma"]["branch"] == "task/gamma")

# --- TSV output shape: 10 tab-separated columns per lane ------------------- #
r, _ = run(fmt=None)  # default TSV
tsv_lines = [ln for ln in r.stdout.splitlines() if ln]
check("tsv: three rows", len(tsv_lines) == 3)
check("tsv: 10 columns", all(len(ln.split("\t")) == 10 for ln in tsv_lines))

# --- null / non-dict agent element: no crash; matched keeps value, UNMATCHED --
#     lanes fail closed to unverified (the list can't be fully trusted) -------- #
mixed = jsonlib.dumps({"result": {"agents": [
    None,
    {"agent": "claude", "agent_status": "working", "cwd": f"{WT}/alpha",
     "pane_id": "w1:p9", "tab_id": "w1:t9", "agent_session": {"value": "U"}},
]}})
r, rows = run(agents_json=mixed, herdr_env=True)
check("null-element: exit 0 (no crash)", r.returncode == 0)
check("null-element: matched lane keeps confident value", rows["alpha"]["agent"] == "claude")
check("null-element: unmatched lane fails closed to unverified",
      rows["beta"]["agent_status"] == "unverified")

# --- non-string agent field: coerced, never crashes the TSV scrub ---------- #
nonstr = jsonlib.dumps({"result": {"agents": [
    {"agent": "claude", "agent_status": 7, "cwd": f"{WT}/alpha",
     "pane_id": "w1:p9", "tab_id": "w1:t9", "agent_session": {"value": "U"}},
]}})
r, _ = run(agents_json=nonstr, herdr_env=True, fmt=None)  # TSV path
check("non-string field: exit 0 (no TypeError)", r.returncode == 0)
r, rows = run(agents_json=nonstr, herdr_env=True)
check("non-string field: coerced to str in JSON", rows["alpha"]["agent_status"] == "7")

# --- TSV field-injection: tab/newline in untrusted fields is scrubbed ------ #
inj = jsonlib.dumps({"result": {"agents": [
    {"agent": "claude", "agent_status": "working\tfake\nrow", "cwd": f"{WT}/alpha",
     "pane_id": "w1:p9", "tab_id": "w1:t9", "agent_session": {"value": "U"}},
]}})
r, _ = run(agents_json=inj, herdr_env=True, fmt=None)  # TSV path
inj_lines = [ln for ln in r.stdout.splitlines() if ln]
check("tsv-injection: row count unchanged (no forged line)", len(inj_lines) == 3)
check("tsv-injection: every row still 10 columns", all(len(ln.split("\t")) == 10 for ln in inj_lines))

# --- --json early-exit paths emit [] (never empty stdout) ------------------ #
r, _ = run(worktrees="", fmt="--json")          # empty worktree porcelain
check("json-empty: exit 0", r.returncode == 0)
check("json-empty: prints [] not empty", r.stdout.strip() == "[]")
r, _ = run(worktrees="", fmt=None)              # TSV counterpart prints nothing
check("tsv-empty: prints nothing", r.stdout.strip() == "")


# --- INTEGRATION: the production HERDR_ENV → ha_list → mktemp → trap → join
#     glue (NOT the LANES_AGENTS_FILE seam), via a fake `herdr` on PATH -------- #
def run_with_fake_herdr(agents_json, worktrees=PORCELAIN):
    env = dict(os.environ)
    env["HERDR_ENV"] = "1"
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)
    (d / "wt").write_text(worktrees)
    (d / "states").write_text(STATES)
    env["LANES_WORKTREES_FILE"] = str(d / "wt")
    env["LANES_STATES_FILE"] = str(d / "states")
    env.pop("LANES_AGENTS_FILE", None)   # force the real ha_list branch, not the seam
    bindir = d / "bin"
    bindir.mkdir()
    herdr = bindir / "herdr"
    herdr.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2" = "agent list" ]; then\n'
        "  cat <<'JSON'\n" + agents_json + "\nJSON\n"
        "  exit 0\n"
        "fi\n"
        "exit 9\n"
    )
    herdr.chmod(0o755)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    r = subprocess.run(["bash", str(SCRIPT), "--json"], env=env,
                       capture_output=True, text=True, timeout=20)
    tmp.cleanup()
    rows = ({row["task"]: row for row in jsonlib.loads(r.stdout)}
            if r.stdout.strip() else {})
    return r, rows


live = jsonlib.dumps({"result": {"agents": [
    {"agent": "claude", "agent_status": "working", "cwd": f"{WT}/alpha",
     "pane_id": "w1:p9", "tab_id": "w1:t9", "agent_session": {"value": "LIVE"}},
]}})
r, rows = run_with_fake_herdr(live)
check("integration: exit 0", r.returncode == 0)
check("integration: ha_list path joins liveness", rows.get("alpha", {}).get("agent") == "claude")
check("integration: session from the live path", rows.get("alpha", {}).get("session") == "LIVE")
check("integration: unmatched lane blank (list populated)",
      rows.get("beta", {}).get("agent_status") == "")


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("lanes.sh: all tests passed")
