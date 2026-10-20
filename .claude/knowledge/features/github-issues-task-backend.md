---
title: "GitHub Issues Task Backend (task-store.py)"
createdAt: 2026-10-19
updatedAt: 2026-10-19
createdFrom: "branch: task/github-issues-task-backend"
pluginVersion: 1.25.0
prime: false
---

# GitHub Issues Task Backend

work-system's backlog is GitHub Issues by default; local `tasks/*.md` files are
an explicit opt-in and the legacy state a later migration (follow-up task
`migrate-tasks-to-github-issues`) converts; until then `auto` stays local
while files exist. Every
skill reaches the backlog through `scripts/task-store.py` (`backend`, `list`,
`get`, `create`, `close`, `ref`) — no skill names `tasks/` or calls `gh issue` for the
backlog. That follows the prose-drift lesson: stateful logic in a tested script,
not in SKILL.md.

## Decisions and why

- **`tasks.backend = auto` resolves deterministically.** Task files in
  `tasks/` (not `ROADMAP.md`, `_index.md`, `README.md`) → local, with a
  hint on leaving it; else a github.com origin with gh installed → github; else
  local. A repo already holding local tasks must not silently start a second
  backlog in issues, so the files win until a migration removes them.
- **No silent fallback to local.** An installed gh that fails `gh auth status`
  is exit 4, under `auto` too. Only an *absent* gh resolves `auto` to local.
  The spec's literal "else local" would flip a GitHub repo to an empty local
  backlog the day a token expires, and `/define` would then write a file the
  team never sees — the split the rule exists to prevent.
- **Task identity is a body marker** `<!-- work-system task: <slug> -->` plus
  the label `work-system:task`. The label keeps ordinary bug issues out; the
  marker keeps the branch `task/<slug>` stable when someone retitles the issue.
  An issue with the label but no marker answers to the slug of its title, so a
  hand-made task issue still works. The marker capture is restricted to
  name characters: an issue body is third-party text and the name flows into
  `key=value` output that skills paste into commands.
- **Lookup by name prefers the open issue**; two open issues with one name is
  exit 5 (pass `#N`); with none open, the newest closed one is returned so a
  repeated `/close` reports `already-closed` instead of "no such task".
  Open issues are listed whole (a list that hits gh's 1000 cap is an error,
  never a silently shorter backlog); closed ones are narrowed by a body search
  first, because closed history grows without bound. The **last** marker
  counts — create appends it, so a quoted marker in the body cannot rename the
  task. Only `#N` is an issue ref; a bare number is a task name. A failing
  `gh issue view` is exit 1, not "no such task" — `/close` reads 3 as nothing
  to close and would leave the issue open.
- **`/close` closes the recorded issue, not a name match.** It reads `#N` from
  TASK.md's `issue:` line before the worktree goes; a name lookup prefers any
  open issue and would close a newer one that reuses the name. `task-store.py
  ref` owns that read (format written and parsed in one script; local backend
  ignores the line): `pinned=no` — a gone worktree on GitHub — means the
  matched issue is shown and confirmed before it is closed, even on an
  auto-accepted delegated close.
- **`gh auth status --active`**: without `--active` gh exits 1 when any
  account on the host has a bad token, though the active one works (gh docs).
- **`auto` counts every task file, `list` only plain names.** A file named
  `$(…).md` is never handed to a skill, but it still keeps the repo local —
  otherwise a backlog of oddly named files would silently flip to GitHub.
- **`/kickoff` copies, never links.** `get --write` renders title, an
  `issue: #N` line and the body (marker stripped) into TASK.md.
- **Local `close` execs `archive-task.sh` unchanged** and passes its output
  through, so `/close`'s commit/push flow keeps reading the same keys.
- **No caching layer.** Every call pays one `gh auth status` round trip.

## Surfaces that cannot use the network

The `[ws]` statusline (and the herdr tab glyphs fed by its `states` mode) never
calls gh for issues. It counts local task files plus every `task/<name>` lane
without one, so on the GitHub backend it shows the tasks in flight and leaves
not-started issues uncounted. `close-request.sh` asks the store whether a gone
lane's task is still open; an unreadable backlog counts as a record, because a
gone lane is only ever `ask`.

## Residuals

`paths.tasks_dir` is not read: `archive-task.sh` and the statusline hardcode
`tasks/`, so the store does too (wiring it is `wire-work-system-paths-to-settings`);
a follow-up the Manager files on the GitHub backend is an issue, so it is Ask,
never written into `tasks/` (one file would pin `auto` to local); pr-flow `/open` does not add `Closes #N`; no status labels,
Projects board or two-way TASK.md sync; github.com only (no GHES host).

## Tests

`scripts/test_task_store.py`: the resolution matrix and the GitHub backend end
to end through a stub `gh` on a PATH that holds only the tools the store needs
(so "gh absent" is real, not simulated).
