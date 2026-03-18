---
name: work-close
description: Clean up after a task is completed — remove worktree, branches, and task file
user_invocable: true
---

# Close Completed Task

> Clean up after a task is completed: verify merge, remove worktree, delete branch and task file

## Instructions

1. **Identify the task**:
   - Run: `git branch --show-current`
   - If on a `task/*` branch, extract task name
   - If on the main branch, run `/work-list` and ask which task to close

2. **Verify task is merged** (if `gh` is available):
   - Run: `gh pr list --state merged --search "task/<task-name>" --limit 5 --json number,title,mergedAt,headRefName`
   - **If merged PR found**: Show details and continue
   - **If NO merged PR**: Warn user and ask for confirmation before proceeding

3. **Detect main branch**:
   - Run: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
   - Fall back to checking `git branch --list main master`

4. **Get worktree info**:
   - Run: `git worktree list`
   - Identify main repo (first entry)
   - Find worktree for this task (match by branch name `task/<task-name>`)
   - Worktree is typically at `.claude/worktrees/<task-name>` (but verify from `git worktree list` output)

5. **Handle current location**:
   - If currently in the worktree being deleted:
     - Warn: "You're in the worktree that will be deleted!"
     - Show: "After cleanup, switch to: <main-repo-path>"

6. **Remove worktree** (if exists):
   - Run: `git worktree remove <worktree-path>`
   - If fails (uncommitted changes):
     - Show `git status` from worktree
     - Ask: "Force remove? (uncommitted changes will be lost)"
     - If yes: `git worktree remove <worktree-path> --force`

7. **Delete local branch**:
   - Run: `git branch -d task/<task-name>`
   - If fails (not fully merged):
     - Ask: "Force delete branch?"
     - If yes: `git branch -D task/<task-name>`

8. **Delete remote branch** (if exists):
   - Run: `git ls-remote --heads origin task/<task-name>`
   - If exists, ask: "Delete remote branch too?"
   - If yes: `git push origin --delete task/<task-name>`

9. **Remove task file**:
   - Navigate to main repo if needed
   - Run: `rm tasks/<task-name>.md`
   - Ask user if they want to commit the removal

10. **Final summary**:
    ```
    Task '<task-name>' closed!

    Cleaned up:
    - Worktree removed
    - Local branch deleted
    - Remote branch deleted (if applicable)
    - Task file removed

    Next: Run `git pull` to sync, then /work-start for next task
    ```

## Safety

- Always verify PR is merged before cleanup
- Warn about uncommitted changes
- Never force-delete without confirmation
- Check project CLAUDE.md and rules for project-specific cleanup steps
