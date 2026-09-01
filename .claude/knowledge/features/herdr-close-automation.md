---
title: "herdr /close Automation"
createdAt: 2026-06-24
updatedAt: 2026-09-01
createdFrom: "PR #18"
updatedFrom: "session: 2026-09-01 (task/delegate-worktree-close-to-manager)"
pluginVersion: 1.13.0
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

## Delegating a worker close to the Manager (1.13.0) — the preferred path

Scenario B is the fragile half of this feature: a worker closing *itself* needs the
armed marker + the `SessionEnd` hook + a detached `/exit` injector that polls for idle,
and it still **cannot confirm its own teardown in-turn** (hence the always-printed
"close it by hand" fallback). The Manager, by contrast, survives the close and sees the
worker tab as just another tab — i.e. Scenario A, which closes once and *verifies*.

So a `/close` invoked inside a worktree now offers to hand the whole close to the
Manager (one `SendMessage` carrying a `work-system close-request` block, then stop).
**Every delegated close removes one use of Scenario B** — the point is not convenience,
it is deleting a use of the path that cannot self-verify.

Detection lives in `herdr-teardown.sh manager-session` (tri-state
`name=<session>|none|unverified`, always exit 0, like `worktree-tab-state`). Design
points worth keeping:

- **`herdr agent list`, not `pane list`.** Only the agent list distinguishes a live
  claude session from a bare shell that survived an earlier `/exit` — and a shell at
  the repo root must never be offered as a delegation target. It also carries the
  terminal title the address is derived from. Reuses the shared realpath cwd match
  (`$HERDR_MATCH_PRELUDE`) and the bounded, JSON-validating `ha_list`.
- **The address is the terminal title, not herdr's `name`.** Verified live: a herdr
  agent named `gcp-auth-159` hosts the session `answer-gcp-auth-questions-buchhalter-159`
  (the name `ListAgents` shows). herdr strips only its own `✳` status glyph, so one
  leading symbol+space (the working spinner `◐`/`◑`) is stripped here too — requiring
  the space, so a punctuation-led name like `/habemus-event` survives intact.
- **The name is a CANDIDATE, never an address.** Also verified live: a pane titled
  `Alpha-Architect` belongs to the session `alpha-architect-c9` — titles can be custom
  or stale. The skill therefore resolves the name against `ListAgents` and drops the
  offer when it matches zero or more than one session. **Never disambiguate with a
  `[ref]`**: refs cannot be mapped back to a repo (they match neither
  `agent_session.value` nor any pane/tab id), so a guess could message a stranger.
  Real-world friction, worth knowing before "improving" this: several unrelated repos
  can all have a tab titled `Manager`, and then no offer ever appears — by design. Count
  only **live interactive** sessions there: offline / Remote-Control namesakes can neither
  receive a close nor legitimately veto one, and counting them kills the feature outright.
  Because the title is settable by any process in the pane, the confirmation must **name
  the resolved recipient** — a person catching a wrong name is the actual trust anchor
  here, not the string match.
- **Fail-closed everywhere.** Two agents at the repo root, a non-claude or not-live one
  there, an unreadable cwd, a junk list element, an empty/malformed list, missing tools
  → `unverified` → no offer, today's flow unchanged. A wrong `none` costs only the
  offer; a wrong `name=` would send a close request to a stranger session.
- **The request is unauthenticated, so the receiver asks.** Cross-session messages carry
  no proof of origin, and a close is destructive. The Manager therefore validates
  (`task=` against `^[A-Za-z0-9._-]+$` before it touches any command, `repo=` against its
  own main repo), cross-checks `worktree=` against the live lanes, re-runs
  `task-status.sh assess` itself — and then **asks the user once, even on a verified
  merged PR**. That is the one place `/close` asks where a user-invoked close would not:
  a user invocation *is* the authorization; an inbound message is not. Ship-blocking
  distinction, found in review — the earlier "verified merge proceeds unasked" rule let a
  forged request delete a worktree somebody was still working in.
- **The payload is three fields — `task=`, `worktree=`, `repo=` — on purpose.** No `pr=`
  or `branch=`: the Manager re-derives both and is told not to trust them, so carrying
  them would only widen what a misdelivered message leaks. What is left is exactly what
  the receiver cross-checks the request against.
- **The gate also refuses cases the delegation cannot serve**, not just unsafe ones: an
  unconfirmed merge (the step-2 question belongs to the person with the context, not to a
  tab they are not looking at) and an `/adopt`-ed branch that kept a non-`task/` name
  (unresolvable by name outside its worktree, so the Manager would receive a close it
  cannot resolve).

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

## Gotcha: whether a clean exit closes the tab depends on the herdr version

- **Only when Claude is the tab's root pane** does a clean `/exit` end the pane's
  only process and make herdr auto-close the tab. That held while `/kickoff` used the
  legacy `agent start --workspace --cwd -- <argv>` placement (herdr ≤0.7.4).
- **herdr 0.7.5+ inverted it.** `agent start` now requires an already-open pane, so a
  kickoff worker runs *inside* a shell pane: `/exit` drops back to that shell and the
  tab survives. The `SessionEnd` hook's `herdr tab close` — armed by `/close`'s
  per-pane marker — became the **primary** teardown there, not a backup. Never write
  (or reason) as if the exit alone closed the tab; both paths rely on the marker.
- What did **not** change: a modern kickoff worker is still a *registered* agent
  (started via `agent start`), so `agent_status` keeps populating and the idle-poller
  below is unaffected. Only the "who closes the tab" half moved.
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
