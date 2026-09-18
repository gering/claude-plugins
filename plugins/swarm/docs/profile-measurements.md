# Profile measurement notes

## Status — 2026-09-17

**The planned live checks are complete:** all three Codex model/effort smoke
calls and all six Kimi comparison calls returned valid JSON with backend and
adapter exit codes 0. Both diff-only calls reported zero observed tool calls.
No token/quota savings or latency improvement are established by this sample.

Auto Mode twice refused a call before provider execution. Work resumed only
after the operator explicitly restored manual permissions; successful calls
were not repeated. No alternate session or transport bypassed either refusal.

**Codex actual-load checks passed on 2026-09-14.** Each requested model/effort configuration
returned exactly `{"findings":[]}` through the current jailed adapter, with
backend and adapter exit codes 0. The assembled smoke prompt was 472 bytes and
requested no tools or repository exploration; each call had a 120-second cap.
This verifies invocation/schema compatibility, not review quality or hidden
provider routing. No fallback model was selected.

| Profile | Requested model | Effort | Seconds | Result |
|---|---|---|---:|---|
| quick | gpt-5.6-sol | low | 5 | valid JSON, rc 0 |
| default | gpt-5.6-sol | medium | 5 | valid JSON, rc 0 |
| max | gpt-6-astra | medium | 6 | valid JSON, rc 0 |

## Measured locally: contract bytes

These are deterministic UTF-8 byte counts, **not token or quota savings**.
The schema is `scripts/schema/finding.schema.json`; the baseline is
`agents.sh` at `6aabecb` (swarm 0.11.0). Only `_kimi_output_contract` output is
counted, including whitespace. No backend was called.

| Contract | Bytes | Reduction from baseline |
|---|---:|---:|
| Baseline, tools enabled / eight calls | 3657 | — |
| Compact, tools enabled / eight calls | 1216 | 2441 |
| Compact, diff-only / zero calls | 985 | 2672 |

The compact contract removes schema descriptions and formatting, but preserves
validation constraints, property names and literal instance values (`const`,
`enum`, etc.). The original schema still validates the result. This trims only
the contract: a large diff still dominates the input. No per-cluster diff slice,
extra fence or coverage truncation was introduced.

## Live Kimi comparison

All completed calls used `kimi-code/k3-256k`, low effort, a 540-second adapter
cap and the identical saved prompt for each unit. Every completed call returned
schema-valid findings with backend/adapter rc 0 and complete tool-call
observations. Durations are the adapter's measured backend interval, excluding
readiness probes. Each cell is a **single run**, not a repeated benchmark.

| Contract / unit | Completed UTC | Prompt bytes | Observed tool calls | Seconds |
|---|---|---:|---:|---:|
| Baseline / breakage | 2026-09-14 15:05 | 58455 | 1 | 124 |
| Baseline / threat | 2026-09-16 10:04 | 58459 | 0 | 280 |
| Compact, tools / breakage | 2026-09-16 10:50 | 56014 | 2 | 163 |
| Compact, tools / threat | 2026-09-16 15:20 | 56018 | 3 | 229 |
| Compact, diff-only / breakage | 2026-09-16 19:33 | 55783 | 0 | 197 |
| Compact, diff-only / threat | 2026-09-17 10:29 | 55787 | 0 | 356 |

Every comparison pair saves exactly **2441 prompt bytes** with tools enabled
or **2672 bytes** in diff-only mode. Diff-only produced accepted reviews with
zero observed tools, but was slower than baseline on both units in this sample.
The compact tools-enabled contract elicited more tool calls than baseline;
latency increased for Breakage and decreased for Threat.

Baseline tool use was already sparse. These observations do **not** demonstrate
a reduced billing multiplier, overall latency or quota consumption. Runs
spanned multiple days; model sampling, load and caching were not controlled.
Findings were collected only for this resource measurement, not adversarially
verified or treated as a review-quality benchmark. Kimi stays opt-in, and this
small sample does not justify changing the default profile's tool policy.

## Reproducing the comparison

The two saved per-unit prompts were verified to contain the exact diff below,
and the baseline contract was compared byte-for-byte with the original before
execution. Completed calls were not repeated automatically.

- Source: `git diff 163908b^ 163908b -- plugins/swarm/scripts/kimi-acp.py`.
- Raw diff bytes: **54,069**.
- Raw diff SHA-256: `4b238c1eabb18a17b7322522a6bb2a8fb219c7d616464d5f8f0d645925777a51`.
- Two units, sequentially: breakage (correctness/removed-behavior), then threat
  (security/adversarial); stop on a failed or quota-blocked first call.
- Baseline: `kimi-code/k3-256k`, low effort, existing tools-enabled/eight-call
  contract. Capture with the instrumentation-only version; using the compact
  contract and calling it the baseline would invalidate the comparison.
- Comparison: the identical fenced diff and unit briefs, first with the compact
  tools-enabled contract, then—only with adequate authorized quota—with the
  quick diff-only policy. Keep model, effort and deadline fixed when comparing
  prompt/tool policy.
- Keep repository text inside the untrusted-data fence, retain the egress guard,
  OS jail, schema validation and explicit Kimi opt-in. No automatic retries.
- Record assembled `prompt_bytes`, observed `tool_calls`, completeness,
  `seconds`, effective deadline and success/failure separately for each unit.
  Record actual provider usage only if the provider exposes it; never derive
  billing from a byte count. Preserve measurements before scratch cleanup.

The instrumentation-only code is not a separate released version. To recreate
its baseline later, retain the measured baseline contract above while using the
new counter; do not downgrade the jail or copy old authentication state.

## What the telemetry means

`prompt_bytes` is the assembled prompt-file size, including lens/policy text and
Kimi's appended schema. `tool_calls` counts distinct observed ACP tool IDs across
permission requests, announcements and status updates, including rejected
attempts; an update is not a second call. `tool_calls_complete` says whether the
stream was fully observed and countable. Missing/invalid observations stay
`null`; partial observed counts are labeled partial. Hard process termination
can leave a partial or absent sidecar.

`bytes / 4 × (1 + tool_calls)` is only a context-exposure heuristic. ACP sends
one prompt to the CLI; provider requests happen inside it. Batching, tokenizer,
hidden context, tool output growth, caching and quota accounting are not
observed by this counter. Therefore it cannot establish a retransmission
multiplier or actual billed tokens. Kimi remains opt-in regardless of this
comparison's eventual result.

## Codex catalog evidence

The non-generative probe implements the installed Codex 0.153.4 contract:
`initialize` → `initialized` → paginated `model/list` with `includeHidden:true`.
All pages share one bounded deadline. Complete pagination does **not** make the
catalog authoritative for provider availability: refresh failures may fall back
to bundled/cached models, and custom aliases can work without being listed.
Missing/unknown selections must remain visibly unverified, not be silently
substituted or falsely rejected. The probe may refresh Codex's own auth/cache.

Version-pinned upstream references:

- [JSONL and initialization](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/app-server/README.md)
- [Model-list request/response types](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/app-server-protocol/src/protocol/v2/model.rs)
- [Cache fallback and explicit model IDs](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/models-manager/src/manager.rs)
- [Catalog endpoint and authentication](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/model-provider/src/models_endpoint.rs)

## Grok runtime remains separate

Profiles do not lift the synchronous Bash window. The backend deadline remains
below 600 seconds after the existing probe/cleanup margin. The
`async-poll-external-voices` task owns crossing that ceiling and must coordinate
with `fix-swarm-review-runtime-handoff` so there is only one runner. More actual
Grok execution time—not merely a lower effort or tool budget—remains an open
integration requirement.
