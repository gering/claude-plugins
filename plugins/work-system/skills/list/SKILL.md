---
name: list
description: |
  Displays a consolidated overview of all tasks in the project: task
  files, associated worktrees, branch state, and PR status (via `gh` if
  available). Useful as a "what am I working on" dashboard across
  multiple parallel tasks.

  Use when: user wants to "list tasks", "show all tasks", "what's open",
  "was läuft gerade", "welche tasks hab ich", "übersicht", needs to pick
  between several in-flight tasks, or wants to spot stale worktrees that
  should be closed.
user_invocable: true
---

# List Tasks and Worktrees

> Show overview of all tasks, worktrees, and their status

## Instructions

1. **Show active worktrees**:
   - Run: `git worktree list`
   - Parse output and format nicely:
   ```
   📂 Active Worktrees:
   ┌─────────────────────────────────────────────────────────┐
   │ Main repo: /path/to/project (main)                     │
   │ Worktree:  /path/to/project/.claude/worktrees/dark-mode  │
   │            └─ Branch: task/dark-mode                    │
   └─────────────────────────────────────────────────────────┘
   ```

2. **List pending tasks**:
   - Run: `ls -1 tasks/*.md 2>/dev/null`
   - For each task file, read first line (title)
   - Check if worktree/branch exists for it
   ```
   📋 Pending Tasks:
   ┌──────────────────────────────────────────────────────────┐
   │ 1. fix-calendar-bug.md                                  │
   │    "Fix DST-related date shift in calendar view"        │
   │    Status: 🔄 In Progress (worktree exists)             │
   │                                                         │
   │ 2. add-dark-mode.md                                     │
   │    "Add dark mode toggle to settings"                   │
   │    Status: 📋 Not Started                               │
   │                                                         │
   │ 3. refactor-notifications.md                            │
   │    "Refactor notification scheduling"                   │
   │    Status: 🔍 In Review (PR #45 open)                   │
   └──────────────────────────────────────────────────────────┘
   ```

3. **Check for open PRs** (if `gh` is available):
   - Run: `gh pr list --state open --json number,title,headRefName --limit 10`
   - Match PRs to tasks by branch name (`task/<task-name>`)
   - Show PR status for each task

4. **Check for orphaned worktrees**:
   - Worktrees without matching task file
   - Worktrees with merged PRs (can be cleaned up)
   ```
   ⚠️ Cleanup Suggested:
   • .claude/worktrees/old-feature — PR merged, can be removed
   ```

5. **Summary statistics**:
   ```
   📊 Summary:
   • Total tasks: 3
   • In progress: 1
   • Ready for review: 1
   • Not started: 1
   • Active worktrees: 2
   ```

6. **Suggested next step** (contextual — pick the single most relevant action based on the state surveyed above):
   - **≥1 task is "Not Started"** →
     `▶️  Next: /kickoff <task-name>  — spin up a worktree and start working (or just /kickoff to pick interactively)`
   - **else, ≥1 task is "Merged" / PR closed but worktree still exists** →
     `▶️  Next: /close <task-name>  — clean up the merged task`
   - **else, ≥1 task is "In Progress"** →
     `▶️  Next: resume with /continue from inside the task worktree`
   - **else (no tasks at all)** →
     `▶️  Next: /define  — capture a new task from the current context`

   Print the chosen line as its own highlighted block directly below the summary. Pick exactly one — do not print multiple "Next" lines.

7. **All commands** (reference, always shown):
   ```
   Commands:
   • /define         — Create a new task
   • /kickoff        — Start a task in a worktree
   • /adopt          — Adopt an existing branch into the work system
   • /continue       — Resume the active task
   • /status <name>  — Check specific task status
   • /close <name>   — Clean up completed task
   ```

## Output Format

Keep the output concise but informative. Use emoji for quick visual scanning:
- 📋 Not started
- 🔄 In progress (has worktree)
- 🔍 In review (PR open)
- ✅ Merged (ready to close)
- ⚠️ Needs attention
