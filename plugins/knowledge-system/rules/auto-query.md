---
description: Automatically check project knowledge before diving into unfamiliar code
globs:
---

# Auto-Query

## When to check knowledge

Before diving into code, check if `.claude/knowledge/_index.md` exists. If it does, read it and look for entries relevant to your current task. Read matching knowledge files before exploring the codebase.

You MUST check knowledge at these moments:

1. **Starting work in an unfamiliar area** — Before reading source code in a part of the codebase you haven't touched in this conversation, check if there's documented knowledge about it. This saves time and avoids rediscovering what's already known.

2. **User asks "how does X work"** — Check knowledge FIRST, before grepping through code. If knowledge exists, answer from it. Only explore code if knowledge is missing or incomplete.

3. **Before making architectural decisions** — If your change affects how components interact, check if there's documented architecture knowledge that should inform your approach.

4. **When you encounter unexpected behavior** — Before debugging from scratch, check if there's a known gotcha or edge case documented in knowledge.

## How to query (inline — do NOT use /query)

1. Read `.claude/knowledge/_index.md`
2. Identify 1-3 relevant entries based on the topic
3. Read those files
4. Use the knowledge to inform your work

Do NOT read the entire knowledge base — be selective. The index tells you what exists.

## When NOT to query

- Trivial changes (typo, formatting, rename)
- You already have full context from earlier in the conversation
- The change is isolated and self-contained (e.g., fixing a single function you already understand)
- No `.claude/knowledge/` directory exists
