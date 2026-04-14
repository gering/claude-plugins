---
name: work-list
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

6. **Quick actions**:
   ```
   Quick Actions:
   • /work-start        — Start a new task
   • /work-create       — Create a new task
   • /work-check <name> — Check specific task status
   • /work-close        — Clean up completed task
   ```

## Output Format

Keep the output concise but informative. Use emoji for quick visual scanning:
- 📋 Not started
- 🔄 In progress (has worktree)
- 🔍 In review (PR open)
- ✅ Merged (ready to close)
- ⚠️ Needs attention
