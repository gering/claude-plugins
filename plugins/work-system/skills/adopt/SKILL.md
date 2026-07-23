---
name: adopt
description: |
  Adopts an existing branch: creates a worktree and generates a task
  file from its commits/diff.
  Trigger: "adopt this branch", "track this branch in the work system".
user_invocable: true
---

# Adopt Existing Branch

> Bring an existing branch into the work system — create a worktree and task file for it

## Arguments

- `$ARGUMENTS` — `<branch> [agent-selector]`: optional branch name to adopt, plus an
  optional worker-agent selector (same set as `/kickoff`: `--opus`, `--sol`, `--grok`,
  `--codex`, `--agent <cli[:model]>`, `--pick`). The selector chooses the worker the
  herdr auto-launch (step 13) starts; omit it to use the repo default.

## Critical: never persist a `cd` into the worktree

This skill runs **in the user's main-repo session**. It creates the worktree; the user enters it in a separate terminal/Claude session (see the final step).

The Bash tool persists CWD between calls — a bare `cd .claude/worktrees/<task>` would silently trap the entire session inside the worktree. Rules for every shell command:

- ❌ Never `cd <worktree>` standalone or as `cd <worktree> && …` without a paired `cd` back.
- ✅ Use `git -C <worktree-path> …` for git operations against the worktree.
- ✅ Use absolute paths or paths relative to the main repo for `cp`, `mkdir`, `ln -s`.
- ✅ If a different CWD is genuinely needed, wrap in a subshell: `(cd <worktree-path> && <cmd>)`.

## Instructions

1. **Check current location** (shared helper — robust against paths with spaces and symlinks):
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" linked` → `main` or `linked`.
     If `linked`, this is already a worktree — stop and explain this command should be run from
     the main repo.
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path` → `<main-repo>` — capture
     it for the CWD-drift check in step 11.

2. **Select branch**:
   - If `$ARGUMENTS` provided, use as branch name
   - Otherwise, list available branches:
     - Run: `git branch --list --no-merged | grep -v '^\*'`
     - Exclude the current branch and any `task/*` branches that already have worktrees
     - Show the list and ask the user which branch to adopt
   - Verify the branch exists: `git rev-parse --verify <branch-name>`

3. **Derive task name**:
   - Strip common prefixes (same set as the shared helper's `strip_prefix`): `task/`, `feature/`,
     `fix/`, `bugfix/`, `hotfix/`, `chore/`, `refactor/`
   - Convert to kebab-case if needed
   - Examples:
     - `feature/dark-mode` → `dark-mode`
     - `fix/calendar-date-bug` → `calendar-date-bug`
     - `my-feature` → `my-feature`
   - Show proposed task name and ask for confirmation

4. **Check for conflicts**:
   - Check if `tasks/<task-name>.md` already exists
   - Check if `task/<task-name>` branch already exists
   - Check if worktree path `.claude/worktrees/<task-name>` already exists
   - If any conflict, ask user how to proceed (rename or skip)

5. **Gather task context from branch**:
   - Detect main branch:
     - Run: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
     - If that fails, check if `main` or `master` exists: `git branch --list main master`
   - Get commit history: `git log --oneline <main-branch>..<branch-name>`
   - Get changed files: `git diff --stat <main-branch>...<branch-name>`
   - Get diff summary: `git diff --shortstat <main-branch>...<branch-name>`

6. **Generate task file**:
   - Use the commit messages and changed files to draft a task file
   - Template:
     ```markdown
     # <Title derived from branch name and commits>

     ## Goal
     <Inferred from commit messages>

     ## Context
     Adopted from existing branch `<original-branch-name>`.

     ## Progress
     - [x] <Summary of work already done, based on commits>

     ## Remaining
     - [ ] <Any obvious remaining work, or "Review and finalize">

     ## Relevant Files
     - `<changed-file-1>`
     - `<changed-file-2>`
     ```
   - Show to user for review and allow edits

7. **Create task file**:
   - Write to: `tasks/<task-name>.md`
   - Create `tasks/` directory if it doesn't exist

8. **Rename branch** (optional):
   - Ask: "Rename branch `<original-name>` to `task/<task-name>`? (recommended for consistency)"
   - If yes: `git branch -m <original-name> task/<task-name>`
   - If no: keep original branch name and note it in the task file

9. **Create worktree**:
   - Create parent directory if needed: `mkdir -p .claude/worktrees`
   - Worktree path: `.claude/worktrees/<task-name>`
   - If branch was renamed: `git worktree add .claude/worktrees/<task-name> task/<task-name>`
   - If branch kept original name: `git worktree add .claude/worktrees/<task-name> <original-branch-name>`

10. **Copy files to worktree** (run from main-repo CWD — no `cd`):
    - Copy task file: `cp tasks/<task-name>.md .claude/worktrees/<task-name>/TASK.md`
    - Copy Claude config if present (a fresh worktree has no `.claude/` dir yet, so create it
      first — otherwise the copy silently no-ops):
      `[ -f .claude/settings.json ] && mkdir -p .claude/worktrees/<task-name>/.claude && cp .claude/settings.json .claude/worktrees/<task-name>/.claude/`

11. **Verify CWD is still in the main repo**:
    - Run: `pwd` and compare to the `<main-repo>` path captured by the helper in step 1.
    - If they differ, **stop and report an error**: "Session CWD drifted into the worktree during adopt — investigate which step ran a persistent `cd`." Do not silently continue.

12. **Select the worker agent** — same as `/kickoff` step 12. Turn the selector
    portion of `$ARGUMENTS` (anything after the branch name) into a concrete
    `SELECTOR` for the launch helper, and set `OFFER_DEFAULT` (`no`, flipped to `yes`
    only when the picker asks to save). With
    `REG="${CLAUDE_PLUGIN_ROOT}/scripts/agent-registry.sh"`: an explicit flag
    (`--opus`/`--sol`/`--grok`/`--codex`/`--agent <cli[:model]>`) is used verbatim, no
    default offer; no flag reads the repo default (`SELECTOR="$(bash "$REG" default
    get)"` — announce it before launching when it's a non-claude worker, then launch;
    empty → the picker); `--pick`, or no flag with no default set, runs the picker
    (`bash "$REG" list` → **AskUserQuestion**, plus a "Save as project default?"
    question that sets `OFFER_DEFAULT=yes` only on Yes). **The picker/announce rules are
    identical to `skills/kickoff/SKILL.md` step 12 — follow that one copy, not a
    divergent paraphrase.** The helper (step 13) resolves and validates `SELECTOR`, so
    don't resolve models/availability yourself.

13. **Launch the worktree session** — automate inside herdr, otherwise show the
    manual block. Inside herdr this replaces the old "print manual instructions" final
    step: `/adopt` now opens the task's tab for you, exactly like `/kickoff`.

    Gate (same as kickoff): automate **only** when `[ "${HERDR_ENV:-}" = "1" ]`, a
    non-empty `$HERDR_WORKSPACE_ID`, and both `command -v herdr` and `command -v
    python3` succeed. Otherwise show the manual block (b). The helper re-checks these
    and exits non-zero if it cannot automate, so a broken socket degrades to (b) too.

    **a) Inside herdr — open a named tab that auto-continues.** Derive a short,
    sidebar-friendly `LABEL` from the **resolved task name** (`<task-name>` from step 3,
    which already stripped `task/`/`feature/`/`fix/`… prefixes — so the label stays
    sensible even when step 8 kept the *original* branch name). Drop filler words,
    hard-cap ~32 chars, pass it PLAIN (the helper prefixes the task's state glyph onto
    the tab label). The worktree path is absolute — build it from the `<main-repo>`
    captured in step 1 (never from a drifted CWD):

    ```sh
    WORKTREE="<main-repo>/.claude/worktrees/<task-name>"   # absolute path
    LABEL="<short sidebar label>"
    bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-launch.sh" launch "$LABEL" "$WORKTREE" "$HERDR_WORKSPACE_ID" "$SELECTOR"
    ```

    This is the **same helper and call `/kickoff` uses** — the single source of truth
    for selector resolution, argv-launch, tab-move, and exit codes. **Branch on its
    result exactly as `skills/kickoff/SKILL.md` step 13a describes** (exit 0 `moved=yes`
    → new background tab; `moved=no` → running as a split in *this* tab, relay the
    helper's stderr; the `OFFER_DEFAULT=yes` → `bash "$REG" default set "<agent>"`
    persistence after a successful launch; exit 2 unknown selector → re-offer the
    picker; exit 3 unavailable agent → report `unavailable=`/`note=` verbatim, re-offer;
    other non-zero → relay stderr, then the manual block). Do not re-implement that
    branching here — following one copy keeps adopt and kickoff from drifting.

    Success report (adopt keeps the *adopted* branch name — use `<current-branch-name>`,
    which is `task/<task-name>` only if step 8 renamed it):
    ```
    Branch adopted and launched in herdr!

    Tab:      <LABEL>   (workspace <HERDR_WORKSPACE_ID>, opened in the background)
    Agent:    <cli:model>   (the helper's `agent=` line)
    Worktree: .claude/worktrees/<task-name>
    Branch:   <current-branch-name>

    The new tab is already running the worker. Switch to it to work there.
    ```

    **b) Outside herdr (or on any fallback) — manual instructions.** Resolve the
    selector to the exact launch command (registry-driven — do not hand-write it per
    CLI): `bash "$REG" resolve "$SELECTOR" --session "<task-name>"`. Take the `argv=`
    lines in order and **shell-quote each word** (the codex/grok bootstrap prompt is one
    `argv=` word containing spaces; space-joining raw would split it). Display this block
    — do **not** execute the `cd`:
    ```
    Branch adopted into work system!

    Original branch: <original-branch-name>
    Task file:       tasks/<task-name>.md
    Worktree:        .claude/worktrees/<task-name>
    Branch:          <current-branch-name>
    Commits:         <count> commits ahead of <main-branch>

    👉 To start working there, open a SEPARATE terminal (not this Claude
       session — this session stays in the main repo) and run:

         cd .claude/worktrees/<task-name>
         <the argv= words, each shell-quoted>
    ```
    For a **claude** worker that is `claude --model <m> -n "<task-name>"
    "/work-system:continue"` — `-n` names the session (shown in `/resume`),
    `/work-system:continue` runs the resume flow (load TASK.md, commits, progress).
    Use the plugin-qualified form: a Claude Code built-in `/continue` shadows the bare
    skill. For **codex/grok** it's `codex -m <model> '<bootstrap prompt>'`. Do **not**
    execute the `cd` yourself — it is for the user's new terminal. If `resolve` exits
    non-zero (2 unknown / 3 unavailable), surface that instead and re-offer the picker.
    On the picker's "save default" path, persist only after the user confirms the worker
    is up (see kickoff step 13b) — you (this main-repo session) then run
    `bash "$REG" default set "<name>"`; it writes the committed `.claude/work-system-agent`.

## Remember

- The original branch is preserved — it's either renamed or checked out as-is
- The task file is a best-effort draft from commit history — the user should review it
- After adoption, the standard workflow applies: `/continue`, `/status`, `/close`
