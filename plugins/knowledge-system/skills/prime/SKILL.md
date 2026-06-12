---
name: prime
description: |
  Loads the foundational knowledge docs (architecture + overviews) straight
  into context so the session starts informed — the map, not everything.
  Trigger: "prime the context", "load the architecture", "prime knowledge".
user_invocable: true
---

# Prime Context

Pull the **foundational** knowledge — architecture and overview docs — directly into the current session context, so Claude starts a working session already oriented. This is the deliberate, deeper companion to the auto-prime mechanism (which only injects the `_index.md`).

## Usage

`/prime`              # foundational docs: architecture/ + overviews + `prime: true`
`/prime <topic>`      # foundational docs scoped to a domain/keyword
`/prime --full`       # the entire knowledge base (with a budget warning)

## Why this runs IN-CONTEXT (not as a subagent)

`/query` deliberately uses a Haiku subagent to keep the main window clean — it answers a question without dragging file contents in. `/prime` is the opposite by design: the whole point is to land the important content **in the main session context**, so the model reads the selected files itself with the Read tool. No subagent.

## Instructions

### 1. Preconditions

- Check that `.claude/knowledge/_index.md` exists. If not: tell the user the knowledge system isn't initialized and suggest `/init`. Stop.
- Parse `$ARGUMENTS`:
  - `--full` → full mode.
  - any other non-flag text → treat as a `<topic>` scope (free text / domain name).
  - empty → default (foundational) mode.

### 2. Read the index

Read `.claude/knowledge/_index.md` to see the categories and entries. If a directory has its own `_index.md`, those are cheap to skim too. Use the index to build the candidate file list — do **not** read every content file yet.

### 3. Select the candidate set

**Default mode** — a file is a candidate if any of these hold:

- It lives under `.claude/knowledge/architecture/`.
- Its filename or `title` signals a system-level overview: `overview`, `architecture`, `system`, `design` (e.g. `features/billing-overview.md`, a title like "Payments — System Overview").
- Its frontmatter has `prime: true`.

Then **exclude** any file whose frontmatter has `prime: false` — that is an explicit opt-out and wins over the heuristics above.

**`<topic>` mode** — same idea, but keep only candidates that relate to the topic (match the topic against path, `title`, and index description). Stay at the foundational/overview level for that topic — load its overview and architecture docs, not every detail file under it.

**`--full` mode** — every `.md` under `.claude/knowledge/**` (skip `_index.md` files). Warn if this is large (see budget).

### 4. Apply the budget

Loading context isn't free. Target a soft budget of **~15 files / ~30k tokens** for default and topic modes.

- If the candidate set fits → load all of it.
- If it exceeds the budget → prioritize in this order and load until the budget is hit:
  1. explicit `prime: true`
  2. `architecture/`
  3. overview-titled docs
  4. the rest
- Whatever didn't fit is **reported as skipped** (see step 6), never silently dropped.
- `--full` mode: if the base clearly blows past the budget, surface the estimated size first and ask the user to confirm before reading everything. Otherwise proceed.

### 5. Load the selected files into context

Read each selected file with the **Read tool**, in a sensible order (architecture first, then overviews, then the rest). The content now lives in the main context — that is the deliverable. Do **not** summarize away the content; the point is to have it available verbatim for the rest of the session.

### 6. Report what was primed

End with a compact summary — what is now in context, grouped, plus anything skipped:

```
Primed <N> docs into context (~<tokens estimate>):
  architecture/  — overview.md, payments.md, auth-sessions.md
  overviews      — features/billing-overview.md
  flagged        — deployment/release-process.md  (prime: true)

Skipped (over budget): features/webhooks.md, features/exports.md
  → /query them on demand, or /prime --full to load everything.

Session is primed. Ask anything about the architecture without a fresh /query.
```

If nothing matched the heuristic at all (e.g. brand-new knowledge base, no architecture docs, no `prime` flags): say so, and suggest either `/curate` to start capturing or `/prime --full` to load whatever exists.

## Relationship to auto-prime, `/query`, and the `prime` flag

- **Auto-prime** (set up by `/init`): injects only `@.claude/knowledge/_index.md` into every session via `CLAUDE.md`. Cheap, always-on, index only — Claude knows *what exists*.
- **`/prime`** (this skill): on demand, loads the actual *content* of the foundational docs — Claude knows *how the system fits together*. Run it at the start of a working session when you're about to touch real architecture.
- **`/query`**: a targeted lookup that stays out of the main context (subagent). Use it for a specific question; use `/prime` to set the stage broadly.
- **The `prime: true|false` frontmatter flag**: the override. `/curate` sets it when creating a doc (foundational → `true`, narrow detail → `false`), and `/reindex` backfills it wherever it's missing. Set it by hand any time to force a doc in or out of `/prime`.
