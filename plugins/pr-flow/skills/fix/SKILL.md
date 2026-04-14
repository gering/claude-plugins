---
name: fix
description: |
  Interactive walkthrough of issues from the latest Claude review. Parses
  the review body into a numbered checklist (with severity labels:
  blocking/suggestion/nit), lets user pick issues to address by number,
  range, "all", or severity. Gives own assessment per issue before
  implementing, applies minimal targeted edits, tracks skipped items.

  Use when: user wants to "fix the review", "address claude's feedback",
  "work through the issues", "implement the reviewer suggestions", "tackle
  the review points", has a completed review and wants to resolve findings.
  Also when user says "fix punkt 1 und 3" / "issues abarbeiten" / "review-
  findings umsetzen".
user_invocable: true
---

# PR Fix Guided Workflow

> Load the latest Claude review, present issues as a numbered checklist, let the user pick which to address, implement fixes one by one, then hand off to `/cycle` for re-review.

## Instructions

0. **Preflight**:
   - Verify `gh` is installed and authenticated (see `/cycle` step 0 for exact commands). Stop with clear error if missing.

1. **Identify PR**:
   - Run: `git branch --show-current`
   - If on `main`/`master`, stop: "You're on the main branch. Switch to a feature branch first."
   - Run: `gh pr view --json number,title,url,headRefName`
   - If no PR, stop: "No open PR on this branch. Run `/cycle` first to create reviews."
   - Store `PR_NUMBER`.

2. **Fetch latest Claude review**:
   - Run:
     ```
     gh pr view <PR_NUMBER> --json comments --jq '[.comments[] | select(.author.login == "claude") | select(.body | contains("**Claude finished"))] | last | .body'
     ```
   - If empty, stop: "No completed Claude review found. Run `/cycle` to trigger one."

3. **Parse issues**:
   - Split the review body into discrete findings (grouped by file, each with severity if noted)
   - Number them sequentially starting at #1
   - For each issue, extract:
     - File path + line(s)
     - Reviewer's concern (quote the key phrase)
     - Severity: **blocking** / **suggestion** / **nit** (infer from wording if not explicit)
     - Reviewer's proposed fix (if given)

4. **Present the checklist**:
   - Render findings as a **markdown table** following the shared format spec at `${CLAUDE_PLUGIN_ROOT}/docs/REVIEW-OUTPUT-FORMAT.md`. Required columns: `# | Severity | Location | Finding | My assessment`. No prose cards, no per-finding headings.
   - Include your own assessment per row (this replaces the separate "Own assessment first" step) — agree/partial/disagree with ≤80 chars reasoning.
   - After the table, append a single prompt line:
     ```
     Which issues do you want to address? (e.g. "1,3", "1-3", "all", "blocking", "skip <N>")
     ```

5. **Honor your table assessment when implementing**:
   - Your assessments are already in the step-4 table — act on them consistently:
     - If you marked `Disagree` and user insists on fixing → flag the conflict once, then follow user's lead
     - If you marked `Agree` → implement the proposed fix
     - If `Partial agree` → clarify what you'll actually do before editing

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
   - Run `/cycle` to commit, push, and trigger re-review
   - Or run `/check` to inspect current state first
   ```

10. **Do NOT auto-trigger `/cycle`** — hand control back to the user. They may want to make additional changes first.

## Edge Cases

- Review comment exists but is still in progress (starts with "Claude Code is working") → stop, ask user to wait or run `/check` to poll
- Latest review is older than the last push → warn: "The review is stale; the code has changed since. Run `/cycle` for a fresh review."
- Review body has no parseable issues (just "LGTM") → inform user, no action needed
- User selects issues that don't exist (e.g. "fix #99") → list valid numbers and ask again

## Notes

- This skill is **interactive by design** — it never fixes things without user confirmation
- It explicitly does NOT run `/cycle` automatically; the user stays in control of when to re-push
- Works standalone: you can run `/fix` any time after a review exists, even days later
