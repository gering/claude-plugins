# `/swarm:review` Pipeline Blueprint (P2–P5)

> Working proof-of-concept for the review pipeline, validated by two live
> ensemble dry-runs on this very branch (2026-07-02/03, runs `wf_b692c02d-990`
> and `wf_354898e8-770`). This is the **starting point for P2**, not shipped
> code — P2 turns it into a registered workflow script under `plugins/swarm/`.
> Design rationale lives in the working task file (kept locally, not committed
> to the repo); this file is the concrete shape.

## Pipeline shape (4 phases)

```
Scope+gate → Fan-out (Claude lenses ∥ codex ∥ grok) → Merge (file,mechanism) → Verify solos → Synthesis
```

1. **Scope + lens gating** — one cheap agent reads the diff, classifies the
   change kind, and decides which lenses are worth running (a lens that can't
   pay off is skipped → whole finder agents saved). Gated-out lenses are
   reported, never silently dropped.
2. **Fan-out** — in parallel: one Claude finder per gated lens (reads the file
   itself → real line numbers) **plus** codex and grok as full multi-lens
   reviews through the adapter (thin wrapper agents; workflow scripts have no
   Bash). All emit the shared `finding.schema.json`.
3. **Merge** — an LLM merge step clusters the pooled findings by
   `(file, mechanism)` — **not** `(file, line)`: external CLIs number against
   the inlined diff, so line equality never matches (dry-run learning L1).
   Consensus = ≥2 distinct backends in one cluster ⇒ CONFIRMED.
4. **Verify** — every solo cluster (one backend only) goes through an
   adversarial 3-state verifier (CONFIRMED/PLAUSIBLE/REFUTED; PLAUSIBLE is the
   default, only REFUTED is dropped). Consensus clusters get a *lighter* verify
   rather than a blind skip — agreement across backends that share the same
   inlined diff + schema + near-identical prompts can be correlated bias, not
   independent proof (see § Security). Prefer cross-family agreement.
5. **Synthesis** — rank (severity, then consensus>solo) and emit the balance
   data (severity split, consensus/solo, per-lens raw vs. surviving).

## Reference script (erprobt — `wf_354898e8-770`)

Path constants are the only thing P2 must rewrite: `ADAPTER` →
`${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh`; the diff/prompt files come from the
scope step (write to a temp path, or have finders read the diff directly).

```js
export const meta = {
  name: 'swarm-review',
  description: 'Local mixture-of-agents review: scope+gate → fan-out → (file,mechanism) merge → verify solos → ranked synthesis.',
  phases: [
    { title: 'Scope', detail: 'classify diff + gate lenses' },
    { title: 'Fan-out', detail: 'Claude lenses + codex + grok in parallel' },
    { title: 'Merge', detail: 'cluster by (file, mechanism)' },
    { title: 'Verify', detail: '3-state verify of solos' },
  ],
}

const ADAPTER = '${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh'  // P2: resolve plugin root
const DIFF_FILE = '<scope-writes-this>'       // diff text for finders to read
const EXTERNAL_PROMPT = '<scope-writes-this>' // full-review prompt for codex/grok (diff inlined)

const CANDIDATE_LENSES = ['correctness', 'security', 'style', 'adversarial', 'conventions']
const LENS_BRIEF = {
  correctness: 'shell quoting/word-splitting, exit codes, set -euo pipefail, JSON handling, argv/ARG_MAX, edge cases',
  security: 'command/argument injection via prompt or filename, unsafe temp files, data leakage',
  style: 'duplication, dead code, unclear constructs, inconsistent idioms',
  adversarial: 'challenge the design/assumptions: what did the author assume that the diff does not guarantee?',
  conventions: 'repo conventions: naming, doc/README sync, version-sync, sibling-script idioms',
}
const FINDINGS_SCHEMA = { /* shared shape — mirrors scripts/schema/finding.schema.json */
  type: 'object', additionalProperties: false, required: ['findings'],
  properties: { findings: { type: 'array', items: {
    type: 'object', additionalProperties: false,
    required: ['file', 'line', 'severity', 'summary', 'failure_scenario', 'confidence', 'recommendation'],
    properties: {
      file: { type: 'string' }, line: { type: 'integer' },
      severity: { enum: ['critical', 'warning', 'minor'] },
      summary: { type: 'string' }, failure_scenario: { type: 'string' },
      confidence: { enum: ['high', 'medium', 'low'] }, recommendation: { type: 'string' },
    },
  } } },
}

// Phase 1: Scope + lens gating
phase('Scope')
const GATE_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['change_kind', 'run', 'skip'],
  properties: {
    change_kind: { type: 'string' },
    run: { type: 'array', items: { type: 'string' } },
    skip: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['lens', 'why'], properties: { lens: { type: 'string' }, why: { type: 'string' } } } },
  },
}
const gate = await agent(
  `You are the scope/lens-gating step of a code review. Read the unified diff at ${DIFF_FILE}.\n` +
  `Candidate lenses: ${CANDIDATE_LENSES.join(', ')}.\n` +
  `Decide which lenses are worth running and which to skip because they cannot pay off. Be decisive but do NOT skip security when any code/argument/filename flows to an external process.\n` +
  `Return change_kind, run (lens names), skip (lens + one-clause why).`,
  { label: 'scope+gate', phase: 'Scope', schema: GATE_SCHEMA, model: 'haiku', effort: 'low' }
)
const runLenses = (gate?.run || CANDIDATE_LENSES).filter((l) => CANDIDATE_LENSES.includes(l))

// Phase 2: Ensemble fan-out
phase('Fan-out')
const claudeThunks = runLenses.map((lens) => () =>
  agent(
    `You are the "${lens}" lens finder. Read the diff at ${DIFF_FILE} and review ONLY through the ${lens} lens: ${LENS_BRIEF[lens]}.\n` +
    `One finding per defect, concrete falsifiable failure_scenario. Prefix each summary with "[${lens}] ". Empty is valid. Cite real file lines.`,
    { label: `claude:${lens}`, phase: 'Fan-out', schema: FINDINGS_SCHEMA, effort: 'medium' }
  ).then((r) => ({ backend: 'claude', lens, findings: r?.findings || [] })).catch(() => ({ backend: 'claude', lens, findings: [] }))
)
const externalThunks = ['codex', 'grok'].map((b) => () =>
  agent(
    `You are a thin transport wrapper — do NOT review yourself. Run this with the Bash tool (timeout 600000), wait for it:\n\n` +
    `bash "${ADAPTER}" run ${b} --effort high --prompt-file "${EXTERNAL_PROMPT}"\n\n` +
    `It prints one JSON object {"findings":[...]} on stdout. Return it VERBATIM. On non-zero exit / no JSON, return {"findings":[]}.`,
    { label: `${b}:full`, phase: 'Fan-out', schema: FINDINGS_SCHEMA, agentType: 'general-purpose', model: 'haiku', effort: 'low' }
  ).then((r) => ({ backend: b, lens: null, findings: (r && Array.isArray(r.findings)) ? r.findings : [] })).catch(() => ({ backend: b, lens: null, findings: [] }))
)
const voices = await parallel([...claudeThunks, ...externalThunks])

const pool = []
for (const v of voices.filter(Boolean)) for (const f of v.findings) {
  let lens = v.lens
  if (!lens) { const m = /^\s*\[(\w+)\]/.exec(f.summary || ''); lens = m ? m[1].toLowerCase() : 'unspecified' }
  pool.push({ ...f, backend: v.backend, lens })
}

// Phase 3: Merge / cluster by (file, mechanism) — LLM step, NOT (file,line) JS
phase('Merge')
let clusters = []
if (pool.length > 0) {
  const CLUSTER_SCHEMA = {
    type: 'object', additionalProperties: false, required: ['clusters'],
    properties: { clusters: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      required: ['file', 'line', 'mechanism', 'severity', 'summary', 'failure_scenario', 'recommendation', 'lens', 'member_indices'],
      properties: {
        file: { type: 'string' }, line: { type: 'integer' }, mechanism: { type: 'string' },
        severity: { enum: ['critical', 'warning', 'minor'] },
        summary: { type: 'string' }, failure_scenario: { type: 'string' }, recommendation: { type: 'string' },
        lens: { type: 'string' }, member_indices: { type: 'array', items: { type: 'integer' } },
      },
    } } },
  }
  const numbered = pool.map((f, i) => `#${i} [${f.backend}/${f.lens}] ${f.file}:${f.line} — ${f.summary} :: ${f.failure_scenario}`).join('\n')
  const res = await agent(
    `Merge/dedup step. ${pool.length} raw findings from claude/codex/grok below. Cluster by UNDERLYING DEFECT — same file + same mechanism = one cluster — EVEN IF line numbers differ (external tools number against the diff; match on meaning, not line). ` +
    `Return per cluster: file, representative line, short mechanism key, severity (max of members), summary, strongest failure_scenario, recommendation, dominant lens, member_indices. Every index in exactly one cluster.\n\n` + numbered,
    { label: 'merge:cluster', phase: 'Merge', schema: CLUSTER_SCHEMA, effort: 'medium' }
  )
  clusters = (res?.clusters || []).map((c) => {
    const members = (c.member_indices || []).filter((i) => i >= 0 && i < pool.length)
    const backends = Array.from(new Set(members.map((i) => pool[i].backend))).sort()
    return { ...c, backends, consensus: backends.length >= 2 ? 'CONFIRMED' : 'solo' }
  })
}
const consensusClusters = clusters.filter((c) => c.consensus === 'CONFIRMED')
const soloClusters = clusters.filter((c) => c.consensus === 'solo')

// Phase 4: 3-state verify of solos
phase('Verify')
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['verdict', 'evidence'],
  properties: { verdict: { enum: ['CONFIRMED', 'PLAUSIBLE', 'REFUTED'] }, evidence: { type: 'string' } },
}
const verifiedSolos = await parallel(soloClusters.map((c) => () =>
  agent(
    `Adversarial verifier for one solo finding — try to REFUTE it against the real repo.\n` +
    `File: ${c.file} (line ${c.line})\nMechanism: ${c.mechanism}\nClaim: ${c.summary}\nFailure: ${c.failure_scenario}\n\n` +
    `Read the file / run read-only checks. Verdict: CONFIRMED / REFUTED / PLAUSIBLE (default when unsure) + one-sentence evidence.`,
    { label: `verify:${c.file.split('/').pop()}`, phase: 'Verify', schema: VERDICT_SCHEMA, effort: 'medium' }
  ).then((v) => ({ ...c, verifier: v?.verdict || 'PLAUSIBLE', evidence: v?.evidence || '' })).catch(() => ({ ...c, verifier: 'PLAUSIBLE', evidence: 'verifier error → PLAUSIBLE' }))
))

// Synthesis + balance data
// P2 (see § Security): consensus is a strong prior, not proof — run a light verify
// here too (or require cross-family agreement) instead of stamping CONFIRMED blind.
const finalConsensus = consensusClusters.map((c) => ({ ...c, verifier: 'CONFIRMED', evidence: `agreed by ${c.backends.join('+')}` }))
const finalSolos = verifiedSolos.filter(Boolean).filter((c) => c.verifier !== 'REFUTED')
const refuted = verifiedSolos.filter(Boolean).filter((c) => c.verifier === 'REFUTED')
const sevRank = { critical: 0, warning: 1, minor: 2 }
const all = [...finalConsensus, ...finalSolos].sort((a, b) => (sevRank[a.severity] - sevRank[b.severity]) || (a.consensus === 'CONFIRMED' ? -1 : 1))
const perLens = {}, survivingPerLens = {}
for (const f of pool) perLens[f.lens] = (perLens[f.lens] || 0) + 1
for (const c of all) survivingPerLens[c.lens] = (survivingPerLens[c.lens] || 0) + 1
return { gate, voices, findings: all, refuted, balance: { total: all.length, consensus: finalConsensus.length, solo: finalSolos.length, refuted: refuted.length, rawPerLens: perLens, survivingPerLens } }
```

## What the dry-runs proved (and the P2 calibration points)

- **Structure holds:** 12 agents, 4 phases, 0 errors, ~6 min. The adapter
  carries the external voices cleanly.
- **Lens gating works but was too aggressive** — it skipped `security` on the
  adapter diff. P2 gate prompt already hardened here: *never skip security
  when code/arguments/filenames flow to an external process.*
- **Merge must be the LLM cluster step, not `(file,line)` JS** (L1). With the
  cluster step, consensus=0 was *honest* (the backends genuinely found
  different defects) — solo+verify is the normal path, consensus the exception.
- **3-state verify earns its place:** it REFUTED a wrong codex claim
  (`--skip-git-repo-check` needing a bypass flag) that a naive pipeline would
  have shipped.
- **MoA is complementary:** grok found defects the Claude lenses didn't and
  vice-versa; codex's one finding was the false one. Different models, different
  catches.

## Security — the trust boundary (MANDATORY for P2, hardened by the loop)

The diff under review is **untrusted input** that flows into agentic backends and
back into the report. Three loop rounds (one of which exfiltrated a real
credential mid-review) converged on these non-negotiable mitigations:

1. **Sandbox every backend + filter the env.** The diff is inlined, so backends
   need NO filesystem/shell tools. Adapter enforces: grok `--tools ""` (verified
   to block file reads) + `--disable-web-search`; codex `-s read-only`; **an
   OS-level read-deny jail** (`sandbox-exec`/`bwrap`) around every call, denying
   secret stores **per-backend** (a backend keeps its own cred dir but not its
   siblings' — verified: codex can't read `~/.grok`); **an env filter** stripping
   secret-shaped vars (the jail blocks files, not the inherited env).
   *Denylist, not allowlist, by necessity:* the node/bun-based CLIs load runtime
   from all over `$HOME`, so a deny-`$HOME`-allowlist jail breaks them (tested:
   codex's node loader dies). The denylist is a backstop; the primary defense is
   that backends need no reads at all (diff inlined) + grok is tool-less. A full
   allowlist (or a purpose-built minimal-runtime container) is the P2 upgrade.
2. **Scrub secrets at the boundaries.** `scrub_secrets` redacts secret-shaped
   content (AWS keys, private keys, gh/sk tokens, `secret=…`) from findings JSON
   before it leaves `run_codex`/`run_grok` — a backstop even if a sandbox is
   bypassed. A final **output gate** re-scrubs every surviving finding (incl.
   Claude finders, which never pass the adapter) before results leave the
   workflow. Scrubbing runs at these two *boundaries* (inbound from a backend,
   outbound to the user) — **not** at merge/verify: finding text reaches those
   in-session agents unredacted, and it is **fencing** (item 3), not a scrub,
   that protects them from the re-interpolated text (second-order injection).
3. **Fence untrusted text as data (both hops).** Wrap untrusted content in
   explicit delimiters carrying a **per-run random nonce** the content cannot
   forge, plus a system instruction that everything inside is data, never
   instructions — deterministic Bash, never an LLM step.
   - *Diff (first hop):* the skill fences the inlined diff (`DIFF-<nonce>`,
     `secrets.token_hex`, collision-checked) before codex/grok/Claude read it.
   - *Findings (second hop):* the free-text fields those backends send BACK
     (`summary`, `failure_scenario`, `mechanism`) are re-interpolated into the
     merge and verify prompts — a diff can plant reviewer instructions in a
     finding. The workflow fences them the same way (`FINDINGS-<nonce>` in merge,
     `FINDING-<nonce>` per solo in verify). Its nonce is a **separate** entropy
     source generated by the skill's Bash prep and **not** written into the
     external prompt, so the backends never see it and cannot forge the delimiter.
     The sandbox has no RNG, so the workflow only collision-checks the nonce
     against the returned findings and extends it deterministically on collision.
     Fences layer WITH the "treat as DATA" instruction, not instead of it; there
     is no secret-scrub *at* merge/verify (item 2), so the fence is the sole
     structural defense for the text those agents read.
     *Threat-model assumptions for the finding fence:* (a) the nonce is minted in
     Bash (`token_hex(8)`; the workflow validates hex+length but cannot measure
     entropy) and its secrecy holds only because it is never sent to a backend
     **and** the orchestrator that transports it into the workflow args is not
     attacker-steerable — the diff it also handles is fenced (first hop) precisely
     to keep planted text from steering that choice. (b) If no valid nonce reaches
     the workflow the fence degrades to the instruction-only guard; that
     degradation is surfaced in the return payload (`fenceDegraded`) and the Bash
     prep fails closed (`SWARM_NONCE_UNAVAILABLE`) so it is never silent. (c) The
     backend-supplied `file` is confined to the repo tree (`repoSafePath`) before
     any reader opens it; symlink-realpath containment is the residual the JS
     sandbox can't enforce.
4. **Bound findings size.** `finding.schema.json` caps summary/failure_scenario/
   recommendation length, so a payload can't route a large blob through a field.
5. **Don't fully trust consensus.** Consensus (≥2 backends) currently skips the
   verifier, but agreement comes from LLM merge-clustering + correlated model
   bias, not independent proof. P2: still run a light verify on consensus
   findings, or require cross-family agreement (not composer+grok).
6. **Prefer deterministic transport, and distinguish error from empty.** The
   Haiku "thin transport" wrapper can silently drop/reshape findings while
   staying schema-valid; where a registered workflow can shell out, pass adapter
   JSON through deterministically instead. A backend that errored/was dropped
   must NOT collapse to the same `{findings:[]}` as a clean empty review —
   carry an explicit `error` marker so a silently-failing backend is visible,
   not mistaken for "found nothing".
7. **Edit authority is Claude-only (P5 `--fix`/`--loop`).** The review is
   advisory by default; acting is opt-in. When it acts, **only the main Claude
   session edits the working tree** — the external backends stay strictly
   review-only (never `codex apply`, no write tools), so a backend compromised
   via the untrusted diff can at most emit a *finding*, never an edit. Claude
   acts only on findings it agreed with (✅/🟨), re-confirms each claim against
   the code before editing (a fabricated finding fails this and is skipped), and
   the loop never pushes or merges — all changes land locally for the user to
   review. This keeps the untrusted-input boundary (1–6, read side) and the
   write boundary (this item) separate: nothing that crosses the review boundary
   ever gains write authority.

## Open P2 wiring (not yet in the blueprint)

- **Registered workflow** (not inline) so per-agent `durationMs` is available
  for the timing balance line.
- **Model labels** in the balance line (`Opus-4.8`/`GPT-5.5`/`grok-4.5`),
  read from each backend's review output.
- **Optional composer lens-gate**: `grok-composer-2.5-fast` can't enforce
  `--json-schema`/`--effort`, but a strict-JSON prompt makes it emit valid JSON
  and reason on demand (tested 2/2). It's ~2× faster than grok-4.5 but its
  ~20s CLI cost undercuts a Haiku gate; keep it optional, with a defensive
  parser + fallback-to-all-lenses.
- **Balance / footer / loop-round box:** render deterministically from the
  synthesis data (see task file P4/P5).
