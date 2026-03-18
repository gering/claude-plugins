---
description: Automatically curate knowledge and rules when patterns change
globs:
---

# Auto-Curate

After completing work that changes established patterns or reveals new best practices:

1. **Pattern changed** → Update the relevant rule or knowledge file directly (see `rules/knowledge-boundaries.md` for where it belongs). Update `_index.md` if a new knowledge file is created.

2. **Outdated knowledge discovered** → Update the relevant file in `.claude/knowledge/` directly.

3. **User feedback becomes a rule** → Add or update the rule in `.claude/rules/` directly.

4. **Bug fix reveals a pattern** → If the root cause or fix is something future-you should know, curate it. If it's trivial, skip it.

5. **Knowledge drift** → When you notice that a knowledge file contains outdated details (wrong counts, renamed files, changed behavior), update it immediately. Knowledge files have no automatic sync — they drift unless actively maintained.

## Content guidelines

- **No volatile values**: Don't hardcode version numbers, counts, or thresholds — describe the pattern instead.
- **No security-sensitive details**: Don't document API keys, secrets, or auth internals in plaintext.
- **Prefer patterns over snapshots**: Describe *how things work*, not *current exact values*.

Don't curate trivial changes. Only curate when future-you would benefit from knowing this.
