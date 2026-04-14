---
name: work-adopt
description: |
  Adopts an existing feature branch into the work system. Creates a
  worktree for the existing branch (instead of creating a new branch like
  /work-start does), generates a matching task file from the branch's
  commits and diff, and wires it into the task directory as if the work
  had been tracked from the start.

  Use when: user has a branch that was created manually (outside the work
  system) and wants to retroactively track it, or says "adopt this
  branch", "bring feature/xyz into the work system", "work-adopt",
  "vorhandenen branch aufnehmen" / "branch adoptieren".
user_invocable: true
---

# Adopt Existing Branch

> Bring an existing branch into the work system — create a worktree and task file for it

## Arguments

- `$ARGUMENTS` - Optional: branch name to adopt

## Instructions

1. **Check current location**:
   - Run: `git worktree list`
   - If this is already a worktree (not first entry), stop and explain this command should be run from the main repo

2. **Select branch**:
   - If `$ARGUMENTS` provided, use as branch name
   - Otherwise, list available branches:
     - Run: `git branch --list --no-merged | grep -v '^\*'`
     - Exclude the current branch and any `task/*` branches that already have worktrees
     - Show the list and ask the user which branch to adopt
   - Verify the branch exists: `git rev-parse --verify <branch-name>`

3. **Derive task name**:
   - Strip common prefixes: `feature/`, `fix/`, `bugfix/`, `hotfix/`, `chore/`, `refactor/`
   - Convert to kebab-case if needed
   - Examples:
     - `feature/dark-mode` → `dark-mode`
     - `fix/calendar-date-bug` → `calendar-date-bug`
     - `my-feature` → `my-feature`
   - Show proposed task name and ask for confirmation

4. **Check for conflicts**:
   - Check if `tasks/<task-name>.md` already exists
   - Check if `task/<task-name>` branch already exists
   - Check if worktree path `.claude/worktrees/<task-name>` already exists
   - If any conflict, ask user how to proceed (rename or skip)

5. **Gather task context from branch**:
   - Detect main branch:
     - Run: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
     - If that fails, check if `main` or `master` exists: `git branch --list main master`
   - Get commit history: `git log --oneline <main-branch>..<branch-name>`
   - Get changed files: `git diff --stat <main-branch>...<branch-name>`
   - Get diff summary: `git diff --shortstat <main-branch>...<branch-name>`

6. **Generate task file**:
   - Use the commit messages and changed files to draft a task file
   - Template:
     ```markdown
     # <Title derived from branch name and commits>

     ## Goal
     <Inferred from commit messages>

     ## Context
     Adopted from existing branch `<original-branch-name>`.

     ## Progress
     - [x] <Summary of work already done, based on commits>

     ## Remaining
     - [ ] <Any obvious remaining work, or "Review and finalize">

     ## Relevant Files
     - `<changed-file-1>`
     - `<changed-file-2>`
     ```
   - Show to user for review and allow edits

7. **Create task file**:
   - Write to: `tasks/<task-name>.md`
   - Create `tasks/` directory if it doesn't exist

8. **Rename branch** (optional):
   - Ask: "Rename branch `<original-name>` to `task/<task-name>`? (recommended for consistency)"
   - If yes: `git branch -m <original-name> task/<task-name>`
   - If no: keep original branch name and note it in the task file

9. **Create worktree**:
   - Create parent directory if needed: `mkdir -p .claude/worktrees`
   - Worktree path: `.claude/worktrees/<task-name>`
   - If branch was renamed: `git worktree add .claude/worktrees/<task-name> task/<task-name>`
   - If branch kept original name: `git worktree add .claude/worktrees/<task-name> <original-branch-name>`

10. **Copy files to worktree**:
    - Copy task file: `cp tasks/<task-name>.md .claude/worktrees/<task-name>/TASK.md`
    - Copy Claude config if it exists: `cp -r .claude/settings.json .claude/worktrees/<task-name>/.claude/ 2>/dev/null`

11. **Final output**:
    ```
    Branch adopted into work system!

    Original branch: <original-branch-name>
    Task file: tasks/<task-name>.md
    Worktree: .claude/worktrees/<task-name>
    Branch: <current-branch-name>
    Commits: <count> commits ahead of <main-branch>

    Next steps:
    1. Open terminal in the worktree directory
    2. Run: claude -n "<task-name>"
    3. Run: /work-continue

    Or use this one-liner:
    cd .claude/worktrees/<task-name> && claude -n "<task-name>"
    ```

## Remember

- The original branch is preserved — it's either renamed or checked out as-is
- The task file is a best-effort draft from commit history — the user should review it
- After adoption, the standard workflow applies: `/work-continue`, `/work-check`, `/work-close`
