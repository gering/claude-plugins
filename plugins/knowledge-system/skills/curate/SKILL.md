---
name: curate
description: Store new learnings in project knowledge or rules
user_invocable: true
---

# Curate Learning

Store a new pattern, decision, or learning in the project knowledge system.

## Usage
`/curate "<description>" [file1 file2 ...]`

## Instructions

1. Parse the user's input:
   - The quoted string is the **description** of what was learned
   - Any file paths after the description are **reference files**

2. If reference files are provided, read them to understand context

3. **Decide where it belongs** (see `rules/knowledge-boundaries.md`):
   - Short code directive (do/don't) → `.claude/rules/<topic>.md`
   - Workflow checklist (PR, deploy) → `CLAUDE.md`
   - Detailed feature/architecture → `.claude/knowledge/<category>/<topic>.md`

4. **Check if an existing file covers this topic**:
   - For rules: check files in `.claude/rules/`
   - For knowledge: read `.claude/knowledge/_index.md`

5. **Update or create**:
   - Existing file covers topic → Edit to add/update
   - New topic → Create new file with proper structure
   - If new knowledge file → update `_index.md`

6. **Content quality checks** before writing:
   - **No volatile values**: Don't hardcode version numbers, counts, or thresholds that change with the code. Describe the pattern instead (e.g. "version is incremented on schema changes" not "version = 11").
   - **No security-sensitive details**: Don't document API keys, secrets, or auth internals in plaintext. Describe the approach without exposing specifics.
   - **Prefer patterns over snapshots**: Describe *how things work*, not *current exact values*. Values change — patterns persist.

7. Report what was stored and where

## Why this runs in-context (not as agent)
Curate needs the conversation context to know what just happened — which files changed, which patterns were applied, which learnings emerged. A subagent would start fresh and lose all of that.
