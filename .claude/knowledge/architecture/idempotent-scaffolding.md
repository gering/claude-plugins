---
title: "Idempotent Scaffolding into Shared User Files"
createdAt: 2026-06-21
updatedAt: 2026-06-22
createdFrom: "branch: task/harden-init-scaffolding"
updatedFrom: "PR #14"
pluginVersion: 1.8.0
prime: true
---

# Idempotent Scaffolding into Shared User Files

Skills that **scaffold into a file the user also owns** (`/init` writing into
`CLAUDE.md`) or **create starter directories** must be safe to run on a repo
that already has hand-written content, and safe to re-run. Two failure modes
turned up dogfooding `/init`; both are footgun-class — fine in the happy path,
wrong on the realistic edge.

## Absorb pre-existing unmarked sections — don't just replace your own block

A managed block is wrapped in sentinel markers
(`<!-- BEGIN knowledge-system -->` / `<!-- END knowledge-system -->`) so re-runs
can replace it in place. But "replace if markers found, else append" duplicates
the heading on the **first** run against a repo where someone hand-wrote that
same section (`## Project Knowledge System`) without the markers.

Resolve in priority order:

1. **Markers present** → replace everything between them (markers included).
   Stop — don't also scan for stray unmarked sections.
2. **No markers, but an unmarked section with the managed heading exists** →
   *absorb* it: replace the whole section, from its heading line through to the
   next heading at the same or higher level (or EOF), with the marker-wrapped
   block.
3. **Neither** → append.

This stays idempotent because after an absorption the block now carries markers,
so the next run takes case 1 — no second absorption, no duplicate heading. Match
the unmarked section on the *exact heading text the block emits*; that heading is
the natural collision target.

## Create directories lazily — don't scaffold empty ones

Git does not track empty directories, so any starter dirs a skill `mkdir`s
(`architecture/`, `features/`, `deployment/`) vanish on the first commit — a
fresh clone never sees them, contradicting what the skill claims to create.

Prefer **lazy creation**: don't make the dirs at all. Let them materialize the
first time a file is written into them (the Write tool creates parent dirs on
demand). Keep the domains discoverable as **headings in an index file** rather
than as empty folders. A `.gitkeep` is the alternative but it's clutter that
needs its own idempotency guard (don't double-add on re-run) and lingers once
real content lands — lazy creation avoids both.

## Never overwrite user content — delegate reconciliation, don't duplicate it

A re-run must refresh only the plugin-managed parts and leave user-authored
files untouched. A "re-initialize everything" reset that blanks the index is a
footgun — it discards the user's curated entries. And building an "add the
missing, prune the stale" reconciler into the scaffolder duplicates logic the
maintenance command already owns: `/reindex` rebuilds `_index.md` from the files
on disk (adds missing entries, drops deleted ones). So when scaffolded content
can drift out of sync with reality, **point at the dedicated reconciler instead
of reimplementing it or resetting destructively**. Removing the special case
beats adding one.

See [[skill-composition]] for the marker/format-contract mechanics these blocks
rely on.
