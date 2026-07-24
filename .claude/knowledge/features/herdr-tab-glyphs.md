---
title: "herdr Tab State Glyphs"
createdAt: 2026-07-16
createdFrom: "session: 2026-07-16"
pluginVersion: 1.9.0
prime: false
---

# herdr Tab State Glyphs

work-system prefixes each task tab's herdr **tab label** with the task's state
glyph (`○` not-started · `●` active · `◇` in review · `◆` approved · `✓`
merged), so the sidebar mirrors the `[ws …]` statusline (e.g. `● close-herdr`,
`◇ ks-label`, `◆ ready-pr`). A tab at the **main repo root** gets `◉` — the
Manager hub among the task satellites (`○` was rejected: it already means
not-started). `◉` is stateless and non-exclusive: it marks the *location*, so
every tab at the main root carries it. `◆` (open PR whose `reviewDecision == APPROVED`,
ready to `/merge`) is the one state needing a second `gh` field beyond
`state`: the PR cache carries `headRef\tstate\treviewDecision`, and an old
two-column cache row degrades safely to `◇` (empty field 3 is never `APPROVED`).

## Single source without a shared sibling

The obvious design — extract the state→glyph mapping into a helper script both
surfaces source — is **blocked by the renderer's self-containment contract**:
`/work-system:statusline install` copies `ws-statusline.sh` alone to
`~/.claude/`, so it must not source siblings. The resolution is a second *mode
in the same file*: `ws-statusline.sh states <dir>` prints
`<task>\t<state>\t<glyph>` per backlog task from the very `task_state()` /
`glyph_of()` functions the render path aggregates. One file — the surfaces
cannot drift. `herdr-tab-glyph.sh` only *applies* the result to the tab label.

## Sync vs cache-only PR refresh

The render path keeps its never-block rule (detached background `gh` refresh,
see [statusline-integration](statusline-integration.md)). `states` mode has
**two policies**, chosen by the caller:

- **Default (sync):** refresh the PR cache *inline* before reading — for
  triggers that fire right after a PR changed state (`/open` → `◇`, `/merge` →
  `✓`, `/cycle` → possibly `◆`). The async TTL path would serve the pre-change
  state and the glyph would flip one survey late.
- **`--cached`:** read the cache only + kick off the same non-blocking
  background refresh the render path uses — for pure-survey callers (`/status`,
  `/list`, `/check`, `/close`) whose state didn't just change and that must not
  block. `/check` especially: it re-runs during CI polling. The flag threads
  `herdr-tab-glyph.sh refresh --cached` → `ws-statusline.sh states --cached`,
  and through the pr-flow shim `refresh-task-glyphs.sh --cached`.

**The two `gh` bounds must not be one value** (`run_bounded <secs> …`:
`timeout`, else a `perl -e 'alarm'` fallback that survives `exec`; unbounded
only on a host with neither — accepted). The sync path takes **8s**: a caller is
blocked on it, so cut a hung network fast; the glyph is cosmetic. The detached
background refresh takes **20s**: nothing waits on it, and killing a slow but
*successful* API call would leave the cache stale for a whole TTL instead of
populating it. Collapsing both onto the short bound is a real regression — it
was shipped and caught in review.

## Renamer rules (herdr-tab-glyph.sh)

- **The glyph lives in the TAB LABEL — one namespace, and it is not the obvious
  one.** herdr has two independent names per tab: `tab rename <tab_id> <label>`
  and `agent rename <pane_id> <name>` (the agent registry's own field, which
  other tooling owns elsewhere). **The sidebar renders the label.** 1.8.0 shipped
  `refresh` writing the *agent name* — invisible, so every re-stamp silently did
  nothing and only the launch-time label survived (a task tab sat at `●` while
  its PR was long in review). Fixed in 1.8.1: `refresh` writes the label, and
  `herdr-launch.sh` passes the glyph *only* to `--label`, keeping the agent and
  `claude -n` session names plain — those are stable identities, and nothing
  refreshes them, so a glyph there would freeze at its launch value.
- **Trigger points:** stamped at launch (`herdr-launch.sh` → `prefix`, both
  modes); re-stamped by `refresh --cached` on `/status`, `/list`, `/close`
  (remaining tabs, main-repo path — `$PWD` may be the just-removed worktree),
  `/kickoff` (after the launch step, so the new tab is included), `/define`
  (no tab of its own yet — the value is the `◉` + resyncing siblings while
  already in the main session), `/continue`'s reopen, and pr-flow's `/open`,
  `/merge`, `/cycle`, `/check`.
- **Matching:** exact realpath equality against `<main>/.claude/worktrees/<task>`
  (→ state glyph) or the main repo root itself (→ `◉`) — same philosophy as
  `herdr-teardown.sh`: an agent cd'd into a *subdir* of either is never renamed,
  and anything outside the repo never is. `◉` needs no new trigger — the same
  `refresh` sweep stamps both. The match needs **both** herdr lists joined on
  `tab_id`: only agents carry `cwd` (the match key), only tabs carry `label`
  (what we stamp). One tab is stamped once (first matching agent wins — a
  mixed-cwd multi-pane tab would otherwise flip-flop each refresh). The join is
  the tab list passed **by file path** (not an argv element): the JSON can be
  hundreds of KB and would blow ARG_MAX/E2BIG, a failure `|| true` would mask as
  `checked=0`. **Caveat (accepted):** only *agent-backed* tabs are reachable —
  `cwd` has no other source than `herdr agent list`, so a main-root tab that is a
  bare shell (no agent) can't be matched and won't get `◉`. In practice a Manager
  tab is a Claude session, so it is agent-backed; a plain terminal at the root is
  not a Manager session.
- **`◉` is stateless — never gated on the backlog.** The main-root mark is a
  location mark, so `refresh` does NOT early-return on an empty `states` (no
  tasks): it stamps `◉` on main-root tabs regardless, and only the per-task
  `glyph_lookup` finds nothing to do. Coupling `◉` to a non-empty backlog was a
  latent bug (the early-return predated `◉` and it inherited the gate) that also
  contradicted the README/CHANGELOG "every main-root tab carries it" promise.
- **Idempotency:** leading glyphs are stripped before re-prefixing via
  byte-exact `case "○ "*` patterns — a bracket expression (`[○●◇✓]`) would
  match per *byte* for multibyte chars under C locale. The strip set includes
  `◉`, so a tab moving between the hub and a worktree swaps glyphs cleanly.
  Renames are only issued when the name actually changes. Everything is
  best-effort exit-0.
- **The extractor's TSV must never have an empty middle field.** The consumer
  reads it with `IFS=$'\t' read -r …`, and tab is IFS *whitespace* — bash
  collapses a run of tabs into ONE delimiter, so an empty field silently
  shifts every later field left. Adding the `kind` column bit exactly here: a
  main-root row emitted an empty `task`, and the agent's *name* slid into it
  (`◉ Manager` → `◉ claude-plugins`). Hence the `key` column carries the task
  name **or** the repo dir name — never nothing. A non-whitespace delimiter
  (`\x1f`) would also work; non-empty fields were the smaller change.

## pr-flow coupling stays soft

pr-flow never imports work-system: `scripts/refresh-task-glyphs.sh` *locates*
`herdr-tab-glyph.sh` and no-ops silently when work-system or herdr is absent.
Resolution order: (1) dev layout `../work-system/scripts/`; (2) the installed
work-system from `~/.claude/plugins/installed_plugins.json`, picking the
**highest version** across the manifest's insertion-ordered records — the
manifest lists only installed versions, so this survives a rollback (unlike the
never-pruned cache); (3) fallback glob `../../work-system/<version>/…`, newest
via line-wise `sort -V` (used only when the manifest is missing/unparsable —
can pick a newer-than-enabled version, the accepted residual). Same rule as
[skill-composition](../architecture/skill-composition.md) §4.

Related: [herdr-kickoff-automation](herdr-kickoff-automation.md),
[herdr-close-automation](herdr-close-automation.md).
