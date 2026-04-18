---
name: backfill-knowledge
description: |
  Mines merged PR history on main for significant learnings that have
  not yet been curated. Dispatches a background agent (Sonnet) that
  reads each PR's title, body, commit messages, and diff, and judges
  whether the change introduces a new feature, an architecture change,
  or a major insight worth preserving. Small bug fixes, refactors,
  dependency bumps, and typo/chore PRs are explicitly excluded.
  Survivors return as a single batch report with short learning
  descriptions and target-file recommendations. User approves by number
  range, then `/curate` runs on each approved candidate with the PR
  context as input. Skips PRs already represented in the knowledge base
  (via `createdFrom` / `updatedFrom` scan and a persistent log).

  Use when: user says "backfill knowledge", "mine PR history", "curate
  from history", "was fehlt noch im knowledge", "alte PRs kurieren",
  "knowledge aus alten PRs bauen", wants to bootstrap knowledge on an
  existing project, or after a long curation gap. Usually paired with
  `/reindex` first (so existing metadata is up to date for the
  idempotency check).
user_invocable: true
---

# Backfill Knowledge from PR History

Retroactively mine merged PRs for significant, durable learnings. Interactive: proposes a batch, you approve a selection, then `/curate` runs on each.

## Usage
`/backfill-knowledge`                    # all unprocessed merged PRs on main
`/backfill-knowledge --last 20`          # only the last 20 merged PRs (bounded run)
`/backfill-knowledge --pr 42`            # single PR (no batch phase)
`/backfill-knowledge --since 2026-01-01` # merged PRs since a date
`/backfill-knowledge --dry-run`          # report only, do not touch log or knowledge

## Instructions

### 1. Preconditions

- Check `gh` CLI: `command -v gh >/dev/null || { echo "gh CLI not installed"; exit; }` and `gh auth status`. Stop with a clear message if either fails.
- Check `.claude/knowledge/_index.md` exists. If not: the knowledge system is not initialized — stop and suggest `/init`.
- Ensure `.claude/logs/` exists (create if missing).
- Read plugin version from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`.
- Capture today's date: `YYYY-MM-DD` (UTC).
- Parse `$ARGUMENTS`: extract `--last N`, `--pr N`, `--since YYYY-MM-DD`, `--dry-run`. Mutually exclusive where it matters (`--pr` skips all filtering).

### 2. Build the candidate PR list

- Determine the default branch name (main/master) via `git symbolic-ref refs/remotes/origin/HEAD`.
- Fetch merged PRs:
  - `--pr N`: single PR, skip the list-building step.
  - `--last N`: `gh pr list --state merged --base <default> --limit N --json number,title,mergedAt`
  - `--since DATE`: `gh pr list --state merged --base <default> --search "merged:>=<DATE>" --limit 200 --json number,title,mergedAt`
  - Default: `gh pr list --state merged --base <default> --limit 200 --json number,title,mergedAt` (200 is the practical upper bound per run — users can run `--since` to cover older stretches).

### 3. Load the already-processed set (idempotency)

Skip PRs already represented:

- **From existing knowledge**: grep frontmatter fields only — `createdFrom` and `updatedFrom` — to avoid matching prose mentions like "the pattern emerged in PR #42" in the body:
  ```bash
  grep -rhE '^(createdFrom|updatedFrom):.*PR #[0-9]+' .claude/knowledge/ 2>/dev/null \
    | grep -oE 'PR #[0-9]+' \
    | sort -u
  ```
  Extract numeric set A.
- **From the log**: parse `.claude/logs/backfill-knowledge.md` (if it exists) for all bulleted PR numbers under "Accepted", "Never", and "Skipped — already curated" sections. Extract numeric set B.
- **Processed set** = A ∪ B. Filter these PRs out of the candidate list before dispatching the agent.

If `--pr N` was passed and N is already in the processed set: warn the user, ask whether to re-process (y) or abort (n).

### 4. Dispatch the background agent

If the remaining candidate list is empty, inform the user and stop — nothing to do.

Otherwise, use the `Agent` tool with:
- `subagent_type`: `general-purpose`
- `model`: `sonnet`
- `run_in_background`: `true`
- `description`: `Backfill knowledge — judge PR history`
- `prompt`: the full instruction block below, with `{{PLUGIN_VERSION}}`, `{{TODAY}}`, `{{PR_LIST}}` (JSON array of numbers), and `{{BASE_BRANCH}}` substituted.

Inform the user in the channel:

> Backfill started as a background agent. It will read PR #<first>–#<last> (title, body, commits, diff), judge each against the strict significance bar (new features / architecture / major insights only), and come back with a single approval report. Typical run: 2–10 minutes depending on PR count.

Return control. Do not block.

### 5. When the agent reports back

The agent's structured output contains:
- `accepted`: list of `{pr, title, learning, target}` objects
- `rejected`: list of `{pr, title, reason}` objects (label + one-line rationale)
- `errors`: list of `{pr, message}` (PRs that could not be fetched / parsed)

Present it as a single report to the user (see "Report format" below). Then wait for their selection.

### 6. Approval and selection

Prompt the user with the numbered `accepted` list and the compressed rejected counts. Accept:

- `y` or `all` → approve all accepted candidates
- `1,3,5` → approve those numbers
- `1-4` → approve range
- `n` or empty → approve nothing (nothing gets curated; nothing goes into the `never`-bucket either)
- `never 2,4` → mark those as "never re-propose" in the log and approve nothing else
- `c` → cancel (no log update at all)

### 7. Curate each approved candidate

For each approved candidate, invoke `/curate` with:
- `"<learning>"` — the one-line learning description from the agent
- Reference files: the 1–3 most relevant file paths from the PR's diff (the agent provides these in its output)
- `--origin "PR #<number>"` so `createdFrom` / `updatedFrom` get stamped with the PR reference, not the current branch

Run sequentially (simpler for the user to read the output). If a curate call fails, note it and continue with the rest.

### 8. Update the log

Append to `.claude/logs/backfill-knowledge.md` (create the file with `# Backfill Log` heading if missing):

```markdown
## <{{TODAY}}> — knowledge-system v<{{PLUGIN_VERSION}}> — range: <scope description>

Accepted (<N>):
- PR #<num> → <target-file> (new|updated)
- ...

Skipped — not significant (<N>):
- #<nums separated by commas, wrapped at 80 cols>

Skipped — already curated (<N>):
- #<nums>

Never (<N>):
- #<nums, from user's "never"-selection>

Errors (<N>):
- PR #<num>: <message>
```

Omit sections with count 0. `--dry-run` writes nothing.

### 9. Summary

Report to the user:
- Accepted count + each resulting knowledge file
- Skipped counts by bucket
- Pointer to the log entry

---

## Agent prompt template

Substitute `{{PLUGIN_VERSION}}`, `{{TODAY}}`, `{{PR_LIST}}`, `{{BASE_BRANCH}}`.

```
You are judging whether each of the provided GitHub pull requests introduces knowledge worth preserving in a Claude Code knowledge base located at `.claude/knowledge/` in the current working directory. The base branch is `{{BASE_BRANCH}}`. Plugin version: {{PLUGIN_VERSION}}. Today: {{TODAY}}.

## The quality bar (strict)

ACCEPT a PR only if it matches one of these:

1. **New user-facing feature** — a capability the project did not have before. Not "add a button" or "extend endpoint X with field Y" — something that changes what the product can do. Example: "add multi-tenant billing", "introduce event sourcing for orders", "add webhook delivery".
2. **Architecture change** — a shift in how components relate, data flows, or systems communicate. Example: "migrate internal RPC from REST to gRPC", "move authentication to a dedicated service", "switch persistence from single-node Postgres to Citus".
3. **Major insight** — a durable finding that will matter in 6+ months: a surprising constraint, a non-obvious invariant discovered through debugging, an explicit design rationale ("we chose X over Y because Z"). Not "fixed a race condition in Q" — unless the fix encodes a pattern the team must apply elsewhere.

REJECT otherwise. The following are explicit non-qualifiers even if they contain *some* information:

- Small bug fixes (even clever ones) — `bug-fix`
- Refactors without behavioral change — `refactor`
- Test additions or coverage improvements — `tests-only`
- Dependency updates — `deps-bump`
- Style, typo, docs-only cleanups — `chore`
- Work-in-progress, rollback, or revert PRs — `other`
- Anything the PR body is too thin to judge — `insufficient-context`
- PRs where the change is real but feels ephemeral / unlikely to matter later — `unclear-value`

When in doubt, reject. A bloated knowledge base is worse than a small one.

## Input

For each PR number in the list `{{PR_LIST}}`, fetch:

1. `gh pr view <N> --json number,title,body,mergedAt,author,state,baseRefName,headRefName`
2. `gh pr view <N> --json commits --jq '[.commits[] | {sha: .oid, headline: .messageHeadline, body: .messageBody}]'`
3. `gh pr diff <N>` — the full diff. If the diff is very large (>3000 lines), request just the file-level stat with `gh pr diff <N> --name-only` and then read selected hunks via `gh api` as needed.

Hold all three in mind as you judge.

## Output format

Return a single JSON document:

{
  "accepted": [
    {
      "pr": 42,
      "title": "Introduce event-sourced order delivery",
      "learning": "Orders flow through an append-only event log before materialization; replay and snapshot semantics guarantee exactly-once customer effects even under retry.",
      "target_recommendation": {
        "action": "create",
        "path": "architecture/event-sourced-orders.md",
        "rationale": "New architectural component, no existing file covers it."
      },
      "reference_files": ["src/orders/event_log.py", "src/orders/replay.py"]
    }
  ],
  "rejected": [
    {"pr": 38, "title": "Fix DST edge case in scheduler", "reason": "bug-fix"},
    {"pr": 41, "title": "Update lodash to 4.17.21", "reason": "deps-bump"}
  ],
  "errors": []
}

Rules for `target_recommendation`:
- `action` is `"create"` (new file) or `"update"` (extend existing). When `"update"`, `path` must exist under `.claude/knowledge/`.
- Check `.claude/knowledge/_index.md` and the per-domain indexes before deciding. Prefer updating an existing file that already covers 70%+ of the topic.
- `path` is always relative to `.claude/knowledge/`.

Rules for `learning`:
- 1–2 sentences. What *durable* knowledge does this PR encode? Not "what did this PR change" — what will a future developer need to know?
- Specific, not vague. "Adds caching" is not a learning. "Caches are keyed by (tenant_id, resource_id); TTL 60s; invalidated on writes via Redis pub/sub" is a learning.

Rules for `reference_files`:
- 1–3 source file paths from the PR that best represent the change. Used by `/curate` to anchor the knowledge entry.
- Exclude test files, generated files, lock files, and docs.

## What NOT to do

- Do NOT write any files.
- Do NOT modify the knowledge base.
- Do NOT trigger /curate.
- Do NOT be generous — only ~10-20% of typical merged PRs should clear the bar. If you find yourself accepting >30%, re-read the quality bar.
```

## Why a background agent

A thorough pass over dozens of PRs involves fetching PR metadata, reading diffs, and reasoning per PR. That's slow — 2–10 minutes depending on count. Running in the foreground would block the session. Sonnet is the right quality/cost point: Haiku is too shallow for nuanced significance judgments, Opus is overkill for structured analysis.

## Report format (for step 5)

```
Backfill candidates — significant knowledge only

1. PR #42 — "Introduce event-sourced order delivery"
   Orders flow through an append-only event log before materialization;
   replay and snapshot semantics guarantee exactly-once effects.
   → create architecture/event-sourced-orders.md

2. PR #78 — "Migrate internal RPC from REST to gRPC"
   Service communication switched to gRPC; .proto contracts, generated
   clients, phased rollout.
   → create architecture/grpc-internal.md + update deployment/ci-cd.md

Skipped — not significant (<N>): #38, #41, #53, #67, #72, #89, ...
Skipped — already curated (<N>): #44, #55, ...

Approve all? [y]   Select: [1,3]   Never-for-future: [never 2]   Cancel: [c]
```

Keep it scannable — short title quote, 1–2 lines of learning summary, explicit target-file recommendation.

## Dry-run mode

`--dry-run` runs the agent end-to-end and shows the report, but:
- Does NOT call `/curate` on any approval input (ignore whatever the user types — but do print what WOULD happen)
- Does NOT update `.claude/logs/backfill-knowledge.md`

Useful as a preview or as a way to exercise the judgment without committing to any curation.
