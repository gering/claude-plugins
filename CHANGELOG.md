# Changelog

All notable changes to the plugins in this marketplace are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/). Each
plugin is versioned independently and follows [Semantic Versioning](https://semver.org/);
entries are grouped per plugin, newest first.

> **Maintaining this file:** every version-bump PR must add an entry to the
> relevant plugin section. `/open` (readiness checks) and `/merge` (pre-merge
> documentation check) enforce this automatically once the file exists.

## knowledge-system

### 1.9.0 — 2026-07-15
- Modernize the statusline segment: rename the visible label `[cks …]` → `[ks …]` and replace the positional `RULES|KNOW` layout with type glyphs (`§` rules, `◈` knowledge, `❖` legacy project knowledge). Display-only (renderer 1.1.0) — no identifiers, paths, or markers change.

### 1.8.2 — 2026-06-21
- Sharper knowledge curation grounding and staleness detection.

### 1.8.1 — 2026-06-19
- Harden `/init` scaffolding: absorb unmarked sections, lazy domain dirs, never overwrite user content.

### 1.8.0 — 2026-06-17
- Extract `/statusline` install logic into a tested script.

### 1.7.0 — 2026-06-12
- Consolidate rules into one per-project surface; rework the usage-rule staleness check to a template version.

### 1.6.0 — 2026-06-12
- Add `/prime` skill to load foundational docs (architecture + overviews) into context, plus the `prime` frontmatter field that marks which knowledge docs it pulls in.

### 1.5.1 — 2026-05-12
- Fix `/statusline` install bugs surfaced in live testing.

### 1.5.0 — 2026-05-08
- Add `/statusline` skill.

### 1.4.0 — 2026-04-18
- Add `/backfill-knowledge` skill and `--origin` override for `/curate`; add `createdFrom`/`updatedFrom` origin metadata to the knowledge schema.

### 1.3.0 — 2026-04-17
- Add `/reindex` skill; extend `/curate` with frontmatter maintenance; add auto-prime and feature docs.

### 1.2.0 — 2026-04-14
- Drop the `knowledge-` prefix from `/init` and `/migrate`.

### 1.1.1 — 2026-04-14
- Expand skill descriptions for auto-trigger matching.

### 1.1.0 — 2026-03-29
- Add auto-query rule (check knowledge before diving into code) and rewrite the auto-curate rule with concrete trigger moments.

## work-system

### 1.8.1 — 2026-07-17
- Mark the main-repo session in the herdr sidebar: a tab sitting exactly at the main repo root is now prefixed with `◉` — the Manager hub among the `○ ● ◇ ◆ ✓` task satellites. Stateless and non-exclusive (it marks the location, so every tab at the root gets it), stamped by the existing `refresh` sweep — no new trigger. The chosen tab label is preserved (prefix only) and `◉` joins the idempotency strip, so hub↔task moves swap glyphs cleanly.
- Fix: state-glyph refreshes never reached the sidebar. herdr keeps two names per tab — the tab label (what the sidebar renders) and the agent registry's own name — and 1.8.0's `refresh` rewrote the latter, so a tab kept its launch-time glyph forever (a task sat at `●` while its PR was in review). `refresh` now rewrites the tab label, joining `herdr agent list` (carries `cwd`) with `herdr tab list` (carries `label`).
- The glyph now lives in the tab label only: `/kickoff` passes the plain label to `herdr agent start` and `claude -n`, so the agent and session names stay stable identities instead of freezing a launch-time glyph. Existing tabs correct themselves on the next refresh; agent names stamped by 1.8.0 are left as-is (rename them yourself if the leftover glyph bothers you).

### 1.8.0 — 2026-07-16
- Mirror task states onto herdr tab names as a leading state glyph (`○ ● ◇ ◆ ✓`: not-started / active / in-review / approved / merged), matching the `[ws …]` statusline. The mapping + precedence stay in `ws-statusline.sh` (new `states` mode, single source of truth); the new `herdr-tab-glyph.sh` stamps the glyph at launch (`/kickoff`, `/continue`) and re-stamps it idempotently on `/status`, `/list`, and `/close` (and via pr-flow's PR-lifecycle skills). `◆` approved is derived from the PR's `reviewDecision`. Survey surfaces (`/status`, `/list`, `/check`, `/close`) read the PR cache without blocking; state-changing skills (`/open`, `/merge`, `/cycle`) do a bounded synchronous refresh.

### 1.7.0 — 2026-07-15
- Add `/statusline` skill: a `[ws ○… ●… ◇… ✓…]` task-backlog segment for Claude Code's status line. Counts `tasks/*.md` by state (not-started / active / in-review / merged) with muted single-width glyphs; PR state comes from a short-TTL `.git/` cache refreshed by a detached background `gh` call, so rendering never blocks on the network. Own marker segment coexists with the knowledge-system `[cks …]` block.

### 1.6.0 — 2026-07-02
- Add `/continue` reopen mode to recover `/exit`-closed herdr tabs.

### 1.5.1 — 2026-06-30
- Harden herdr `/close` teardown against silent orphan tabs.

### 1.5.0 — 2026-06-29
- Archive the task file on `/close` instead of deleting it.

### 1.4.1 — 2026-06-24
- Automate `/close` herdr tab teardown.

### 1.4.0 — 2026-06-24
- Automate `/kickoff` worktree launch inside herdr (via agent start, extracted into a tested helper).

### 1.3.1 — 2026-06-23
- Route `/kickoff` and `/adopt` through the shared main-repo-path helper.

### 1.3.0 — 2026-06-17
- Refresh work-system: safe dependency install, markdown tables, sharper `/status`.

### 1.2.5 — 2026-06-15
- Make `/define` worktree-aware — write task files to the main repo.

### 1.2.4 — 2026-05-11
- Prevent CWD contamination in worktree skills.

### 1.2.3 — 2026-04-18
- `/list` contextual next-step hint.

### 1.2.2 — 2026-04-18
- `/close` syncs main after a task merges.

### 1.2.1 — 2026-04-15
- Simplify the `/kickoff` one-liner.

### 1.2.0 — 2026-04-15
- Drop the `work-` prefix from all skills.

### 1.1.7 — 2026-04-14
- Expand skill descriptions for auto-trigger matching.

### 1.1.6 — 2026-04-01
- Use the short task name for session naming in `/work-start` and `/work-adopt`.

### 1.1.5 — 2026-03-26
- Fix `gh pr list` to use `--head` instead of `--search` for branch matching.

### 1.1.4 — 2026-03-24
- Fix branch-deletion false positive with the rebase-merge strategy.

### 1.1.3 — 2026-03-23
- Fix `/close` handling of gitignored task files and `TASK.md`.

### 1.1.2 — 2026-03-20
- Add session naming to the `/work-start`/`/work-adopt` one-liners; `/work-continue` auto-installs deps and suggests a session rename.

### 1.1.1 — 2026-03-18
- Add session rename to `/work-continue`.

### 1.1.0 — 2026-03-18
- Add `/work-adopt` skill; store worktrees under `.claude/worktrees/`.

## pr-flow

### 1.3.0 — 2026-07-16
- `/open`, `/merge`, `/cycle`, and `/check` refresh the work-system herdr tab glyphs after PR state changes (soft-coupled via `scripts/refresh-task-glyphs.sh` — silent no-op when work-system or herdr is absent). `/check` uses `--cached` (read-only survey, no blocking `gh` call); the state-changing skills refresh synchronously.

### 1.2.3 — 2026-07-13
- Align the `/cycle` review table with the swarm findings-table layout.

### 1.2.2 — 2026-06-12
- Make `/rebase` risk-based: auto-proceed when changed files don't overlap, show menus otherwise.

### 1.2.1 — 2026-06-12
- Extract a shared readiness-checks spec.

### 1.2.0 — 2026-06-12
- Add `--loop` mode to `/cycle` (auto-fix agreed findings and re-cycle until clean).

### 1.1.9 — 2026-05-18
- Add the enforce-merge-skill rule.

### 1.1.8 — 2026-05-12
- Ship compact skill descriptions to fit the listing budget.

### 1.1.7 — 2026-04-18
- Skip the rebase prompt when invoked by `/merge` or `/cycle`.

### 1.1.6 — 2026-04-18
- Single confirmation for rebase + force-push.

### 1.1.5 — 2026-04-18
- Review-audit follow-ups.

### 1.1.4 — 2026-04-15
- Remove redundant prompts from `/merge` when all checks are green.

### 1.1.3 — 2026-04-15
- `/merge` adds a pre-merge documentation-readiness check.

### 1.1.2 — 2026-04-15
- `/rebase` polls for a review after force-push.

### 1.1.1 — 2026-04-15
- Rename `/create` to `/open` and drop the `pr-` prefix from all skills.

### 1.0.5 — 2026-04-14
- Shared review output format spec.

### 1.0.4 — 2026-04-14
- Expand skill descriptions for auto-trigger matching.

### 1.0.3 — 2026-04-14
- `/pr-create` polls for first-review completion.

### 1.0.2 — 2026-04-14
- Extract polling into a shared script.

### 1.0.1 — 2026-04-14
- Auto-execute readiness checks in `/pr-create`; remove menus.

### 1.0.0 — 2026-04-14
- Initial pr-flow plugin with the PR review workflow skills.

## swarm

### 0.5.0 — 2026-07-17
- Grow the review lens set from 5 to 11 (all default-on): methodological `removed-behavior` + `cross-file-trace` (factual, normal verify) and design-quality `reuse` / `simplification` / `efficiency` / `altitude` (suggestion-shaped, `kind: "design"`).
- Organize the lenses into 4 clusters (`LENS_CLUSTERS` — single source of truth): breakage / threat / design / consistency. Claude fan-out runs one finder per cluster by default (≤4 agents); `--max` splits to one finder per lens (≤11). The gate still prunes per-lens.
- Verify design findings with a kind-aware applicability prompt (reuse target real? simpler form behavior-identical?) through the same 3-state verifier — consensus design clusters included (agreement isn't applicability); report them in their own `Design` table so they never dilute the defect ranking (`balance.design` counts them).
- Extend the external backend prompt with the six new angles so cross-family consensus can form on design findings too.
- Harden the lens plumbing after the first dogfooding run (swarm reviewed its own diff): keep validly tagged off-cluster lens prefixes (validate against the global set, not the finder's subset), derive `CANDIDATE_LENSES` from `LENS_CLUSTERS` (one list), untagged external findings no longer re-kind a merged cluster, and `pr-post.py` owns design-row ordering + `[lens]` prefixing via new optional `kind`/`lens` row fields (unit-tested).
- Harden a second time after the `--max` dogfooding round (per-lens split, 13 voices): never auto-accept an all-untagged consensus cluster (no lens backs it — verified like a solo, via a single `needsVerify` partition); the design verifier now sees the finding's `recommendation` (the proposal it tests) and carries an escape hatch for defects mis-filed under a design lens; workflow-assigned stable `num` per finding; merge-agent lens validated against the lens set; improvement invitation scoped to design finder units; `LENS_BRIEF` startup assertion + `test_lens_sync.py` guards the lens mirrors; `REFUTED` is its own balance segment (a refuted consensus design cluster is not a solo); doc sweep of the stale "verifies solos" wording.
- Harden a third time after an external-only `--max` dogfooding round (codex + grok-4.5 + composer, no Claude lenses): the merge-supplied cluster lens is accepted only when a member actually tagged it (a globally-valid lens no member carried no longer corrupts the `[lens]` prefix / `survivingPerLens`), and `unspecified` never wins the majority tally; the design verifier's mis-filed-bug exception now reclassifies the finding to `kind: "defect"` (`reclassifyToDefect`) so a bug wearing a design lens leaves the Design table; a methodological-lens (`removed-behavior` / `cross-file-trace`) cross-family consensus with no repo-reading Claude voice is verified rather than auto-accepted (diff-only externals can't confirm a repo fact); `test_lens_sync.py` pins `METHODOLOGICAL_LENSES` to the cluster set.
- Harden a fourth time after the external-only round's fixes were re-reviewed by a full-ensemble `--max` pass (the verifier read the real repo and confirmed two regressions in the prior commit): the methodological-consensus guard now checks that a Claude voice actually *tagged* the methodological lens (member `(backend, lens)`), not mere family presence — plurality lens resolution could otherwise label a cluster `cross-file-trace` off two externals while the lone Claude member tagged `[correctness]` and never checked the claim; `needsVerify` fires on any design-tagged member so a mixed design+defect cluster still applicability-checks its proposal, and the design fence carries the proposal for all-untagged clusters too; a `reclassifyToDefect` finding now also strips its design lens (else pr-post re-buckets it to Design when the step-5 handoff drops `kind`) and drops to solo (an applicability pass alone must not mint a consensus defect); README pipeline diagram + consensus blurb corrected to the 0.5.0 verify gate (the earlier sweep missed them).
- Harden a fifth time after a full-ensemble `--max` confirmation pass (which REFUTED all re-flags of the prior round's fixes but caught fresh ones): `pr-post.py` buckets rows by `kind` ALONE — a dropped kind falls to defect (the safe bucket), the lens is no longer a design fallback that could hide a reclassified/mixed-cluster bug in the Design table (`DESIGN_LENSES` retired); the design applicability verifier redacts out-of-repo path tokens from the proposal before fencing, so a hostile `--pr` proposal naming `~/.aws/…` can't lure the verifier into an out-of-repo read; the dead `c.kind === 'design'` verify-rubric disjunct is dropped; `test_lens_sync.py` upgraded from a subset to a completeness check (`METHODOLOGICAL_LENSES == breakage − topical`); the SKILL `num`-verbatim rule is scoped to round 0 (cross-round `#` is presenter-owned) and two stale doc comments corrected.

### 0.4.3 — 2026-07-17
- Remove the `grok-composer-2.5-fast` backend: grok CLI 0.2.101 dropped the model, so the composer voice (adapter path, defensive parser, workflow voice, docs) failed at runtime. `grok-4.5` is now the only grok model, and the ensemble is three voices (Claude lenses + codex + grok-4.5).
- Make grok readiness model-aware: `ready`/`list` now require `grok-4.5` to appear in `grok models`, not just auth — so a dropped or renamed model reads as "not ready" with an actionable hint instead of failing mid-review. The probe runs unjailed like the sibling `codex login status` check (a readiness check passes no untrusted diff, so it needs no sandbox and stays free of the review path's python3 profile-build), bounded by `SWARM_PROBE_TIMEOUT` (10s, `timeout -k` so a SIGTERM-ignoring CLI can't hang it; separate from the review-length `SWARM_TIMEOUT`). It falls back to the auth check — with a warning on stderr, never silently — whenever it can't produce a clean answer (no coreutils `timeout`, non-zero exit, timeout, or an empty/unparseable list), rather than dropping grok from the fan-out.

### 0.4.2 — 2026-07-15
- Fix grok CLI 0.2.101 compat: pin `grok-4.5` (upstream renamed `grok-build`), cap grok effort at `high` (the `max` tier is gone; the adapter maps `xhigh`/`max` → `high` so stale callers degrade instead of erroring).

### 0.4.1 — 2026-07-15
- Extract the `/swarm:review --pr` publish path into a deterministic, unit-tested `scripts/pr-post.py` (per-cell sanitizer, stale-head gate, `gh` post); shrink `SKILL.md` step 5 to orchestration + the human confirm gate.

### 0.4.0 — 2026-07-13
- Add `/swarm:review --pr`: review a PR diff and post the gated result via `gh`.

### 0.3.1 — 2026-07-13
- Fence finding text structurally in the merge/verify prompts.

### 0.3.0 — 2026-07-12
- Add `/swarm:review --fix` / `--loop` / `--max` actions (P5).

### 0.2.1 — 2026-07-12
- Pin the codex backend to `gpt-5.6-terra`.

### 0.2.0 — 2026-07-08
- Add the `/swarm:review` mixture-of-agents pipeline: scope → fan-out → merge → verify (P2).

### 0.1.0 — 2026-07-03
- Initial swarm plugin: local mixture-of-agents review adapter (P1).
