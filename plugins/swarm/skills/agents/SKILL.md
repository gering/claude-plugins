---
name: agents
description: |
  Shows swarm backend status: which review agents (claude, codex, grok) are
  installed and authenticated.
  Trigger: "swarm agents", "which review backends are live", "agent status".
user_invocable: true
---

# Swarm Agent Status

> Probe all review backends and report what `/swarm:review` would use right now.

## Instructions

1. Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/agents.sh" list --json`
2. Render the JSON array as a table:

   | Backend | Installed | Version | Ready | Notes |
   |---------|-----------|---------|-------|-------|

   - `available: false` → Installed ❌, Notes = "not installed"
   - `available: true, ready: false` → Ready ❌, Notes = the `hint` field (e.g. "run: codex login")
   - both true → ✅ ✅, Notes empty
3. Close with one line stating which backends a review would fan out to right
   now (all with `available && ready`), e.g.:
   `Ensemble: claude + codex + grok — full swarm available.`
   If only claude is live, note that reviews still work but without
   cross-agent consensus.

## Notes

- Read-only, no side effects — safe to run anytime.
- `claude` is always ready when Claude Code runs (reviews happen in-session
  via the Agent tool; the external CLIs are called through the adapter).
