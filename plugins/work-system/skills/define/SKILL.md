---
name: define
description: |
  Creates a new task markdown file from the current conversation context.
  Extracts intent, acceptance criteria, affected files, and open questions;
  generates a kebab-case task name; saves the file to the project's task
  directory (typically `.claude/tasks/` or `tasks/`) for later resumption.

  Use when: user wants to "create a task", "neuen task anlegen", "track
  this work", "plan this", "capture what we're about to do", needs to
  persist work intent before starting (especially in a separate worktree
  via /kickoff). Also "aufgabe anlegen" / "define".
user_invocable: true
---

# Create New Task

> Create a new task file from current context and conversation

## Arguments

- `$ARGUMENTS` - Optional: brief description of the task

## Instructions

1. **Gather task information**:

   If `$ARGUMENTS` provided, use as starting point for the task description.

   Ask the user:
   - "What should this task accomplish?"
   - "Are there specific files or areas of code involved?"
   - "What are the acceptance criteria?"

2. **Generate task name**:
   - Create a kebab-case name from the description
   - Examples:
     - "Fix the calendar date bug" → `fix-calendar-date-bug`
     - "Add dark mode support" → `add-dark-mode-support`
   - Show proposed name and ask for confirmation

3. **Check for duplicates**:
   - Run: `ls tasks/ 2>/dev/null | grep -i "<keywords>"`
   - If `gh` is available: `gh pr list --state open --search "<keywords>" --limit 3`
   - If similar tasks exist, show them and ask if user wants to continue

4. **Create task file**:

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

5. **Include conversation context**:
   - If there was relevant discussion, summarize it in the Notes section
   - If specific code or files were mentioned, add them to Relevant Files
   - If errors or bugs were discussed, include error messages

6. **Write the file**:
   - Path: `tasks/<task-name>.md`
   - Show the content to user for review
   - Ask: "Create this task file?"

7. **Confirm creation**:
   ```
   ✅ Task created: tasks/<task-name>.md

   Next steps:
   • Start immediately: /kickoff
   • View all tasks: /list
   • Check status later: /status <task-name>
   ```

8. **Optional — Start immediately**:
   - Ask: "Would you like to start working on this task now?"
   - If yes, proceed with `/kickoff` workflow

## Tips

- Keep task names short but descriptive
- One task = one focused goal
- Break large features into multiple tasks
- Include enough context for future-you (or Claude) to understand
