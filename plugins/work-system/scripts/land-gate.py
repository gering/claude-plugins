#!/usr/bin/env python3
"""land-gate.py — is this lane mergeable now, worth waiting for, or stopped?

/land used to abort on any state that was not already mergeable, including
states that resolve on their own: a PR whose head GitHub has not synced to the
pushed branch yet (the PR #100 incident), CI still queued, a gh call that hit a
5xx. This script is the ONE place that classifies a pre-merge state, so the
skill prose only relays `class=`:

    state=ready   class=none       everything /land checks holds: hand to /merge
    state=wait    class=transient  resolves without the user: arm and wait
    state=stop    class=hard       needs the user: report `reason=` and stop
    state=merged  class=none       the PR is already merged (step 1's MERGED path)

Subcommands (run inside the lane's worktree; nothing typed in is repo-derived
except the PR number and the arm id this script printed):

    probe --pr <n>
        One classification, no state written. Prints head_sha= (the local
        HEAD it classified) for `arm --head`.
    arm --pr <n> --head <sha> [--max-secs <s>]
        Record "land armed" for <sha>, the head probe classified (refused with
        reason=head-moved when HEAD is no longer that commit): the exact head
        the user ran /land on, the PR head seen at that moment, a deadline
        (default 2700s = 45 min) and a random arm_id. Prints armed=yes|no,
        arm_id=, head=, until=, reason=.
    wait --pr <n> --arm-id <id> [--slice-secs <s>]
        Re-probe every few seconds for one slice (default 390s: with a final
        probe's network timeouts it stays under a tool call's 10-minute cap) and return as soon as the state is not `wait`.
        Prints `progress ...` lines while waiting, then the final classification
        plus armed_left=<s> (and last_wait=<reason> on a stop that ended a
        wait, e.g. armed-timeout). A slice that ends still waiting prints state=wait;
        the caller runs `wait` again. The deadline is in the marker, so the bound
        holds however often the caller loops: past it, reason=armed-timeout.
    disarm
        Remove the marker. (`landing.sh end` removes it too.)

The marker (<git common dir>/work-system/armed/<task>) is loop state, never
authority: it grants nothing, it only lets one /land carry its head and
deadline across tool calls. `wait` refuses it unless the caller holds its
arm_id, the HEAD is still the armed head, the pane matches (in herdr: the
landing marker's pane must still host the same agent session) and the deadline
has not passed. A running `wait` re-reads the marker on every probe and stops
(reason=disarmed) once it is gone or re-armed, and writes loop state back only
while its own arm is on file. No daemon: a lost session leaves the PR unmerged.

Residuals: outside herdr there is no pane to bind, so only the arm_id, the head
binding and the deadline guard the marker; the skill's "never `wait` again
after a context loss" rule carries the rest. The write-back's check-then-replace
is not locked: a removal landing in that microsecond window is undone, and the
wait goes on until its next stop (head, deadline, class).

Merge state: DRAFT and BEHIND need the user (hard). BLOCKED with every check
passed is either a missing review (REVIEW_REQUIRED, hard) or a required check
that has not registered yet: that waits for the CI grace after the head synced,
then stops as merge-blocked (a protection rule /land cannot satisfy).

Head binding: the armed head is the local HEAD at `arm`. Any other local HEAD
-> head-moved (hard). While the PR lags, its head may only be the one seen at
`arm` (or the armed head itself); a third head -> pr-head-moved (hard).

Exit: 0 answered (any state), 1 not inside a lane, 2 usage.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHA_RE = re.compile(r"[0-9a-f]{40}")
TASK_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
ARM_RE = re.compile(r"[0-9a-f]{32}")

DEFAULT_MAX_SECS = 2700
# One slice plus a final classify() (gh + ls-remote + two fetches, 30 s each)
# must stay under a tool call's 600 s cap. --slice-secs may only shorten it.
MAX_SLICE_SECS = 390
DEFAULT_SLICE_SECS = MAX_SLICE_SECS
POLL_SECS = float(os.environ.get("LAND_GATE_POLL_SECS", "20"))
# A PR head with no check at all, in a repo that has workflow files, is given
# this long after its head synced before it counts as "no CI" (a workflow that
# never triggers on PRs must not hold the merge for 45 minutes).
CI_GRACE_SECS = int(os.environ.get("LAND_GATE_CI_GRACE_SECS", "180"))
GH_TIMEOUT = int(os.environ.get("LAND_GATE_GH_TIMEOUT", "30"))
GIT_NET_TIMEOUT = int(os.environ.get("LAND_GATE_NET_TIMEOUT", "30"))

FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
OK = {"SUCCESS", "NEUTRAL", "SKIPPED", "STALE"}


def usage():
    print("usage: land-gate.py {probe --pr <n> | arm --pr <n> --head <sha> [--max-secs <s>] | "
          "wait --pr <n> --arm-id <id> [--slice-secs <s>] | disarm}", file=sys.stderr)
    sys.exit(2)


def run(cmd, timeout=15, env=None, want_err=False):
    """(rc, stdout[, stderr]) — a timeout or a missing binary is rc 124 / 127."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env)
        res = (p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        res = (124, "", "")
    except OSError:
        res = (127, "", "")
    return res if want_err else res[:2]


def git(*args, timeout=15):
    rc, out = run(["git", *args], timeout=timeout,
                  env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    return rc, out.strip()


def kv(text):
    d = {}
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            d.setdefault(k, v)
    return d


# ------------------------------------------------------------------ the lane
class Lane:
    def __init__(self):
        rc, out = run(["bash", str(HERE / "main-repo-path.sh"), "linked"])
        if rc != 0 or out.strip() != "linked":
            print("land-gate.py: not inside a task worktree", file=sys.stderr)
            sys.exit(1)
        res = kv(run(["bash", str(HERE / "task-status.sh"), "resolve"])[1])
        self.task = res.get("task_name", "")
        self.branch = res.get("task_branch", "")
        if not TASK_RE.fullmatch(self.task) or not self.branch:
            print("land-gate.py: this worktree is not on a task branch", file=sys.stderr)
            sys.exit(1)
        rc, common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
        if rc != 0 or not common:
            print("land-gate.py: cannot read the repository's git common dir", file=sys.stderr)
            sys.exit(1)
        self.marker = Path(common) / "work-system" / "armed" / self.task
        self.top = git("rev-parse", "--show-toplevel")[1]

    def head(self):
        rc, out = git("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        return out if rc == 0 and SHA_RE.fullmatch(out) else ""


# ------------------------------------------------------------------ the marker
MARKER_KEYS = {
    "version": r"1", "task": TASK_RE.pattern, "pr": r"[0-9]{1,9}",
    "head": SHA_RE.pattern, "pr_head_at_arm": r"(?:[0-9a-f]{40})?",
    "arm_id": ARM_RE.pattern, "armed_at": r"[0-9]{1,12}", "until": r"[0-9]{1,12}",
    "synced_at": r"[0-9]{0,12}", "last_wait": r"[a-z-]{0,32}",
    "worker_pane": r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}",
}
MARKER_REQUIRED = ("version", "task", "pr", "head", "arm_id", "armed_at", "until")


def read_marker(lane):
    p = lane.marker
    if p.is_symlink() or not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    seen = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        k, sep, v = line.partition("=")
        if not sep or k not in MARKER_KEYS or k in seen or not re.fullmatch(MARKER_KEYS[k], v):
            return None
        seen[k] = v
    if any(k not in seen for k in MARKER_REQUIRED) or seen["task"] != lane.task:
        return None
    return seen


def write_marker(lane, m):
    d = lane.marker.parent
    try:
        d.mkdir(parents=True, exist_ok=True)
        if d.is_symlink() or lane.marker.is_symlink():
            return False
        tmp = d / (".armed.%s" % secrets.token_hex(6))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join("%s=%s\n" % (k, m[k]) for k in MARKER_KEYS if k in m))
        os.replace(tmp, lane.marker)
        return True
    except OSError:
        return False


def update_marker(lane, m):
    """Write back loop state only while THIS arm is still on file, so a marker
    that disarm or `landing.sh end` removed (or a newer /land re-armed) is not
    re-created by a running wait (unlocked: see the module's residuals)."""
    cur = read_marker(lane)
    if cur is None or cur["arm_id"] != m["arm_id"]:
        return False
    return write_marker(lane, m)


def remove_marker(lane):
    try:
        lane.marker.unlink()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ inputs
def pr_view(pr):
    """The PR as gh reports it, or None when gh could not answer (transient)."""
    rc, out = run(["gh", "pr", "view", str(pr), "--json",
                   "number,state,headRefName,headRefOid,mergeable,mergeStateStatus,"
                   "reviewDecision,statusCheckRollup"], timeout=GH_TIMEOUT)
    if rc != 0:
        return None
    try:
        d = json.loads(out)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def check_states(rollup):
    """[(name, ok|pending|failed)] from the PR head's statusCheckRollup."""
    out = []
    for c in rollup if isinstance(rollup, list) else []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or c.get("context") or "")
        if c.get("__typename") == "StatusContext" or ("state" in c and "status" not in c):
            st = str(c.get("state") or "").upper()
            out.append((name, "failed" if st in FAILED else "ok" if st in OK else "pending"))
        elif str(c.get("status") or "").upper() != "COMPLETED":
            out.append((name, "pending"))
        else:
            concl = str(c.get("conclusion") or "").upper()
            out.append((name, "failed" if concl in FAILED else "ok" if concl in OK else "pending"))
    return out


_REQUIRED = {}


def required_checks(pr):
    """Names of the PR's required checks: a set (empty = branch protection
    requires none), or None when gh could not say. A known answer is kept for
    the process: the required set does not change during one /land."""
    if pr in _REQUIRED:
        return _REQUIRED[pr]
    rc, out, err = run(["gh", "pr", "checks", str(pr), "--required", "--json", "name"],
                       timeout=GH_TIMEOUT, want_err=True)
    try:
        d = json.loads(out) if out.strip() else None
    except ValueError:
        d = None
    if isinstance(d, list):
        _REQUIRED[pr] = {str(x.get("name") or "") for x in d if isinstance(x, dict)}
    elif "no required checks" in err.lower():
        _REQUIRED[pr] = set()
    return _REQUIRED.get(pr)


def ci_state(rollup, pr):
    """green | pending | failed | failed-optional | unknown | none.

    Like /pr-flow:merge, only a REQUIRED check blocks: a red optional check is
    `failed-optional` (/merge warns and asks), never a hard stop. A red check
    while gh cannot say which checks are required is `unknown` (wait: the gh
    hiccup passes; a real red stays red). Pending checks are waited for while
    branch protection requires none (the CI run is then the only signal), else
    only the required ones."""
    states = check_states(rollup)
    if not states:
        return "none"
    if all(st == "ok" for _, st in states):
        return "green"
    req = required_checks(pr)

    if req is None and any(st == "failed" for _, st in states):
        return "unknown"
    if any(st == "failed" and n in req for n, st in states):
        return "failed"
    if any(st == "pending" and (not req or n in req) for n, st in states):
        return "pending"
    return "failed-optional" if any(st == "failed" for _, st in states) else "green"


def has_workflows(top):
    d = Path(top) / ".github" / "workflows"
    try:
        return any(p.suffix in (".yml", ".yaml") for p in d.iterdir())
    except OSError:
        return False


def relation(a, b, remote="origin"):
    """same | ahead | behind | diverged | unreadable — a against b."""
    if a == b:
        return "same"
    for sha in (a, b):
        if git("cat-file", "-e", sha + "^{commit}")[0] != 0:
            if git("fetch", "-q", "--", remote, sha, timeout=GIT_NET_TIMEOUT)[0] != 0:
                return "unreadable"
    rc, out = git("rev-list", "--left-right", "--count", "%s...%s" % (a, b))
    m = re.fullmatch(r"(\d+)\s+(\d+)", out) if rc == 0 else None
    if not m:
        return "unreadable"
    left, right = int(m.group(1)), int(m.group(2))
    if left == 0 and right == 0:
        return "same"
    if left == 0:
        return "behind"
    if right == 0:
        return "ahead"
    return "diverged"


def pushed_ref(branch):
    """(remote, ref) the PR's head branch lives at: the branch's configured
    remote (else origin) and refs/heads/<branch> — the PR's headRefName, which
    classify() already matched against this branch. Not branch.<b>.merge: an
    upstream that tracks another branch (say origin/main) is not the PR."""
    rc, remote = git("config", "--get", "branch.%s.remote" % branch)
    if rc or not remote or remote == ".":
        remote = "origin"
    return remote, "refs/heads/" + branch


def remote_tip(remote, ref):
    """The upstream branch tip as the remote has it now: sha, '' (absent), None (unreadable)."""
    rc, out = git("ls-remote", "--", remote, ref, timeout=GIT_NET_TIMEOUT)
    if rc != 0:
        return None
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name == ref and SHA_RE.fullmatch(sha):
            return sha
    return ""


# ------------------------------------------------------------------ the classification
def classify(lane, pr, armed=None, now=None):
    """dict with state/class/reason + the evidence it was decided on."""
    now = int(time.time()) if now is None else now
    r = {"head": "", "remote": "", "pr_head": "", "pr_state": "", "ci": "",
         "merge_state": ""}

    def out(state, reason):
        cls = {"ready": "none", "merged": "none", "wait": "transient", "stop": "hard"}[state]
        r.update(state=state, cls=cls, reason=reason)
        return r

    if shutil.which("gh") is None:
        return out("stop", "nogh")
    head = lane.head()
    r["head_sha"] = head
    if not head:
        return out("stop", "head-unreadable")
    if armed:
        if now >= int(armed["until"]):
            return out("stop", "armed-timeout")
        if head != armed["head"]:
            return out("stop", "head-moved")
    rc, dirty = git("status", "--porcelain")
    if rc != 0:
        return out("stop", "status-unreadable")
    if dirty:
        return out("stop", "dirty-tree")

    view = pr_view(pr)
    if view is None:
        return out("wait", "gh-unavailable")
    state = str(view.get("state") or "")
    r["pr_state"] = state or "unknown"
    oid = str(view.get("headRefOid") or "")
    r["pr_head"] = oid if SHA_RE.fullmatch(oid) else ""
    if str(view.get("number") or "") != str(pr):
        return out("stop", "pr-mismatch")
    if state == "MERGED":
        r["head"] = relation(head, r["pr_head"]) if r["pr_head"] else "unreadable"
        return out("merged", "merged")
    if state == "CLOSED":
        return out("stop", "pr-closed")
    if state != "OPEN" or not r["pr_head"]:
        return out("wait", "gh-unavailable")
    if view.get("headRefName") != lane.branch:
        return out("stop", "pr-other-branch")

    up = pushed_ref(lane.branch)
    tip = remote_tip(*up)
    if tip is None:
        return out("wait", "remote-unreadable")
    if tip == "":
        return out("stop", "remote-missing")
    r["remote"] = relation(head, tip, up[0])
    if r["remote"] == "unreadable":
        return out("wait", "remote-unreadable")
    if r["remote"] == "ahead":
        return out("stop", "unpushed")
    if r["remote"] != "same":
        return out("stop", "remote-" + r["remote"])

    # local == the pushed branch. A PR head that differs is GitHub lagging behind
    # the push — but only from a head this /land already knew about.
    r["head"] = relation(head, r["pr_head"], up[0])
    if armed is not None and not armed.get("pr_head_at_arm"):
        armed["pr_head_at_arm"] = r["pr_head"]   # gh could not answer at arm time
        update_marker(lane, armed)
    if r["head"] != "same":
        if armed and r["pr_head"] not in (armed.get("pr_head_at_arm"), armed["head"]):
            return out("stop", "pr-head-moved")
        return out("wait", "pr-lagging")

    if armed is not None and not armed.get("synced_at"):
        armed["synced_at"] = str(now)
        update_marker(lane, armed)

    if str(view.get("reviewDecision") or "") == "CHANGES_REQUESTED":
        return out("stop", "changes-requested")
    ms = str(view.get("mergeStateStatus") or "").upper()
    r["merge_state"] = ms
    if ms == "DIRTY" or str(view.get("mergeable") or "").upper() == "CONFLICTING":
        return out("stop", "conflict")
    r["ci"] = ci_state(view.get("statusCheckRollup"), pr)
    if r["ci"] == "failed":
        return out("stop", "ci-failed")
    if r["ci"] == "pending":
        return out("wait", "ci-pending")
    if r["ci"] == "none" and has_workflows(lane.top):
        synced = int(armed["synced_at"]) if armed and armed.get("synced_at") else now
        if armed is None or now - synced < CI_GRACE_SECS:
            return out("wait", "ci-not-started")
    if r["ci"] == "unknown":
        return out("wait", "ci-required-unknown")
    if ms == "UNKNOWN" or not ms:
        return out("wait", "merge-state-unknown")
    if ms == "DRAFT":
        return out("stop", "draft")
    if ms == "BEHIND":
        return out("stop", "behind-base")
    if ms == "BLOCKED":
        if str(view.get("reviewDecision") or "") == "REVIEW_REQUIRED":
            return out("stop", "review-required")
        synced = int(armed["synced_at"]) if armed and armed.get("synced_at") else now
        if armed is None or now - synced < CI_GRACE_SECS:
            return out("wait", "merge-blocked")
        return out("stop", "merge-blocked")
    return out("ready", "ready")


def emit(r, extra=None):
    print("state=" + r["state"])
    print("class=" + r["cls"])
    print("reason=" + r["reason"])
    for k in ("head", "head_sha", "remote", "pr_state", "pr_head", "ci", "merge_state"):
        print("%s=%s" % (k, r.get(k, "")))
    for k, v in (extra or {}).items():
        print("%s=%s" % (k, v))


# ------------------------------------------------------------------ identity
def same_worker(lane, armed):
    """May this session keep waiting on this marker?"""
    pane = armed.get("worker_pane", "")
    if not pane:
        return True
    if os.environ.get("HERDR_PANE_ID", "") != pane:
        return False
    st = kv(run(["bash", str(HERE / "landing.sh"), "status", lane.task], timeout=30)[1])
    # stale = herdr's list shows that pane hosting another session now.
    return st.get("landing") != "stale" and st.get("worker_pane", pane) == pane


# ------------------------------------------------------------------ commands
def take_flags(argv, spec):
    got = {}
    it = iter(argv)
    for a in it:
        if a not in spec or a in got:
            usage()
        v = next(it, None)
        if v is None or not re.fullmatch(spec[a], v):
            usage()
        got[a] = v
    return got


def cmd_probe(argv):
    f = take_flags(argv, {"--pr": r"[0-9]{1,9}"})
    if "--pr" not in f:
        usage()
    emit(classify(Lane(), f["--pr"]))


def cmd_arm(argv):
    f = take_flags(argv, {"--pr": r"[0-9]{1,9}", "--head": SHA_RE.pattern,
                          "--max-secs": r"[0-9]{1,6}"})
    if "--pr" not in f or "--head" not in f:
        usage()
    lane = Lane()
    head = lane.head()
    if not head:
        print("armed=no\nreason=head-unreadable")
        return
    if head != f["--head"]:
        # a commit landed after probe classified (and the user acknowledged) its head
        print("armed=no\nreason=head-moved")
        return
    view = pr_view(f["--pr"]) or {}
    oid = str(view.get("headRefOid") or "")
    now = int(time.time())
    m = {"version": "1", "task": lane.task, "pr": f["--pr"], "head": head,
         "pr_head_at_arm": oid if SHA_RE.fullmatch(oid) else "",
         "arm_id": secrets.token_hex(16), "armed_at": str(now),
         "until": str(now + int(f.get("--max-secs", DEFAULT_MAX_SECS))),
         "synced_at": ""}
    pane = os.environ.get("HERDR_PANE_ID", "")
    if os.environ.get("HERDR_ENV") == "1" and re.fullmatch(MARKER_KEYS["worker_pane"], pane):
        m["worker_pane"] = pane
    if not write_marker(lane, m):
        print("armed=no\nreason=unwritable")
        return
    print("armed=yes")
    print("arm_id=" + m["arm_id"])
    print("head=" + head)
    print("until=" + m["until"])


def cmd_wait(argv):
    f = take_flags(argv, {"--pr": r"[0-9]{1,9}", "--arm-id": ARM_RE.pattern,
                          "--slice-secs": r"[0-9]{1,4}"})
    if "--pr" not in f or "--arm-id" not in f:
        usage()
    lane = Lane()
    armed = read_marker(lane)
    if armed is None or not secrets.compare_digest(armed["arm_id"], f["--arm-id"]) \
            or armed["pr"] != f["--pr"]:
        emit({"state": "stop", "cls": "hard", "reason": "not-armed"})
        return
    if not same_worker(lane, armed):
        emit({"state": "stop", "cls": "hard", "reason": "armed-elsewhere"})
        return
    start = time.monotonic()
    slice_secs = min(int(f.get("--slice-secs", DEFAULT_SLICE_SECS)), MAX_SLICE_SECS)
    last_reason, last_print = armed.get("last_wait") or None, start
    printed = None
    while True:
        cur = read_marker(lane)
        if cur is None or cur["arm_id"] != armed["arm_id"]:
            # disarm / landing.sh end / a newer /land ran meanwhile
            r = {"state": "stop", "cls": "hard", "reason": "disarmed"}
            left = 0
            break
        armed = cur
        r = classify(lane, f["--pr"], armed)
        left = max(0, int(armed["until"]) - int(time.time()))
        if r["state"] != "wait":
            break
        if r["reason"] != armed.get("last_wait"):
            armed["last_wait"] = r["reason"]   # survives into the next call's timeout line
            update_marker(lane, armed)
        last_reason = r["reason"]
        t = time.monotonic()
        if r["reason"] != printed or t - last_print >= 120:
            print("progress elapsed=%ds reason=%s armed_left=%ds" % (
                int(t - start), r["reason"], left), flush=True)
            printed, last_print = r["reason"], t
        if t - start + POLL_SECS > slice_secs or left <= 0:
            break
        time.sleep(min(POLL_SECS, max(left, 0.1)))
    extra = {"armed_left": left}
    if r["state"] == "stop" and last_reason:
        extra["last_wait"] = last_reason   # what it was waiting for (armed-timeout)
    emit(r, extra)


def cmd_disarm(argv):
    if argv:
        usage()
    print("removed=" + ("yes" if remove_marker(Lane()) else "no"))


def main():
    if len(sys.argv) < 2:
        usage()
    cmd = {"probe": cmd_probe, "arm": cmd_arm, "wait": cmd_wait,
           "disarm": cmd_disarm}.get(sys.argv[1])
    if cmd is None:
        usage()
    cmd(sys.argv[2:])


if __name__ == "__main__":
    main()
