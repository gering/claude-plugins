# Work System Plugin

Generic task and worktree workflow system for Claude Code. Manage tasks as markdown files, work in isolated git worktrees, and track progress through the full lifecycle.

## Workflow

```
/define  →  /kickoff  →  /continue  →  /status  →  /close
    │                │                  │                  │               │
 Create task    Create worktree    Load context &    Check PR/branch   Clean up
 markdown file  & branch           resume work       status            everything

              /adopt
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
| `/define` | Create a new task (markdown file with Goal/Context/Requirements) |
| `/kickoff` | Start a task in an isolated git worktree |
| `/adopt` | Adopt an existing branch into the work system |
| `/continue` | Resume work on current task, load context |
| `/status` | Check task status (PRs, branches, commits) |
| `/close` | Clean up after merge (worktree, branches, task file) |
| `/list` | Overview of all tasks, worktrees, and status |

## How It Works

### Tasks

Each task is a markdown file in your project's `tasks/` directory. This is a centralized backlog on the main worktree — `/define` always writes there, even when invoked from inside a linked worktree, so tasks stay visible to `/kickoff` and `/list` and survive `/close`:

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
> /define Add dark mode support
```

### 2. Start working

```
> /kickoff
```

Creates a worktree, copies the task file, and shows how to open a new Claude session in it.

### 3. Continue in the worktree

Open a new Claude session in the worktree — `-n` names the session, and the
`/continue` initial prompt loads the task context in one step:

```
cd .claude/worktrees/add-dark-mode
claude -n "add-dark-mode" "/continue"
```

Loads the task context, checks dependencies, and shows current progress.

### 4. Check status

```
> /status add-dark-mode
```

### 5. Close after merge

```
> /close
```

## herdr integration

When you run the work system inside a **herdr** session (herdr is a terminal
multiplexer for AI coding agents), `/kickoff` and `/close` automate the terminal
juggling you'd otherwise do by hand — `/kickoff` opens the task's tab, `/close`
tears it down. They activate when `HERDR_ENV=1`, a herdr workspace id is set, and
both `herdr` and `python3` are on `PATH`; if any prerequisite is missing or the
socket is unreachable they fall back to the plain, herdr-free behaviour, so you're
never left stranded. Outside herdr, every skill behaves exactly as documented above.

### `/kickoff` opens a named tab

Inside herdr, `/kickoff` doesn't just create the worktree and print manual
instructions — it opens a new herdr **tab** in the *same* workspace, with the
worktree as its cwd, and starts the task there for you:

- The tab is **named after the task** (shortened for a readable sidebar — see
  `skills/kickoff/SKILL.md` step 12 for the exact rule), so the sidebar shows one
  clear entry per task instead of a wall of identical `claude` agents. The same
  short label names the herdr agent and the Claude session; the underlying
  `task/<name>` branch is unchanged, so `/continue` still resolves the task.
- Claude is launched directly (`herdr agent start … -- claude -n "<label>"
  "/continue"`), so the real `claude` process is what herdr's agent-state
  detection sees, and `/continue` loads the task context automatically on startup.
- The new tab opens in the background (`--no-focus`), so your kickoff session
  stays in front; switch to the tab when you're ready to work there.

### `/close` tears down the task's tab

Once a task is merged, `/close` runs its usual cleanup (worktree, branch, task
file) and then closes that task's herdr **tab** too. It finds the tab by cwd — no
state file — looking it up *before* removing the worktree. Two entry points:

- **From the main session** (the usual case): `/close <task>` closes the
  worktree's tab directly — a different tab, so nothing self-terminates.
- **From inside the worktree tab**: Claude cannot close its own tab, only exit
  cleanly. So `/close` focuses the main tab and arms a **detached `/exit`** that
  fires the moment the turn ends — Claude exits cleanly, its tab auto-closes, and
  you land back in the main session, hands-free. (The exit is delivered to an idle
  prompt, not mid-turn; injecting into a busy TUI is unreliable.) If herdr
  injection isn't available it instead asks you to press **Ctrl+D**.

A `SessionEnd` hook ships with the plugin as a backup for the self-close path: it
closes the tab on a clean exit, but only when `/close` armed a per-pane marker, so
it never fires on an ordinary session exit. All of this lives in the tested
`scripts/herdr-teardown.sh`; see `skills/close/SKILL.md` step 12 for the flow.

## Adopting Existing Branches

Already started work on a branch outside the work system? Use `/adopt` to bring it in:

```
> /adopt feature/dark-mode
```

This will:
1. Analyze your branch's commit history and changed files
2. Generate a task file from that context
3. Optionally rename the branch to `task/<name>` for consistency
4. Create a worktree and set everything up

After adoption, the standard workflow applies (`/continue`, `/status`, `/close`).

## Project-Specific Configuration

- **Dependencies**: Auto-detects project type (Node, Python, Rust, Flutter, Go, Ruby) when dependencies are missing in a worktree, shows the install command, and runs it — pinned to a project-local location (`.venv`, `vendor/bundle`, …), asking first only before any global/system install.
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
