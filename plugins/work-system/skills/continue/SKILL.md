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

1. **Verify we're in a worktree, identify the task**:
   - Run: `git worktree list`
   - Run: `git branch --show-current`
   - Detect the main branch: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'` (fallback: `main`/`master`).
   - If the current branch **is** the main branch, suggest using `/kickoff` instead and stop.
   - Otherwise treat the current branch as the task branch — it may be `task/<name>` from
     `/kickoff`, or an original name kept by `/adopt` (which allows declining the rename).
     Derive the task name by stripping a leading `task/` — or `feature/`, `fix/`, `bugfix/`,
     `hotfix/`, `chore/`, `refactor/` — prefix:
     `task/fix-calendar` → `fix-calendar`, `feature/dark-mode` → `dark-mode`.

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
   | `Gemfile.lock` | `vendor/bundle` missing | `bundle config set --local path vendor/bundle && bundle install` |
   | `go.sum` | — (module cache is shared) | `go mod download` |
   | `Cargo.lock` | `target` missing | `cargo build` |
   | `requirements.txt` | `.venv` missing | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |

   Only consider a command when the indicator file exists **and** the missing-check passes:
   where a directory is listed, require it to be absent; a `—` row has no directory gate
   (run it when the indicator is present — `go mod download` is idempotent if the shared
   module cache is already populated).

   **Show the command, then run it.** Each command in the table installs into a
   project-local location — `node_modules`, `target`, `.dart_tool`, `vendor/bundle`,
   `.venv`, … — so it's safe to run without asking; don't add friction. The two commands
   whose tools would *otherwise* default to a global/shared store are already pinned local
   in the table: Python via `.venv`, Ruby via `bundle config set --local path vendor/bundle`.
   One hard rule:
   - **Never install into the global/system environment.** Don't drop the local-path pinning
     (a bare `pip install` or plain `bundle install` would pollute global site-packages /
     the system gem store). If a detected install can't be redirected to a project-local
     location and would mutate the global/system environment, **show it and ask first**
     instead of running it. (`go mod download` populates Go's shared module cache by design —
     that's expected, not a global install to guard against.)

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
   - Branch: <current branch>
   - Changed files: <count>
   - Commits: <count since branching>

   Ready to continue! What would you like to work on?
   ```

## Remember

- Check project CLAUDE.md and rules for project-specific checks and conventions
- Commit regularly with meaningful messages
- Run project-specific checks before creating a PR
