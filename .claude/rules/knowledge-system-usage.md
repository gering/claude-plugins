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
