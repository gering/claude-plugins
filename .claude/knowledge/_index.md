# Knowledge Index

## Architecture
- `architecture/skill-design-conventions.md` — Context economy as design principle; description budget + `skillListingBudgetFraction`; Trigger-line format
- `architecture/skill-composition.md` — Flag contracts (`--no-poll`/`--auto`), shared scripts, format contracts, soft coupling
- `architecture/model-economics.md` — Which model per skill and why (Haiku/Sonnet/session-model)
- `architecture/idempotent-scaffolding.md` — Scaffolding into shared user files: absorb unmarked sections, lazy dirs, never overwrite user content
- `architecture/worktree-task-file-copy.md` — Why `/kickoff` copies the task file into the worktree (not a symlink): no skill simplification, avoids accidental-commit repo poisoning + cross-platform fragility
- `architecture/manager-worker-orchestration.md` — Design decisions for the coordinated Manager/Worker model: lane identity = worktree path, cross-agent (claude/codex/grok) git-as-uniform-bus, central `~/.agent-mail/` mailbox (Maildir/AMQ, outbox+inbox, hook-driven push), milestone worker autonomy, merge sequencer, roadmap-as-derived-view
- `architecture/plugin-settings-system.md` — Per-plugin TOML config over schema defaults: ownership split (plugin owns schema+defaults, settings plugin owns resolve/validate/IO), defaults=current-behavior + `[compat]` migration, `[related_projects]` peer address book, consumer contract (read resolved via `settings.py get --json`), serializer/symlink/`set`-path hardening lessons

## Features
- `features/backfill-and-origin-metadata.md` — `/backfill-knowledge` significance bar + origin-reconstruction cascade
- `features/statusline-integration.md` — Status-line segments: plugins can't own `statusLine.command`; marker-block injection; the `[ks …]` + `[ws …]` two-segment coexistence and ws's never-block-on-network PR cache
- `features/herdr-kickoff-automation.md` — herdr `herdr-launch.sh`: `launch` (`/kickoff`) + `resume` (`/continue <task>` reopens an `/exit`-closed tab)
- `features/herdr-close-automation.md` — `/close` in herdr: cwd-tab teardown, plugin SessionEnd hook, the one TUI-exit primitive, detached self-exit onto idle
- `features/herdr-tab-glyphs.md` — Task-state glyphs (`○ ● ◇ ◆ ✓`) + main-root `◉` on herdr tab labels: `states` mode in the self-contained renderer, sync-vs-`--cached` PR refresh per caller, exact-cwd rename rules, soft pr-flow shim
- `features/kickoff-agent-selection.md` — `/kickoff` worker choice: single committed per-repo default (no global/fallback/ranking) else picker; `agent-registry.sh` as SoT; bounded model-aware grok probe (inconclusive→trust-auth); non-claude "document, don't fake" degradation; announce-not-prompt for external defaults
- `features/task-archiving-on-close.md` — `/close` archives (not deletes) the task file; adaptive commit + ff-push to main
- `features/swarm-backend-adapter.md` — 0.6.0 read+web posture: OS secret-jail (denylist, worktree-aware, git-config-safe), per-voice fail-closed degrade, `jail` verb, prompt egress guard + residual risks; plus verified codex/grok CLI facts (schema JSON, effort mapping, model-aware readiness)
- `features/swarm-review-pipeline.md` — `/swarm:review` pipeline: skill↔Workflow wiring, family-consensus, 0.5.0 lens clusters + design-kind verify, `--fix`/`--loop` (deterministic close-out via `loop-closeout.py`), `--pr` publish via deterministic `pr-post.py`

## Deployment
- `deployment/ci-structure-checks.md` — `check-structure.py` as the single automated guard for a build-less repo
