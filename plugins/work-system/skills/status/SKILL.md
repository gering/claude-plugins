---
name: status
description: |
  Read-only task snapshot: branch state, PR, `/close`-readiness.
  Trigger: "check this task", "task status", "is this merged?".
user_invocable: true
---

# Check Task Status

> Verify if a task has already been completed or check its progress

## Arguments

- `$ARGUMENTS` - Optional: task name to check (without .md extension)

## Instructions

1. **Determine the task and its branch**:
   - Detect `<main-branch>`: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'` (fallback `main`/`master`).
   - **From a worktree** — `git branch --show-current` is **not** `<main-branch>`: that branch
     **is** the task branch. Set `<task-branch>` to it (it may be `task/<name>` from `/kickoff`
     or an original name kept by `/adopt`) and derive `<task-name>` by stripping a leading
     `task/`/`feature/`/`fix/`/`bugfix/`/`hotfix/`/`chore/`/`refactor/` prefix.
   - **By name** — `$ARGUMENTS` given, or running on `<main-branch>`: `<task-name>` = the
     argument (else list tasks and ask). Resolve `<task-branch>` to the real ref, in order:
     1. `task/<task-name>` if it exists (`git rev-parse --verify --quiet task/<task-name>`);
     2. else the first match of `git branch --all | grep -i "<task-name>"` (catches an adopted
        branch that kept its original name);
     3. else the branch recorded in `tasks/<task-name>.md` (adopt notes it when the rename was
        declined).
     If none resolves, the branch is gone — use `task/<task-name>` for the reporting text and
     rely on PR/commit evidence (3a/3c).
   - Below, **`<task-branch>`** is that resolved ref and **`<task-name>`** the stripped name.

2. **Read task file** (if exists):
   - Run: `cat tasks/<task-name>.md 2>/dev/null`
   - Extract key information:
     - Task title/goal
     - Files mentioned
     - Key terms for searching

3. **Search for evidence of completion** — a) and b) are authoritative *when present*;
   c) and d) corroborate, and become the best available evidence when a)/b) can't run
   (see step 4 for how they combine):

   a) **Check for the task's PR** (if `gh` is available) — strongest signal:
   - Primary, exact match on the resolved branch: `gh pr list --state all --head "<task-branch>" --limit 5 --json number,title,state,mergedAt,url`
   - Only if that returns nothing, fall back to a fuzzy search (may surface unrelated PRs):
     `gh pr list --state all --search "<task-name>" --limit 5 --json number,title,state,mergedAt,url`
   - A **merged** PR for `<task-branch>` means completed.

   b) **Check the task branch** — second-strongest signal:
   - `<main-branch>` was detected in step 1. Determine which main refs **exist** (a local-only
     or never-fetched repo has no remote-tracking ref — passing a missing ref makes git `fatal`):
     `<main-refs>` = the local `<main-branch>`, plus `origin/<main-branch>` **only if**
     `git rev-parse --verify --quiet origin/<main-branch>` succeeds; `<merge-ref>` =
     `origin/<main-branch>` if it exists, else `<main-branch>`.
   - Confirm the branch exists: `git branch --all --list "*<task-branch>"` (trailing-anchored,
     **not** `*<task-branch>*`, so task `foo` doesn't match sibling `task/foo-bar`).
   - **Merged?** `git branch --merged <merge-ref> --list "<task-branch>"` (local + exact — a stale
     `origin/<task-branch>` can't falsely match, and `<merge-ref>` prefers `origin/<main-branch>`
     so a GitHub merge counts even before you pull). Lists it → merged → completed.
   - **Present but NOT in `--merged`** → *inconclusive*, not proof of "not merged": squash/rebase
     merges (GitHub's default on many repos) rewrite SHAs so the tip is never an ancestor of
     main even after merge. Lean on a) or c). (`/close` step 8 handles the same caveat.)
   - **Absent** → likely completed and cleaned up by `/close`; confirm via a) or c).

   c) **Commit history** — corroboration, and the *primary* signal when `gh` is unavailable:
   - Run: `git log <main-refs> --oneline --grep="<task-name>" | head -10` (`<main-refs>` from 3b —
     the merged history of local and, when present, remote-tracking main; covers a PR merged on
     GitHub but not yet pulled, and never `fatal`s on a missing `origin/<main-branch>`; not
     `--all`, so unmerged WIP branches don't count).
   - A task name in a message is easy to produce by accident, so behind a)/b) it only supports.
     But when `gh` can't run and the branch is gone, task commits *present in main's history*
     are the best available completion evidence — report it with lower confidence.

   d) **Check mentioned files** (weak signal — if task mentions specific files):
   - Run: `git log <main-refs> --oneline -- <file-path> | head -5`
   - Activity, not completion. Corroboration only.

4. **Analyze and report**:

   Decision rule — combine the signals by confidence:
   - **COMPLETED (confirmed):** a merged PR for `<task-branch>` (3a), or a still-existing branch
     listed by `git branch --merged <merge-ref>` (3b).
   - **COMPLETED (likely, unconfirmed):** the branch is gone (cleaned up by `/close`) or `gh` is
     unavailable, AND task commits appear in `<main-refs>` (3c) — report COMPLETED, noting it's
     unconfirmed (no live PR/branch left to prove it).
   - **IN PROGRESS:** the branch exists with an open PR (3a), or with local commits and no merge
     evidence. Never assert "not merged" from `--merged` alone — a branch absent from it may
     still be squash/rebase-merged; say "merge unconfirmed" and fall through to 3a/3c first.
   - **NOT STARTED:** no PR, no branch, and no task commits in `<main-refs>`.

   **Strong evidence (completed)**:
   ```
   ✅ Task appears COMPLETED

   Evidence:
   • PR #123 "Fix calendar bug" — MERGED (2026-01-15)   ← authoritative (3a)
   • Branch task/fix-calendar already cleaned up by /close   ← corroborating
   • 3 commits for "calendar bug" present in main   ← corroborating (3c)

   Recommendation: Delete task file with /close
   ```
   (A *deleted* branch is corroboration, not 3b proof — `git branch --merged` can only list a
   branch that still exists. When the branch is gone, authority comes from the PR (3a) or, with
   no `gh`, the commit history (3c).)

   **Partial evidence (in progress)**:
   ```
   🔄 Task appears IN PROGRESS

   Evidence:
   • Branch task/fix-calendar exists (not merged)
   • PR #124 is open
   • No merged commits found

   Recommendation: Continue work or check PR status
   ```

   **No evidence (not started)**:
   ```
   📋 Task appears NOT STARTED

   No PRs, branches, or commits found for this task.

   Recommendation: Start with /kickoff
   ```

5. **Offer actions**:
   - If completed: "Delete task file?"
   - If in progress: "Open PR in browser?" / "Continue work?"
   - If not started: "Start this task?"
