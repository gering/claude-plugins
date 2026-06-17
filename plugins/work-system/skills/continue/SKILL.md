---
name: continue
description: |
  Resumes the current task: loads task file, recent commits, progress,
  acceptance criteria, open questions.
  Trigger: "continue", "resume the task", "pick up where I left off".
user_invocable: true
---

# Continue Task in Worktree

> Load context and continue working on the current task

## Instructions

1. **Verify we're in a worktree**:
   - Run: `git worktree list`
   - Run: `git branch --show-current`
   - If on the main branch (not a `task/*` branch), suggest using `/kickoff` instead
   - Extract task name from branch (e.g., `task/fix-calendar` → `fix-calendar`)

2. **Check for TASK.md**:
   - Read `TASK.md` in the current directory
   - If exists, read and display the task requirements
   - If missing, check if task file exists in main repo:
     - Run: `git worktree list` to get main repo path (first entry)
     - Try to read from main repo's `tasks/<task-name>.md`

3. **Install dependencies** (detect, then install):
   Auto-detect the project type when dependencies appear to be missing:

   | Indicator | Missing check | Command |
   |-----------|--------------|---------|
   | `bun.lockb` or `bun.lock` | `node_modules` missing | `bun install` |
   | `package-lock.json` | `node_modules` missing | `npm install` |
   | `pnpm-lock.yaml` | `node_modules` missing | `pnpm install` |
   | `yarn.lock` | `node_modules` missing | `yarn install` |
   | `pubspec.yaml` | `.dart_tool` missing | `flutter pub get` |
   | `Gemfile.lock` | `vendor/bundle` missing | `bundle install` |
   | `go.sum` | — | `go mod download` |
   | `Cargo.lock` | `target` missing | `cargo build` |
   | `requirements.txt` | `.venv` missing | `python -m venv .venv && .venv/bin/pip install -r requirements.txt` |

   Only consider a command if the indicator file exists AND the missing-check directory is absent.

   **Show the command, then run it.** Every command above installs into a project-local
   location (`node_modules`, `target`, `vendor/bundle`, `.venv`, …), so it's safe to run
   without asking — don't add friction. One hard rule:
   - **Never install into the global/system environment.** For Python, always use the local
     `.venv` form in the table — a bare `pip install` would pollute the user's global
     site-packages. If a detected install can't be redirected to a project-local location
     and would mutate the global/system environment, **show it and ask first** instead of
     running it.

4. **Load project context** (optional):
   - If `.claude/knowledge/` exists, query the Knowledge Agent: "What are the project patterns and architecture?"
   - Otherwise, check CLAUDE.md and rules for project context

5. **Check current progress**:
   - Run: `git status --short`
   - Run: `git log --oneline -5`
   - Show what's already been done

6. **Create/update todo list**:
   - Based on TASK.md requirements, create actionable todos
   - Mark any completed items based on git history

7. **Ready to work**:
   ```
   Context loaded for task: <task-name>

   Task Summary:
   <first 3 lines of TASK.md>

   Current Status:
   - Branch: task/<task-name>
   - Changed files: <count>
   - Commits: <count since branching>

   Ready to continue! What would you like to work on?
   ```

## Remember

- Check project CLAUDE.md and rules for project-specific checks and conventions
- Commit regularly with meaningful messages
- Run project-specific checks before creating a PR
