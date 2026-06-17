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

1. **Determine which task to check**:
   - If `$ARGUMENTS` provided, use that as task name
   - Otherwise, check current branch:
     - Run: `git branch --show-current`
     - If on `task/*` branch, extract task name
   - If still no task, list available tasks and ask user

2. **Read task file** (if exists):
   - Run: `cat tasks/<task-name>.md 2>/dev/null`
   - Extract key information:
     - Task title/goal
     - Files mentioned
     - Key terms for searching

3. **Search for evidence of completion** — weight the signals: a) and b) are
   authoritative, c) and d) are only weak corroboration (see step 4):

   a) **Check for the task's PR** (if `gh` is available) — strongest signal:
   - Primary, exact-branch match: `gh pr list --state all --head "task/<task-name>" --limit 5 --json number,title,state,mergedAt,url`
   - Only if that returns nothing, fall back to a fuzzy search (may surface unrelated PRs):
     `gh pr list --state all --search "<task-name>" --limit 5 --json number,title,state,mergedAt,url`
   - Show matching PRs with status; a **merged** PR for `task/<task-name>` means completed.

   b) **Check the task branch** — second-strongest signal:
   - Run: `git branch --all --list "*task/<task-name>*"`
   - If the branch is gone but a merged PR exists → completed and cleaned up.
   - If the branch exists, check whether it merged into main:
     `git branch --all --merged <main-branch> --list "*task/<task-name>*"`
     (detect `<main-branch>` via `git symbolic-ref refs/remotes/origin/HEAD` → fallback `main`/`master`).

   c) **Commit history** (weak signal — corroboration only, do not conclude from this alone):
   - Run: `git log --all --oneline --grep="<task-name>" | head -10`
   - A task *name* appearing in a commit message is easy to produce by accident (a WIP
     commit, an unrelated mention). Treat matches as supporting evidence behind a) and b),
     never as proof of completion.

   d) **Check mentioned files** (weak signal — if task mentions specific files):
   - Run: `git log --oneline -- <file-path> | head -5`
   - Recent changes show *activity*, not completion. Corroboration only.

4. **Analyze and report**:

   Decision rule: only conclude **COMPLETED** when an authoritative signal (3a or 3b)
   confirms it — a merged PR for `task/<task-name>`, or the task branch merged into the
   main branch. Commit-message or file-activity matches (3c/3d) on their own are *not*
   enough; if they're the only signal, report IN PROGRESS / inconclusive instead.

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
