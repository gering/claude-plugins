---
name: kickoff
description: |
  Creates an isolated `task/<name>` worktree off main and opens a worker
  session there (Claude, codex, or grok — your pick).
  Trigger: "start working on X", "kickoff", "create a worktree".
user_invocable: true
---

# Start Task with Git Worktree

> Select a task and create an isolated worktree for parallel development

## Critical: never persist a `cd` into the worktree

This skill runs **in the user's main-repo session**. Its job is to *create* the worktree, not to enter it. The user opens the worktree in a separate terminal/Claude session (see step 13).

Because the Bash tool persists working directory between calls, a bare `cd .claude/worktrees/<task>` would silently trap the entire session inside the worktree — every subsequent `git status`, relative path, or check would target the worktree instead of the main repo. This has caused real user-visible bugs.

**Rules for every shell command in this skill:**
- ❌ Never run `cd <worktree>` as a standalone command, or `cd <worktree> && …` without a paired `cd -`/`cd <main-repo>` afterwards.
- ✅ Use `git -C <worktree-path> …` for git operations against the worktree.
- ✅ Use absolute paths or paths relative to the main repo for `cp`, `mkdir`, `ln -s`, etc.
- ✅ If a step genuinely needs a different CWD (rare), wrap it in a subshell: `(cd <worktree-path> && <cmd>)` — the CWD change dies with the subshell.

The same rule applies to any project-specific setup the user's `CLAUDE.md` may ask you to perform (symlinks for `data/`, copying credentials, etc.) — translate them into `git -C` / absolute / subshell form before executing.

## Instructions

### Arguments: `<task> [agent-selector]`

The **task name** is the argument token that does **not** start with `-` — with
one exception: `--agent` **consumes the immediately-following token as its value**
(the `cli[:model]`), so that token is *not* a task-name candidate. So in
`/kickoff --agent claude:sonnet add-dark-mode`, `claude:sonnet` is the agent value
and `add-dark-mode` is the task. Every other selector is valueless. An optional
**agent selector** picks the worker CLI×model — a `--…` token is never the task name:

| selector | worker |
|----------|--------|
| *(none)* | the repo's **project default** if set; otherwise the interactive picker |
| `--pick` | the interactive picker (even when a default is set) |
| `--fable` / `--opus` | claude on fable / opus |
| `--codex` / `--sol` | codex on gpt-5.6-terra / gpt-5.6-sol |
| `--grok` | grok-4.5 |
| `--agent <cli[:model]>` | any registry entry, e.g. `--agent claude:sonnet` or `--agent codex` |

This table mirrors `agent-registry.sh` for reader convenience only — **never
hardcode it in a decision**. Step 12 resolves the selector through the script,
which is the single source of truth for aliases, models, availability, and the
project default. There is **no** global default and no shipped fallback: a repo
with no default gets the picker, which then offers to save the pick. The default
is a per-repo committed file (`.claude/work-system-agent`), set via
`agent-registry.sh default set <name>`.

1. **Check current location** (shared helper — robust against paths with spaces and symlinks):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" linked` → `main` or `linked`.
     If `linked`, this is already a worktree — stop and suggest `/continue` instead.
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path` → `<main-repo>` — capture
     it for the CWD-drift check in step 11.

2. **Show available tasks**:
   - Run: `find tasks -maxdepth 1 -type f -name '*.md' 2>/dev/null` — `find` (not an
     `ls tasks/*.md` glob, which lists the whole cwd under bash `nullglob` when there are no
     matches) lists only top-level pending task files and excludes the `tasks/archive/`
     directory `/close` creates.
   - **If the output is empty** (note: `find` exits 0 even with no matches, so don't rely on a
     `||` fallback): there are no pending tasks — suggest creating one with `/define` and stop.
   - Otherwise list each task with its first line (title); the task name is the basename minus
     `.md`. Ask the user which task to work on.

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

12. **Select the worker agent** — turn the argument selector into a concrete
    `SELECTOR`, which step 13 passes straight to the launch helper. Also set
    `OFFER_DEFAULT=no` (flipped to `yes` only on the picker path below).
    `REG="${CLAUDE_PLUGIN_ROOT}/scripts/agent-registry.sh"`.

    - **An explicit flag was given** (`--fable`, `--opus`, `--codex`, `--sol`,
      `--grok`, or `--agent <cli[:model]>`): `SELECTOR` is that flag (for
      `--agent`, the `cli[:model]` value, e.g. `claude:sonnet`). One-off — no
      default offer.
    - **No flag:** read the repo default: `SELECTOR="$(bash "$REG" default get)"`
      (the helper validates the committed value; a stale/unknown name prints
      empty, so it can't route the launch).
      - **Non-empty** → use it directly (the common path: no picker). **If that
        default is a non-claude worker** (`SELECTOR` starts `codex:`/`grok:`),
        first **announce** it — e.g. "Launching **codex:gpt-5.6-sol** (project
        default) — this sends the task to a third-party model; pass `--pick` to
        choose another." This is a visibility line, **not** a prompt: a committed
        default from a cloned repo shouldn't silently route your code off-Claude,
        but it also shouldn't block. Claude defaults launch with no such line.
      - **Empty** (no project default set, or the committed value was invalid) →
        fall through to the **picker** below.
    - **`--pick`, or no flag with no default set → the picker.** Run
      `bash "$REG" list` and present the rows with **AskUserQuestion**: one option
      per entry, label = the `NAME` (`cli:model`), description = the model plus its
      availability (append the `NOTE`, e.g. "unavailable — run: grok login", for
      any row with `AVAILABLE=no`). **List unavailable entries too — do not hide
      them** (mark them), order available first. In the **same** AskUserQuestion
      call add a second question, "Save this as the project default?" (Yes / No).
      Set `SELECTOR` = the picked `NAME`; set `OFFER_DEFAULT=yes` **only if** the
      user chose Yes to that second question (otherwise leave it `no`). (Interpret
      the answer, don't string-match a label — "Yes" means yes.)

    Do not resolve models, the default, or availability yourself — the helper owns
    that. Step 13 passes `SELECTOR` to `herdr-launch.sh`, which resolves +
    validates it (and reports a clear error if it is unavailable), so an
    unavailable pick is handled there, not here.

13. **Launch the worktree session** — automate it inside herdr, otherwise show
    the manual block.

    Detect herdr: automate **only** when `[ "${HERDR_ENV:-}" = "1" ]`, a non-empty
    `$HERDR_WORKSPACE_ID`, and both `command -v herdr` and `command -v python3`
    succeed. Otherwise show the manual block (b). (The launch helper re-checks
    these and exits non-zero if it cannot automate, so a broken socket degrades
    gracefully too.)

    **a) Inside herdr — open a named tab that auto-continues:**

    Derive a short, sidebar-friendly label from the task name — drop filler words
    (`automate`, `in`, …) so it reads punchy (e.g. `automate-close-in-herdr` →
    `close-herdr`), hard-cap ~32 chars. Pass it PLAIN — the launch helper
    prefixes the task's state glyph (`○ ● ◇ ◆ ✓`, e.g. `● close-herdr`) onto the
    **tab label** itself, mirroring the `[ws …]` statusline. This `LABEL` names the
    herdr agent, the tab, and (for a claude worker) the `-n` session — but the glyph
    rides only on the tab label (what the sidebar renders); the agent and session
    names stay plain. The `task/<task-name>` branch is unchanged, so `/continue`
    still resolves the task inside the worktree. (Only a claude worker runs
    `/continue` — codex/grok get a bootstrap prompt from the registry instead; see
    step 12.) The worktree path is
    absolute (`<main-repo>/.claude/worktrees/<task-name>`, with `<main-repo>` from
    step 1). Then call the shared launch helper:

    ```sh
    WORKTREE="<main-repo>/.claude/worktrees/<task-name>"   # absolute path
    LABEL="<short sidebar label, e.g. close-herdr>"
    bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-launch.sh" launch "$LABEL" "$WORKTREE" "$HERDR_WORKSPACE_ID" "$SELECTOR"
    ```

    The helper resolves `SELECTOR` through the registry and spawns the worker as
    **argv** (`herdr agent start … -- <resolved worker argv>`) — execing the binary
    directly instead of typing into a fresh shell, which structurally avoids the
    shell-startup keystroke race — then moves the agent into its own background tab.
    It is the **single source of truth** for the launch (resolution, robust JSON
    parsing, graceful fallback, exit codes); do not re-implement the herdr commands
    or the resolution inline. Branch on its result:
    - **exit 0 with `moved=yes`** → the task is running in its own background tab
      (`tab=<id>`) with worker `agent=<cli:model>`. Report success (template below).
    - **exit 0 with `moved=no`** → the worker started but the tab move failed, so it
      is running as a split in *this* session's tab — tell the user it's here, not in
      a new tab.
    - **After a successful launch (either `moved=` value), if `OFFER_DEFAULT=yes`**
      (the picker path, user chose to save): `bash "$REG" default set "<agent>"`
      (the `agent=` value) to write the repo's committed default. Mention it, and
      that it's an uncommitted change to `.claude/work-system-agent` to commit when
      ready. Skip on any non-zero launch (don't persist a default that didn't run).
    - **exit 2** → unknown/invalid selector. Tell the user and re-offer the picker.
    - **exit 3** → the chosen agent is unavailable (stdout `unavailable=<name>` +
      `note=<hint>`). Report it verbatim (e.g. "codex not ready — run: codex login")
      and re-offer the picker or another selector. Nothing was spawned.
    - **other non-zero exit** → the helper could not automate (herdr/python3 missing,
      broken socket, or no pane id). Show the manual block (b).

    Success report (fill `Tab` from the helper's `tab=` line, `Agent` from `agent=`):
    ```
    Worktree created and launched in herdr!

    Tab:      <LABEL>   (workspace <HERDR_WORKSPACE_ID>, opened in the background)
    Agent:    <cli:model>
    Location: .claude/worktrees/<task-name>
    Branch:   task/<task-name>

    The new tab is already running the worker. Switch to it to work there.
    ```

    **b) Outside herdr — manual instructions.** Resolve the selector to the exact
    launch command (registry-driven — do not hand-write it per CLI):

    ```sh
    bash "$REG" resolve "$SELECTOR" --session "<task-name>"
    ```

    Take the `argv=` lines (in order) as the command words. **Shell-quote each
    word** as you render the command — do NOT just space-join the raw values: for
    codex/grok the whole bootstrap prompt is ONE `argv=` word containing spaces, so
    without quotes the shell would split it into separate arguments and the CLI
    would mangle/reject the prompt. Display this block — it is *not* a command to
    execute:
    ```
    Worktree created!

    Location: .claude/worktrees/<task-name>
    Branch:   task/<task-name>
    Agent:    <cli:model from the `name=` line>
    Task file: TASK.md (copied into the worktree)

    👉 To start working there, open a SEPARATE terminal (not this Claude
       session — this session stays in the main repo) and run:

         cd .claude/worktrees/<task-name>
         <the argv= words, each shell-quoted — e.g. codex -m gpt-5.6-sol 'Read TASK.md …'>
    ```
    For a **claude** worker the command is `claude --model <m> -n "<task-name>"
    "/continue"` — `-n` names the session (shown in `/resume`), `/continue` runs the
    resume flow (load TASK.md, commits, progress). For **codex/grok** it is
    `codex -m <model> '<bootstrap prompt>'` (they have no work-system skills, so the
    prompt tells them to read TASK.md and drive to a PR). Do **not** execute the `cd`
    yourself — it is for the user's new terminal. If `resolve` exits non-zero
    (2 unknown / 3 unavailable), surface that instead and re-offer the picker.

    **If `OFFER_DEFAULT=yes`** (the picker path): do **not** auto-persist here —
    unlike the herdr path, this block only *prints* a command for the user to run
    in a separate terminal, so no launch is confirmed, and the rule is "persist a
    default only after a worker actually started." Instead tell the user to confirm
    once the worker is up, then **you** (this main-repo session) run
    `bash "$REG" default set "<name>"` (the resolved `name=`) to save it — run it
    here, not in the user's terminal (where `$REG` is undefined and the cwd would be
    the worktree). It writes the committed `.claude/work-system-agent` in the main
    repo; mention it's an uncommitted change to commit when ready.

## Remember

- Each worktree is an isolated workspace
- Multiple Claude instances can work on different tasks simultaneously
- The main repo stays clean on the main branch
- Use `/close` after PR is merged to clean up
