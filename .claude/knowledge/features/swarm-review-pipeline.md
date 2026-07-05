---
title: "Swarm Review Pipeline (/swarm:review)"
createdAt: 2026-07-05
updatedAt: 2026-07-05
createdFrom: "branch: task/swarm-p2-security-architecture"
updatedFrom: "branch: task/swarm-p2-security-architecture"
pluginVersion: 1.8.2
prime: false
---

# Swarm Review Pipeline (`/swarm:review`)

P2 turns the blueprint into a working review: a **Workflow-tool script**
(`plugins/swarm/workflows/swarm-review.js`) launched by the `/swarm:review`
skill. Shape: `scope+gate → fan-out (4 voices) → merge (file,mechanism) →
verify solos → output-gated synthesis`. Four voices: Claude lenses ∥ codex ∥
grok-build ∥ composer (see [[swarm-backend-adapter]]).

## The skill ↔ workflow wiring (the non-obvious parts)

- **`args` reaches the workflow script as a JSON *string*, not an object.**
  `args?.adapter` was `undefined` until the script normalized:
  `let INPUT = typeof args === 'string' ? JSON.parse(args) : (args || {})`.
  This cost a run to discover (the first smoke test tripped the input guard with
  0 agents). Always parse-if-string at the top of a plugin workflow.
- **`${CLAUDE_PLUGIN_ROOT}` is NOT substituted inside a `.js` file** (only in
  SKILL.md/markdown). So the adapter path and the temp-file paths must be passed
  **via `args`** from the skill (which *does* get the substitution), e.g.
  `Workflow({scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/swarm-review.js", args: {adapter, diffFile, externalPromptFile, externalVoices}})`.
- **Workflow JS has no Bash/filesystem access**, so the diff never enters the
  script. The **skill** builds two temp files in deterministic Bash — the raw
  diff (Claude finders `Read` it) and a **fenced external prompt** (review
  instructions + the diff wrapped in untrusted-data markers) — and passes their
  paths. The external CLIs get the fenced prompt via `agents.sh run … --prompt-file`.
- The skill invoking `Workflow` is the explicit **opt-in** the Workflow tool
  requires; a plugin skill may not otherwise trigger it.

## Design decisions

- **Consensus counts model *families*, not backends.** A cross-family cluster
  (≥2 of claude / openai / grok) is CONFIRMED without extra verify; everything
  else is solo and goes through the adversarial 3-state verifier. composer +
  grok-build agreeing is one grok vote — they cannot alone mint consensus.
- **Security is intentionally minimal** (user directive: no cannons-at-sparrows).
  The P1 adapter floor stays (sandbox, tool-less grok, secret scrub, env filter,
  caps); P2 adds only three cheap things — **fencing** the diff as data
  (deterministic Bash, not an LLM step that could be steered into dropping it),
  an **output gate** (a final JS secret-scrub over *every* surviving finding,
  incl. Claude finders that never pass the adapter), and **error ≠ empty** (the
  external transport returns `{ok,error,findings}` so a dropped backend is
  reported distinctly, never collapsed to a clean empty review). The container /
  auth-proxy pieces from the security doc are deferred as accepted residual.

## Verified end-to-end (2026-07-05)

Real background runs on this branch: a Claude-only smoke run proved the wiring
(6 agents, correct return shape); the review **found a real bug in its own
composer parser** (first-object-vs-findings-object), which was then fixed. Only
`REFUTED` solos are dropped; consensus/solo/refuted counts + per-lens raw→
surviving ship in the `balance` block the skill renders.
