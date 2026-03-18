---
name: query
description: Query project knowledge about architecture, features, deployment, etc.
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

## Design rationale
- Knowledge agent (Haiku): fast and cheap for indexed knowledge lookup
- Explore agent (fallback): thorough codebase search when knowledge gaps exist
- Both run as subagents to keep the main context window clean
