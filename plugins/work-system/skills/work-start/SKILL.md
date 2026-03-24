---
name: work-start
description: Select a task and create an isolated git worktree for parallel development
user_invocable: true
---

# Start Task with Git Worktree

> Select a task and create an isolated worktree for parallel development

## Instructions

1. **Check current location**:
   - Run: `git worktree list`
   - If this is already a worktree (not first entry), stop and suggest `/work-continue` instead

2. **Show available tasks**:
   - Run: `ls -1 tasks/ 2>/dev/null || echo "No tasks found"`
   - If no tasks exist, suggest creating one with `/work-create`
   - List all tasks with their first line (title)
   - Ask user which task to work on

3. **Read selected task**:
   - Read the task file from `tasks/<task-name>.md`
   - Extract task name from filename (e.g., `fix-calendar-bug.md` → `fix-calendar-bug`)

4. **Quick completion check**:
   - If `gh` is available: `gh pr list --state merged --head "task/<task-name>" --limit 1 --json number,title`
   - If merged PR found, warn user and ask if they want to continue or delete the task

5. **Detect main branch**:
   - Run: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
   - If that fails, check if `main` or `master` exists: `git branch --list main master`
   - Use detected branch as base for the worktree

6. **Derive worktree path**:
   - Worktree path: `.claude/worktrees/<task-name>`
   - Create parent directory if needed: `mkdir -p .claude/worktrees`
   - Example: task `fix-bug` → `.claude/worktrees/fix-bug`

7. **Create worktree**:
   - Run: `git worktree add .claude/worktrees/<task-name> -b task/<task-name>`
   - If branch already exists, use: `git worktree add .claude/worktrees/<task-name> task/<task-name>`

8. **Copy files to worktree**:
   - Copy task file: `cp tasks/<task-name>.md .claude/worktrees/<task-name>/TASK.md`
   - Copy Claude config if it exists: `cp -r .claude/settings.json .claude/worktrees/<task-name>/.claude/ 2>/dev/null`
   - This gives Claude in the worktree access to the task and permissions

9. **Symlinks note**:
   - For large directories like `node_modules`, configure `symlinkDirectories` in `.claude/settings.json`
   - For project-specific files needed in worktrees (credentials, build configs), add symlink instructions to your project's CLAUDE.md

10. **Load project context** (optional):
    - If `.claude/knowledge/` exists, query the Knowledge Agent: "What are the project patterns and architecture?"
    - Otherwise, check CLAUDE.md and rules for project context

11. **Final instructions**:
    ```
    Worktree created!

    Location: .claude/worktrees/<task-name>
    Branch: task/<task-name>
    Task file copied to: TASK.md

    Next steps:
    1. Open terminal in the worktree directory
    2. Run: claude --name "task/<task-name>"
    3. Run: /work-continue

    Or use this one-liner:
    cd .claude/worktrees/<task-name> && claude -n "task/<task-name>"
    ```

## Remember

- Each worktree is an isolated workspace
- Multiple Claude instances can work on different tasks simultaneously
- The main repo stays clean on the main branch
- Use `/work-close` after PR is merged to clean up
