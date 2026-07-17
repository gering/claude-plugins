---
title: "Kickoff Agent Selection: registry, per-repo default, honest degradation"
createdAt: 2026-07-17
updatedAt: 2026-07-17
createdFrom: "session: 2026-07-17 (task/kickoff-agent-selection)"
updatedFrom: "session: 2026-07-17"
pluginVersion: 1.9.0
---

# Kickoff Agent Selection

`/kickoff` picks the worktree worker (CLI × model) instead of hardcoding
`claude`. The non-obvious shape and the decisions behind it:

## Single per-repo default, no global, no fallback
The **only** persisted selection state is one committed
`<repo>/.claude/work-system-agent` (`default=<cli:model>`). No global per-user
default, no shipped fallback, no `--auto` ranking, no `--last`. With no flag:
use the repo default if set, else the **picker** — which offers (in the same
AskUserQuestion) to save the pick as the project default (applied only after a
successful launch). This was deliberately simplified *down* to this from an
earlier ranking/two-tier design — the user wanted "project default or picker,"
nothing more. `--pick` forces the picker even when a default exists.

## Registry is the single source of truth
`scripts/agent-registry.sh` owns aliases (`--fable`/`--opus`/`--codex`/`--sol`/
`--grok`/`--agent cli[:model]`), the launch argv per CLI, availability, and
`default get`/`set`. `herdr-launch.sh` stays CLI-agnostic: it execs the resolved
`argv=` words (argv-exec, no shell-typing race — same reason as the kickoff
launch). Skills never hardcode the CLI list. `default get` **validates** its
committed value against the registry — a stale/removed/attacker-supplied name
reads as "no default" (→ picker), never routes or bricks kickoff.

## grok availability is model-aware and bounded
grok drops/renames models between releases (composer `grok-composer-2.5-fast`
died in 0.2.10x; `grok-build`→`grok-4.5` before that). So grok availability
checks the model is in `grok models`, not just auth. The probe is **always
bounded** (timeout → gtimeout → a self-contained background-killer watchdog with
fds detached so the command substitution doesn't block) so `list`/the picker
never hangs. A failed *or* empty-but-successful (reformatted) `grok models` is
**inconclusive → trust auth (available)**, not "model gone" — a network hiccup
or format drift must not disable the backend. codex/claude stay auth-only (no
clean model-list command). See [[swarm-backend-adapter]] for the sibling probe.

## Non-claude degradation: document, don't fake
codex/grok have no work-system skills, so a launched worker gets a bootstrap
prompt (read TASK.md → commit → PR) instead of `/continue`. Everything
git/PR-derived (`/status`, `/list`, `[ws]` statusline, `/close` tab teardown)
is CLI-agnostic. `/close` Scenario B (`/exit` self-teardown) is claude-only *by
construction* (only a claude session can invoke `/close` from inside its tab).
`/continue` reopen **always sends `claude -c`** — the worker is not persisted
per task (per-task agent memory is a deliberate later idea), so for a codex/grok
task the user resumes the real worker themselves; the skill surfaces this inline
rather than pretending. `supports=` in the registry is **reserved** metadata
(the seed for the manager/worker-orchestration design) — not yet consumed.

## Security: announce, don't prompt
A committed external-worker default routes worktree code to a third-party CLI.
The chosen mitigation is to **announce** ("Launching codex — project default…")
before launch, not a consent prompt — an explicit product decision (a cloned
repo can already run hooks/CLAUDE.md, and the committed-default-launches-silently
UX was intentional).
