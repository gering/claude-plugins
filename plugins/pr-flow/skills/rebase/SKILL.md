---
name: rebase
description: |
  Standalone rebase check against the PR's base branch. Determines the
  base authoritatively from `gh pr view` (not the local default), shows
  what's new on base, asks for confirmation, executes with auto-stash
  support, aborts cleanly on conflicts, and warns before any
  force-with-lease push. Also invoked internally by /create and
  /cycle.

  Use when: user wants to "rebase against main", "update branch with
  latest", "am I behind", "check if rebase needed", "sync with base",
  has a long-running branch that might need catching up. Also when user
  says "rebasen" / "auf main aktualisieren" / "ist der branch aktuell?".
user_invocable: true
---

# PR Rebase Check

> Detect whether the current branch is behind its target/base branch. If yes, show what's new on base, ask for confirmation, and rebase. Abort cleanly on conflicts.

## Why this skill exists

The base branch is a **property of the PR**, not a local assumption. A branch might be named `feature/foo` but PR'd against `develop`, not `main`. Using the wrong base for the rebase check gives false negatives (branch looks up-to-date but isn't) or false positives (suggesting a rebase against the wrong target).

This skill is also used internally by `/create` (step 2) and `/cycle` (step 2) — run it standalone whenever you want to verify.

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

5. **Show what's new and ask**:
   ```
   Branch `<CURRENT_BRANCH>` is behind `<BASE_BRANCH>` by N commit(s):

     abc123 Fix null check in auth handler
     def456 Bump dependency X to 2.3.0
     789aaa Refactor logger init

   Rebase `<CURRENT_BRANCH>` onto `origin/<BASE_BRANCH>`?
   [y] yes, rebase now
   [n] no, leave as-is (warning will remain)
   [d] show full diff first before deciding
   ```
   - `d`: run `git log -p HEAD..origin/<BASE_BRANCH>` (paginate for large diffs), then ask again
   - `n`: stop with ⚠️ "Rebase skipped — branch remains N commits behind `<BASE_BRANCH>`"
   - `y`: continue to step 6

6. **Uncommitted changes guard**:
   - Run: `git status --porcelain`
   - If changes exist: stop with error "Uncommitted changes present — commit or stash before rebasing."
     - Offer: "Stash them automatically? `git stash push -m 'pr-flow rebase auto-stash'` — [y/N]"
     - If yes: stash, remember to pop after rebase
     - If no: stop, user handles it

7. **Execute rebase**:
   - Run: `git rebase origin/<BASE_BRANCH>`
   - **On success**:
     - If auto-stash was used, run `git stash pop`. If pop conflicts, warn but continue.
     - ✅ "Rebased successfully. Branch is now on top of `<BASE_BRANCH>` (N commits replayed)."
   - **On conflicts**:
     - Run: `git rebase --abort` (clean state restored)
     - If auto-stash was used, `git stash pop` to restore working tree
     - ❌ "Rebase conflicts detected. Aborted cleanly — branch is back to its original state."
     - List the conflicting files (from the rebase output) and suggest:
       - "Option A: Resolve manually with `git rebase origin/<BASE_BRANCH>`, fix conflicts, `git rebase --continue`"
       - "Option B: Merge instead — `git merge origin/<BASE_BRANCH>` (preserves branch history but creates a merge commit)"
       - Ask which the user prefers — do NOT automatically resolve conflicts.

8. **Post-rebase: remote state**:
   - If the branch has an upstream: warn that a force-push will be needed
     - `git push --force-with-lease` (safer than `--force`)
     - Ask user for confirmation before pushing. **Never force-push without explicit approval.**
   - If no upstream yet: no push needed, branch is local only

9. **Final summary**:
   ```
   ✅ Rebased `<CURRENT_BRANCH>` onto `<BASE_BRANCH>`

   Replayed N commits. Base moved forward by M commits.
   <if upstream existed> Next: `git push --force-with-lease` when ready.
   <if used by /create or /cycle> Continuing with the parent skill.
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

- This skill **never force-pushes without confirmation** — you stay in control
- Conflicts → abort + suggest, never auto-resolve
- Safe to run repeatedly: if no rebase is needed, it's a 2-command no-op
- Designed to be called both standalone and from `/create` / `/cycle`
