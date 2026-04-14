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

- **knowledge-system** (v1.1.0) — Knowledge management with three layers: Rules, Knowledge, Memory. Skills: `/init`, `/query`, `/curate`, `/migrate`
- **work-system** (v1.1.6) — Task and worktree workflow. Skills: `/work-create`, `/work-start`, `/work-adopt`, `/work-continue`, `/work-check`, `/work-close`, `/work-list`
- **pr-flow** (v1.0.1) — PR review feedback loop. Skills: `/pr-create`, `/pr-cycle`, `/pr-check`, `/pr-fix`, `/pr-rebase`, `/pr-merge`

## Plugin Anatomy

Plugins are purely declarative — no build step, no compiled code. Everything is Markdown with YAML frontmatter:

- **Skills** (`SKILL.md`): Define slash commands. Frontmatter specifies name, description, and arguments.
- **Agents** (`.md` in `agents/`): Define sub-agents with model, tools, and memory scope in frontmatter.
- **Rules** (`.md` in `rules/`): Auto-loaded directives with optional glob patterns for file-scoped activation.

## Versioning

Plugin versions are tracked in two places that must stay in sync:
1. `plugins/<name>/.claude-plugin/plugin.json` — canonical version
2. `.claude-plugin/marketplace.json` — marketplace registry version

When bumping a plugin version, update both files.

## Project Knowledge System
- **Rules** (`.claude/rules/`): Always active — coding style, patterns, dos/don'ts
- **Knowledge** (`.claude/knowledge/`): On demand — query with `/query`
- **Curate**: Use `/curate` to store new learnings after implementing features or fixing bugs
