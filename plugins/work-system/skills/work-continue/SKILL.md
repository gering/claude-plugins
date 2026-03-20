---
name: work-continue
description: Load context and continue working on the current task in a worktree
user_invocable: true
---

# Continue Task in Worktree

> Load context and continue working on the current task

## Instructions

1. **Verify we're in a worktree**:
   - Run: `git worktree list`
   - Run: `git branch --show-current`
   - If on the main branch (not a `task/*` branch), suggest using `/work-start` instead
   - Extract task name from branch (e.g., `task/fix-calendar` → `fix-calendar`)

2. **Check for TASK.md**:
   - Read `TASK.md` in the current directory
   - If exists, read and display the task requirements
   - If missing, check if task file exists in main repo:
     - Run: `git worktree list` to get main repo path (first entry)
     - Try to read from main repo's `tasks/<task-name>.md`

3. **Install dependencies automatically**:
   Auto-detect project type and install if dependencies are missing:

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
   | `requirements.txt` | `.venv` missing | `pip install -r requirements.txt` |

   Only run if the indicator file exists AND the missing check directory is absent.
   Run the install command automatically — do NOT ask, just run it and show the result.

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
