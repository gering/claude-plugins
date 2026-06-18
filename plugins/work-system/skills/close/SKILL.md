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

1. **Identify the task, its branch, and its merge state** — via the shared helper:
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/task-status.sh" assess "$ARGUMENTS"`
     (`$ARGUMENTS` is the optional task name; empty when run from inside the worktree).
   - **If `on_main=yes` and `task_name` is empty**: run `/list`, ask which task to close, then
     re-run `assess "<chosen-name>"`.
   - **If `branch_ambiguous=yes`**: the name matched several branches and `task_branch` is only
     the first. List the candidates **case-insensitively, matching the helper's own fuzzy rule**
     — `git branch --all --format='%(refname:short)' | grep -i -F -- "<task-name>"` (a plain
     `--list "*<task-name>*"` glob is case-sensitive and would hide the colliding branches) — and
     ask which to close. Never run the destructive steps (7–9) on a fuzzy guess.
   - **If `detached=yes`** (detached HEAD, no name given): there's no task branch to close — ask
     for the task name explicitly and re-run.
   - Read the fields: `<task-branch>` = `task_branch` (the resolved real ref — the current branch
     in a worktree, or `task/<name>` / an adopted original name when resolved by name),
     `<task-name>` = `task_name`, `<main-branch>` = `main_branch`, plus `verdict`, `confidence`,
     `pr_state`, `pr_number`, `branch_merged`.
   - **Wherever the steps below write `task/<task-name>`, use the resolved `<task-branch>`** — so
     an adopted branch that kept its original name is closed correctly, not orphaned.

2. **Verify the task is merged** — the safety gate; never skip it silently:
   - **Merge confirmed** (`verdict=COMPLETED` with `confidence=confirmed` — i.e. a merged PR, or
     the branch is an ancestor of the helper's `<merge-ref>`): show the evidence
     (`PR #<pr_number>`, or "branch merged into `<main-branch>`") and continue.
   - **Not confirmed** (open/no PR, `branch_merged=unknown`/`na`, or `gh` unavailable so
     `pr_state=nogh`): **warn** what is and isn't known — e.g. "merge unconfirmed: no merged PR
     found / branch is not an ancestor of main (may be squash/rebase-merged, or `gh`
     unavailable)" — and **ask for confirmation before any cleanup**. Never let the worktree
     removal (step 7) or branch deletion (step 8) proceed on an unconfirmed merge without it.

3. **Main branch**: `<main-branch>` was already resolved by the helper in step 1 — reuse it; do
   not re-detect.

4. **Get worktree info**:
   - Run: `git worktree list`
   - Identify main repo (first entry)
   - Find worktree for this task (match by branch name `task/<task-name>`)
   - Worktree is typically at `.claude/worktrees/<task-name>` (but verify from `git worktree list` output)

5. **Sync local main with remote** (fast-forward check) — only when an `origin` remote exists:
   - **If there is no `origin` remote** (`git remote get-url origin` fails — a purely local repo,
     where step 2 may have confirmed a local-only ancestor merge): skip this step, there is
     nothing to fetch.
   - When the merge landed via a GitHub PR, `origin/<main-branch>` is ahead of local
     `<main-branch>` until pulled. Syncing now avoids a confusing "not fully merged" error from
     `git branch -d` in step 8 and leaves the workspace ready for the next task.
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

8. **Delete local branch** (only if it exists locally):
   - If `branch_exists=no` (from step 1 — e.g. the task was resolved remote-only, or the local
     branch was already deleted): skip this step, there is nothing local to delete.
   - If merge was confirmed in step 2: use `git branch -D task/<task-name>` directly
     (the `-d` safety check produces false positives with GitHub's rebase-merge strategy,
     where commits are rewritten with new SHAs — the real safety gate is step 2's merge check)
   - If merge was not confirmed (manual close): try `git branch -d task/<task-name>` first
     - If fails (not fully merged): ask "Force delete branch?"
     - If yes: `git branch -D task/<task-name>`

9. **Delete remote branch** (only if there is an `origin` remote):
   - **If `git remote get-url origin` fails** (purely local repo): skip this step — there is no
     remote branch to delete (mirrors step 5's guard).
   - Run: `git ls-remote --heads origin task/<task-name>`
   - **Returns nothing** → the remote branch is already gone (e.g. the repo auto-deletes head
     branches on merge); skip this step — nothing to delete, and `--delete` on a missing ref
     would error.
   - **Exists AND merge was confirmed in step 2** → delete directly, no prompt:
     `git push origin --delete task/<task-name>` (the merge already integrated the work).
   - **Exists, merge not confirmed** (manual close) → ask "Delete remote branch too?" first; only
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
