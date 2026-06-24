# Knowledge Index

## Architecture
- `architecture/skill-design-conventions.md` — Context economy as design principle; description budget + `skillListingBudgetFraction`; Trigger-line format
- `architecture/skill-composition.md` — Flag contracts (`--no-poll`/`--auto`), shared scripts, format contracts, soft coupling
- `architecture/model-economics.md` — Which model per skill and why (Haiku/Sonnet/session-model)
- `architecture/idempotent-scaffolding.md` — Scaffolding into shared user files: absorb unmarked sections, lazy dirs, never overwrite user content

## Features
- `features/backfill-and-origin-metadata.md` — `/backfill-knowledge` significance bar + origin-reconstruction cascade
- `features/statusline-integration.md` — Status-line segment: plugins can't own `statusLine.command`; marker-block injection
- `features/herdr-kickoff-automation.md` — `/kickoff` in herdr: argv-launch via `herdr-launch.sh` (race-free), named tab per task
- `features/herdr-close-automation.md` — `/close` in herdr: cwd-tab teardown, plugin SessionEnd hook, the one TUI-exit primitive, detached self-exit onto idle
- `features/task-archiving-on-close.md` — `/close` archives (not deletes) the task file: archive-task.sh, adaptive tracking (inherits `tasks/`), collision-safe, append-only `_index.md` log

## Deployment
- `deployment/ci-structure-checks.md` — `check-structure.py` as the single automated guard for a build-less repo
