---
name: status
description: |
  Read-only status snapshot of a task. Shows branch state (ahead/behind
  main, unpushed commits), associated PR (open/merged/closed, CI status,
  reviews if `gh` is available), and whether the work is ready for
  `/close`. Does not modify anything.

  Use when: user wants to "check this task", "is this done", "status of
  the task", "was macht task X", "ist das gemerged", needs to know
  whether a task branch is still active or safe to clean up. Also
  "status" / "task status".
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

3. **Search for evidence of completion**:

   a) **Check for PRs** (if `gh` is available):
   - Run: `gh pr list --state all --search "<task-name>" --limit 5 --json number,title,state,mergedAt,url`
   - Show any matching PRs with status

   b) **Check for branches**:
   - Run: `git branch --all | grep -i "<task-name>"`
   - Show existing branches

   c) **Search commit history**:
   - Run: `git log --all --oneline --grep="<task-name>" | head -10`
   - Also search for key terms from task file

   d) **Check mentioned files** (if task mentions specific files):
   - Run: `git log --oneline -- <file-path> | head -5`
   - Show recent changes to those files

4. **Analyze and report**:

   **Strong evidence (completed)**:
   ```
   ✅ Task appears COMPLETED

   Evidence:
   • PR #123 "Fix calendar bug" — MERGED (2024-01-15)
   • Branch task/fix-calendar was merged and deleted
   • 3 commits mention "calendar bug"

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
