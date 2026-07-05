# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Claude Code plugin marketplace** (monorepo) containing plugins that extend Claude Code with custom skills, agents, and rules. The marketplace is published under the name `gering-plugins`.

## Repository Structure

- `.claude-plugin/marketplace.json` — Marketplace manifest (plugin registry with names, versions, sources)
- `plugins/<plugin-name>/` — Each plugin is a self-contained directory with:
  - `.claude-plugin/plugin.json` — Plugin metadata (name, version, description)
  - `skills/<skill-name>/SKILL.md` — Skill definitions (slash commands)
  - `agents/<name>.md` — Agent definitions (optional)
  - `rules/<name>.md` — Auto-loaded rule files (optional)
  - `README.md` — Plugin documentation

## Current Plugins

- **knowledge-system** (v1.5.x) — Knowledge management with three layers: Rules, Knowledge, Memory. Skills: `/init`, `/query`, `/curate`, `/reindex`, `/backfill-knowledge`, `/migrate`, `/statusline`
- **work-system** (v1.2.x) — Task and worktree workflow. Skills: `/define`, `/kickoff`, `/adopt`, `/continue`, `/status`, `/close`, `/list`
- **pr-flow** (v1.1.x) — PR review feedback loop. Skills: `/open`, `/cycle`, `/check`, `/fix`, `/rebase`, `/merge`
- **swarm** (v0.2.x) — Local mixture-of-agents code review (external `codex`/`grok`/`composer` CLIs + Claude lenses). P2: `/swarm:review` pipeline (scope→fan-out→merge→verify). Skills: `/swarm:review`, `/swarm:agents`

## Plugin Anatomy

Plugins are purely declarative — no build step, no compiled code. Everything is Markdown with YAML frontmatter:

- **Skills** (`SKILL.md`): Define slash commands. Frontmatter specifies name, description, and arguments.
- **Agents** (`.md` in `agents/`): Define sub-agents with model, tools, and memory scope in frontmatter.
- **Rules** (`.md` in `rules/`): Auto-loaded directives with optional glob patterns for file-scoped activation.

### Context efficiency

Whenever you add or change anything that ends up in a Claude Code session's permanent context — frontmatter descriptions, auto-loaded rule files, `CLAUDE.md` content, status-line output, plugin metadata — actively consider the token cost. Every word here is paid for in *every* session, not just the one that uses the feature.

Default questions before writing:
- Does this need to be in the always-loaded surface, or can it live in a body/doc that loads on demand?
- Can the same meaning be expressed in half the words?
- Is this content duplicated somewhere else that's already loaded?

Err on the side of terse. Detail belongs in the file body the model reads when it actually invokes the skill — not in the activation surface.

### Skill descriptions: keep them short

Skill `description` frontmatter is loaded into every Claude Code session for activation matching. It counts against `skillListingBudgetFraction` (default 1%) — when the total exceeds the budget, descriptions get truncated and skills may stop matching.

Guidelines:
- **Aim for ~15–30 words per description** (~150–220 chars). Hard ceiling: 40 words.
- **Structure:** one short sentence on *what* the skill does + a `Trigger: "...", "...", "..."` line with 2–4 short example phrases.
- **English only**, even when the user works in another language — keep all source files in English per global conventions.
- **No feature lists in the description.** Subcommands, edge-case behavior, internal flow — all of that belongs in the `SKILL.md` body, which the model only loads once the skill is invoked.
- **No "Use when:" prose blocks** — the `Trigger:` line replaces them.

## Versioning

Plugin versions are tracked in two places that must stay in sync:
1. `plugins/<name>/.claude-plugin/plugin.json` — canonical version
2. `.claude-plugin/marketplace.json` — marketplace registry version

When bumping a plugin version, update both files. CI (`scripts/check-structure.py`)
fails on version drift, so both must match before a PR can merge.

## Structure Checks

`scripts/check-structure.py` mechanically verifies repo invariants (JSON
validity, version sync, SKILL.md frontmatter + description word budget, internal
`${CLAUDE_PLUGIN_ROOT}` references, shell syntax). It runs in CI on every PR and
push to main, and can be run locally before pushing. Keep it green.

<!-- BEGIN knowledge-system -->
## Project Knowledge System

The project's knowledge index is auto-loaded below. Query detailed
entries with `/query`, store new insights with `/curate`, run a QA pass
with `/reindex`. See `.claude/rules/knowledge-system-usage.md` for when
to use each and the full command list.

@.claude/knowledge/_index.md
<!-- END knowledge-system -->
