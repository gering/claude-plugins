---
name: curate
description: |
  Stores new learnings in the project's knowledge layer. Captures recurring
  patterns, surprising fixes, architectural decisions, and generalizable
  lessons discovered while coding. Writes to `.claude/knowledge/` (topical
  markdown files) for retrievable documentation or `.claude/rules/`
  (always-active directives) depending on scope. Maintains frontmatter
  metadata (timestamps, plugin version) automatically.

  Use when: user says "remember this", "save this learning", "curate",
  "add to knowledge", "capture this pattern", "document this decision",
  after solving a non-trivial bug or making a judgment call worth
  preserving. Also "merk dir das" / "in die knowledge aufnehmen" /
  "als rule speichern".
user_invocable: true
---

# Curate Learning

Store a new pattern, decision, or learning in the project knowledge system.

## Usage
`/curate "<description>" [file1 file2 ...]`

## Instructions

### 1. Parse the user's input
- The quoted string is the **description** of what was learned
- Any file paths after the description are **reference files**

### 2. Read reference files
If reference files are provided, read them to understand context.

### 3. Decide where it belongs
See `rules/knowledge-boundaries.md`:
- Short code directive (do/don't) → `.claude/rules/<topic>.md`
- Workflow checklist (PR, deploy) → `CLAUDE.md`
- Detailed feature/architecture → `.claude/knowledge/<category>/<topic>.md`

**CLAUDE.md safeguard:** if the target is `CLAUDE.md`, never write inside the `<!-- BEGIN knowledge-system -->` / `<!-- END knowledge-system -->` block — that region is managed by `/init` and will be regenerated on re-run, so any content placed there is lost. Append above or below the block.

### 4. Check if an existing file covers this topic
- For rules: check files in `.claude/rules/`
- For knowledge: read `.claude/knowledge/_index.md`

### 5. Update or create the target file

When writing a knowledge file (under `.claude/knowledge/`), **maintain frontmatter** per the schema below.

#### Frontmatter schema

```yaml
---
title: "<human-readable display name>"
createdAt: <YYYY-MM-DD>      # ISO-8601 date-only, UTC
updatedAt: <YYYY-MM-DD>      # ISO-8601 date-only, UTC
pluginVersion: <x.y.z>       # knowledge-system version
---
```

Note: `reindexedAt` is written by `/reindex` only — do NOT touch it here.

#### Frontmatter maintenance rules

**New files (no prior frontmatter):**
- `title`: derive a short human-readable title from the content (not the filename)
- `createdAt`: today's date in `YYYY-MM-DD` (UTC)
- `updatedAt`: same as `createdAt`
- `pluginVersion`: read from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` (`version` field)

**Existing files with frontmatter (content is being updated):**
- Update `updatedAt` to today's date
- Update `pluginVersion` to the current plugin version
- Leave `title`, `createdAt`, `reindexedAt` unchanged

**Existing files WITHOUT frontmatter (touched for the first time):**
Bring them into form before adding new content:
- Derive `title` from the existing H1 or content
- `createdAt`: reconstruct from git:
  ```bash
  git log --diff-filter=A --format=%aI -- <file> | tail -1 | cut -dT -f1
  ```
  If the file is uncommitted or git returns nothing, fall back to today's date.
- `updatedAt`: today's date (we're about to write)
- `pluginVersion`: current plugin version

**Rule files** (`.claude/rules/*.md`) use their own lightweight frontmatter (`description`, optional `globs`). Do NOT apply the knowledge frontmatter schema to rules.

### 6. Content quality checks (before writing)

- **No volatile values**: Don't hardcode version numbers, counts, or thresholds that change with the code. Describe the pattern instead (e.g., "version is incremented on schema changes" not "version = 11").
- **No security-sensitive details**: Don't document API keys, secrets, or auth internals in plaintext. Describe the approach without exposing specifics.
- **Prefer patterns over snapshots**: Describe *how things work*, not *current exact values*. Values change — patterns persist.

### 7. Update `_index.md`
If a new knowledge file was created → add an entry to the corresponding `_index.md` (or `.claude/knowledge/_index.md` root index).

### 8. Report what was stored
One-line confirmation: file path created/updated, layer (rule vs knowledge vs CLAUDE.md), and which frontmatter fields were set.

## Why this runs in-context (not as agent)

Curate needs the conversation context to know what just happened — which files changed, which patterns were applied, which learnings emerged. A subagent would start fresh and lose all of that.

The plugin version lookup requires `${CLAUDE_PLUGIN_ROOT}`, which is set in the skill's execution context.
