---
name: reindex
description: |
  Thorough QA pass over the project knowledge base. Dispatches a
  background agent (Sonnet with large-context window) that rebuilds every
  `_index.md`, validates and proposes cross-references, backfills missing
  frontmatter from git history, updates `reindexedAt`, and appends a
  bullet-point run summary to `.claude/logs/reindex.md`. Intended as an
  occasional maintenance measure, not a hot-path command.

  Use when: user says "reindex", "run knowledge QA", "clean up knowledge",
  "check cross-references", "rebuild indexes", "verify knowledge base",
  before a release, after a large refactor, or periodically every few
  weeks. Also "knowledge aufräumen" / "index neu bauen" / "QA lauf".
user_invocable: true
---

# Reindex Knowledge Base

Run a thorough, cross-cutting QA pass over `.claude/knowledge/` as a background agent.

## Usage
`/reindex`
`/reindex --dry-run`   # report findings, do not write

## Instructions

### 1. Preconditions

- Check that `.claude/knowledge/_index.md` exists. If it does not: inform the user that the knowledge system is not initialized and suggest `/init`. Stop.
- Check that `.claude/logs/` exists — create it if missing.
- Read the plugin version from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` (`version` field). You will pass this to the agent so it can stamp `pluginVersion`.
- Capture today's date as `YYYY-MM-DD` (UTC) — pass this to the agent so the run is tagged with a consistent date.
- Parse `$ARGUMENTS`: if it contains `--dry-run`, set `{{DRY_RUN}}` to `true`; otherwise `false`. This must be resolved before substitution so the placeholder never lands literally in the agent prompt.

### 2. Dispatch the background agent

Use the `Agent` tool with:
- `subagent_type`: `general-purpose`
- `model`: `sonnet`
- `run_in_background`: `true`
- `description`: `Knowledge-base QA reindex`
- `prompt`: the full instruction block below, with `{{PLUGIN_VERSION}}`, `{{TODAY}}`, and `{{DRY_RUN}}` substituted.

### 3. Inform the user

Immediately report to the user (in the channel, not as a separate tool call):

> Reindex started as a background agent. It will walk the knowledge base, rebuild indexes, validate and propose cross-references, backfill frontmatter, and append a summary to `.claude/logs/reindex.md`. You'll be notified when the report is ready — typical run: 1–3 minutes.

Return control. Do not block.

### 4. When the agent reports back

The user will be notified automatically. When they bring the result back into the conversation (or ask about it), surface:
- The run summary (what changed, what was proposed, any warnings)
- Pointer to `.claude/logs/reindex.md` for the appended entry
- If the agent flagged **proposed cross-links** or **duplicates to review**, ask whether the user wants to act on them interactively

---

## Agent prompt template

Substitute `{{PLUGIN_VERSION}}`, `{{TODAY}}`, and `{{DRY_RUN}}` before passing.

```
You are running a thorough QA pass over a Claude Code knowledge base located at `.claude/knowledge/` in the current working directory. The knowledge-system plugin version is {{PLUGIN_VERSION}}. Today's date (UTC) is {{TODAY}}. Dry-run mode: {{DRY_RUN}} (if `true`, report findings but do NOT write any changes).

## Scope

Operate over every `.md` file under `.claude/knowledge/**`. Do NOT touch files under `.claude/rules/`, `.claude/logs/`, or anywhere else.

## Tasks

Perform all of the following in order:

### A. Rebuild `_index.md` files

For each directory under `.claude/knowledge/` that contains content files:
- Enumerate the content `.md` files (skip `_index.md` itself).
- Read each file's frontmatter `title` (fall back to the first H1 if no frontmatter).
- Rebuild the directory's `_index.md` with entries: `- <relative-filename> — <title>`.
- At the root, `.claude/knowledge/_index.md` should list top-level categories with one-line descriptions (keep existing descriptions if present, derive from content otherwise).

### B. Backfill frontmatter

For any knowledge file missing required fields, fill them in:
- `title`: derive from H1 or filename.
- `createdAt`: `git log --diff-filter=A --format=%aI -- <file> | tail -1 | cut -dT -f1`. Fallback: {{TODAY}}.
- `updatedAt`: `git log -1 --format=%cI -- <file> | cut -dT -f1`. Fallback: {{TODAY}}.
- `createdFrom`: reconstruct from the first commit that touched the file. Get the SHA with `git log --diff-filter=A --format=%H -- <file> | tail -1`, then resolve to a PR. Use the following cascade, first hit wins:
  1. **`gh` lookup** (robust across merge, squash, and rebase-merge modes): `gh pr list --search <sha> --state merged --limit 1 --json number --jq '.[0].number // ""'`. GitHub knows which PR a commit belongs to regardless of how it landed. If the query returns a number: write `createdFrom: "PR #<N>"`.
  2. **Squash-commit suffix** (for repos where `gh` is unavailable or offline): `git log -1 --format=%s <sha>` — if the subject ends with `(#<N>)`, extract `<N>` and write `createdFrom: "PR #<N>"`.
  3. **Merge-commit subject** (classic GitHub merge mode): scan `git log --merges --first-parent origin/<main> --format='%H %s'` for the commit whose second parent contains `<sha>`; if its subject matches `Merge pull request #([0-9]+)`, use that number.
  4. **Branch fallback**: if the SHA sits on a named branch other than main and none of the above resolved, write `createdFrom: "branch: <branch-name>"`.
  5. **Unresolved**: leave the field empty — do NOT guess. A later `/reindex` run may succeed once the branch is merged.
- `updatedFrom`: same cascade applied to the latest commit that touched the file (`git log -1 --format=%H -- <file>`).
- If `createdFrom` already holds a `"branch: <name>"` value AND the branch has since been merged to main, **upgrade** the value to `"PR #<N>"` by re-running the cascade. Same upgrade logic for `updatedFrom`.

Always add/update on every touched file:
- `reindexedAt`: {{TODAY}}.
- `pluginVersion`: {{PLUGIN_VERSION}}.

Date format: `YYYY-MM-DD` only (ISO-8601, date-only, UTC).

### C. Validate cross-references

For each knowledge file, find markdown links (`[text](path)`) that reference other files inside `.claude/knowledge/`:
- If the target file exists → OK.
- If the target file does not exist → flag as **dead reference** and mark it for the report. Do NOT auto-remove links without user confirmation.

### D. Propose new cross-references

Read every knowledge file. For any pair where:
- Both files discuss the same feature/component/concept (same proper nouns, same code paths), AND
- Neither links to the other,

propose a bidirectional link. Add the suggestion to the report — do NOT insert the links automatically. User reviews and accepts.

Use judgment: do not over-propose. Only suggest links that would meaningfully help navigation. Aim for quality over quantity.

### E. Detect likely duplicates

If two files cover nearly the same topic (>70% content overlap), flag them as **duplicate candidates** in the report. Do NOT auto-merge.

### F. Write the run log

Append a new heading to `.claude/logs/reindex.md`:

```
## {{TODAY}} — knowledge-system v{{PLUGIN_VERSION}}
- Rebuilt N _index.md entries
- Backfilled frontmatter on M files (list key ones)
- Validated X cross-references, Y dead references flagged
- Proposed Z new cross-links (see report)
- Flagged W duplicate candidates (see report)
- <short line of overall assessment>
```

Create `.claude/logs/reindex.md` if it does not exist — start it with the heading `# Reindex Log` followed by the first run entry.

In dry-run mode, do NOT write the log — only report what would have been written.

### G. Final report

Return a structured summary to the caller:

- Files processed: N
- `_index.md` files rebuilt: M
- Frontmatter fields backfilled: list of (file → fields)
- Dead references: list (file → broken link)
- Proposed cross-links: list of (file A ↔ file B, one-line rationale)
- Duplicate candidates: list of (file A ↔ file B, one-line reason)
- Overall: "clean" / "some maintenance applied" / "review needed"

Keep the report tight — bullet points, no prose walls.

## Rules

- If {{DRY_RUN}} is `true`: perform analysis but do NOT write any file changes. Report what WOULD be done.
- Never modify files outside `.claude/knowledge/` and `.claude/logs/reindex.md`.
- Never auto-apply duplicate merges or dead-link removals — always report for human review.
- Cross-link suggestions: only propose, never insert automatically.
- Frontmatter-less files: MUST be brought into schema form.
- Keep the log entry tight: bullet points, one heading per run, no prose.
```

## Model rationale

- **`subagent_type: general-purpose`** — needs tool access (Read, Write, Edit, Bash for git, Glob, Grep). General-purpose has the full toolkit.
- **`model: sonnet`** — this is a semantic reasoning task (judgment about duplicates, cross-links, frontmatter). Haiku would miss subtle overlaps. Opus is overkill for what is essentially structured analysis. Sonnet hits the quality/cost sweet spot.
- **`run_in_background: true`** — a thorough QA pass reads many files and reasons over the whole graph. Runs in 1–3 minutes. Blocking the session would be hostile. The user is notified on completion and can inspect the summary when convenient.
- **Why not session-in-context** — keeps the main context window clean. The agent reads ~dozens of files; the main session doesn't need that churn.

## Dry-run mode

`/reindex --dry-run` reports findings without writing:
- No file modifications
- No log entry
- Just the structured summary

Useful before running the first time on an uncurated knowledge base, or to preview what a cleanup run would change.
