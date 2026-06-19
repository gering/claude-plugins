---
title: "Skill Composition: Flag Contracts, Shared Scripts, Soft Coupling"
createdAt: 2026-06-18
updatedAt: 2026-06-19
createdFrom: "branch: task/dogfood-knowledge-system"
updatedFrom: "branch: task/dogfood-knowledge-system"
pluginVersion: 1.8.0
prime: true
---

# Skill Composition

Skills in this marketplace compose rather than duplicate. Three mechanisms make
that work, and a fourth keeps plugins loosely coupled so they can split apart
cleanly later.

## 1. Flag contracts (one skill as another's subroutine)

A skill that a parent invokes exposes flags that suppress interaction the parent
has already handled. The canonical example is `/rebase`, called as a subroutine
by `/open`, `/cycle`, and `/merge`, with `--no-poll` (the parent does its own
review polling) and `--auto` (the parent's invocation *is* the authorization, so
the subroutine skips menus it would otherwise show).

The principle, not the exact flag mechanics (which live in
`plugins/pr-flow/skills/rebase/SKILL.md` and evolve there): a parent-authorized
flag removes **prompts**, never **safety stops**. Conflicts still abort,
missing-upstream still stops — `--auto` only drops the confirmation the parent
already implied by calling. Re-prompting after an explicit opt-in is its own
anti-pattern. Refer to the rebase skill for the current, authoritative
semantics rather than relying on this summary.

## 2. Shared scripts as single source of truth

Logic used by multiple skills lives in one script, not copied prose.
`plugins/pr-flow/scripts/claude-review.sh` (poll / latest / latest-after) is
shared by five skills — `/check`, `/cycle`, `/merge`, `/open`, `/rebase`
(note: `/fix` does **not** use it; it only consumes the review *format* below).
One implementation to harden — real-world fixes (fractional-seconds parsing,
merge-method detection from PR history, `mergeStateStatus` interpretation) land
once and all callers benefit.

## 3. Format contracts between skills

When one skill must parse another's output, the format is pinned in a shared
spec. `plugins/pr-flow/docs/REVIEW-OUTPUT-FORMAT.md` lets `/fix` deterministically
parse the findings table `/cycle` produces. The contract is the interface;
neither side reverse-engineers the other.

## 4. Soft coupling for a future split

Plugins detect each other generically, never by hard dependency.
pr-flow recognizes knowledge systems by convention (`.claude/knowledge/`,
`.cursor/rules/`, `AGENTS.md`, …) rather than importing the knowledge-system
plugin; `/merge` *offers* `/close` instead of calling it. This keeps the
plugins independently installable and is the right shape for an eventual
extraction (e.g. the Codex split).

## Why extract-to-shared-spec is the standing remedy

Duplicated logic across two skills drifts. The fix is always to pull the shared
part into one source the callers reference — exactly mechanisms 2 and 3. The
doc-readiness checks once duplicated between `/open` and `/merge` were resolved
this way: both now read `plugins/pr-flow/docs/READINESS-CHECKS.md` instead of
each carrying their own copy. When you see the same logic in two skills, that is
the move.

Related: [[skill-design-conventions]]. See also the `cwd-safety` rule
(`.claude/rules/cwd-safety.md`).
