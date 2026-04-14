---
name: cycle
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

2. **Check if rebase is needed** — delegate to `/rebase --no-poll`:
   - Invoke the `/rebase` skill **with the `--no-poll` flag** so it does not poll for reviews itself (this skill handles polling in step 8). It will:
     - Determine the PR's base branch (authoritative source: `gh pr view`)
     - Detect divergence, ask the user for confirmation, execute or skip
     - Abort cleanly on conflicts
   - Proceed with this skill only if `/rebase` returned cleanly (up-to-date, rebased successfully, or user declined)
   - If conflicts aborted the rebase: stop this cycle, let the user resolve manually, then re-run `/cycle`

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
    - Check CI status: `gh pr checks <PR_NUMBER>` — fold the result into the status line
    - **Render the output following the shared format spec** at `${CLAUDE_PLUGIN_ROOT}/docs/REVIEW-OUTPUT-FORMAT.md`. Read that file before presenting. Required sections: header, status line, findings **markdown table**, optional previously-raised section, single-line recommendation.
    - **Do NOT deviate from the table format** — no prose cards, no per-finding headings, no nested bullets. See the "Forbidden formatting patterns" section of the spec.
    - **Do NOT immediately start fixing anything** — wait for the user to indicate which items to address.

## Edge Cases

- `gh` not installed or not authenticated → stop with clear error in step 0
- No uncommitted changes → skip commit, just push + trigger
- No PR exists → inform user, suggest creating one
- Base branch has new commits → handled by `/rebase` (delegated in step 2)
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
