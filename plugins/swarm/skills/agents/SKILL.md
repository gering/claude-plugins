---
name: agents
description: |
  Shows swarm backend status: which review agents (claude, codex, grok, kimi) are
  installed and authenticated.
  Trigger: "swarm agents", "which review backends are live", "agent status".
user_invocable: true
---

# Swarm Agent Status

> Probe all review backends and report which are live.

## Instructions

1. Run from the current repository: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh" list --json`
   (Kimi's `ready` already includes the opt-in and the OS jail; its `hint`
   names whichever is missing — "opt-in only" means pass `--kimi` to
   `/swarm:review` or export `SWARM_KIMI=1`, it is not an install problem.)
2. Render the JSON array as a table:

   | Backend | Installed | Version | Ready | Notes |
   |---------|-----------|---------|-------|-------|

   - `available: false` → Installed ❌, Notes = "not installed"
   - `available: true, ready: false` → Ready ❌, Notes = the `hint` field (e.g. "run: codex login")
   - both true → ✅ ✅, Notes = `hint` when nonempty (model availability may be unverified); otherwise empty
3. Close with one line stating which backends are live (`available && ready`),
   e.g.:
   `Live backends: claude + codex + grok + kimi — full ensemble.`
   If only claude is live, note that installing/authenticating the external
   CLIs (`codex`, `grok`, `kimi`) would widen the ensemble. Do not reference other
   swarm commands until they ship.

## Notes

- No repository edits or review generation. Probes may refresh a CLI's own auth/cache.
- **Codex model readiness is advisory.** This status command checks the adapter
  fallback model; review prep supplies its profile-selected model instead.
  `model/list` may use cached/bundled data and omit custom aliases, so unknown
  selections produce an auth-only `hint`, even with `ready:true`. Preserve that
  hint; a catalog hit is not proof that the model can generate. Auth-probe
  failures still mean not-ready.
- `claude` is always ready when Claude Code runs (reviews happen in-session
  via the Agent tool; the external CLIs are called through the adapter).
- **`kimi` Ready is model/transport-aware** — it requires the real
  `~/.kimi-code/credentials/kimi-code.json`, ACP stdio support, and the adapter's
  pinned model (`KIMI_DEFAULT_MODEL` in `agents.sh`; the `hint` names the
  effective id) in `kimi provider list --json`. A failed, bounded, or
  unrecognized-format capability probe degrades audibly to trusting credentials
  rather than silently dropping the Moonshot family; a clean negative stays
  not-ready. Kimi is live for reviews only with `jail=yes`, because ACP has no
  safe jail-less read tier — and only when **opted in** (`/swarm:review --kimi`
  or `SWARM_KIMI=1`): Moonshot meters it on 5-hour/7-day quotas, so a stock
  review is the three-family ensemble and Kimi joins on request.
- **`grok` Ready is a heuristic** — it means a non-empty `~/.grok/auth.json`
  exists, that the CLI offers `--prompt-file` (the out-of-band prompt transport),
  **and** that a grok model can be selected (see below), NOT that the
  token is valid/unexpired (codex, by contrast, runs a real `codex login
  status`, bounded — and since that probe IS the auth question and reaches the
  same network the review needs, a probe that hits the wall is reported
  **not-ready**: a wedged CLI otherwise burns the full wall once per gated
  cluster. *Any* non-zero probe rc is not-ready — there is no fail-open branch,
  because the one rc that carried it (126) is also the shell's "found but cannot
  be invoked". The hint names which case it was).
  So grok can show Ready yet fail at review time
  on a stale token; treat it as "credentials present" and let the run surface a
  real auth error. A not-ready hint about the MODEL is NOT an auth problem — relay
  it verbatim, it names the actual cause (no `--prompt-file`, no canonical model
  on offer, a catalog that could not be read, or a model whose `--json-schema`
  enforcement could not be established). Never default to "update the CLI".
- **`Ready` does not say WHICH grok model runs.** "grok" is a policy — the newest
  canonical `grok-4.x/5.x` the CLI offers whose structured output was measured
  by a cached synthetic probe. When the user asks which model that is, run
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh" grok-model` and report
  `selected`, `latest_candidate`, `source` and — if non-empty — `degraded`
  verbatim. Only `source=latest` means the latest model is in use.
