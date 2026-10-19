---
name: define
description: |
  Creates a task (GitHub issue or local file) from current context: intent,
  acceptance criteria, affected files, open questions.
  Trigger: "create a task", "define a task", "capture this work".
user_invocable: true
---

# Create New Task

> Create a new task from current context and conversation

## Where the task lands

The backlog is **centralized** and visible to `/kickoff` and `/list`: by default GitHub Issues
in origin's repository, or markdown files in the main worktree's `tasks/` (setting
`tasks.backend`). Only `task-store.py` knows which — never write to `tasks/` or call `gh`
directly. From a linked worktree the task still lands in the main repo's backlog, not on the
task branch.

Resolve the main repo path once (step 1) and use it below. Never persist a `cd` — Bash CWD leaks across tool calls.

## Arguments

- `$ARGUMENTS` - Optional: brief description of the task

## Instructions

1. **Resolve the main repo path** (shared helper — handles paths with spaces and symlinks):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path` → `<main-repo>`.
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" linked` → `main` or `linked`.
   - Run: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/task-store.py" backend "<main-repo>"` → `backend=github|local`, plus `repo=` and
     `visibility=` (github) or `tasks_dir=` (local), and maybe `hint=`. Non-zero exit → show
     its stderr and stop: exit 4 is a GitHub backend whose `gh` is missing, unauthenticated or
     unreachable, and there is no fallback to a local file.

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
   - Run: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/task-store.py" list "<main-repo>"` and look for titles or names matching the keywords
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

7. **Create the task**:
   - Show the content to the user for review and ask once, naming the target:
     - github: "Create this task as an issue in `<repo>`?" — and when `visibility=public`,
       add that the issue will be **publicly visible**.
     - local: "Create this task file in `<tasks_dir>/`?"
   - On yes: write the content to a temp file in your scratchpad, run
     `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/task-store.py" create "<main-repo>" "<task-name>" --body-file <tmp>` (the `# <Task Title>`
     heading becomes the issue title), then delete the temp file.
   - Non-zero exit → show its stderr; exit 5 means the name is taken — propose another.

8. **Confirm creation**:
   ```
   ✅ Task created: <#N and url= | path=, from step 7's output>

   Next steps:
   • Start immediately: /kickoff
   • View all tasks: /list
   • Check status later: /status <task-name>
   ```
   - When step 1 reported `linked`, add: "Added to the main repo's backlog, not this worktree."

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
