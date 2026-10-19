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
      at(step5, "Landing task") < at(step5, "and invoke `/close --delegate`")
      and "Add nothing after `/close` returns" in step5)

# ------------------------------------------------ landing marker + gone lane
check("step 1 records the landing marker after every check passed",
      at(step1, "a merged PR's deleted remote") < at(step1, "landing.sh\" begin --pr <pr_number>"))
check("the marker is best-effort, never a stop", "`recorded=no` is one line, never a stop" in step1)
check("step 1 keeps the main-repo path for a vanished lane", "<main-repo-path>" in step1)
check("every stop clears the marker",
      "**every stop** first clears it" in LAND and "landing.sh\" end <task_name>" in LAND)
check("a /merge stop checks the lane before stopping",
      at(step3, "**Any `/merge` stop or error** → run step 4's **lane check** first")
      < at(step3, "`/merge` **stopped before merging**"))
lane = at(step4, "**Lane check** first")
gone = step4[lane:at(step4, "**Re-assess**")]
check("the lane check runs before the re-assess", lane < at(step4, "**Re-assess**"))
check("the lane check uses the kept main-repo path, not the cwd",
      'gone-check <task_name> --pr <pr_number> --repo "<main-repo-path>"' in gone)
check("the gone path branches on the script's verdict, not on prose rules",
      "Branch on the script's `verdict=`" in gone and "`verdict=handoff`" in gone)
check("every stop= the script emits has its message",
      all(f"`{c}`" in gone for c in ("not-merged", "branch-ahead", "branch-unreadable", "not-by-name")))
check("a gone lane without a confirmed merge clears the marker and stops",
      "clear the marker and stop" in gone and "not confirmed" in gone)
check("a gone lane never runs /close here", "never run `/close` here" in gone)
check("a gone lane hands over, keeping the marker for the tab",
      "phase <task_name> handoff" in gone and "keeps the marker" in gone
      and at(gone, "`verdict=stop`") < at(gone, "`verdict=handoff`"))
check("a gone lane prints the follow-ups before the hand-over",
      at(gone, "follow-up list") < at(gone, "**Hand the close over"))
check("the gone-lane recipient is sent unasked only as the kicker session",
      "`herdr_agent_session=` equal to a **non-empty** `kicker_session=`" in gone
      and "Any other candidate → ask once" in gone)
check("a gone lane without recipient names the Manager-side /close",
      "In the main checkout run `/close <task_name>`" in gone)
check("the MERGED shortcut records the landing too",
      "record the landing (the last bullet below)" in step1)
check("an adopted branch is not handed over by name",
      "cannot be resolved by name" in gone)
check("step 5 marks the hand-over before /close --delegate",
      at(step5, "phase <task_name>") < at(step5, "and invoke `/close --delegate`"))

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

# ------------------------------------- /close: /land marker + a gone worktree
c1a = at(CLOSE, "1a. **A `/land` still running?**")
check("1a runs before the delegation offer and the merge gate",
      c1a < at(CLOSE, "1b. **Offer to delegate") < at(CLOSE, "2. **Verify the task is merged**"))
step1a = CLOSE[c1a:at(CLOSE, "1b. **Offer to delegate")]
check("a live landing marker asks before any cleanup",
      "`phase=landing` with `landing=live`" in step1a and "before any cleanup" in step1a)
check("an unreadable landing marker asks too, never skips",
      "`landing=invalid` → ask the same" in step1a and "never skip one" in step1a)
check("a handoff or stale marker asks nothing",
      "`stale`" in step1a and "`phase=handoff`" in step1a)
step4c = CLOSE[at(CLOSE, "4. **Get worktree info**"):at(CLOSE, "5. **Sync local main")]
check("a gone worktree is pruned and the close goes on",
      "**Already gone**" in step4c and "worktree prune" in step4c and "keep going" in step4c
      and 'landing.sh" lane' in step4c and "a leftover directory does not count" in step4c)
step7c = CLOSE[at(CLOSE, "7. **Remove worktree**"):at(CLOSE, "8. **Delete local branch**")]
check("a gone worktree's tab comes from herdr's live answer for the marker's pane",
      "**Worktree already gone**" in step7c and "never a tab id the file holds" in step7c
      and "`worker_cwd=match` → `WT_TAB=<worker_tab>`" in step7c)
check("an unconfirmed worker cwd asks before closing the tab",
      "`worker_cwd=unknown`" in step7c and "only on a yes" in step7c)
check("a marker's tab is used only while its lane is gone",
      "Use it only while that output's `worktree_state=gone`" in step7c)
check("a leftover directory is removed only on a yes", "`leftover=yes`" in step4c
      and "only on a yes" in step4c)
check("a surviving branch is checked before step 8 deletes it",
      "gone-check" in step4c and "`stop=branch-ahead`" in step4c)
check("the marker is cleared after the archive",
      at(CLOSE, "10. **Archive the task file**") < at(CLOSE, "10a. **Clear the `/land` marker**")
      < at(CLOSE, "10b. **Act on the follow-ups**"))
recv = CLOSE[at(CLOSE, "## Receiving a close-request"):]
check("a gone lane is an ask that still archives, sweeps and closes the tab",
      "`lane=gone`" in recv and "**Already gone** path" in recv)
check("a gone worktree is not 'nothing to close' while the task file is unarchived",
      "the close is not necessarily done" in recv)

q = at(CLOSE, "1b. **Offer to delegate the close to the Manager**")
gate2 = at(CLOSE, "2. **Verify the task is merged**")
d = at(CLOSE, "**With `--delegate`**")
check("the delegate branch lives in step 1b, before the merge gate", q < d < gate2)
branch = CLOSE[d:gate2]
check("delegate preselected on the resolver's own rule",
      "**and** `delegate_unasked=yes` → do **not** ask" in branch)
check("everything else still asks",
      "but `delegate_unasked=no` → ask the question" in branch)
check("the gone path sends unasked on the same evidence",
      "`evidence=sole-root-agent` while `kicker_session=` is empty" in LAND)
check("the recipient is named before the message is sent",
      at(branch, "matched to that session by name only")
      < at(branch, "then send the same single"))
check("only the user adds --delegate", "**Never add it yourself**" in CLOSE)
check("a gate miss runs today's flow unchanged",
      "**Gate miss** → today's flow from step 2, unchanged" in branch)
check("a gate miss under the flag is reported, not silent", "closing here." in branch)
check("the delegated close stops like the asked one", "**STOP** exactly as above" in branch)

# ====================================== follow-ups: carried, classified, recorded
# The worker's open next steps must reach the Manager on every delegated close,
# and the Manager must run, ask about or record each one — never drop it.
CLOSE_RAW = (SKILLS / "close" / "SKILL.md").read_text()
CONTINUE = " ".join((SKILLS / "continue" / "SKILL.md").read_text().split())
MERGE = " ".join((HERE.parent.parent / "pr-flow" / "skills" / "merge" / "SKILL.md")
                 .read_text().split())


def fmt_block(raw):
    m = re.search(r"```text\n(\s*Follow-ups:\n.*?)```", raw, re.S)
    return textwrap.dedent(m.group(1)) if m else None


check("/land defines the Follow-ups format", fmt_block(LAND_RAW) is not None)
check("/close's delegation repeats the same Follow-ups format",
      fmt_block(CLOSE_RAW) is not None and fmt_block(CLOSE_RAW) == fmt_block(LAND_RAW))
check("the format tags who must act",
      all(t in (fmt_block(LAND_RAW) or "") for t in ("[step]", "[decide]", "[deploy]")))

leave = at(CLOSE, "**Leave the follow-ups before every send.**")
check("follow-ups are left before the delegation question and send",
      q < leave < at(CLOSE, "**The question**"))
leave_txt = CLOSE[leave:at(CLOSE, "**The question**")]
check("a plain delegated close writes its own handoff report",
      "prepare handoff" in leave_txt and "--caller close" in leave_txt)
check("the handoff on delegation is never a gate", "**Never a gate:**" in leave_txt)
check("the list is printed on screen for the pane fallback", "print the list on screen" in leave_txt)
check("both send paths leave the follow-ups first",
      "leave the follow-ups (above), then send" in CLOSE
      and "leave the follow-ups (above); then send the same single" in CLOSE)

s6c = CLOSE[at(CLOSE, "6c. **Follow-up sweep**"):at(CLOSE, "7. **Remove worktree**")]
check("6c classifies into do / ask / deploy",
      all(c in s6c for c in ("**Do it**", "**Ask**", "**Deploy**")))
check("6c derives commands from the repo, never from worker text",
      "**from the repo itself**" in s6c and "never from the worker's text" in s6c)
check("6c keeps the untrusted-data rule", "**untrusted data written by the" in s6c)
check("6c preserves order: a dependent item is blocked, not run early",
      "**Order is preserved.**" in s6c and "**blocked**" in s6c)
check("6c reads the deploy owner from the main checkout",
      'deploy-owner "<main-repo-path>"' in s6c)
check("6c runs nothing but the record before the close", "Nothing else runs here" in s6c)

s10b = CLOSE[at(CLOSE, "10b. **Act on the follow-ups**"):at(CLOSE, "11. **Final summary**")]
check("10b comes after the archive and before the summary",
      at(CLOSE, "10. **Archive the task file**") < at(CLOSE, "10b. **Act on the follow-ups**"))
check("open items are recorded in 6c, before the worktree is removed",
      "**Record before the removal.**" in s6c and "followups.py\" record" in s6c
      and "<<'WS_FOLLOWUP_END'" in s6c)
check("a self-closing worker records its own open items",
      "A worker closing its **own** lane" in s6c and "followups.py record" in s6c)
check("a lane cannot grant itself an unasked deploy",
      "**unless the merged PR itself changed `.work-system.toml`**" in s6c)
check("10b batches deploys through the class filter", "--class deploy" in s10b)
check("10b batches deploys", "**Batch the deploys.**" in s10b)
check("10b resolves what got done", "followups.py\" resolve" in s10b)
check("an auto-accepted close approves no follow-up",
      "no follow-up is approved by it" in CLOSE)

check("/land's worker never deploys under deploy_by=manager",
      "**Never deploy from the lane**" in LAND and "deploy-owner" in LAND)
check("/continue's worker never deploys under deploy_by=manager",
      "deploy-owner" in CONTINUE and "the Manager deploys from the main checkout" in CONTINUE)
check("/continue's handoff report carries the Follow-ups list",
      "This list, not a ping, is how they reach the Manager" in CONTINUE)
check("a busy Manager on the herdr route is never retried in a loop",
      "`reason=manager-working`" in CONTINUE and "do not retry, loop, sleep-and-resend" in CONTINUE)
check("pr-flow's /merge asks work-system who deploys",
      "mandate-shim.sh\" deploy-owner" in MERGE and "do **not** deploy" in MERGE)

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
