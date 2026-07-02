# Knowledge Index

## Architecture
- `architecture/skill-design-conventions.md` — Context economy as design principle; description budget + `skillListingBudgetFraction`; Trigger-line format
- `architecture/skill-composition.md` — Flag contracts (`--no-poll`/`--auto`), shared scripts, format contracts, soft coupling
- `architecture/model-economics.md` — Which model per skill and why (Haiku/Sonnet/session-model)
- `architecture/idempotent-scaffolding.md` — Scaffolding into shared user files: absorb unmarked sections, lazy dirs, never overwrite user content
- `architecture/worktree-task-file-copy.md` — Why `/kickoff` copies the task file into the worktree (not a symlink): no skill simplification, avoids accidental-commit repo poisoning + cross-platform fragility

## Features
- `features/backfill-and-origin-metadata.md` — `/backfill-knowledge` significance bar + origin-reconstruction cascade
- `features/statusline-integration.md` — Status-line segment: plugins can't own `statusLine.command`; marker-block injection
- `features/herdr-kickoff-automation.md` — herdr `herdr-launch.sh`: `launch` (`/kickoff`) + `resume` (`/continue <task>` reopens an `/exit`-closed tab)
- `features/herdr-close-automation.md` — `/close` in herdr: cwd-tab teardown, plugin SessionEnd hook, the one TUI-exit primitive, detached self-exit onto idle
- `features/task-archiving-on-close.md` — `/close` archives (not deletes) the task file; adaptive commit + ff-push to main
- `features/swarm-backend-adapter.md` — Verified codex/grok CLI facts (schema-enforced JSON, effort mapping, stdin hang) behind `swarm`'s adapter script

## Deployment
- `deployment/ci-structure-checks.md` — `check-structure.py` as the single automated guard for a build-less repo
