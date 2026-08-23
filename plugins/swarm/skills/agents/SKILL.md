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

1. Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh" list --json`
2. Run from the current repository: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh" jail`
   and record whether it returned `jail=yes`.
3. Render the JSON array as a table:

   | Backend | Installed | Version | Ready | Notes |
   |---------|-----------|---------|-------|-------|

   - `available: false` → Installed ❌, Notes = "not installed"
   - `available: true, ready: false` → Ready ❌, Notes = the `hint` field (e.g. "run: codex login")
   - both true → ✅ ✅, Notes empty
   - for `kimi`, when both are true but `jail=no`, keep its Ready value and set
     Notes = "no working jail/repo root — unavailable for review"
4. Close with one line stating which backends are live (`available && ready`,
   plus `jail=yes` for Kimi), e.g.:
   `Live backends: claude + codex + grok + kimi — full ensemble.`
   If only claude is live, note that installing/authenticating the external
   CLIs (`codex`, `grok`, `kimi`) would widen the ensemble. Do not reference other
   swarm commands until they ship.

## Notes

- Read-only, no side effects — safe to run anytime.
- `claude` is always ready when Claude Code runs (reviews happen in-session
  via the Agent tool; the external CLIs are called through the adapter).
- **`kimi` Ready is model/transport-aware** — it requires the real
  `~/.kimi-code/credentials/kimi-code.json`, ACP stdio support, and the pinned
  `kimi-code/k3-256k` model in `kimi provider list --json`. A failed, bounded, or
  unrecognized-format capability probe degrades audibly to trusting credentials
  rather than silently dropping the Moonshot family; a clean negative stays
  not-ready. Kimi is live for reviews only with `jail=yes`, because ACP has no
  safe jail-less read tier.
- **`grok` Ready is a heuristic** — it means a non-empty `~/.grok/auth.json`
  exists, that the CLI offers `--prompt-file` (the out-of-band prompt transport),
  **and** that `grok models` still lists a schema-verified model, NOT that the
  token is valid/unexpired (codex, by contrast, runs
  a real `codex login status` — bounded, and a probe that does NOT complete
  degrades to "credentials present" with a stderr warning instead of reporting
  not-ready, so a captive portal cannot silently drop the whole openai family).
  So grok can show Ready yet fail at review time
  on a stale token; treat it as "credentials present" and let the run surface a
  real auth error. A not-ready hint naming the model list is NOT an auth problem,
  and it has **three different remedies** — read which one the hint states:
  the CLI has no `--prompt-file` (too old → update it), it offers no canonical
  model at all (also too old → update it), or it offers canonical models that are
  not schema-verified (usually NEWER than this adapter knows → verify
  `--json-schema` on the named id and add it to `GROK_SCHEMA_VERIFIED`). Never relay it as "update the CLI" by default; that
  sends the second user to update an already-current install. The model check degrades to auth-only (with a warning on stderr) when
  the probe can't run — no coreutils `timeout` to bound it, or an unreadable
  model list.
