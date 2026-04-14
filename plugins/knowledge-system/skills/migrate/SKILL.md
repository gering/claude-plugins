---
name: migrate
description: |
  Migrates a project's legacy ByteRover-based knowledge into the native
  `knowledge-system` layout. Reads existing ByteRover entries, reclassifies
  each as a rule (always-active) or knowledge (on-demand), and writes to
  the standard `.claude/` structure.

  Use when: user has an existing ByteRover-based project and wants to
  "migrate knowledge", "switch to native knowledge system", "move from
  ByteRover", or says "migrate" / "byterover migrieren" in a knowledge
  context.
user_invocable: true
---

# Migrate from ByteRover

Convert an existing ByteRover knowledge base to the native knowledge system.

## Usage
`/migrate`
`/migrate --dry-run`

## Instructions

### 1. Check for ByteRover data

Look for `.brv/context-tree/` directory. If it doesn't exist, inform the user that no ByteRover data was found and suggest using `/init` instead.

### 2. Read all ByteRover knowledge files

- Recursively read all `.md` files in `.brv/context-tree/`
- **Skip** files named `context.md` — these are ByteRover index files, not actual knowledge
- Track the total count of files found

### 3. Deduplicate

ByteRover creates many near-duplicate files. For each file:
- Compare content similarity (title + key paragraphs)
- If two files cover the same topic with >80% overlap, keep the longer/more detailed one
- Track how many duplicates were skipped

### 4. Categorize each file

For each unique file, decide where it belongs:

| Content pattern | Destination |
|----------------|-------------|
| Short directive, do/don't, pattern rule (<5 lines of content) | `.claude/rules/<topic>.md` |
| Detailed architecture, system design | `.claude/knowledge/architecture/<topic>.md` |
| Feature description, user flow | `.claude/knowledge/features/<topic>.md` |
| Deployment, CI/CD, release process | `.claude/knowledge/deployment/<topic>.md` |
| Data models, schemas | `.claude/knowledge/models/<topic>.md` |
| Monitoring, debugging, support | `.claude/knowledge/monitoring/<topic>.md` |
| Everything else | `.claude/knowledge/general/<topic>.md` |

When creating rule files, use proper frontmatter:
```markdown
---
description: <what this rule is about>
globs: <relevant file patterns, if applicable>
---
```

### 5. Ensure directory structure exists

Create `.claude/knowledge/` and all necessary subdirectories. Run `/init` logic if the knowledge system hasn't been set up yet.

### 6. Build `_index.md`

Create or update `.claude/knowledge/_index.md` with entries for all migrated knowledge files:
```markdown
# Knowledge Index

## Architecture
- `architecture/topic.md` — One-line description

## Features
- `features/topic.md` — One-line description
```

### 7. Clean up ByteRover configuration

- Read `.claude/settings.local.json`
- Remove any ByteRover hooks (look for entries referencing `brv`, `byterover`, or `.brv/`)
- Remove ByteRover permission entries
- Write the cleaned settings back

### 8. Update CLAUDE.md

- Read the existing CLAUDE.md
- Remove or replace any ByteRover section (look for "ByteRover", "brv", "context-tree")
- Add the knowledge system section if not present (same as `/init`)

### 9. Dry run mode

If the user passed `--dry-run`:
- Do all the analysis (steps 1-4) but don't write any files
- Report what WOULD be created/changed
- This lets the user review before committing

### 10. Report summary

Present a summary table:
```
Migration complete:
- Rules created: X
- Knowledge files created: Y
- Duplicates skipped: Z
- ByteRover config removed: Yes/No
- CLAUDE.md updated: Yes/No
```

List all created files so the user can review them.
