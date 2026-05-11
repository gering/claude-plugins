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

- **knowledge-system** (v1.4.0) — Knowledge management with three layers: Rules, Knowledge, Memory. Skills: `/init`, `/query`, `/curate`, `/reindex`, `/backfill-knowledge`, `/migrate`
- **work-system** (v1.2.3) — Task and worktree workflow. Skills: `/define`, `/kickoff`, `/adopt`, `/continue`, `/status`, `/close`, `/list`
- **pr-flow** (v1.1.7) — PR review feedback loop. Skills: `/open`, `/cycle`, `/check`, `/fix`, `/rebase`, `/merge`

## Plugin Anatomy

Plugins are purely declarative — no build step, no compiled code. Everything is Markdown with YAML frontmatter:

- **Skills** (`SKILL.md`): Define slash commands. Frontmatter specifies name, description, and arguments.
- **Agents** (`.md` in `agents/`): Define sub-agents with model, tools, and memory scope in frontmatter.
- **Rules** (`.md` in `rules/`): Auto-loaded directives with optional glob patterns for file-scoped activation.

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

When bumping a plugin version, update both files.

## Project Knowledge System
- **Rules** (`.claude/rules/`): Always active — coding style, patterns, dos/don'ts
- **Knowledge** (`.claude/knowledge/`): On demand — query with `/query`
- **Curate**: Use `/curate` to store new learnings after implementing features or fixing bugs
