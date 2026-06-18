---
title: "Skill Design Conventions & Context Economy"
createdAt: 2026-06-18
updatedAt: 2026-06-18
createdFrom: "branch: task/dogfood-knowledge-system"
updatedFrom: "PR #8"
pluginVersion: 1.7.0
prime: true
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

- **Length:** aim for ~15–30 words per description (~150–220 chars). Hard
  ceiling ~40 words.
- **Structure:** one short sentence on *what* the skill does, then a
  `Trigger: "...", "...", "..."` line with 2–4 short example phrases. The
  `Trigger:` line replaces any prose "Use when:" block — it is what the matcher
  keys on.
- **English only**, even when the user works in another language — all source
  files stay in English per project convention.
- **No feature lists in the description.** Subcommands, flags, edge-case
  behavior, internal flow all belong in the `SKILL.md` body, loaded only once
  the skill is invoked.

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

Related: [[skill-composition]], [[model-economics]], [[ci-structure-checks]].
