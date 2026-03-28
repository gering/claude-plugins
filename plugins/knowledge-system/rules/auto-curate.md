---
description: Automatically curate knowledge and rules at key moments
globs:
---

# Auto-Curate

## When to curate

You MUST check for curate-worthy learnings at these moments:

1. **Before a push** — Review what changed in the commits being pushed. Ask yourself: "Would future-me benefit from knowing why this was done this way?" If yes, curate before pushing.

2. **Before creating a PR** — Review the full diff. Curate any non-obvious patterns, architectural decisions, or gotchas that emerged during the work.

3. **After a non-trivial bug fix** — If the root cause was surprising or the fix required understanding a non-obvious interaction, curate it.

4. **When the user corrects your approach** — If the user says "don't do X" or "always do Y", check if it should become a rule.

5. **When you notice outdated knowledge** — If a knowledge file contains wrong details (renamed files, changed behavior), update it immediately.

## How to curate (inline — do NOT use /curate)

Decide where it belongs (see `rules/knowledge-boundaries.md`):
- Short code directive (do/don't) → `.claude/rules/<topic>.md`
- Workflow checklist (PR, deploy) → `CLAUDE.md`
- Detailed feature/architecture → `.claude/knowledge/<category>/<topic>.md`
- User preference/feedback → Memory

Then:
1. Check if an existing file already covers the topic (read `.claude/rules/` or `.claude/knowledge/_index.md`)
2. Update the existing file, or create a new one
3. If a new knowledge file was created, update `.claude/knowledge/_index.md`
4. Briefly tell the user what you curated and where

## What to curate

- Architectural decisions and their rationale
- Non-obvious patterns or conventions that emerged
- Gotchas, edge cases, and workarounds with context
- Integration details between components

## What NOT to curate

- Trivial changes (rename, typo fix, formatting)
- Volatile values (version numbers, counts, thresholds)
- Security-sensitive details (API keys, secrets, auth internals)
- Things already obvious from the code itself

## Content guidelines

- Describe **how things work**, not current exact values
- Include **why**, not just what — the reasoning is what helps future-you
- Keep it concise — if it's more than a paragraph, it's probably knowledge, not a rule
