#!/usr/bin/env python3
"""Tests for land-gate.py — the one hard-vs-transient classification of /land.

A REAL bare origin, a main checkout and a task worktree with an upstream, plus a
fake `gh` (pr view --json) reading its answer from a state file, in front of a
shadow copy of /usr/bin + /bin with every host gh/herdr filtered out.

Covers every classification the skill relays (ready, the transient waits incl.
the PR #100 `pr-lagging` case, the hard stops), and the armed state: arm/wait
across calls, head binding (head-moved, pr-head-moved), the deadline, the
arm_id and pane checks, and `landing.sh end` clearing it.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
GATE = HERE / "land-gate.py"
LANDING = HERE / "landing.sh"
FAILS = []

_SHADOW = tempfile.TemporaryDirectory()
SHADOW = Path(os.path.realpath(_SHADOW.name))
for src in ("/usr/bin", "/bin"):
    for name in os.listdir(src):
        target = os.path.join(src, name)
        if name in ("gh", "herdr") or not os.path.exists(target) or os.path.lexists(SHADOW / name):
            continue
        (SHADOW / name).symlink_to(target)


def check(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".rstrip())


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def parse(out):
    d = {}
    for line in out.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            d.setdefault(k, v)
    return d


GREEN = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
         {"__typename": "StatusContext", "state": "SUCCESS"}]


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(os.path.realpath(self.tmp.name))
        self.origin = d / "origin.git"
        git(d, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.main = d / "repo"
        git(d, "clone", "-q", str(self.origin), str(self.main))
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"),
                     ("core.hooksPath", "/dev/null"), ("commit.gpgsign", "false")):
            git(self.main, "config", k, v)
        (self.main / "README").write_text("x\n")
        git(self.main, "add", "README")
        git(self.main, "commit", "-q", "-m", "init")
        git(self.main, "push", "-q", "origin", "HEAD:main")
        self.wt = self.main / ".claude" / "worktrees" / "foo"
        git(self.main, "worktree", "add", "-q", "-b", "task/foo", str(self.wt))
        self.commit("work")
        git(self.wt, "push", "-q", "-u", "origin", "task/foo")
        self.common = Path(git(self.main, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        self.marker = self.common / "work-system" / "armed" / "foo"
        self.bin = d / "bin"
        self.bin.mkdir()
        self.view = d / "view.json"
        self.checks = d / "required.json"
        (self.bin / "gh").write_text(
            "#!/bin/bash\n"
            f'[ -f "{d}/gh-fail" ] && exit 1\n'
            'case "$1 $2" in\n'
            f'  "pr view") cat "{self.view}" ;;\n'
            # like gh: no file = "no required checks", rc 1; a file = its JSON
            f'  "pr checks") if [ -f "{self.checks}" ]; then cat "{self.checks}"; exit 1; fi\n'
            "     echo \"no required checks reported on the 'x' branch\" >&2; exit 1 ;;\n"
            "  *) exit 9 ;;\n"
            "esac\n")
        (self.bin / "gh").chmod(0o755)
        self.set_view()

    def commit(self, msg):
        git(self.wt, "commit", "-q", "--allow-empty", "-m", msg)
        return git(self.wt, "rev-parse", "HEAD")

    def head(self):
        return git(self.wt, "rev-parse", "HEAD")

    def set_view(self, oid=None, state="OPEN", rollup=GREEN, ms="CLEAN",
                 mergeable="MERGEABLE", decision="", branch="task/foo", number=7):
        self.view.write_text(json.dumps({
            "number": number, "state": state, "headRefName": branch,
            "headRefOid": oid or self.head(), "mergeable": mergeable,
            "mergeStateStatus": ms, "reviewDecision": decision,
            "statusCheckRollup": rollup}))

    def set_required(self, names):
        """None = no required checks; a list = those names; "broken" = unreadable."""
        if names is None:
            self.checks.unlink(missing_ok=True)
        else:
            self.checks.write_text("garbage" if names == "broken" else
                                   json.dumps([{"name": n} for n in names]))

    def gh_fail(self, on=True):
        p = self.view.parent / "gh-fail"
        p.write_text("") if on else p.unlink(missing_ok=True)

    def run(self, *args, env=None, script=GATE):
        e = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
        e.update(PATH=f"{self.bin}:{SHADOW}", LAND_GATE_POLL_SECS="0.2",
                 LAND_GATE_CI_GRACE_SECS="180")
        e.update(env or {})
        cmd = ["python3", "-I", str(script), *args] if script == GATE else ["bash", str(script), *args]
        p = subprocess.run(cmd, cwd=self.wt, env=e, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def probe(self, **kw):
        rc, out, err = self.run("probe", "--pr", "7", **kw)
        return parse(out), rc, err


def expect(name, f, state, reason, **kw):
    d, rc, err = f.probe(**kw)
    check(name, rc == 0 and d.get("state") == state and d.get("reason") == reason,
          f"(rc={rc} state={d.get('state')} reason={d.get('reason')} err={err.strip()[:200]})")
    return d


# ------------------------------------------------------------------ classification
f = Fixture()
d = expect("ready: PR head == pushed HEAD, CI green, clean", f, "ready", "ready")
check("ready carries class=none and the evidence", d.get("class") == "none"
      and d.get("head") == "same" and d.get("remote") == "same" and d.get("ci") == "green")
check("probe prints the classified HEAD for arm --head", d.get("head_sha") == f.head(), str(d))

# The PR #100 incident: the push landed (local == origin branch), the PR head did not.
old = f.head()
git(f.wt, "commit", "-q", "--amend", "--allow-empty", "-m", "work (amended)")
git(f.wt, "push", "-q", "-f", "origin", "task/foo")
f.set_view(oid=old)
d = expect("pr-lagging: local == origin, PR head stale (amend)", f, "wait", "pr-lagging")
check("pr-lagging is transient and names the diverged PR head",
      d.get("class") == "transient" and d.get("head") == "diverged" and d.get("remote") == "same")
f.set_view()

f.set_view(rollup=[{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}])
expect("ci-pending: a running check", f, "wait", "ci-pending")
f.set_view(rollup=[{"__typename": "StatusContext", "state": "PENDING"}])
expect("ci-pending: a pending status", f, "wait", "ci-pending")
RED = {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}
SLOW = {"__typename": "CheckRun", "name": "preview", "status": "QUEUED", "conclusion": ""}
f.set_view(rollup=GREEN + [RED])
f.set_required(["lint"])
d = expect("ci-failed: a failing REQUIRED check is hard", f, "stop", "ci-failed")
check("ci-failed is class=hard", d.get("class") == "hard")
f.set_required("broken")
d = expect("a red check while gh cannot say what is required: wait, not a hard stop",
           f, "wait", "ci-required-unknown")
check("... reported as ci=unknown", d.get("ci") == "unknown", str(d))
f.set_required(None)
d = expect("a red check with no required checks: ready, /merge warns", f, "ready", "ready")
check("... reported as ci=failed-optional", d.get("ci") == "failed-optional", str(d))
f.set_required(["scan"])
d = expect("a red OPTIONAL check beside required ones: ready", f, "ready", "ready")
check("... also ci=failed-optional", d.get("ci") == "failed-optional")
f.set_view(rollup=[{"__typename": "CheckRun", "name": "scan", "status": "COMPLETED",
                    "conclusion": "SUCCESS"}, SLOW])
expect("a pending OPTIONAL check beside passed required ones: ready", f, "ready", "ready")
f.set_required(None)
expect("a pending check with no required checks: wait for the CI run", f, "wait", "ci-pending")
f.set_view(rollup=[{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "STALE"}])
expect("a STALE conclusion never holds the merge", f, "ready", "ready")
f.set_view(rollup=[])
expect("no checks and no workflows: ready", f, "ready", "ready")
wf = f.wt / ".github" / "workflows"
wf.mkdir(parents=True)
(wf / "ci.yml").write_text("on: pull_request\n")
git(f.wt, "add", ".github")
git(f.wt, "commit", "-q", "-m", "ci")
git(f.wt, "push", "-q", "origin", "task/foo")
f.set_view(rollup=[])
expect("no checks yet but workflows exist: ci-not-started", f, "wait", "ci-not-started")
f.set_view(ms="UNKNOWN")
expect("mergeStateStatus UNKNOWN: wait", f, "wait", "merge-state-unknown")
f.set_view(ms="DRAFT")
expect("a draft PR is hard", f, "stop", "draft")
f.set_view(ms="BEHIND")
expect("BEHIND (base requires an up-to-date branch) is hard", f, "stop", "behind-base")
f.set_view(ms="BLOCKED", decision="REVIEW_REQUIRED")
expect("BLOCKED on a missing review is hard", f, "stop", "review-required")
f.set_view(ms="BLOCKED")
expect("BLOCKED with every check passed (a required one not registered yet): wait",
       f, "wait", "merge-blocked")
f.set_view(ms="UNSTABLE")
expect("UNSTABLE (an optional check red) is still ready", f, "ready", "ready")
f.set_view(ms="DIRTY", mergeable="CONFLICTING")
expect("conflict is hard", f, "stop", "conflict")
f.set_view(decision="CHANGES_REQUESTED")
expect("changes requested is hard", f, "stop", "changes-requested")
f.set_view(state="CLOSED")
expect("closed PR is hard", f, "stop", "pr-closed")
f.set_view(state="MERGED")
d = expect("merged PR: state=merged", f, "merged", "merged")
check("merged reports the head relation", d.get("head") == "same")
f.set_view(branch="other")
expect("PR from another branch is hard", f, "stop", "pr-other-branch")
f.set_view(number=8)
expect("PR number mismatch is hard", f, "stop", "pr-mismatch")
f.set_view()
f.gh_fail()
d = expect("gh error/5xx: transient", f, "wait", "gh-unavailable")
f.gh_fail(False)

(f.wt / "dirty.txt").write_text("x")
expect("dirty tree is hard", f, "stop", "dirty-tree")
(f.wt / "dirty.txt").unlink()

f.commit("local only")
f.set_view()
expect("unpushed local commit is hard", f, "stop", "unpushed")
git(f.wt, "reset", "-q", "--hard", "HEAD~1")

# origin moved past local (another push the lane does not have)
tmpc = Path(f.tmp.name) / "other"
git(Path(f.tmp.name), "clone", "-q", "-b", "task/foo", str(f.origin), str(tmpc))
for k, v in (("user.email", "t@e"), ("user.name", "t"), ("commit.gpgsign", "false")):
    git(tmpc, "config", k, v)
git(tmpc, "commit", "-q", "--allow-empty", "-m", "elsewhere")
git(tmpc, "push", "-q", "origin", "task/foo")
f.set_view()
expect("origin ahead of local is hard", f, "stop", "remote-behind")
git(f.wt, "pull", "-q", "--ff-only")
f.set_view()
expect("back in sync after the pull", f, "ready", "ready")

git(f.wt, "branch", "--unset-upstream")
expect("no upstream config: the PR's branch on origin still counts", f, "ready", "ready")
git(f.wt, "branch", "-u", "origin/main")
expect("an upstream tracking another branch is not the PR's branch", f, "ready", "ready")
git(f.wt, "branch", "-u", "origin/task/foo")

nogh = f.run("probe", "--pr", "7", env={"PATH": str(SHADOW)})
check("no gh is hard", parse(nogh[1]).get("reason") == "nogh")

rc, out, err = f.run("probe")
check("probe without --pr is a usage error", rc == 2)
rc, out, err = f.run("probe", "--pr", "7; rm -rf /")
check("a non-numeric PR is a usage error", rc == 2)
p = subprocess.run(["python3", "-I", str(GATE), "probe", "--pr", "7"], cwd=f.main,
                   capture_output=True, text=True, env={"PATH": f"{f.bin}:{SHADOW}"})
check("the main checkout is not a lane (exit 1)", p.returncode == 1)

# ------------------------------------------------------------------ armed
g = Fixture()
git(g.wt, "commit", "-q", "--amend", "--allow-empty", "-m", "amended")
git(g.wt, "push", "-q", "-f", "origin", "task/foo")
g.set_view(oid=old)
rc, out, _ = g.run("arm", "--pr", "7", "--head", g.head())
a = parse(out)
check("arm records the armed head", a.get("armed") == "yes" and a.get("head") == g.head()
      and len(a.get("arm_id", "")) == 32)
check("marker lives in the git common dir, mode 600",
      g.marker.is_file() and (g.marker.stat().st_mode & 0o777) == 0o600)
m = parse(g.marker.read_text())
check("marker remembers the PR head seen at arm", m.get("pr_head_at_arm") == old)
aid = a.get("arm_id", "")

rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aid, "--slice-secs", "1")
w = parse(out)
check("a slice that ends still lagging says wait + armed_left",
      w.get("state") == "wait" and w.get("reason") == "pr-lagging"
      and int(w.get("armed_left", "0")) > 2600, out)
check("waiting prints progress lines", "progress elapsed=" in out)

g.set_view()     # GitHub caught up, CI green
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aid, "--slice-secs", "5")
check("wait returns ready once the PR head synced and CI is green",
      parse(out).get("state") == "ready", out)

g.set_view(oid="f" * 40)
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aid, "--slice-secs", "1")
check("a third PR head (neither armed nor seen at arm) disarms: pr-head-moved",
      parse(out).get("reason") == "pr-head-moved", out)
g.set_view()

rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", "0" * 32, "--slice-secs", "1")
check("a wrong arm_id never waits", parse(out).get("reason") == "not-armed")
rc, out, _ = g.run("wait", "--pr", "8", "--arm-id", aid, "--slice-secs", "1")
check("another PR number never waits", parse(out).get("reason") == "not-armed")
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", "nothex", "--slice-secs", "1")
check("a malformed arm_id is a usage error", rc == 2)

g.commit("new local commit")
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aid, "--slice-secs", "1")
check("a new local head disarms: head-moved", parse(out).get("reason") == "head-moved", out)
git(g.wt, "reset", "-q", "--hard", "HEAD~1")

# A wait whose marker vanishes mid-slice stops and never re-creates it.
g.set_view(oid="e" * 40)   # lagging again, with a PR head arm will record
rc, out, _ = g.run("arm", "--pr", "7", "--head", g.head())
aidr = parse(out).get("arm_id", "")
e = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
e.update(PATH=f"{g.bin}:{SHADOW}", LAND_GATE_POLL_SECS="0.2")
proc = subprocess.Popen(["python3", "-I", str(GATE), "wait", "--pr", "7", "--arm-id", aidr,
                         "--slice-secs", "20"], cwd=g.wt, env=e, stdout=subprocess.PIPE, text=True)
time.sleep(1.5)
g.marker.unlink()
g.set_view()   # synced: a stale in-memory arm would now write synced_at and say ready
out, _ = proc.communicate(timeout=60)
check("a marker removed mid-wait stops the wait: disarmed",
      parse(out).get("reason") == "disarmed", out)
check("... and the running wait did not re-create it", not g.marker.exists())

# The timeout line names what it waited for, even when the deadline hits a new call.
g.set_view(oid="e" * 40)
rc, out, _ = g.run("arm", "--pr", "7", "--head", g.head(), "--max-secs", "2")
aidt = parse(out).get("arm_id", "")
g.run("wait", "--pr", "7", "--arm-id", aidt, "--slice-secs", "1")
time.sleep(2.2)
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aidt, "--slice-secs", "1")
check("armed-timeout on a fresh call still carries last_wait",
      parse(out).get("reason") == "armed-timeout" and parse(out).get("last_wait") == "pr-lagging", out)
g.set_view()

rc, out, _ = g.run("arm", "--pr", "7", "--head", g.head(), "--max-secs", "0")
aid0 = parse(out).get("arm_id", "")
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aid0, "--slice-secs", "1")
check("past the deadline: armed-timeout (hard)",
      parse(out).get("reason") == "armed-timeout" and parse(out).get("class") == "hard", out)

rc, out, _ = g.run("arm", "--pr", "7", "--head", g.head(), env={"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1"})
aidp = parse(out).get("arm_id", "")
check("in herdr the marker names the pane", "worker_pane=w1:p1" in g.marker.read_text())
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aidp, "--slice-secs", "1",
                   env={"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p2"})
check("another pane never continues the wait", parse(out).get("reason") == "armed-elsewhere", out)

g.marker.write_text(g.marker.read_text() + "evil=1\n")
rc, out, _ = g.run("wait", "--pr", "7", "--arm-id", aidp, "--slice-secs", "1")
check("a marker with an unknown key is refused", parse(out).get("reason") == "not-armed")

rc, out, _ = g.run("arm", "--pr", "7", "--head", g.head())
rc, out, _ = g.run("end", "foo", script=LANDING)
check("landing.sh end clears the armed state", "disarmed=yes" in out and not g.marker.exists(), out)
rc, out, _ = g.run("disarm")
check("disarm without a marker says removed=no", "removed=no" in out)

# CI grace: workflows exist, no check ever appears → ready after the grace.
h = Fixture()
wf = h.wt / ".github" / "workflows"
wf.mkdir(parents=True)
(wf / "ci.yml").write_text("on: push\n")
git(h.wt, "add", ".github")
git(h.wt, "commit", "-q", "-m", "ci")
git(h.wt, "push", "-q", "origin", "task/foo")
h.set_view(rollup=[])
rc, out, _ = h.run("arm", "--pr", "7", "--head", h.head())
aid = parse(out).get("arm_id", "")
rc, out, _ = h.run("wait", "--pr", "7", "--arm-id", aid, "--slice-secs", "5",
                   env={"LAND_GATE_CI_GRACE_SECS": "1"})
check("no check after the CI grace counts as no CI: ready", parse(out).get("state") == "ready", out)
h.set_view(ms="BLOCKED")
rc, out, _ = h.run("arm", "--pr", "7", "--head", h.head())
aid = parse(out).get("arm_id", "")
rc, out, _ = h.run("wait", "--pr", "7", "--arm-id", aid, "--slice-secs", "5",
                   env={"LAND_GATE_CI_GRACE_SECS": "1"})
check("BLOCKED past the grace: a rule /land cannot satisfy, hard",
      parse(out).get("reason") == "merge-blocked" and parse(out).get("state") == "stop", out)
rc, out, _ = h.run("arm", "--pr", "7", "--head", "0" * 40)
check("arm refuses a head other than the one probe classified",
      parse(out).get("armed") == "no" and parse(out).get("reason") == "head-moved", out)
rc, out, _ = h.run("arm", "--pr", "7")
check("arm without --head is a usage error", rc == 2)

if FAILS:
    print("FAIL:")
    for x in FAILS:
        print("  -", x)
    sys.exit(1)
print("land-gate: all tests passed")
