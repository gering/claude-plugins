---
name: close
description: |
  Cleans up a completed task: verifies PR merged, removes worktree,
  deletes branch, archives the task file.
  Trigger: "close this task", "task cleanup", "task is done".
user_invocable: true
---

# Close Completed Task

> Clean up after a task is completed: verify merge, remove worktree, delete branch and task file

## Critical: never `cd` between repo and worktree

This skill may run from the main repo *or* from inside the worktree being deleted. Bash CWD persists between tool calls, so changing directory mid-flow either traps the session in the worktree (when run from main) or leaves it pointing at a deleted path (when run from inside).

Rules:
- ❌ Do not `cd <worktree>` or `cd <main-repo>` during this skill.
- ✅ All operations against either tree go through explicit paths: `git -C <main-repo-path> …`, `git -C <worktree-path> …`, `rm <main-repo-path>/tasks/<task-name>.md`, etc.
- After deletion, the session's CWD may already be in a now-removed directory — that's the user's problem to fix (a new `cd` in their terminal), not something this skill should "repair" mid-run.

## Instructions

1. **Identify the task**:
   - Run: `git branch --show-current`
   - If on a `task/*` branch, extract task name
   - If on the main branch, run `/list` and ask which task to close

2. **Verify task is merged** (if `gh` is available):
   - Run: `gh pr list --state merged --head "task/<task-name>" --limit 1 --json number,title,mergedAt,headRefName`
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

5. **Sync local main with remote** (fast-forward check):
   - The task's PR was merged on GitHub (step 2) — `origin/<main-branch>` is ahead of local `<main-branch>` until it's pulled. Syncing now avoids a confusing "not fully merged" error in step 7 and leaves the workspace ready for the next task.
   - Operate against the main repo path identified in step 4 (not the task worktree):
     - Fetch: `git -C <main-repo-path> fetch origin <main-branch> --quiet`
     - Behind count: `git -C <main-repo-path> rev-list --count <main-branch>..origin/<main-branch>`
     - Ahead count: `git -C <main-repo-path> rev-list --count origin/<main-branch>..<main-branch>`
   - **Behind == 0**: already in sync — proceed silently.
   - **Behind > 0 AND Ahead == 0**: fast-forward cleanly:
     `git -C <main-repo-path> merge --ff-only origin/<main-branch>`
     Log: "main fast-forwarded (<N> commits pulled from origin)".
   - **Behind > 0 AND Ahead > 0** (divergence — local main has unpushed commits): do NOT auto-pull. Surface a warning with both counts and the suggested command (`git pull --rebase`), then continue cleanup — divergence is a separate concern from closing the task.

6. **Handle current location**:
   - If currently in the worktree being deleted:
     - Warn: "You're in the worktree that will be deleted!"
     - Show: "After cleanup, switch to: <main-repo-path>"

7. **Remove worktree** (if exists) — all commands use explicit paths, never `cd`:
   - First check for untracked/modified files: `git -C <worktree-path> status --short`
   - If the only difference is `TASK.md` (untracked, copied by kickoff), use `--force` directly:
     `git -C <main-repo-path> worktree remove <worktree-path> --force`
   - Otherwise try: `git -C <main-repo-path> worktree remove <worktree-path>`
   - If fails (uncommitted changes beyond TASK.md):
     - Show full status: `git -C <worktree-path> status`
     - Ask: "Force remove? (uncommitted changes will be lost)"
     - If yes: `git -C <main-repo-path> worktree remove <worktree-path> --force`

8. **Delete local branch**:
   - If a merged PR was confirmed in step 2: use `git branch -D task/<task-name>` directly
     (the `-d` safety check produces false positives with GitHub's rebase-merge strategy,
     where commits are rewritten with new SHAs — the real safety gate is the merged-PR check)
   - If NO merged PR was found (manual close): try `git branch -d task/<task-name>` first
     - If fails (not fully merged): ask "Force delete branch?"
     - If yes: `git branch -D task/<task-name>`

9. **Delete remote branch**:
   - Run: `git ls-remote --heads origin task/<task-name>`
   - **Returns nothing** → the remote branch is already gone (e.g. the repo auto-deletes head
     branches on merge); skip this step — nothing to delete, and `--delete` on a missing ref
     would error.
   - **Exists AND a merged PR was confirmed in step 2** → delete directly, no prompt:
     `git push origin --delete task/<task-name>` (the merge already integrated the work).
   - **Exists, no merged PR** (manual close) → ask "Delete remote branch too?" first; only
     push the delete on confirmation.

10. **Remove task file** (use the main-repo path from step 4 — do not `cd`):
    - Run: `rm <main-repo-path>/tasks/<task-name>.md`
    - Check if the file was git-tracked: `git -C <main-repo-path> status --short tasks/<task-name>.md`
    - If there is a staged/unstaged change (file was tracked): ask user if they want to commit the removal
    - If no git change (file was gitignored or untracked): just report "Task file removed", no commit needed

11. **Final summary**:
    ```
    Task '<task-name>' closed!

    Cleaned up:
    - Worktree removed
    - Local branch deleted
    - Remote branch deleted (if applicable)
    - Task file removed
    - main synced with origin (<N> commits pulled)     [if fast-forward happened]

    Next: /kickoff for next task
    ```

## Safety

- Always verify PR is merged before cleanup
- Warn about uncommitted changes
- Never force-delete without confirmation
- Check project CLAUDE.md and rules for project-specific cleanup steps
