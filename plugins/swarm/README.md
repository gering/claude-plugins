# Swarm

Local mixture-of-agents code review for Claude Code. Fans out one review
across multiple independent agents — Claude subagents plus the `codex` and
`grok` CLIs — merges and deduplicates their findings, and presents a single
ranked report. Cross-agent agreement is a strong confidence signal when it
occurs; single-agent findings (the common case) pass an adversarial 3-state
verifier so real catches survive and noise is dropped.

Complementary to [pr-flow](../pr-flow/): pr-flow drives the GitHub-PR
`@claude`-bot loop; swarm reviews **locally**, before anything is pushed.

## Status

**Phase 1 of 6** — plugin scaffold + backend adapter layer. The review
pipeline (`/swarm:review`) lands in later phases.

## Commands

- `/swarm:agents` — show which review backends are installed, authenticated,
  and ready.

Planned: `/swarm:review` (main command), `/swarm:adversarial`, `/swarm:style`,
`/swarm:security` (thin lens presets).

## Architecture

### Backend adapter (`scripts/agents.sh`)

All deterministic backend logic lives in one script; skills never call the
external CLIs directly:

```
agents.sh list [--json]       # probe all backends → status table / JSON
agents.sh available <backend> # installed? prints version
agents.sh ready <backend>     # authenticated? hint on stderr if not
agents.sh run <backend> [--prompt-file f] [--effort E] [--model M] [--schema f]
                              # lens prompt in → findings JSON out
```

Backends:

| Backend | Role | Mechanics |
|---------|------|-----------|
| `claude` | probe-only | reviews run in-session via the Agent tool |
| `codex` | external reviewer | `codex exec --output-schema` in a read-only sandbox; auth via `codex login status` |
| `grok` | external reviewer | headless `-p` with inline `--json-schema`; findings extracted from the response envelope |

Unavailable backends drop silently from the ensemble — `claude` alone still
works.

### Shared findings schema (`scripts/schema/finding.schema.json`)

Both external CLIs enforce the same JSON schema on their output, so the
ensemble merge receives uniform findings:

```json
{
  "findings": [
    {
      "file": "scripts/foo.sh",
      "line": 42,
      "severity": "warning",
      "summary": "One-sentence statement of the defect",
      "failure_scenario": "Concrete, falsifiable inputs → wrong behavior",
      "confidence": "high",
      "recommendation": "Suggested fix"
    }
  ]
}
```
Severity is one of `critical | warning | minor`; confidence one of
`high | medium | low`.

`failure_scenario` is required and must be falsifiable — it is what the
verifier tests in the confidence phase.

## Requirements

- `python3` on PATH (JSON handling in the adapter).
- `codex` and/or `grok` CLIs are optional — install and authenticate them to
  widen the ensemble.
