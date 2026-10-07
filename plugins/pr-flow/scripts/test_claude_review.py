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
    by issue_comment, as a DIRECT child of the top-level `on:` — not a branch
    name, not a workflow input that happens to be called issue_comment;
  * a push- or pull_request-triggered claude workflow is not a comment bot, but
    it is not proof of one's absence either: the App can serve the same repo;
  * an incidental "claude" (a cache key, a comment) is not a bot — but it is
    not proof of ABSENCE either: a scan can prove `yes` and never `no`, since
    the Claude GitHub App answers comments with no workflow file at all;
  * the probe reads the DEFAULT BRANCH, because that is the ref GitHub runs an
    issue_comment workflow from — not the checked-out task branch;
  * "cannot tell" is its own answer, distinct from "no" — and covers the case
    the probe cannot see locally: a repo served only by the Claude GitHub App,
    which has no workflow dir at all; a custom trigger_phrase; a job that
    delegates to a reusable workflow;
  * matching is STRUCTURAL: a TODO comment, a `run:` heredoc, or a file in a
    subdirectory GitHub never reads must not add up to a working bot;
  * paths with spaces survive (the first version word-split them and reported
    a working bot as absent), the candidate set is never capped, and an I/O
    failure is "unknown", never "no".

The `route` section below covers the remembered answer layered on top, and
`poll` / `latest-after` are run against a stub `gh` that executes their real
`--jq` filters. `latest` is exercised in production by /cycle and /check.
"""
import json
import os
import re
import shutil
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


def sh(*args, env=None, cwd=None):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, cwd=cwd)


def run(*args):
    return sh("has-bot", *args)


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
# Not a bot — and still not proof that none exists. Anthropic ships a
# `pull_request`-triggered review workflow, and a repo can run that AND be served
# by the GitHub App for `@claude` mentions. `no` there would permanently reroute
# a working bot, so this is `unknown` like every other can't-tell.
r = kv(run(str(repo_with(PUSH_TRIGGERED))).stdout)
check("a push-only claude workflow is not a comment bot", r.get("has_bot") != "yes")
check("but it is 'cannot tell', not 'no'", r.get("has_bot") == "unknown")
check("and the reason distinguishes it from 'nothing found'",
      "issue_comment" in r.get("why", ""))
check("the near-miss workflow is still named", "w0.yml" in r.get("matched", ""))

# --- an incidental mention of claude ----------------------------------------
# Not a bot — but not provably bot-LESS either. The Claude GitHub App answers
# `@claude review` with no workflow file of its own, so unrelated CI in the same
# directory says nothing about whether it is installed. Answering `no` here
# rerouted a working bot permanently, and `no` is the one answer no consumer
# asks about. Verified in this very repo, which is served by the App and carries
# only structure-checks.yml.
r = kv(run(str(repo_with(INCIDENTAL))).stdout)
check("a cache key mentioning claude is not a yes", r.get("has_bot") != "yes")
check("nothing-references-the-bot is 'cannot tell', not 'no'",
      r.get("has_bot") == "unknown")
check("and the reason names the App as the thing it cannot see",
      "App" in r.get("why", ""))

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

# --- structural matching: text is not configuration --------------------------
# Substring greps used to say "yes" to all of these — and the caller then
# commented into the void and polled for ten minutes.
COMMENT_ONLY = """\
name: Claude
on:
  pull_request:
# TODO: also trigger on issue_comment
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""
r = kv(run(str(repo_with(COMMENT_ONLY))).stdout)
check("an issue_comment in a YAML comment is not a trigger", r.get("has_bot") != "yes")
check("and it is the push-only case", "issue_comment" in r.get("why", ""))

HEREDOC = """\
name: Docs
on:
  issue_comment:
    types: [created]
jobs:
  docs:
    steps:
      - run: |
          cat <<EOF > example.yml
            - uses: anthropics/claude-code-action@v1
          EOF
"""
r = kv(run(str(repo_with(HEREDOC))).stdout)
check("a uses: inside a run: block is not a step", r.get("has_bot") != "yes")
check("but the mention still makes it 'cannot tell', not 'no'",
      r.get("has_bot") == "unknown")

CUSTOM_PHRASE = """\
name: Claude Review
on:
  issue_comment:
    types: [created]
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          trigger_phrase: "/review"
"""
r = kv(run(str(repo_with(CUSTOM_PHRASE))).stdout)
check("a custom trigger_phrase is unknown, not yes", r.get("has_bot") == "unknown")
check("and the reason names the phrase", "trigger_phrase" in r.get("why", ""))
DEFAULT_PHRASE = CUSTOM_PHRASE.replace('"/review"', '"@claude"')
check("an explicit @claude trigger_phrase is still a bot",
      kv(run(str(repo_with(DEFAULT_PHRASE))).stdout).get("has_bot") == "yes")

REUSABLE = """\
name: Review
on:
  issue_comment:
    types: [created]
jobs:
  review:
    uses: my-org/shared-ci/.github/workflows/claude-review.yml@main
"""
r = kv(run(str(repo_with(REUSABLE))).stdout)
check("a reusable-workflow delegate is unknown, not no", r.get("has_bot") == "unknown")
check("and the reason says so", "reusable" in r.get("why", ""))

# GitHub reads .github/workflows itself, never a subdirectory.
deep = repo_with()
sub = deep / ".github" / "workflows" / "archive"
sub.mkdir(parents=True)
(sub / "old.yml").write_text(COMMENT_TRIGGERED)
r = kv(run(str(deep)).stdout)
check("a workflow in a subdirectory is not examined", r.get("has_bot") != "yes")
(deep / ".github" / "workflows" / "ci.yml").write_text(INCIDENTAL)
check("nor does it make a plain-CI repo a bot repo",
      kv(run(str(deep)).stdout).get("has_bot") != "yes")

# A trailing comment on the real step must not hide it.
COMMENTED_STEP = COMMENT_TRIGGERED.replace(
    "claude-code-action@v1", "claude-code-action@v1   # pinned")
check("a trailing comment on the uses: line is fine",
      kv(run(str(repo_with(COMMENTED_STEP))).stdout).get("has_bot") == "yes")
CRLF = COMMENT_TRIGGERED.replace("\n", "\r\n")
check("a CRLF workflow is read",
      kv(run(str(repo_with(CRLF))).stdout).get("has_bot") == "yes")

# --- every path emits the same four keys -------------------------------------
KEYS = {"has_bot", "why", "workflows_dir", "matched"}
for label, path in (("yes", str(repo_with(COMMENT_TRIGGERED))),
                    ("unknown (push-only claude workflow)", str(repo_with(PUSH_TRIGGERED))),
                    ("unknown (nothing references the bot)", str(repo_with(INCIDENTAL))),
                    ("unknown (no workflows dir)", str(repo_with())),
                    ("not a git repo", tempfile.mkdtemp())):
    keys = {l.partition("=")[0] for l in run(path).stdout.splitlines()}
    check(f"all four keys on the {label} path", keys == KEYS)
    check(f"and it exits 0 on the {label} path", run(path).returncode == 0)

# --- a local scan can prove presence, never absence --------------------------
# Every non-`yes` verdict is `unknown`: the GitHub App answers `@claude review`
# with no workflow file, so nothing on disk can rule it out. `no` stays in the
# vocabulary for a future authoritative signal, but is not emitted today.
for fixture in (PUSH_TRIGGERED, INCIDENTAL, COMMENT_ONLY):
    r = kv(run(str(repo_with(fixture))).stdout)
    check("a non-bot workflow set never answers 'no'", r.get("has_bot") != "no")
    check("it answers 'cannot tell'", r.get("has_bot") == "unknown")
    check("and it names the ref it inspected", "inspected" in r.get("why", ""))

# --- the probe reads the DEFAULT BRANCH, not the checkout --------------------
# GitHub runs an issue_comment workflow from the default branch. A task branch
# that adds one would otherwise probe `yes`, and /cycle would comment into the
# void and poll for ten minutes; one that removes it would probe `no`.
def repo_with_committed(*workflows, branch=None):
    repo = repo_with(*workflows)
    (repo / "README.md").write_text("x\n")   # a commit needs content
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    if branch:
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)
    return repo

base = repo_with_committed(INCIDENTAL, branch="task/add-bot")
wf = base / ".github" / "workflows"
(wf / "claude.yml").write_text(COMMENT_TRIGGERED)
subprocess.run(["git", "-C", str(base), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(base), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "add bot"], check=True)
r = kv(run(str(base)).stdout)
check("a bot added only on the task branch is not reported as live",
      r.get("has_bot") != "yes")
check("and the probed ref is named in workflows_dir",
      r.get("workflows_dir", "").startswith("refs/heads/"))
check("the ref is fully qualified, so a same-named tag cannot shadow it",
      ":" in r.get("workflows_dir", "")
      and r.get("workflows_dir", "").split(":")[0].startswith("refs/"))

gone = repo_with_committed(COMMENT_TRIGGERED, branch="task/remove-bot")
(gone / ".github" / "workflows" / "w0.yml").unlink()
subprocess.run(["git", "-C", str(gone), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(gone), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "drop bot"], check=True)
check("a bot removed only on the task branch is still found on the default branch",
      kv(run(str(gone)).stdout).get("has_bot") == "yes")

live = repo_with_committed(COMMENT_TRIGGERED)
check("on the default branch itself the answer is unchanged",
      kv(run(str(live)).stdout).get("has_bot") == "yes")
sub = live / "deep" / "nested"
sub.mkdir(parents=True)
check("and a subdirectory still gets the same answer",
      kv(run(str(sub)).stdout).get("has_bot") == "yes")
# A committed workflow in a subdirectory GitHub ignores stays ignored on the ref
# path too (ls-tree is non-recursive, and a tree entry is not a *.yml name).
arch = repo_with_committed()
awf = arch / ".github" / "workflows" / "archive"
awf.mkdir(parents=True)
(awf / "old.yml").write_text(COMMENT_TRIGGERED)
subprocess.run(["git", "-C", str(arch), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(arch), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "archive"], check=True)
check("a committed subdirectory workflow is not read from the ref either",
      kv(run(str(arch)).stdout).get("has_bot") != "yes")

# --- quoted YAML keys ---------------------------------------------------------
# `"issue_comment":` and `"on":` are valid YAML; an unquoted-only pattern
# answered no for a bot that works.
QUOTED = """\
name: Claude Review
"on":
  "issue_comment":
    types: [created]
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""
check("a quoted issue_comment key is still a trigger",
      kv(run(str(repo_with(QUOTED))).stdout).get("has_bot") == "yes")
FLOW = """\
name: Claude Review
on: [issue_comment, pull_request]
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""
check("flow-style on: [issue_comment] is a trigger",
      kv(run(str(repo_with(FLOW))).stdout).get("has_bot") == "yes")

# --- issue_comment counts only under the top-level `on:` ---------------------
# Matching it at any indentation made an ordinary step input look like a comment
# trigger, so /cycle commented into the void and polled to the timeout.
NESTED_INPUT = """\
name: Claude Nightly
on:
  push:
    branches: [main]
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          issue_comment: true
"""
r = kv(run(str(repo_with(NESTED_INPUT))).stdout)
check("an issue_comment step INPUT is not a trigger", r.get("has_bot") != "yes")
check("and it reads as the push-only case", "issue_comment" in r.get("why", ""))

NESTED_ENV = """\
name: Claude Nightly
on: [push]
jobs:
  review:
    env:
      issue_comment: "no"
    steps:
      - uses: anthropics/claude-code-action@v1
"""
check("an issue_comment env entry is not a trigger either",
      kv(run(str(repo_with(NESTED_ENV))).stdout).get("has_bot") != "yes")

# The real thing still works in both spellings, one level under `on:`.
check("a nested issue_comment under on: is still a trigger",
      kv(run(str(repo_with(COMMENT_TRIGGERED))).stdout).get("has_bot") == "yes")
ON_LIST = """\
name: Claude Review
on:
  - issue_comment
  - pull_request
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""
check("a block-sequence on: list is a trigger",
      kv(run(str(repo_with(ON_LIST))).stdout).get("has_bot") == "yes")

# --- the probed ref is fully qualified --------------------------------------
# `rev-parse main` resolves a TAG named main ahead of the branch, so an
# auto-fetched tag used to decide the answer for the whole repo.
shadow = repo_with_committed(COMMENT_TRIGGERED)
subprocess.run(["git", "-C", str(shadow), "rm", "-q", "-r", ".github"], check=True)
subprocess.run(["git", "-C", str(shadow), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "drop"], check=True)
# A tag named like the default branch, pointing at the bot-less commit.
default = subprocess.run(["git", "-C", str(shadow), "symbolic-ref", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
subprocess.run(["git", "-C", str(shadow), "tag", default + "-shadow"], check=True)
subprocess.run(["git", "-C", str(shadow), "reset", "-q", "--hard", "HEAD~1"], check=True)
subprocess.run(["git", "-C", str(shadow), "tag", default], check=True)
subprocess.run(["git", "-C", str(shadow), "reset", "-q", "--hard", default + "-shadow"], check=True)
r = kv(run(str(shadow)).stdout)
check("a tag named like the branch does not decide the answer",
      r.get("workflows_dir", "").startswith("refs/heads/"))
check("the branch content is what was read", r.get("has_bot") != "yes")

# --- the default branch comes from the branch's own upstream remote ----------
# A repo tracking `upstream` whose default is `trunk` was probed against a stale
# `origin/main`, i.e. a branch GitHub never runs the workflow from.
up = repo_with_committed(INCIDENTAL)
subprocess.run(["git", "-C", str(up), "update-ref",
                "refs/remotes/origin/main", "HEAD"], check=True)
subprocess.run(["git", "-C", str(up), "checkout", "-q", "-b", "work"], check=True)
wf = up / ".github" / "workflows"
(wf / "claude.yml").write_text(COMMENT_TRIGGERED)
subprocess.run(["git", "-C", str(up), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(up), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "bot on trunk"], check=True)
subprocess.run(["git", "-C", str(up), "update-ref",
                "refs/remotes/upstream/trunk", "HEAD"], check=True)
subprocess.run(["git", "-C", str(up), "symbolic-ref",
                "refs/remotes/upstream/HEAD", "refs/remotes/upstream/trunk"], check=True)
subprocess.run(["git", "-C", str(up), "config", "branch.work.remote", "upstream"], check=True)
r = kv(run(str(up)).stdout)
check("the branch's own upstream remote decides the default branch",
      r.get("workflows_dir", "").startswith("refs/remotes/upstream/trunk:"))
check("and the bot on that branch is found", r.get("has_bot") == "yes")
check("a resolved default branch is not reported as a guess",
      "guess" not in r.get("why", ""))

# When nothing records a default branch, say that the ref was guessed rather
# than presenting it as authoritative.
g = repo_with_committed(INCIDENTAL)
check("a fallback ref is labelled as guessed",
      "guess" in kv(run(str(g)).stdout).get("why", ""))

# --- names with spaces and non-ASCII survive the listing ---------------------
# The first version of the ref path passed NUL-delimited names through a command
# substitution, which drops NUL — the loop saw no files and a working bot was
# reported absent. These names also exercise git's path quoting.
for name in ("my workflow.yml", "wörkflow.yml"):
    sp = repo_with()
    (sp / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (sp / ".github" / "workflows" / name).write_text(COMMENT_TRIGGERED)
    (sp / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(sp), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(sp), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    check(f"a committed workflow named {name!r} is read from the ref",
          kv(run(str(sp)).stdout).get("has_bot") == "yes")

# --- issue_comment must be a DIRECT child of `on:` ---------------------------
# Matching anywhere below `on:` made a branch NAME and a workflow input read as
# comment triggers, so /cycle commented into the void and polled to the timeout.
BRANCH_NAMED = """\
name: Claude Nightly
on:
  push:
    branches: [issue_comment]
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""
check("a BRANCH named issue_comment is not a trigger",
      kv(run(str(repo_with(BRANCH_NAMED))).stdout).get("has_bot") != "yes")

CALL_INPUT = """\
name: Reusable Claude
on:
  workflow_call:
    inputs:
      issue_comment:
        type: boolean
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""
check("a workflow_call INPUT named issue_comment is not a trigger",
      kv(run(str(repo_with(CALL_INPUT))).stdout).get("has_bot") != "yes")

FLOW_BRANCH = """\
name: Claude Nightly
on: {push: {branches: [issue_comment]}}
jobs:
  review:
    steps:
      - uses: anthropics/claude-code-action@v1
"""
check("flow style naming a branch issue_comment is not a trigger",
      kv(run(str(repo_with(FLOW_BRANCH))).stdout).get("has_bot") != "yes")

# The real trigger still reads, in every spelling.
for fixture, label in ((COMMENT_TRIGGERED, "block mapping"),
                       (ON_LIST, "block sequence"),
                       (FLOW, "flow list"),
                       (QUOTED, "quoted keys")):
    check(f"a real issue_comment trigger still reads: {label}",
          kv(run(str(repo_with(fixture))).stdout).get("has_bot") == "yes")



# --- route: setting, memory, evidence ----------------------------------------
# `has_bot=unknown` is settled by /cycle's poll. Before `route`, nothing kept the
# answer, so every round and every lane of a bot-less repo posted `@claude review`
# and polled ten minutes again (PR #69, #70). The properties under test:
#   * `review.route` pins the route; `auto` (the default) keeps today's try-once;
#   * a recorded timeout is shared by every worktree and never committed;
#   * it is reversible: a later Claude bot reply, has_bot=yes,
#     review.route=github and route-clear drop it — but --offline never writes;
#   * evidence is conservative (only mentions the bot would obey) and never
#     remembered;
#   * the poll books a timeout itself, and never once any Claude comment appeared.
def route(*args, env=None, cwd=None):
    return kv(sh("route", *args, env=env, cwd=cwd).stdout)


def common_dir(repo):
    return Path(sh_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def sh_git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


def memory_file(repo):
    return common_dir(repo) / "pr-flow" / "review-route"


repo = repo_with_committed()
r = route(str(repo), "--offline")
check("auto + unknown tries the bot", r.get("route") == "bot")
check("and asks the caller to record a timeout", r.get("record") == "yes")
check("the default route is named as such", r.get("source") == "default")
check("has_bot vocabulary is unchanged", r.get("has_bot") == "unknown")

(repo / ".pr-flow.toml").write_text('[review]\nroute = "local"\n')
r = route(str(repo), "--offline")
check("review.route = local skips the bot", r.get("route") == "local")
check("and says it came from the setting",
      r.get("source") == "setting" and ".pr-flow.toml" in r.get("why", ""))
check("a pinned route skips the probe", r.get("has_bot") == "")
rec = kv(sh("route-record", str(repo), "--pr", "7").stdout)
check("only auto records a timeout", rec.get("recorded") == "no")

(repo / ".pr-flow.toml").write_text('[review]\nroute = "sometimes"\n')
r = route(str(repo), "--offline")
check("an invalid route falls back to the schema default", r.get("route") == "bot")
check("and the note says why", "review.route must be one of" in r.get("why", ""))
(repo / ".pr-flow.toml").unlink()

target = Path(tempfile.mkdtemp()) / "outside.toml"
target.write_text('[review]\nroute = "local"\n')
(repo / ".pr-flow.toml").symlink_to(target)
r = route(str(repo), "--offline")
check("a symlinked .pr-flow.toml is not followed", r.get("route") == "bot")
check("and the note names the symlink", "symlink" in r.get("why", ""))
(repo / ".pr-flow.toml").unlink()

# route-record argument hygiene: usage errors are exit 2, not a silent exit 1.
check("route-record needs a PR number", sh("route-record", str(repo)).returncode == 2)
check("a trailing --pr is a usage error", sh("route-record", str(repo), "--pr").returncode == 2)
check("PR 0 is refused", sh("route-record", str(repo), "--pr", "0").returncode == 2)
check("a zero-padded PR is refused", sh("route-record", str(repo), "--pr", "012").returncode == 2)

# A timed-out poll is remembered — for every worktree of the repo.
rec = kv(sh("route-record", str(repo), "--pr", "70").stdout)
check("auto records a timeout", rec.get("recorded") == "yes")
check("the record lives in the git common dir, outside the tree",
      Path(rec.get("file", "")) == memory_file(repo) and not (repo / "pr-flow").exists())
r = route(str(repo), "--offline")
check("a remembered timeout routes local", r.get("route") == "local")
check("from memory", r.get("source") == "memory")
check("the report names the PR", "PR #70" in r.get("why", ""))
check("the report names the date", re.search(r"no bot answered on \d{4}-\d\d-\d\d",
                                             r.get("why", "")) is not None)
check("memory is not recorded again", r.get("record") == "no")
wt = Path(tempfile.mkdtemp()) / "lane"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "task/x", str(wt)],
               check=True)
check("another lane of the same repo shares the answer",
      route(str(wt), "--offline").get("route") == "local")
check("git status stays clean", sh_git(repo, "status", "--porcelain") == "")

# --branch resolves the lane itself — no work-system needed, and a wrapper's
# stdout can no longer stand in for a directory.
r = route("--offline", "--branch", "task/x", cwd=repo)
check("--branch resolves the worktree holding the branch",
      Path(r.get("lane", "")).resolve() == wt.resolve() and r.get("route") == "local")
check("and says the branch answered", r.get("lane_source") == "branch")
check("a branch no worktree holds says cwd",
      route("--offline", "--branch", "nope", cwd=repo).get("lane_source") == "cwd")
check("--branch without a value is a usage error",
      sh("route", "--branch").returncode == 2)

# Reversible — but only from a networked caller. --offline is read-only.
(repo / ".pr-flow.toml").write_text('[review]\nroute = "github"\n')
r = route(str(repo), "--offline")
check("review.route = github overrides the memory", r.get("route") == "bot")
check("but never records", r.get("record") == "no")
check("and a pin skips the probe", r.get("has_bot") == "")
check("--offline does not erase the shared memory", memory_file(repo).exists())
(repo / ".pr-flow.toml").unlink()

# Stub `gh` that runs the REAL --jq expression over a fixture, so the filters
# (bot accounts, author_association, reply timestamps) are what is under test.
stub = Path(tempfile.mkdtemp())
(stub / "gh").write_text(r"""#!/usr/bin/env bash
[ "$1" = auth ] && exit 0
expr=""
while [ $# -gt 0 ]; do [ "$1" = --jq ] && expr="$2"; shift; done
exec jq -r "$expr" "$GH_FIXTURE"
""")
(stub / "gh").chmod(0o755)
fixtures = Path(tempfile.mkdtemp())


def gh_env(name, data):
    f = fixtures / f"{name}.json"
    f.write_text(json.dumps(data))
    return dict(os.environ, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", GH_FIXTURE=str(f))


def comment(login, body, when="2026-01-01T00:00:00Z", kind="User", assoc="OWNER"):
    return {"user": {"login": login, "type": kind}, "body": body,
            "created_at": when, "author_association": assoc}


asked = [comment("owner", "@claude review", f"2026-01-0{i}T00:00:00Z") for i in (1, 2)]
env = gh_env("asked", asked)
check("with a memory and no newer bot reply, local stays",
      route(str(repo), env=env).get("source") == "memory")

stale_reply = asked + [comment("claude[bot]", "**Claude finished**", "2000-01-01T00:00:00Z", "Bot")]
impostor = asked + [comment("claude-ci-notifier[bot]", "done", "2999-01-01T00:00:00Z", "Bot")]
check("only the Claude App's own login clears the record",
      route(str(repo), env=gh_env("impostor", impostor)).get("source") == "memory")
check("a bot reply OLDER than the record does not clear it",
      route(str(repo), env=gh_env("old", stale_reply)).get("source") == "memory")

fresh = asked + [comment("claude[bot]", "**Claude finished**", "2999-01-01T00:00:00Z", "Bot")]
r = route(str(repo), env=gh_env("fresh", fresh))
check("a Claude bot reply after the record switches back to the bot",
      r.get("route") == "bot" and r.get("source") == "default")
check("and says so in the report", "remembered no-bot cleared" in r.get("why", ""))
check("and the record is gone", not memory_file(repo).exists())

r = route(str(repo), env=env)
check("repeated unanswered @claude mentions route local", r.get("route") == "local")
check("as evidence, not proof", r.get("source") == "evidence"
      and "not proof" in r.get("why", "") and r.get("has_bot") == "unknown")
check("evidence is never remembered", not memory_file(repo).exists())
check("any bot comment defeats the evidence",
      route(str(repo), env=gh_env("bot", asked + [comment("ci[bot]", "green", kind="Bot")]))
      .get("route") == "bot")
check("a single mention is not enough",
      route(str(repo), env=gh_env("one", asked[:1])).get("route") == "bot")
outsiders = [comment("rando", "@claude review", assoc="NONE") for _ in range(5)]
check("mentions by outsiders the bot would ignore do not count",
      route(str(repo), env=gh_env("outsiders", outsiders)).get("route") == "bot")
check("--offline never asks gh", route(str(repo), "--offline", env=env).get("route") == "bot")

# has_bot=yes beats a remembered timeout; only a networked call clears it.
botrepo = repo_with(COMMENT_TRIGGERED)
sh("route-record", str(botrepo), "--pr", "3")
r = route(str(botrepo), "--offline")
check("has_bot=yes beats a remembered timeout", r.get("route") == "bot" and r.get("source") == "probe")
check("--offline leaves the record alone", memory_file(botrepo).exists())
route(str(botrepo), env=gh_env("none", []))
check("a networked route clears it", not memory_file(botrepo).exists())
# The bot installed later as a workflow: the probe outranks old unanswered asks
# too, so the switch back needs no @claude mention from a route that posts none.
r = route(str(botrepo), env=env)
check("has_bot=yes beats the evidence", r.get("route") == "bot" and r.get("source") == "probe")

# A reply in the very second the record was written is not "older".
sh("route-record", str(repo), "--pr", "8")
at = next(l.split("=", 1)[1] for l in memory_file(repo).read_text().splitlines()
          if l.startswith("recorded_at="))
r = route(str(repo), env=gh_env("same", asked + [comment("claude[bot]", "done", at, "Bot")]))
check("a same-second Claude reply clears the record", not memory_file(repo).exists())

sh("route-record", str(repo), "--pr", "71")
sh("route-clear", str(wt))
check("route-clear from any lane forgets it",
      route(str(repo), "--offline").get("source") == "default")

# A tampered record is ignored, not echoed into the round report.
memory_file(repo).write_text("no_bot=yes\nrecorded_at=$(touch /tmp/pwn)\npr=1\n")
check("a malformed record is ignored",
      route(str(repo), "--offline").get("source") == "default")
memory_file(repo).unlink()


# --- poll --record: the script books the timeout, never on a slow bot --------
def poll(name, comments, *extra):
    env = gh_env(name, {"comments": comments})
    return subprocess.run(["bash", str(SCRIPT), "poll", "5", "2026-01-01T00:00:00Z",
                           "--max", "1", "--interval", "0", *extra],
                          capture_output=True, text=True, cwd=repo, env=env)


def pr_comment(login, body, when="2026-02-01T00:00:00Z"):
    return {"author": {"login": login}, "body": body, "createdAt": when}


p = poll("silent", [], "--record", str(repo))
check("a silent timeout exits 1", p.returncode == 1 and "TIMEOUT" in p.stderr)
check("and is booked by the script", "route_recorded=yes" in p.stderr
      and memory_file(repo).exists())
memory_file(repo).unlink()

p = poll("slow", [pr_comment("claude", "Claude Code is working…")], "--record", str(repo))
check("a bot that acknowledged but was slow is not recorded as absent",
      "route_recorded=no" in p.stderr and not memory_file(repo).exists())
p = poll("error", [pr_comment("claude", "**Claude encountered an error**")], "--record", str(repo))
check("a bot that answered with an error is not recorded as absent",
      "route_recorded=no" in p.stderr and not memory_file(repo).exists())
p = poll("notrepo", [], "--record", tempfile.mkdtemp())
check("a non-repo --record dir still reports one route_recorded line",
      "route_recorded=no (--record dir is not a git repository)" in p.stderr)
p = subprocess.run(["bash", str(SCRIPT), "poll", "5", '1900" or true or "', "--max", "1",
                    "--interval", "0"], capture_output=True, text=True, cwd=repo,
                   env=gh_env("inject", {"comments": [pr_comment("claude", "**Claude finished**",
                                                                 "2000-01-01T00:00:00Z")]}))
check("a SINCE that would rewrite the jq filter is refused", p.returncode == 2
      and "Claude finished" not in p.stdout)

p = subprocess.run(["bash", str(SCRIPT), "latest-after", "5", '1900" or true or "'],
                   capture_output=True, text=True, cwd=repo,
                   env=gh_env("inject2", {"comments": [pr_comment("claude", "**Claude finished**",
                                                                  "2000-01-01T00:00:00Z")]}))
check("latest-after refuses the same SINCE injection", p.returncode == 2
      and "Claude finished" not in p.stdout)
for bad in (["--max", "0"], ["--max", "x"], ["--interval", "-1"], ["--max"]):
    p = subprocess.run(["bash", str(SCRIPT), "poll", "5", "2026-01-01T00:00:00Z", *bad,
                        "--record", str(repo)], capture_output=True, text=True, cwd=repo,
                       env=gh_env("badmax", {"comments": []}))
    check(f"poll {' '.join(bad)} is a usage error, never a booked timeout",
          p.returncode == 2 and not memory_file(repo).exists())

# A write that fails must not claim success (errexit is off inside `|| …`).
shutil.rmtree(common_dir(repo) / "pr-flow", ignore_errors=True)
(common_dir(repo) / "pr-flow").write_text("not a dir")
w = sh("route-record", str(repo), "--pr", "4")
check("a failed write exits non-zero", w.returncode == 1)
check("and never prints recorded=yes", "recorded=yes" not in w.stdout)
(common_dir(repo) / "pr-flow").unlink()

p = poll("norecord", [])
check("without --record a timeout records nothing", not memory_file(repo).exists())

sh("route-record", str(repo), "--pr", "9")
p = poll("done", [pr_comment("claude", "**Claude finished** review")], "--record", str(repo))
check("a finished review is returned", p.returncode == 0)
check("and clears the remembered answer", not memory_file(repo).exists())
p = poll("ci", [pr_comment("github-actions", "**Claude finished** (quoted in a CI log)")])
check("another author quoting the marker is not a review", p.returncode == 1)

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("claude-review.sh has-bot + route: all tests passed")
