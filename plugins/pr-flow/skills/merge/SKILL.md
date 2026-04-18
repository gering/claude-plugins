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

## Execution Principles

**No sanity-check prompts.** When every preflight check is ✅ (or ➖ N/A), execute the merge immediately without a final "Proceed? / Merge ausführen?" confirmation. The user ran `/merge` — that IS the authorization.

**Only ask when a decision is needed.** A prompt is warranted only for: draft-to-ready flip (step 1), rebase confirmation (delegated to `/rebase`), merge-method when ambiguous (step 9), WIP commit cleanup (step 10), and the three-way f/m/a decision when ⚠️ warnings exist (step 13). Cleanup defaults (step 12) apply silently unless something is protected/unusual.

**If ⚠️ warnings exist**, present the three-way prompt in step 13 **exactly once**. Never stack it with additional confirmation questions.

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

2. **Rebase check** — delegate to `/rebase --no-poll --auto`:
   - Run `/rebase` with **both** `--no-poll` and `--auto`. Rationale:
     - `--no-poll`: polling would delay the merge — any review should already have been handled by a prior `/cycle`.
     - `--auto`: the user invoked `/merge`; that invocation authorizes rebase + force-push as preflight. Asking again would be a redundant second prompt.
   - `/rebase --auto` still aborts cleanly on conflicts (no destructive behavior skipped — only the routine confirmation is skipped).
   - If `/rebase` aborts due to conflicts → stop this skill: "Merge requires up-to-date branch. Resolve conflicts, then re-run `/merge`."

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
   - Fetch the latest Claude review comment via the shared helper:
     ```
     bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" latest <PR_NUMBER>
     ```
     Returns the raw body of the most recent `@claude`-authored comment, or empty if none.
   - If exists AND newer than last push: parse for **blocking** severity issues
   - If exists BUT older than last push: mark as stale, advise `/cycle` first
   - Count open blocking issues
   - If > 0 → ⚠️ warn explicitly, require explicit confirmation before merging. **Do not silently ignore.**

8. **Documentation readiness** (read-only at this stage — mirrors `/open` checks 3c-3f):
   - Compare branch diff against base: `git diff origin/<BASE_BRANCH>...HEAD --name-only`
   - **README freshness**: user-visible code changed (`src/`, `lib/`, `plugins/`, `skills/`, public entry points) but `README.md` itself was not touched → ⚠️ "README may be stale" (manual — cannot auto-write). Changes to other `*.md` files (CONTRIBUTING, CHANGELOG, etc.) do not count as a README update.
   - **Version bump**: if project versions releases (`package.json`/`plugin.json`/`Cargo.toml`/`pyproject.toml` with version field + git tags or recent bump commits) AND no version field bumped on this branch AND changes look user-facing (feat/fix/breaking from commit messages) → ⚠️ "Version not bumped" with suggestion (patch/minor/major). Auto-fixable.
   - **Changelog**: if `CHANGELOG.md`/`HISTORY.md`/`.changeset/` exists AND not touched on this branch AND changes are user-facing → ⚠️ "Changelog missing entry". Auto-fixable (draft entry).
   - **Knowledge/conventions**: detect any knowledge location (`.claude/knowledge/`, `.cursor/rules/`, `AGENTS.md`, `CONVENTIONS.md`, `docs/adr/`, etc.). If new patterns detected (via commit-message heuristic from `/open` step 3f) AND knowledge location not touched → ⚠️ "Knowledge gap". Auto-fixable.
   - Categorize each finding as **auto-fixable** (version, changelog, knowledge) or **manual** (README).
   - Collect warnings into `DOC_WARNINGS` — surface them in the final plan (step 13) and handle user decision there.
   - **Do not mutate anything here** — this is read-only inspection.

9. **Detect merge method** (priority order):
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

10. **Commit hygiene** (only if method is `merge` or `rebase`):
   - For `squash`: skip — commits get squashed, messages don't matter individually
   - Otherwise: `git log --format='%h %s' origin/<BASE>..HEAD`
   - Flag messages that look like work-in-progress: `^(wip|fix|asdf|temp|xxx)\b`, `.{1,5}$` (very short), starts with lowercase non-verb
   - If any found → show list, ask: "Stop so you can clean up history with `git rebase -i`, then re-run `/merge`? [y/N]"
     - `y`: stop this skill immediately — do NOT run `git rebase -i` from Claude. The user runs it in their own shell, then re-invokes `/merge`.
     - `n`: continue with current history

11. **Merge commit / squash message** (for `merge` or `squash`):
    - `squash`: propose a squash message — default to PR title + body summary
    - `merge`: propose default GitHub-generated message OR custom (ask user)
    - `rebase`: no merge message; individual commits land as-is

12. **Post-merge cleanup plan** (apply defaults silently — do NOT prompt per option):
    - Delete remote branch after merge: **default yes** (unless the remote branch is protected; check via `gh api repos/:owner/:name/branches/<HEAD_BRANCH>/protection 2>/dev/null` — if protection exists, default no)
    - Checkout `<BASE>` + pull after merge: **default yes**
    - `task/*` branch pattern: detect. If matches, the final summary will suggest `/close` (not auto-run, just a handoff message). No prompt here.
    - Git worktree (non-task): detect via `git worktree list`. If current branch is in a worktree AND not `task/*`, the final summary will suggest `git worktree remove <path>` as a next step. No prompt here.
    - Collect the chosen defaults into the merge plan presentation — user sees them in step 13, can still abort by picking `a` if doc warnings exist, or by Ctrl-C.

13. **Present final plan** (with documentation decision if warnings exist):
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

    ── Documentation ──
    ⚠️  Version not bumped (detected feat: suggest 1.2.3 → 1.3.0)    [auto-fixable]
    ⚠️  CHANGELOG.md missing entry                                    [auto-fixable]
    ⚠️  README.md may be stale (src/ changed, docs untouched)         [manual]
    ✅ Knowledge: internal changes, nothing generalizable

    ── Merge plan ──
    Method:          rebase+merge  (18/20 last PRs used rebase)
    Delete remote:   yes
    Checkout base:   yes, then pull
    Task cleanup:    /close will be offered (branch matches task/*)
    ```

    - **If `DOC_WARNINGS` is empty AND no other ⚠️ remains** (all checks ✅ or ➖): **proceed directly to step 14 — do NOT append any prompt.** Not "Proceed? [y/n]", not "Merge ausführen?", not any sanity-check question. The user invoked `/merge`; that is the authorization. Just print the plan table and go.
    - **If one or more ⚠️ warnings remain** (doc gaps and/or unresolved Claude blocking issues), ask exactly one prompt:
      ```
      How would you like to proceed?
        [f] fix automatically — apply auto-fixable doc updates, commit,
            hand off to /cycle for push + re-review. You'll run /merge
            again after the new review cycle.
        [m] merge anyway — proceed with the warnings as-is (explicit
            override; doc gaps remain on main)
        [a] abort — I'll fix manually, run /merge later
      ```
    - Handle the response:
      - `f` (auto-fix path):
        1. For each **auto-fixable** finding, apply the update directly:
           - **Version**: bump the detected version field per the suggestion. If multiple files (e.g. plugin.json + marketplace.json + CLAUDE.md), update all in sync.
           - **Changelog**: draft an entry (Added/Changed/Fixed per detected change type), append under the unreleased section or next version heading.
           - **Knowledge**: invoke `/curate` when `knowledge-system` is detected, otherwise append a short entry directly to the most relevant knowledge/conventions file.
        2. **Manual findings** (e.g. README) are NOT auto-fixed — list them explicitly: "Still needs your attention: <list>". Continue anyway; user acknowledged by choosing `f`.
        3. Stage changes: `git add -A`
        4. Commit: `git commit -m "docs: update for PR #<N> merge"` (or more specific subject based on what was changed)
        5. Stop this skill with: "Doc updates committed. Run `/cycle` to push + re-review. Re-run `/merge` when ready."
      - `m` (merge anyway): proceed to step 14. The doc gaps remain on main — user's explicit choice.
      - `a` (abort): stop cleanly, leave state as-is.

14. **Execute merge**:
    - Based on method:
      - `rebase`: `gh pr merge <N> --rebase [--delete-branch]`
      - `squash`: `gh pr merge <N> --squash [--delete-branch] --subject "<title>" --body "<body>"`
      - `merge`:  `gh pr merge <N> --merge  [--delete-branch] --subject "<title>" --body "<body>"`
    - **Never pass `--admin`.** If branch protection blocks the merge: stop and surface the error. Root-cause over workaround.
    - Capture output + exit code

15. **Post-merge actions**:
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

16. **Final summary**:
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
- Project uses no versioning / no changelog / no knowledge system → doc step 8 marks those as ➖ N/A; nothing to warn about
- Only manual doc warnings (e.g. README) with no auto-fixable ones → `f` option still offered but explained as "only manual items remain — I'll flag them and continue"; user can still choose `m` to accept as-is

## Notes

- This skill **never** force-merges via `--admin`. If something is red, fix the root cause.
- Claude blocking issues are **warnings with explicit confirmation**, not hard blocks — the user may have discussed and accepted them already.
- Merge-method detection uses progressively weaker signals: repo-allowed > historical pattern > user choice. No stored convention — each merge verifies from scratch.
- Post-merge cleanup hands off to other skills (`/close`) rather than duplicating their logic.
- Safe to re-run: step 1 detects already-merged PRs via `state` field.
- Documentation readiness (step 8) is **read-only**; auto-fix only happens after the user picks `f` in step 13. This keeps the check cheap if the user wants to skip it with `m`.
- Auto-fix does not push directly — it commits locally and hands off to `/cycle` so review cycle is preserved as the single place that handles push + review orchestration.
