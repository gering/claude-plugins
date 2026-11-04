---
title: "Manager Worker Status and Status-Change Events"
createdAt: 2026-11-03
updatedAt: 2026-11-04
createdFrom: "branch: task/add-manager-status-and-events"
updatedFrom: "branch: task/add-manager-status-and-events"
pluginVersion: 1.9.0
prime: false
---

# Manager Worker Status and Status-Change Events

`scripts/workers.sh` (logic in `workers.py`) is the status slice of
`/work-system:manager`: `list`, `screen`, `question` (read-only) and `events`
(one-shot, deduplicated). Dispatch/collect are a later slice
(`add-manager-watch-loop`).

## Decisions

- **No second classifier, no copies.** `workers.sh` follows the `sessions.sh`
  pattern: it sources `herdr-agent.sh` and exports `$HERDR_MATCH_PRELUDE`,
  `$HERDR_NAME_PRELUDE` and `$HERDR_COMPOSER_PY`; `workers.py` `exec`s the
  preludes and dies with exit 3 when run without the wrapper. Roles come from
  `classify_cwd` against the main root that `main-repo-path.sh` finds for each
  agent cwd (the prototype's `.git`-dir check was a second classifier).
- **Read-only by construction.** The only write path is `manager.sh prompt`.
  `test_workers.py` proves it through the fake herdr's argv log across every
  run (`agent send-keys|prompt|send-text`, `pane send-*` never appear). There is
  no `answer` subcommand: questions are handed up to the human, never answered.
- **Own-pane guard lives in `manager.sh prompt`**, not in `events`: a Manager
  running `events` would resolve itself as the lane's Manager. The root fix
  (`reason=manager-is-self`) protects every caller; `test_manager.py` now calls
  `prompt` from a worker pane because its fixture's caller pane IS the Manager.
- **Dedupe identity.** `blocked` = pane + session UUID (an `/exit` successor in
  the same pane starts fresh; no UUID → no event). `ready-for-merge` = lane
  realpath + head (a new reviewed head re-arms). A busy worker keeps its ready
  record instead of resetting it, so a working spell does not re-fire.
- **ready-for-merge without a run record** (Robert, 2026-11-03): open PR from
  `task-status.sh assess`, then `gh pr view`: same-repo, non-draft, head == lane
  HEAD (pushed), `MERGEABLE`, no `CHANGES_REQUESTED`, at least one check and all
  green (zero checks is NOT green: right after a push CI may not have registered),
  plus `mandate.sh covered`, and `mergeStateStatus` CLEAN/HAS_HOOKS (round 2:
  `mergeable` ignores branch protection, so a fast green lint before the
  required check registered read as ready; BLOCKED/BEHIND now say no). One
  rule, `ready_for_merge()`, behind both `events` and `workers.sh ready` — the
  skill calls the latter instead of deriving it.
  Tri-state yes/no/unknown: `unknown` (gh blip, lazy `mergeable`, assess's
  `pr_state=none`, which is also what a failing gh looks like) keeps the record,
  so a transient error never re-arms a delivered event. A `no` keeps the head
  it last fired for (round 3: a re-run check going red then green re-fired the
  same head), and a fired head with nothing left to deliver is not re-checked
  (saves the gh chain per tick). Only checked once EVERY agent in the lane
  is idle/done without `verify` (round 3: the first-listed pane decided); round
  4 moved that check into `ready` too (`lane-busy`), so skill and events agree;
  round 5: over every workspace (a `--workspace` run saw only its own panes)
  and every non-self agent in the lane or a sub-directory of it (an agent in
  `<lane>/src` classifies as `other`); `ready` reads only that lane's screens.
  Switch to the run record once it exists.
- **The command never comes from a repo** (Robert): `on-blocked` only from the
  CLI flag or `~/.config/work-system/events.toml` (O_NOFOLLOW, owner == uid, not
  group/world-writable). The schema deliberately has no command key; a repo's
  `.work-system.toml` gets only `events.enabled` and `events.escalate_after`,
  read at the MAIN checkout (`SETTINGS_PROJECT_ROOT`), never the lane's own
  worker-writable copy — otherwise a worker could silence its own events.
- **Dedupe edges found in review:** herdr's `unknown` status is no observation
  (keeps the blocked record); records carry their workspace and are pruned only
  within the run's scope (a `--workspace w2` run must not wipe the `--all`
  timer's w1 records); an empty agent list prunes nothing (herdr repopulating,
  as lanes.sh treats it), and only a record whose pane, session or lane was seen
  gone is pruned (round 4: a role that fell to `other` on a main-repo-path
  timeout, or a missing UUID, deleted the record and re-fired next tick); a
  re-sent ping of an open event is a `retry=` line, not an event; a failing
  hook is retried on at most 3 runs, and its output goes to /dev/null (a captured pipe held by a background notifier ran
  into the 30 s timeout and re-fired); a ping whose reason cannot change
  (`manager-is-self`, non-claude Manager, `bad-pane`, `no-main-repo`, all after
  a unique resolve) is `final`, not retried each tick.
- **A failed read is not an empty screen** (`read_text` returns None): an idle
  row whose screen could not be read gets `verify`, `screen` exits 4, `question`
  exits 4 for a blocked pane and prints `asks=unknown` for an idle one.
  The state file is validated before any delivery: a lost state re-sends every
  event, so unreadable JSON is exit 2, only a missing file starts empty.
- **Dialog options** come from one 1..k run near the bottom; description lines
  between options are allowed (AskUserQuestion), and a question or `[y/n]` line
  below the run voids it (an older list above a later prompt is not the dialog).
- **A typed ping slugs task and path** (round 6): `manager.sh prompt` types the
  sender prefix into the Manager's composer, so `task=` and `worktree=` are
  slugs there (a worktree name with spaces read as a sentence outside the
  bracket); the SendMessage `body` keeps the real names. `ping_text` carries no
  task name at all, so the slug rule lives in one place.
- **Raw cwd for paths, `clean()` for display.** The prototype stored the
  display-cleaned cwd and then used it for git/settings/ping: a whitespace fold
  or truncation pointed them at another (or no) worktree.
- **Ping in the worker's workspace.** `manager.sh resolve` scopes its fallback by
  `$HERDR_WORKSPACE_ID`; `events` sets it to the worker's workspace for the ping
  (the caller may be a timer outside herdr or a Manager elsewhere) and leaves
  `$HERDR_PANE_ID` as the caller's, so the own-pane guard still holds.
- **No loop ships.** herdr 0.9.5 has no status-change hook (`agent wait` only),
  so `events` is a one-shot for a user timer; a `<state>.lock` flock makes an
  overlapping tick print `summary: skipped=locked` instead of double-pinging.
  A herdr failure exits 3/4/5 before the state file is touched.

## Residuals

- Composer may change between `manager.sh prompt`'s draft read and the send
  (herdr has no atomic check-and-prompt), as for every `prompt` caller.
- Flags `asks`/`verify` are screen heuristics; `verify` only says "read it".
- The hook payload carries `last_line` and the cwd (the spec requires them); a
  screen line can hold a secret, so the README tells users to forward it only
  where they would show the screen.
- `ready_for_merge` costs two gh calls per idle lane per tick (assess + view):
  kept, because assess is the agreed source; fold into one `gh pr view` when the
  run record replaces it.
- cron's minimal env: herdr/gh/Python 3.11 must be on the entry's `PATH`.
