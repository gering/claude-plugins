---
title: "Skill Design Conventions & Context Economy"
createdAt: 2026-06-18
updatedAt: 2026-10-16
createdFrom: "PR #13"
updatedFrom: "task: fix-swarm-skill-arg-substitution"
pluginVersion: 1.8.2
prime: true
reindexedAt: 2026-07-12
---

# Skill Design Conventions & Context Economy

The governing design principle across every plugin in this marketplace:
**anything that lands in always-loaded session context is paid for in every
session, not just the one that uses the feature.** Always-loaded surfaces are
skill `description` frontmatter, auto-loaded rule files, `CLAUDE.md` content,
status-line output, and plugin metadata. Detail belongs in the file body the
model reads *on demand* when it invokes the skill — not in the activation
surface.

## Why skill descriptions must stay short

Each skill's `description` frontmatter is loaded into every session for
activation matching. It counts against `skillListingBudgetFraction` (default
1% of context). When the combined descriptions exceed that budget, Claude Code
**truncates** them — and a truncated description can stop matching its trigger
phrases, so the skill silently becomes harder to invoke. This is the concrete
failure mode that justifies the budget discipline; it is not a stylistic
preference.

## The conventions

The concrete rules — description length budget, the `Trigger:`-line structure,
English-only, no feature lists in the description — are stated as the
always-loaded source of truth in CLAUDE.md's "Skill descriptions: keep them
short" section. They are deliberately **not** restated here: a second copy in an
on-demand doc only drifts from the always-loaded one. This file captures the
*why* and the mechanism (above and below); CLAUDE.md is the checklist.

## How to apply

Before writing anything destined for an always-loaded surface, ask: does this
need to be always-on, or can it live in an on-demand body? Can the same meaning
be expressed in half the words? Is it already loaded somewhere else (avoid
duplication that drifts)? Err terse. The marketplace has lived this — see the
commit "Compact skill descriptions to fit listing budget."

## Plugin-global vs per-project always-loaded surface

A corollary of the cost principle: **a plugin should not ship always-loaded
rule files**. A rule bundled in the plugin (e.g. the former `auto-query.md` /
`auto-curate.md`) loads into *every* session of *every* project that has the
plugin installed — even projects that never use the feature. That is a tax the
non-user pays.

The fix is to move that guidance into a **per-project file written by `/init`**
(`.claude/rules/knowledge-system-usage.md`):

- It costs nothing for projects that haven't run `/init`.
- It is committed alongside the project, so it is team-shared and reviewable.
- A single per-project surface also resolves contradictions — two competing
  plugin-global rules (inline-vs-`/query`) collapse into one authored file.

The trade-off this creates is **staleness**: a committed project file can fall
behind the plugin's current guidance. Solved with a **template-version marker**
(`knowledge-system-usage template-vN`) carried in the file. The number is bumped
only when the template content changes (independent of plugin version), and
`/reindex` compares each project copy's marker against the plugin's current
number to flag drift — surfacing staleness without ever auto-mutating a
committed project file.

Related: [skill-composition](skill-composition.md), [model-economics](model-economics.md), [ci-structure-checks](../deployment/ci-structure-checks.md).

## Skill bodies are templates: no `$<digit>` anywhere

Claude Code substitutes `$N` (0-based shorthand for `$ARGUMENTS[N]`) in the
whole rendered SKILL.md, fenced code blocks included. The on-disk file stays
correct, so the bug only shows when the skill runs with arguments. A prep helper's
`sed -n "s/^$1=//p"` rendered `/swarm:review --pr 65` as `s/^65=`. Every codex/grok
model lookup came back empty and the review went Claude-only without an error.
An index with no argument stays literal, so a test run without arguments passes.

- Keep positional parameters out of skill bodies. Inline helpers forward `"$@"`
  (not a token) to a script (`swarm/scripts/kv.sh`), which reads them from disk.
- Don't rely on the documented `\$1` escape inside shell code: if the
  substitution ever skips it, the backslash lands in the shell.
- `check-structure.py` fails on any `$<digit>` in a SKILL.md. A declared
  `arguments:` name (`$name`) is substituted the same way. No skill declares one
  yet, and the lint doesn't cover it.
- A value that a helper produced and a lookup then lost is a prep error
  (`SWARM_PREP_ERR`), not a degraded voice.
