---
name: init
description: |
  Scaffolds the knowledge-system layout in a project: creates
  `.claude/knowledge/` and `.claude/rules/` directories, initial index
  files, and a root `CLAUDE.md` pointer if missing. Idempotent — safe to
  re-run on existing projects without overwriting content.

  Use when: user wants to "set up knowledge", "initialize knowledge
  system", "bootstrap .claude/", "start documenting conventions", during
  new project adoption, or says "init" / "knowledge-system aufsetzen" /
  "knowledge anlegen".
user_invocable: true
---

# Initialize Knowledge System

Scaffold the knowledge system directory structure and starter files in the current project.

## Usage
`/init`
`/init "MyProject"`

## Instructions

1. **Check if already initialized**: Look for `.claude/knowledge/_index.md`. If it exists, inform the user the knowledge system is already set up and ask if they want to re-initialize.

2. **Create the directory structure**:
   ```
   .claude/knowledge/
     _index.md
     architecture/
     features/
     deployment/
   ```

3. **Create `_index.md`** with this starter template:
   ```markdown
   # Knowledge Index

   ## Architecture
   <!-- Add architecture knowledge files here -->
   <!-- Example: - `architecture/overview.md` — High-level system architecture -->

   ## Features
   <!-- Add feature knowledge files here -->
   <!-- Example: - `features/auth.md` — Authentication and authorization flow -->

   ## Deployment
   <!-- Add deployment knowledge files here -->
   <!-- Example: - `deployment/ci-cd.md` — CI/CD pipeline and release process -->
   ```

4. **Add a section to CLAUDE.md** (create CLAUDE.md if it doesn't exist):
   - First, read the existing CLAUDE.md to avoid duplicating content
   - Only add the section if it doesn't already exist
   - Append this section:
   ```markdown

   ## Project Knowledge System
   - **Rules** (`.claude/rules/`): Always active — coding style, patterns, dos/don'ts
   - **Knowledge** (`.claude/knowledge/`): On demand — query with `/query`
   - **Curate**: Use `/curate` to store new learnings after implementing features or fixing bugs
   ```

5. **Optionally ask the user** (using AskUserQuestion) for project details:
   - Project name (used in descriptions)
   - Primary language/framework (to suggest starter rule categories)
   - Skip if the user passed arguments or seems to want a quick setup

6. **Report what was created**: List all directories and files created, and suggest next steps:
   - "Add your first rule: create `.claude/rules/<topic>.md`"
   - "Add architecture knowledge: create `.claude/knowledge/architecture/overview.md`"
   - "Query knowledge anytime with `/query`"
   - "Store learnings with `/curate`"
