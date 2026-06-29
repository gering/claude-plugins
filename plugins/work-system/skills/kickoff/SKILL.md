---
name: kickoff
description: |
  Creates a `task/<name>` branch off main in an isolated worktree and
  opens a fresh Claude Code session there.
  Trigger: "start working on X", "kickoff", "create a worktree".
user_invocable: true
---

# Start Task with Git Worktree

> Select a task and create an isolated worktree for parallel development

## Critical: never persist a `cd` into the worktree

This skill runs **in the user's main-repo session**. Its job is to *create* the worktree, not to enter it. The user opens the worktree in a separate terminal/Claude session (see step 12).

Because the Bash tool persists working directory between calls, a bare `cd .claude/worktrees/<task>` would silently trap the entire session inside the worktree — every subsequent `git status`, relative path, or check would target the worktree instead of the main repo. This has caused real user-visible bugs.

**Rules for every shell command in this skill:**
- ❌ Never run `cd <worktree>` as a standalone command, or `cd <worktree> && …` without a paired `cd -`/`cd <main-repo>` afterwards.
- ✅ Use `git -C <worktree-path> …` for git operations against the worktree.
- ✅ Use absolute paths or paths relative to the main repo for `cp`, `mkdir`, `ln -s`, etc.
- ✅ If a step genuinely needs a different CWD (rare), wrap it in a subshell: `(cd <worktree-path> && <cmd>)` — the CWD change dies with the subshell.

The same rule applies to any project-specific setup the user's `CLAUDE.md` may ask you to perform (symlinks for `data/`, copying credentials, etc.) — translate them into `git -C` / absolute / subshell form before executing.

## Instructions

1. **Check current location** (shared helper — robust against paths with spaces and symlinks):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" linked` → `main` or `linked`.
     If `linked`, this is already a worktree — stop and suggest `/continue` instead.
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path` → `<main-repo>` — capture
     it for the CWD-drift check in step 11.

2. **Show available tasks**:
   - Run: `find tasks -maxdepth 1 -type f -name '*.md' 2>/dev/null || echo "No tasks found"`
     — `find` (not an `ls tasks/*.md` glob, which lists the whole cwd under bash `nullglob`
     when there are no matches) lists only top-level pending task files and excludes the
     `tasks/archive/` directory `/close` creates.
   - If no tasks exist, suggest creating one with `/define`
   - List all tasks with their first line (title); the task name is the basename minus `.md`
   - Ask user which task to work on

3. **Read selected task**:
   - Read the task file from `tasks/<task-name>.md`
   - Extract task name from filename (e.g., `fix-calendar-bug.md` → `fix-calendar-bug`)

4. **Quick completion check**:
   - If `gh` is available: `gh pr list --state merged --head "task/<task-name>" --limit 1 --json number,title`
   - If merged PR found, warn user and ask if they want to continue or delete the task

5. **Detect main branch**:
   - Run: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
   - If that fails, check if `main` or `master` exists: `git branch --list main master`
   - Use detected branch as base for the worktree

6. **Derive worktree path**:
   - Worktree path: `.claude/worktrees/<task-name>`
   - Create parent directory if needed: `mkdir -p .claude/worktrees`
   - Example: task `fix-bug` → `.claude/worktrees/fix-bug`

7. **Create worktree**:
   - Run: `git worktree add .claude/worktrees/<task-name> -b task/<task-name>`
   - If branch already exists, use: `git worktree add .claude/worktrees/<task-name> task/<task-name>`

8. **Copy files to worktree** (run from main-repo CWD — relative paths target the main repo):
   - Copy task file: `cp tasks/<task-name>.md .claude/worktrees/<task-name>/TASK.md`
   - Copy Claude config if present (a fresh worktree has no `.claude/` dir yet, so create it
     first — otherwise the copy silently no-ops):
     `[ -f .claude/settings.json ] && mkdir -p .claude/worktrees/<task-name>/.claude && cp .claude/settings.json .claude/worktrees/<task-name>/.claude/`
   - This gives Claude in the worktree access to the task and permissions
   - Do **not** `cd` into the worktree to perform copies — paths from the main repo work fine.

9. **Project-specific setup (symlinks, data dirs, etc.)**:
   - For large directories like `node_modules`, configure `symlinkDirectories` in `.claude/settings.json`.
   - If the project's `CLAUDE.md` instructs creating symlinks for shared resources (e.g. a `data/` directory), execute them **without a persistent `cd`**. Safe forms:
     - `ln -s "$(pwd)/data" .claude/worktrees/<task-name>/data` — absolute symlink target, link path relative to main repo.
     - `(cd .claude/worktrees/<task-name> && ln -s ../../../data data)` — subshell, CWD change dies on close.
   - Never run `cd .claude/worktrees/<task-name>` as a standalone or trailing-`&&` command. See the "Critical" note at the top of this file.

10. **Load project context** (optional):
    - If `.claude/knowledge/` exists, query the Knowledge Agent: "What are the project patterns and architecture?"
    - Otherwise, check CLAUDE.md and rules for project context

11. **Verify CWD is still in the main repo**:
    - Run: `pwd` and compare to the `<main-repo>` path captured by the helper in step 1.
    - If they differ, **stop and report an error**: "Session CWD drifted into the worktree during kickoff — investigate which step ran a persistent `cd`." Do not silently continue; a contaminated session will mislead every subsequent command.

12. **Launch the worktree session** — automate it inside herdr, otherwise show
    the manual block.

    Detect herdr: automate **only** when `[ "${HERDR_ENV:-}" = "1" ]`, a non-empty
    `$HERDR_WORKSPACE_ID`, and both `command -v herdr` and `command -v python3`
    succeed. Otherwise show the manual block (b). (The launch helper re-checks
    these and exits non-zero if it cannot automate, so a broken socket degrades
    gracefully too.)

    **a) Inside herdr — open a named tab that auto-continues:**

    Derive a short, sidebar-friendly label from the task name — drop filler words
    (`automate`, `in`, …) so it reads punchy (e.g. `automate-close-in-herdr` →
    `close-herdr`), hard-cap ~32 chars. This `LABEL` names the herdr agent, the
    tab, and the Claude session; the `task/<task-name>` branch is unchanged, so
    `/continue` still resolves the task inside the worktree. The worktree path is
    absolute (`<main-repo>/.claude/worktrees/<task-name>`, with `<main-repo>` from
    step 1). Then call the shared launch helper:

    ```sh
    WORKTREE="<main-repo>/.claude/worktrees/<task-name>"   # absolute path
    LABEL="<short sidebar label, e.g. close-herdr>"
    bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-launch.sh" "$LABEL" "$WORKTREE" "$HERDR_WORKSPACE_ID"
    ```

    The helper spawns Claude as **argv** (`herdr agent start … -- claude -n
    "$LABEL" "/continue"`) — execing the binary directly instead of typing into a
    fresh shell, which structurally avoids the shell-startup keystroke race — then
    moves the agent into its own background tab. It is the **single source of
    truth** for the launch (robust JSON parsing, graceful fallback, exit codes);
    do not re-implement the herdr commands inline. Branch on its result:
    - **exit 0 with `moved=yes`** → the task is running in its own background tab
      (`tab=<id>`). Report success (template below).
    - **exit 0 with `moved=no`** → Claude started but the tab move failed, so it is
      running as a split in *this* session's tab — tell the user it's here, not in
      a new tab.
    - **non-zero exit** → the helper could not automate (herdr/python3 missing,
      broken socket, or no pane id). Show the manual block (b).

    Success report (fill `Tab` from the helper's `tab=` line):
    ```
    Worktree created and launched in herdr!

    Tab:      <LABEL>   (workspace <HERDR_WORKSPACE_ID>, opened in the background)
    Location: .claude/worktrees/<task-name>
    Branch:   task/<task-name>

    The new tab is already running `claude … /continue`. Switch to it to work there.
    ```

    **b) Outside herdr — manual instructions** (display this block — it is *not* a
    command to execute):
    ```
    Worktree created!

    Location: .claude/worktrees/<task-name>
    Branch:   task/<task-name>
    Task file: TASK.md (copied into the worktree)

    👉 To start working there, open a SEPARATE terminal (not this Claude
       session — this session stays in the main repo) and run:

         cd .claude/worktrees/<task-name>
         claude -n "<task-name>" "/continue"
    ```
    `-n "<task-name>"` names the session (shown in `/resume` and the terminal title);
    the `/continue` initial prompt runs the resume flow (load TASK.md, recent commits,
    progress) deterministically — both in one launch. Do **not** execute the `cd` command
    yourself — it is for the user's new terminal. (Inside herdr, path (a) instead names
    the session after the shortened sidebar `LABEL`, so the same task can show a shorter
    name in `/resume` than this manual form.)

## Remember

- Each worktree is an isolated workspace
- Multiple Claude instances can work on different tasks simultaneously
- The main repo stays clean on the main branch
- Use `/close` after PR is merged to clean up
