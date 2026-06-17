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
   - If `$ARGUMENTS` provided → `<task-name>` = `$ARGUMENTS`; its branch follows the convention
     `task/<task-name>` (3b's broad fallback also catches a branch adopted under another name).
   - Otherwise, from `git branch --show-current`:
     - Detect `<main-branch>` (see 3b). If the current branch **is** `<main-branch>`, list
       available tasks and ask which to check.
     - Else this worktree's branch **is** the task branch — it may be `task/<name>` from
       `/kickoff` or an original name kept by `/adopt`. Derive `<task-name>` by stripping a
       leading `task/`/`feature/`/`fix/`/`bugfix/`/`hotfix/`/`chore/`/`refactor/` prefix.
   - Throughout below, **`<task-branch>`** is the resolved ref: the current branch when run
     inside a worktree, or `task/<task-name>` when only a name was given. `<task-name>` is the
     stripped name.

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
   - Detect `<main-branch>`: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'` (fallback `main`/`master`).
   - Confirm the branch exists — exact `<task-branch>`, then a broad fallback for adopted names:
     `git branch --all --list "*<task-branch>"` (trailing-anchored, **not** `*<task-branch>*`,
     so task `foo` doesn't match sibling `task/foo-bar`); if empty, `git branch --all | grep -i "<task-name>"`.
   - **Merged?** Check the *local* branch against the remote-tracking main — no fetch needed,
     and `origin/<main-branch>` reflects a GitHub merge even before you pull:
     `git branch --merged origin/<main-branch> --list "<task-branch>"`. If it lists the branch
     → merged → completed. (Match the local branch, not `--all`, so a stale `origin/<task-branch>`
     ref can't falsely report a still-unmerged task as merged.)
   - **Present but NOT in `--merged`** → *inconclusive*, not proof of "not merged": squash/rebase
     merges (GitHub's default on many repos) rewrite SHAs so the tip is never an ancestor of
     main even after merge. Lean on a) or c). (`/close` step 8 handles the same caveat.)
   - **Absent** → likely completed and cleaned up by `/close`; confirm via a) or c).

   c) **Commit history** — corroboration, and the *primary* signal when `gh` is unavailable:
   - Run: `git log <main-branch> origin/<main-branch> --oneline --grep="<task-name>" | head -10`
     (search the merged history of both local and remote-tracking main — covers a PR merged on
     GitHub but not yet pulled — not `--all`, so unmerged WIP branches don't count).
   - A task name in a message is easy to produce by accident, so behind a)/b) it only supports.
     But when `gh` can't run and the branch is gone, task commits *present in main's history*
     are the best available completion evidence — report it with lower confidence.

   d) **Check mentioned files** (weak signal — if task mentions specific files):
   - Run: `git log <main-branch> origin/<main-branch> --oneline -- <file-path> | head -5`
   - Activity, not completion. Corroboration only.

4. **Analyze and report**:

   Decision rule — combine the signals by confidence:
   - **COMPLETED (confirmed):** a merged PR for `<task-branch>` (3a), or the branch listed by
     `git branch --merged origin/<main-branch>` (3b).
   - **COMPLETED (likely, unconfirmed):** `gh` unavailable AND the branch is gone AND task
     commits appear in `<main-branch>`/`origin/<main-branch>` (3c) — report COMPLETED, noting
     it's unconfirmed.
   - **IN PROGRESS:** the branch exists with an open PR (3a), or with local commits and no
     merge evidence. Never assert "not merged" from `--merged` alone — a branch absent from it
     may still be squash/rebase-merged; say "merge unconfirmed" and fall through to 3a/3c first.
   - **NOT STARTED:** no PR, no branch, and no task commits in `<main-branch>`/`origin/<main-branch>`.

   **Strong evidence (completed)** — driven by 3a/3b:
   ```
   ✅ Task appears COMPLETED

   Evidence:
   • PR #123 "Fix calendar bug" — MERGED (2026-01-15)   ← authoritative
   • Branch task/fix-calendar merged into main, then deleted   ← authoritative
   • (3 commits mention "calendar bug" — corroborating only)

   Recommendation: Delete task file with /close
   ```

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
