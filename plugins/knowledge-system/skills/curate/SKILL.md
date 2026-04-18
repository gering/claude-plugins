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
`/curate "<description>" [files...] --origin "PR #42"`   # origin override (used by /backfill-knowledge)

## Instructions

### 1. Parse the user's input

The invocation has the shape:
```
/curate "<description>" [reference_files...] [--origin "<value>"]
```

Parse rules (apply in this order):
- The **first quoted string** is the `description` of what was learned.
- The `--origin` flag is optional and may appear **anywhere** after the description. It consumes exactly one argument — the immediately following quoted string — as its value. All other positional arguments (before `--origin`, between the description and `--origin`, or after the flag's value) are **reference files**.
- Concrete examples:
  - `/curate "auth uses JWT" src/auth.ts` → desc=`"auth uses JWT"`, refs=[`src/auth.ts`], origin=auto
  - `/curate "auth uses JWT" src/auth.ts --origin "PR #42"` → desc=`"auth uses JWT"`, refs=[`src/auth.ts`], origin=`PR #42`
  - `/curate "auth uses JWT" --origin "PR #42" src/auth.ts src/middleware.ts` → desc=`"auth uses JWT"`, refs=[`src/auth.ts`, `src/middleware.ts`], origin=`PR #42`
- `--origin "<value>"` overrides the auto-detected origin for `createdFrom` / `updatedFrom`. Reserved for programmatic callers like `/backfill-knowledge` — humans should not need this flag.

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
createdFrom: "<origin>"      # see "Origin detection" below
updatedFrom: "<origin>"      # see "Origin detection" below
pluginVersion: <x.y.z>       # knowledge-system version
---
```

Note: `reindexedAt` is written by `/reindex` only — do NOT touch it here.

#### Origin detection (`createdFrom` / `updatedFrom`)

Determine the current origin once at skill start, then stamp it into the right field depending on whether the knowledge file is new or updated.

If `--origin "<value>"` was passed on the command line, use that verbatim and skip the auto-detection below.

Otherwise, auto-detect:

1. Check branch: `git rev-parse --abbrev-ref HEAD`
   - If `main` or `master`: origin = `"session: <today-YYYY-MM-DD>"`. Done.
2. Check for an associated PR: `gh pr view --json number,state 2>/dev/null`
   - If a PR exists (any state): origin = `"PR #<number>"`
   - If no PR exists: origin = `"branch: <branch-name>"` — `/reindex` will later upgrade this to `"PR #<N>"` once the branch is merged.

#### Frontmatter maintenance rules

**New files (no prior frontmatter):**
- `title`: derive a short human-readable title from the content (not the filename)
- `createdAt`: today's date in `YYYY-MM-DD` (UTC)
- `updatedAt`: same as `createdAt`
- `createdFrom`: current origin (see above)
- `updatedFrom`: same as `createdFrom`
- `pluginVersion`: read from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` (`version` field)

**Existing files with frontmatter (content is being updated):**
- Update `updatedAt` to today's date
- Update `updatedFrom` to the current origin
- Update `pluginVersion` to the current plugin version
- Leave `title`, `createdAt`, `createdFrom`, `reindexedAt` unchanged

**Existing files WITHOUT frontmatter (touched for the first time):**
Bring them into form before adding new content:
- Derive `title` from the existing H1 or content
- `createdAt`: reconstruct from git:
  ```bash
  git log --diff-filter=A --format=%aI -- <file> | tail -1 | cut -dT -f1
  ```
  If the file is uncommitted or git returns nothing, fall back to today's date.
- `updatedAt`: today's date (we're about to write)
- `createdFrom`: attempt reconstruction from the first commit's merge context using the PR-resolution cascade defined canonically in `/reindex` SKILL.md, step B ("createdFrom: reconstruct from the first commit..."). Do NOT reimplement the cascade here — the `/reindex` description is the source of truth, and if the logic ever evolves, only one place needs to change. If the cascade returns unresolved, fall back to the current origin.
- `updatedFrom`: current origin
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
