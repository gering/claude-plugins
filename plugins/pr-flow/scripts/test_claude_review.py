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
  * a workflow needs a comment trigger to answer `@claude review`; a
    push-triggered claude workflow is not a bot for this purpose;
  * an incidental "claude" (a cache key, a comment) is not a bot;
  * "cannot tell" is its own answer, distinct from "no".

The other subcommands (poll/latest/latest-after) talk to `gh` and are exercised
in production by /cycle and /check.
"""
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


def repo_with(*workflows):
    repo = Path(tempfile.mkdtemp())
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

# --- no workflows at all ----------------------------------------------------
r = kv(run(str(repo_with())).stdout)
check("no workflows dir means no bot", r.get("has_bot") == "no")
check("it names the workflows dir it looked at",
      r.get("workflows_dir", "").endswith(".github/workflows"))

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
