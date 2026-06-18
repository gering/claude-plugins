---
title: "Skill Composition: Flag Contracts, Shared Scripts, Soft Coupling"
createdAt: 2026-06-18
updatedAt: 2026-06-18
createdFrom: "branch: task/dogfood-knowledge-system"
updatedFrom: "branch: task/dogfood-knowledge-system"
pluginVersion: 1.7.0
prime: true
---

# Skill Composition

Skills in this marketplace compose rather than duplicate. Three mechanisms make
that work, and a fourth keeps plugins loosely coupled so they can split apart
cleanly later.

## 1. Flag contracts (one skill as another's subroutine)

A skill that a parent invokes exposes flags that suppress redundant interaction.
The canonical example is `/rebase`, called as a subroutine by `/open`,
`/cycle`, and `/merge`:

- **`--no-poll`** — skip the post-push review polling step. The parent does its
  own polling; double-polling would be wasteful and confusing.
- **`--auto`** — skip the overlap menu entirely. The parent's invocation *is*
  the authorization to rebase and force-push, so re-prompting would violate the
  "interactive by default, autonomy as explicit opt-in" principle from the
  wrong direction (asking again after the user already opted in).

The contract is deliberate: `--auto` removes prompts the parent already
authorized, but **does not** remove genuine safety stops — conflicts still
abort cleanly, missing-upstream / detached-HEAD still stop. Under `--auto`,
uncommitted changes are auto-stashed and popped (reversible, so not a stopping
condition); a stash-pop *conflict* is surfaced distinctly so the parent does
not `git add -A` over markers. This subtlety matters for `/cycle`, which
rebases before committing its pending changes — a hard stop there would abort
the cycle before anything is saved.

## 2. Shared scripts as single source of truth

Logic used by multiple skills lives in one script, not copied prose.
`pr-flow/claude-review.sh` (poll / latest / latest-after) backs three skills
(`/cycle`, `/check`, `/fix`). One implementation to harden — real-world fixes
(fractional-seconds parsing, merge-method detection from PR history,
`mergeStateStatus` interpretation) land once and all callers benefit.

## 3. Format contracts between skills

When one skill must parse another's output, the format is pinned in a shared
spec. `REVIEW-OUTPUT-FORMAT.md` lets `/fix` deterministically parse the table
`/cycle` produces. The contract is the interface; neither side reverse-engineers
the other.

## 4. Soft coupling for a future split

Plugins detect each other generically, never by hard dependency.
pr-flow recognizes knowledge systems by convention (`.claude/knowledge/`,
`.cursor/rules/`, `AGENTS.md`, …) rather than importing the knowledge-system
plugin; `/merge` *offers* `/close` instead of calling it. This keeps the
plugins independently installable and is the right shape for an eventual
extraction (e.g. the Codex split).

## Anti-pattern to watch

Duplicated logic across two skills drifts. `/merge`'s doc-readiness checks that
"mirror `/open` checks 3c–3f" are flagged as a drift risk precisely because the
shared-spec pattern (mechanism 3) exists but wasn't applied there yet.

Related: [[skill-design-conventions]], [[cwd-safety]].
