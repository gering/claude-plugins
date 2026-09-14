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


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("claude-review.sh has-bot: all tests passed")
