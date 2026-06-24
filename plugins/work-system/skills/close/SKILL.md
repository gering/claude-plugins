---
name: close
description: |
  Cleans up a completed task: verifies PR merged, removes worktree,
  deletes branch, archives the task file.
  Trigger: "close this task", "task cleanup", "task is done".
user_invocable: true
---

# Close Completed Task

> Clean up after a task is completed: verify merge, remove worktree, delete branch and task file

## Critical: never `cd` between repo and worktree

This skill may run from the main repo *or* from inside the worktree being deleted. Bash CWD persists between tool calls, so changing directory mid-flow either traps the session in the worktree (when run from main) or leaves it pointing at a deleted path (when run from inside).

Rules:
- ❌ Do not `cd <worktree>` or `cd <main-repo>` during this skill.
- ✅ All operations against either tree go through explicit paths: `git -C <main-repo-path> …`, `git -C <worktree-path> …`, `bash "${CLAUDE_PLUGIN_ROOT}/scripts/archive-task.sh" archive <main-repo-path> …`, etc.
- After deletion, the session's CWD may already be in a now-removed directory — that's the user's problem to fix (a new `cd` in their terminal), not something this skill should "repair" mid-run.

## Instructions

1. **Identify the task, its branch, and its merge state** — via the shared helper:
   - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/task-status.sh" assess "$ARGUMENTS"`
     (`$ARGUMENTS` is the optional task name; empty when run from inside the worktree).
   - **If `on_main=yes` and `task_name` is empty**: run `/list`, ask which task to close, then
     re-run `assess "<chosen-name>"`.
   - **If `detached=yes`** (detached HEAD, no name given): there's no task branch to close — ask
     for the task name explicitly and re-run.
   - **If `branch_exists=no`** (run by name and no real branch matched — resolution is exact-only,
     so `task_branch` is just the `task/<name>` convention): there is nothing resolved to close.
     Treat per step 2's verdict (likely NOT_STARTED). If the task lives on an `/adopt`'d branch
     that kept a non-`task/` name (e.g. `feature/x`), it can't be resolved by name from the main
     repo (the helper strips known prefixes, then matches only `task/<name>`/`<name>`) — run
     `/close` from **inside its worktree**, where resolution uses the current branch.
   - Read the fields: `<task-branch>` = `task_branch` (the resolved real ref — the current branch
     in a worktree, or an exact `task/<name>` match when resolved by name), `<task-name>` =
     `task_name`, `<main-branch>` = `main_branch`, plus `verdict`, `confidence`, `pr_state`,
     `pr_number`, `branch_merged`.
   - **Wherever the steps below write `task/<task-name>`, use the resolved `<task-branch>`** — so
     an adopted branch that kept its original name is closed correctly, not orphaned.

2. **Verify the task is merged** — the safety gate; never skip it silently. Only a **merged PR**
   confirms a merge: topology can't tell a real merge from a never-committed branch sitting at
   main, and a squash/rebase merge rewrites SHAs — so the helper never reports `branch_merged=yes`,
   and `confidence=confirmed` means exactly `pr_state=MERGED`.
   - **Merge confirmed** (`verdict=COMPLETED` with `confidence=confirmed`, i.e. `pr_state=MERGED`):
     show the evidence (`PR #<pr_number>`) and continue.
   - **Not confirmed** (anything else — open/closed/no PR, `branch_merged=unknown`/`no`/`na`, `gh`
     unavailable so `pr_state=nogh`, OR any `confidence=likely` verdict including a freshly
     kicked-off branch still sitting at main): **warn** what is and isn't known — e.g. "merge
     unconfirmed: no merged PR found (may be squash/rebase-merged, or `gh` unavailable)" — and
     **ask for confirmation before any cleanup**. Never let the worktree removal (step 7) or
     branch deletion (step 8) proceed on an unconfirmed merge without it.

3. **Main branch**: `<main-branch>` was already resolved by the helper in step 1 — reuse it; do
   not re-detect.

4. **Get worktree info**:
   - Resolve the main repo path robustly (handles paths with spaces — don't hand-parse
     `git worktree list`): `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path` →
     `<main-repo-path>`
   - Run: `git worktree list`
   - Find the worktree for this task (match by its branch `<task-branch>`)
   - Worktree is typically at `<main-repo-path>/.claude/worktrees/<task-name>` (but verify from
     `git worktree list` output)

5. **Sync local main with remote** (fast-forward check) — only when an `origin` remote exists:
   - **If there is no `origin` remote** (`git remote get-url origin` fails — a purely local repo,
     where step 2 may have confirmed a local-only ancestor merge): skip this step, there is
     nothing to fetch.
   - When the merge landed via a GitHub PR, `origin/<main-branch>` is ahead of local
     `<main-branch>` until pulled. Syncing now avoids a confusing "not fully merged" error from
     `git branch -d` in step 8 and leaves the workspace ready for the next task.
   - Operate against the main repo path identified in step 4 (not the task worktree):
     - Fetch: `git -C <main-repo-path> fetch origin <main-branch> --quiet`
     - Behind count: `git -C <main-repo-path> rev-list --count <main-branch>..origin/<main-branch>`
     - Ahead count: `git -C <main-repo-path> rev-list --count origin/<main-branch>..<main-branch>`
   - **Behind == 0**: already in sync — proceed silently.
   - **Behind > 0 AND Ahead == 0**: fast-forward cleanly:
     `git -C <main-repo-path> merge --ff-only origin/<main-branch>`
     Log: "main fast-forwarded (<N> commits pulled from origin)".
   - **Behind > 0 AND Ahead > 0** (divergence — local main has unpushed commits): do NOT auto-pull. Surface a warning with both counts and the suggested command (`git pull --rebase`), then continue cleanup — divergence is a separate concern from closing the task.

6. **Handle current location**:
   - If currently in the worktree being deleted:
     - Warn: "You're in the worktree that will be deleted!"
     - Show: "After cleanup, switch to: <main-repo-path>"

7. **Remove worktree** (if exists) — all commands use explicit paths, never `cd`:
   - **herdr — capture the task's tab BEFORE removal:** if `[ "${HERDR_ENV:-}" = "1" ]`
     **and** `command -v herdr` succeeds, look up the worktree's herdr tab id *now* —
     after removal its cwd points at a deleted path and the match is impossible:
     `WT_TAB=$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-teardown.sh" worktree-tab "$HERDR_WORKSPACE_ID" "<worktree-path>")`
     The helper re-checks every herdr prerequisite (socket, `python3`, workspace id) and
     exits non-zero printing nothing if it cannot match. An empty `WT_TAB` **outside**
     herdr means "nothing to tear down" — skip step 12 and close exactly as today. An
     empty `WT_TAB` **while** `HERDR_ENV=1` means the tab couldn't be located by cwd
     (e.g. the pane's cwd drifted off the worktree root): don't pretend it was handled —
     add a line to the final summary like "herdr: couldn't locate this task's tab
     automatically — close it by hand if it's still open," then skip step 12.
     Then capture **this session's own tab** so self-close is decided by pane id, not
     the possibly-empty `$HERDR_TAB_ID`:
     `OWN_TAB=$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-teardown.sh" own-tab "$HERDR_WORKSPACE_ID" "$HERDR_PANE_ID")`
     Classify: `SELF=yes` when `WT_TAB` and `OWN_TAB` are both non-empty and **equal**
     (you are *inside* the tab being removed); `SELF=no` when both are non-empty and
     differ. If `OWN_TAB` is empty (this session's tab couldn't be resolved), do **not**
     guess a scenario — step 12 will skip the automatic teardown and just name the tab
     for the user; guessing risks `close-tab` killing the live session's own tab
     mid-turn. Reading ids removes nothing — the teardown itself is step 12, after cleanup.
   - First check for untracked/modified files: `git -C <worktree-path> status --short`
   - If the only difference is `TASK.md` (untracked, copied by kickoff), use `--force` directly:
     `git -C <main-repo-path> worktree remove <worktree-path> --force`
   - Otherwise try: `git -C <main-repo-path> worktree remove <worktree-path>`
   - If fails (uncommitted changes beyond TASK.md):
     - Show full status: `git -C <worktree-path> status`
     - Ask: "Force remove? (uncommitted changes will be lost)"
     - If yes: `git -C <main-repo-path> worktree remove <worktree-path> --force`

8. **Delete local branch** (only if it exists *locally*):
   - If `branch_scope` is not `local` (from step 1 — the task was resolved remote-only, or no
     branch exists yet): skip this step, there is no local branch to delete. (Gate on
     `branch_scope`, **not** `branch_exists`: the latter is `yes` for a remote-only resolution
     too, so `git branch -D` would fail with "branch not found".)
   - If merge was confirmed in step 2: use `git branch -D <task-branch>` directly
     (the `-d` safety check produces false positives with GitHub's rebase-merge strategy,
     where commits are rewritten with new SHAs — the real safety gate is step 2's merge check)
   - If merge was not confirmed (manual close): try `git branch -d <task-branch>` first
     - If fails (not fully merged): ask "Force delete branch?"
     - If yes: `git branch -D <task-branch>`

9. **Delete remote branch** (only if there is an `origin` remote):
   - **If `git remote get-url origin` fails** (purely local repo): skip this step — there is no
     remote branch to delete (mirrors step 5's guard).
   - Run: `git ls-remote --heads origin <task-branch>`
   - **Returns nothing** → the remote branch is already gone (e.g. the repo auto-deletes head
     branches on merge); skip this step — nothing to delete, and `--delete` on a missing ref
     would error.
   - **Exists AND merge was confirmed in step 2** → delete directly, no prompt:
     `git push origin --delete <task-branch>` (the merge already integrated the work).
   - **Exists, merge not confirmed** (manual close) → ask "Delete remote branch too?" first; only
     push the delete on confirmation.

10. **Archive the task file** (use the main-repo path from step 4 — do not `cd`):
    Move it into `tasks/archive/` with a closed-stamp instead of deleting it, so the
    finished-task context (goal, acceptance criteria, shipping PR) survives — `tasks/` is
    untracked by design, so a deleted task would otherwise be gone for good. The helper
    builds the stamp, suffixes the filename on a name collision (never clobbers), appends a
    one-line `tasks/archive/_index.md` entry, and reports whether the archive is committable.
    - **Merge confirmed** (step 2, `pr_number` is set) — fetch the merge commit for the stamp
      (best-effort; omit `--sha` when empty), then archive with the PR:
      ```sh
      SHA=$(gh pr view <pr_number> --json mergeCommit --jq '.mergeCommit.oid' 2>/dev/null)
      bash "${CLAUDE_PLUGIN_ROOT}/scripts/archive-task.sh" archive <main-repo-path> <task-name> <task-branch> --pr <pr_number> --sha "$SHA"
      ```
    - **Manual close** (no merged PR) — archive without `--pr`; the stamp records
      "closed manually (no merged PR)":
      ```sh
      bash "${CLAUDE_PLUGIN_ROOT}/scripts/archive-task.sh" archive <main-repo-path> <task-name> <task-branch>
      ```
    - **Helper exits 3** ("no task file"): nothing to archive (the file was never created) —
      note it in the summary and continue; not a failure.
    - Read the helper's `key=value` output (`archived_path`, `collision`, `tracked`):
      - **`tracked=yes`** (archive is *not* gitignored — `tasks/` is tracked in this project):
        the move is a committable change. Show `git -C <main-repo-path> status --short tasks/`
        and ask whether to commit the archive. If yes, stage only the archive (never a blanket
        `tasks/`, which would sweep in pending task files):
        ```sh
        git -C <main-repo-path> add tasks/archive/
        git -C <main-repo-path> add -A -- "tasks/<task-name>.md" 2>/dev/null || true
        git -C <main-repo-path> commit -m "Archive task <task-name>"
        ```
      - **`tracked=no`** (archive is local-only, mirroring a deliberately-untracked `tasks/`):
        no commit — just report.
    - Report: "Task file archived to `<archived_path>`" (add "(name existed — suffixed)" when
      `collision=yes`).

11. **Final summary**:
    ```
    Task '<task-name>' closed!

    Cleaned up:
    - Worktree removed
    - Local branch deleted
    - Remote branch deleted (if applicable)
    - Task file archived → tasks/archive/<name>.md
    - main synced with origin (<N> commits pulled)     [if fast-forward happened]
    - herdr tab closed (if run inside a herdr session — see step 12)

    Next: /kickoff for next task
    ```

12. **Tear down the herdr tab** — only when step 7 captured a non-empty `WT_TAB`
    (you are in a herdr session and the task had a tab). Outside herdr, or when no
    tab matched, `/close` is already done — stop here. **Only proceed if the cleanup
    above (steps 7–10) actually completed**: if any step stopped for a confirmation
    you haven't resolved, or you aborted, do **not** run any teardown below
    (*especially* never `self-exit`) — leave the session alive so the user can act.
    Route **every** herdr call through the shared helper; never inline `herdr …`.

    **If `OWN_TAB` was empty in step 7** (this session's tab couldn't be resolved):
    skip the automatic close and just report "herdr: close the task's tab
    (`$WT_TAB`) yourself" — never run `close-tab`/`self-exit` on a guess (it could
    kill the live session's own tab mid-turn).

    **Scenario A — `SELF=no`** (`WT_TAB` ≠ `OWN_TAB`; you're in a *different* tab,
    normally the main session): close the worktree tab directly — a different tab, so
    no self-kill:
    ```sh
    bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-teardown.sh" close-tab "$WT_TAB"
    ```
    Report: "herdr: closed the task's tab (`$WT_TAB`)." Done. (The idle task-agent
    in that tab dies with it — fine, the task is merged and cleaned up.)

    **Scenario B — `SELF=yes`** (`WT_TAB` == `OWN_TAB`; `/close` was run from *inside*
    the worktree tab): Claude cannot close its own tab, only **exit cleanly**; the
    plugin's `SessionEnd` hook (`hooks/hooks.json` → `herdr-teardown.sh
    on-session-end`) closes the tab on that exit, but only because the marker below
    opts this session in. Order matters — run this **after** the step-11 summary is
    printed (nothing after the exit runs):
    1. Resolve the main tab, **excluding this dying tab** so the fallback never
       focuses it:
       ```sh
       MAIN_TAB=$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-teardown.sh" main-tab "$HERDR_WORKSPACE_ID" "<main-repo-path>" "$OWN_TAB")
       ```
    2. Arm the self-close marker (records **this** tab to close on the clean exit):
       ```sh
       bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-teardown.sh" arm-self-close "$WT_TAB"
       ```
    3. Focus the main tab (skip if `MAIN_TAB` is empty):
       ```sh
       bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-teardown.sh" focus-tab "$MAIN_TAB"
       ```
    4. Trigger the clean exit — **B-inject (default):** as the **very last action**
       (after the step-11 summary is printed), arm a detached injector that exits
       this session once the turn ends:
       ```sh
       bash "${CLAUDE_PLUGIN_ROOT}/scripts/herdr-teardown.sh" self-exit "$HERDR_PANE_ID" "$HERDR_WORKSPACE_ID"
       ```
       `self-exit` returns immediately and the detached injector **polls until this
       session goes idle** (the turn ends) before injecting — so the `/exit` lands on
       an **idle** prompt, the state in which it exits cleanly (verified live), never
       mid-turn. (`herdr pane run "/exit"` does nothing to Claude's TUI and `ctrl+d`
       doesn't exit either — only `send-text "/exit"` + `Return` onto an idle prompt
       works, which is what the injector does.) Claude's clean exit **auto-closes**
       its (root-pane) tab; the marker + `SessionEnd` hook from step 2 are the backup
       for sessions whose tab does not auto-close (e.g. Claude launched inside a
       shell pane).
       - **B-hook (fallback):** if `self-exit` can't run (`herdr` injection
         unavailable / non-zero exit), do **not** inject — tell the user: "Cleanup
         done, main tab focused — press **Ctrl+D** (or type `/exit`) to close this
         finished tab." The same auto-close + hook tear it down on that manual clean
         exit. Never defer a tab-close to a SIGHUP/idle kill — that risks a corrupt
         transcript / broken `--resume`.

## Safety

- Always verify PR is merged before cleanup
- Warn about uncommitted changes
- Never force-delete without confirmation
- Check project CLAUDE.md and rules for project-specific cleanup steps
