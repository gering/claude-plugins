---
title: "herdr Tab State Glyphs"
createdAt: 2026-07-16
createdFrom: "session: 2026-07-16"
pluginVersion: 1.9.0
prime: false
---

# herdr Tab State Glyphs

work-system prefixes each task tab's herdr agent name with the task's state
glyph (`○` not-started · `●` active · `◇` in review · `◆` approved · `✓`
merged), so the sidebar mirrors the `[ws …]` statusline (e.g. `● close-herdr`,
`◇ ks-label`, `◆ ready-pr`). `◆` (open PR whose `reviewDecision == APPROVED`,
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
cannot drift. `herdr-tab-glyph.sh` only *applies* the result to agent names.

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

- **Trigger points:** stamped at launch (`herdr-launch.sh prefix`, both modes —
  the Claude *session name* stays plain; glyphs would clutter `/resume`);
  re-stamped by `refresh` on `/status`, `/list`, `/close` (remaining tabs, main-repo
  path — `$PWD` may be the just-removed worktree) and pr-flow's `/open`,
  `/merge`, `/cycle`, `/check`.
- **Matching:** exact realpath equality `agent.cwd == <main>/.claude/worktrees/<task>`
  (same philosophy as `herdr-teardown.sh`): an unrelated agent cd'd into a
  worktree subdir is never renamed; agents outside task worktrees are never
  touched. Rename targets the `pane_id`; `herdr agent list` exposes `cwd`,
  `name`, `pane_id` per agent (verified live 2026-07-16).
- **Idempotency:** leading glyphs are stripped before re-prefixing via
  byte-exact `case "○ "*` patterns — a bracket expression (`[○●◇✓]`) would
  match per *byte* for multibyte chars under C locale. Renames are only issued
  when the name actually changes. Everything is best-effort exit-0.

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
