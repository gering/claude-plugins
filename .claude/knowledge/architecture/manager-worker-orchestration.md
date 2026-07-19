---
title: "Manager/Worker Orchestration (design)"
createdAt: 2026-07-18
createdFrom: "session: design-manager-worker-orchestration 2026-07-18"
pluginVersion: 1.8.1
prime: false
---

# Manager/Worker Orchestration (design)

Design decisions for evolving work-system from fire-and-forget kickoffs into a
coordinated Manager/Worker model. This is the **decision record**; the
implementation is spawned across tasks `add-lane-registry`, `add-lane-mailbox`,
`add-manager-watch-loop`, `extend-worker-autonomy`, `add-merge-sequencer`,
`add-roadmap-skill`, `add-mailbox-statusline`, and `add-agent-broadcast` (follow-up).
Full working notes: the design task's `DESIGN-NOTES.md`.

## The model
- **Manager** = the Claude Code session at the main repo root (herdr `◉` tab). A
  *coordinator*, not a merge robot — the human stays merge authority unless
  explicitly delegated at kickoff.
- **Worker** = one {claude|codex|grok} session per worktree, driving its task to a
  reviewed, mergeable PR.
- **Lane** = `(worktree_path, task, branch)`. **Identity = worktree_path** — the one
  key stable across agent types and restarts. herdr pane/tab, `agent_status`,
  session UUID, PR state are live-attached attributes, **never identity**. Only the
  task file persists; everything else is derived live (no lane registry file, no
  stashed ids).

## Gate verdict (herdr substrate, verified live 2026-07-16, herdr 0.7.0)
- An `agent start`-launched tab **does** expose a pollable `agent_status` ∈
  `idle|working|blocked|done|unknown`. `blocked` (worker on an input/permission
  prompt) is the primary "needs the Manager" signal. Poll via `agent list`/`agent
  get`; **bounded** block-wait via `agent wait --status … --timeout` (no busy loop).
- Status is produced by **installed hook integrations** merged with a TUI-scrape
  rule engine (`agent explain`), NOT self-reported by the agent.
- `agent read --source recent` returns clean text incl. Claude's `※ recap:` line +
  statusline. This RESOLVES the previously-UNVERIFIED agent-status flag in
  [herdr-kickoff-automation](../features/herdr-kickoff-automation.md) /
  [herdr-close-automation](../features/herdr-close-automation.md) and adds `blocked`
  to their documented `idle|working|done` set.

## Cross-agent constraint (workers may be claude, codex, OR grok)
The Manager is always Claude Code. Workers are tiered by herdr integration:
- **claude, codex** — installed hook (`herdr-agent-state.sh`) → authoritative status
  **+ session UUID**; `agent read` carries branch+`PR#` (claude also `※ recap:`).
- **grok** — **no installable integration, no session UUID**, TUI-scrape-only status
  (coarse; `blocked` unlikely), minimal `agent read`.
→ The orchestration **contract is expressed in git/PR terms**, the ONE channel
uniform across all three (every agent produces commits/branches/PRs). `agent_status`
is a coarse liveness layer; `agent read` is best-effort detail, parsed *per agent
type*, never format-required. Agent-specific skills (`/open`, `/rebase`) are each
worker's *implementation* of hitting agent-agnostic milestones — soft-coupling
([skill-composition](skill-composition.md)) applied across agent types.

## Worker↔Manager mailbox (the coordination substrate)
A file mailbox carries judgment/intent signals that have no git artifact.
- **Central `~/.agent_messaging/` — NOT per-worktree** (decided 2026-07-19). Participant
  id = **encoded absolute path**; a hook derives its dir from `cwd`:
  `~/.agent_messaging/lanes/<enc(cwd)>/{outbox,inbox}.jsonl`. Central over `<root>/.mailbox/`
  because (a) it is the rendezvous for **broadcast / cross-Manager** (below), (b)
  never-commit is automatic (outside every repo — no per-repo gitignore), (c) no
  cross-worktree-boundary writes. Tool-agnostic name, JSONL append-only; survives `/close`
  removing the worktree.
- **Envelope:** `{id, ts, from, to, type, body}`. `from`+`to` required. Types:
  `ready-to-close`, `blocked-on-decision`, `coordination-request`, `needs-human`,
  `broadcast-request`.
- **Topology — outbox + inbox, NOT inbox-only.** Each participant writes **only its
  own outbox**; the Manager is the **only** writer of any inbox. Buys: single writer
  per file (no clobber), no cross-participant writes by a worker, and the
  **single-sequencer invariant** — worker→worker messages are *addressable* but
  **route through the Manager** (it drains outboxes, delivers to inboxes, may
  reorder/veto/batch). Workers never write another lane's inbox.
- **Drain by offset, never mid-file delete.** Reader (Manager for outboxes, worker
  for its inbox) tracks a last-read byte offset in a sidecar; files stay append-only,
  archived/truncated only once fully consumed. Preserves single-writer.
- **`ready-to-close` is worker-authoritative + a handoff report.** Only the worker knows
  if a *second* PR is open or a post-merge TODO remains — a single PR's `✓ merged` is a
  soft "near-done" hint, NOT a close trigger. The message carries the worker's fresh
  context as `{summary, follow_ups[], deploy?, updates[], learnings[]}`: `follow_ups` →
  Manager `/define`; `deploy` runs **from main after merge** (Manager-at-main model);
  `updates` = dependency/config recommendations (e.g. a plugin bump); `learnings` →
  Manager `/curate`. The Manager acts on these, then it/human runs `/close`.

## Broadcast + cross-Manager (follow-up phase; central location adopted now)
The central store unlocks system-wide coordination across the *multiple Managers* a
machine runs (one per project). `~/.agent_messaging/broadcast/global.jsonl` is the one
multi-writer append log every Manager reads (offset-tracked); `managers/<enc>.json`
presence files make Managers discoverable. Use: system-wide notices, or capability
queries ("who has Cloudflare access?") answered point-to-point into the asker's inbox.
**Single-sequencer up a level:** a worker emits `broadcast-request` and its **Manager**
decides/posts; **Managers peer-to-peer** (they are the coordinators). Aligns with
`plugin-settings-system`'s `[related_projects]` peering registry. Broadcast + registry
ship as a dedicated follow-up (`add-agent-broadcast`), not the Wave-1 core.

## Push without polling — Claude Code hooks first
Files are pull; hooks make delivery push-like at turn boundaries, all gated by a
cheap `stat` (append-only ⇒ compare byte offset, read only the new tail):
- **Stop hook** (turn end): inbox grew → `decision:block` + `additionalContext`
  injects the message so the worker continues. Respect `stop_hook_active` (exit 0 when
  true) + block only on a real offset advance → no loop / 8-block cap. Injected text is
  context; the worker still applies its own autonomy gates.
- **SessionStart(`resume`)**: pull what arrived while closed. **UserPromptSubmit**:
  belt-and-suspenders prepend.
- **Notification hook** (`permission_prompt`/`idle_prompt`) auto-emits
  `blocked-on-decision`/`needs-human` to the outbox — worker-stuck detection becomes
  push, no Manager poll.
- **Manager** drains all outboxes at its own Stop/UserPromptSubmit boundaries.
- The **one gap**: a worker idle *at the prompt* (already Stopped). Only here does a
  herdr **idle-wake ping** (`agent send` onto idle) earn its keep. Optional.
Hooks are Claude-Code-specific → reinforces **claude-first**; codex/grok stay
poll/Manager-inferred until they grow equivalents.

## Worker autonomy arc (milestone-defined, agent-tiered)
Default arc = git/PR **milestones**: `commits → push → PR open → review pass →
ready-to-close`. claude self-drives via work-system/pr-flow/swarm; codex/grok drive
to *PR open* with `gh`, and the **Manager fills the review gap** (`/swarm:review --pr
N` — any agent's PR is a valid target). Kickoff **pre-authorizes** commit/push/PR/
review/own-branch-rebase/agreed-fixes. **Stop-early gates** (→ `blocked`/escalate):
failing CI, scope drift, any destructive/irreversible step, **merge to main** (never
without explicit delegation), force-push beyond own-branch lease, any human decision.

## Merge sequencing — deterministic decision helper
`merge-sequencer.sh` (a *decision* helper, not an actor) over the lanes view outputs:
mergeable-now (`◆` + CI green + no conflict) and, after a merge, rebase-due lanes
(behind main ∧ file overlap). One PR at a time; the human authorizes each; the
Manager sends rebase intents. The `marketplace.json`+`plugin.json` version-line
collision is baked in. Encodes today's ROADMAP prose rule as a script (prose-drift).

## Roadmap — a derived view, co-equal with the GH board
Source of truth = task files + git/PR state. `ROADMAP.md` and the future GH board
(see task `design-github-projects-task-source`) are **two derived views**, not
competing sources — ROADMAP is the local/offline projection, the board the
shared/online one; both read the same `states`/`task-status.sh` substrate. Inside
ROADMAP.md, a marker-delimited **Manager-auto block** (per-lane state ○●◇◆✓, PR#,
version) is regenerated from the lanes view; the **human-curated block** (waves,
serialization policy, rationale) is never machine-touched (idempotent-scaffolding:
absorb-unmarked, never overwrite user content). Owned by a new `/roadmap` skill;
`/list` stays the ephemeral snapshot.

## Safety envelope (cross-cutting)
Fail-closed pane id (reuse `worktree-tab-state` tri-state — never inject into an
unverified pane); no force-push beyond own-branch `--force-with-lease`, never
shared/main; no merge-to-main without delegation; bounded herdr waits, survey refresh
`--cached`; grok `always-approve` ⇒ Manager sends nothing destructive to a grok lane.

## Reused existing substrate (build ON, don't reinvent)
- `ws-statusline.sh states <main> [--cached]` → `task\tstate\tglyph` = a ready-made
  lane registry with states; the state machine ○●◇◆✓ + `◉` and the tab↔worktree join
  already exist in [herdr-tab-glyphs](../features/herdr-tab-glyphs.md).
- The PR cache (`headRef\tstate\treviewDecision`) + sync/`--cached` refresh policy.

Related: [skill-composition](skill-composition.md),
[idempotent-scaffolding](idempotent-scaffolding.md),
[herdr-kickoff-automation](../features/herdr-kickoff-automation.md),
[herdr-tab-glyphs](../features/herdr-tab-glyphs.md).
