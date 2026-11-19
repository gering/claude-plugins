#!/usr/bin/env python3
"""Tests for sync-main.sh — /close step 5's fast-forward of local main.

Run standalone (`python3 test_sync_main.py`) or via check-structure.py. Pinned:
only a strictly-behind main on its own checkout is moved, and only as a
fast-forward; divergence, another checked-out branch, tracked local edits
(dirty) and files in the way are reported and leave every ref where it was.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "sync-main.sh"
FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


def kv(out):
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)


def commit(repo, name, text="x\n"):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", name)


def head(repo, ref="main"):
    return git(repo, "rev-parse", ref).stdout.strip()


def setup(root):
    """origin (bare), main (the Manager's checkout) and up (pushes to origin)."""
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], capture_output=True)
    main = root / "main"
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], capture_output=True)
    for r in (main,):
        git(r, "config", "user.email", "t@example.com")
        git(r, "config", "user.name", "T")
        git(r, "checkout", "-q", "-b", "main")
    commit(main, "seed")
    git(main, "push", "-q", "origin", "main")
    up = root / "up"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(up)], capture_output=True)
    git(up, "config", "user.email", "t@example.com")
    git(up, "config", "user.name", "T")
    return main, up


with tempfile.TemporaryDirectory() as d:
    main, up = setup(Path(d))

    r = kv(run(str(main), "main").stdout)
    check("in sync → in-sync", r.get("result") == "in-sync" and r.get("behind") == "0")

    commit(up, "a"); commit(up, "b")
    git(up, "push", "-q", "origin", "main")
    r = kv(run(str(main), "main").stdout)
    check("behind only → fast-forwarded", r.get("result") == "fast-forwarded" and r.get("behind") == "2")
    check("...to origin's head", head(main) == head(up))

    commit(up, "c"); git(up, "push", "-q", "origin", "main")
    commit(main, "local")
    before = head(main)
    r = kv(run(str(main), "main").stdout)
    check("diverged → reported, untouched",
          r.get("result") == "diverged" and r.get("ahead") == "1" and head(main) == before)
    git(main, "reset", "-q", "--hard", "origin/main")
    commit(main, "unpushed")
    r = kv(run(str(main), "main").stdout)
    check("ahead only → ahead, never in-sync", r.get("result") == "ahead" and r.get("ahead") == "1")
    git(main, "reset", "-q", "--hard", "origin/main")

    commit(up, "d"); git(up, "push", "-q", "origin", "main")
    git(main, "checkout", "-q", "-b", "side")
    side = head(main, "side")
    r = kv(run(str(main), "main").stdout)
    check("another branch checked out → wrong-branch, nothing moves",
          r.get("result") == "wrong-branch" and r.get("current") == "side"
          and head(main, "side") == side)
    git(main, "branch", "-q", "-f", "main", "origin/main")   # main in sync, still parked on side
    r = kv(run(str(main), "main").stdout)
    check("in sync but parked on another branch → wrong-branch, not in-sync",
          r.get("result") == "wrong-branch" and r.get("behind") == "0")
    git(main, "checkout", "-q", "main")

    commit(up, "f"); git(up, "push", "-q", "origin", "main")
    (main / "f").write_text("local file in the way\n")
    before = head(main)
    r = kv(run(str(main), "main").stdout)
    check("local edits in the way → ff-failed, untouched",
          r.get("result") == "ff-failed" and r.get("error") and head(main) == before)
    (main / "f").unlink()

    (main / "seed").write_text("local edit\n")
    r = kv(run(str(main), "main").stdout)
    check("a tracked edit while behind → dirty, nothing moves",
          r.get("result") == "dirty" and "seed" in r.get("files", "") and head(main) == before)
    git(main, "merge", "-q", "--ff-only", "origin/main")
    r = kv(run(str(main), "main").stdout)
    check("a tracked edit while in sync → dirty, never in-sync", r.get("result") == "dirty")
    git(main, "checkout", "-q", "--", "seed")
    (main / "untracked.log").write_text("x\n")
    check("an untracked file alone stays in-sync", kv(run(str(main), "main").stdout).get("result") == "in-sync")
    (main / "untracked.log").unlink()

    git(main, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
    commit(up, "e"); git(up, "push", "-q", "origin", "main")
    r = kv(run(str(main), "main").stdout)
    check("a narrowed fetch config still compares against the fresh origin/main",
          r.get("result") == "fast-forwarded" and head(main) == head(up))
    git(main, "remote", "set-url", "origin", str(Path(d) / "gone.git"))
    r = kv(run(str(main), "main").stdout)
    check("unreachable origin → fetch-failed", r.get("result") == "fetch-failed")
    git(main, "remote", "remove", "origin")
    check("no origin → no-origin", kv(run(str(main), "main").stdout).get("result") == "no-origin")

    check("usage: missing args is exit 2", run(str(main)).returncode == 2)
    check("a branch name that looks like an option is exit 2",
          run(str(main), "--upload-pack=x").returncode == 2)
    check("a non-repo is exit 2", run(d, "main").returncode == 2)

if FAILS:
    print("FAIL test_sync_main.py:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ok test_sync_main.py")
