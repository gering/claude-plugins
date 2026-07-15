# Changelog

All notable changes to the plugins in this marketplace are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/). Each
plugin is versioned independently and follows [Semantic Versioning](https://semver.org/);
entries are grouped per plugin, newest first.

> **Maintaining this file:** every version-bump PR must add an entry to the
> relevant plugin section. `/open` (readiness checks) and `/merge` (pre-merge
> documentation check) enforce this automatically once the file exists.

## knowledge-system

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
