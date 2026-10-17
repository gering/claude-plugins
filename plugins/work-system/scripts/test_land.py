#!/usr/bin/env python3
"""Contract tests for /land and /close --delegate.

Both are prose skills: the composition order, the authorization statement and the
delegate-vs-gate-miss branches live in SKILL.md, and prose drifts under later
edits. These assertions pin the parts whose drift would close a lane on an
unconfirmed merge or delegate on a guess.
"""

import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).parent
SKILLS = HERE.parent / "skills"
LAND_RAW = (SKILLS / "land" / "SKILL.md").read_text()
# Whitespace-normalized, so a reflow that keeps the meaning keeps the test green.
LAND = " ".join(LAND_RAW.split())
CLOSE = " ".join((SKILLS / "close" / "SKILL.md").read_text().split())

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def at(text, needle):
    i = text.find(needle)
    assert i >= 0, f"missing anchor: {needle!r}"
    return i


# ============================================================ /land composition
pre = at(LAND, "1. **Preconditions**")
report = at(LAND, "2. **Handoff report with follow-ups**")
merge = at(LAND, "3. **Merge**")
confirm = at(LAND, "4. **Confirm the merge**")
close = at(LAND, "5. **Report, then close**")
check("preconditions, report, merge, confirm, close run in that order",
      pre < report < merge < confirm < close)

step1 = LAND[pre:report]
check("refuses outside a task worktree", "main-repo-path.sh\" linked" in step1
      and "on_main=yes" in step1 and "detached=yes" in step1)
check("a PR that cannot be merged stops before the report",
      all(f"`pr_state={s}`" in step1 for s in ("none", "nogh", "CLOSED")))
check("an open or merged PR runs the head check before the report",
      "`pr_state=OPEN` or `MERGED` → run step 4's **head check** block now" in step1)
check("/land refuses arguments", "`/land` takes no arguments" in LAND)
check("an open PR without upstream stops before the merge",
      "`OPEN` only — **an upstream**" in step1 and "no upstream" in step1)
check("pr-flow presence is checked first", at(step1, "pr-flow installed?") < at(step1, "assess"))
check("a missing pr-flow stops before anything runs",
      "pr-flow is not installed" in step1 and "**before** anything else runs" in step1)
check("never falls back to a bare gh merge", "Never fall back to a bare `gh pr merge`" in step1)
check("the PR number comes from assess in step 1", "pr_number" in step1)
# By name, assess matches only task/<name> and would miss an /adopt-ed branch;
# a bare assess binds the checked-out branch, which step 4 verifies first.
calls = re.findall(r'task-status\.sh" assess[^`]*', LAND)
check("/land calls assess at all", len(calls) > 0)
check("every assess call binds the checked-out branch (no name argument)",
      all(c.strip() == 'task-status.sh" assess' for c in calls))

step2 = LAND[report:merge]
check("the handoff report carries the PR so the sweep can match it", "--pr <pr_number>" in step2)
check("follow-ups are an explicit list in the summary", "Follow-ups:" in step2)
check("the handoff report is never a gate", "**Never a gate.**" in step2)
check("the land report is always written, not deduplicated away", "**Write it** even when" in step2)
# The bridge-flag audit for /land lives with the others in test_insights_handoff.py.

step3 = LAND[merge:confirm]
check("merge goes through /pr-flow:merge", "invoke `/pr-flow:merge`" in step3)
check("a stopped merge closes nothing", "Nothing was closed" in step3)

step4 = LAND[confirm:close]
check("the merge is confirmed via assess, not a message",
      "Assess again" in step4 and "confidence=confirmed" in step4)
check("an unconfirmed merge closes nothing", "Nothing was closed" in step4)
check("an open PR left after /merge is never closed", "do **not** run `/close`" in step4)
check("an open PR needs the exact head; a merged one may be ahead of HEAD",
      "**Before a merge** (step 1, `OPEN`): only `head=same` passes" in step4
      and "`head=same` or `head=behind` (the PR carries everything here) passes" in step4)
check("a diverged branch is not reported as ahead", "head=diverged" in step4)
check("the merged head must contain HEAD, fail-closed", "head=unreadable" in step4)
check("local commits after a merge get their own message",
      "After a merge, `head=ahead` or `head=diverged` →" in step4)


step5 = LAND[close:]
check("close runs with the delegation flag", "`/close --delegate`" in step5)
check("the follow-ups are printed, not only stored", "- Follow-ups:" in step5)
check("the land summary is printed before /close, whose output stays last",
      at(step5, "Landing task") < at(step5, "Then invoke `/close --delegate`")
      and "Add nothing after `/close` returns" in step5)

check("the user's invocation is the merge authorization",
      "**The user invoking `/land` IS the merge authorization.**" in LAND)
check("never run on the worker's own initiative or a peer's request",
      "never run `/land` on your own initiative" in LAND and "peer session" in LAND)
check("never injected into another pane", "never type `/land` into another session's pane" in LAND)
front = LAND_RAW.split("---")[1]
check("the model cannot invoke /land through the Skill tool",
      re.search(r"^disable-model-invocation: true$", front, re.M) is not None)

check("/land lists the asked outcome of --delegate", "- **Asked**" in step5)
check("/land is not chained by hand", "never chain `/pr-flow:merge` and `/close --delegate`" in LAND)

# ======================================================== /close --delegate
check("close documents the flag", "- `--delegate` —" in CLOSE)
# The harness substitutes $ARGUMENTS verbatim, flag included, so no command in
# /close may carry it — the flag would become the task name.
check("no /close command receives the raw arguments", "$ARGUMENTS" not in CLOSE)
check("/close commands use the flag-free task argument", 'assess "<task-arg>"' in CLOSE)
check("a task argument with shell characters is refused",
      "anything but letters, digits and `._/-` is not a task name" in CLOSE)
check("a token starting with - is never the task", "a token starting with `-` is never a task name" in CLOSE)
check("6b re-runs assess with step 1's resolved argument", 'assess "<step-1-arg>"' in CLOSE)
check("a second task-name token is refused", "`/close` takes one task name" in CLOSE)
check("every typed name is charset-checked, not only the argument",
      "**every** name before it is typed into a command" in CLOSE
      and "(after the charset check above)" in CLOSE)
check("an uncommitted-only follow-up pauses before /close",
      "the user accepted it during step 3" in step5 and "Go on only on a yes" in step5)
check("the re-assess must confirm the same PR", "A confirmed merge must be step 1's PR" in step4
      and at(step4, "Proceed **only** on") < at(step4, "A confirmed merge must be step 1's PR"))
check("an unconfirmed merge points back to /land, not to a bare /close",
      "not a plain `/close`, which skips the head check" in step4)

q = at(CLOSE, "1b. **Offer to delegate the close to the Manager**")
gate2 = at(CLOSE, "2. **Verify the task is merged**")
d = at(CLOSE, "**With `--delegate`**")
check("the delegate branch lives in step 1b, before the merge gate", q < d < gate2)
branch = CLOSE[d:gate2]
check("delegate preselected on the kicker record only",
      "**and** `evidence=kicker` → do **not** ask" in branch)
check("root-agent evidence still asks",
      "`evidence=sole-root-agent` or `evidence=leftmost-tab` → ask the question" in branch)
check("the recipient is named before the message is sent",
      at(branch, "matched to that session by name only")
      < at(branch, "then send the same single"))
check("only the user adds --delegate", "**Never add it yourself**" in CLOSE)
check("a gate miss runs today's flow unchanged",
      "**Gate miss** → today's flow from step 2, unchanged" in branch)
check("a gate miss under the flag is reported, not silent", "closing here." in branch)
check("the delegated close stops like the asked one", "**STOP** exactly as above" in branch)

# ============================================ head check, executed (not grepped)
# The classification decides whether /close may delete the branch, so run the
# snippet itself against real repos: swapped arms or a set -e abort must fail here.
m = re.search(r"\*\*Head check\*\*.*?```sh\n(.*?)```", LAND_RAW, re.S)
check("the head check is one sh block", m is not None)
if m:
    snippet = textwrap.dedent(m.group(1)).replace("<pr_number>", "7")

    def git(repo, *args):
        return subprocess.run(["git", "-C", repo, *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def head_state(repo, pr_oid):
        with tempfile.TemporaryDirectory() as bindir:
            gh = Path(bindir, "gh")
            gh.write_text(f"#!/bin/sh\nprintf '%s\\n' '{pr_oid}'\n")
            gh.chmod(0o755)
            env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
            out = subprocess.run(["bash", "-e", "-c", snippet], cwd=repo, env=env,
                                 capture_output=True, text=True).stdout
        return out.strip()

    with tempfile.TemporaryDirectory() as repo:
        git(repo, "init", "-q")
        for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
            git(repo, "config", k, v)
        def commit(msg):
            git(repo, "commit", "-q", "--allow-empty", "-m", msg)
            return git(repo, "rev-parse", "HEAD")
        base = commit("base")
        tip = commit("tip")
        check("head check: same", head_state(repo, tip) == "head=same")
        check("head check: ahead", head_state(repo, base) == "head=ahead")
        git(repo, "reset", "-q", "--hard", base)
        check("head check: HEAD behind the PR head", head_state(repo, tip) == "head=behind")
        other = commit("other")
        check("head check: diverged", head_state(repo, tip) == "head=diverged")
        check("head check: unreadable OID", head_state(repo, "nope") == "head=unreadable")
        check("head check: unknown commit", head_state(repo, "0" * 40) == "head=unreadable")

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("land / close --delegate: all tests passed")
