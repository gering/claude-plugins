---
name: merge
description: |
  Merges the current PR safely with exhaustive preflight. Detects merge
  method via repo-allowed settings + historical pattern of last 20 PRs
  (rebase / squash / merge). Verifies CI, human reviews, open Claude
  blocking issues, mergeable state, and branch protection. Handles
  backup-convention-aware local branch cleanup (rename to backup/* if
  convention exists, else delete). Refuses --admin bypass — root-cause
  over workaround.

  Use when: user wants to "merge the PR", "ship it", "land the PR",
  "close out the PR", "merge and cleanup", PR is approved and ready to
  merge. Also when user says "mergen" / "PR abschließen" / "einmerge" /
  "ready to merge".
user_invocable: true
---

# Merge Pull Request

> Exhaustive preflight (rebase, CI, reviews, open Claude issues, mergeable state), merge-method detection, clean execution, and post-merge cleanup. Refuses to bypass branch protection — root-cause over workaround.

## Instructions

0. **Preflight**:
   - `command -v gh >/dev/null` — else stop with "gh CLI not installed — https://cli.github.com"
   - `gh auth status >/dev/null 2>&1` — else stop with "gh not authenticated — run: gh auth login"

1. **Identify PR**:
   - `git branch --show-current` — if on `main`/`master`, stop: "You're on the default branch — nothing to merge."
   - `gh pr view --json number,title,url,state,isDraft,baseRefName,headRefName,mergeable,mergeStateStatus,mergeCommit`
   - If no PR → stop, suggest `/open`
   - If state ≠ `OPEN` → stop with state info
   - If `isDraft` → ask: "PR is draft. Mark as ready for review? [y/N]"
     - `y`: `gh pr ready <N>`, continue
     - `n`: stop
   - Store `PR_NUMBER`, `BASE_BRANCH`, `HEAD_BRANCH`.

2. **Rebase check** — delegate to `/rebase`:
   - Run `/rebase`. It determines the base from the PR itself and handles rebase + conflict abort cleanly.
   - If a rebase happened: the branch needs to be pushed (with force-with-lease). `/rebase` will have handled the force-push confirmation.
   - If user declined a needed rebase → stop this skill: "Merge requires up-to-date branch. Re-run `/merge` after resolving."

3. **Local cleanliness**:
   - `git status --porcelain` — if anything: stop "Commit or stash local changes before merging."
   - `git log @{u}..HEAD --oneline 2>/dev/null` — if unpushed commits: stop "Push local commits first (`/cycle` handles this)."

4. **Refresh PR state** (GitHub recomputes after push/rebase):
   - Wait ~5s if anything was pushed/rebased in step 2
   - Re-run: `gh pr view <N> --json mergeable,mergeStateStatus,reviews`
   - Interpret `mergeStateStatus`:
     - `CLEAN` → ✅ ready
     - `HAS_HOOKS` → ✅ ready (hooks will run)
     - `BEHIND` → ⚠️ base moved again since step 2, re-run `/rebase`
     - `BLOCKED` → ⚠️ required reviews or checks missing (step 5 + 6 will detail)
     - `CONFLICTING` → ❌ stop: "Merge conflicts. Resolve manually (`git merge origin/<BASE>` or `git rebase`)"
     - `UNSTABLE` → ⚠️ CI not green but branch protection allows merge — treat as warning
     - `UNKNOWN` → wait 10s and retry up to 3× (GitHub still computing)

5. **CI status**:
   - `gh pr checks <N>`
   - Categorize: passed / failed / pending / skipped
   - If **any required check** is failing or pending → ⚠️ note it. Branch protection will block merge anyway; surface early.
   - `gh api repos/:owner/:name/branches/<BASE_BRANCH>/protection 2>/dev/null --jq '.required_status_checks.contexts // []'` — compare required contexts against current check results
   - If required checks missing → ❌ block: "Required check `<name>` hasn't run or failed"

6. **Human reviews**:
   - `gh pr view <N> --json reviews --jq '.reviews'`
   - Count latest review per reviewer: approved, changes_requested, commented, dismissed
   - Check branch protection: `gh api repos/:owner/:name/branches/<BASE_BRANCH>/protection --jq '.required_pull_request_reviews // null'`
   - If protection requires N approvals and we have <N → ❌ block
   - If any reviewer requested changes and hasn't re-approved → ❌ block
   - Otherwise → ✅

7. **Open Claude review issues**:
   - Fetch latest Claude review comment (see `/fix` step 2 for the jq query)
   - If exists AND newer than last push: parse for **blocking** severity issues
   - If exists BUT older than last push: mark as stale, advise `/cycle` first
   - Count open blocking issues
   - If > 0 → ⚠️ warn explicitly, require explicit confirmation before merging. **Do not silently ignore.**

8. **Detect merge method** (priority order):
   - **a) Repo allowed methods** — `gh api repos/:owner/:name --jq '{merge: .allow_merge_commit, squash: .allow_squash_merge, rebase: .allow_rebase_merge}'`
     - If only one is true → that's the only option. No question.
   - **b) Historical pattern** — analyze last 20 merged PRs:
     ```
     gh pr list --state merged --limit 20 --json number,mergeCommit
     ```
     For each `mergeCommit.oid`:
     - `git cat-file -p <sha> 2>/dev/null | head -3` — count `parent` lines
     - 2 parents → regular merge commit
     - 1 parent AND commit SHA not equal to PR's head SHA → squash merge
     - No merge commit present (PR's head commits appear directly in base history) → rebase+merge
     - Tally: if a method dominates (>80% of last 20) → propose it with the evidence, user confirms
     - If <5 merged PRs exist → fall through to (c)
   - **c) Ask user** — list allowed methods and ask. Default candidate ordering: `rebase` > `squash` > `merge`.

9. **Commit hygiene** (only if method is `merge` or `rebase`):
   - For `squash`: skip — commits get squashed, messages don't matter individually
   - Otherwise: `git log --format='%h %s' origin/<BASE>..HEAD`
   - Flag messages that look like work-in-progress: `^(wip|fix|asdf|temp|xxx)\b`, `.{1,5}$` (very short), starts with lowercase non-verb
   - If any found → show list, ask: "Clean up with `git rebase -i` before merging? [y/N]"
     - `y`: stop this skill, user cleans up, re-runs `/merge`
     - `n`: continue with current history

10. **Merge commit / squash message** (for `merge` or `squash`):
    - `squash`: propose a squash message — default to PR title + body summary
    - `merge`: propose default GitHub-generated message OR custom (ask user)
    - `rebase`: no merge message; individual commits land as-is

11. **Post-merge cleanup plan**:
    - Ask: "Delete remote branch after merge? [Y/n]" — default yes unless branch is protected
    - Ask: "Checkout `<BASE>` + pull after merge? [Y/n]" — default yes
    - Detect `task/*` branch pattern — if matches, offer: "This looks like a `work-system` task. Run `/close` after merge to clean up worktree + task file? [Y/n]"
    - Detect git worktree: `git worktree list` — if current branch is in a worktree AND not a task/*, offer to remove it

12. **Present final plan**:
    ```
    PR #42: <title>
    <URL>

    ── Preflight ──
    ✅ Branch up-to-date with main
    ✅ Local clean (no uncommitted/unpushed)

    ── GitHub state ──
    ✅ Mergeable: CLEAN
    ✅ CI: 8 passed, 0 failed
    ✅ Reviews: 1 approved, 0 changes requested
    ⚠️  Claude review: 1 blocking issue still open (#3 — src/foo.ts:42)

    ── Merge plan ──
    Method:          rebase+merge  (18/20 last PRs used rebase)
    Delete remote:   yes
    Checkout base:   yes, then pull
    Task cleanup:    /close will be offered (branch matches task/*)

    ⚠️  1 unresolved blocking review issue. Merge anyway?

    Proceed? [y/n]
    ```

13. **Execute merge**:
    - Based on method:
      - `rebase`: `gh pr merge <N> --rebase [--delete-branch]`
      - `squash`: `gh pr merge <N> --squash [--delete-branch] --subject "<title>" --body "<body>"`
      - `merge`:  `gh pr merge <N> --merge  [--delete-branch] --subject "<title>" --body "<body>"`
    - **Never pass `--admin`.** If branch protection blocks the merge: stop and surface the error. Root-cause over workaround.
    - Capture output + exit code

14. **Post-merge actions**:
    - If checkout base chosen: `git checkout <BASE> && git pull origin <BASE>`
    - **Local branch handling** — detect backup convention and execute directly (no prompt):
      - Run: `git for-each-ref --format='%(refname:short)' refs/heads/ | grep -E '^(backup|archive|old)/' | head -5`
      - If any backup-prefixed branches exist → project uses a backup convention. Use the same prefix.
        - Rename: `git branch -m <HEAD_BRANCH> <prefix>/<HEAD_BRANCH>` (e.g. `backup/feature/foo`)
        - If target name already exists, append date suffix: `<prefix>/<HEAD_BRANCH>-YYYYMMDD`
        - Report: "Moved to `<prefix>/<HEAD_BRANCH>` (backup convention detected)"
      - If no backup convention detected → `git branch -d <HEAD_BRANCH>`
        - If `-d` refuses (branch not fully merged, rare after successful PR merge) → report and ask user before `-D`
    - If task/*: offer `/close` (do not auto-run — hand over). Note that `/close` will take precedence over the backup handling above.
    - If worktree: offer `git worktree remove <path>` (ask first, do not auto-run)

15. **Final summary**:
    ```
    ✅ Merged PR #42 via rebase
    ✅ Deleted remote branch `feature/foo`
    ✅ Checked out `main`, pulled latest

    Next:
    - Run `/close` to clean up the task worktree  (task/* branch detected)
    ```

## Edge Cases

- Branch protection blocks merge → stop, surface exact reason from the GitHub error, never bypass with `--admin`
- `CONFLICTING` state → stop, instruct manual resolution (don't auto-rebase here — `/rebase` already ran)
- `UNKNOWN` mergeable state → retry with backoff (10s × 3), then ask user to retry `/merge` later
- No required checks configured + CI failing → warn but allow (no protection rule blocks it)
- Historical pattern detection fails (new repo, <5 merged PRs) → fall back to ask user
- Historical pattern is split (no >80% dominant method) → ask user, show the split
- Repo only allows one method → no question, just use it
- Multiple unresolved Claude blocking issues → require explicit `yes-merge-anyway` confirmation, not just `y`
- User is not a repo admin and PR needs admin merge → stop, explain, don't try `--admin`
- Backup branch name already exists (previous merge of same name) → append date suffix: `backup/feature-foo-20260414`

## Notes

- This skill **never** force-merges via `--admin`. If something is red, fix the root cause.
- Claude blocking issues are **warnings with explicit confirmation**, not hard blocks — the user may have discussed and accepted them already.
- Merge-method detection uses progressively weaker signals: repo-allowed > historical pattern > user choice. No stored convention — each merge verifies from scratch.
- Post-merge cleanup hands off to other skills (`/close`) rather than duplicating their logic.
- Safe to re-run: step 1 detects already-merged PRs via `state` field.
