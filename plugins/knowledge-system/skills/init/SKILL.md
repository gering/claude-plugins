---
name: init
description: |
  Scaffolds the knowledge-system layout in a project: creates
  `.claude/knowledge/` and `.claude/rules/` directories with starter files,
  writes the auto-prime rule, and injects the knowledge index into
  `CLAUDE.md` as an `@`-reference. Idempotent — safe to re-run on existing
  projects; regenerates plugin-managed artifacts without touching user
  content.

  Use when: user wants to "set up knowledge", "initialize knowledge
  system", "bootstrap .claude/", "start documenting conventions", during
  new project adoption, or says "init" / "knowledge-system aufsetzen" /
  "knowledge anlegen".
user_invocable: true
---

# Initialize Knowledge System

Scaffold the knowledge system directory structure, the auto-prime rule, and the CLAUDE.md entry.

## Usage
`/init`
`/init "MyProject"`

## What this skill creates

Plugin-managed (regenerated on re-run, safe to delete):
- `.claude/rules/knowledge-system-usage.md` — always-active directives + index-load fallback
- A wrapped block inside `CLAUDE.md` (markers: `<!-- BEGIN knowledge-system -->` / `<!-- END knowledge-system -->`) that `@`-imports the knowledge index

User content (created empty once, never overwritten afterwards):
- `.claude/knowledge/_index.md`
- `.claude/knowledge/architecture/`, `features/`, `deployment/` (starter domains)

## Instructions

### 1. Check if already initialized

Look for `.claude/knowledge/_index.md`. If it exists, inform the user that the knowledge system is already set up. Ask whether to:
- **Re-run plugin-managed parts only** (rule file + CLAUDE.md block) — the default
- **Re-initialize everything** including the knowledge directory structure (only if they really want a clean slate — existing knowledge files are NOT deleted, only the starter `_index.md` is reset)

### 2. Create the directory structure

```
.claude/knowledge/
  _index.md
  architecture/
  features/
  deployment/
```

`.claude/knowledge/_index.md` starter template:
```markdown
# Knowledge Index

## Architecture
<!-- Add architecture knowledge files here -->
<!-- Example: - `architecture/overview.md` — High-level system architecture -->

## Features
<!-- Add feature knowledge files here -->
<!-- Example: - `features/auth.md` — Authentication and authorization flow -->

## Deployment
<!-- Add deployment knowledge files here -->
<!-- Example: - `deployment/ci-cd.md` — CI/CD pipeline and release process -->
```

Skip this step if `_index.md` already exists.

### 3. Write the auto-prime rule

Write `.claude/rules/knowledge-system-usage.md` with this exact content (overwrite any existing plugin-managed version):

```markdown
---
description: Knowledge-system usage — always-active directives and index-load fallback
---

<!-- This file is managed by the knowledge-system plugin. -->
<!-- Safe to delete if you uninstall the plugin. Edit freely if you customize. -->

# Project Knowledge System

## Auto-load fallback

Your context should already contain the knowledge index, injected via
`@.claude/knowledge/_index.md` from `CLAUDE.md` on session start.

**If you do not see a "Knowledge Index" section in your initial context,
read `.claude/knowledge/_index.md` with the Read tool once at the start of
the session before any `/query`.** Do not re-read on subsequent turns —
once per session is enough.

## When to use which command

- Before non-trivial changes to unfamiliar modules: `/query "<question>"` — retrieves relevant entries without dragging full files into context.
- After discovering a pattern, fix, or decision worth preserving: `/curate "<insight>" [file...]` — stores it in the right layer (rule vs knowledge).
- When the knowledge base feels stale, indexes drift, or cross-references look wrong: `/reindex` — runs a thorough QA pass (infrequent, Sonnet-1M-backed).

## Layers

- `.claude/rules/` — always-loaded directives (this file lives here)
- `.claude/knowledge/` — on-demand detailed knowledge, accessed via `/query`
- `CLAUDE.md` — project-level guidance, loaded every session
```

### 4. Update CLAUDE.md with the auto-prime block

Read the existing `CLAUDE.md` (create empty if absent). Locate the block delimited by:

```
<!-- BEGIN knowledge-system -->
...
<!-- END knowledge-system -->
```

- **If markers exist**: replace everything between them with the fresh block below.
- **If markers do not exist**: append the block to the end of `CLAUDE.md` (with a leading blank line).

The block (exact content):

```markdown
<!-- BEGIN knowledge-system -->
## Project Knowledge System

The project's knowledge index is auto-loaded below. Query detailed
entries with `/query`, store new insights with `/curate`, run a QA pass
with `/reindex`. See `.claude/rules/knowledge-system-usage.md` for the
full directives.

@.claude/knowledge/_index.md
<!-- END knowledge-system -->
```

Idempotency rule: never duplicate the block. Always replace in place when the markers are found.

### 5. Optional: ask for project details

Use `AskUserQuestion` sparingly — only if the user did not pass arguments and seems to want customization:
- Project name (used in description)
- Primary language/framework (suggests starter rule categories)

Skip if the user passed an argument or seems to want a quick setup.

### 6. Report what was done

List created/updated files and a compact next-steps block:

```
Created/updated:
- .claude/knowledge/_index.md                         (new)
- .claude/knowledge/{architecture,features,deployment}/  (new)
- .claude/rules/knowledge-system-usage.md             (updated — plugin-managed)
- CLAUDE.md                                           (block updated)

Next:
- Add architecture knowledge: create .claude/knowledge/architecture/overview.md
- Store a first learning: /curate "<insight>"
- Query knowledge: /query "<question>"
- Run a QA pass: /reindex
```
