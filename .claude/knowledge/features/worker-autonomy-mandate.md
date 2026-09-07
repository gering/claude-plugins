---
title: "Worker Autonomy Mandate (MANDATE.md)"
createdAt: 2026-09-07
updatedAt: 2026-09-07
createdFrom: "branch: task/extend-worker-autonomy"
updatedFrom: "branch: task/extend-worker-autonomy"
pluginVersion: 1.9.0
prime: false
---

# Worker Autonomy Mandate (MANDATE.md)

Slice A of the autonomy arc sketched in
[manager-worker-orchestration](../architecture/manager-worker-orchestration.md):
the arc says kickoff *pre-authorizes* a set of milestones — this is the record
that makes that survive a process boundary. `/kickoff` writes `MANDATE.md`
beside `TASK.md`; `/continue`, `/open` and `/cycle` read it instead of
re-deriving what the worker may do. Format and semantics live in
`plugins/work-system/scripts/mandate.sh`; the skills only call it.

## Authorization is a record, never an inference

The whole point is a hard line between *describing work* and *granting
permission*. A worker must not conclude it may merge because TASK.md says the
task ends in a merged PR, because a previous session did it, or because someone
started it. So only `MANDATE.md`'s **leading frontmatter block** grants
anything — the parser stops at the closing `---`, and a body that quotes
`allow: merge` out of a task file grants nothing. That case is not theoretical:
task prose gets pasted into these files, which is why `test_mandate.py` asserts
it.

## The 0/1/3 exit contract

`mandate.sh allows <action>` is the only question callers should ask, and its
three answers must stay three:

- **0** — allowed
- **1** — `denied` (in `deny`) or `unlisted` (in neither list)
- **3** — no mandate recorded at all, or (through pr-flow's shim) work-system
  is not installed

Collapsing 1 and 3 into "not allowed" is the defect this contract exists to
prevent: **1 is a decision the user made, 3 is a question they were never
asked.** Hence "no mandate" is *not* a lockdown — it is exactly the pre-mandate
behavior where the worker asks before each milestone. `deny` beats `allow`,
matching is whole-action (never substring), and silence is not consent.

## What has to be durable, and why

`review_budget` bounds review→fix rounds, and `review_rounds_used` is
incremented **in the file**, not in a loop variable. An in-session counter dies
with the session, so a worker resumed via `claude -c` after a context loss would
silently restart its allowance and loop as long again. Anything that bounds
autonomy has to outlive the process it bounds.

Storage is the worktree root (a deliberate choice over a per-worktree git-dir or
a central per-repo store): visible and hand-editable, so widening a running
lane is just an edit. The price is a `/MANDATE.md` line in every consumer
repo's `.gitignore`, same as `/TASK.md` — see
[worktree-task-file-copy](../architecture/worktree-task-file-copy.md) for why
that file is ephemeral in the first place.

Match the mandate to the worker: codex/grok/kimi cannot run the review skills,
so recording `local-review` for them is worse than recording nothing. Their
bootstrap prompt (`agent-registry.sh`) names `MANDATE.md` precisely because they
have no `/continue` to read it for them.

## Soft coupling, and what "missing" means

pr-flow reaches work-system through `scripts/mandate-shim.sh`, which shares the
plugin locator (`scripts/lib-work-system.sh`) with `refresh-task-glyphs.sh` —
the same dev/manifest/cache cascade described in
[statusline-integration](statusline-integration.md)'s sibling shims. The two
shims differ deliberately: a missing work-system makes the glyph shim a silent
no-op, but makes the mandate shim answer **3**. A capability question must never
be answered by silence, or an absent plugin reads as a refusal.

## Recommend only what can work

Shipped alongside, and the same class of bug: `/open` and `/cycle` used to send
bot-less repos to `/cycle`, which comments `@claude review` into the void and
polls until timeout. Both now check whether a review workflow exists at all
before recommending or triggering one, and route a repo without one to the local
`/swarm:review --pr <N>` (run when the mandate allows it, offered otherwise) —
see [swarm-review-pipeline](swarm-review-pipeline.md). Reporting a missing
capability beats recommending a command that cannot succeed here.

## Bash trap found building this

`die` inside a command substitution kills only the subshell. `mandate_path()`
printed its result, so a failing `git rev-parse` left the caller with a
truncated `/MANDATE.md` and exit 0 — it would have written to the filesystem
root. The fix is the general one for this shape: resolve into a global
(`resolve_mandate_path`) and call it directly, so `die` can actually exit. Same
family as the print-vs-cache trap in
[swarm-backend-adapter](swarm-backend-adapter.md).
