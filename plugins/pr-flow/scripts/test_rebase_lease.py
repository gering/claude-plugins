#!/usr/bin/env python3
"""Tests for pr-flow's rebase-lease.sh — run standalone or via
scripts/check-structure.py's "plugin tests" check.

The property that matters: `/rebase` pins its force-push to the remote state
seen before the rebase. A teammate's push that a background fetch pulls into
the remote-tracking ref between rebase and push must make the push FAIL — a
bare `--force-with-lease` would pass it and overwrite the commit. An unchanged
remote must push. Every repo is a hermetic temp repo (bare "origin" + clones),
with no global/system git config and no user hooks.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "rebase-lease.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


ENV = dict(
    os.environ,
    GIT_CONFIG_GLOBAL=os.devnull,
    GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], env=ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(repo, name, text, msg):
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def lease(repo, *args):
    r = subprocess.run(["bash", str(SCRIPT), *args], cwd=str(repo), env=ENV,
                       capture_output=True, text=True)
    kv = dict(line.partition("=")[::2] for line in r.stdout.splitlines())
    return r.returncode, kv, r.stderr


def setup():
    """origin (bare) with main + task/x; `me` and `mate` clones on task/x."""
    root = Path(tempfile.mkdtemp())
    origin = root / "origin.git"
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    me = root / "me"
    git(root, "clone", "-q", str(origin), str(me))
    commit(me, "base.txt", "base\n", "root")
    git(me, "push", "-q", "origin", "main")
    git(me, "checkout", "-q", "-b", "task/x")
    commit(me, "mine.txt", "mine\n", "mine")
    git(me, "push", "-q", "-u", "origin", "task/x")
    mate = root / "mate"
    git(root, "clone", "-q", "-b", "task/x", str(origin), str(mate))
    # main moves on, so the rebase in `me` really rewrites task/x
    git(mate, "checkout", "-q", "main")
    commit(mate, "main.txt", "main\n", "main moves")
    git(mate, "push", "-q", "origin", "main")
    git(mate, "checkout", "-q", "task/x")
    return origin, me, mate


def rebase(me):
    git(me, "fetch", "-q", "origin", "main")
    git(me, "rebase", "-q", "origin/main")


# 1) Unchanged remote → push succeeds, remote branch = rebased HEAD.
origin, me, _ = setup()
rc, rec, _ = lease(me, "record")
check("record: exit 0", rc == 0)
check("record: pinned", rec.get("lease") == "pinned")
check("record: remote/ref", rec.get("remote") == "origin"
      and rec.get("ref") == "refs/heads/task/x")
check("record: sha = tracking ref", rec.get("sha") == git(me, "rev-parse", "origin/task/x"))
rebase(me)
rc, out, _ = lease(me, "push", rec["sha"])
check("unchanged: exit 0", rc == 0)
check("unchanged: push=ok", out.get("push") == "ok" and out.get("remote") == "origin")
check("unchanged: remote updated",
      git(origin, "rev-parse", "refs/heads/task/x") == git(me, "rev-parse", "HEAD"))

# 2) A teammate pushes after `record`, a background fetch pulls it into the
#    tracking ref → the pinned lease rejects; the teammate's commit survives.
origin, me, mate = setup()
rc, rec, _ = lease(me, "record")
rebase(me)
commit(mate, "theirs.txt", "theirs\n", "teammate")
git(mate, "push", "-q", "origin", "task/x")
theirs = git(mate, "rev-parse", "HEAD")
git(me, "fetch", "-q", "origin")            # the IDE's background fetch
check("moved: tracking ref moved", git(me, "rev-parse", "origin/task/x") == theirs)
rc, out, err = lease(me, "push", rec["sha"])
check("moved: exit 3", rc == 3)
check("moved: push=rejected", out.get("push") == "rejected" and out.get("reason") == "lease"
      and out.get("branch") == "task/x" and out.get("remote") == "origin")
check("moved: git's error passed through", "stale info" in err)
check("moved: teammate commit kept", git(origin, "rev-parse", "refs/heads/task/x") == theirs)
# ...and the bare lease this replaces would have overwritten it (the bug).
r = subprocess.run(["git", "-C", str(me), "push", "-q", "--force-with-lease"],
                   env=ENV, capture_output=True, text=True)
check("bare lease: would overwrite (documents the bug)",
      r.returncode == 0 and git(origin, "rev-parse", "refs/heads/task/x") != theirs)

# 3) A teammate's commit already fetched but never integrated → record stops
#    before the rebase, because pinning it would force-push it away.
origin, me, mate = setup()
commit(mate, "theirs.txt", "theirs\n", "teammate")
git(mate, "push", "-q", "origin", "task/x")
git(me, "fetch", "-q", "origin")
rc, rec, _ = lease(me, "record")
check("diverged: exit 3", rc == 3)
check("diverged: lease=diverged", rec.get("lease") == "diverged" and rec.get("missing") == "1"
      and rec.get("remote") == "origin" and rec.get("ref") == "refs/heads/task/x")

# 4) Upstream configured but the tracking ref is gone → absent; the push must
#    find no remote branch: succeeds when it is gone, rejects when it reappeared.
origin, me, mate = setup()
git(mate, "push", "-q", "origin", "--delete", "task/x")
git(me, "update-ref", "-d", "refs/remotes/origin/task/x")
rc, rec, _ = lease(me, "record")
check("absent: lease=absent", rc == 0 and rec.get("lease") == "absent")
rc, out, _ = lease(me, "push", "absent")
check("absent: push=ok", rc == 0 and out.get("push") == "ok")
origin, me, mate = setup()
git(me, "update-ref", "-d", "refs/remotes/origin/task/x")   # remote still has it
rebase(me)   # a new HEAD: pushing an unchanged one is "up-to-date", lease unchecked
rc, out, _ = lease(me, "push", "absent")
check("absent but exists: rejected", rc == 3 and out.get("push") == "rejected")

# 5) No upstream / detached HEAD → none, nothing to push.
origin, me, _ = setup()
git(me, "checkout", "-q", "-b", "local-only")
rc, rec, _ = lease(me, "record")
check("no upstream: none", rc == 0 and rec.get("lease") == "none"
      and rec.get("reason") == "no-upstream")
git(me, "checkout", "-q", "--detach")
rc, rec, _ = lease(me, "record")
check("detached: none", rc == 0 and rec.get("reason") == "detached")

# 6) Our OWN rewritten commit (amend after pushing, or a rebase whose push
#     failed) is not a teammate's: the branch's reflog held it → pinned.
origin, me, _ = setup()
pushed = git(me, "rev-parse", "HEAD")
(me / "mine.txt").write_text("mine amended\n")
git(me, "commit", "-q", "-a", "--amend", "--no-edit")
rc, rec, _ = lease(me, "record")
check("amend: pinned", rc == 0 and rec.get("lease") == "pinned"
      and rec.get("sha") == pushed and rec.get("rewritten") == "yes")
rebase(me)
rc, out, _ = lease(me, "push", rec["sha"])
check("amend: push=ok", rc == 0 and out.get("push") == "ok")

# 7) Upstream with ANOTHER name (branch cut from origin/main, never pushed):
#     simple push refuses that, so must we — never force-push onto main. It is
#     `none` (rebase locally, `git push -u` comes next), not a stop.
origin, me, _ = setup()
git(me, "checkout", "-q", "-b", "feat", "origin/main")
commit(me, "f.txt", "f\n", "feat")
main_before = git(origin, "rev-parse", "refs/heads/main")
rc, rec, _ = lease(me, "record")
check("name mismatch: none", rc == 0 and rec.get("lease") == "none"
      and rec.get("reason") == "upstream-name-mismatch:main" and rec.get("remote") == "origin")
rc, out, _ = lease(me, "push", git(me, "rev-parse", "origin/main"))
check("name mismatch: push refused", rc == 4 and out.get("push") == "unresolved")
check("name mismatch: main untouched", git(origin, "rev-parse", "refs/heads/main") == main_before)
# ...but once that branch is published under its own name (pushed without -u),
# the same-named remote branch is the target: pinned, diverged check active.
git(me, "push", "-q", "origin", "feat")
git(me, "fetch", "-q", "origin")
rc, rec, _ = lease(me, "record")
check("name mismatch published: pinned to same name", rc == 0 and rec.get("lease") == "pinned"
      and rec.get("ref") == "refs/heads/feat" and rec.get("branch") == "feat"
      and rec.get("sha") == git(me, "rev-parse", "origin/feat"))
# push.default=current pushes the same name, not the upstream's
git(me, "config", "push.default", "current")
rc, rec, _ = lease(me, "record")
check("current: own name", rc == 0 and rec.get("lease") == "pinned"
      and rec.get("ref") == "refs/heads/feat")
# push.default=upstream/tracking would push to the upstream (main), but a
# differently named upstream is never a force-push target: same name instead
git(me, "config", "push.default", "tracking")
rc, rec, _ = lease(me, "record")
check("tracking: own name, not main", rc == 0 and rec.get("lease") == "pinned"
      and rec.get("ref") == "refs/heads/feat")
# ...and unpublished, it is `none` — never a pinned lease on main
git(me, "config", "push.default", "upstream")
git(me, "checkout", "-q", "-b", "feat2", "origin/main")
commit(me, "f2.txt", "f2\n", "feat2")
main_before = git(origin, "rev-parse", "refs/heads/main")
rc, rec, _ = lease(me, "record")
check("upstream unpublished: none", rc == 0 and rec.get("lease") == "none"
      and rec.get("reason") == "upstream-name-mismatch:main")
rc, out, _ = lease(me, "push", main_before)
check("upstream unpublished: push refused, main untouched", rc == 4
      and git(origin, "rev-parse", "refs/heads/main") == main_before)
git(me, "checkout", "-q", "feat")
# a remote.<name>.push refspec is a target this script does not mirror → stop
git(me, "config", "push.default", "simple")
git(me, "config", "remote.origin.push", "refs/heads/feat:refs/heads/review")
rc, rec, _ = lease(me, "record")
check("push refspec: unresolved exit 4", rc == 4 and rec.get("lease") == "unresolved"
      and rec.get("reason") == "push-refspec")

# 8) Fork (triangular) setup: upstream on `origin`, pushRemote = `fork` →
#     the push goes to the fork, the lease reads the fork's tracking ref.
origin, me, _ = setup()
fork = origin.parent / "fork.git"
git(origin.parent, "init", "-q", "--bare", "-b", "main", str(fork))
git(me, "remote", "add", "fork", str(fork))
git(me, "push", "-q", "fork", "task/x")
git(me, "fetch", "-q", "fork")
git(me, "config", "branch.task/x.pushRemote", "fork")
rc, rec, _ = lease(me, "record")
check("fork: remote=fork", rc == 0 and rec.get("remote") == "fork"
      and rec.get("sha") == git(me, "rev-parse", "fork/task/x"))
origin_before = git(origin, "rev-parse", "refs/heads/task/x")
rebase(me)
rc, out, _ = lease(me, "push", rec["sha"])
check("fork: pushed to fork", rc == 0 and git(fork, "rev-parse", "refs/heads/task/x")
      == git(me, "rev-parse", "HEAD"))
check("fork: origin untouched", git(origin, "rev-parse", "refs/heads/task/x") == origin_before)

# 9) Usage errors: no args, unknown mode, a non-sha expect value.
check("no args: exit 2", lease(me)[0] == 2)
check("bad mode: exit 2", lease(me, "force")[0] == 2)
check("bad sha: exit 2", lease(me, "push", "HEAD")[0] == 2)
check("push no sha: exit 2", lease(me, "push")[0] == 2)

if FAILS:
    print("FAIL:\n  " + "\n  ".join(FAILS))
    sys.exit(1)
print("ok")
