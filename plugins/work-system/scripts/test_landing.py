#!/usr/bin/env python3
"""Tests for landing.sh — the /land marker that outlives its worktree.

A REAL git repo with a task worktree under .claude/worktrees/, a fake `herdr`
(agent get / agent list) and a fake `gh` (pr view) in front of a shadow copy of
/usr/bin + /bin with every host gh/herdr filtered out.

Covers: begin inside/outside a lane (with and without herdr), the marker's place
(git common dir, so a worktree removal keeps it) and mode, status live / stale /
unverified / invalid and the worker-cwd classes, a worktree removed under the
marker (tab still found by pane + session), phase/end, gone-check, and the
argument guards (hostile task name, --repo that is not a main checkout).
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "landing.sh"
SESSION = "11111111-2222-3333-4444-555555555555"
KICKER = "99999999-8888-7777-6666-555555555555"

FAILS = []

_SHADOW = tempfile.TemporaryDirectory()
SHADOW = Path(os.path.realpath(_SHADOW.name))


def build_shadow(dest, sources):
    # A dangling source entry is skipped, so a later source's real binary of
    # that name still gets linked; lexists, not exists(), so a name already
    # placed counts as present (usrmerge lists every name under both dirs).
    for src in sources:
        for name in os.listdir(src):
            target = os.path.join(src, name)
            if (name in ("gh", "herdr") or not os.path.exists(target)
                    or os.path.lexists(dest / name)):
                continue
            (dest / name).symlink_to(target)


build_shadow(SHADOW, ("/usr/bin", "/bin"))


def check(name, cond):
    if not cond:
        FAILS.append(name)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def parse(out):
    d = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        d[k] = v
    return d


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(os.path.realpath(self.tmp.name))
        self.d = d
        self.main = d / "repo"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "config", "user.email", "t@example.com")
        git(self.main, "config", "user.name", "t")
        git(self.main, "config", "core.hooksPath", "/dev/null")
        (self.main / "README").write_text("x\n")
        git(self.main, "add", "README")
        git(self.main, "commit", "-q", "-m", "init")
        self.wt = self.main / ".claude" / "worktrees" / "foo"
        git(self.main, "worktree", "add", "-q", "-b", "task/foo", str(self.wt))
        (self.wt / ".ws-kicker").write_text(f"version=1\nagent_session={KICKER}\n")
        self.marker = self.main / ".git" / "work-system" / "landing" / "foo"

        self.bin = d / "bin"
        self.bin.mkdir()
        self.state = d / "state"
        self.state.mkdir()
        self.set_get(self.worker(self.wt))
        self.set_agents([self.worker(self.wt)])
        self.set_prview("MERGED", "task/foo", git(self.main, "rev-parse", "task/foo"))
        st = self.state
        (self.bin / "herdr").write_text(
            "#!/bin/bash\n"
            'case "$1 $2" in\n'
            f'  "agent get") cat "{st}/get" ;;\n'
            f'  "agent list") cat "{st}/agents" ;;\n'
            "  *) exit 9 ;;\n"
            "esac\n")
        (self.bin / "gh").write_text(
            "#!/bin/bash\n"
            f'[ "$1 $2" = "pr view" ] && cat "{st}/prview" || exit 9\n')
        for f in ("herdr", "gh"):
            (self.bin / f).chmod(0o755)

    @staticmethod
    def worker(cwd, pane="w1:p9", tab="w1:t9", session=SESSION):
        return {"agent": "claude", "agent_status": "working", "cwd": str(cwd),
                "pane_id": pane, "tab_id": tab, "agent_session": {"value": session}}

    def set_prview(self, state, head, oid, merge="ab" * 20):
        (self.state / "prview").write_text(f"7|{state}|{head}|{oid}|{merge}\n")

    def set_get(self, agent):
        (self.state / "get").write_text(json.dumps({"result": agent}))

    def set_agents(self, agents):
        (self.state / "agents").write_text(json.dumps({"result": {"agents": agents}}))

    def run(self, *args, cwd=None, herdr=True):
        env = {"PATH": f"{self.bin}:{SHADOW}", "HOME": str(self.d), "TMPDIR": str(self.d)}
        if herdr:
            env.update(HERDR_ENV="1", HERDR_PANE_ID="w1:p9")
        r = subprocess.run(["bash", str(SCRIPT), *args], cwd=str(cwd or self.main),
                           env=env, capture_output=True, text=True, timeout=60)
        return r, parse(r.stdout)

    def close(self):
        self.tmp.cleanup()


# --- begin --------------------------------------------------------------------- #
fx = Fixture()
r, o = fx.run("begin", "--pr", "7")
check("begin: refuses the main checkout", r.returncode == 0
      and o.get("recorded") == "no" and o.get("reason") == "not-a-lane")
r, o = fx.run("begin", "--pr", "7x", cwd=fx.wt)
check("begin: a non-numeric PR is a usage error", r.returncode == 2)

r, o = fx.run("begin", "--pr", "7", cwd=fx.wt)
check("begin: recorded in the lane", r.returncode == 0 and o.get("recorded") == "yes")
check("begin: marker lives in the git common dir, not the worktree",
      o.get("marker") == str(fx.marker) and fx.marker.is_file())
check("begin: marker is private", stat.S_IMODE(fx.marker.stat().st_mode) == 0o600)
body = fx.marker.read_text()
check("begin: records the worker by pane + session",
      "worker_pane=w1:p9\n" in body and f"worker_session={SESSION}\n" in body)
check("begin: copies the kicker's session", f"kicker_session={KICKER}\n" in body
      and "kicker_record=present\n" in body and "kicker_source=kickoff\n" in body)
check("begin: phase landing, PR, branch, realpath worktree",
      "phase=landing\n" in body and "pr=7\n" in body and "branch=task/foo\n" in body
      and f"worktree={fx.wt}\n" in body)

# --- status ------------------------------------------------------------------- #
r, o = fx.run("status", "foo")
check("status: live worker", o.get("landing") == "live" and o.get("phase") == "landing"
      and o.get("worker_pane") == "w1:p9" and o.get("worker_tab") == "w1:t9"
      and o.get("worker_cwd") == "match" and o.get("worktree_state") == "present"
      and o.get("kicker_session") == KICKER and o.get("pr") == "7")
r, o = fx.run("status", "foo", cwd=fx.wt)
check("status: same answer from inside the lane", o.get("landing") == "live")
fx.set_agents([fx.worker(fx.wt / "src")])
_, o = fx.run("status", "foo")
check("status: a lane subdirectory still matches", o.get("worker_cwd") == "match")
fx.set_agents([fx.worker(fx.main)])
_, o = fx.run("status", "foo")
check("status: a worker pane at the repo root is a mismatch",
      o.get("landing") == "live" and o.get("worker_cwd") == "mismatch")
fx.set_agents([fx.worker(fx.wt, session="00000000-0000-0000-0000-000000000000")])
_, o = fx.run("status", "foo")
check("status: the pane hosts another session → stale",
      o.get("landing") == "stale" and "worker_tab" not in o)
fx.set_agents([fx.worker(fx.wt, pane="w1:p2")])
_, o = fx.run("status", "foo")
check("status: no such pane in a populated list → stale", o.get("landing") == "stale")
fx.set_agents([])
_, o = fx.run("status", "foo")
check("status: an empty agent list is unverified, never stale",
      o.get("landing") == "unverified")
_, o = fx.run("status", "foo", herdr=False)
check("status: outside herdr → unverified", o.get("landing") == "unverified")
_, o = fx.run("status", "bar")
check("status: no marker → none", o.get("landing") == "none" and "phase" not in o)

# --- the worktree is removed under the marker ----------------------------------- #
fx.set_agents([fx.worker(fx.wt)])
git(fx.main, "worktree", "remove", "--force", str(fx.wt))
_, o = fx.run("status", "foo")
check("gone: the marker survives the worktree removal", fx.marker.is_file())
check("gone: the tab is still found by pane + session",
      o.get("landing") == "live" and o.get("worker_tab") == "w1:t9"
      and o.get("worktree_state") == "gone")
check("gone: a deleted cwd that was the lane still matches", o.get("worker_cwd") == "match")
fx.set_agents([fx.worker("")])
_, o = fx.run("status", "foo")
check("gone: an unreadable cwd is unknown, not match", o.get("worker_cwd") == "unknown")

r, o = fx.run("gone-check", "foo", "--pr", "7", "--repo", str(fx.main))
check("gone-check: worktree gone, PR merged from this branch",
      r.returncode == 0 and o.get("worktree_state") == "gone"
      and o.get("pr_state") == "MERGED" and o.get("pr_number") == "7"
      and o.get("head_branch_match") == "yes" and o.get("merge_sha") == "ab" * 20)
fx.set_prview("MERGED", "task/other", "cd" * 20)
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: another head branch → no", o.get("head_branch_match") == "no")
(fx.state / "prview").write_text("garbage\n")
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: unreadable PR → unknown", o.get("pr_state") == "unknown"
      and o.get("head_branch_match") == "unknown")

# --- phase / end ----------------------------------------------------------------- #
r, o = fx.run("phase", "foo", "handoff", "--repo", str(fx.main))
check("phase: handoff", o.get("updated") == "yes" and "phase=handoff\n" in fx.marker.read_text())
r, o = fx.run("phase", "bar", "handoff")
check("phase: no marker → updated=no", o.get("updated") == "no")
r, o = fx.run("phase", "foo", "landing")
check("phase: only handoff is accepted", r.returncode == 2)
r, o = fx.run("end", "foo")
check("end: removes the marker", o.get("removed") == "yes" and not fx.marker.exists())
r, o = fx.run("end", "foo")
check("end: a missing marker is fine", r.returncode == 0 and o.get("removed") == "no")
fx.close()

# --- what "gone" means, and the branch that may outlive the worktree ---------------- #
fx = Fixture()
(fx.wt / "work.txt").write_text("work\n")
git(fx.wt, "add", "work.txt")
git(fx.wt, "commit", "-q", "-m", "work")
head = git(fx.main, "rev-parse", "task/foo")
fx.set_prview("MERGED", "task/foo", head)
fx.run("begin", "--pr", "7", cwd=fx.wt)
_, o = fx.run("lane", "foo")
check("lane: a listed worktree is present", o.get("worktree_state") == "present")
git(fx.main, "worktree", "remove", "--force", str(fx.wt))
fx.wt.mkdir(parents=True)
(fx.wt / "stray").write_text("x\n")
_, o = fx.run("lane", "foo")
check("lane: a leftover directory git no longer lists is gone", o.get("worktree_state") == "gone"
      and o.get("leftover") == "yes" and o.get("worktree") == str(fx.wt))
_, o = fx.run("status", "foo")
check("status: same predicate for worktree_state", o.get("worktree_state") == "gone")
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: a leftover directory does not read as present",
      o.get("worktree_state") == "gone" and o.get("pr_state") == "MERGED")
check("gone-check: a surviving branch at the PR head", o.get("branch") == "task/foo"
      and o.get("branch_tip") == "same")
check("gone-check: merged, same PR, nothing only the branch holds → handoff",
      o.get("verdict") == "handoff" and "stop" not in o)
git(fx.main, "branch", "-f", "task/foo", "main")
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: a branch the PR head contains is behind", o.get("branch_tip") == "behind")
git(fx.main, "branch", "-f", "task/foo", head)
git(fx.main, "checkout", "-q", "task/foo")
(fx.main / "late.txt").write_text("late\n")
git(fx.main, "add", "late.txt")
git(fx.main, "commit", "-q", "-m", "late")
git(fx.main, "checkout", "-q", "main")
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: a commit the PR lacks is ahead", o.get("branch_tip") == "ahead"
      and o.get("verdict") == "stop" and o.get("stop") == "branch-ahead")
fx.set_prview("MERGED", "task/foo", "ef" * 20)
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: an unknown PR head is unreadable, never same", o.get("branch_tip") == "unreadable"
      and o.get("stop") == "branch-unreadable")
git(fx.main, "branch", "-D", "task/foo")
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: no surviving branch → none", o.get("branch_tip") == "none"
      and o.get("verdict") == "handoff")
fx.set_prview("OPEN", "task/foo", head)
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: an open PR stops as not-merged", o.get("stop") == "not-merged")
_, o = fx.run("gone-check", "foo", "--pr", "8")
check("gone-check: another PR number stops as not-merged", o.get("stop") == "not-merged")
fx.close()

fx = Fixture()
git(fx.main, "worktree", "add", "-q", "-b", "task/bar", str(fx.main / ".claude" / "worktrees" / "bar"))
subprocess.run(["rm", "-rf", str(fx.wt)], check=True)   # deleted, never pruned
_, o = fx.run("lane", "foo")
check("lane: a deleted worktree git still lists as prunable is gone",
      o.get("worktree_state") == "gone")
_, o = fx.run("lane", "bar")
check("lane: another lane stays present", o.get("worktree_state") == "present")
fx.close()

# --- an /adopt-ed lane: branch name and directory name differ ---------------------- #
fx = Fixture()
dm = fx.main / ".claude" / "worktrees" / "dark-mode"
git(fx.main, "worktree", "add", "-q", "-b", "feature/darkMode", str(dm))
_, o = fx.run("begin", "--pr", "7", cwd=dm)
mk = fx.main / ".git" / "work-system" / "landing" / "darkMode"
check("adopted: begin records task from the branch, worktree from the dir",
      o.get("recorded") == "yes" and mk.is_file() and f"worktree={dm}\n" in mk.read_text())
_, o = fx.run("lane", "darkMode")
check("adopted: the lane is the marker's directory, present",
      o.get("worktree") == str(dm) and o.get("worktree_state") == "present")
git(fx.main, "worktree", "remove", "--force", str(dm))
fx.set_prview("MERGED", "feature/darkMode", git(fx.main, "rev-parse", "feature/darkMode"))
_, o = fx.run("gone-check", "darkMode", "--pr", "7")
check("adopted: a branch not resolvable by name stops the hand-over",
      o.get("worktree_state") == "gone" and o.get("head_branch_match") == "yes"
      and o.get("stop") == "not-by-name")
fx.close()

# --- worktree still there -------------------------------------------------------- #
fx = Fixture()
fx.run("begin", "--pr", "7", cwd=fx.wt, herdr=False)
check("begin outside herdr: recorded without a worker",
      fx.marker.is_file() and "worker_pane" not in fx.marker.read_text())
_, o = fx.run("status", "foo")
check("status: a marker without a worker is unverified", o.get("landing") == "unverified")
_, o = fx.run("gone-check", "foo", "--pr", "7")
check("gone-check: a live worktree is present, nothing more",
      o.get("worktree_state") == "present" and "pr_state" not in o)

# --- an untrustworthy marker ------------------------------------------------------ #
good = fx.marker.read_text()
for name, text in (("duplicate key", good + "pr=8\n"),
                   ("unknown key", good + "cmd=rm -rf /\n"),
                   ("flag-like pane", good + f"worker_pane=--all\nworker_session={SESSION}\n"),
                   ("pane without session", good + "worker_pane=w1:p9\n"),
                   ("another task", good.replace("task=foo", "task=bar")),
                   ("missing field", good.replace("phase=landing\n", "")),
                   ("a worktree outside .claude/worktrees", good.replace(
                       f"worktree={fx.wt}\n", f"worktree={fx.d / 'elsewhere-lane'}\n")),
                   ("a nested path under a lane", good.replace(
                       f"worktree={fx.wt}\n", f"worktree={fx.wt / 'sub'}\n")),
                   ("the main checkout as worktree", good.replace(
                       f"worktree={fx.wt}\n", f"worktree={fx.main}\n"))):
    fx.marker.write_text(text)
    _, o = fx.run("status", "foo")
    check(f"invalid: {name}", o.get("landing") == "invalid" and "worker_tab" not in o)
# A marker naming another, live lane parses — and reads as present, which is
# what keeps /close from ever taking that lane's tab.
bar = fx.main / ".claude" / "worktrees" / "bar"
git(fx.main, "worktree", "add", "-q", "-b", "task/bar", str(bar))
fx.marker.write_text(good.replace(f"worktree={fx.wt}\n", f"worktree={bar}\n"))
_, o = fx.run("status", "foo")
check("a marker naming another live lane reads as present, never gone",
      o.get("landing") != "invalid" and o.get("worktree_state") == "present")
fx.marker.unlink()
(fx.d / "elsewhere").write_text(good)
fx.marker.symlink_to(fx.d / "elsewhere")
_, o = fx.run("status", "foo")
check("invalid: a symlinked marker is not read", o.get("landing") == "invalid")
r, o = fx.run("begin", "--pr", "7", cwd=fx.wt)
check("begin: never writes through a symlinked marker",
      o.get("recorded") == "no" and (fx.d / "elsewhere").read_text() == good)
fx.marker.unlink()

# --- a kicker record that is not one record --------------------------------------- #
(fx.wt / ".ws-kicker").write_text(f"agent_session={KICKER}\nagent_session={KICKER}\n")
fx.run("begin", "--pr", "7", cwd=fx.wt)
check("begin: a duplicated agent_session is not copied",
      "kicker_session" not in fx.marker.read_text())
check("begin: a broken record still counts as present",
      "kicker_record=present\n" in fx.marker.read_text())
fx.marker.unlink()
for src in ("source=foo\n", "source=backfill\nsource=backfill\n"):
    (fx.wt / ".ws-kicker").write_text(f"agent_session={KICKER}\n{src}")
    fx.run("begin", "--pr", "7", cwd=fx.wt)
    check(f"begin: an unvouched {src.strip()!r} copies no kicker",
          "kicker_session" not in fx.marker.read_text()
          and "kicker_record=present\n" in fx.marker.read_text())
    fx.marker.unlink()
(fx.wt / ".ws-kicker").write_text(f"agent_session={KICKER}\nsource=backfill\n")
fx.run("begin", "--pr", "7", cwd=fx.wt)
check("begin: a backfill record is copied as such",
      "kicker_source=backfill\n" in fx.marker.read_text())
fx.marker.unlink()
saved = (fx.wt / ".ws-kicker").read_text()
(fx.wt / ".ws-kicker").unlink()
fx.run("begin", "--pr", "7", cwd=fx.wt)
r, o = fx.run("status", "foo")
check("begin: no record → kicker_record=absent", o.get("kicker_record") == "absent")
fx.marker.unlink()
(fx.wt / ".ws-kicker").write_text(saved)

# --- argument guards -------------------------------------------------------------- #
canary = fx.d / "pwned"
hostile = f"x$(touch {canary})"
for args in (["status", hostile], ["end", hostile], ["phase", hostile, "handoff"],
             ["gone-check", hostile, "--pr", "7"]):
    r, _ = fx.run(*args)
    check(f"guard: hostile task name for {args[0]} → usage", r.returncode == 2)
check("guard: a hostile task name never executes", not canary.exists())
for bad in ("-rf", ".", ".."):
    r, _ = fx.run("status", bad)
    check(f"guard: task {bad!r} → usage", r.returncode == 2)
r, _ = fx.run("status", "foo", "--repo", str(fx.wt))
check("guard: --repo must be the main checkout", r.returncode == 1)
r, _ = fx.run("status", "foo", "--repo")
check("guard: --repo without a value → usage", r.returncode == 2)
r, _ = fx.run("bogus")
check("guard: unknown subcommand → usage", r.returncode == 2)
fx.close()

# --- shadow PATH: a dangling link listed by both sources ----------------------- #
# The runner's /bin is /usr/bin (usrmerge) and holds a dangling link; the second
# pass must skip it instead of failing with FileExistsError.

with tempfile.TemporaryDirectory() as _td:
    _td = Path(_td)
    _a, _b, _dest = _td / "usr-bin", _td / "bin", _td / "shadow"
    for _d in (_a, _b, _dest):
        _d.mkdir()
    for _d in (_a, _b):
        (_d / "dangling").symlink_to(_td / "missing")
    (_a / "tool").symlink_to(_td / "missing")
    (_b / "tool").write_text("")
    try:
        build_shadow(_dest, (_a, _b))
        _ok = (not os.path.lexists(_dest / "dangling")
               and os.readlink(_dest / "tool") == str(_b / "tool"))
    except FileExistsError:
        _ok = False
    check("shadow build skips a dangling source entry", _ok)

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
_SHADOW.cleanup()
print("landing.sh: all tests passed")
