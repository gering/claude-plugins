---
name: query
description: |
  Retrieves relevant project knowledge on demand — architecture overviews,
  feature specifications, deployment procedures, recurring patterns, and
  past decisions. Searches `.claude/knowledge/` first, then `.claude/rules/`,
  surfacing the most relevant entries with file paths for follow-up reading.

  Use when: user asks "how does X work here", "what's the convention for Y",
  "show me the deployment process", "what do we know about Z", before
  making non-trivial changes to unfamiliar areas of the codebase, or says
  "query" / "was weißt du über X" / "wie funktioniert X hier" / "knowledge
  zu X".
user_invocable: true
---

# Query Knowledge

Search the project knowledge base for information about architecture, features, patterns, deployment, etc.

## Usage
`/query "How does the notification system work?"`
`/query "What is the service architecture?"`

## Instructions

1. Parse the user's query string

2. Use the **Agent tool** to launch the `Knowledge` agent with the query as prompt:
   - Agent name: `Knowledge` (defined in `agents/knowledge.md`)
   - The agent reads `.claude/knowledge/_index.md` to find relevant files
   - The agent reads only the matching files (not the entire knowledge base)
   - The agent returns a concise answer with file references

3. **If the Knowledge Agent found an answer** → present it to the user

4. **If the Knowledge Agent found nothing** → launch an **Explore agent** to search the project source code:
   - Use the Agent tool with `subagent_type: Explore`
   - Pass the same query
   - Present the explore agent's findings to the user
   - Mention that this came from code exploration, not documented knowledge

## Model rationale

- **Knowledge agent** runs on **Haiku**. The task is bounded lookup — read the index, pick the relevant files, summarize. No reasoning over ambiguous requirements, no architectural judgment. Haiku delivers this in <2s at ~1/10 the cost of Sonnet/Opus. Since `/query` is invoked often (potentially before every non-trivial change), cost compounds — Haiku keeps it usable.
- **Explore agent (fallback)** does NOT override the model. Codebase exploration is open-ended: which files to read, what's relevant, when to stop. That needs session-model reasoning quality. It also runs rarely (only when knowledge has a gap), so cost is negligible.
- **Why subagents, not in-context**: keeps the main context window clean. `/query` can return a dense 5-line answer instead of dragging 10 file contents into the primary session.
