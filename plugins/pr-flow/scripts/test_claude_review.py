#!/usr/bin/env python3
"""Tests for claude-review.sh's `has-bot` subcommand — run standalone or via
scripts/check-structure.py's "plugin tests" check.

Scope: the review-bot probe `/open` and `/cycle` branch on. Both directions are
expensive when wrong, in opposite ways: a false "no bot" reroutes a repo that
has a working reviewer to the local route, while a false "yes" comments
`@claude review` into the void and polls until timeout. The probe replaced a
`grep -rlie claude .github/workflows/` inlined verbatim in two SKILL.md files,
so the properties under test are exactly the ones that grep got wrong:

  * it is anchored on the repo ROOT, not $PWD — /cycle may run from a
    subdirectory, or from the main repo while the PR belongs to a worktree;
  * a bot is a workflow that BOTH `uses:` the claude action AND is triggered
    by issue_comment; a push-triggered claude workflow is not one, and a
    comment workflow that merely mentions @claude is "cannot tell";
  * an incidental "claude" (a cache key, a comment) is not a bot;
  * "cannot tell" is its own answer, distinct from "no" — and covers the case
    the probe cannot see locally: a repo served only by the Claude GitHub App,
    which has no workflow dir at all;
  * paths with spaces survive (the first version word-split them and reported
    a working bot as absent), the candidate set is never capped, and an I/O
    failure is "unknown", never "no".

The other subcommands (poll/latest/latest-after) talk to `gh` and are exercised
in production by /cycle and /check.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "claude-review.sh"

FAILS = []

COMMENT_TRIGGERED = """\
name: Claude Review
on:
  issue_comment:
    types: [created]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: anthropics/claude-code-action@v1
"""

PUSH_TRIGGERED = """\
name: Claude Nightly
on:
  push:
    branches: [main]
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""

INCIDENTAL = """\
name: Build
on: [pull_request]
jobs:
  build:
    steps:
      - uses: actions/cache@v4
        with:
          key: claude-plugins-${{ hashFiles('**/lock') }}
"""

LOOSE_MENTION = """\
name: Triage
on:
  issue_comment:
    types: [created]
jobs:
  triage:
    steps:
      # ping @claude manually if this looks like a regression
      - run: echo triage
"""


def check(name, cond):
    if not cond:
        FAILS.append(name)


def run(*args):
    return subprocess.run(
        ["bash", str(SCRIPT), "has-bot", *args], capture_output=True, text=True
    )


def kv(out):
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k] = v
    return d


def repo_with(*workflows, name=None):
    repo = Path(tempfile.mkdtemp())
    if name:
        repo = repo / name
        repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    if workflows:
        wf = repo / ".github" / "workflows"
        wf.mkdir(parents=True)
        for i, body in enumerate(workflows):
            (wf / f"w{i}.yml").write_text(body)
    return repo


# --- cannot tell is its own answer ------------------------------------------
r = kv(run(tempfile.mkdtemp()).stdout)
check("outside a git repo the answer is unknown", r.get("has_bot") == "unknown")
check("unknown is not 'no'", r.get("has_bot") != "no")
check("unknown says why", "git repository" in r.get("why", ""))

# --- no workflows at all: cannot tell, not "no" ------------------------------
# A repo served only by the Claude GitHub App has no workflow dir either, and it
# DOES answer @claude review. Saying "no" here would hard-route it to the local
# review and silently remove the bot path that worked before this probe existed.
r = kv(run(str(repo_with())).stdout)
check("no workflows dir is unknown, not no", r.get("has_bot") == "unknown")
check("and the reason names the App case", "App" in r.get("why", ""))
check("it names the workflows dir it looked at",
      r.get("workflows_dir", "").endswith(".github/workflows"))
empty = repo_with()
(empty / ".github" / "workflows").mkdir(parents=True)
check("an empty workflows dir is the same as none",
      kv(run(str(empty)).stdout).get("has_bot") == "unknown")

# --- a real comment-triggered reviewer --------------------------------------
repo = repo_with(COMMENT_TRIGGERED)
r = kv(run(str(repo)).stdout)
check("a comment-triggered claude workflow is a bot", r.get("has_bot") == "yes")
check("the matched workflow is named", "w0.yml" in r.get("matched", ""))

# The probe must survive being called from anywhere in the repo — this is the
# cwd-relative bug the inline grep had.
sub = repo / "deep" / "nested"
sub.mkdir(parents=True)
check("a subdirectory gets the same answer",
      kv(run(str(sub)).stdout).get("has_bot") == "yes")

# --- a claude workflow that no comment can trigger --------------------------
r = kv(run(str(repo_with(PUSH_TRIGGERED))).stdout)
check("a push-only claude workflow is not a comment bot", r.get("has_bot") == "no")
check("and the reason distinguishes it from 'nothing found'",
      "issue_comment" in r.get("why", ""))
check("the near-miss workflow is still named", "w0.yml" in r.get("matched", ""))

# --- an incidental mention of claude ----------------------------------------
r = kv(run(str(repo_with(INCIDENTAL))).stdout)
check("a cache key mentioning claude is not a bot", r.get("has_bot") == "no")
check("and it reads as 'nothing references the bot'",
      "references" in r.get("why", ""))

# --- a comment workflow that only mentions @claude ---------------------------
# issue_comment + a stray "@claude" used to add up to "yes"; that comments into
# the void and polls for ten minutes. It is "cannot tell", so the caller asks.
r = kv(run(str(repo_with(LOOSE_MENTION))).stdout)
check("a loose @claude mention is unknown, not yes", r.get("has_bot") == "unknown")
check("the loose match is named so the user can look", "w0.yml" in r.get("matched", ""))

# --- paths with spaces and glob characters -----------------------------------
# The first version passed the candidate list through an unquoted variable, so
# "/My Projects/repo" split into two non-existent paths and a working bot was
# reported absent — permanently, on every run.
for name in ("my repo", "repo [v2]", "a*b"):
    r = kv(run(str(repo_with(COMMENT_TRIGGERED, name=name))).stdout)
    check(f"a bot under a path with special chars is found: {name!r}",
          r.get("has_bot") == "yes")

# --- no cap on the candidate set ---------------------------------------------
# Five push-only claude workflows sorted ahead of the real one used to exhaust a
# `head -5` before the comment-trigger test ran.
many = repo_with(*([PUSH_TRIGGERED] * 7), COMMENT_TRIGGERED)
check("the eighth workflow is still examined",
      kv(run(str(many)).stdout).get("has_bot") == "yes")

# --- an unreadable workflows dir is unknown, never no -------------------------
if os.geteuid() != 0:
    locked = repo_with(COMMENT_TRIGGERED)
    wf = locked / ".github" / "workflows"
    os.chmod(wf, 0)
    try:
        r = kv(run(str(locked)).stdout)
        check("an unreadable workflows dir is unknown", r.get("has_bot") == "unknown")
        check("and it says so", "readable" in r.get("why", ""))
    finally:
        os.chmod(wf, 0o755)

# --- a real bot alongside unrelated workflows -------------------------------
r = kv(run(str(repo_with(INCIDENTAL, COMMENT_TRIGGERED, PUSH_TRIGGERED))).stdout)
check("one real bot among several workflows is found", r.get("has_bot") == "yes")
check("only the comment-triggered one is matched",
      "w1.yml" in r.get("matched", "") and "w0.yml" not in r.get("matched", ""))


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("claude-review.sh has-bot: all tests passed")
