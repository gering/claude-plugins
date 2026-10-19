---
title: "Session plugin staleness + /reload-plugins refresh (sessions.sh)"
createdAt: 2026-10-19
updatedAt: 2026-10-20
createdFrom: "branch: task/add-session-plugin-refresh"
updatedFrom: "branch: task/add-session-plugin-refresh"
pluginVersion: 1.9.0
prime: false
---

# Session plugin staleness + `/reload-plugins` refresh

`/work-system:sessions` (`scripts/sessions.sh` → `sessions.py`) answers "which
running herdr sessions still run old plugin code?" and reloads the idle ones.
Verdict rules and exit codes live in the `sessions.py` header; this entry keeps
the *why* and the facts that were verified live.

## Why reload, not restart

The built-in `/reload-plugins` applies installed/updated plugins, skills, agents,
hooks and plugin MCP servers to a running session. Background tasks, monitors and
crons survive, and there is no resume-the-right-session problem. Verified live
(Claude Code 2.1.303, herdr 0.8.2) in a throwaway `spike-` session:
- It needs no `--force` with or without conversation history. `--force` is only for
  a reload that would change MCP/LSP tools (docs).
- It prints one line: `⎿  Reloaded: N plugins · N skills · …`. The model sees it
  in its transcript.
- `herdr agent prompt <pane> "/reload-plugins"` submits it cleanly: the slash
  autocomplete's Enter picks the exact command.

A real restart (Claude Code update, env change) stays out of scope (follow-up).

## Staleness = time + version, never "assumed current"

A session is `stale` when an effective install's `lastUpdated` is after the session
start (or after the last reload *this tool* recorded), or when the install is older
than the marketplace version. A reload cannot fix the second case.
- **Session start** comes from `herdr pane process-info` (the foreground claude:
  pid == the foreground process group) plus `ps -o etime=`, which is locale-free.
  `lstart` would need date parsing.
- **Startup grace (10s).** Claude Code writes a worktree's `local` install record
  during its own startup (observed ~1s after process start). Without the grace,
  every fresh worktree session read `stale`. The grace covers only that record
  (scope `local`, projectPath == the session cwd, `installedAt == lastUpdated`,
  i.e. just created) and only against the process start: any other update in
  that window is still `stale`.
- **Reload record** is keyed by pane *and* claude pid in
  `~/.local/state/work-system/session-reloads.json`. A new session in the same
  pane has a new pid, so an old record never vouches for it. A reload typed by
  hand is not recorded (residual: one harmless extra reload).
- Anything undecidable is `unknown`: no start time, no `lastUpdated`, a
  non-numeric version, a plugin missing from the marketplace, competing records,
  an unreadable `installed_plugins.json` (an empty fallback would read as
  "nothing installed" and therefore `current`).

## Effective install (scope precedence)

The documented order is local > project > user > managed. Sources:
`claude plugin update` scope auto-detect, and the enablement precedence in the
plugins docs. Which record a session *loads* is inferred from that order, not
documented. A record matches when `projectPath` == the session's cwd or, when the
cwd is a linked worktree's top level, the main checkout's root (docs: worktrees
share the main checkout's project installs). A cwd that is only a *subdir* of
a recorded project is `unknown`, not a match. `installed_plugins.json` holds many
per-worktree `local` records, mostly orphans of removed worktrees. The audit
groups them by plugin+scope: the CLI has no `--project` flag, so their uninstall
would have to run from a path that is gone.

## Refresh never interrupts

Per target: `herdr-agent.sh wait --until idle --until done --until blocked` (bounded, so a blocked pane is reported at once), then a
status re-check via `get`, then the composer draft check, then send, then
verification. The draft check is `$HERDR_COMPOSER_PY`, moved from `manager.sh` into
`herdr-agent.sh` so the "never type into a user draft" rule has one home. Both
sessions.sh and `manager.sh prompt` use it; it runs last, right before the send,
after a pid check (a new session in the pane is skipped). Verification: while
the 1000-line read is not full, the pre-send transcript is a prefix of the
later one and the new lines are exactly its continuation; once full, the
last `ANCHOR_LINES` stable pre-send lines are found again instead. Both comparisons use only *stable*
lines (input echoes `❯`, reply starts `⏺`): the first real refresh over 15
sessions reported 3 successful reloads as `unconfirmed`, because Claude Code
redraws transient panels (`✦ Heads up`, `※ recap`) above the composer every
turn and an anchor on those is never found again. The new lines must end in a
`/reload-plugins` echo with a `Reloaded:` line after it. Three review rounds
broke simpler checks: a line count and a pair count read "nothing new" when the
send pushed an old pair out of the window, and a pure content anchor matched a
tail of identical reload pairs. A line asking for `/reload-plugins --force` (looked for one poll past the summary) makes the result
`partial` (not recorded, the line quoted); the record carries the send time, not the time the
summary was seen. A missing summary is
`unconfirmed`, not recorded, and carries the pane's reply line (e.g. a `--force`
notice). The own pane (`$HERDR_PANE_ID`) and non-claude agents are never typed into.
`--all` without `--yes` only lists targets (`confirm=required`, `pane@pid` tokens; `targets=none` when empty) so the skill asks
once; `--all --yes --only <tokens>` then types into exactly those sessions — a pane token alone would let a new claude in that pane through.

Both callers (sessions.py and `manager.sh prompt`) run the composer check as
`python3 -I -c "$HERDR_COMPOSER_PY"`: without `-I` the *invoking* process's cwd
(a worker worktree, say) is on `sys.path`, and a planted `re.py` there would
decide the draft verdict. Stable means flush left (column 0): an
indented `❯` is a menu cursor or displayed content, never an echo. Accepted
residual: when the read window is full and its last stable lines are repeated
`/reload-plugins` echoes, the anchor cannot tell the new echo from the old
ones and the reload reads `unconfirmed`. The rightmost-match search makes
that fail safe (one more harmless reload later), never a false confirmation.

## Testing note

A live "busy" test in the spike failed twice for a benign reason: the user's
hooks blocked the long `sleep`, so the turn ended at once and `wait` correctly
returned `done`. The busy/draft/new-pid paths are covered deterministically by
`test_sessions.py`'s herdr stub, which logs every `agent prompt` call.
