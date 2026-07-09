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

**Phase 3 of 6** — the pipeline can now **act**. `/swarm:review` fans a diff
across four voices (Claude lenses + `codex` + `grok-build` + `composer`),
merges by mechanism, verifies solo findings, presents one ranked report, and —
with `--fix` / `--loop` — applies the findings you agreed with.

## Commands

- `/swarm:review [ref | --staged | pathspec] [--fix | --loop[=N]]` — review a
  diff with the full ensemble. Defaults to the branch delta vs the default
  branch (including uncommitted work). `--fix` applies the agreed findings once;
  `--loop[=N]` re-reviews after each fix round until it converges (cap default
  `10`).
- `/swarm:agents` — show which review backends are installed, authenticated,
  and ready.

Planned: `/swarm:adversarial`, `/swarm:style`, `/swarm:security` (thin lens
presets).

## The pipeline (`/swarm:review`)

```
Scope+gate → Fan-out (Claude lenses ∥ codex ∥ grok-build ∥ composer)
          → Merge (file, mechanism) → Verify solos → Ranked synthesis
```

1. **Scope + gate** — a cheap agent classifies the diff and picks which Claude
   lenses are worth running (security is never gated out when code/args/files
   flow to an external process).
2. **Fan-out** — four voices in parallel: one Claude finder per gated lens plus
   `codex`, `grok-build` and `composer` as full reviews through the adapter.
3. **Merge** — an LLM step clusters findings by `(file, mechanism)`, not
   `(file, line)` (external CLIs number against the inlined diff).
4. **Verify** — solo clusters go through an adversarial 3-state verifier
   (`CONFIRMED`/`PLAUSIBLE`/`REFUTED`; only `REFUTED` is dropped).

**Consensus counts model *families*, not backends.** `grok-build` and
`composer` are both grok, so their agreement is one vote — a `CONSENSUS` tag
requires ≥2 of *claude / openai / grok*. Everything else is a solo and earns
its place through the verifier.

**Security is minimal by design.** The diff is fenced as untrusted data, the
external CLIs run sandboxed + tool-less (grok) with a secret scrub at the
adapter boundary, and a final **output gate** re-scrubs every surviving finding
before it reaches you. Findings are advisory by default; `--fix` / `--loop` act
only on the ones you agreed with, and **only Claude** applies edits — the
external agents stay review-only, never touching your code. The full threat
model lives in `docs/pipeline-blueprint.md` § Security.

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
| `codex` | external reviewer | `codex exec --output-schema` (model `gpt-5.6-terra`, effort `xhigh`) in a read-only sandbox; auth via `codex login status` |
| `grok` | external reviewer | headless `-p` with inline `--json-schema` (model `grok-build`); findings extracted from the response envelope. `--model grok-composer-2.5-fast` takes a separate defensive-parse path (a ~2×-faster second grok voice, no schema flag). |

Unavailable backends drop from the ensemble — `claude` alone still works.
`/swarm:review` reports a backend that *errored* mid-run distinctly from one
that cleanly found nothing (error ≠ empty).

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
