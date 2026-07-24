---
name: define
description: |
  Creates a task file from current context: intent, acceptance criteria,
  affected files, open questions.
  Trigger: "create a task", "define a task", "capture this work".
user_invocable: true
---

# Create New Task

> Create a new task file from current context and conversation

## Where the task file lands

Task files are a **centralized backlog** on the main worktree, visible to `/kickoff` and `/list`. When `/define` runs from inside a linked worktree, the file must still land in the **main repo's** `tasks/` — otherwise it's isolated on the task branch and lost when `/close` removes the worktree.

Resolve the main repo path once (step 1) and use it for every path below. Never persist a `cd` — Bash CWD leaks across tool calls; use the absolute `<main-repo>` path instead.

## Arguments

- `$ARGUMENTS` - Optional: brief description of the task

## Instructions

1. **Resolve the main repo path** (shared helper — handles paths with spaces and symlinks):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path` → `<main-repo>` (the main worktree, where the shared `tasks/` backlog lives).
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" linked` → `main` or `linked`. `linked` = invoked from a worktree (the task file still goes to `<main-repo>/tasks/`); `main` = behavior unchanged.

2. **Gather task information**:

   If `$ARGUMENTS` provided, use as starting point for the task description.

   Ask the user:
   - "What should this task accomplish?"
   - "Are there specific files or areas of code involved?"
   - "What are the acceptance criteria?"

3. **Generate task name**:
   - Create a kebab-case name from the description
   - Examples:
     - "Fix the calendar date bug" → `fix-calendar-date-bug`
     - "Add dark mode support" → `add-dark-mode-support`
   - Show proposed name and ask for confirmation

4. **Check for duplicates**:
   - Run: `ls "<main-repo>/tasks/" 2>/dev/null | grep -i "<keywords>"`
   - If `gh` is available: `gh pr list --state open --search "<keywords>" --limit 3`
   - If similar tasks exist, show them and ask if user wants to continue

5. **Create task file**:

   Template:
   ```markdown
   # <Task Title>

   ## Goal
   <What should be accomplished>

   ## Context
   <Background information, why this is needed>

   ## Requirements
   - [ ] Requirement 1
   - [ ] Requirement 2
   - [ ] Requirement 3

   ## Relevant Files
   - `<file-path-1>`
   - `<file-path-2>`

   ## Notes
   <Any additional context from the conversation>
   ```

6. **Include conversation context**:
   - If there was relevant discussion, summarize it in the Notes section
   - If specific code or files were mentioned, add them to Relevant Files
   - If errors or bugs were discussed, include error messages

7. **Write the file**:
   - Path: `<main-repo>/tasks/<task-name>.md` (absolute — never `cd` into the main repo to write it)
   - Show the content to user for review
   - Ask: "Create this task file?"

8. **Confirm creation**:
   ```
   ✅ Task created: <main-repo>/tasks/<task-name>.md

   Next steps:
   • Start immediately: /kickoff
   • View all tasks: /list
   • Check status later: /status <task-name>
   ```
   - When step 1 reported `linked`, add: "Written to the main repo backlog, not this worktree."

9. **Sync herdr tab glyphs** (best-effort, silent):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-tab-glyph.sh" refresh --cached "<main-repo>"`
     (the `<main-repo>` path from step 1).
   - The new task has no tab yet (nothing to stamp for it) — the value is the
     main-repo tab's `◉` hub mark and re-deriving sibling worker glyphs while
     already in the main session, so `--cached` reads the PR cache instead of a
     blocking `gh` call. Outside herdr it is a silent no-op. Ignore its output —
     never block or report on it.

10. **Optional — Start immediately**:
    - Ask: "Would you like to start working on this task now?"
    - If yes, proceed with `/kickoff` workflow

## Tips

- Keep task names short but descriptive
- One task = one focused goal
- Break large features into multiple tasks
- Include enough context for future-you (or Claude) to understand
