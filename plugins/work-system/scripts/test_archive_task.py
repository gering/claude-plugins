#!/usr/bin/env python3
"""Tests for archive-task.sh's `autocommit` subcommand — run standalone
(`python3 test_archive_task.py`) or via scripts/check-structure.py's
"plugin tests" check.

Scope: just the flag-routing logic behind /close step 10's opt-in
(`.claude/work-system-close-autocommit`) — the part that is actually
script-testable. The `archive`/`commit-push` subcommands are exercised
manually via /close; they need a real git repo and are covered by the
design rationale in `.claude/knowledge/features/task-archiving-on-close.md`.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "archive-task.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def run(*args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True
    )


def flag_path(repo):
    return repo / ".claude" / "work-system-close-autocommit"


with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    repo.mkdir()

    # --- get: no flag file -> disabled by default ------------------------- #
    r = run("autocommit", "get", str(repo))
    check("no flag file -> enabled=no", r.stdout.strip() == "enabled=no")
    check("get exits 0", r.returncode == 0)

    # --- set: writes the file, creates .claude/ as needed ------------------ #
    r = run("autocommit", "set", str(repo))
    check("set exits 0", r.returncode == 0)
    check("set creates the flag file", flag_path(repo).is_file())
    check("set writes 'yes'", flag_path(repo).read_text().strip() == "yes")

    r = run("autocommit", "get", str(repo))
    check("after set -> enabled=yes", r.stdout.strip() == "enabled=yes")

    # --- unset: removes the file, reverts to disabled ----------------------- #
    r = run("autocommit", "unset", str(repo))
    check("unset exits 0", r.returncode == 0)
    check("unset removes the flag file", not flag_path(repo).exists())
    check("after unset -> enabled=no", run("autocommit", "get", str(repo)).stdout.strip() == "enabled=no")

    # --- content variants: only yes/true (whitespace-trimmed) count -------- #
    flag_path(repo).parent.mkdir(parents=True, exist_ok=True)
    for content, expected in [
        ("yes\n", "yes"),
        ("true\n", "yes"),
        (" yes \n", "yes"),
        ("no\n", "no"),
        ("1\n", "no"),
        ("", "no"),
    ]:
        flag_path(repo).write_text(content)
        got = run("autocommit", "get", str(repo)).stdout.strip()
        check(f"content {content!r} -> enabled={expected}", got == f"enabled={expected}")

    # --- a repo that never had .claude/ at all ------------------------------ #
    bare = Path(tmp) / "bare"
    bare.mkdir()
    check("no repo dir at all -> enabled=no", run("autocommit", "get", str(bare)).stdout.strip() == "enabled=no")

    # --- usage errors -------------------------------------------------------- #
    check("get missing repo -> exit 2", run("autocommit", "get").returncode == 2)
    check("set missing repo -> exit 2", run("autocommit", "set").returncode == 2)
    check("unknown op -> exit 2", run("autocommit", "bogus", str(repo)).returncode == 2)
    check("bare autocommit -> exit 2", run("autocommit").returncode == 2)


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("archive-task.sh autocommit: all tests passed")
