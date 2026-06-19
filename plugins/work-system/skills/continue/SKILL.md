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

1. **Verify we're in a worktree, identify the task** — via the shared helper:
   - Run: `git worktree list` (confirm this is a linked worktree, not the main repo).
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/task-status.sh" resolve` and read its
     `key=value` output: `on_main`, `detached`, `task_name`, `task_branch`, `main_branch`.
   - If `on_main=yes` (the current branch **is** the main branch), suggest `/kickoff` instead
     and stop.
   - If `detached=yes` (detached HEAD — no current branch), report it and stop: there's no task
     branch to resume; check out the `task/<name>` branch first.
   - Otherwise `<task-branch>` = `task_branch` (the current branch — may be `task/<name>` from
     `/kickoff` or an original name kept by `/adopt`) and `<task-name>` = `task_name` (the
     helper already stripped the `task/`/`feature/`/`fix/`/… prefix). Use these below.

2. **Check for TASK.md**:
   - Read `TASK.md` in the current directory
   - If exists, read and display the task requirements
   - If missing, check if the task file exists in the main repo:
     - Resolve the main repo path robustly (handles paths with spaces — don't hand-parse
       `git worktree list`): `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path`
     - Try to read from `<main-repo>/tasks/<task-name>.md`

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
   | `Cargo.lock` | — (registry cache is shared) | `cargo fetch` |
   | `requirements.txt` | `.venv` missing | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |

   Only consider a command when the indicator file exists **and** the missing-check passes:
   where a directory is listed, require it to be absent; a `—` row has no directory gate
   (run it when the indicator is present — `go mod download` / `cargo fetch` are idempotent if
   the shared cache is already populated).

   Every command only **fetches dependencies** — none compiles the project (that's why Rust
   uses `cargo fetch`, not `cargo build`); keep it that way so resume stays fast.

   **Show the command, then run it.** Each command writes into either a project-local location
   (`node_modules`, `.dart_tool`, `vendor/bundle`, `.venv`, …) or a per-user shared package
   cache (Go's module cache, Cargo's `~/.cargo` registry), so it's safe to run without asking;
   don't add friction. The two tools that would *otherwise* default to a global/shared store
   are pinned local in the table: Python via `.venv`, Ruby via
   `bundle config set --local path vendor/bundle`. One hard rule:
   - **Never install into the global/system environment.** Don't drop the local-path pinning
     (a bare `pip install` or plain `bundle install` would pollute global site-packages /
     the system gem store). If a detected install can't be redirected to a project-local
     location and would mutate the global/system environment, **show it and ask first**
     instead of running it. (Go's and Cargo's shared package caches are populated by design —
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
