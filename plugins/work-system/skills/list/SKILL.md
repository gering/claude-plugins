---
name: list
description: |
  Overview of all tasks: files, worktrees, branch state, PR status.
  Trigger: "list tasks", "what's open", "tasks overview".
user_invocable: true
---

# List Tasks and Worktrees

> Show overview of all tasks, worktrees, and their status

## Instructions

1. **Show active worktrees**:
   - Run: `git worktree list` — git lists the **main** worktree first, then each linked one.
   - Render as a markdown table under a `## 📂 Active Worktrees` heading; the first row is `Main`,
     the rest are `Worktree`:

   | Type | Path | Branch |
   |------|------|--------|
   | Main | `/path/to/project` | `main` |
   | Worktree | `…/.claude/worktrees/dark-mode` | `task/dark-mode` |

2. **List pending tasks**:
   - Run: `ls -1 tasks/*.md 2>/dev/null`
   - For each task file: read its first line (title) and resolve its real branch via the shared
     helper — `bash "${CLAUDE_PLUGIN_ROOT}/scripts/task-status.sh" resolve "<task-name>"` — then
     read `task_branch` and `branch_exists`:
     - `branch_exists=yes` → the helper matched a real branch (an exact `task/<name>`, or the
       checked-out worktree branch). Bind the task to `task_branch`; use it for the worktree/
       branch/PR lookup below.
     - `branch_exists=no` → no branch for this task yet (`task_branch` is just the convention) →
       render `📋 Not Started`.
   - Never assume a hardcoded `task/<task-name>` — always use the resolved `task_branch`.
   - (An `/adopt`'d branch that kept a non-`task/` name isn't matched by name here — it still
     surfaces via its worktree in steps 1/4.)
   - Render as a markdown table under a `## 📋 Tasks` heading:

   | # | Task | Title | Status |
   |---|------|-------|--------|
   | 1 | `fix-calendar-bug` | Fix DST-related date shift in calendar view | 🔄 In Progress |
   | 2 | `add-dark-mode` | Add dark mode toggle to settings | 📋 Not Started |
   | 3 | `refactor-notifications` | Refactor notification scheduling | 🔍 In Review (PR #45) |

3. **Check for open PRs** (if `gh` is available):
   - Run once: `gh pr list --state open --json number,title,headRefName --limit 30`
   - Match each PR to a task by comparing its `headRefName` to that task's bound `task_branch`
     (from step 2) — not a hardcoded `task/<task-name>`, so `/adopt`-renamed `task/<name>` branches
     match too.
   - Show PR status for each task

4. **Check for orphaned worktrees**:
   - Worktrees without matching task file
   - Worktrees with merged PRs (can be cleaned up)
   ```
   ⚠️ Cleanup Suggested:
   • .claude/worktrees/old-feature — PR merged, can be removed
   ```

5. **Summary statistics**:
   - Archived count: `find tasks/archive -maxdepth 1 -type f -name '*.md' ! -name '_index.md' 2>/dev/null | wc -l | tr -d ' '`
     — closed tasks moved aside by `/close` (the non-recursive `tasks/*.md` glob in step 2
     already excludes them). Omit the line when the count is 0.
   ```
   📊 Summary:
   • Total tasks: 3
   • In progress: 1
   • Ready for review: 1
   • Not started: 1
   • Active worktrees: 2
   • Archived: 7 (tasks/archive/)
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

7. **Sync herdr tab glyphs** (best-effort, silent):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-tab-glyph.sh" refresh --cached "$PWD"`
   - Inside herdr this re-stamps every open task tab's state glyph
     (`○ ● ◇ ◆ ✓`, plus the main-repo tab's `◉` hub mark) to match the surveyed
     states; outside herdr it is a silent no-op (the main-repo `◉` is stateless,
     so an empty backlog still stamps it — only per-task glyphs have nothing to
     do). `--cached`: this is a read-only survey, so
     read the PR cache instead of a blocking `gh` call (a background refresh
     keeps it current). Ignore its output — never block or report on it.

8. **All commands** (reference, always shown):
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
