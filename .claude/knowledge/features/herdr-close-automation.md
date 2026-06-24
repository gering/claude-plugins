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

- **Find the tab by cwd, before removing the worktree.** `herdr pane list` exposes
  each pane's `cwd`/`tab_id`; the worktree tab is the pane whose `cwd` == the
  worktree path. This lookup MUST run *before* `git worktree remove` — afterwards
  the cwd points at a deleted path and never matches. Match on `cwd` (always
  present), not `foreground_cwd` (absent on idle panes).
- **`WT_TAB == $HERDR_TAB_ID` is the self-close discriminator.** If the worktree's
  tab id equals the running session's own tab, `/close` is running *inside* the tab
  being removed (Scenario B, self-close); otherwise it's a different tab (Scenario
  A, e.g. the main session). Scenario A closes the tab directly (`herdr tab close`);
  Scenario B cannot — Claude can't close its own tab, only exit.
- **Plugins ship the `SessionEnd` hook — no settings.json injection.** A plugin's
  `hooks/hooks.json` (in the plugin root, NOT `.claude-plugin/`) auto-merges with
  user hooks on install; the command may use `${CLAUDE_PLUGIN_ROOT}` and **inherits
  the session's env** (so `HERDR_PANE_ID`/`HERDR_TAB_ID` are visible). This is
  unlike the status line, which can't be plugin-owned and needs marker-block
  injection (see [[statusline-integration]]).
- **The hook is conditional via a per-pane marker.** `/close` (Scenario B) writes a
  marker keyed by `$HERDR_PANE_ID` (containing the tab to close); the hook closes
  that tab only when the marker exists, so it is a no-op on every ordinary session
  exit. Marker lives under `$HOME/.cache` (stable across the pane's processes),
  not `$TMPDIR` (per-process on macOS).

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
  arms a **detached** injector (`nohup … & disown`) that sleeps a few seconds — past
  the turn's end — then runs `inject-exit` against its own pane, landing `/exit` on
  the now-idle prompt (the state proven to exit cleanly). `nohup` keeps it alive past
  the launching turn. This sidesteps mid-turn delivery entirely and needs no
  `--dangerously-skip-permissions` agent to test.

Related: [[herdr-kickoff-automation]], [[skill-composition]] (helper-script single
source of truth). The "never persistent `cd`" footgun the path commands avoid is a
rule — see `.claude/rules/cwd-safety.md`.
