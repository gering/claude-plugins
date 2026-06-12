---
name: cycle
description: |
  Full @claude review iteration: commit, push, trigger, poll, present
  issues. `--loop` auto-fixes agreed findings and re-cycles until clean.
  Trigger: "cycle the PR", "push and review", "loop the review".
user_invocable: true
---

# PR Review Feedback Loop

> Stage, commit, push, trigger Claude review on PR, wait for feedback in background, and display results.

## Arguments

`$ARGUMENTS` may carry flags and/or a commit message, in any order:

- `--loop` (alias `--auto`) — **loop mode**: after each review, autonomously fix every finding you agree with (incl. 🟡 suggestions and ⚪ nits) and re-cycle, repeating until the reviewer raises nothing you still agree with. See "Loop mode" below.
- `--max=N` — safety cap on loop iterations (default `10`). Ignored without `--loop`.
- Any remaining non-flag text — commit message for the pending changes of the first iteration.

Strip the flags first; whatever is left over is the commit message.

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

2. **Check if rebase is needed** — delegate to `/rebase --no-poll --auto`:
   - Invoke the `/rebase` skill **with `--no-poll` and `--auto`**:
     - `--no-poll`: this skill handles review polling itself in step 8.
     - `--auto`: the user invoked `/cycle`; that invocation authorizes rebase + force-push as preparation. Asking again would be a redundant prompt.
   - `/rebase` will determine the PR's base branch (authoritative: `gh pr view`), detect divergence, rebase + force-push if needed, and abort cleanly on conflicts.
   - Proceed with this skill only if `/rebase` returned cleanly (up-to-date or rebased successfully).
   - If conflicts aborted the rebase: stop this cycle, let the user resolve manually, then re-run `/cycle`.

3. **Handle uncommitted changes**:
   - Run: `git status --porcelain`
   - If changes exist:
     - If `$ARGUMENTS` provided, use as commit message
     - Otherwise, generate a concise commit message based on the changes (show diff first)
     - Separate **tracked** (modified/deleted) files from **untracked** files — surface the untracked list explicitly in the confirmation prompt so the user can spot accidental additions (`.env`, build artifacts, editor backups) before they get staged
     - ALWAYS ask for confirmation before committing when no `$ARGUMENTS` provided, including the list of files that will be staged
     - Stage all changes: `git add -A` — the preceding confirmation step is what makes this safe
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
    - If findings exist, append one tip line: `💡 /cycle --loop auto-fixes everything you'd agree with and re-cycles until the review is clean.` (Skip the tip if the review is already clean, or if this run is already in loop mode.)

## Loop mode (`--loop`)

When `--loop` (alias `--auto`) is present, `/cycle` stops being a single pass and becomes an autonomous converge-the-review loop. Invoking it **authorizes the autonomous commit + push cycle** — the same way a plain `/cycle` invocation authorizes its rebase + force-push. The only step still gated behind a confirmation is the final squash (it rewrites history and force-pushes).

### Setup (once, before the loop)

- Parse `--max=N` (default `10`). This caps total iterations so the loop can never run forever.
- Initialize counters: `ROUND = 0`, `FIXES_TOTAL = 0`, `FIX_COMMITS = 0`, and an `OPEN` list (findings you disagreed with, deduped across rounds).
- **Reuse a fresh review if one already exists**: if the latest `@claude` review on the PR is newer than the latest push (not stale) and has findings, skip the initial commit/push/trigger and go straight to "Fix agreed" with that review. Otherwise run one normal cycle (steps 1–10 above) to obtain the first review.

### Each iteration

1. **Fix agreed** — take the current review and, exactly like `/fix` steps 3–6, parse it into discrete findings and give each your own assessment (`Agree` / `Partial agree` / `Disagree`):
   - Fix every `Agree` and the accepted part of every `Partial agree` — **including 🟡 suggestions and ⚪ nits** (polish is in scope for the loop).
   - Before editing, confirm the reviewer's claim still matches the code (comment rot / already-fixed / line drift → skip that finding, don't force it).
   - Add every `Disagree` to the `OPEN` list (so it is reported, not silently dropped).
   - `FIXES_TOTAL += fixes applied this round`.
2. **Print per-round stats** (before the wait):
   ```
   🔁 Loop round <ROUND+1>/<MAX> · fixes total: <FIXES_TOTAL> · this round: <Y> · open (disagreed): <Z>
   ```
3. **Termination check** — break the loop (go to "Wrap-up") if any holds:
   - The review had **zero findings** → converged clean ✅.
   - **Nothing was agreed** this round (every finding is a `Disagree`) → only disagreements remain.
   - **No files changed** this round (everything agreed turned out to be comment-rot / already-fixed) → nothing actionable left.
   - `ROUND + 1 >= MAX` → safety cap hit.
   - The user said to stop (see "Interruptible").
4. **Re-cycle** — run steps 3–10 above (commit the fixes → push → hide outdated → trigger → poll). Use a terse commit message, e.g. `Address review round <ROUND+1>`. Increment `FIX_COMMITS += 1`.
5. `ROUND += 1`, then loop back to step 1 with the fresh review.

### Interruptible

The review wait is a background Bash poll, so the user can interject at any time. If they say "stop" / "enough" / "es reicht" (or interrupt), finish any in-flight edit, then break the loop and go straight to Wrap-up — do not start another round.

### Wrap-up

1. **Summary**:
   ```
   ✅ Loop finished after <ROUND> round(s) — <reason: clean | only disagreements | max reached | stopped by you>
   Fixes applied: <FIXES_TOTAL> across <FIX_COMMITS> commit(s)
   Still open (disagreed, not changed): <Z>
   ```
   If `OPEN` has entries, render them as a findings table per `${CLAUDE_PLUGIN_ROOT}/docs/REVIEW-OUTPUT-FORMAT.md` so the user can decide what to do with them.
2. **Offer to squash** — only if `FIX_COMMITS >= 2`:
   - Ask: "Squash the `<FIX_COMMITS>` review-loop fix commits into one? (Rewrites history + force-pushes.)"
   - On yes: `git reset --soft HEAD~<FIX_COMMITS>`, then a single commit:
     ```
     Apply review-loop fixes (<FIX_COMMITS> rounds, <FIXES_TOTAL> fixes)
     ```
     Then `git push --force-with-lease`. This collapses only the loop's fix commits — the original feature commits are untouched. `HEAD~<FIX_COMMITS>` stays correct even if `/rebase` ran mid-loop, because a rebase replays our commits without changing how many sit on top of the base.
   - On no: leave history as-is.
   - If `FIX_COMMITS < 2`: skip the offer silently (nothing to squash).
3. Hand control back. Suggest `/merge` if the loop converged clean, or manual follow-up / `/fix` if disagreements remain.

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
- `--loop` turns the single pass into an autonomous fix-agreed → re-cycle loop (capped by `--max`, default 10); it is the autonomous counterpart to the interactive `/fix`, which never re-cycles on its own
- For deeper local analysis (silent failures, test coverage, type design), consider installing the complementary `pr-review-toolkit` plugin
