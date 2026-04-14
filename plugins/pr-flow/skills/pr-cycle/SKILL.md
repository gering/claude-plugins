---
name: pr-cycle
description: |
  Runs a full review iteration with the @claude GitHub review bot. Commits
  pending changes (with confirmation), pushes to remote, hides outdated
  previous reviews, triggers @claude review (or detects auto-trigger),
  polls in the background until completion, and presents structured
  results with numbered issues grouped by file and severity.

  Use when: user wants to "trigger claude review", "get review feedback",
  "iterate on the PR", "push and review", "cycle the PR", has made changes
  to an open PR and wants new feedback. Also when user says "lass claude
  nochmal drüberschauen" / "review-zyklus" / "push und feedback".
user_invocable: true
---

# PR Review Feedback Loop

> Stage, commit, push, trigger Claude review on PR, wait for feedback in background, and display results.

## Arguments

- `$ARGUMENTS` - Optional: commit message for pending changes

## Instructions

0. **Preflight**:
   - Verify `gh` is installed: `command -v gh >/dev/null || { echo "gh CLI not installed — https://cli.github.com"; exit 1; }`
   - Verify auth: `gh auth status >/dev/null 2>&1 || { echo "gh not authenticated — run: gh auth login"; exit 1; }`
   - If either fails, stop and instruct the user.

1. **Check current branch and PR**:
   - Run: `git branch --show-current`
   - If on `main` or `master`, stop: "You're on the main branch. Switch to a feature branch first."
   - Run: `gh pr view --json number,title,url,headRefName,baseRefName 2>/dev/null`
   - If no PR exists, inform user and suggest: `gh pr create`
   - Store `PR_NUMBER`, `PR_URL`, and `BASE_BRANCH` (from baseRefName) for later use

2. **Check if rebase is needed** — delegate to `/pr-rebase`:
   - Invoke the `/pr-rebase` skill. It will:
     - Determine the PR's base branch (authoritative source: `gh pr view`)
     - Detect divergence, ask the user for confirmation, execute or skip
     - Abort cleanly on conflicts
   - Proceed with this skill only if `/pr-rebase` returned cleanly (up-to-date, rebased successfully, or user declined)
   - If conflicts aborted the rebase: stop this cycle, let the user resolve manually, then re-run `/pr-cycle`

3. **Handle uncommitted changes**:
   - Run: `git status --porcelain`
   - If changes exist:
     - If `$ARGUMENTS` provided, use as commit message
     - Otherwise, generate a concise commit message based on the changes (stage and show diff first)
     - ALWAYS ask for confirmation before committing if no `$ARGUMENTS` provided
     - Stage all changes: `git add -A`
     - Commit with the message
   - If no changes, skip to step 4

4. **Push to remote**:
   - Run: `git push`
   - If push fails (no upstream), run: `git push -u origin HEAD`
   - If already up-to-date, continue

5. **Hide previous review comments**:
   - Find all previous Claude review comments on the PR:
     ```
     gh pr view <PR_NUMBER> --json comments --jq '[.comments[] | select(.author.login == "claude") | .id]'
     ```
   - For each comment ID, minimize it as "OUTDATED" via GraphQL:
     ```
     gh api graphql -f query='mutation { minimizeComment(input: {subjectId: "<COMMENT_NODE_ID>", classifier: OUTDATED}) { minimizedComment { isMinimized } } }'
     ```
   - If no previous comments exist, skip silently

6. **Check for auto-triggered review**:
   - Store the current timestamp before checking:
     - ISO: `date -u +%Y-%m-%dT%H:%M:%SZ` → TRIGGER_ISO
   - Wait ~5 seconds after push, then check if a review was already auto-triggered:
     ```
     bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" latest-after <PR_NUMBER> "<TRIGGER_ISO>"
     ```
   - If output is non-empty: a review was auto-triggered by the project's CI/webhook config — skip to step 8 (polling)
   - If output is empty: trigger manually in step 7

7. **Trigger Claude review** (only if no auto-trigger detected):
   - Run: `gh pr comment <PR_NUMBER> --body "@claude review"`

8. **Launch background polling via Bash**:
   - Use the **Bash tool** with `run_in_background: true` to invoke the shared polling script:
     ```
     bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" poll <PR_NUMBER> "<TRIGGER_ISO>"
     ```
   - Default timeout is 20 iterations × 30s = 10 minutes. Override with `--max N --interval S` if needed.
   - On success: the script prints the review comment body to stdout (exit 0).
   - On timeout: the script prints "TIMEOUT" to stderr and exits 1 — surface the PR URL to the user.
   - Background Bash tasks can use `gh` via `Bash(gh:*)` in the allowlist; background agents cannot.

9. **Inform user**:
   ```
   Review triggered on PR #<PR_NUMBER>.
   Polling in the background — you can continue working.
   You'll be notified when the review is complete.
   ```

10. **Present review results** (when background Bash task completes):
    - Read the output of the background task (the raw review comment body)
    - Check CI status: `gh pr checks <PR_NUMBER>`
      - If any checks failed: include them prominently at the top of the summary with the check name and failure reason
      - If checks are still running: note this
      - If all checks passed: briefly confirm
    - Parse and present a COMPLETE structured summary so the user never needs to open the browser:
      - Number each issue sequentially (e.g. #1, #2, #3) so the user can reference them easily (e.g. "fix #1 and #3")
      - List EVERY issue raised, grouped by file, with:
        - File path and line number(s)
        - What the reviewer flagged (quote key phrases)
        - Severity (blocking / suggestion / nit)
      - Previously raised issues and their status (fixed/remaining)
      - New issues found in this cycle
      - Overall verdict: are there blocking issues?
    - Give your own assessment of each point (agree/disagree, severity, whether it's worth fixing)
    - End with a recommendation:
      - If blocking issues: "Run `/pr-fix` to work through them, then `/pr-cycle` again"
      - If no blocking issues: "Ready to merge? Run `gh pr merge`"
    - **Do NOT immediately start fixing anything** — discuss with the user first
    - Wait for the user to decide which points to address

## Edge Cases

- `gh` not installed or not authenticated → stop with clear error in step 0
- No uncommitted changes → skip commit, just push + trigger
- No PR exists → inform user, suggest creating one
- Base branch has new commits → handled by `/pr-rebase` (delegated in step 2)
- Branch already up-to-date with remote → skip push, just trigger review
- Review auto-triggered after push → skip manual trigger, go straight to polling
- Review times out → agent reports timeout with link to PR
- Push fails → show error, don't trigger review

## Notes

- The review bot typically responds within 1-5 minutes
- The review comment starts with `**Claude finished @<user>'s task in ...**`
- The actual feedback follows after a `---` separator
- Steps 1-7 run in the foreground (fast, interactive)
- Step 8 runs as a background Bash task (no permission issues, unlike background agents)
- When the Bash task completes, the raw comment is returned and summarized by the main agent (step 10)
- For deeper local analysis (silent failures, test coverage, type design), consider installing the complementary `pr-review-toolkit` plugin
