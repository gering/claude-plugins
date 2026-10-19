#!/usr/bin/env python3
"""Tests for review-record.sh — swarm's soft-coupled hand-off of a finished
local review to work-system's merge gate (mandate.sh reviewed/fixed).

The properties under test: without work-system, without a mandate, or with a
work-system too old to know the verbs, recording is a reported no-op (exit 3),
never an error that would stop a review; inside a lane it records exactly what
mandate.sh records, so `covered` then sees the reviewed state.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
REAL_MANDATE = HERE.parent.parent / "work-system" / "scripts" / "mandate.sh"
FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def layout(work_system="real"):
    """A fake plugin tree: <root>/swarm (+ optionally <root>/work-system)."""
    root = Path(tempfile.mkdtemp())
    sw = root / "swarm" / "scripts"
    sw.mkdir(parents=True)
    for f in ("review-record.sh", "lib-work-system.sh"):
        (sw / f).write_text((HERE / f).read_text())
    if work_system:
        ws = root / "work-system" / "scripts"
        ws.mkdir(parents=True)
        (ws / "mandate.sh").write_text(
            REAL_MANDATE.read_text() if work_system == "real"
            else 'echo "usage: mandate.sh {path|lane|show|init|allows|round|actions}" >&2; exit 2\n')
    return root


ENV = dict(os.environ, HOME=tempfile.mkdtemp(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
ENV.pop("CLAUDE_PLUGIN_ROOT", None)


def run(root, *args):
    return subprocess.run(["bash", str(root / "swarm" / "scripts" / "review-record.sh"), *args],
                          capture_output=True, text=True, timeout=60, env=ENV)


def git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True,
                          check=True, env=ENV).stdout.strip()


repo = Path(tempfile.mkdtemp())
git(repo, "init", "-q", "-b", "main")
(repo / "a").write_text("a\n")
git(repo, "add", "-A")
git(repo, "commit", "-qm", "a")
lane = Path(tempfile.mkdtemp()) / "lane"
git(repo, "worktree", "add", "-q", "-b", "task/x", str(lane))

r = run(layout(None), "reviewed", "--head", "HEAD", str(lane))
check("no work-system is a reported no-op",
      r.returncode == 3 and "reason=no-work-system" in r.stdout)
r = run(layout("old"), "reviewed", "--head", "HEAD", str(lane))
check("an old work-system is a reported no-op",
      r.returncode == 3 and "reason=old-work-system" in r.stdout)
root = layout()
r = run(root, "reviewed", "--head", "HEAD", str(lane))
check("no mandate is a reported no-op", r.returncode == 3 and "reason=no-mandate" in r.stdout)

mandate = root / "work-system" / "scripts" / "mandate.sh"
subprocess.run(["bash", str(mandate), "init", str(lane), "allow=commit", "authorized_by=user",
                "task=x"], check=True, capture_output=True, env=ENV)
(lane / "a").write_text("b\n")
git(lane, "commit", "-qam", "b")
head = git(lane, "rev-parse", "HEAD")
r = run(root, "reviewed", "--head", head, str(lane))
check("inside a lane the review is recorded",
      r.returncode == 0 and "reviewed_recorded=yes" in r.stdout)
r = subprocess.run(["bash", str(mandate), "covered", str(lane)], capture_output=True, text=True,
                   env=ENV)
check("and covers the committed head", r.returncode == 0 and "covered=yes" in r.stdout)
(lane / "a").write_text("c\n")
r = run(root, "fixed", "--after", head, str(lane))
check("fixed passes through", r.returncode == 0 and "fixed_recorded=yes" in r.stdout)
r = run(root, "fixed", "--after", head, str(lane))
check("a refused fixed keeps its exit 1 and reason",
      r.returncode == 1 and "reason=already-recorded" in r.stdout)
check("an unknown verb is a usage error", run(root, "covered").returncode == 2)
check("reviewed without --head is a usage error", run(root, "reviewed", str(lane)).returncode == 2)
check("fixed without --after is a usage error", run(root, "fixed", str(lane)).returncode == 2)

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("review-record.sh: all tests passed")
