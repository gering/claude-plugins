---
name: pr-fix
description: Work through issues from the latest Claude review — parse, prioritize, and guide fixes interactively
user_invocable: true
---

# PR Fix Guided Workflow

> Load the latest Claude review, present issues as a numbered checklist, let the user pick which to address, implement fixes one by one, then hand off to `/pr-cycle` for re-review.

## Instructions

0. **Preflight**:
   - Verify `gh` is installed and authenticated (see `/pr-cycle` step 0 for exact commands). Stop with clear error if missing.

1. **Identify PR**:
   - Run: `git branch --show-current`
   - If on `main`/`master`, stop: "You're on the main branch. Switch to a feature branch first."
   - Run: `gh pr view --json number,title,url,headRefName`
   - If no PR, stop: "No open PR on this branch. Run `/pr-cycle` first to create reviews."
   - Store `PR_NUMBER`.

2. **Fetch latest Claude review**:
   - Run:
     ```
     gh pr view <PR_NUMBER> --json comments --jq '[.comments[] | select(.author.login == "claude") | select(.body | contains("**Claude finished"))] | last | .body'
     ```
   - If empty, stop: "No completed Claude review found. Run `/pr-cycle` to trigger one."

3. **Parse issues**:
   - Split the review body into discrete findings (grouped by file, each with severity if noted)
   - Number them sequentially starting at #1
   - For each issue, extract:
     - File path + line(s)
     - Reviewer's concern (quote the key phrase)
     - Severity: **blocking** / **suggestion** / **nit** (infer from wording if not explicit)
     - Reviewer's proposed fix (if given)

4. **Present the checklist**:
   ```
   Found <N> issues in the latest Claude review (PR #<N>):

   #1  [blocking]   src/foo.ts:42
       "Empty catch block swallows the error"
       Fix: log the error or rethrow

   #2  [suggestion] src/bar.ts:17
       "Magic number 86400 — extract to constant"

   #3  [nit]        README.md:5
       "Typo: 'recieve' → 'receive'"

   Which issues do you want to address?
   - Numbers (e.g. "1,3" or "1-3")
   - "all" for everything
   - "blocking" for just blocking issues
   - "skip <N>" to mark as won't-fix (will be noted in next review)
   ```

5. **Own assessment first** (before implementing):
   - For each selected issue, briefly state your take: agree / partially agree / disagree and why
   - If you disagree with a blocking issue, flag it — don't silently implement something you think is wrong
   - If the user wants to override your assessment, follow their lead

6. **Implement fixes one by one**:
   - For each selected issue:
     - Read the relevant file(s) to confirm the context matches the reviewer's description
     - If the reviewer's claim doesn't match current code (comment rot, already fixed, line drift), note it and skip
     - Apply the fix — prefer minimal, targeted edits
     - **Do not bundle unrelated cleanup** (follow the "don't add features beyond what was asked" principle)
   - After each fix, briefly confirm what changed (file + 1-line summary)

7. **Handle skipped issues**:
   - If user chose to skip any, keep a list
   - At handoff, mention these will surface again in the next review unless the reviewer is convinced they're non-issues (e.g. via a reply comment)

8. **Test the fixes** (if test infrastructure exists):
   - If the project has a test command (check `package.json`, `Makefile`, etc.), ask the user if they want to run tests before re-reviewing
   - Don't assume — some projects have slow test suites

9. **Handoff**:
   ```
   ✅ Fixed <N> issues: #<list>
   ⏭️  Skipped <M> issues: #<list>  (will appear again in next review)
   ❌ Could not fix <K>: #<list>  (reason: ...)

   Next steps:
   - Run `/pr-cycle` to commit, push, and trigger re-review
   - Or run `/pr-check` to inspect current state first
   ```

10. **Do NOT auto-trigger `/pr-cycle`** — hand control back to the user. They may want to make additional changes first.

## Edge Cases

- Review comment exists but is still in progress (starts with "Claude Code is working") → stop, ask user to wait or run `/pr-check` to poll
- Latest review is older than the last push → warn: "The review is stale; the code has changed since. Run `/pr-cycle` for a fresh review."
- Review body has no parseable issues (just "LGTM") → inform user, no action needed
- User selects issues that don't exist (e.g. "fix #99") → list valid numbers and ask again

## Notes

- This skill is **interactive by design** — it never fixes things without user confirmation
- It explicitly does NOT run `/pr-cycle` automatically; the user stays in control of when to re-push
- Works standalone: you can run `/pr-fix` any time after a review exists, even days later
