#!/usr/bin/env python3
"""Tests for pr-flow's rebase-predict.sh — run standalone or via
scripts/check-structure.py's "plugin tests" check.

The property that matters: `/rebase` proceeds without a menu unless a real
conflict is predicted, so the helper must (1) call the monorepo's everyday
case — both sides touched the same file in different places — clean, (2) call
a real content conflict a conflict and name the file, (3) never read a bad ref
or a missing `merge-tree --write-tree` as either verdict, and (4) leave the
worktree, index and refs exactly as they were. Every repo is a hermetic temp
repo with its own config (no user hooks, no global git config).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "rebase-predict.sh"

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
                          capture_output=True, text=True).stdout


def write(repo, name, text):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def repo_with(base_files):
    repo = Path(tempfile.mkdtemp())
    git(repo, "init", "-q", "-b", "main")
    for name, text in base_files.items():
        write(repo, name, text)
    commit(repo, "root")
    return repo


def predict(repo, *args, env=None):
    r = subprocess.run(["bash", str(SCRIPT), *args], cwd=str(repo),
                       env=env or ENV, capture_output=True, text=True)
    lines = r.stdout.splitlines()
    kv = {}
    files = []
    for line in lines:
        k, _, v = line.partition("=")
        if k == "file":
            files.append(v)
        else:
            kv[k] = v
    return r.returncode, kv, files


def state(repo):
    return (git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
            git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
            git(repo, "rev-parse", "HEAD"))


CHANGELOG = "".join(f"line {i}\n" for i in range(40))

# 1) Same files on both sides, different hunks (the marketplace.json/CHANGELOG
#    case that used to trigger the overlap menu) → clean.
repo = repo_with({"CHANGELOG.md": CHANGELOG, "a.txt": "a\n"})
git(repo, "checkout", "-q", "-b", "task/x")
write(repo, "CHANGELOG.md", "branch entry\n" + CHANGELOG)
write(repo, "a.txt", "a changed\n")
commit(repo, "branch")
git(repo, "checkout", "-q", "main")
write(repo, "CHANGELOG.md", CHANGELOG + "main entry\n")
commit(repo, "main")
git(repo, "checkout", "-q", "task/x")
write(repo, "untracked.txt", "dirty\n")   # a dirty tree must not matter or change
before = state(repo)
rc, kv, files = predict(repo, "main")
check("overlap-clean: exit 0", rc == 0)
check("overlap-clean: prediction=clean", kv.get("prediction") == "clean")
check("overlap-clean: no files", files == [])
check("overlap-clean: repo untouched", state(repo) == before)

# 2) Same lines changed on both sides → conflict, file named, repo untouched.
repo = repo_with({"f.txt": "a\nb\nc\n", "other.txt": "o\n"})
git(repo, "checkout", "-q", "-b", "task/y")
write(repo, "f.txt", "a\nBRANCH\nc\n")
write(repo, "dir with space/g.txt", "branch\n")
commit(repo, "branch")
git(repo, "checkout", "-q", "main")
write(repo, "f.txt", "a\nMAIN\nc\n")
write(repo, "dir with space/g.txt", "main\n")
write(repo, "other.txt", "o2\n")
commit(repo, "main")
git(repo, "checkout", "-q", "task/y")
before = state(repo)
rc, kv, files = predict(repo, "main")
check("conflict: exit 0", rc == 0)
check("conflict: prediction=conflict", kv.get("prediction") == "conflict")
check("conflict: files named", sorted(files) == ["dir with space/g.txt", "f.txt"])
check("conflict: clean file not listed", "other.txt" not in files)
check("conflict: repo untouched", state(repo) == before)
# explicit head argument gives the same answer from another checkout
git(repo, "checkout", "-q", "main")
rc, kv, _ = predict(repo, "main", "task/y")
check("conflict: explicit head", kv.get("prediction") == "conflict")

# 3) Base-side rename of a file the branch edited elsewhere → git follows the
#    rename, so clean (the old --no-renames overlap check flagged this).
repo = repo_with({"old.txt": CHANGELOG})
git(repo, "checkout", "-q", "-b", "task/z")
write(repo, "old.txt", "top\n" + CHANGELOG)
commit(repo, "branch")
git(repo, "checkout", "-q", "main")
git(repo, "mv", "old.txt", "new.txt")
commit(repo, "rename")
git(repo, "checkout", "-q", "task/z")
rc, kv, _ = predict(repo, "main")
check("rename: prediction=clean", kv.get("prediction") == "clean")

# 4) Modify/delete → conflict.
repo = repo_with({"d.txt": "x\n"})
git(repo, "checkout", "-q", "-b", "task/d")
write(repo, "d.txt", "y\n")
commit(repo, "branch")
git(repo, "checkout", "-q", "main")
git(repo, "rm", "-q", "d.txt")
commit(repo, "delete")
git(repo, "checkout", "-q", "task/d")
rc, kv, files = predict(repo, "main")
check("modify/delete: conflict", kv.get("prediction") == "conflict" and files == ["d.txt"])

# 5) Bad refs → unknown, never clean/conflict (merge-tree exits 1 for these too).
rc, kv, _ = predict(repo, "no-such-branch")
check("bad base: exit 0", rc == 0)
check("bad base: unknown", kv.get("prediction") == "unknown"
      and kv.get("reason") == "bad-ref:base")
rc, kv, _ = predict(repo, "main", "no-such-head")
check("bad head: unknown", kv.get("prediction") == "unknown"
      and kv.get("reason") == "bad-ref:head")

# 6) A git without `merge-tree --write-tree` → unknown. Simulated by a git shim
#    that fails merge-tree the way git < 2.38 does (usage error, rc 129).
shim_dir = Path(tempfile.mkdtemp())
real_git = subprocess.run(["bash", "-c", "command -v git"], capture_output=True,
                          text=True, env=ENV).stdout.strip()
(shim_dir / "git").write_text(
    "#!/bin/sh\n"
    'if [ "$1" = merge-tree ]; then echo "usage: git merge-tree" >&2; exit 129; fi\n'
    f'exec "{real_git}" "$@"\n')
(shim_dir / "git").chmod(0o755)
env_old = dict(ENV, PATH=f"{shim_dir}{os.pathsep}{ENV.get('PATH', '')}")
rc, kv, _ = predict(repo, "main", env=env_old)
check("old git: exit 0", rc == 0)
check("old git: unknown", kv.get("prediction") == "unknown"
      and kv.get("reason", "").startswith("merge-tree-unavailable"))

# 6b) A shim that prints a non-oid first token with rc 0 → unknown, not clean.
(shim_dir / "git").write_text(
    "#!/bin/sh\n"
    'if [ "$1" = merge-tree ]; then printf "deadbeef\\0"; exit 0; fi\n'
    f'exec "{real_git}" "$@"\n')
rc, kv, _ = predict(repo, "main", env=env_old)
check("short oid: unknown", kv.get("prediction") == "unknown")

# 6c) A branch that merged the base and resolved a conflict there: the tip
#     merge is clean, but rebase drops the merge and replays the conflicting
#     commit → must be unknown, never clean.
repo = repo_with({"f.txt": "a\nb\nc\n"})
git(repo, "checkout", "-q", "-b", "task/m")
write(repo, "f.txt", "a\nBRANCH\nc\n")
commit(repo, "branch")
git(repo, "checkout", "-q", "main")
write(repo, "f.txt", "a\nMAIN\nc\n")
commit(repo, "main")
git(repo, "checkout", "-q", "task/m")
subprocess.run(["git", "-C", str(repo), "merge", "-q", "main"], env=ENV,
               capture_output=True)
write(repo, "f.txt", "a\nRESOLVED\nc\n")
git(repo, "add", "-A")
git(repo, "commit", "-q", "--no-edit")
git(repo, "checkout", "-q", "main")
write(repo, "g.txt", "unrelated\n")
commit(repo, "unrelated")
git(repo, "checkout", "-q", "task/m")
rc, kv, _ = predict(repo, "main")
check("merge commits: unknown", kv.get("prediction") == "unknown"
      and kv.get("reason") == "merge-commits")

# 7) Usage errors.
rc, _, _ = predict(repo)
check("no args: exit 2", rc == 2)
rc, _, _ = predict(repo, "a", "b", "c")
check("three args: exit 2", rc == 2)

# 8) Not a git repo → unknown, exit 0.
rc, kv, _ = predict(Path(tempfile.mkdtemp()), "main")
check("no repo: unknown", rc == 0 and kv.get("prediction") == "unknown")

if FAILS:
    print("FAIL:\n  " + "\n  ".join(FAILS))
    sys.exit(1)
print("ok")
