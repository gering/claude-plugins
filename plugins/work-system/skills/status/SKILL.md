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

1. **Assess the task** — delegate the git/gh detective work to the shared helper:
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/task-status.sh" assess "$ARGUMENTS"`
     (pass `$ARGUMENTS`; it is empty when none is given, and the helper then uses the current
     worktree branch).
   - The helper prints `key=value` lines — read: `verdict` (COMPLETED / IN_PROGRESS /
     NOT_STARTED), `confidence` (confirmed / likely / none), `task_name`, `task_branch`,
     `branch_exists`, `branch_merged` (yes / unknown / na), `pr_state` (MERGED / OPEN / CLOSED /
     none / nogh), `pr_number`, `pr_url`, `commits_in_main`, `main_branch`, `on_main`,
     `detached`, `branch_ambiguous`.
   - **If `on_main=yes` and `task_name` is empty** (run from the main repo with no argument):
     list the files in `tasks/`, ask which task to check, then re-run `assess "<chosen-name>"`.
   - **If `detached=yes`**: the worktree is on a detached HEAD — there is no task branch to check.
     Report that and stop (check out the `task/<name>` branch, or pass a task name explicitly).
   - **If `branch_ambiguous=yes`**: the name matched several branches and `task_branch` is just
     the first — note the ambiguity and suggest re-running with the exact name.

   The helper owns the parts that are fiddly in prose: it resolves `task_branch` from the
   current branch (or `task/<name>` + fallbacks by name, read via `--format` so no stray
   whitespace), prefers `origin/<main>` for the merge check but never `fatal`s when it is
   absent, and reports a branch that isn't an ancestor of main as `branch_merged=unknown`
   (possible squash/rebase merge) rather than "not merged".

2. **Read the task file** for human context (optional):
   - Run: `cat tasks/<task_name>.md 2>/dev/null` — use the title/goal to label the report.

3. **Report** from `verdict` + `confidence`, quoting the concrete evidence fields:

   **`verdict=COMPLETED`:**
   ```
   ✅ Task appears COMPLETED   (<confidence>: confirmed | likely — unconfirmed)

   Evidence:
   • PR #<pr_number> — <pr_state> (<pr_url>)              [when pr_state=MERGED]
   • Branch <task_branch> merged into <main_branch>       [when branch_merged=yes]
   • <commits_in_main> commit(s) for "<task_name>" in <main_branch>   [corroborating]

   Recommendation: Delete task file with /close
   ```
   `confidence=likely` means no live PR/branch proved it (no `gh`, or branch already cleaned up
   by `/close`) — label it "likely (unconfirmed)".

   **`verdict=IN_PROGRESS`:**
   ```
   🔄 Task appears IN PROGRESS

   Evidence:
   • Branch <task_branch> exists                          [only when branch_exists=yes]
   • PR #<pr_number> is open                              [when pr_state=OPEN]
   • PR #<pr_number> was closed without merging           [when pr_state=CLOSED]
   • Merge unconfirmed (may be squash/rebase-merged)      [when branch_merged=unknown]

   Recommendation: Continue work, or check/reopen the PR
   ```
   Never say "not merged" when `branch_merged=unknown` — say "merge unconfirmed". Only claim the
   branch exists when `branch_exists=yes` (a remote-only or already-deleted branch may not).

   **`verdict=NOT_STARTED`:**
   ```
   📋 Task appears NOT STARTED

   No PR, branch, or task commits found for "<task_name>".

   Recommendation: Start with /kickoff
   ```

4. **Offer actions**:
   - If completed: "Delete task file?"
   - If in progress: "Open PR in browser?" / "Continue work?"
   - If not started: "Start this task?"
