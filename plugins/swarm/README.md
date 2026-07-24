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

**Phase 5 of 6** — the pipeline can now **act** (P3/P4 lens presets still to
come). `/swarm:review` fans a diff
across three voices (Claude lenses + `codex` + `grok-4.5`),
merges by mechanism, verifies solo findings + design suggestions, presents one
ranked report, and —
with `--fix` / `--loop` — applies the findings you agreed with.

## Commands

- `/swarm:review [ref | --staged | pathspec] [--fix | --loop[=N]] [--max]` —
  review a diff with the full ensemble. Defaults to the branch delta vs the
  default branch (including uncommitted work). `--fix` applies the agreed
  findings once; `--loop[=N]` re-reviews after each fix round until it converges
  (cap default `10`); `--max` runs the deepest-effort profile (codex
  `gpt-5.6-sol`/`xhigh`, Claude finders + verifier `xhigh`, one Claude finder
  per **lens** instead of per cluster; grok already runs
  at `high`, its ceiling) — slower,
  more thorough, composes with `--fix`/`--loop`.
- `/swarm:review --pr [<number>]` — run the same ensemble against a **GitHub
  PR's diff** (`gh pr diff`; bare `--pr` resolves the current branch's PR) and,
  after a single confirmation, post the output-gated result as a PR comment via
  `gh pr comment` — the codex/grok/Claude voice on GitHub with no CI, repo
  secrets, or API-token cost. Read-only (never edits the tree); mutually
  exclusive with `--fix`/`--loop`. The comment is posted under your own `gh`
  identity, so it does not disturb pr-flow's `@claude` review polling.
- `/swarm:agents` — show which review backends are installed, authenticated,
  and ready.

Planned: `/swarm:adversarial`, `/swarm:style`, `/swarm:security` (thin lens
presets).

## The pipeline (`/swarm:review`)

```
Scope+gate → Fan-out (Claude lenses ∥ codex ∥ grok-4.5)
          → Merge (file, mechanism) → Verify (solos + design + unverified consensus) → Ranked synthesis
```

1. **Scope + gate** — a cheap agent classifies the diff and picks which Claude
   lenses are worth running (security is never gated out when code/args/files
   flow to an external process; design lenses are first-class, skipped only
   when the diff can't pay off for them).
2. **Fan-out** — three voices in parallel: one Claude finder per lens
   **cluster** (per lens under `--max`) plus `codex` and `grok-4.5` as full
   reviews through the adapter.
3. **Merge** — an LLM step clusters findings by `(file, mechanism)`, not
   `(file, line)` (external CLIs number against the inlined diff).
4. **Verify** — every solo, every design cluster (even with consensus), every
   all-untagged consensus, and every Claude-unchecked methodological consensus
   go through an adversarial 3-state verifier (`CONFIRMED`/`PLAUSIBLE`/`REFUTED`;
   only `REFUTED` is dropped); tagged topical-defect consensus is auto-accepted.
   Design findings get an **applicability** prompt instead (is the reuse target
   real? is the simpler form behavior-identical?) — same three states.

**11 lenses in 4 clusters** (the cluster is the Claude fan-out unit):

| Cluster | Lenses | Guiding question |
|---------|--------|------------------|
| `breakage` | correctness, removed-behavior, cross-file-trace | what breaks? |
| `threat` | security, adversarial | what's exploitable / which assumption fails? |
| `design` | reuse, simplification, efficiency, altitude | is this good, maintainable code? |
| `consistency` | style, conventions | does it fit the codebase? |

Design-lens findings carry `kind: "design"` and render in their own report
section, so suggestions never dilute the defect ranking.

**Consensus counts model *families*, not voices.** Several Claude lenses
flagging the same thing is one vote, not a cross-check — a `CONSENSUS` tag
requires ≥2 of *claude / openai / grok*. Everything else is a solo and earns
its place through the verifier. Only **tagged topical-defect** consensus is
auto-accepted; design, all-untagged, and Claude-unchecked methodological
consensus still go through the verifier (agreement isn't repo-grounded
applicability — externals can still share a hallucination).

**Security is layered by design.** Untrusted text is fenced with a per-run
random nonce at both hops — the diff going into the backends, and the finding
text they send back into the merge/verify prompts (closing second-order
injection). External CLIs run **read+web** (file-read to find out-of-diff bugs;
web for external knowledge only) under an OS secret-jail that denies HOME secret
stores and repo-root `.env*`/`data/`/key/cred files (root-level; nested via
`SWARM_DENY_PATHS`; the main checkout too in a linked worktree) — no write/shell
tools. On a host with no working sandbox the adapter **fails closed per voice**
(grok tool-less/no-web, codex web hard-off) rather than running read+web bare. A
prompt egress guard forbids putting repo content into web queries (model-
cooperation-dependent; the jail is the hard boundary). A secret scrub at the
adapter boundary plus a final **output gate** re-scrub findings before they reach you.
Findings are advisory by default; `--fix` / `--loop` act only on the ones you
agreed with, and **only Claude** applies edits — external agents stay
review-only. The full threat model lives in `docs/pipeline-blueprint.md`
§ Security.

## Architecture

### Backend adapter (`scripts/agents.sh`)

All deterministic backend logic lives in one script; skills never call the
external CLIs directly:

```
agents.sh list [--json]       # probe all backends → status table / JSON
agents.sh available <backend> # installed? prints version
agents.sh ready <backend>     # authenticated? hint on stderr if not
agents.sh jail                # jail=yes|no — will read+web be granted? (working
                              # OS sandbox AND a resolvable repo root)
agents.sh run <backend> [--prompt-file f] [--effort E] [--model M] [--schema f]
                              # lens prompt in → findings JSON out
```

Backends:

| Backend | Role | Mechanics |
|---------|------|-----------|
| `claude` | probe-only | reviews run in-session via the Agent tool |
| `codex` | external reviewer | `codex exec -s read-only -C <repo> -c tools.web_search=true --output-schema` (model `gpt-5.6-terra`); file-read + web under read-only; auth via `codex login status` |
| `grok` | external reviewer | headless `--single=` with inline `--json-schema` (model `grok-4.5`, the only supported grok model); strict `--tools` allowlist (`read_file,list_dir,grep,web_search,web_fetch`) + `--cwd <repo>` — no write/shell. Readiness is model-aware: auth **and** `grok-4.5` present in `grok models`. |

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
