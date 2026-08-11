# CC-Harness foreign agents at `/kickoff`

When a `cc-harness-agents` helper is on `PATH`, `/kickoff` offers its foreign
agents as workers — a foreign model running *inside* the Claude Code harness
(full skills, lenses, `/continue`, `/close`), routed through a local gateway.
When the helper is absent, behaviour is unchanged (one `command -v`).

This page is the **plugin-side contract**. The helper itself is machine-local
(gateway URL, credentials, model ceilings) and is *not* shipped with the plugin.
A reference implementation lives in the paired dotfiles change that extracts the
helper as a PATH binary; anything that implements the two subcommands below is
enough for auto-detect.

## Why a PATH helper (and not a shell function)

Earlier idea "invoke the interactive `claude()` wrapper via `zsh -ic`" was
rejected: fragile, ties the plugin to zsh, interactive-shell side effects. A
shell-agnostic PATH binary keeps detection a cheap `command -v`, keeps the
launch argv-exec-native (no shell), and keeps all machine-local config
(gateway / creds / models / context ceilings) out of the public plugin.

## Contract

### `cc-harness-agents list`

Probe without starting a session. Print one TSV row per foreign agent, **exactly
four columns**:

```
name<TAB>model<TAB>available<TAB>note
```

Example:

```
cc-harness:grok	grok-4.5	yes	-
cc-harness:sol	gpt-5.6-sol	no	run: cliproxyapi -codex-login
```

- `name` is already namespaced (`cc-harness:<id>`).
- `available` is `yes` | `no` | `unknown`. The plugin treats only the literal
  `yes` as available; everything else is fail-closed for launch.
- `note` is `-` (or empty) when there is nothing to say; otherwise a short fix
  hint the picker shows next to a greyed entry.
- Unavailable agents are listed too. Order is the helper's table order.
- Exit codes (list):
  - `0` — the probe ran (regardless of per-agent availability).
  - `3` — capability absent (no usable gateway token at all). The plugin treats
    this as "helper not configured here" and silently adds no rows — distinct
    from "configured, but this provider is not logged in" (`available=no`).
  - `2` — usage error.

> **Not the same shape as `agent-registry.sh list`.** The registry emits five
> columns (`name/cli/model/available/note`). The helper has no `cli` field; the
> plugin maps deliberately when merging. Do not feed helper rows into a
> five-column parser.

### `cc-harness-agents exec <name> [--] <argv…>`

Set the routing environment for `<name>`, then **`exec "$@"`** so the calling
process *becomes* the target (typically `claude`) — no lingering wrapper in the
process tree. That is load-bearing: herdr's agent-state detection and
work-system's `/close` teardown both key on the pane's root process being
`claude`.

- `<name>` accepts either form: `grok` or `cc-harness:grok`.
- No `--model` on the `claude` side — the helper sets `ANTHROPIC_MODEL` (and the
  tier defaults / context ceiling) via env before the exec.
- Exit codes (exec, *before* the target runs):
  - never returns on success (process replaced).
  - `1` — requested agent not available (not logged in / gateway down).
  - `2` — usage / unknown agent.
  - `3` — capability absent (no token).
- Once the target is running, its own status comes back unchanged; a consumer
  that needs a reliable availability answer asks `list`, which never execs.

### What the plugin does with this

| surface | behaviour |
|---------|-----------|
| `agent-registry.sh list` | if `command -v cc-harness-agents` succeeds, run `list` (bounded) and merge rows as `cli=cc-harness`; helper absent or exit 3 → no change |
| `agent-registry.sh resolve cc-harness:<id>` | availability + note from the helper (no re-probe); argv = `cc-harness-agents exec <id> -- claude [-n <session>] /work-system:continue` |
| `/kickoff` picker | harness rows labelled "foreign model in the Claude Code harness, routed via a local gateway"; unavailable greyed with the helper's fix hint; available first |
| lifecycle | runs as a full CC session → `/close` Scenario A/B and tab glyphs unchanged; `supports=` is the same set as a native claude worker. **Exception:** `/continue`'s reopen sends a bare `claude -c`, which resumes the transcript *without* the routing env — see below |
| default | a committed `cc-harness:<id>` default is accepted when the helper lists it, and falls through to the picker when the helper is gone (same validation as a stale native name) |

Nothing gateway-specific is hardcoded in the plugin. A sixth foreign model is a
new row in the helper's table — the plugin has no per-agent code path, which is
why the tests assert a name the plugin has never shipped (`cc-harness:never-seen`).

### Known gap: `/continue` reopen loses the routing

`herdr-launch.sh resume` always sends a bare `claude -c`, because the work-system
does not persist which worker a task used. For a harness task that resumes the
right transcript **on the wrong model**: without `cc-harness-agents exec` the
session has no `ANTHROPIC_BASE_URL`/`ANTHROPIC_MODEL`, so it silently continues on
the user's default Claude model. Nothing looks broken — which is why `/continue`
states it inline rather than claiming parity.

Resume a harness worker by hand in the tab:

```sh
cc-harness-agents exec <id> -- claude -c
```

Closing this properly needs per-task worker persistence (a deliberate later idea),
not a change to the helper contract.

## Setup sketch (reference)

Exact install steps live with the helper. The shape is:

1. Install and run a local Anthropic-compatible gateway (e.g. CLIProxyAPI) that
   fronts the foreign providers, with a client token on disk.
2. Log each provider into the gateway so it holds OAuth / API credentials.
3. Put a `cc-harness-agents` binary on `PATH` that implements `list` / `exec`
   against that gateway.
4. `/kickoff --pick` (or `agent-registry.sh list`) now shows the harness rows.

Context ceilings differ per agent and are often plan-gated (e.g. a marketed 1M
window may serve 256k on the current plan). The helper owns that value; the
plugin never restates or assumes a window — another reason to stay a pure
consumer.

## Security notes (for helper authors)

- `exec` hands the gateway token to whatever argv it runs. Deny both the token
  file *and* `cc-harness-agents exec` in Claude Code permission rules if you
  don't want a session to exfiltrate the token; `list` is safe (prints no
  secret).
- **Know what those deny rules do *not* cover**, or you will trust a boundary
  that isn't there. They evaluate Claude Code **tool calls**, so they miss both
  ends of the realistic path:
  - **This plugin's own launch path is unaffected.** `/kickoff` calls
    `agent-registry.sh resolve` (a command string containing no
    `cc-harness-agents` token, so no rule matches), and `herdr-launch.sh` then
    spawns `herdr agent start … -- cc-harness-agents exec <id> -- claude …`
    inside the herdr server — never as a Bash tool call the permission system
    sees. That is by design (it is how the worker starts), but it means the rule
    is not what stops a launch.
  - **A worker session can still call it.** `exec` accepts arbitrary argv, so a
    malicious instruction reaching a worker (e.g. via an `/adopt`-generated
    `TASK.md`) can run `cc-harness-agents exec <id> -- sh -c '…$ANTHROPIC_AUTH_TOKEN…'`
    within the contract.
  - Cron, a package postinstall, or any plain shell is outside Claude Code
    entirely. Bash rules also match on command *text*, so an unusual spelling of
    the path misses them.

  Net: the deny rules reduce casual exposure in an interactive session. The real
  boundary is **who may execute the helper at all** (file permissions, PATH
  hygiene) — treat installing it as granting user-equivalent code execution.
- An argv allow-list was considered and rejected: it would break the contract
  ("run this routed"), and the sanctioned target `claude` can run arbitrary
  commands itself. A helper author who wants a tighter boundary can pin `exec`
  to a fixed `claude` target — the plugin only ever asks for that shape — at the
  cost of the general contract.
- Gateway liveness should be a *plausibility* check that withholds the token
  from a bare socket listener (e.g. an unauthenticated request must be
  refused), not a bare TCP connect.
- **`list` output is rendered to users as authoritative hints.** The plugin
  strips control characters and caps field length at ingest, but a helper should
  not emit instruction-shaped notes: they are displayed, never executed.

## Related

- `scripts/agent-registry.sh` — PATH detect, list merge, resolve/emit_argv
- `skills/kickoff/SKILL.md` step 12 — picker presentation
- `.claude/knowledge/features/kickoff-agent-selection.md` — design decisions
