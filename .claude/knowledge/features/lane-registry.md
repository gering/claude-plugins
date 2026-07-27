---
title: "Lane registry: lanes.sh + herdr-agent.sh"
createdAt: 2026-07-24
updatedAt: 2026-07-24
createdFrom: "branch: task/add-lane-registry"
updatedFrom: "branch: task/add-lane-registry"
pluginVersion: 1.9.0
prime: false
---

# Lane registry: `lanes.sh` + `herdr-agent.sh`

Wave-1 foundation for the coordinated Manager/Worker model (see
[manager-worker-orchestration](../architecture/manager-worker-orchestration.md)).
Two work-system scripts; all later orchestration tasks consume them.

## `herdr-agent.sh` — the one herdr-agent wrapper + the shared cwd-match

Guarded wrappers over the `herdr agent list|get|read|wait` primitives (see the
script header for the exact exit-code contract and the bounded-wait default).
Two design points that matter downstream:

- **Degrade, never block.** Missing `herdr`/`python3`, an unreachable server, or
  malformed JSON is a non-zero exit with empty stdout — distinct codes so a
  caller can tell *why*. `read` is best-effort (free-form pane text, no schema);
  `wait` injects a default `--timeout` when the caller omits one, so a wrapper
  can never hang (the "no busy loop" guarantee).
- **It is the single home of the realpath cwd↔worktree match.** The match lives
  as `$HERDR_MATCH_PRELUDE` — a python source string (`match_roots` +
  `classify_cwd`) that consumers *prepend* to their own snippet. Sourcing the
  script is **side-effect free** (a `BASH_SOURCE[0] == $0` guard suppresses the
  CLI dispatch), so a consumer can pull in just the prelude.

`herdr-tab-glyph.sh` was refactored to consume that prelude instead of its own
inline copy (it drove the [tab glyphs](herdr-tab-glyphs.md)). It has **no unit
test**, so the refactor was regression-guarded by running its classification
over a *live herdr snapshot* before and after and confirming byte-identical
output — the technique to reach for when refactoring an untested renderer.

`herdr-teardown.sh` was deliberately **left alone**: its `realpath(cwd) ==
target` lookup is a 1:1 match against one given path returning a tab id — a
*different* operation from the 1:N "classify this cwd against the repo's whole
worktrees dir" that `classify_cwd` does. Forcing it through the shared helper
would be scope creep with regression risk on a tested path.

## `lanes.sh` — the derived-live lane view

Joins `ws-statusline.sh states --cached` (backlog state+glyph) with `herdr agent
list` (liveness), one row per active worktree. `--cached` = pure survey, never
blocks on `gh`. TSV by default, `--json` for objects (columns listed in the
script header).

- **Keyed by worktree path**, never pane/tab ids — those churn and are liveness
  data, not identity. The lane *set* comes from `git worktree list` (also the
  source of the `branch` column), filtered to direct children of
  `.claude/worktrees/`; state joins by task name, liveness by realpath'd cwd
  (first agent in a worktree wins).
- **Liveness degrade tri-state**, mirroring `herdr-teardown`'s
  `worktree-tab-state`: outside a herdr session (`HERDR_ENV != 1`) → blank;
  inside herdr but the list is unreachable/empty/malformed → fail-closed
  `unverified` (never a guessed liveness); populated list but this worktree
  unmatched → confidently blank (there really is no live worker).

## Testability seam

Both the join and its degrade paths are unit-tested without a real repo/herdr
via three env **test seams** — `LANES_WORKTREES_FILE` / `LANES_STATES_FILE` /
`LANES_AGENTS_FILE` inject the three raw inputs, so `test_lanes.py` exercises
join / degraded / unverified / first-wins / exclusion hermetically.
`test_herdr_agent.py` covers the primitives + degrade + bounded wait with a fake
`herdr` on PATH. Both run under `check-structure.py`'s plugin-test check.
