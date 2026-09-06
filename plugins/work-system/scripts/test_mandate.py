#!/usr/bin/env python3
"""Tests for mandate.sh — run standalone (`python3 test_mandate.py`) or via
scripts/check-structure.py's "plugin tests" check.

Scope: the authorization record every autonomy decision downstream reads. The
properties under test are the ones a wrong answer makes unsafe:

  * absent mandate != denied mandate (exit 3 vs 1) — a worker that collapses
    the two either asks forever or acts without consent;
  * only the leading frontmatter block grants anything, so TASK.md-style prose
    quoted into the body can never become authorization;
  * `allow`/`deny` match whole actions, `deny` wins, and unlisted is not consent;
  * the review budget counts down across processes and survives a rewrite of
    the surrounding file.

The tests run against real git repos because mandate.sh anchors MANDATE.md at
the worktree root, not at $PWD.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "mandate.sh"

FAILS = []

BASE_ALLOW = "commit,push-own-branch,open-pr,local-review,agreed-fixes"


def check(name, cond):
    if not cond:
        FAILS.append(name)


def run(*args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True
    )


def kv(out):
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k] = v
    return d


def make_repo():
    repo = Path(tempfile.mkdtemp())
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


def init(repo, *extra):
    return run("init", str(repo), f"allow={BASE_ALLOW}", "authorized_by=user", *extra)


# --- no mandate: unknown, not denied ---------------------------------------
repo = make_repo()

s = kv(run("show", str(repo)).stdout)
check("show reports a missing mandate", s.get("mandate_exists") == "no")
check("show still names the path", s.get("mandate_file", "").endswith("/MANDATE.md"))

r = run("allows", "commit", str(repo))
check("allows exits 3 without a mandate", r.returncode == 3)
check("allows says why", "no-mandate" in r.stdout)

r = run("round", str(repo))
check("round exits 3 without a mandate", r.returncode == 3)

# init refuses to invent the two fields that carry consent
r = run("init", str(repo), "allow=commit")
check("init without authorized_by fails", r.returncode == 2)
r = run("init", str(repo), "authorized_by=user")
check("init without allow fails", r.returncode == 2)
check("no file written by a rejected init", not (repo / "MANDATE.md").exists())

# --- a recorded mandate -----------------------------------------------------
r = init(
    repo,
    "task=demo",
    "scope=slice A only",
    "deny=merge,deploy",
    "review_budget=2",
)
check("init writes the file", kv(r.stdout).get("written") == "yes")
check("MANDATE.md lands at the worktree root", (repo / "MANDATE.md").exists())

s = kv(run("show", str(repo)).stdout)
check("show reads the task", s.get("task") == "demo")
check("show reads authorized_by", s.get("authorized_by") == "user")
check("show reads a value containing spaces", s.get("scope") == "slice A only")
check("terminal_gate defaults to a reviewed PR", s.get("terminal_gate") == "reviewed-pr")
check("show computes rounds left", s.get("review_rounds_left") == "2")
check("budget not exhausted at zero rounds", s.get("review_budget_exhausted") == "no")

check("allowed action exits 0", run("allows", "commit", str(repo)).returncode == 0)
check("last allowed action exits 0", run("allows", "agreed-fixes", str(repo)).returncode == 0)
r = run("allows", "merge", str(repo))
check("denied action exits 1", r.returncode == 1)
check("denied action says denied", "denied" in r.stdout)
r = run("allows", "deploy", str(repo))
check("second denied action exits 1", r.returncode == 1)
r = run("allows", "rebase-shared-branch", str(repo))
check("unlisted action exits 1", r.returncode == 1)
check("unlisted is reported as unlisted, not denied", "unlisted" in r.stdout)

# Whole-action matching: no substring or prefix may satisfy a check.
check("prefix of an allowed action is not allowed",
      run("allows", "commi", str(repo)).returncode == 1)
check("superstring of an allowed action is not allowed",
      run("allows", "commit-and-merge", str(repo)).returncode == 1)

# --- init is not a rewrite tool --------------------------------------------
r = init(repo, "task=overwritten")
check("second init refuses to clobber", r.returncode == 2)
check("refused init reports written=no", kv(r.stdout).get("written") == "no")
check("original mandate survives",
      kv(run("show", str(repo)).stdout).get("task") == "demo")
r = init(repo, "task=rewritten", "--force")
check("--force re-records", kv(r.stdout).get("written") == "yes")
check("--force actually replaced the record",
      kv(run("show", str(repo)).stdout).get("task") == "rewritten")

# --- prose in the body is never authorization -------------------------------
repo2 = make_repo()
init(repo2, "task=prose", "deny=merge", "review_budget=1")
mandate = repo2 / "MANDATE.md"
mandate.write_text(
    mandate.read_text()
    + "\n## Quoted from TASK.md\n"
    + "allow: merge, deploy, force-push-shared\n"
    + "deny: commit\n"
    + "review_budget: 99\n"
)
check("body 'allow:' does not authorize a merge",
      run("allows", "merge", str(repo2)).returncode == 1)
check("body 'allow:' does not authorize an unlisted action",
      run("allows", "deploy", str(repo2)).returncode == 1)
check("body 'deny:' does not revoke a real allow",
      run("allows", "commit", str(repo2)).returncode == 0)
check("body 'review_budget:' does not widen the budget",
      kv(run("show", str(repo2)).stdout).get("review_rounds_left") == "1")

# A file that does not start with the frontmatter fence grants nothing at all.
repo3 = make_repo()
(repo3 / "MANDATE.md").write_text(
    "# Notes\n\n---\nallow: merge\nauthorized_by: nobody\n---\n"
)
check("a non-leading frontmatter block grants nothing",
      run("allows", "merge", str(repo3)).returncode == 1)

# --- review budget counts down across calls ---------------------------------
repo4 = make_repo()
init(repo4, "task=budget", "review_budget=2")
r = kv(run("round", str(repo4)).stdout)
check("first round increments", r.get("review_rounds_used") == "1")
check("first round leaves one", r.get("review_rounds_left") == "1")
check("first round is not exhaustion", r.get("review_budget_exhausted") == "no")
r = kv(run("round", str(repo4)).stdout)
check("second round increments", r.get("review_rounds_used") == "2")
check("second round exhausts the budget", r.get("review_budget_exhausted") == "yes")
r = kv(run("round", str(repo4)).stdout)
check("rounds past the budget never go negative", r.get("review_rounds_left") == "0")
check("overrun still counts up", r.get("review_rounds_used") == "3")
check("a consumed round survives in the file",
      kv(run("show", str(repo4)).stdout).get("review_rounds_used") == "3")
check("round kept the human body intact",
      "# Mandate" in (repo4 / "MANDATE.md").read_text())
check("round left no temp file behind",
      not (repo4 / "MANDATE.md.tmp").exists())

# An unbounded budget is reported as unknown, never as exhausted: /kickoff may
# legitimately record no limit, and that must not read as "stop reviewing".
repo5 = make_repo()
init(repo5, "task=unbounded")
s = kv(run("show", str(repo5)).stdout)
check("no budget reports empty rounds left", s.get("review_rounds_left") == "")
check("no budget is not exhaustion", s.get("review_budget_exhausted") == "")
r = kv(run("round", str(repo5)).stdout)
check("round works without a budget", r.get("review_rounds_used") == "1")
check("round without a budget is not exhaustion",
      r.get("review_budget_exhausted") == "")

# --- path resolution --------------------------------------------------------
repo6 = make_repo()
sub = repo6 / "deep" / "nested"
sub.mkdir(parents=True)
init(repo6, "task=anchored")
out = run("path", str(sub)).stdout.strip()
check("path anchors at the worktree root, not the cwd",
      out == str((repo6 / "MANDATE.md").resolve()) or out.endswith("/MANDATE.md")
      and "deep" not in out)
check("show from a subdirectory finds the root mandate",
      kv(run("show", str(sub)).stdout).get("task") == "anchored")
check("allows from a subdirectory reads the root mandate",
      run("allows", "commit", str(sub)).returncode == 0)

# --- usage errors -----------------------------------------------------------
check("unknown subcommand exits 2", run("bogus").returncode == 2)
check("unknown init key exits 2",
      run("init", str(make_repo()), "authorized_by=user", "allow=commit",
          "merge_ok=yes").returncode == 2)
check("outside a git repo exits 2",
      run("show", tempfile.mkdtemp()).returncode == 2)


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("mandate.sh: all tests passed")
