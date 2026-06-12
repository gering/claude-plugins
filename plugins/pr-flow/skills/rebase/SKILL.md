---
name: rebase
description: |
  Rebases against the PR's actual base: proceeds without asking when
  changed files don't overlap, menu otherwise, aborts on conflicts.
  Trigger: "rebase against main", "sync with base", "am I behind?".
user_invocable: true
---

# PR Rebase Check

> Detect whether the current branch is behind its target/base branch. If yes, show what's new on base, ask for confirmation, and rebase. Abort cleanly on conflicts. After a successful force-push (standalone use only), wait for an auto-triggered Claude review and present the result.

## Arguments

- `--no-poll` — Skip the post-push review polling step. Set automatically when this skill is invoked from `/cycle` or `/open` (the parent does its own polling).
- `--auto` — Skip the step-5 confirmation prompt. Proceed as if the user answered `y` — rebase, then force-push if an upstream exists. Used when the parent skill (`/merge`) has already authorized the rebase+push as part of its own invocation. Conflicts still abort cleanly. Stopping conditions (uncommitted changes, missing upstream, etc.) still apply.

## Why this skill exists

The base branch is a **property of the PR**, not a local assumption. A branch might be named `feature/foo` but PR'd against `develop`, not `main`. Using the wrong base for the rebase check gives false negatives (branch looks up-to-date but isn't) or false positives (suggesting a rebase against the wrong target).

This skill is also used internally by `/open` (step 2) and `/cycle` (step 2) — run it standalone whenever you want to verify.

## Instructions

0. **Preflight**:
   - `git` is assumed. For PR base detection: `command -v gh >/dev/null || { echo "gh CLI not installed — https://cli.github.com"; exit 1; }`
   - `gh auth status >/dev/null 2>&1 || { echo "gh not authenticated — run: gh auth login"; exit 1; }`

1. **Current branch**:
   - Run: `git branch --show-current`
   - If on `main`/`master` (or any default branch): stop "You're on the default branch — nothing to rebase."
   - Store as `CURRENT_BRANCH`.

2. **Determine the base branch** (priority order — the PR is authoritative):
   - **a) Active PR:** `gh pr view --json baseRefName,number,url 2>/dev/null`
     - If a PR exists → use `baseRefName` as `BASE_BRANCH`. Store PR number for context in messages.
   - **b) Upstream tracking:** if no PR, check: `git for-each-ref --format='%(upstream:short)' refs/heads/<CURRENT_BRANCH>` — if set and not equal to current, use that (strip `origin/` prefix)
   - **c) Repo default:** `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
   - **d) Fallback:** ask the user which base branch to check against (default suggestion: `main`)
   - Tell the user which source was used, e.g. "Checking rebase against `develop` (from PR #42)" — so they can correct if wrong.

3. **Fetch base**:
   - Run: `git fetch origin <BASE_BRANCH>`
   - If fetch fails (network, permissions, branch gone) → show error, stop.

4. **Check for divergence**:
   - New commits on base not in current branch:
     ```
     git log --oneline HEAD..origin/<BASE_BRANCH>
     ```
   - If empty → ✅ "Branch is up-to-date with `<BASE_BRANCH>`. No rebase needed." — stop.
   - If non-empty → continue to step 5.

5. **Assess conflict risk, then proceed or ask**:

   Gather the facts first:
   - Upstream: `git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null` → store as `HAS_UPSTREAM` (true if non-empty).
   - File overlap between both sides since the merge-base:
     ```
     comm -12 <(git diff --name-only HEAD...origin/<BASE_BRANCH> | sort) \
              <(git diff --name-only origin/<BASE_BRANCH>...HEAD | sort)
     ```
   - Always print the new-commits summary (from step 4) so the user sees what's coming in.

   **Safe path — overlap is empty:** do NOT prompt. Announce in one line and continue to step 6:
   ```
   No file overlap with `<BASE_BRANCH>` — rebasing<if HAS_UPSTREAM> + force-push (--force-with-lease)</if> now.
   ```
   Invoking `/rebase` is the authorization for this conflict-free case. Should a conflict occur anyway (rename, semantic), step 7's clean abort still catches it.

   **Decision path — overlap is non-empty:** a judgment call is needed, so present a real selection menu via the **AskUserQuestion tool** — never a free-text `y/n/d` prompt:
   - Question: "Branch `<CURRENT_BRANCH>` is N commit(s) behind `<BASE_BRANCH>`; M overlapping file(s): <list, truncate at 5>. Rebase<if HAS_UPSTREAM> + force-push</if>?"
   - Options:
     1. **Rebase + force-push** (Recommended) — continue to step 6. This single choice authorizes both the rebase AND the force-push (if `HAS_UPSTREAM`); do NOT re-ask later.
     2. **Show diff first** — run `git log -p HEAD..origin/<BASE_BRANCH>` for the overlapping files (paginate), then re-present the menu.
     3. **Leave as-is** — stop with ⚠️ "Rebase skipped — branch remains N commits behind `<BASE_BRANCH>`".

   **If `--auto` was passed**: skip the menu even on overlap — print the summary, then proceed as if option 1 was chosen. The parent skill's invocation is the authorization.

6. **Uncommitted changes guard**:
   - Run: `git status --porcelain`
   - If changes exist, ask via the **AskUserQuestion tool** (menu, not free text):
     - Question: "Uncommitted changes present — they'd block the rebase. Stash them?"
     - Options:
       1. **Auto-stash** (Recommended) — `git stash push -m 'pr-flow rebase auto-stash'`, remember to pop after the rebase
       2. **Abort** — stop, user commits/stashes manually and re-runs `/rebase`
   - In `--auto` mode: do NOT stash silently — stop with the error instead (the parent skill surfaces it). Mutating the working tree needs an explicit choice.

7. **Execute rebase**:
   - Run: `git rebase origin/<BASE_BRANCH>`
   - **On success**:
     - If auto-stash was used, run `git stash pop`. If pop conflicts, warn but continue.
     - ✅ "Rebased successfully. Branch is now on top of `<BASE_BRANCH>` (N commits replayed)."
   - **On conflicts**:
     - Run: `git rebase --abort` (clean state restored)
     - If auto-stash was used, `git stash pop` to restore working tree
     - ❌ "Rebase conflicts detected. Aborted cleanly — branch is back to its original state."
     - List the conflicting files (from the rebase output), then ask via the **AskUserQuestion tool** (menu, not free text):
       - **Resolve manually** — user runs `git rebase origin/<BASE_BRANCH>`, fixes conflicts, `git rebase --continue` in their own shell; this skill stops here
       - **Merge instead** — `git merge origin/<BASE_BRANCH>` (preserves branch history, creates a merge commit)
       - **Leave as-is** — stop, branch stays behind
     - Do NOT automatically resolve conflicts, regardless of choice or `--auto`.

8. **Post-rebase: remote state**:
   - If `HAS_UPSTREAM` is true: execute `git push --force-with-lease` directly — the `y` from step 5 already covered this. Do NOT ask again.
     - Set `FORCE_PUSHED = true` on success.
     - If the push is rejected (e.g. remote advanced since `y`), stop with the error and suggest re-running `/rebase` — do NOT auto-retry with `--force`.
   - If `HAS_UPSTREAM` is false: no push needed, branch is local only (`FORCE_PUSHED = false`)

9. **Wait for auto-triggered review** (standalone only):
   - **Skip entirely** if any of these are true:
     - `--no-poll` argument was passed (parent skill /cycle or /open will poll)
     - `FORCE_PUSHED` is false (no push → no auto-trigger possible)
     - No PR number known from step 2 (no PR → nothing to review)
   - Otherwise:
     - Store `TRIGGER_ISO = $(date -u +%Y-%m-%dT%H:%M:%SZ)` immediately after the force-push
     - Wait ~15 seconds (auto-trigger workflows fire within 10-20s)
     - Check for an auto-triggered review:
       ```
       bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" latest-after <PR_NUMBER> "<TRIGGER_ISO>"
       ```
     - If output is non-empty → a review has started. Launch background polling:
       ```
       bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" poll <PR_NUMBER> "<TRIGGER_ISO>"
       ```
       Use the **Bash tool** with `run_in_background: true`. When it completes, render the review following `${CLAUDE_PLUGIN_ROOT}/docs/REVIEW-OUTPUT-FORMAT.md`.
     - If output is empty → no auto-trigger detected. Inform user they can run `/cycle` to trigger a review manually. Do NOT trigger automatically — `/rebase` is a rebase tool, not a review trigger.

10. **Final summary**:
    ```
    ✅ Rebased `<CURRENT_BRANCH>` onto `<BASE_BRANCH>`

    Replayed N commits. Base moved forward by M commits.
    <if upstream existed and pushed>    Force-pushed to origin.
    <if upstream existed but not pushed> Next: `git push --force-with-lease` when ready.
    <if auto-trigger detected>          Review polling in background — results will be presented when complete.
    <if pushed but no auto-trigger>     No auto-triggered review — run `/cycle` to trigger one.
    <if used by /open or /cycle>        Continuing with the parent skill.
    ```

## Edge Cases

- Detached HEAD → stop: "Not on a branch."
- Base branch not reachable (deleted on remote) → stop with the failed `git fetch` error and suggest re-setting the PR's base via `gh pr edit --base <branch>`
- Branch already ahead of base and has no missing commits → ✅ up-to-date, no-op
- User on default branch → stop (nothing to rebase)
- `gh` unavailable → fall back to upstream/symbolic-ref detection; warn that PR-specific base cannot be verified
- Rebase conflicts → **always abort cleanly**, never leave a half-rebased state
- PR's base branch has been renamed/deleted on remote → show error with remediation hints
- Rebase moves many commits and user wants interactive rebase instead → out of scope for this skill; user should run `git rebase -i` manually

## Notes

- **Confirmation model**: zero file overlap → proceed without asking (the `/rebase` invocation is the authorization; conflicts still abort cleanly). Overlap → one menu choice authorizes both rebase and force-push. Decisions are always real selection menus (AskUserQuestion), never free-text prompts
- Conflicts → abort + suggest, never auto-resolve
- Safe to run repeatedly: if no rebase is needed, it's a 2-command no-op
- Designed to be called both standalone and from `/open` / `/cycle`
