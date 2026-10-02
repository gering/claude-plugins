#!/usr/bin/env python3
"""Tests for close-request.sh — the Manager's auto-accept decision + sweep.

Each case builds a REAL git repo with a task worktree under .claude/worktrees/,
then drives the real script with a fake `gh` and a fake `herdr` in front of a
shadow copy of /usr/bin + /bin with every host gh/herdr filtered out (CI runners
ship gh in /usr/bin, so a plain /usr/bin:/bin PATH would leak the real one). HOME is
a throwaway dir so the sweep's insights lookup never touches the user's store.

Covers: the `auto` path (one and zero agents), every `reject` reason, every
`ask` reason (alone and combined), a hostile task name that must never execute,
and the sweep's material file (pane read, 0600, unverified/absent liveness).
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "close-request.sh"

FAILS = []

# One shadow of /usr/bin + /bin per test run, minus any host gh/herdr — built
# once, not per fixture (it is a few thousand symlinks on a typical host).
_SHADOW = tempfile.TemporaryDirectory()
SHADOW = Path(os.path.realpath(_SHADOW.name))
for _src in ("/usr/bin", "/bin"):
    for _name in os.listdir(_src):
        if _name in ("gh", "herdr") or (SHADOW / _name).exists():
            continue
        (SHADOW / _name).symlink_to(os.path.join(_src, _name))


def check(name, cond):
    if not cond:
        FAILS.append(name)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class Fixture:
    """main repo + task worktree `foo` (+ a second lane `bar`), fake gh/herdr."""

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
        self.add_lane("foo", self.wt)
        (self.wt / "f.txt").write_text("work\n")
        git(self.wt, "add", "f.txt")
        git(self.wt, "commit", "-q", "-m", "work on foo")
        (self.wt / "TASK.md").write_text("task\n")
        (self.wt / "MANDATE.md").write_text("mandate\n")
        self.bar = self.main / ".claude" / "worktrees" / "bar"
        self.add_lane("bar", self.bar)
        self.head = git(self.main, "rev-parse", "refs/heads/task/foo")

        self.bin = d / "bin"
        self.bin.mkdir()
        self.home = d / "home"
        self.home.mkdir()
        self.state = d / "state"
        self.state.mkdir()
        self.set_pr("MERGED")
        self.set_head(self.head)
        self.set_agents([self.agent(self.wt)])
        (self.state / "pane").write_text("deploy the docs site after merge\n")
        self.write_fakes()

    def add_lane(self, name, path):
        git(self.main, "worktree", "add", "-q", "-b", f"task/{name}", str(path))

    @staticmethod
    def agent(cwd, pane="w1:p1"):
        return {"agent": "claude", "agent_status": "idle", "cwd": str(cwd),
                "pane_id": pane, "tab_id": "w1:t1"}

    def set_pr(self, state):
        (self.state / "prlist").write_text(f"7|{state}|https://example/pr/7\n")

    def set_head(self, sha, merge="ab" * 20):
        (self.state / "prview").write_text(f"{sha}|{merge}\n")

    def set_agents(self, agents):
        payload = {"result": {"agents": agents}}
        (self.state / "agents").write_text(json.dumps(payload))

    def write_fakes(self, gh=True):
        st = self.state
        g = self.bin / "gh"
        if gh:
            g.write_text(
                "#!/bin/bash\n"
                f'case "$1 $2" in\n'
                f'  "pr list") cat "{st}/prlist" ;;\n'
                f'  "pr view") cat "{st}/prview" ;;\n'
                "  *) exit 9 ;;\n"
                "esac\n")
            g.chmod(0o755)
        elif g.exists():
            g.unlink()
        h = self.bin / "herdr"
        h.write_text(
            "#!/bin/bash\n"
            f'case "$1 $2" in\n'
            f'  "agent list") cat "{st}/agents" ;;\n'
            f'  "pane read") echo "$@" >> "{st}/pane-argv"; cat "{st}/pane" ;;\n'
            "  *) exit 9 ;;\n"
            "esac\n")
        h.chmod(0o755)

    def env(self, herdr=True):
        e = {"PATH": f"{self.bin}:{SHADOW}", "HOME": str(self.home),
             "TMPDIR": str(self.d)}
        if herdr:
            e["HERDR_ENV"] = "1"
        return e

    def message(self, task="foo", worktree=None, repo=None, raw=None):
        body = raw if raw is not None else (
            "work-system close-request\n"
            f"task={task}\n"
            f"worktree={worktree if worktree is not None else self.wt}\n"
            f"repo={repo if repo is not None else self.main}\n")
        f = self.d / "msg"
        f.write_text(body)
        return f

    def evaluate(self, herdr=True, **kw):
        r = subprocess.run(["bash", str(SCRIPT), "evaluate", str(self.message(**kw))],
                           cwd=self.main, env=self.env(herdr), capture_output=True,
                           text=True, timeout=60)
        return r, parse(r.stdout)

    def fake_insights(self, reports):
        """Run sweep from a copy of the scripts dir whose insights bridge is a fake:
        `reported` lists `reports` [(id, recorded_at, pr|None)], `read` returns
        each one's JSON. Tests close-request.sh's own selection + identity check."""
        sd = self.d / "scripts"
        sd.mkdir(exist_ok=True)
        for f in HERE.glob("*.sh"):
            (sd / f.name).write_text(f.read_text())
        st = self.state
        helper = st / "insights.py"
        helper.write_text(
            "import json, sys\n"
            f"db = json.load(open({str(st / 'reports.json')!r}))\n"
            "print(json.dumps(db[sys.argv[2]]))\n")
        db = {rid: {"summary": f"notes of {rid}",
                    "work": {"pr": {"value": pr}} if pr is not None else {}}
              for rid, _, pr in reports}
        (st / "reports.json").write_text(json.dumps(db))
        listing = "".join(f"report={rid} recorded_at={at} trigger=handoff\n"
                          for rid, at, _ in reports)
        (st / "listing").write_text(listing)
        (sd / "insights-handoff.sh").write_text(
            "#!/bin/bash\n"
            'case "$1" in\n'
            f'  probe) echo "helper={helper}" ;;\n'
            f'  reported) cat "{st}/listing" ;;\n'
            "  *) exit 9 ;;\n"
            "esac\n")
        return sd / SCRIPT.name

    def sweep(self, task="foo", herdr=True, script=SCRIPT, extra=()):
        r = subprocess.run(["bash", str(script), "sweep", task, *extra], cwd=self.main,
                           env=self.env(herdr), capture_output=True, text=True,
                           timeout=60)
        return r, parse(r.stdout)

    def close(self):
        self.tmp.cleanup()


def parse(out):
    d = {"reason": []}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k == "reason":
            d["reason"].append(v.split(" ", 1)[0])
        else:
            d[k] = v
    return d


# --- auto ------------------------------------------------------------------- #
fx = Fixture()
r, o = fx.evaluate()
check("auto: exit 0", r.returncode == 0)
check("auto: decision", o.get("decision") == "auto")
check("auto: no reasons", o["reason"] == [])
check("auto: task echoed", o.get("task") == "foo")
check("auto: worktree is the realpath lane", o.get("worktree") == str(fx.wt))
check("auto: pr number", o.get("pr") == "7")
check("auto: merge sha", bool(o.get("merge_sha")))
check("auto: one agent (the requester)", o.get("lane_agents") == "1")

fx.set_agents([fx.agent(fx.bar)])            # requester already gone
r, o = fx.evaluate()
check("auto: zero agents in the lane is fine", o.get("decision") == "auto"
      and o.get("lane_agents") == "0")
fx.close()

# --- reject ----------------------------------------------------------------- #
fx = Fixture()
_, o = fx.evaluate(raw="hello\ntask=foo\n")
check("reject: missing marker", o.get("decision") == "reject" and o["reason"] == ["malformed"])
_, o = fx.evaluate(raw=f"work-system close-request\ntask=foo\ntask=bar\n"
                       f"worktree={fx.wt}\nrepo={fx.main}\n")
check("reject: duplicate key", o.get("decision") == "reject" and o["reason"] == ["malformed"])
_, o = fx.evaluate(raw=f"work-system close-request\ntask=foo\nrepo={fx.main}\n")
check("reject: missing field", o.get("decision") == "reject" and o["reason"] == ["malformed"])

canary = fx.d / "pwned"
_, o = fx.evaluate(task=f"foo$(touch {canary})")
check("reject: hostile task name", o.get("decision") == "reject"
      and o["reason"] == ["invalid-task"] and "task" not in o)
check("reject: hostile task name never executes", not canary.exists())

_, o = fx.evaluate(task="_under")
check("reject: underscore-led name passes validation (then mismatches the lane)",
      o["reason"] == ["lane-mismatch"])
for bad in ("-rf", ".", "..", ".hidden"):
    _, o = fx.evaluate(task=bad)
    check(f"reject: task {bad!r} is not a plain name", o["reason"] == ["invalid-task"])

_, o = fx.evaluate(repo=str(fx.d / "elsewhere"))
check("reject: foreign repo", o.get("decision") == "reject" and o["reason"] == ["repo-mismatch"])
_, o = fx.evaluate(repo="relative/path")
check("reject: relative repo", o["reason"] == ["repo-mismatch"])

_, o = fx.evaluate(worktree=str(fx.main))
check("reject: main checkout is not a lane", o["reason"] == ["not-a-lane"])
_, o = fx.evaluate(worktree=str(fx.d / "nowhere"))
check("reject: nonexistent worktree", o["reason"] == ["not-a-lane"])

_, o = fx.evaluate(worktree=str(fx.bar))
check("reject: another task's lane", o.get("decision") == "reject"
      and o["reason"] == ["lane-mismatch"] and "worktree" not in o)
fx.close()

# --- ask: one reason each ---------------------------------------------------- #
fx = Fixture()
fx.set_pr("OPEN")
_, o = fx.evaluate()
check("ask: open PR → not-merged", o.get("decision") == "ask" and "not-merged" in o["reason"])
fx.set_pr("MERGED")

(fx.wt / "scratch.txt").write_text("unsaved\n")
_, o = fx.evaluate()
check("ask: untracked file → dirty", o.get("decision") == "ask"
      and o["reason"] == ["dirty-worktree"])
(fx.wt / "scratch.txt").unlink()
(fx.wt / "f.txt").write_text("edited\n")
_, o = fx.evaluate()
check("ask: modified file → dirty", o["reason"] == ["dirty-worktree"])
git(fx.wt, "checkout", "-q", "--", "f.txt")

git(fx.main, "config", "status.showUntrackedFiles", "no")
(fx.wt / "scratch.txt").write_text("unsaved\n")
_, o = fx.evaluate()
check("ask: untracked file found despite status.showUntrackedFiles=no",
      o["reason"] == ["dirty-worktree"])
(fx.wt / "scratch.txt").unlink()
git(fx.main, "config", "--unset", "status.showUntrackedFiles")

(fx.main / ".git" / "info").mkdir(exist_ok=True)
(fx.main / ".git" / "info" / "exclude").write_text("TASK.md\nMANDATE.md\n")
_, o = fx.evaluate()
check("auto: gitignored TASK.md/MANDATE.md are the lane files, not a reason",
      o.get("decision") == "auto")
(fx.main / ".git" / "info" / "exclude").write_text("TASK.md\nMANDATE.md\nsecret.env\n")
(fx.wt / "secret.env").write_text("TOKEN=x\n")
_, o = fx.evaluate()
check("ask: gitignored file would be deleted", o.get("decision") == "ask"
      and o["reason"] == ["ignored-files"])
(fx.wt / "secret.env").unlink()
# The lane-file allowlist is exact: a lookalike name is real work, not TASK.md.
(fx.main / ".git" / "info" / "exclude").write_text("TASK.md\nMANDATE.md\nMANDATE_md\n")
(fx.wt / "MANDATE_md").write_text("notes\n")
(fx.wt / "TASKxmd").write_text("notes\n")
_, o = fx.evaluate()
check("ask: lane-file lookalikes are not the lane files",
      o.get("decision") == "ask" and set(o["reason"]) == {"ignored-files", "dirty-worktree"})
(fx.wt / "MANDATE_md").unlink()
(fx.wt / "TASKxmd").unlink()

(fx.wt / "late.txt").write_text("after merge\n")
git(fx.wt, "add", "late.txt")
git(fx.wt, "commit", "-q", "-m", "post-merge")
_, o = fx.evaluate()
check("ask: commit after merge", o.get("decision") == "ask"
      and o["reason"] == ["post-merge-commits"])
fx.set_head(git(fx.main, "rev-parse", "refs/heads/task/foo"))

_, o = fx.evaluate()
check("ask→auto again once tip == head", o.get("decision") == "auto")

fx.write_fakes(gh=False)
_, o = fx.evaluate()
check("ask: gh unavailable", o.get("decision") == "ask" and "gh-unavailable" in o["reason"])
fx.write_fakes(gh=True)

(fx.state / "prview").write_text("\n")
_, o = fx.evaluate()
check("ask: PR head unreadable", o["reason"] == ["pr-head-unreadable"])
fx.set_head(git(fx.main, "rev-parse", "refs/heads/task/foo"))

fx.set_agents([fx.agent(fx.wt, "w1:p1"), fx.agent(fx.wt, "w1:p2")])
_, o = fx.evaluate()
check("ask: two agents", o.get("decision") == "ask"
      and o["reason"] == ["multiple-agents"] and o.get("lane_agents") == "2")

fx.set_agents([fx.agent(fx.wt, "w1:p1"), fx.agent(fx.wt / "sub", "w1:p2")])
_, o = fx.evaluate()
check("ask: an agent in a lane subdirectory counts", o["reason"] == ["multiple-agents"])

fx.set_agents([fx.agent(fx.wt), {"agent": "claude", "cwd": None, "pane_id": "w1:p3"}])
_, o = fx.evaluate()
check("ask: agent without cwd → unverified", o["reason"] == ["liveness-unverified"])

fx.set_agents([])
_, o = fx.evaluate()
check("ask: empty agent list → unverified", o["reason"] == ["liveness-unverified"]
      and o.get("lane_agents") == "unverified")
(fx.state / "agents").write_text('{"result":{"agents":[null]}}')
_, o = fx.evaluate()
check("ask: malformed agent → unverified", o["reason"] == ["liveness-unverified"])
fx.set_agents([fx.agent(fx.wt)])

_, o = fx.evaluate(herdr=False)
check("ask: outside herdr → unverified", o["reason"] == ["liveness-unverified"])

# --- remote branch: deleted by /close step 9, so it must hold nothing new --- #
origin = fx.d / "origin.git"
subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
git(fx.main, "remote", "add", "origin", str(origin))
_, o = fx.evaluate()
check("auto: remote branch already gone", o.get("decision") == "auto")
git(fx.main, "push", "-q", "origin", "task/foo")
_, o = fx.evaluate()
check("auto: remote branch == merged head", o.get("decision") == "auto")
# A ref that only TAIL-matches the ls-remote pattern must not supply the sha.
subprocess.run(["git", "--git-dir", str(origin), "update-ref",
                "refs/backup/refs/heads/task/foo", git(fx.main, "rev-parse", "main")], check=True)
_, o = fx.evaluate()
check("auto: a tail-matching foreign ref is ignored", o.get("decision") == "auto")
other = fx.d / "other"
subprocess.run(["git", "clone", "-q", "-b", "task/foo", str(origin), str(other)], check=True)
git(other, "config", "user.email", "t@example.com")
git(other, "config", "user.name", "t")
(other / "remote-late.txt").write_text("pushed after merge\n")
git(other, "add", "remote-late.txt")
git(other, "commit", "-q", "-m", "remote post-merge")
git(other, "push", "-q", "origin", "task/foo")
_, o = fx.evaluate()
check("ask: remote branch has a commit after the merge", o["reason"] == ["remote-ahead"])
git(fx.main, "remote", "set-url", "origin", str(fx.d / "missing.git"))
_, o = fx.evaluate()
check("ask: remote unreadable", o["reason"] == ["remote-unverified"])
git(fx.main, "remote", "remove", "origin")

# --- ask: every failed condition is named, not just the first --------------- #
fx.set_pr("CLOSED")
(fx.wt / "scratch.txt").write_text("unsaved\n")
fx.set_agents([fx.agent(fx.wt, "w1:p1"), fx.agent(fx.wt, "w1:p2")])
_, o = fx.evaluate()
check("ask: combined reasons all listed",
      {"not-merged", "dirty-worktree", "multiple-agents"} <= set(o["reason"]))
fx.close()

# --- sweep ------------------------------------------------------------------ #
fx = Fixture()
r, o = fx.sweep()
mat = Path(o.get("material", "/nonexistent"))
check("sweep: exit 0", r.returncode == 0)
check("sweep: no insights store → report none", o.get("report") == "none")
check("sweep: pane read", o.get("pane") == "read")
check("sweep: material exists", mat.is_file())
check("sweep: material is private", mat.is_file() and stat.S_IMODE(mat.stat().st_mode) == 0o600)
check("sweep: material carries the pane text",
      mat.is_file() and "deploy the docs site" in mat.read_text())
argv = (fx.state / "pane-argv").read_text() if (fx.state / "pane-argv").exists() else ""
check("sweep: reads the lane agent's pane, visible + bounded",
      "w1:p1" in argv and "--source visible" in argv and "--lines" in argv)
mat.unlink(missing_ok=True)

fx.set_agents([fx.agent(fx.wt, "w1:p1"), fx.agent(fx.wt / "sub", "w1:p2"),
               fx.agent(fx.wt, "--source")])
r, o = fx.sweep()
argv = (fx.state / "pane-argv").read_text() if (fx.state / "pane-argv").exists() else ""
check("sweep: every lane pane is read", o.get("panes") == "2/3")
check("sweep: a flag-like pane id is never passed to herdr",
      not any(l.startswith("pane read --") for l in argv.splitlines()))
Path(o.get("material", "/x")).unlink(missing_ok=True)

fx.set_agents([])
_, o = fx.sweep()
check("sweep: empty agent list → pane unverified", o.get("pane") == "unverified")
Path(o.get("material", "/x")).unlink(missing_ok=True)

fx.set_agents([fx.agent(fx.bar)])
_, o = fx.sweep()
check("sweep: no agent in the lane → pane none", o.get("pane") == "none")
Path(o.get("material", "/x")).unlink(missing_ok=True)

_, o = fx.sweep(herdr=False)
check("sweep: outside herdr → pane absent", o.get("pane") == "absent")
Path(o.get("material", "/x")).unlink(missing_ok=True)

r, o = fx.sweep(task="foo;bar")
check("sweep: invalid task → usage", r.returncode == 2 and "material" not in o)
for bad in (["--pr"], ["--pr", "7x"], ["--pr", "7", "x"], ["--bogus"]):
    r, o = fx.sweep(extra=bad)
    check(f"sweep: bad args {bad} → usage", r.returncode == 2 and "material" not in o)

# Report selection + identity, against a fake insights bridge.
fx.set_agents([fx.agent(fx.bar)])
script = fx.fake_insights([("ins-old", "2026-01-01T00:00:00Z", 3),
                           ("ins-new", "2026-09-01T00:00:00Z", 7),
                           ("ins-future", "2999-01-01T00:00:00Z", 7)])
_, o = fx.sweep(script=script)
mat = Path(o.get("material", "/x"))
check("sweep: newest report wins, a future-dated one skipped", o.get("report") == "ins-new"
      and o.get("report_recorded_at") == "2026-09-01T00:00:00Z")
check("sweep: report_pr from work.pr", o.get("report_pr") == "7")
check("sweep: no --pr → no report_match", "report_match" not in o)
check("sweep: report body in material", mat.is_file() and "notes of ins-new" in mat.read_text())
mat.unlink(missing_ok=True)
_, o = fx.sweep(script=script, extra=["--pr", "7"])
mat = Path(o.get("material", "/x"))
check("sweep: --pr equal → match yes", o.get("report_match") == "yes"
      and mat.is_file() and "notes of ins-new" in mat.read_text())
mat.unlink(missing_ok=True)
_, o = fx.sweep(script=script, extra=["--pr", "9"])
mat = Path(o.get("material", "/x"))
check("sweep: --pr different → match no, body withheld", o.get("report_match") == "no"
      and mat.is_file() and "notes of" not in mat.read_text())
mat.unlink(missing_ok=True)
script = fx.fake_insights([("ins-url", "2026-09-01T00:00:00Z",
                            "https://github.com/o/r/pull/9")])
_, o = fx.sweep(script=script, extra=["--pr", "7"])
mat = Path(o.get("material", "/x"))
check("sweep: a URL-shaped work.pr is a PR number too",
      o.get("report_pr") == "9" and o.get("report_match") == "no"
      and mat.is_file() and "notes of" not in mat.read_text())
mat.unlink(missing_ok=True)
script = fx.fake_insights([("ins-nopr", "2026-09-01T00:00:00Z", None)])
_, o = fx.sweep(script=script, extra=["--pr", "7"])
mat = Path(o.get("material", "/x"))
check("sweep: report without PR → match unknown, body kept",
      o.get("report_match") == "unknown" and "report_pr" not in o
      and mat.is_file() and "notes of ins-nopr" in mat.read_text())
mat.unlink(missing_ok=True)
fx.close()

r = subprocess.run(["bash", str(SCRIPT), "bogus"], capture_output=True, text=True)
check("usage: unknown subcommand exits 2", r.returncode == 2)

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
_SHADOW.cleanup()
print("close-request.sh: all tests passed")
