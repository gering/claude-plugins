---
title: "Backfill from History & Origin Metadata"
createdAt: 2026-06-18
updatedAt: 2026-07-12
createdFrom: "PR #2"
updatedFrom: "session: 2026-07-12"
pluginVersion: 1.8.2
prime: false
reindexedAt: 2026-07-12
---

# Backfill from History & Origin Metadata

Two related knowledge-system capabilities for retroactively populating a
knowledge base and tracking where each entry came from.

## `/backfill-knowledge` — mine merged PRs

Bootstraps (or extends) a knowledge base from merged-PR history. Shape:

- A **background Sonnet agent** fetches each PR's metadata, commits, and diff
  and judges it against a **strict significance bar** — only new user-facing
  features, architecture changes, or durable major insights pass. Bug fixes,
  refactors, tests, deps-bumps, chores are rejected. The exact bar and target
  acceptance rate live in the skill prompt
  (`plugins/knowledge-system/skills/backfill-knowledge/SKILL.md`); the guiding
  principle: a bloated base is worse than a small one.
- The agent **never writes files** — it returns a candidate report. The user
  approves a selection, then `/curate` runs per pick with `--origin "PR #<N>"`.
- **Idempotency** comes from two sources unioned together: PR numbers already
  anchored in `createdFrom`/`updatedFrom` frontmatter, plus a persistent log
  (`.claude/logs/backfill-knowledge.md`). The log's "Skipped — not significant"
  bucket is critical: without it, every run re-judges every rejected PR and
  regenerates the same noise.

## Origin metadata (`createdFrom` / `updatedFrom`)

Knowledge files record their provenance so a later reader (or `/reindex`) knows
where the learning originated. The value is one of `"PR #<N>"`,
`"branch: <name>"`, or `"session: <date>"`.

The **reconstruction cascade** resolves a commit SHA to a PR. Its full,
authoritative definition lives in `/reindex` (step B) — that is the single
source; do not re-enumerate or reimplement it here. The shape:

- **Primary (online):** `gh pr list --search <sha> --state merged`. GitHub knows
  which PR a commit belongs to regardless of merge mode (merge, squash,
  rebase-FF), so this covers virtually all online cases.
- **Offline fallbacks:** parse the commit subject — both the squash suffix
  `(#<N>)` and the classic merge-commit `Merge pull request #<N>` form — and
  the branch name, in the order `/reindex` defines.
- **Don't guess:** if nothing resolves unambiguously, the field is left **empty**
  rather than filled with a guess.

A `"branch: <name>"` value is upgraded to `"PR #<N>"` later by `/reindex` once
the branch merges — re-running the cascade on the same SHAs, never on the branch
name (the branch may be deleted post-merge).

Related: [skill-design-conventions](../architecture/skill-design-conventions.md) (frontmatter as managed surface).
