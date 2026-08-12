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
| `/kickoff` | Start a task in an isolated git worktree, with a choice of worker agent (Claude/codex/grok/kimi) |
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

Creates a worktree, copies the task file, and opens a worker session in it. With
no flag `/kickoff` launches the repo's **default** agent — or, if none is set,
shows a picker and offers to save your choice as the default:

```
> /kickoff add-dark-mode             # the project default (or picker if none set)
> /kickoff add-dark-mode --opus      # claude on opus
> /kickoff add-dark-mode --sol       # codex on gpt-5.6-sol
> /kickoff add-dark-mode --grok      # grok-4.5
> /kickoff add-dark-mode --kimi      # kimi-code on k3-256k
> /kickoff add-dark-mode --pick      # force the interactive picker
```

See [Worker agent selection](#worker-agent-selection) below for the full set.

### 3. Continue in the worktree

Open a new Claude session in the worktree — `-n` names the session, and the
`/work-system:continue` initial prompt loads the task context in one step (use
the plugin-qualified form: a Claude Code built-in `/continue` shadows the bare
skill name):

```
cd .claude/worktrees/add-dark-mode
claude -n "add-dark-mode" "/work-system:continue"
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

**Skip the prompt with a per-repo opt-in.** In a repo where archiving-to-main is
the established norm, the commit+push ask is pure friction. Commit
`.claude/work-system-close-autocommit` (content `yes`) and `/close` skips the
prompt and commits+pushes the archive directly — still reporting the outcome, and
still bounded by the same safety guards (archive-scoped commit, fast-forward
only, never force-pushes, refuses when `main` has other unpushed commits).

Set it with `archive-task.sh autocommit set <repo>` (`unset` to disable, `get` to
check — that subcommand's header documents the exact accepted values and status
codes). Off by default, per-repo only — no global default. The flag is honored
**only once it is committed**: the value is read from the committed version, not
the working tree, so a file that was merely written — by a tool, or by an agent
working in a worktree — never authorizes the skip. Anything unresolved reports
`enabled=no`, you simply get the prompt back, and the tool tells you why. Paths
resolve to the main checkout, so `set` from inside a worktree still writes where
`/close` reads.

Be clear on what that buys you: it raises the bar from *"anything can write a
file"* to *"something had to commit"* — **not** proof a human reviewed it, since
whoever can commit in your repo can commit this too. What bounds the damage is
the commit+push itself: archive-scoped, fast-forward only, never a force-push,
refused when `main` carries other unpushed commits — so the worst case is inert
archived markdown reaching `main` unreviewed.

## Worker agent selection

`/kickoff` doesn't hardcode Claude as the worktree worker. Each agent is a
CLI × model, with availability probed by a script (`scripts/agent-registry.sh`,
the single source of truth). With no flag it launches the repo's **default**; a
flag picks another:

| flag | worker |
|------|--------|
| *(none)* | the repo's project default if set; otherwise the picker (which offers to save the pick) |
| `--pick` | the interactive picker, even when a default is set (unavailable agents are marked, not hidden) |
| `--fable` / `--opus` | claude on fable / opus |
| `--codex` / `--sol` | codex on gpt-5.6-terra / gpt-5.6-sol |
| `--grok` | grok-4.5 |
| `--kimi` | kimi-code on k3-256k (launches in two phases — see below) |
| `--agent <cli[:model]>` | any registry entry, e.g. `--agent claude:sonnet` or `--agent codex` |

**The default is a single per-repo setting** — no global default, no shipped
fallback. It lives in a committed `.claude/work-system-agent` file, so it travels
with the repo. Set it explicitly with `agent-registry.sh default set <name>`, or
just let the picker offer to save your choice the first time you kickoff a task
in a repo with no default yet. Everything is registry-driven — no ranking, no LLM
call; the default is a simple, explicit choice (the hook where future task-aware
routing can plug in).

**Non-Claude workers degrade honestly.** codex/grok/kimi have no work-system
skills, so a launched worker gets a bootstrap prompt (read `TASK.md`, commit, open
a PR) instead of `/continue`. Everything git/PR-derived (`/status`, `/list`, the
`[ws]` statusline, `/close`'s tab teardown) works for any worker; only
claude-session concepts differ. `/continue`'s reopen **always sends `claude -c`**
— the work-system doesn't persist which worker a task used (per-task agent memory
is a later idea), so it can't dispatch per CLI. That resumes a claude worker; for
a codex/grok/kimi task it's a *new* Claude session, so you resume the real worker
yourself in the tab (`codex resume --last` / `grok -c` / `kimi -c`) — `/continue`
surfaces this caveat inline. Since codex and grok read `AGENTS.md`, dropping a
short `AGENTS.md` note into the worktree is an optional way to give them standing
task guidance.

**kimi launches in two phases.** It has no positional launch prompt, and its
one-shot `-p` flag can't be combined with the autonomous `--auto`/`-y` modes — so
the worker is `sh -c 'if kimi -m "$1" -p "$2"; then exec kimi -c --auto; else
<marker + exit>; fi' …`: the `-p` seed works the task through once (it runs tools
unattended), then `exec` hands over to the interactive autonomous session, which
inherits the seed's full history. So unlike the other workers, a kimi tab has
already made progress by the time you switch to it.

If that seed **fails** (expired auth, an unconfigured model), phase 2 is not
reached at all: the wrapper prints a machine-readable marker, says TASK.md was not
started, and exits with the seed's own code. An empty `kimi -c --auto` session
would look like a healthy worker to both you and herdr's detection, which is worse
than no tab. What `/kickoff` can do about it depends on the herdr version. On **0.7.5+** the
wrapper runs inside a shell pane, so the launcher sees that pane fall back to its
prompt without a worker ever appearing, reports the launch as failed and removes
the tab it had created. On **0.7.0–0.7.4** the wrapper *is* the tab's root process,
so herdr closes the tab itself the moment the seed exits; the launcher only
confirms the pane is gone and tells you to run the worker by hand to see its error.

**What that costs you, stated plainly.** Both kimi phases run *without
tool-approval prompts* — the seed by construction (`-p` cannot be combined with
an approval mode) and phase 2 by choice (`--auto`). claude/codex/grok all ask
before acting; kimi does not. So whatever `TASK.md` says is carried out in a
worktree that holds your git credentials, before you have looked at the tab. For
`/kickoff` that file is yours. For `/adopt` it is summarized from **someone
else's** commits and diff — read it before launching a kimi worker on it.
`/kickoff` announces the unattended start whenever the resolved worker is kimi.

## herdr integration

When you run the work system inside a **herdr** session (herdr is a terminal
multiplexer for AI coding agents), `/kickoff`, `/adopt`, and `/close` automate the
terminal juggling you'd otherwise do by hand — `/kickoff` and `/adopt` open the
task's tab, `/close` tears it down. They activate when `HERDR_ENV=1`, a herdr workspace id is set, and
both `herdr` and `python3` are on `PATH`; if any prerequisite is missing or the
socket is unreachable they fall back to the plain, herdr-free behaviour, so you're
never left stranded. Outside herdr, every skill behaves exactly as documented above.

### `/kickoff` and `/adopt` open a named tab

Inside herdr, `/kickoff` doesn't just create the worktree and print manual
instructions — it opens a new herdr **tab** in the *same* workspace, with the
worktree as its cwd, and starts the task there for you. `/adopt` does exactly the
same once it has created the worktree from an existing branch — same helper, same
tab, same worker selection (`--opus`/`--sol`/`--grok`/`--kimi`/`--pick`, or the repo default);
its tab label comes from the *resolved* task name, so it's sensible even when `/adopt`
keeps the original branch name rather than renaming it to `task/<name>`:

- The tab is **named after the task** (shortened for a readable sidebar — see
  `skills/kickoff/SKILL.md` step 13 for the exact rule), so the sidebar shows one
  clear entry per task instead of a wall of identical agents. The same short label
  names the herdr agent (and, for a claude worker, the `-n` session); the task's
  branch — `task/<name>` from `/kickoff`, or the original branch `/adopt` kept — is
  unchanged, so the resume flow still resolves the task.
- The real CLI process is what herdr's agent-state detection ends up watching. A
  claude worker gets `claude --model <m> -n "<label>" "/work-system:continue"` and
  loads the task context automatically; a codex/grok/kimi worker gets a bootstrap
  prompt (read `TASK.md`, drive the task to a PR) since they have no work-system
  skills. A kimi worker runs behind a short `sh -c` wrapper that `exec`s into kimi.
- **How** the worker reaches herdr depends on the herdr version, which the launcher
  feature-detects from `herdr agent start --help` (never from a version number —
  the change landed mid-0.7.x). On **0.7.5+ / 0.8** `agent start` needs an
  already-open pane, so the task's tab is created first and the worker started in
  its root pane (`agent start <label> --kind <cli> --pane <id> -- <args>`), with
  herdr's `agent_pane_busy` — the shell still running its rc files — retried within
  a bounded window. On **0.7.0–0.7.4** the old `agent start --workspace --cwd -- …`
  plus `pane move --new-tab` sequence is kept unchanged. If neither contract is
  recognizable, nothing is created and you get the manual command instead.
- If a launch can't be **confirmed** (the worker never became detectable, an
  unclassifiable herdr error, a pane-id mismatch), the tab is left in place and the
  skill tells you to look at it rather than launching a second worker onto the same
  worktree. Failures that provably started nothing roll their tab back instead, so a
  retry loop can't leave a trail of blank tabs.
- The new tab opens in the background (`--no-focus`), so the session you launched
  from (`/kickoff` or `/adopt`, in the main repo) stays in front; switch to the tab
  when you're ready to work there. On 0.7.5+ the worker lives inside that tab's
  shell, so `/exit` returns you to a shell rather than closing the tab — `/close`'s
  marker + `SessionEnd` hook handle teardown.

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
  fires once the turn ends — Claude exits cleanly, the armed marker + `SessionEnd`
  hook close its tab, and you land back in the main session, hands-free. (The injector polls until the
  prompt is idle before delivering the exit; injecting into a busy TUI is
  unreliable.) If herdr injection isn't available it instead asks you to press
  **Ctrl+D**. Because a self-close fires *after* the turn and can't be confirmed
  in-turn, `/close` always prints the tab id as a fallback — if the tab lingers,
  close it by hand.

A `SessionEnd` hook ships with the plugin for that path: it closes the tab on a
clean exit, but only when `/close` wrote a short-lived per-pane marker file (under
`$HOME/.cache`), so it never fires on an ordinary session exit. On herdr 0.7.5+ the
hook is the *primary* mechanism, not a backup — `agent start` needs an existing
pane, so a kickoff worker runs **inside** a shell pane and its exit drops back to
that shell instead of ending the tab.
All of this lives in the tested `scripts/herdr-teardown.sh`; see
`skills/close/SKILL.md` step 12 for the flow.

### `/work-system:continue <task>` reopens a closed task tab

A bare `/exit` (even just to restart Claude Code) costs you your place: on herdr
0.7.0–0.7.4 the kickoff worker is the tab's **root pane**, so the exit closes the
whole tab; on 0.7.5+ it runs inside a shell pane, so the exit leaves a bare shell
sitting in the worktree. Either way the worktree and the resumable session survive.
Run
`/work-system:continue <task>` **from the main session** to get it back (use the
plugin-qualified form — a Claude Code built-in `/continue` shadows the bare skill
name): it reopens a herdr tab
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
glyph — the same `○ ● ◇ ◆ ✓` set the `[ws …]` statusline segment renders, so
both surfaces speak one visual language (e.g. `● close-herdr`, `◇ ks-label`,
`◆ ready-pr`, `✓ dark-mode`):

- `○` not-started · `●` active (worktree) · `◇` in review (PR open) ·
  `◆` approved (PR review APPROVED — ready to `/merge`) · `✓` merged.

A session sitting in the **main repo root** — the hub you kick tasks off from —
gets `◉` instead (e.g. `◉ Manager`), so the sidebar reads as one hub plus its
task satellites. `◉` marks the *location*, not an identity: every tab at the
main root carries it. It is stateless — the state glyphs belong to tasks.

The mapping and its precedence live in `scripts/ws-statusline.sh` (a `states`
mode next to the render mode — one file, so sidebar and statusline can never
disagree); `scripts/herdr-tab-glyph.sh` applies it to the herdr tab label.

The glyph is stamped when `/kickoff` or `/continue` opens the tab, and
re-stamped — idempotently, only when it changed — whenever you survey or move
task state: `/status`, `/list`, `/close`, `/kickoff` (after launching the new
tab), and `/define` (after writing the task file) refresh every open task tab
of the repo, and the pr-flow skills (`/open`, `/merge`, `/cycle`, `/check`)
trigger the same refresh, so `●` flips to `◇` when the PR opens, to `◆` when it
is approved, and to `✓` when it merges. Survey surfaces (`/status`, `/list`,
`/check`, `/close`, `/kickoff`, `/define`) read the PR cache and never block;
the state-changing skills (`/open`, `/merge`, `/cycle`) do a bounded
synchronous `gh` refresh so the new state shows at once. The same refreshes
stamp `◉` on the main-root tabs — no separate trigger. Only tabs sitting
*exactly* at a task worktree or the main root are
renamed (one cd into a subdir and yours is left alone); a tab is renamed only
when its label actually changes, your chosen label is kept and merely prefixed,
and outside herdr everything is a silent no-op. The glyph lives in the **tab
label** and nowhere else — the herdr agent name and the `claude -n` session name
keep the plain label, since those are stable identities.

## Adopting Existing Branches

Already started work on a branch outside the work system? Use `/adopt` to bring it in:

```
> /adopt feature/dark-mode              # repo default worker
> /adopt feature/dark-mode --opus       # claude on opus
> /adopt feature/dark-mode --sol        # codex on gpt-5.6-sol
> /adopt feature/dark-mode --pick       # force the interactive picker
```

This will:
1. Analyze your branch's commit history and changed files
2. Generate a task file from that context
3. Optionally rename the branch to `task/<name>` for consistency
4. Create a worktree and set everything up
5. Inside a [herdr](#herdr-integration) session, open the task's tab and start the
   chosen worker there (same auto-launch as `/kickoff`); outside herdr it prints the
   manual launch command instead

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
