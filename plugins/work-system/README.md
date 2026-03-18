# Work System Plugin

Generic task and worktree workflow system for Claude Code. Manage tasks as markdown files, work in isolated git worktrees, and track progress through the full lifecycle.

## Workflow

```
/work-create  →  /work-start  →  /work-continue  →  /work-check  →  /work-close
    │                │                  │                  │               │
 Create task    Create worktree    Load context &    Check PR/branch   Clean up
 markdown file  & branch           resume work       status            everything

              /work-adopt
                  │
           Adopt existing branch
           into the work system
```

### Task Lifecycle

```
📋 Not Started  →  🔄 In Progress  →  🔍 In Review  →  ✅ Merged
  (task file)      (worktree)         (PR open)        (ready to close)
```

## Commands

| Command | Description |
|---------|-------------|
| `/work-create` | Create a new task (markdown file with Goal/Context/Requirements) |
| `/work-start` | Start a task in an isolated git worktree |
| `/work-adopt` | Adopt an existing branch into the work system |
| `/work-continue` | Resume work on current task, load context |
| `/work-check` | Check task status (PRs, branches, commits) |
| `/work-close` | Clean up after merge (worktree, branches, task file) |
| `/work-list` | Overview of all tasks, worktrees, and status |

## How It Works

### Tasks

Each task is a markdown file in your project's `tasks/` directory:

```markdown
# Add Dark Mode Support

## Goal
Add a dark mode toggle to the settings page.

## Context
Users have requested dark mode support. The app currently only has a light theme.

## Requirements
- [ ] Add theme toggle in settings
- [ ] Implement dark color scheme
- [ ] Persist user preference

## Relevant Files
- `src/theme.ts`
- `src/components/Settings.tsx`

## Notes
Design mockups are in Figma.
```

### Worktrees

When you start a task, a git worktree is created under `.claude/worktrees/`:

```
my-project/
├── .claude/worktrees/
│   ├── dark-mode/     ← worktree (on task/dark-mode branch)
│   └── fix-bug/       ← worktree (on task/fix-bug branch)
├── src/
└── ...
```

This is consistent with where Claude Desktop/Web creates worktrees. Multiple Claude instances can work on different tasks simultaneously without conflicts.

### Branch Naming

Branches follow the pattern `task/<task-name>`:
- Task file: `tasks/add-dark-mode.md`
- Branch: `task/add-dark-mode`
- Worktree: `.claude/worktrees/add-dark-mode`

## Typical Workflow

### 1. Create a task

```
> /work-create Add dark mode support
```

### 2. Start working

```
> /work-start
```

Creates a worktree, copies the task file, and shows how to open a new Claude session in it.

### 3. Continue in the worktree

```
cd .claude/worktrees/add-dark-mode
claude
> /work-continue
```

Loads the task context, checks dependencies, and shows current progress.

### 4. Check status

```
> /work-check add-dark-mode
```

### 5. Close after merge

```
> /work-close
```

## Adopting Existing Branches

Already started work on a branch outside the work system? Use `/work-adopt` to bring it in:

```
> /work-adopt feature/dark-mode
```

This will:
1. Analyze your branch's commit history and changed files
2. Generate a task file from that context
3. Optionally rename the branch to `task/<name>` for consistency
4. Create a worktree and set everything up

After adoption, the standard workflow applies (`/work-continue`, `/work-check`, `/work-close`).

## Project-Specific Configuration

- **Dependencies**: Auto-detects project type (Node, Python, Rust, Flutter, Go, Ruby) and suggests install commands when dependencies are missing in worktrees.
- **Symlinks**: Configure `symlinkDirectories` in `.claude/settings.json` for large directories that shouldn't be duplicated.
- **Checks**: Add project-specific build/test/lint commands to your project's `CLAUDE.md`.
- **Knowledge integration**: If you use the [knowledge-system](../knowledge-system/) plugin, context is automatically loaded when starting or continuing tasks.

## Requirements

- Git (with worktree support)
- [GitHub CLI](https://cli.github.com/) (`gh`) — optional, for PR integration

## Installation

Part of the [gering-plugins](https://github.com/gering/claude-plugins) marketplace:

```
/plugin marketplace add gering/claude-plugins
/plugin install work-system
```
