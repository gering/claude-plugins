---
name: rebase
description: |
  Rebases against the PR's actual base: proceeds without asking when
  changed files don't overlap, menu otherwise, aborts on conflicts.
  Trigger: "rebase against main", "sync with base", "am I behind?".
user_invocable: true
---

# PR Rebase Check

> Detect whether the current branch is behind its target/base branch. If yes, show what's new on base, then rebase — proceeding without a prompt when the changed files don't overlap, asking via a menu when they do. Abort cleanly on conflicts. After a successful force-push (standalone use only), wait for an auto-triggered Claude review and present the result.

## Arguments

- `--no-poll` — Skip the post-push review polling step. Set automatically when this skill is invoked from `/cycle` or `/open` (the parent does its own polling).
- `--auto` — Skip the step-5 menu entirely (even when files overlap) — rebase, then force-push if an upstream exists. Used when the parent skill (`/merge`, `/cycle`) has already authorized the rebase+push as part of its own invocation. Conflicts still abort cleanly. Uncommitted changes are auto-stashed and popped afterward (not a stopping condition under `--auto`); other stopping conditions (missing upstream, detached HEAD, etc.) still apply.

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
   - File overlap between both sides since the merge-base. Pass `--no-renames` so a rename can't hide a conflict: with rename detection on, a base-side rename `foo`→`bar` shows only `bar`, so if the branch edits `foo` the paths miss each other — and a rebase that CAN conflict (when the rename commit also touched those lines) would slip through as "no overlap". `--no-renames` surfaces the old path (`foo` as a delete) so that overlap is caught. This is deliberately conservative: a clean rename git would auto-resolve also gets flagged (an extra menu, never a skipped check). It also pins the result against the user's `diff.renames` config so the classification is deterministic.
     ```
     comm -12 <(git diff --no-renames --name-only HEAD...origin/<BASE_BRANCH> | sort) \
              <(git diff --no-renames --name-only origin/<BASE_BRANCH>...HEAD | sort)
     ```
   - Always print the new-commits summary (from step 4) so the user sees what's coming in.

   **Safe path — overlap is empty:** do NOT prompt (beyond step 6's uncommitted-changes guard, which may still ask in standalone mode). Announce in one line and continue to step 6:
   ```
   No file overlap with `<BASE_BRANCH>` — proceeding with rebase<if HAS_UPSTREAM> + force-push (--force-with-lease)</if>.
   ```
   Invoking `/rebase` is the authorization for this conflict-free case. The actual force-push happens in step 8, after the rebase succeeds — if a conflict surfaces anyway (semantic, or a rename `--no-renames` didn't catch), step 7's clean abort catches it and no force-push occurs.

   **Decision path — overlap is non-empty:** a judgment call is needed, so present a real selection menu via the **AskUserQuestion tool** — never a free-text `y/n/d` prompt:
   - Question: "Branch `<CURRENT_BRANCH>` is N commit(s) behind `<BASE_BRANCH>`; M overlapping file(s): <list, truncate at 5>. Rebase<if HAS_UPSTREAM> + force-push</if>?"
   - Options:
     1. **Rebase + force-push** (Recommended) — continue to step 6. This single choice authorizes both the rebase AND the force-push (if `HAS_UPSTREAM`); do NOT re-ask later.
     2. **Show diff first** — run `git log -p HEAD..origin/<BASE_BRANCH>` for the overlapping files (paginate), then re-present the menu.
     3. **Leave as-is** — stop with ⚠️ "Rebase skipped — branch remains N commits behind `<BASE_BRANCH>`".

   **If `--auto` was passed**: skip the menu even on overlap — print the summary, then proceed as if option 1 was chosen. The parent skill's invocation is the authorization.

6. **Uncommitted changes guard**:
   - Run: `git status --porcelain`
   - If changes exist:
     - **Standalone**: ask via the **AskUserQuestion tool** (menu, not free text):
       - Question: "Uncommitted changes present — they'd block the rebase. Stash them?"
       - Options:
         1. **Auto-stash** (Recommended) — `git stash push -m 'pr-flow rebase auto-stash'`, remember to pop after the rebase
         2. **Abort** — stop, user commits/stashes manually and re-runs `/rebase`
     - **`--auto` mode**: auto-stash directly — `git stash push -m 'pr-flow rebase auto-stash'`, remember to pop after the rebase. No prompt: stash+pop is reversible (step 7 pops it on every `--auto` exit — clean success, conflict-abort, or other failure — and surfaces it if the pop itself conflicts), and the parent's invocation already authorized the rebase preparation — that is what `--auto` is for. This matters for `/cycle`, which rebases (step 2) *before* committing its pending changes (step 3): a hard stop here would abort the cycle before anything is committed.

7. **Execute rebase**:
   - Run: `git rebase origin/<BASE_BRANCH>`
   - **On success**:
     - If auto-stash was used, run `git stash pop`. If the pop itself **conflicts**, do NOT silently continue — STOP and report that the working tree now holds stash-pop conflict markers that must be resolved before any commit. A parent (`/cycle`) must NOT `git add -A` over them. This is not a "returned cleanly" outcome.
     - ✅ "Rebased successfully. Branch is now on top of `<BASE_BRANCH>` (N commits replayed)."
   - **On conflicts** (rebase reports merge conflicts):
     - Run: `git rebase --abort` (branch back to its pre-rebase commit; an auto-stash, if any, is still held — NOT yet popped)
     - ❌ "Rebase conflicts detected. Aborted cleanly — branch is back to its original state."
     - **`--auto` mode**: if auto-stash was used, `git stash pop` to restore the parent's working tree (the parent expects its changes back). If that pop **conflicts**, report it explicitly as a stash-pop conflict (working tree now holds markers) — NOT as a plain "rebase aborted" — so the parent does not `git add -A` over it. Then report the conflicting files and stop, returning control to the parent (`/cycle`/`/merge` handle the outcome). Do NOT present a menu.
     - **Standalone**: do NOT pop an auto-stash yet — a dirty tree would block the options below. List the conflicting files, then ask via the **AskUserQuestion tool** (menu, not free text):
       - **Resolve manually** — run `git rebase origin/<BASE_BRANCH>`, fix conflicts, `git rebase --continue`, then `git stash pop` if an auto-stash is held
       - **Merge instead** — `git merge origin/<BASE_BRANCH>` (creates a merge commit), then `git stash pop` if an auto-stash is held
       - **Leave as-is** — stop, branch stays behind; if an auto-stash is held, `git stash pop` now to restore the working tree
     - If an auto-stash is held and its pop is deferred to an option above, tell the user explicitly: "Your uncommitted changes are safe in stash `pr-flow rebase auto-stash` — pop them when done."
     - Do NOT automatically resolve conflicts, regardless of choice or mode.
   - **On any other failure** (non-zero exit that is neither clean success nor a merge conflict — e.g. a leftover `rebase-apply`/`rebase-merge` dir, a rejecting pre-rebase hook, an unreachable base): if a rebase is in progress, `git rebase --abort`; then if auto-stash was used, `git stash pop` so the changes are never stranded — and if that pop conflicts, surface the stash-pop markers (parent must not `git add -A` over them), same as the other branches; stop with the raw error. **Never leave an auto-stash dangling.**

8. **Post-rebase: remote state**:
   - If `HAS_UPSTREAM` is true: execute `git push --force-with-lease` directly — step 5 already authorized this (safe path: the `/rebase` invocation; decision path: option 1; `--auto`: the parent's invocation). Do NOT ask again.
     - Set `FORCE_PUSHED = true` on success.
     - If the push is rejected (remote advanced since the last fetch), stop with the error and suggest re-running `/rebase` — do NOT auto-retry with `--force`.
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

- **Confirmation model**: zero file overlap → proceed without asking about the rebase itself (the `/rebase` invocation is the authorization; conflicts still abort cleanly). Overlap → one menu choice authorizes both rebase and force-push. The step-6 uncommitted-changes guard is independent of overlap and may still prompt on the safe path (standalone only — under `--auto` it auto-stashes instead of prompting). Decisions are always real selection menus (AskUserQuestion), never free-text prompts
- Conflicts → abort + suggest, never auto-resolve
- Safe to run repeatedly: if no rebase is needed, it's a 2-command no-op
- Designed to be called both standalone and from `/open` / `/cycle`
