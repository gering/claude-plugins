---
name: curate
description: |
  Stores a learning in `.claude/knowledge/` or `.claude/rules/` with
  managed frontmatter.
  Trigger: "remember this", "save this learning", "curate".
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

### 4. Check for existing coverage (dedup)

Before writing, check whether this learning is already covered — in the target
layer **and** in the always-loaded surfaces. Storing a second copy of
always-loaded content is pure cost: it is paid for in every session and drifts
out of sync with the original.

- **Target layer:**
  - For rules: scan files in `.claude/rules/`.
  - For knowledge: read `.claude/knowledge/_index.md` (and open the closest-looking file).
- **Always-loaded surfaces** (these are already in your context this session — check what you can see):
  - `CLAUDE.md` at the project root — its content loads into every session.
  - `.claude/rules/*.md` — auto-loaded directives.
  - The memory index already loaded in this session (if any) — if the learning is about how the *user* wants to work, it belongs in memory, not here.

On overlap, do **not** write a second copy. Instead:
- Store only the **non-duplicative delta** (the part that is genuinely new), or
- Store a short pointer to the authoritative location, or
- If it is already fully covered always-loaded, write nothing and say so.

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
pluginVersion: <x.y.z>       # read fresh from plugin.json at curation time
prime: <true|false>          # see "Prime assessment" below — does /prime load this?
---
```

Note: `reindexedAt` is written by `/reindex` only — do NOT touch it here.

#### Prime assessment (`prime`)

`prime` marks whether `/prime` should pull this doc into context at session start. It is a foundational-vs-detail judgment:

- `prime: true` — foundational/orienting: architecture, system overviews, cross-cutting flows, the "how it all fits together" docs. Someone new to the project would want this in context before touching code.
- `prime: false` — narrow detail: a single edge case, one config knob, a localized gotcha. Useful via `/query` on demand, but noise in a broad prime.

When in doubt, lean `false` — `/prime` is meant to load the map, not the whole atlas. Anything under `architecture/` is almost always `prime: true`.

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
- `pluginVersion`: read **fresh** from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` (`version` field) at write time — never reuse a value seen earlier in the session, so a long session can't stamp a stale version
- `prime`: assess `true`/`false` per "Prime assessment" above

**Existing files with frontmatter (content is being updated):**
- Update `updatedAt` to today's date
- Update `updatedFrom` to the current origin
- Update `pluginVersion` to the current plugin version (read fresh from plugin.json, as above)
- Leave `title`, `createdAt`, `createdFrom`, `reindexedAt` unchanged
- Leave `prime` unchanged if already set (it is a deliberate choice); only add it (via the assessment) if the field is missing

**Existing files WITHOUT frontmatter (touched for the first time):**
Bring them into form before adding new content:
- Derive `title` from the existing H1 or content
- `createdAt`: reconstruct from git:
  ```bash
  git log --diff-filter=A --format=%aI -- <file> | tail -1 | cut -dT -f1
  ```
  If the file is uncommitted or git returns nothing, fall back to today's date.
- `updatedAt`: today's date (we're about to write)
- `createdFrom`: attempt reconstruction from the first commit's merge context using the PR-resolution cascade defined canonically in `/reindex` SKILL.md, task B ("createdFrom: reconstruct from the first commit..."). Do NOT reimplement the cascade here — the `/reindex` description is the source of truth, and if the logic ever evolves, only one place needs to change. If the cascade returns unresolved, fall back to the current origin.
- `updatedFrom`: current origin
- `pluginVersion`: current plugin version (read fresh from plugin.json, as above)
- `prime`: assess `true`/`false` per "Prime assessment" above

**Rule files** (`.claude/rules/*.md`) use their own lightweight frontmatter (`description`, optional `globs`). Do NOT apply the knowledge frontmatter schema to rules.

### 6. Content quality checks (before writing)

- **Grounding gate (most important)**: every concrete claim — file paths, counts, flag/skill names, "which skill does what", thresholds, cascades — must be grounded in a reference file **actually read this run** (step 2) or in what verifiably just happened in this session. Do NOT assert specifics from memory or from a diff summary. If you can't point to where a claim came from, read the source to confirm it or leave it out. This is the single biggest source of curated-knowledge defects.
- **Prefer linking over restating mutable specifics**: flag lists, option tables, thresholds, command cascades, exact counts, and version numbers live in a SKILL.md / script / config and *will* drift. Link to the authoritative source (e.g. "see `path/to/SKILL.md` step N") instead of copying it. Restate only the durable shape ("resolution uses a first-hit cascade"), never the mutable detail or current snapshot.
- **No security-sensitive details**: Don't document API keys, secrets, or auth internals in plaintext. Describe the approach without exposing specifics.
- **Link format**: *cross-references between knowledge entries* use markdown links `[text](relative/path.md)`, knowledge → knowledge only — never `[[wikilinks]]` (memory's convention). Linking out to source code / a SKILL.md (the bullet above) and pointing at an always-loaded source (step 4) are both fine; the knowledge → knowledge rule only governs links *between entries*. See `/reindex` task C for the authoritative statement.

### 7. Update `_index.md`
If a new knowledge file was created → add an entry to the corresponding `_index.md` (or `.claude/knowledge/_index.md` root index).

### 8. Report what was stored
One-line confirmation: file path created/updated, layer (rule vs knowledge vs CLAUDE.md), and which frontmatter fields were set. If step 4 resolved to writing nothing (already fully covered always-loaded), say that instead and name where it is already covered — do not invent a file path.

## Why this runs in-context (not as agent)

Curate needs the conversation context to know what just happened — which files changed, which patterns were applied, which learnings emerged. A subagent would start fresh and lose all of that.

The plugin version lookup requires `${CLAUDE_PLUGIN_ROOT}`, which is set in the skill's execution context.
