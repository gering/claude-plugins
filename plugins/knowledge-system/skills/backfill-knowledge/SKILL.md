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

**Flag precedence and validation:**
- `--pr N` wins over everything else. If present, ignore `--last` and `--since` and skip the idempotency-set filter in Step 3 (the user explicitly asked for that single PR — if it was already processed, warn as described in Step 3 and prompt for confirmation).
- `--last N` and `--since DATE` are mutually exclusive. If both appear, stop with an error: "Pass exactly one of `--last` and `--since`."
- `--dry-run` combines freely with any of the above.

- Determine the default branch name (main/master) via `git symbolic-ref refs/remotes/origin/HEAD`.
- Fetch merged PRs:
  - `--pr N`: single PR, skip the list-building step.
  - `--last N`: `gh pr list --state merged --base <default> --limit N --json number,title,mergedAt`
  - `--since DATE`: `gh pr list --state merged --base <default> --search "merged:>=<DATE>" --limit 200 --json number,title,mergedAt`
  - Default: `gh pr list --state merged --base <default> --limit 200 --json number,title,mergedAt` (200 is the practical upper bound per run — users can run `--since` to cover older stretches).

### 3. Load the already-processed set (idempotency)

Skip PRs already represented:

- **From existing knowledge**: grep frontmatter fields only — `createdFrom` and `updatedFrom` — to avoid matching prose mentions like "the pattern emerged in PR #42" in the body. Use two stages so the PR-number extraction only sees the already-anchored portion, not the rest of the line:
  ```bash
  grep -rhoE '^(createdFrom|updatedFrom):[[:space:]]*"?PR #[0-9]+' .claude/knowledge/ 2>/dev/null \
    | grep -oE 'PR #[0-9]+' \
    | sort -u
  ```
  Stage 1's `-o` restricts the match to *just* the anchored prefix (field name + optional quote + `PR #N`); stage 2 then pulls the number from that captured fragment. This blocks both prose leaks (`session: 2026-04-17 see PR #99`) and trailing edits (`createdFrom: "PR #123" # superseded by PR #456` — #456 will not leak).
  Extract numeric set A.
- **From the log**: parse `.claude/logs/backfill-knowledge.md` (if it exists) for all bulleted PR numbers under the sections "Accepted", "Never", "Skipped — already curated", **and "Skipped — not significant"** — the last one is critical, otherwise every run re-judges every rejected PR and generates the same noise over and over. Extract numeric set B.
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

The agent never writes files and never invokes `/curate`, so `--dry-run` does NOT need to be passed into it — the flag only gates steps 7+8 in the outer skill (the `/curate` invocations and the log append). Any future refactor must preserve this invariant.

Inform the user in the channel:

> Backfill started as a background agent. It will read <N> merged PRs (title, body, commits, diff), judge each against the strict significance bar (new features / architecture / major insights only), and come back with a single approval report. Typical run: 2–10 minutes depending on PR count.

Return control. Do not block.

### 5. When the agent reports back

The agent's structured output contains:
- `accepted`: list of `{pr, title, learning, target}` objects
- `rejected`: list of `{pr, title, reason}` objects (label + one-line rationale)
- `errors`: list of `{pr, message}` (PRs that could not be fetched / parsed)

Present it as a single report to the user (see "Report format" below). Then wait for their selection.

### 6. Approval and selection

Prompt the user with the numbered `accepted` list (see "Report format" below) and the compressed rejected counts. Accept exactly one of the following inputs — combined forms like `1,3 never 2,4` are NOT supported and the user is instructed to run the skill twice if they want both kinds of action in one pass:

- `y` or `all` → approve all accepted candidates
- `1,3,5` → approve those numbers
- `1-4` → approve the inclusive range
- `1-3,7` / `1,4-6,9` → mixed list+range forms are accepted; the parser expands ranges and unions them with individual numbers
- `n` or empty → approve nothing (nothing gets curated; nothing goes into the `never`-bucket either; the run still appends a log entry so the already-judged PRs don't get re-judged next time)
- `never 2,4` / `never 2-3,5` → mark those numbers (same list+range expansion) as "never re-propose" in the log. Approves nothing this run. To both approve some and never-mark others, run twice.
- `c` → cancel (no log update at all)

### 7. Curate each approved candidate

For each approved candidate, invoke `/curate` with the exact shell-argument shape:

```
/curate "<learning>" <ref_file_1> <ref_file_2> <ref_file_3> --origin "PR #<number>"
```

- `<learning>` — the one-line learning string from the agent's `accepted[].learning` field
- `<ref_file_*>` — the paths listed in the agent's `accepted[].reference_files` (1–3 entries; all are included)
- `--origin "PR #<number>"` — ensures `createdFrom` on new files and `updatedFrom` on updated files get stamped with the PR reference, not the current branch

Run sequentially (simpler for the user to read the output). If a curate call fails, note it and continue with the rest — do NOT abort the whole batch.

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
3. `gh pr diff <N>` — the full diff. If the diff is very large (>3000 lines), get the touched-file list via `gh pr view <N> --json files --jq '.files[].path'` instead and then read selected hunks via `gh api` as needed. (Note: `gh pr diff` does NOT have a `--name-only` flag; the file list lives on `pr view`.)

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
   Reference files: src/orders/event_log.py, src/orders/replay.py

2. PR #78 — "Migrate internal RPC from REST to gRPC"
   Service communication switched to gRPC; .proto contracts, generated
   clients, phased rollout.
   → create architecture/grpc-internal.md + update deployment/ci-cd.md
   Reference files: proto/internal.proto, src/clients/grpc.go

Skipped — not significant (<N>): #38, #41, #53, #67, #72, #89, ...
Skipped — already curated (<N>): #44, #55, ...

Approve all? [y]   Select: [1,3]   Never-for-future: [never 2]   Cancel: [c]
```

Keep it scannable: title quote, 1–2 lines of learning summary, explicit target-file recommendation, and the reference files that will be passed to `/curate`. The user needs to see the reference files at approval time — otherwise they cannot predict what gets attached to the knowledge entry.

## Dry-run mode

`--dry-run` runs the agent end-to-end and shows the report, but:
- Does NOT call `/curate` on any approval input (ignore whatever the user types — but do print what WOULD happen)
- Does NOT update `.claude/logs/backfill-knowledge.md`

Useful as a preview or as a way to exercise the judgment without committing to any curation.
