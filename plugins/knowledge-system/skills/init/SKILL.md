---
name: init
description: |
  Scaffolds `.claude/knowledge/` + `.claude/rules/`, writes the usage
  rule, injects the index into `CLAUDE.md`. Idempotent.
  Trigger: "init knowledge", "set up knowledge system", "bootstrap .claude/".
user_invocable: true
---

# Initialize Knowledge System

Scaffold the knowledge system directory structure, the usage rule, and the CLAUDE.md entry.

## Usage
`/init`
`/init "MyProject"`

## What this skill creates

Plugin-managed (regenerated on re-run, safe to delete):
- `.claude/rules/knowledge-system-usage.md` — index-load fallback + command pointers
- A wrapped block inside `CLAUDE.md` (markers: `<!-- BEGIN knowledge-system -->` / `<!-- END knowledge-system -->`) that `@`-imports the knowledge index

User content (created once, never overwritten afterwards):
- `.claude/knowledge/_index.md` — lists the starter domains (Architecture /
  Features / Deployment) as headings

The domain directories (`architecture/`, `features/`, `deployment/`) are **not**
scaffolded empty — Git would not track them anyway. Each materializes the first
time `/curate` or `/migrate` writes a knowledge file into it.

## Instructions

### 1. Check if already initialized

Look for `.claude/knowledge/_index.md`. If it exists, inform the user that the knowledge system is already set up. Ask whether to:
- **Re-run plugin-managed parts only** (rule file + CLAUDE.md block) — the default
- **Re-initialize everything** including the knowledge directory structure (only if they really want a clean slate — existing knowledge files are NOT deleted, only the starter `_index.md` is reset)

### 2. Create the knowledge index

Create `.claude/knowledge/` and write `_index.md` — the only file scaffolded
here. Do **not** create empty `architecture/`, `features/`, `deployment/`
directories: Git does not track empty directories, so they would vanish on the
first commit and a fresh clone would never see them. The three starter domains
live as headings inside `_index.md`; each directory materializes the first time
`/curate` or `/migrate` writes a file into it (the Write tool creates the parent
directory on demand).

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

### 3. Write the usage rule

Write `.claude/rules/knowledge-system-usage.md` with this **exact** content (overwrite any existing plugin-managed version). The marker carries a **template version** (`template-v1` below) — a counter that is bumped **only when the template content here changes**, independent of the plugin version. `/reindex` reads this same number and flags any project copy whose marker differs. (Maintainer note: when you edit the template below, increment `template-vN` here; that single number is the source of truth `/reindex` compares against.)

```markdown
---
description: Knowledge system — when to consult/curate, index-load fallback, commands
---

<!-- knowledge-system-usage template-v1 — managed by the plugin; re-run /init to refresh (/reindex flags staleness). -->
<!-- Safe to delete on uninstall. Delete the marker line above to opt out of staleness checks. -->

# Project Knowledge System

## Auto-load fallback

The knowledge index is injected via `@.claude/knowledge/_index.md` from
`CLAUDE.md` on session start. **If you do not see a "Knowledge Index" section
in your initial context, read `.claude/knowledge/_index.md` once with the Read
tool** before any `/query`. Once per session is enough — do not re-read.

## Consult before diving in

Check the index for relevant entries before exploring code when: starting in an
area untouched this conversation; the user asks "how does X work" (check
knowledge before grepping); a change affects how components interact; or you hit
unexpected behavior (look for a documented gotcha first).

- **Index already names the file** → read it inline; no subagent for a known path.
- **Open-ended question** → `/query "<question>"` — a cheap Haiku subagent
  answers without pulling full files into context.

Be selective — pull only the few relevant entries the index points to, never bulk-read the whole base. Skip for trivial or self-contained changes, or when you already have the context.

## Curate at key moments

Store a learning with `/curate "<insight>" [file...]` (it picks the right layer
and maintains frontmatter) when: about to push or open a PR (non-obvious
patterns, decisions, gotchas in the diff); after a surprising bug fix; the user
corrects your approach ("always do Y"); or you notice stale knowledge (fix it
promptly — wrong docs mislead a later `/query`). Capture the *why*, not volatile
values; skip trivia, secrets, and the obvious.

## Other commands

- `/prime [topic|--full]` — load foundational docs (architecture + overviews)
  into context for real architectural work; beyond the always-loaded index.
- `/reindex` — thorough QA pass when indexes drift or cross-refs look stale.
- `/backfill-knowledge` — mine merged PR history for missing learnings (run
  `/reindex` first so the idempotency check has up-to-date origin metadata).
```

### 4. Update CLAUDE.md with the auto-prime block

Read the existing `CLAUDE.md` (create empty if absent). Resolve where the
managed block goes, in this priority order:

1. **Markers present** — locate the block delimited by
   `<!-- BEGIN knowledge-system -->` / `<!-- END knowledge-system -->` and
   replace everything between them (markers included) with the fresh block
   below. Stop here — do not also scan for stray unmarked sections.
2. **No markers, but an unmarked `## Project Knowledge System` section exists** —
   a hand-written section predating this plugin would otherwise leave a duplicate
   heading. Absorb it: replace the whole section — from its
   `## Project Knowledge System` heading line through everything up to (but not
   including) the next heading at the same or higher level (a line starting with
   `## ` or `# `), or end of file if none follows — with the marker-wrapped block
   below. Match the heading on its exact text `## Project Knowledge System` (the
   heading this block emits); deeper `###` headings inside the section are part
   of it, not boundaries.
3. **Neither** — append the block to the end of `CLAUDE.md` (with a leading
   blank line).

The block (exact content):

```markdown
<!-- BEGIN knowledge-system -->
## Project Knowledge System

The project's knowledge index is auto-loaded below. Query detailed
entries with `/query`, store new insights with `/curate`, run a QA pass
with `/reindex`. See `.claude/rules/knowledge-system-usage.md` for when
to use each and the full command list.

@.claude/knowledge/_index.md
<!-- END knowledge-system -->
```

Idempotency rules:
- Never duplicate the block — always replace in place when markers are found.
- After an absorption (case 2) the block is now marker-wrapped, so a re-run
  takes case 1 and replaces in place: no second absorption, no duplicate heading.
- When you absorbed an unmarked section, say so in the report — e.g.
  `CLAUDE.md (absorbed existing "## Project Knowledge System" section)`.

### 5. Optional: ask for project details

Ask the user sparingly — only if they did not pass arguments and seem to want customization:
- Project name (used in description)
- Primary language/framework (suggests starter rule categories)

Skip if the user passed an argument or seems to want a quick setup.

### 6. Report what was done

List created/updated files and a compact next-steps block:

```
Created/updated:
- .claude/knowledge/_index.md                         (new)
- .claude/rules/knowledge-system-usage.md             (updated — plugin-managed)
- CLAUDE.md                                           (block updated)

Next:
- Add architecture knowledge: create .claude/knowledge/architecture/overview.md
- Store a first learning: /curate "<insight>"
- Query knowledge: /query "<question>"
- Prime context with the foundational docs: /prime
- Run a QA pass: /reindex
- Optional: show `[cks rules|knowledge]` in your status line — `/knowledge-system:statusline install`
```
