---
name: kickoff
description: |
  Creates a `task/<name>` branch off main in an isolated worktree and
  opens a fresh Claude Code session there.
  Trigger: "start working on X", "kickoff", "create a worktree".
user_invocable: true
---

# Start Task with Git Worktree

> Select a task and create an isolated worktree for parallel development

## Critical: never persist a `cd` into the worktree

This skill runs **in the user's main-repo session**. Its job is to *create* the worktree, not to enter it. The user opens the worktree in a separate terminal/Claude session (see step 12).

Because the Bash tool persists working directory between calls, a bare `cd .claude/worktrees/<task>` would silently trap the entire session inside the worktree — every subsequent `git status`, relative path, or check would target the worktree instead of the main repo. This has caused real user-visible bugs.

**Rules for every shell command in this skill:**
- ❌ Never run `cd <worktree>` as a standalone command, or `cd <worktree> && …` without a paired `cd -`/`cd <main-repo>` afterwards.
- ✅ Use `git -C <worktree-path> …` for git operations against the worktree.
- ✅ Use absolute paths or paths relative to the main repo for `cp`, `mkdir`, `ln -s`, etc.
- ✅ If a step genuinely needs a different CWD (rare), wrap it in a subshell: `(cd <worktree-path> && <cmd>)` — the CWD change dies with the subshell.

The same rule applies to any project-specific setup the user's `CLAUDE.md` may ask you to perform (symlinks for `data/`, copying credentials, etc.) — translate them into `git -C` / absolute / subshell form before executing.

## Instructions

1. **Check current location**:
   - Run: `git worktree list`
   - If this is already a worktree (not first entry), stop and suggest `/continue` instead

2. **Show available tasks**:
   - Run: `ls -1 tasks/ 2>/dev/null || echo "No tasks found"`
   - If no tasks exist, suggest creating one with `/define`
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

8. **Copy files to worktree** (run from main-repo CWD — relative paths target the main repo):
   - Copy task file: `cp tasks/<task-name>.md .claude/worktrees/<task-name>/TASK.md`
   - Copy Claude config if it exists: `cp -r .claude/settings.json .claude/worktrees/<task-name>/.claude/ 2>/dev/null`
   - This gives Claude in the worktree access to the task and permissions
   - Do **not** `cd` into the worktree to perform copies — paths from the main repo work fine.

9. **Project-specific setup (symlinks, data dirs, etc.)**:
   - For large directories like `node_modules`, configure `symlinkDirectories` in `.claude/settings.json`.
   - If the project's `CLAUDE.md` instructs creating symlinks for shared resources (e.g. a `data/` directory), execute them **without a persistent `cd`**. Safe forms:
     - `ln -s "$(pwd)/data" .claude/worktrees/<task-name>/data` — absolute symlink target, link path relative to main repo.
     - `(cd .claude/worktrees/<task-name> && ln -s ../../../data data)` — subshell, CWD change dies on close.
   - Never run `cd .claude/worktrees/<task-name>` as a standalone or trailing-`&&` command. See the "Critical" note at the top of this file.

10. **Load project context** (optional):
    - If `.claude/knowledge/` exists, query the Knowledge Agent: "What are the project patterns and architecture?"
    - Otherwise, check CLAUDE.md and rules for project context

11. **Verify CWD is still in the main repo**:
    - Run: `pwd` and compare to the main-repo path captured from `git worktree list` in step 1.
    - If they differ, **stop and report an error**: "Session CWD drifted into the worktree during kickoff — investigate which step ran a persistent `cd`." Do not silently continue; a contaminated session will mislead every subsequent command.

12. **Final instructions for the user** (display this block — it is *not* a command to execute):
    ```
    Worktree created!

    Location: .claude/worktrees/<task-name>
    Branch:   task/<task-name>
    Task file: TASK.md (copied into the worktree)

    👉 To start working there, open a SEPARATE terminal (not this Claude
       session — this session stays in the main repo) and run:

         cd .claude/worktrees/<task-name>
         claude -n "<task-name>" "/continue"
    ```
    `-n "<task-name>"` names the session (shown in `/resume` and the terminal title);
    the `/continue` initial prompt runs the resume flow (load TASK.md, recent commits,
    progress) deterministically — both in one launch. Do **not** execute the `cd` command
    yourself — it is for the user's new terminal.

## Remember

- Each worktree is an isolated workspace
- Multiple Claude instances can work on different tasks simultaneously
- The main repo stays clean on the main branch
- Use `/close` after PR is merged to clean up
