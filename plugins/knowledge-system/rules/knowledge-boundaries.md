---
description: Where to store different types of project information
globs:
---

# Knowledge Boundaries

When adding or updating project information, put it in the right place:

| Content type | Where | Example |
|---|---|---|
| **Workflow checklists** (PR, deploy, review) | `CLAUDE.md` | PR checklist, backend reference |
| **Meta-rules** about CLAUDE.md itself | `CLAUDE.md` | "Keep CLAUDE.md short" |
| **Short code directives** (always needed) | `.claude/rules/` | "Use lazy getters", "No trailing commas" |
| **Detailed architecture/features** (query on demand) | `.claude/knowledge/` | Service init phases, address system flow |
| **User preferences/feedback** | Memory (`~/.claude/.../memory/`) | "Don't summarize at end of response" |

## Decision rules

- If it's a **do/don't for writing code** → Rule
- If it's a **process before shipping** (PR, deploy) → CLAUDE.md
- If it needs **more than 5 lines** of explanation → Knowledge
- If it's about **how the user wants to work** → Memory
- CLAUDE.md should NEVER contain code style rules — those belong in `.claude/rules/`
