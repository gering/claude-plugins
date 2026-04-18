# Knowledge System Plugin

Lightweight, native knowledge management for Claude Code projects. Build up a persistent, layered knowledge base as you work — and have Claude actually use it, automatically, every session.

## Features

### Auto-Prime — Claude knows your project from turn 1

**What it does.** `/init` injects `@.claude/knowledge/_index.md` into your project's `CLAUDE.md`. Claude Code auto-loads `CLAUDE.md` on every session start and expands `@file` references inline — so your knowledge index is in Claude's context before the first prompt. A fallback directive in `.claude/rules/knowledge-system-usage.md` ensures the index still gets loaded (via one-time Read) even if something with the `@` expansion goes wrong.

**Why it matters.** No more "please read CLAUDE.md and skim the codebase first". Claude opens the session already knowing what exists, where, and how it fits together. The first answer is informed.

### Layered by lifecycle, not by folder

**What it does.** Knowledge splits into three layers by how often it needs to be present:
- **Rules** (`.claude/rules/`) — loaded every session. Short directives: "use camelCase", "never mock the DB in integration tests".
- **Knowledge** (`.claude/knowledge/`) — pulled on demand via `/query`. Deep context on architecture, features, flows.
- **Claude's Memory** — automatic, global. Independent of this plugin.

**Why it matters.** Always-on rules stay tight (low context cost). Big knowledge stays out of context until relevant. Claude's built-in memory keeps cross-session user-specific context separate. Right info, right cost, right scope.

### `/curate` — capture without ceremony

**What it does.** `/curate "the auth middleware validates JWT before routing" src/middleware/auth.ts`. Claude decides the target layer (rule vs knowledge vs CLAUDE.md), finds overlapping entries, merges instead of duplicating, and updates the index. Frontmatter (`updatedAt`, `pluginVersion`) is maintained automatically.

**Why it matters.** The barrier to storing a learning is one line. You actually capture what you know instead of losing it in chat history. Days later, `/query` finds it.

### `/query` — cheap, targeted lookups on Haiku

**What it does.** `/query "how does the notification system work?"` spawns a Haiku subagent against the knowledge index, returns a dense summary with file references. Falls back to an Explore agent (session model) only if the knowledge base has a gap.

**Why it matters.** Haiku is sub-second and ~1/10 the cost of the session model. Since `/query` is meant to run often — ideally before every non-trivial change to unfamiliar code — the cost has to be invisible or nobody uses it. It is.

### `/reindex` — QA as a background agent

**What it does.** `/reindex` dispatches a **background agent** (Sonnet with the 1M-context window) that walks the entire knowledge base and:
- rebuilds every `_index.md`
- validates cross-references (linked files still exist, paths still correct)
- **proactively proposes new cross-links** between files that discuss the same concepts but don't link each other
- backfills missing `createdAt` / `updatedAt` from git history
- updates `reindexedAt` and `pluginVersion`
- appends a short bullet-point summary to `.claude/logs/reindex.md`

**Why a background agent.** A thorough QA pass reads many files and reasons over the whole graph — that's slow. Running it in the foreground would block your session for minutes. As a background agent, you type `/reindex` and keep working; the summary comes back when it's done.

**Why Sonnet 1M.** Cross-reference detection and duplicate analysis need to hold the whole knowledge graph in context. The 1M window makes that a single pass instead of a paginated mess. Quality of judgment matters here — `/reindex` runs rarely, so model cost isn't the bottleneck.

### Cross-reference detection — your knowledge becomes a graph

**What it does.** During `/reindex`, the agent doesn't just validate existing links — it *proposes new ones*. If `features/billing.md` discusses the payment flow and `architecture/payments.md` describes the same system from a structural angle, `/reindex` suggests linking them in both directions.

**Why it matters.** A pile of markdown files decays into disconnected fragments. A graph stays navigable: one entry point leads to related context, Claude follows links, users follow links. Over time the knowledge base gets *more useful*, not just bigger.

### Run logs — see what changed over time

**What it does.** Every `/reindex` run appends a short, bullet-point summary to `.claude/logs/reindex.md`: when it ran, what plugin version, what it changed, why.

```markdown
## 2026-04-17 — knowledge-system v1.3.0
- Rebuilt 12 _index.md entries
- Backfilled frontmatter: architecture/auth.md (added updatedAt from git)
- Proposed cross-link: features/billing.md <-> architecture/payments.md
- No duplicates, no dead references
```

**Why it matters.** You can look back and see how your knowledge evolved — which sections got reorganized, when dead references were cleaned up, what cross-links were added. Useful on its own, and essential when you come back to a project after weeks and wonder "what state is this in?". Append-only, low-noise — no ephemeral entries, one heading per run.

### Git-aware metadata — no manual bookkeeping

**What it does.** `createdAt`, `updatedAt`, `createdFrom`, and `updatedFrom` can all be derived from `git log` and merge-commit parsing when missing. Dates use ISO-8601 date-only (`YYYY-MM-DD`, UTC) — no time-of-day, no timezone confusion, diff-friendly. Origin fields store a PR number (`"PR #42"`) or a branch name (`"branch: feature/xyz"`) so every knowledge entry carries traceability back to where it came from.

**Why it matters.** Nobody remembers to update an `updatedAt` or an origin field. The git history does. `/reindex` and `/curate` fill them in for you, so both recency and provenance are trustworthy.

### `/backfill-knowledge` — mine your PR history for significant learnings

**What it does.** Walks merged PRs on main, reads each one (title, body, commit messages, diff), and has Sonnet judge: *is this a new feature, an architecture change, or a major insight worth preserving?* Small bug fixes, typos, refactors, dependency bumps — all filtered out by the LLM, even if they contain some information. Survivors come back as a **single batch report**: numbered candidates with one-line learning summaries and a recommended target file. You approve by range (`1,3,5`, `all`, `1-4`) and the skill then runs `/curate` on each. Skips PRs already represented in the knowledge base (scan of `createdFrom` / `updatedFrom` + a processed-log at `.claude/logs/backfill-knowledge.md`). Runs as a background agent.

**Why it matters.** Knowledge bases start empty — you only curate going forward. Months of merged work sit in PR history, uncurated. `/backfill-knowledge` is how you bootstrap from history without opening a flood of low-value entries. The quality bar is explicit: only PRs that encode a durable decision or pattern get proposed.

### Clean uninstall — markers, not guesswork

**What it does.** Everything `/init` writes is either its own file (like the usage rule) or wrapped in `<!-- BEGIN knowledge-system -->` / `<!-- END knowledge-system -->` markers inside `CLAUDE.md`.

**Why it matters.** When you remove the plugin, you know exactly what to delete and what to keep. Your knowledge files (`.claude/knowledge/**`) are *your content* — they persist even if the plugin goes. No orphaned cruft, no guessing what came from where.

## Quick start

```
> /plugin install knowledge-system
> /init
> /curate "Request IDs flow through the X-Request-ID header and are logged in every service" src/middleware/request-id.ts
> /query "how do request IDs propagate?"
```

## Commands

| Command | Description |
|---------|-------------|
| `/init` | Scaffold knowledge system: directories, starter files, auto-prime rule, CLAUDE.md entry |
| `/query` | Retrieve relevant knowledge on demand — Haiku subagent, sub-second |
| `/curate` | Store a new learning in the right layer; merges with existing entries |
| `/reindex` | Thorough QA pass: rebuild indexes, validate cross-refs, backfill frontmatter, log |
| `/backfill-knowledge` | Mine merged PR history for significant learnings (features, architecture, major insights); proposes a batch for approval before curating |
| `/migrate` | Migrate from ByteRover to native knowledge system |

## How it works

### The three layers

1. **Rules** (`.claude/rules/`) — always loaded into every session. Short directives for style, patterns, dos/don'ts. Keep them tight: if a rule is longer than ~10 lines, it probably belongs in knowledge instead.

2. **Knowledge** (`.claude/knowledge/`) — on-demand detailed documentation. Organized by domain (`architecture/`, `features/`, `deployment/`, custom subdirs). Retrieved via `/query`, not by default in context.

3. **Claude's Memory** — the built-in auto-memory. Captures user preferences and cross-session context automatically. Operates independently of this plugin.

### Auto-prime mechanism

`/init` does two things to ensure Claude actually uses the system:

- Injects `@.claude/knowledge/_index.md` into `CLAUDE.md`, wrapped in `<!-- BEGIN knowledge-system -->` / `<!-- END knowledge-system -->` markers. Claude Code auto-loads `CLAUDE.md` every session and expands `@file` references inline — so your knowledge index lives in Claude's context from the start.
- Writes `.claude/rules/knowledge-system-usage.md`, an always-active rule with usage directives (when to `/query`, when to `/curate`, when to suggest `/reindex`) and a **fallback**: if for any reason the index isn't in context, Claude reads it once via the Read tool. Belt-and-suspenders.

## Usage examples

### Before making a change

You're about to touch an unfamiliar part of the codebase. Instead of scanning files by hand, ask:

```
> /query "what's the contract between the payment service and the order service?"
```

Claude returns a dense summary with file references: read only what's relevant, skip the rest.

### After fixing a non-obvious bug

You just debugged a 3-hour race condition. Before moving on:

```
> /curate "The order event handler is NOT idempotent — duplicate delivery creates duplicate line items. We fixed it with a DB-level unique constraint on (order_id, event_id), not in app code." src/events/order-handler.ts
```

Claude picks the right file — probably updates an existing `architecture/event-delivery.md` or creates `features/order-idempotency.md`. It bumps `updatedAt` and `pluginVersion`, and updates the index.

### After a design decision

```
> /curate "We chose Postgres over MySQL for the metrics store. JSON support won out over MySQL's replication tooling. Main constraint: must handle schemaless event payloads without migrations."
```

This looks like an architectural decision. Stored in `knowledge/architecture/` (ADR-style as a dedicated folder coming in Phase 2 — see Roadmap below).

### Periodic quality pass

Once in a while — before a big release, after a refactor, or when things feel stale:

```
> /reindex
```

Runs a thorough Sonnet-1M pass. You get back a report like:

```
Reindex complete (knowledge-system v1.3.0)

Rebuilt indexes:       12 _index.md files
Frontmatter backfilled: 3 files (added createdAt/updatedAt from git)
Cross-refs added:       2 new bidirectional links suggested
  - features/billing.md <-> architecture/payments.md
  - features/auth.md <-> architecture/sessions.md
Dead references:        0
Duplicates flagged:     0
```

Summary is appended to `.claude/logs/reindex.md` — so you can see what changed over time without digging.

### Retroactively curate from PR history

When you adopt the knowledge system on an existing project, months of learnings are sitting in merged PRs. Run `/reindex` once first (it backfills `createdFrom` / `updatedFrom` on existing knowledge so the idempotency check knows what's already represented), then:

```
> /backfill-knowledge --last 50
```

A Sonnet background agent reads each of the last 50 merged PRs (title, body, commit messages, diff), judges significance against a strict bar (new features / architecture / major insights only — small bug fixes excluded), and returns a report like:

```
Backfill candidates — significant knowledge only

1. PR #42 — "Introduce event-sourced order delivery"
   Architecture change: orders flow through event log before materialization.
   Invariants, replay semantics, snapshot strategy.
   Recommendation: create architecture/event-sourced-orders.md

2. PR #78 — "Migrate internal RPC from REST to gRPC"
   Architecture change: service communication protocol switched.
   .proto schemas, client generation, deployment changes.
   Recommendation: create architecture/grpc-internal.md + update deployment/ci-cd.md

3. PR #145 — "Multi-tenant billing support"
   New feature with tenant-isolation design decisions.
   Recommendation: create features/multi-tenant-billing.md

Skipped (27 PRs judged not significant): #38, #41, #53, ... (see log)
Skipped (12 PRs already curated): #67, #89, ... (see log)

Approve all? [y]   Select: [1,3]   Cancel: [c]
```

You pick, `/curate` runs on each accepted candidate with the PR's context as input. Processed PR numbers are recorded to `.claude/logs/backfill-knowledge.md` so subsequent runs skip them.

## Frontmatter schema

All knowledge files may (and by convention should) carry this frontmatter. Everything is optional — `/reindex` and `/curate` backfill missing fields on touch.

```yaml
---
title: "Auth flow with JWT"          # human-readable display name
createdAt: 2026-04-17                # ISO-8601 date-only (YYYY-MM-DD), UTC
updatedAt: 2026-04-17                # touched on every content change
reindexedAt: 2026-04-17              # set only by /reindex
createdFrom: "PR #42"                # traceability — where did this entry originate
updatedFrom: "PR #57"                # traceability — where did the last edit come from
pluginVersion: 1.4.0                 # knowledge-system version at last write
---
```

### Field semantics

| Field | Written by | When |
|-------|-----------|------|
| `title` | `/curate` | On create or when display name should diverge from filename |
| `createdAt` | `/curate` (new files), `/reindex` (backfill) | Derived from `git log --diff-filter=A --format=%aI -- <file> \| tail -1` when backfilling |
| `updatedAt` | `/curate` (every edit), `/reindex` (backfill) | Derived from `git log -1 --format=%cI -- <file>` when backfilling |
| `reindexedAt` | `/reindex` only | Updated each QA run |
| `createdFrom` | `/curate` (new files), `/reindex` (backfill), `/backfill-knowledge` | Origin of the entry — a PR number, a branch, or a session. Reconstructed from the first commit's merge context when backfilling. |
| `updatedFrom` | `/curate` (every edit), `/reindex` (backfill), `/backfill-knowledge` | Origin of the last edit. Reconstructed from the latest commit's merge context when backfilling. |
| `pluginVersion` | `/curate`, `/reindex` | The knowledge-system version at the last write (content or metadata) |

### `createdFrom` / `updatedFrom` format

Quoted string with a source prefix so the field is extensible for future origins:

| Value | When |
|-------|------|
| `"PR #42"` | The edit happened on a merged PR. Preferred form whenever a PR exists. |
| `"branch: feature/my-work"` | On a branch that has no PR yet (in-progress work). Upgraded to the `PR #N` form by `/reindex` once the branch is merged. |
| `"session: 2026-04-17"` | Direct edit on main or outside any branch workflow (rare). Uses the ISO date for uniqueness. |

Reconstructability note: for any committed knowledge file, `git log` + GitHub merge-commit message parsing (`Merge pull request #N`, `(#N)` squash suffix) recovers the PR number in the common case. `/reindex` does this automatically; if the merge context cannot be determined unambiguously, the field stays empty rather than guessing.

### Format rules

- **All timestamps are ISO-8601 date-only** (`YYYY-MM-DD`, UTC). No time-of-day, no timezones — diff-friendly.
- Frontmatter-less files remain valid. They are brought into form the next time `/curate` touches them or `/reindex` runs.

## Logs

Two append-only logs in `.claude/logs/`:

**`reindex.md`** — one heading per `/reindex` run:

```markdown
## 2026-04-18 — knowledge-system v1.4.0
- Rebuilt 12 _index.md entries
- Backfilled frontmatter: architecture/auth.md (added updatedAt, createdFrom from git)
- Proposed cross-link: features/billing.md <-> architecture/payments.md
- No duplicates, no dead references
```

**`backfill-knowledge.md`** — one heading per `/backfill-knowledge` run, followed by a tally of processed PRs and their dispositions (`accepted`, `skipped: not significant`, `skipped: already curated`, `never: user-denied`):

```markdown
## 2026-04-18 — knowledge-system v1.4.0 — range: last 50 PRs

Accepted (3):
- PR #42 → architecture/event-sourced-orders.md (new)
- PR #78 → architecture/grpc-internal.md (new) + deployment/ci-cd.md (updated)
- PR #145 → features/multi-tenant-billing.md (new)

Skipped — not significant (27):
- #38, #41, #53, #67, #72, #89, #91, #96, #102, #108, #115, #118, #123,
  #127, #132, #139, #142, #148, #151, #159, #163, #168, #172, #177, #181,
  #184, #189

Skipped — already curated (12):
- #44, #51, #55, ...

Never (0):
```

The `never`-bucket is persistent — subsequent runs skip those PRs silently.

## Roadmap (Phase 2)

- **`/audit`** — semantic QA beyond `/reindex`: duplicate detection, stale content flagging, volatile-value scan, knowledge-gap analysis per code directory.
- **ADR category** — `.claude/knowledge/decisions/` with numbered, immutable Architecture Decision Records. Detection heuristic in `/curate` routes decision-shaped insights there automatically. ADRs bring their own status semantics (`proposed` / `accepted` / `superseded`).
- **`.claude/docs/`** — optional authoritative reference folder that `/query` consults with higher priority than accumulated knowledge.
- **Query ranking** — `/query` uses recency and reference count to rank when many entries match.
- **Maturity lifecycle** — introduce a `maturity` field (`draft` / `stable` / `deprecated`) with a promotion mechanism. Only worth adding once `/query` or ADRs actually consume the field.
- **Activity log** — broader log beyond `/reindex`: session summaries, bug-fix context, captured at `/merge` or `/close` time.

## Installation

Part of the [gering-plugins](https://github.com/gering/claude-plugins) marketplace:

```
/plugin marketplace add gering/claude-plugins
/plugin install knowledge-system
```

## Uninstall

`/init` writes files into the project directory that persist after the plugin is uninstalled. Everything it writes is marked so cleanup is unambiguous.

### Plugin-managed files (safe to remove)

- `.claude/rules/knowledge-system-usage.md` — the always-active directives rule
- The block inside `CLAUDE.md` wrapped in `<!-- BEGIN knowledge-system -->` and `<!-- END knowledge-system -->` markers

### User data (remove only if you really want to lose it)

- `.claude/knowledge/**` — all knowledge files authored via `/curate` or by hand
- `.claude/logs/reindex.md` — log history of `/reindex` runs
- `.claude/logs/backfill-knowledge.md` — log history of `/backfill-knowledge` runs (including the persistent `never`-list)

### Steps

1. Uninstall the plugin:
   ```
   /plugin uninstall knowledge-system
   ```

2. Remove the rule file and the CLAUDE.md section:
   ```
   rm -f .claude/rules/knowledge-system-usage.md
   # In CLAUDE.md, delete everything between the BEGIN and END knowledge-system markers (inclusive).
   ```

3. (Optional) Remove your knowledge and log history — **this is your own content**, not plugin data:
   ```
   rm -rf .claude/knowledge
   rm -f .claude/logs/reindex.md .claude/logs/backfill-knowledge.md
   ```
