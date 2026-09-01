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
- `features/herdr-kickoff-automation.md` — herdr `herdr-launch.sh`: `launch` (`/kickoff` + `/adopt`) + `resume`; feature-detected dual contract (legacy placement vs 0.7.5+ `agent start --kind --pane`), registry-declared transport, rollback vs `blocked=unverified`, worker death from process state not terminal text
- `features/lane-registry.md` — `lanes.sh` + `herdr-agent.sh` (Wave 1): the one herdr-agent wrapper (degrade-not-block, bounded wait) + centralized `$HERDR_MATCH_PRELUDE` cwd↔worktree match (consumed by herdr-tab-glyph, regression-guarded via live snapshot); lanes.sh joins states+liveness keyed by worktree_path with a worktree-tab-state degrade tri-state; env test-seams for hermetic join tests
- `features/herdr-close-automation.md` — `/close` in herdr: cwd-tab teardown, plugin SessionEnd hook, the one TUI-exit primitive, detached self-exit onto idle
- `features/herdr-tab-glyphs.md` — Task-state glyphs (`○ ● ◇ ◆ ✓`) + main-root `◉` on herdr tab labels: `states` mode in the self-contained renderer, sync-vs-`--cached` PR refresh per caller, exact-cwd rename rules, soft pr-flow shim
- `features/kickoff-agent-selection.md` — `/kickoff` worker choice: single committed per-repo default (no global/fallback/ranking) else picker; `agent-registry.sh` as SoT; optional PATH-detected `cc-harness:<id>` class (pure consumer of `list`/`exec`, no gateway hardcoding); bounded model-aware grok/kimi probes (inconclusive→trust-auth); kimi's two-phase seed+continue argv + `argv_shell=`; non-claude "document, don't fake" degradation; announce-not-prompt for external defaults
- `features/task-archiving-on-close.md` — `/close` archives (not deletes) the task file; adaptive commit + ff-push to main; per-repo `.claude/work-system-close-autocommit` opt-in skips the ask
- `features/swarm-backend-adapter.md` — 0.6.0 read+web posture: OS secret-jail (denylist, worktree-aware, git-config-safe), per-voice fail-closed degrade, `jail` verb, prompt egress guard + residual risks; plus verified codex/grok CLI facts (out-of-band prompt transport vs. the argv/`MAX_ARG_STRLEN` wall, schema JSON, effort mapping, model-aware readiness); measured runtime drivers (cluster 13x > effort 2.3x > size) + per-call telemetry; one-parser config (`agents.sh config`) after 3 rounds of the same split-brain bug class; plus the bash traps found across 8 review rounds (print-vs-cache in `$( )`, apostrophes in `awk '…'`, comments in line continuations, bash 3.2, loud-vs-silent failure choice)
- `features/swarm-review-pipeline.md` — `/swarm:review` pipeline: skill↔Workflow wiring, family-consensus, 0.5.0 lens clusters + design-kind verify, `--fix`/`--loop` (deterministic close-out via `loop-closeout.py`), `--pr` publish via deterministic `pr-post.py`

## Deployment
- `deployment/ci-structure-checks.md` — `check-structure.py` as the single automated guard for a build-less repo
