# Swarm

Local mixture-of-agents code review for Claude Code. Fans out one review
across multiple independent agents — Claude subagents plus the `codex` and
`grok` CLIs, and `kimi` on request (`--kimi`) — merges and deduplicates their
findings, and presents a single ranked report. Cross-family agreement is a strong confidence signal when it
occurs; single-family findings (the common case) pass an adversarial 3-state
verifier so real catches survive and noise is dropped.

Complementary to [pr-flow](../pr-flow/): pr-flow drives the GitHub-PR
`@claude`-bot loop; swarm reviews **locally**, before anything is pushed.

## Status

**Phase 5 of 6** — the pipeline can now **act**. `/swarm:review` fans a diff
across up to four model families (Claude lenses + `codex` + `grok`, plus
`kimi` when opted in), each running one call per gated lens cluster, merges by mechanism, verifies solo
findings + design suggestions, presents one ranked report, and — with `--fix` /
`--loop` — applies the findings you agreed with.

## Commands

- `/swarm:review [ref | --staged | pathspec] [--fix | --loop[=N]] [--max] [--kimi]` —
  review a diff with the full ensemble. Defaults to the branch delta vs the
  default branch (including uncommitted work). `--fix` applies the agreed
  findings once; `--loop[=N]` re-reviews after each fix round until it converges
  (cap default `10`); `--max` runs the deepest-effort profile (codex
  `xhigh`, Claude finders + verifier `xhigh`, grok `low` → `medium`, Kimi
  `low` → `high`, and **every** voice — Claude, codex, grok, kimi — fanning
  out per **lens** instead of per cluster; kimi stays on its breakage + threat
  lenses on both profiles, being quota-metered) — slower, more
  thorough, costs up to `3 × 11` external calls, composes with
  `--fix`/`--loop`. `--kimi` opts Kimi in for the run (the fourth family):
  Moonshot meters its CLI on 5-hour and 7-day quotas, and one two-cluster
  review drained the entry plan's 5-hour window — every ACP tool round-trip
  re-sends the whole ~370 KiB cluster prompt — so a stock review is the
  three-family ensemble. `export SWARM_KIMI=1` opts in permanently.
- `/swarm:review --pr [<number>]` — run the same ensemble against a **GitHub
  PR's diff** (`gh pr diff`; bare `--pr` resolves the current branch's PR) and,
  after a single confirmation, post the output-gated result as a PR comment via
  `gh pr comment` — the codex/grok/kimi/Claude ensemble on GitHub with no CI, repo
  secrets, or API-token cost. Read-only (never edits the tree); mutually
  exclusive with `--fix`/`--loop`. The comment is posted under your own `gh`
  identity, so it does not disturb pr-flow's `@claude` review polling.
- `/swarm:agents` — show which review backends are installed, authenticated,
  and ready.

Planned: `/swarm:adversarial`, `/swarm:style`, `/swarm:security` (thin lens
presets).

## The pipeline (`/swarm:review`)

```
Scope+gate → Fan-out (Claude lenses ∥ codex ∥ grok ∥ kimi)
          → Merge (file, mechanism) → Verify (solos + design + unverified consensus) → Ranked synthesis
```

1. **Scope + gate** — a cheap agent classifies the diff and picks which lenses
   are worth running, **for every voice** (design lenses are first-class,
   skipped only when the diff can't pay off for them). `security`,
   `adversarial` and `correctness` are a **mandatory floor the gate cannot
   prune** — since it now prunes for everyone, a lens it drops would be reviewed
   by nobody. Every pruned lens is reported as gated-out, never silently
   dropped.
2. **Fan-out** — all voices at the **same granularity**: one Claude finder per
   gated lens **cluster**, and `codex` + `grok` (+ `kimi` when opted in) each
   once per gated cluster too (per lens under `--max`). The gate prunes calls for everyone — a
   fully-gated-out cluster spawns nothing for any voice — and each finding's
   `[lens]` tag is authoritative, because the voice *is* that lens.
3. **Merge** — an LLM step clusters findings by `(file, mechanism)`, not
   `(file, line)` (external CLIs number against the inlined diff).
4. **Verify** — every solo, every design cluster (even with consensus), every
   all-untagged consensus, and every Claude-unchecked methodological consensus
   go through an adversarial 3-state verifier (`CONFIRMED`/`PLAUSIBLE`/`REFUTED`;
   only `REFUTED` is dropped); tagged topical-defect consensus is auto-accepted.
   Design findings get an **applicability** prompt instead (is the reuse target
   real? is the simpler form behavior-identical?) — same three states.

**11 lenses in 5 clusters** (the cluster is the fan-out unit for *every* voice):

| Cluster | Lenses | Guiding question |
|---------|--------|------------------|
| `breakage` | correctness, removed-behavior | what breaks? |
| `reach` | cross-file-trace | what else does this touch? |
| `threat` | security, adversarial | what's exploitable / which assumption fails? |
| `design` | reuse, simplification, efficiency, altitude | is this good, maintainable code? |
| `consistency` | style, conventions | does it fit the codebase? |

`reach` is a one-lens cluster on purpose — because of measured **lens
crowd-out**, not speed. In a combined three-lens `breakage` call, 3 of 4 findings
came from `cross-file-trace` alone; split apart, the remaining two lenses
produced 4 findings the combined call had missed. Isolation also means a timeout
there costs one lens rather than three, and the gate can prune the whole call on
a diff with no cross-file surface. It does **not** make the review faster: the
longest single call drops 374 s → 313 s, and total work rises.

Design-lens findings carry `kind: "design"` and render in their own report
section, so suggestions never dilute the defect ranking.

**Consensus counts model *families*, not voices.** Several Claude lenses
flagging the same thing is one vote, not a cross-check — a `CONSENSUS` tag
requires ≥2 of *claude / openai / grok / moonshot*. Everything else is a solo and earns
its place through the verifier. Only **tagged topical-defect** consensus is
auto-accepted; design, all-untagged, and Claude-unchecked methodological
consensus still go through the verifier (agreement isn't repo-grounded
applicability — externals can still share a hallucination).

**Security is layered by design.** Untrusted text is fenced with a per-run
random nonce at both hops — the diff going into the backends, and the finding
text they send back into the merge/verify prompts (closing second-order
injection). External CLIs run **read+web** (file-read to find out-of-diff bugs;
web for external knowledge only) under an OS jail that (1) denies HOME secret
stores and repo-root `.env*`/`data/`/key/cred files (root-level; nested via
`SWARM_DENY_PATHS`; the main checkout too in a linked worktree) and (2)
**inverts the write model**: every write is denied, then only the scratch dir,
the per-user temp/cache dirs, `/dev` and the backend's own auth state are
re-allowed, and the reviewed repository/Git directories plus the host's shell
startup files and agent config surfaces are denied again on top (`sandbox-exec`
last-match-wins rules / `bwrap` read-only root with writable binds, plus
`GIT_OPTIONAL_LOCKS=0`). That is what makes a **shell** affordable for every
external voice — `git log/show/blame`, grep pipelines — with the jail, not the
CLI's own permission model, as the boundary. On a host
with no working sandbox the adapter **fails closed per voice** (grok
tool-less/no-web, codex web hard-off inside its own read-only sandbox) rather than running read+web bare. A
prompt egress guard forbids putting repo content into web queries (model-
cooperation-dependent; the jail is the hard boundary). A secret scrub at the
adapter boundary plus a final **output gate** re-scrub findings before they reach you.
Kimi is **opt-in** (`--kimi` / `SWARM_KIMI=1`; un-opted, `list --json` says
"opt-in only" without spending a probe) and **ready only under this OS jail**
(the hint says so), and runs with an ephemeral HOME that holds only links to its managed-provider
auth directories (a refresh must land on the host file — Moonshot rotates
refresh tokens, and a refresh inside a private copy logged the operator out)
and a filtered projection of its config
(provider/model catalogue and search services — never hooks or MCP); the
repository's own `.kimi-code/`, `.kimi/` and `.mcp.json` are denied to it as
well. Kimi keeps its shell for **read-only commands** (`git log/show/blame/
diff`, grep/rg pipelines, `ls`, `cat`…): the ACP client vets every command
against a positive allowlist — no chaining, redirection, substitution, config
injection (`git -c`), `find -exec`, `rg --pre` — approves an allowlisted one
when Kimi asks, and kills the session on first sight of anything else (any
other tool kind outside read/search/fetch/think likewise). That gate is
defense-in-depth, not the write boundary. grok runs from an **ephemeral
HOME/GROK_HOME** too (neutral `.claude/settings.json`, only `auth.json` linked
back): grok 1.0 otherwise loads the operator's Claude Code settings —
permission rules AND hooks (it ran a SessionStart hook), plugins with MCP
servers, the global `Claude.md`, and from the reviewed repo `CLAUDE.md`,
`.claude/rules`, `.mcp.json`, `.grok/` — all denied to it now. codex runs with
`--ignore-user-config --ignore-rules` and, under the jail, **without its own
seatbelt**: a nested `sandbox-exec` fails against any outer deny rule, which had
silently killed every codex shell command (and so every file read) since the
jail arrived. Documented residuals: the jail has no network rule (`--deny`
prefix rules keep grok off `curl`/`ssh`/`git push`…, prompt-level only), and
arbitrary child-process execution inside the jail is not prevented.
Findings are advisory by default; `--fix` / `--loop` act only on the ones you
agreed with, and **only Claude** applies edits — external agents stay
review-only. The full threat model lives in `docs/pipeline-blueprint.md` § Security.

## Architecture

### Backend adapter (`scripts/agents.sh`)

All deterministic backend logic lives in one script; skills never call the
external CLIs directly:

```
agents.sh list [--json]       # probe all backends → status table / JSON
agents.sh available <backend> # installed? prints version
agents.sh ready <backend>     # authenticated? hint on stderr if not
agents.sh config              # resolved numeric config (caps, timeouts,
                              # probe budget) — the ONE parser; the review
                              # skill reads these instead of re-deriving them
agents.sh jail                # jail=yes|no — will read+web be granted? (working
                              # OS sandbox AND a resolvable repo root)
agents.sh run <backend> [--prompt-file f] [--lens-instr s --lens-instr-sum hex]
                        [--effort E] [--model M] [--schema f]
                        [--telemetry f --unit name]
                              # lens prompt in → findings JSON out
                              # --lens-instr: the gated cluster's lens briefs,
                              # prepended verbatim before the prompt body. The
                              # workflow passes it on every per-cluster call;
                              # an empty value is refused, never run lens-free.
                              # --lens-instr-sum: FNV-1a/32 of that text, and
                              # REQUIRED with it — the transport retypes the
                              # instruction, so the adapter verifies it rather
                              # than trusting it (a reworded scope would
                              # otherwise be reported under the wrong lenses).
```

Backends:

| Backend | Role | Mechanics |
|---------|------|-----------|
| `claude` | probe-only | reviews run in-session via the Agent tool |
| `codex` | external reviewer | `codex exec -s danger-full-access --ignore-user-config --ignore-rules -C <repo> -c tools.web_search=true --output-schema` under the OS jail (its own seatbelt cannot nest inside it; `-s read-only` is kept only on a jail-less host), model `gpt-5.6-sol`, `medium` by default / `xhigh` under `--max`, prompt on stdin (`-- -`); shell + file-read + web; auth via `codex login status` |
| `grok` | external reviewer | headless `--prompt-file` with inline `--json-schema`; the model is **discovered** — the newest canonical id whose schema enforcement is verified (the current set lives in `GROK_SCHEMA_VERIFIED` in `agents.sh`), never a silent upgrade to an unverified one. Strict `--tools` allowlist (`read_file,list_dir,grep,run_terminal_command,web_search,web_fetch`) + `--permission-mode dontAsk` + `--deny` prefix rules (egress/destructive verbs) + `--cwd <repo>`, run from an ephemeral HOME/GROK_HOME with only `auth.json` linked — the OS jail's inverted write model makes the shell read-only in effect. Readiness is model-aware: auth, `--prompt-file` support, **and** a verified model on offer in `grok models`. `ready` answers usable/not-usable plus a hint; the concrete id is selected at `run` time and appears in that call's telemetry line. |
| `kimi` | external reviewer | ACP v1 over stdio (`kimi acp`), pinned to `kimi-code/k3-256k`; the complete prompt is an ACP content block, not argv. Isolated HOME/KIMI_CODE_HOME that links the host's `credentials/`+`oauth/` (links, not a copy — Moonshot rotates refresh tokens, so a refresh must land on the host file) and carries a filtered config projection. The client advertises no FS/terminal capability and approves only allowlisted read-only shell commands once and rejects every other permission request (defense-in-depth); repository immutability is OS-enforced. Invalid output or policy/protocol drift is a visible backend error, never an empty review. Requires auth, ACP, the pinned model, and a working OS jail. |

The prompt always reaches a backend **out-of-band** — never as an argv word — so
the diff is bounded by model context rather than `exec`'s `MAX_ARG_STRLEN`:
stdin for codex, `--prompt-file` for grok, and an ACP `session/prompt` content
block for Kimi. `SWARM_MAX_PROMPT_BYTES` (default 512 KiB) is that sanity cap;
above it `/swarm:review` cleanly skips the externals instead of letting each
call fail. Kimi receives the schema as a high-priority output contract in that
prompt, then the adapter validates its final JSON locally. There is no retry:
an invalid response fails closed immediately rather than multiplying up to
5 default or 11 `--max` calls.
`SWARM_PROBE_TIMEOUT` (default 10 s) bounds the short readiness probes and is
capped at 20 s — the review's timeout margin is derived from it, so a larger
value would eat the wall it is meant to protect; `run` and `config` refuse
anything above the ceiling rather than normalizing it.

Each external call is timed (`--telemetry <file> --unit <name>`), including
Kimi's effective ACP model/thinking level and adapter-side schema rejection.
The report flags any voice that spent most of the wall **that call actually ran under** —
recorded per record, not assumed from `SWARM_TIMEOUT`, which is overridable and
which the workflow shrinks by its probe margin. A call that *survives* near the
wall is invisible in the error list but is the one about to start failing.
`backend_rc=0` with a non-zero adapter result means the backend replied
but its response was rejected; an ACP negotiation failure before `session/prompt`
keeps `backend_rc` null instead of claiming a model response.

Unavailable backends drop from the ensemble — `claude` alone still works.
`/swarm:review` reports a backend that *errored* mid-run distinctly from one
that cleanly found nothing (error ≠ empty).

### Shared findings schema (`scripts/schema/finding.schema.json`)

Every external backend is normalized through the same JSON schema: codex and
grok enforce it in their CLIs; Kimi is instructed with it and then validated
strictly by the local ACP client. The ensemble merge therefore receives uniform
findings, while malformed Kimi output becomes a visible backend error:

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
- `codex`, `grok`, and/or `kimi` CLIs are optional — install and authenticate
  them to widen the ensemble. Kimi additionally needs ACP support, the adapter's
  pinned model (`KIMI_DEFAULT_MODEL`; the `list --json` hint names it), a
  working OS jail, and the per-run opt-in (`--kimi`, or `SWARM_KIMI=1`).
