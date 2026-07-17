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
     `branch_exists`, `branch_merged` (no / unknown / na — never confirms a merge on its own),
     `pr_state` (MERGED / OPEN / CLOSED / none / nogh), `pr_number`, `pr_url`, `commits_in_main`,
     `main_branch`, `on_main`, `detached`.
   - **If `on_main=yes` and `task_name` is empty** (run from the main repo with no argument):
     list the files in `tasks/`, ask which task to check, then re-run `assess "<chosen-name>"`.
   - **If `detached=yes`**: the worktree is on a detached HEAD — there is no task branch to check.
     Report that and stop (check out the `task/<name>` branch, or pass a task name explicitly).

   The helper owns the parts that are fiddly in prose: it resolves `task_branch` from the current
   branch (or an exact `task/<name>` ref match by name — exact-only, no fuzzy substring guessing,
   and it never resolves the main branch as a task), prefers `origin/<main>` for the merge check
   but never `fatal`s when it is absent, and reports a branch whose tip differs from the merge ref
   as `branch_merged=unknown` (behind-but-reachable, or a possible squash/rebase merge) rather than
   "not merged".

2. **Read the task file** for human context (optional):
   - Run: `cat tasks/<task_name>.md 2>/dev/null` — use the title/goal to label the report.

3. **Report** from `verdict` + `confidence`, quoting the concrete evidence fields:

   **`verdict=COMPLETED`:**
   ```
   ✅ Task appears COMPLETED   (<confidence>: confirmed | likely — unconfirmed)

   Evidence:
   • PR #<pr_number> — <pr_state> (<pr_url>)              [when pr_state=MERGED]
   • <commits_in_main> commit(s) for "<task_name>" in <main_branch>   [only when commits_in_main>0]

   Recommendation: Delete task file with /close
   ```
   `confidence=likely` means no merged PR proved it (no `gh`, or branch already cleaned up by
   `/close`, with task commits in main) — label it "likely (unconfirmed)". A merged PR is the
   only thing that yields `confirmed`; a still-present branch never reports COMPLETED.

   **`verdict=IN_PROGRESS`:**
   ```
   🔄 Task appears IN PROGRESS

   Evidence:
   • Branch <task_branch> exists                          [only when branch_exists=yes]
   • PR #<pr_number> is open                              [when pr_state=OPEN]
   • PR #<pr_number> was closed without merging           [when pr_state=CLOSED]
   • Merge unconfirmed (may be squash/rebase-merged)      [only when branch_merged=unknown AND pr_state in none/nogh]

   Recommendation: Continue work, or check/reopen the PR
   ```
   The merge-unconfirmed line is a hedge for when there is no live PR signal — suppress it when
   `pr_state` is OPEN/CLOSED/MERGED (the PR already states the authoritative status; printing both
   would contradict itself). Never say "not merged" when `branch_merged=unknown` — say "merge
   unconfirmed". Only claim the branch exists when `branch_exists=yes` (a remote-only or
   already-deleted branch may not).

   **`verdict=NOT_STARTED`:**
   ```
   📋 Task appears NOT STARTED

   No branch found for "<task_name>".
   • No matching PR.                                          [when pr_state=none]
   • PR status unknown (gh unavailable).                      [when pr_state=nogh]
   • <commits_in_main> commit(s) in <main_branch> mention "<task_name>", but the name is too
     generic to count as proof — check those manually.        [only when commits_in_main>0]

   Recommendation: Start with /kickoff
   ```
   Don't claim "no PR found" when `pr_state=nogh` — no PR lookup ran (gh was unavailable), so say
   "PR status unknown" instead. And don't print "no task commits found" when `commits_in_main>0`:
   the verdict is NOT_STARTED only because the name was too generic to treat history matches as
   evidence (it failed the helper's specificity gate), not because no matching commits exist.

4. **Offer actions**:
   - If completed: "Delete task file?"
   - If in progress: "Open PR in browser?" / "Continue work?"
   - If not started: "Start this task?"

5. **Sync herdr tab glyphs** (best-effort, silent):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-tab-glyph.sh" refresh --cached "$PWD"`
   - Inside herdr this re-stamps every open task tab's state glyph
     (`○ ● ◇ ◆ ✓`, plus the main-repo tab's `◉` hub mark) to match the surveyed
     state; outside herdr it is a silent no-op (the main-repo `◉` is stateless,
     so an empty backlog still stamps it — only per-task glyphs have nothing to
     do). `--cached`: this is a read-only survey, so
     read the PR cache instead of a blocking `gh` call (a background refresh
     keeps it current). Ignore its output — never block or report on it.
