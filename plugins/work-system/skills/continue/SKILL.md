---
name: continue
description: |
  Resumes a task, or reopens its herdr tab from the main session with
  `/continue <task>`.
  Trigger: "continue", "resume the task", "reopen <task>", "pick up where I left off".
user_invocable: true
---

# Continue Task in Worktree

> Load context and continue working on the current task

## Instructions

`/continue` runs in one of two modes, chosen by **where it is invoked**:

- **Inside a worktree** → *in-session resume*: load TASK.md, deps, recent commits,
  progress; keep working here. (Steps 1–7 under "In-session resume".)
- **From the main session with a `<task>` argument** → *reopen*: open the task's
  herdr tab at its worktree and resume its Claude session there (`claude -c`), then
  focus it — recovering a task tab that a bare `/exit` closed (kickoff tabs run
  Claude as the root pane, so `/exit` closes them). ("Reopen mode" below.)

**Pick the mode first:**
- Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" linked` → `main` | `linked`.
  - `linked` (inside a worktree):
    - **No argument, or the argument names *this* worktree's task** → **in-session
      resume**: go to step 1 below.
    - **Argument names a *different* task** → **Reopen mode** for that task (you can
      reopen another task's tab from here — `main-repo-path.sh path` still resolves the
      main repo from a linked worktree). Do **not** silently ignore the argument and
      resume the current task instead.
  - `main` **with** a `<task>` argument → **Reopen mode**.
  - `main` **without** an argument → nothing to resume in-session here; tell the user
    to name a task (`/continue <task>`) or use `/kickoff` / `/list`, and stop.

### Reopen mode (main session + `<task>` arg)

1. **Resolve the task's worktree:**
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/task-status.sh" resolve "<task>"` and read
     `task_name` (prefix stripped) and `on_main`. If `on_main=yes`, the name resolved to
     the main branch — not a task; ask for a real task name and stop.
   - Resolve the main repo, then the worktree path:
     `MAIN_REPO="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path)"` →
     `WORKTREE="$MAIN_REPO/.claude/worktrees/<task-name>"`.
   - If `WORKTREE` is not a directory, there is no worktree for this task — suggest
     `/kickoff <task>` (or `/list`) and stop.

2. **Reopen + resume** — automate inside herdr, otherwise show the manual block.

   Detect herdr: automate **only** when `[ "${HERDR_ENV:-}" = "1" ]`, a non-empty
   `$HERDR_WORKSPACE_ID`, and both `command -v herdr` and `command -v python3`
   succeed. (The helper re-checks these and exits non-zero if it cannot automate, so
   a broken socket degrades to the manual block too.)

   **a) Inside herdr — reopen the tab and resume via the shared helper:**
   ```sh
   LABEL="<short sidebar label from the task name, e.g. close-herdr>"   # same convention as /kickoff
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-launch.sh" resume "$LABEL" "$WORKTREE" "$HERDR_WORKSPACE_ID"
   ```
   The helper finds an already-open tab at the worktree and focuses it, or (if none)
   creates a tab and runs `claude -c` **inside a shell pane** (so a later `/exit`
   drops back to the shell and the tab survives), then focuses it. It is the **single
   source of truth** for the herdr commands (robust JSON parsing, graceful fallback,
   exit codes) — do not inline them. Branch on its `key=value` output:
   - **exit 0, `reused=yes`** → a tab was already open at this worktree (the task was
     never `/exit`-ed); the helper just focused it — **no** second session was started.
     Tell the user the task was already open and is now focused.
   - **exit 0, `reused=no resumed=yes`** → a fresh tab was opened and `claude -c` was
     sent into it; report the reopen (template below).
   - **exit 0, `reused=no resumed=no`** → the tab opened but `claude -c` could not be
     sent; tell the user the tab is up at the worktree and to run `claude -c` in it by
     hand.
   - **exit 0 with an empty `tab=`** (and `reused=no`) → the pane opened (`pane=<id>`)
     but its tab id couldn't be read; tell the user it's up and may need a manual focus.
   - **non-zero exit** → the helper could not automate (herdr/python3 missing, broken
     socket, or no pane id). Show the manual block (b).

   Success report for the `resumed=yes` case (fill `Tab` from the `tab=` line):
   ```
   Reopened task tab: <LABEL>   (workspace <HERDR_WORKSPACE_ID>)
   Worktree: .claude/worktrees/<task-name>

   The tab is running `claude -c` to resume the task's most-recent session. Switch to
   it to continue. (If this worktree never hosted a Claude session — e.g. it came from
   `/adopt` — `claude -c` will report nothing to resume; just start `claude` there.)
   ```

   **b) Outside herdr — manual block** (display this — do **not** execute the `cd`):
   ```
   Reopen the task in a SEPARATE terminal:

       cd <worktree>
       claude -c        # resume the most-recent session for this worktree
   ```
   `claude -c` continues where the task left off; `claude --resume` instead opens a
   picker if `-c` lands on the wrong session. Do **not** run the `cd` yourself — it is
   for the user's terminal.

### In-session resume (inside a worktree)

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
