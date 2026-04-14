---
name: pr-check
description: |
  Read-only status snapshot of the current PR. Shows CI check results
  (passed/failed/pending), human review state (approvals, changes
  requested), latest Claude review summary with staleness detection,
  uncommitted/unpushed local drift, and an overall merge-readiness
  verdict with recommended next step.

  Use when: user wants to "check PR status", "what's the state", "is it
  ready to merge", "show CI status", "quick health check", "how's the
  PR looking", needs status without mutating anything. Also when user
  says "status?" / "wie sieht's aus?" / "was macht der PR?".
user_invocable: true
---

# PR Status Check

> Non-intrusive snapshot of the current PR's health: CI status, human reviews, last Claude review summary, and merge-readiness verdict.

## Instructions

0. **Preflight**:
   - Verify `gh` is installed: `command -v gh >/dev/null` — if missing, stop with "gh CLI not installed — https://cli.github.com"
   - Verify auth: `gh auth status >/dev/null 2>&1` — if missing, stop with "gh not authenticated — run: gh auth login"

1. **Identify PR**:
   - Run: `git branch --show-current`
   - If on `main`/`master`, stop: "You're on the main branch — nothing to check."
   - Run: `gh pr view --json number,title,url,state,isDraft,mergeable,mergeStateStatus,headRefName,baseRefName`
   - If no PR exists, inform user and stop (suggest `gh pr create` or `/pr-cycle`)

2. **CI status**:
   - Run: `gh pr checks <PR_NUMBER>` (table format is fine)
   - Categorize:
     - ✅ passed
     - ❌ failed (list name + URL if available)
     - ⏳ pending / running
     - ⚠️ skipped / neutral

3. **Human reviews**:
   - Run: `gh pr view <PR_NUMBER> --json reviews --jq '.reviews[] | {author: .author.login, state: .state, submittedAt: .submittedAt}'`
   - Summarize: approved count, changes requested count, pending
   - If a human requested changes, list their open review bodies briefly

4. **Latest Claude review** (if any):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" latest <PR_NUMBER> --json`
   - Parse JSON: `{createdAt, body}`
   - If `body` is non-empty:
     - Extract issue count and summary
     - Show timestamp (how old is it?)
     - If older than the latest push (`gh pr view <PR_NUMBER> --json commits --jq '.commits | last | .commit.authoredDate'`), mark as **stale** → suggest `/pr-cycle` to refresh
   - If none, note: "No Claude review yet — run `/pr-cycle` to trigger one"

5. **Uncommitted local changes**:
   - Run: `git status --porcelain`
   - If any, warn: "You have uncommitted changes — the PR doesn't reflect your current state"

6. **Unpushed commits**:
   - Run: `git log @{u}..HEAD --oneline 2>/dev/null`
   - If any, warn: "Local commits not yet pushed"

7. **Present structured summary**:
   - **Top section** — status overview in this fixed layout:
     ```
     PR #<N>: <title>
     <URL>

     Branch: <head> → <base>  |  State: <open/merged/closed>  |  Draft: <yes/no>

     ── CI ──
     ✅ 5 passed  ❌ 1 failed  ⏳ 2 running
     Failed: <check-name> — <reason if available>

     ── Reviews ──
     Humans: 1 approved, 0 changes requested
     Claude: last review 2h ago (stale — new push since)

     ── Local ──
     ⚠️ 2 uncommitted files
     ⚠️ 1 unpushed commit
     ```
   - **Claude review section** — if a Claude review exists, render its findings following the shared format spec at `${CLAUDE_PLUGIN_ROOT}/docs/REVIEW-OUTPUT-FORMAT.md`. Required: header + status + **markdown findings table**. No prose cards, no per-finding headings. See forbidden patterns in the spec.
   - If no Claude review: skip the findings table, just note "No Claude review yet."

8. **Recommendation** — exactly one line, picked per the rules in `${CLAUDE_PLUGIN_ROOT}/docs/REVIEW-OUTPUT-FORMAT.md` (Recommendation section). Adapt to pr-check specific context:
   - If all green + approved + no local drift: "Ready to merge — run `/pr-merge`"
   - If stale Claude review or unpushed work: "Run `/pr-cycle` to refresh"
   - If open issues from last review: "Run `/pr-fix` to work through them"
   - If CI failing: list the failures and suggest fixing before re-triggering

## Notes

- This skill is **read-only** — it never commits, pushes, or triggers a review
- Safe to run repeatedly (e.g. polling during long CI runs)
- Complements `/pr-cycle` (active loop) and `/pr-fix` (work through issues)
