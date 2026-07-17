---
name: agents
description: |
  Shows swarm backend status: which review agents (claude, codex, grok) are
  installed and authenticated.
  Trigger: "swarm agents", "which review backends are live", "agent status".
user_invocable: true
---

# Swarm Agent Status

> Probe all review backends and report which are live.

## Instructions

1. Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh" list --json`
2. Render the JSON array as a table:

   | Backend | Installed | Version | Ready | Notes |
   |---------|-----------|---------|-------|-------|

   - `available: false` → Installed ❌, Notes = "not installed"
   - `available: true, ready: false` → Ready ❌, Notes = the `hint` field (e.g. "run: codex login")
   - both true → ✅ ✅, Notes empty
3. Close with one line stating which backends are live (all with
   `available && ready`), e.g.:
   `Live backends: claude + codex + grok — full ensemble.`
   If only claude is live, note that installing/authenticating the external
   CLIs (`codex`, `grok`) would widen the ensemble. Do not reference other
   swarm commands until they ship.

## Notes

- Read-only, no side effects — safe to run anytime.
- `claude` is always ready when Claude Code runs (reviews happen in-session
  via the Agent tool; the external CLIs are called through the adapter).
- **`grok` Ready is a heuristic** — it means a non-empty `~/.grok/auth.json`
  exists **and** that `grok models` still lists the adapter's model
  (`grok-4.5`), NOT that the token is valid/unexpired (codex, by contrast, runs
  a real `codex login status`). So grok can show Ready yet fail at review time
  on a stale token; treat it as "credentials present" and let the run surface a
  real auth error. Not-ready with a "does not offer grok-4.5" hint means the
  grok CLI dropped/renamed the model — update the CLI, it is not an auth
  problem. The model check degrades to auth-only (with a warning on stderr) when
  the probe can't run — no coreutils `timeout` to bound it, or an unreadable
  model list.
