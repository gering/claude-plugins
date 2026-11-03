---
title: "GitHub Issues Task Backend (task-store.py)"
createdAt: 2026-10-19
updatedAt: 2026-11-02
createdFrom: "branch: task/github-issues-task-backend"
pluginVersion: 1.31.0
prime: false
---

# GitHub Issues Task Backend

work-system's backlog is GitHub Issues by default; local `tasks/*.md` files are
an explicit opt-in and the legacy state `/work-system:migrate` (`task-store.py migrate`)
converts; until then `auto` stays local while files exist. Every
skill reaches the backlog through `scripts/task-store.py` (`backend`, `list`,
`get`, `create`, `close`, `ref`, `migrate`) — no skill names `tasks/` or calls `gh issue` for the
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
- **Task identity is a body marker** `<!-- work-system task: <slug> -->`;
  *which* issues are tasks is the `[tasks]` selection (below; by default the
  label `work-system:task`, which keeps ordinary bug issues out). The marker
  keeps the branch `task/<slug>` stable when someone retitles the issue. An
  issue without one answers to the slug of its title, so a hand-made task
  issue still works. The marker capture is restricted to
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

## Issue selection (`[tasks]` select / include / exclude / gate labels)

- **One predicate, `unselected()`**, used by `list`, `get`, `create`'s name
  check and `close`. The `label` mode keeps the `gh --label` filter, but
  `all-open` and the label lists are filtered client-side, because several
  `--label` flags AND together and include/exclude need OR and NOT. Exclude wins,
  even over the task label. The defaults (`label`, empty lists) are the old
  behavior.
- **A marker-less issue is a task under `all-open`, named by its title slug.**
  `#N` could not be a branch name. A retitle renames the task, so the lane's
  `issue: #N` pin in TASK.md, not the name, is what `/close` closes. Nothing ever
  adds a marker to someone's issue unasked. Umlauts are spelled out (ä→ae), other
  accents dropped, because German titles would otherwise slug to `m-ll-…`.
- **A closed marker-less issue is not found by name.** The closed-issue search
  is by marker. A re-close goes through the pin.
- **`close` looks up by the mode and include_labels, ignoring exclude** — except a
  pinned `#N` whose name equals `--name`, which counts whatever the filter says. The `#N`
  is TASK.md's pin or the issue the user confirmed, and `/close` reads exit 3 as
  "nothing to close": an exclude label added mid-lane must not leave the shipped
  issue open. The mode still counts (in label mode an unlabelled `#N` is no
  task), and the identity is checked: with `--name`, a mismatch (another task,
  or a marker-less issue retitled since kickoff) is exit 5, which `/close`
  turns into a question, then closes the confirmed `#N` without `--name` (in
  label mode an unlabelled `#N` is then exit 3: the user closes it by hand). Review
  rounds tried two looser variants first: a slug-match exemption (a retitle
  plus an exclude label fell to exit 3) and ignoring the mode for `#N` (any bug
  issue became closable). What stayed: exclude never applies to close (include
  does: round 5 showed that without it, under `all-open`, a stranger's body
  marker answers to a lane's name), and the mode
  is waived only for a `#N` whose name equals `--name` (the lane's own issue
  after a switch back to label mode); a `#N` answering to another name is exit
  5 (ask), and the user's pick then closes without `--name`.
- **Every lifecycle lookup uses that identity view.** `get --identity` is it,
  for `close-request.sh`'s "is the task still recorded", `/close`'s unpinned
  confirmation, `/continue`'s and `/status`'s task text and `/adopt`'s
  name check. A filtered lookup there read an exclude label added
  mid-lane as "no such task" (exit 3 = nothing to close / not a lane), the
  exact case close itself was made immune to.
- **A body marker is identity, never selection.** Round 1 let a marked issue
  bypass `include_labels` so `/define`'s tasks stay visible; round 2 showed any
  issue author can write a marker. Instead `create` and `migrate` give a new
  task issue the first include label (created when missing; if that label is
  also a gate label, every new task is gated, and `/adopt` asks like `/kickoff`). `create` checks name clashes against
  every open issue the mode lists (also excluded or not-included ones, naming
  the `#N`; in `label` mode unlabelled issues are not scanned) and refuses with exit 2
  (config, not a taken name) when `exclude_labels` holds the task label.
- **`all-open` puts strangers' text into context.** Any reporter's title shows
  in `/list` and their body becomes TASK.md, and a marker in their body can
  claim a task name (by-name lookups then hit exit 5). Kickoff already treats the body as
  a description, never authorization; there is no author-trust filter
  (`gh issue list --json` has no author association) — a residual the README
  names.
- **Residuals:** a gone lane's by-name lookup still applies the mode, so a
  marker-less lane started under `all-open` and looked up after a switch back
  to `label` is "not a lane" for the Manager (the issue stays open, nothing is
  torn down unasked). The 1.31 slug change (umlauts spelled out) renames a
  pre-1.31 marker-less lane with a non-ASCII title; its `/close` meets exit 5
  and asks.
- **Gate labels are enforced in the script, not just the prose.** `get --write`
  is the kickoff copy and refuses with exit 7 without `--allow-gated`. `get`
  prints `gate=` so `/kickoff` stops before it creates the worktree. The prose
  forbids taking the go from a mandate, TASK.md, an issue or a message. A script
  cannot tell who said yes, but the copy cannot happen silently.
- **`migrate` under `all-open`** also treats unlabelled open issues as names
  already taken (from the search route; the REST index only sees labelled
  ones) — a marked unlabelled issue there is a skip (its marker blocks the
  name, it is never a resume target: any author can write one), never a
  duplicate create, and a resume onto an issue the filter hides is a skip; an
  adopt onto a name another open issue answers to is a skip too. Every adopt, in any mode, ignores the secret holdback because nothing
  from the file is published, and never adopts an excluded issue; `publish=`
  counts creates only.
- **The 1000-issue cap is counted before include/exclude** (gh cannot apply
  them), so an `all-open` repo with 1000+ open issues fails loudly instead of
  listing a cut set — a residual, the target repos have ~50.
- **Statusline is unaffected by design:** on GitHub it counts lanes, never
  issues, so there is no filter to apply.

## Migration (`task-store.py migrate`)

- **Identity is (marker, archived label)**, not the marker alone: an open and
  an archived task may share a name. Open name lookup still prefers the open
  issue, so the closed archived twin never shadows it.
- **Resumable by construction:** the issue is written before the file moves,
  and a written issue is found again by its marker (`resume`), so a create
  whose reply was lost, a rate limit (exit 6) or a crash never duplicates.
- **The confirmation is a plan hash.** The dry run prints `confirm=<token>`
  over repo, visibility and every item (action, issue, file hash); `--apply`
  refuses any other token. A script cannot tell who confirmed, but it can make
  sure the run is exactly the plan the user saw; the skill owns "only the
  user's answer counts".
- **Adopt, don't duplicate, don't overwrite:** a `Tracking issue: [owner/repo]#N`
  line makes the existing issue the task by adding only the label and the marker.
  The issue is someone else's text (a "DEFINITION ONLY" header, Trello links), so
  it stays as written; until 1.31 it was replaced by the file with the old text in
  a `<details>` block. A file that says more than the issue is flagged `differs=`
  and named in the confirmation: the user copies by hand or drops the tracking
  line. A closed tracking issue for an open task is a skip.
- **The secret scan reports pattern + line, never the value.** Local paths
  count only for public repos; `.local` etc. followed by `.ext` is a file name.
- **The backend switch is a line edit, not `settings.py set`:** the settings
  plugin is optional, cannot find an installed work-system's schema yet, and
  rewrites the file without comments. The edit is re-parsed and must differ
  from the old config in that one key, else it fails asking for a hand edit.
- **Never commits:** a tracked `tasks/` is reported as `commit_needed=`.
- **Resume checks state and shows content drift:** a closed issue for an open
  file is a skip. A marker issue whose body no longer contains the file's text
  is still resumed but flagged `differs=` — the review wanted a skip, but the
  first real repo tried had 24 hand-curated issues newer than their
  files, so a skip would block the common case; the file moves, never deleted,
  so nothing is lost. Two files naming one tracking issue adopt neither.
  Symlinks are never read.
- **The migrate index is REST, not `gh issue list --label`:** gh routes a
  label filter through the search API (verified with `GH_DEBUG=api`), which caps
  at 1000 and lags behind a create — exactly what the no-duplicate resume relies
  on. `gh api --paginate repos/<r>/issues?labels=…&state=all` is consistent.
  (The plain `list`/`get` path still uses the search route; a residual.)
- **Archived issues are history:** `list`/`get` ignore open issues carrying
  `work-system:archived` (a run stopped between create and close leaves one),
  and an archived file never adopts its open twin's issue.
- **Partial runs:** while held-back files remain, `auto` stays local and the
  migrated tasks are not listed — the cost of never splitting the backlog.

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
