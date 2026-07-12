---
title: "herdr /close Automation"
createdAt: 2026-06-24
updatedAt: 2026-07-12
createdFrom: "PR #18"
updatedFrom: "session: 2026-07-12"
pluginVersion: 1.8.2
prime: false
reindexedAt: 2026-07-12
---

# herdr /close Automation

Inside a herdr session, `/close` tears down the finished task's herdr **tab** on
top of its usual worktree/branch/task-file cleanup. The deterministic herdr logic
lives in one tested helper — `plugins/work-system/scripts/herdr-teardown.sh`
(called from `skills/close/SKILL.md` steps 7 + 12) — plus a plugin-shipped
`SessionEnd` hook (`plugins/work-system/hooks/hooks.json`). This entry captures the
durable design and the hard-won TUI-exit gotchas; the scripts are the source of
truth. Companion to [herdr-kickoff-automation](herdr-kickoff-automation.md) (kickoff creates the tab this
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
  injection (see [statusline-integration](statusline-integration.md)). Note CI: `check-structure.py` must
  scan `hooks/*.json` for `${CLAUDE_PLUGIN_ROOT}` refs too, else a renamed script
  breaks the hook while CI stays green.
- **The hook is conditional via a short-lived per-pane marker.** `/close` (Scenario
  B) writes a marker keyed by `$HERDR_PANE_ID` (a `<timestamp> <tab>` pair); the
  hook closes that tab only when a *fresh* marker exists — a stale one (the user
  never did the clean exit, or a herdr restart reused the pane id) is dropped
  without closing. Marker lives under a **fixed** `$HOME/.cache` — not
  `$XDG_CACHE_HOME` (may diverge between the /close shell and the hook's env), not
  `$TMPDIR` (per-process on macOS) — so /close and the hook always agree on the path.
- **Tear down, then verify — or name the tab (1.5.1).** A teardown must confirm its
  own effect or hand the user an explicit fallback; it must never *report* a close it
  didn't observe. So `close-tab` (Scenario A) now closes **once** and then polls until
  the tab is gone (it does *not* re-issue the close — herdr may recycle the closed tab
  id onto a fresh tab, so a second `tab close` could kill an unrelated live one),
  returning `closed|still-open|unverified` via the `tab_status` helper
  (`present|gone|unverified`); /close names the tab for a manual close on anything but
  `closed`. Scenario B's self-close fires *asynchronously* after the turn
  and **cannot be confirmed in-turn**, so /close step 12 *always* appends "close by
  hand: `<tab-id>`" — turning a silent idle orphan into a visible, actionable line.

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
  `agent_status` until a confirmed `idle`/`done`** (the launching turn has ended) and
  only then runs `inject-exit`, landing `/exit` on the now-idle prompt (the state
  proven to exit cleanly). Polling beats a fixed `sleep N` timer, which fires
  mid-turn whenever the closing turn outlasts the guess. Empirically (verified live)
  herdr's `agent_status` is one of `idle|working|done|unknown`, and a `nohup … &
  disown` poller **survives** past the launching Bash tool call — so the detached
  mechanism is sound; the real failure was the poll window timing out (raised
  30s→120s in 1.5.1) or a dropped `/exit`. It injects only on `idle`/`done` (never
  `working`/`unknown`, which are ambiguous) and **exactly once** — a second injection
  can't tell a dropped `/exit` from a user who reopened the tab and is momentarily
  idle, so it would risk killing that live session; a residual orphan is instead
  surfaced by /close's always-printed manual-close line. Critically it injects
  *only* on a confirmed idle status — a transient `herdr pane list` failure yields
  empty output, which must be retried, **not** mistaken for idle (that would inject
  mid-turn); a vanished pane or a never-idle timeout injects nothing. `nohup` keeps
  the injector alive past the launching turn; the args are passed positionally to an
  internal subcommand (no `bash -c "<interpolated>"`, which would double-eval an
  unusual pane id). This sidesteps mid-turn delivery entirely and needs no
  `--dangerously-skip-permissions` agent to test.

Related: [herdr-kickoff-automation](herdr-kickoff-automation.md), [skill-composition](../architecture/skill-composition.md) (helper-script single
source of truth). The "never persistent `cd`" footgun the path commands avoid is a
rule — see `.claude/rules/cwd-safety.md`.
