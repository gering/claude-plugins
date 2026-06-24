---
title: "herdr /close Automation"
createdAt: 2026-06-24
updatedAt: 2026-06-24
createdFrom: "branch: task/automate-close-in-herdr"
updatedFrom: "branch: task/automate-close-in-herdr"
pluginVersion: 1.8.2
prime: false
---

# herdr /close Automation

Inside a herdr session, `/close` tears down the finished task's herdr **tab** on
top of its usual worktree/branch/task-file cleanup. The deterministic herdr logic
lives in one tested helper — `plugins/work-system/scripts/herdr-teardown.sh`
(called from `skills/close/SKILL.md` steps 7 + 12) — plus a plugin-shipped
`SessionEnd` hook (`plugins/work-system/hooks/hooks.json`). This entry captures the
durable design and the hard-won TUI-exit gotchas; the scripts are the source of
truth. Companion to [[herdr-kickoff-automation]] (kickoff creates the tab this
tears down).

## Design decisions

- **Find the tab by cwd (realpath), before removing the worktree.** `herdr pane
  list` exposes each pane's `cwd`/`tab_id`; the worktree tab is the pane whose cwd
  resolves to the worktree path. Compare by `realpath` on both sides — herdr stores
  the *resolved* cwd, so on macOS a `/tmp`→`/private/tmp` (or symlinked `/Users`)
  worktree path would never string-match and the whole teardown would silently
  no-op. This lookup MUST run *before* `git worktree remove` — afterwards the cwd
  points at a deleted path. Match on `cwd` (always present), not `foreground_cwd`
  (absent on idle panes).
- **Decide self-close by pane id, not `$HERDR_TAB_ID`.** Compare the worktree tab
  to *this session's own tab*, resolved from `$HERDR_PANE_ID` (`own-tab`). Equal →
  Scenario B (self-close, Claude can only exit, not close its own tab); different →
  Scenario A (a different tab, close it directly). Do **not** key the decision on
  `$HERDR_TAB_ID`: if it's empty/unset, an equality test misclassifies a self-close
  as Scenario A and `herdr tab close` then kills the live session's own tab
  mid-turn (corrupt transcript). If the own tab can't be resolved, skip the auto
  teardown rather than guess.
- **Plugins ship the `SessionEnd` hook — no settings.json injection.** A plugin's
  `hooks/hooks.json` (in the plugin root, NOT `.claude-plugin/`) auto-merges with
  user hooks on install; the command may use `${CLAUDE_PLUGIN_ROOT}` and **inherits
  the session's env** (so `HERDR_PANE_ID`/`HERDR_TAB_ID` are visible). This is
  unlike the status line, which can't be plugin-owned and needs marker-block
  injection (see [[statusline-integration]]). Note CI: `check-structure.py` must
  scan `hooks/*.json` for `${CLAUDE_PLUGIN_ROOT}` refs too, else a renamed script
  breaks the hook while CI stays green.
- **The hook is conditional via a short-lived per-pane marker.** `/close` (Scenario
  B) writes a marker keyed by `$HERDR_PANE_ID` (a `<timestamp> <tab>` pair); the
  hook closes that tab only when a *fresh* marker exists — a stale one (the user
  never did the clean exit, or a herdr restart reused the pane id) is dropped
  without closing. Marker lives under a **fixed** `$HOME/.cache` — not
  `$XDG_CACHE_HOME` (may diverge between the /close shell and the hook's env), not
  `$TMPDIR` (per-process on macOS) — so /close and the hook always agree on the path.

## Gotcha: there is exactly one way to exit Claude's TUI from outside

Verified live against throwaway herdr tabs. To make a *running* Claude session exit
cleanly from another process:

- `herdr pane run <pane> "/exit"` does **nothing** — `pane run` targets a shell, not
  Claude's TUI (the same shell-vs-TUI mismatch that bit kickoff's first launch).
- `herdr pane send-keys <pane> ctrl+d` does **not** exit either (and `C-d`/`^d` are
  rejected key names; the accepted spelling is `ctrl+d`, but it has no effect here).
- `herdr pane send-text <pane> "/exit"` **then** `herdr pane send-keys <pane> Return`
  **is** the clean exit. (`send-text "/exit"` alone opens the slash-command menu;
  the separate `Return` runs it.)

## Gotcha: clean exit auto-closes the tab; inject onto idle, not mid-turn

- When Claude is the tab's **root pane** (kickoff launches it via `agent start --
  claude`), a clean `/exit` ends the pane's only process, so herdr **auto-closes the
  tab**. The `SessionEnd` hook's `herdr tab close` is therefore a *backup* — it does
  the real work only for sessions where Claude is *not* the root pane (e.g. launched
  inside a shell pane), where exiting drops back to the shell and the tab survives.
- **Self-close injects onto an idle prompt, never mid-turn.** `/close` is itself a
  turn; injecting `/exit` while Claude is busy is unreliable. The helper's `self-exit`
  arms a **detached** injector (`nohup … & disown`) that **polls the pane's
  `agent_status` until it leaves `working`** (the launching turn has ended) and only
  then runs `inject-exit` against its own pane, landing `/exit` on the now-idle
  prompt (the state proven to exit cleanly). Polling beats a fixed `sleep N` timer,
  which fires mid-turn whenever the closing turn outlasts the guess. `nohup` keeps
  the injector alive past the launching turn; the args are passed positionally to an
  internal subcommand (no `bash -c "<interpolated>"`, which would double-eval an
  unusual pane id). This sidesteps mid-turn delivery entirely and needs no
  `--dangerously-skip-permissions` agent to test.

Related: [[herdr-kickoff-automation]], [[skill-composition]] (helper-script single
source of truth). The "never persistent `cd`" footgun the path commands avoid is a
rule — see `.claude/rules/cwd-safety.md`.
