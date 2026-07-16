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
| `/continue` | Resume the current task (in a worktree); or `/continue <task>` from the main session reopens the task's herdr tab and resumes it |
| `/status` | Check task status (PRs, branches, commits) |
| `/close` | Clean up after merge (worktree, branches; archives the task file) |
| `/list` | Overview of all tasks, worktrees, and status |
| `/statusline` | Add a `[ws …]` task-backlog segment to Claude Code's status line |

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

Instead of deleting the task file, `/close` **archives** it to
`tasks/archive/<name>.md` with a closed-stamp (date, shipping PR + merge commit,
branch) and appends a one-line entry to `tasks/archive/_index.md` — a queryable
record of finished work. The archive inherits whatever `tasks/` does: gitignored
`tasks/` keeps it local-only; otherwise the move is committable and `/close` asks
once to commit **and** fast-forward-push it to `main` (so local `main` never
diverges; a failed push just leaves the commit local). `/list` shows the archived
count in its summary.

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
file) and then closes that task's herdr **tab** too. It identifies the tab by
matching the pane's cwd (no persisted layout file) *before* removing the worktree,
and decides self-close vs. a different-tab close by pane id. Two entry points:

- **From the main session** (the usual case): `/close <task>` closes the
  worktree's tab directly — a different tab, so nothing self-terminates. It then
  **verifies** the tab is gone; if the close didn't take — or herdr couldn't be
  re-queried to confirm it — it names the tab so you can close it by hand rather
  than leaving a silent orphan.
- **From inside the worktree tab**: Claude cannot close its own tab, only exit
  cleanly. So `/close` focuses the main tab and arms a **detached `/exit`** that
  fires once the turn ends — Claude exits cleanly, its tab auto-closes, and
  you land back in the main session, hands-free. (The injector polls until the
  prompt is idle before delivering the exit; injecting into a busy TUI is
  unreliable.) If herdr injection isn't available it instead asks you to press
  **Ctrl+D**. Because a self-close fires *after* the turn and can't be confirmed
  in-turn, `/close` always prints the tab id as a fallback — if the tab lingers,
  close it by hand.

For the self-close path a `SessionEnd` hook ships with the plugin as a backup: it
closes the tab on a clean exit, but only when `/close` wrote a short-lived per-pane
marker file (under `$HOME/.cache`), so it never fires on an ordinary session exit.
All of this lives in the tested `scripts/herdr-teardown.sh`; see
`skills/close/SKILL.md` step 12 for the flow.

### `/continue <task>` reopens a closed task tab

A kickoff tab runs Claude as its **root pane**, so a bare `/exit` (even just to
restart Claude Code) ends that pane and herdr closes the whole tab — the worktree
and the resumable session survive, but you lose your place. Run
`/continue <task>` **from the main session** to get it back: it reopens a herdr tab
at the task's worktree and resumes the existing session with `claude -c` (the
most-recent session for that cwd — since each worktree hosts exactly one task, the
cwd identifies it unambiguously), then focuses the tab.

The reopened tab is hardened against the same `/exit`: Claude runs **inside a shell
pane** (not as the root pane), so a later `/exit` just drops back to the shell and
the tab stays open. If the task's tab is in fact still open (you never `/exit`-ed),
reopen just **focuses** it rather than starting a second `claude -c` on the same
worktree. Outside herdr, `/continue <task>` prints the manual
`cd <worktree> && claude -c` block instead. Run from *inside* a worktree,
`/continue` is unchanged — it loads context and resumes in place (pass a *different*
task name to reopen that one's tab from here). The reopen shares the tested
`scripts/herdr-launch.sh` with `/kickoff` (a `resume` mode alongside `launch`); see
`skills/continue/SKILL.md`.

### Task tabs carry their state glyph

Inside herdr, every task tab's sidebar name is prefixed with the task's state
glyph — the same `○ ● ◇ ✓` set (not-started / active / in-review / merged) the
`[ws …]` statusline segment renders, so both surfaces speak one visual
language (e.g. `● close-herdr`, `◇ ks-label`, `✓ dark-mode`). The mapping and
its precedence live in `scripts/ws-statusline.sh` (a `states` mode next to the
render mode — one file, so sidebar and statusline can never disagree);
`scripts/herdr-tab-glyph.sh` applies it to herdr agent names.

The glyph is stamped when `/kickoff` or `/continue` opens the tab, and
re-stamped — idempotently, only when it changed — whenever you survey or move
task state: `/status`, `/list`, and `/close` (for the remaining tabs) refresh
every open task tab of the repo, and the pr-flow skills (`/open`, `/merge`,
`/cycle`, `/check`) trigger the same refresh after PR state changes, so `●`
flips to `◇` when the PR opens and to `✓` when it merges. Agents outside task
worktrees are never renamed; outside herdr everything is a silent no-op.

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
