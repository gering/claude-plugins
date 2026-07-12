---
title: "Swarm Backend Adapter Layer"
createdAt: 2026-07-02
updatedAt: 2026-07-05
createdFrom: "branch: task/add-swarm-plugin"
updatedFrom: "branch: task/swarm-p2-security-architecture"
pluginVersion: 1.8.2
prime: false
---

# Swarm Backend Adapter Layer

The `swarm` plugin reviews locally with a mixture-of-agents ensemble: Claude
subagents plus the external `codex` and `grok` CLIs. All deterministic backend
logic lives in one script — `plugins/swarm/scripts/agents.sh` (verbs: `list`,
`available`, `ready`, `run`) — so skills never call an external CLI directly.
The script header documents the per-backend mechanics; this entry captures the
*verified* CLI behavior the adapter is built on and the gotchas that cost a
debugging round.

## Verified CLI facts (codex 0.128 / grok 0.2.77, 2026-07)

- **Uniform findings JSON** is achievable from both CLIs: `codex exec
  --output-schema <file>` and `grok --json-schema '<inline>'` both enforce a
  JSON Schema on the final answer. One bundled schema
  (`scripts/schema/finding.schema.json`) feeds both — the ensemble merge never
  parses free-form review prose. In strict structured-output modes all
  properties must be `required`, so the schema requires every field and uses
  honest defaults (`line: 0`, self-reported `confidence`) instead of optionals.
- **Where the JSON lands differs per CLI**: codex writes the pure JSON via
  `--output-last-message <file>` (stdout carries the agent transcript,
  stderr the progress log); grok prints a response **envelope** on stdout —
  the validated object is its `.structuredOutput` field.
- **grok's default model rejects `--effort`** (`grok-composer-2.5-fast` errors
  with "does not support parameter reasoningEffort"). The adapter must pin
  `-m grok-build`. grok's effort ladder (low…max) matches code-review's;
  codex has no `max` tier → map `max`→`xhigh` (`-c model_reasoning_effort=…`).
- **codex model is pinned** to `CODEX_DEFAULT_MODEL` (`gpt-5.6-terra`, the adapter
  passes `-m` on every call), overridable per call via `--model` — so a review is
  reproducible instead of tracking the user's ambient `~/.codex/config` default.
  The pipeline runs codex at `high` normally; the `--max` profile overrides both
  model and effort (`gpt-5.6-sol` @ `xhigh`) — see [[swarm-review-pipeline]].
- **`grok-composer-2.5-fast` does not enforce `--json-schema`** — but it is
  still usable as a second grok voice. Given a strict-JSON *prompt* (the adapter
  appends the schema text and drops `--json-schema`/`--effort`), it emits **pure
  `{"findings":[...]}` directly on stdout — no response envelope, no
  `structuredOutput`** (verified P2, grok 0.2.82; simpler than grok-build's
  envelope). The adapter routes `--model grok-composer-2.5-fast` to a separate
  `run_grok_composer` path that parses the answer **defensively**: collect ALL
  balanced `{...}` objects (whole string, fenced blocks, every `{` run), pick the
  first **non-empty** `findings` object (a leading `{"note":…}`/`{"findings":[]}`
  would otherwise mask the real one), and **validate every item against
  `finding.schema.json`** (composer, unlike codex/grok-build, is not CLI-schema-
  enforced — a malformed item must ERROR, not reach merge/verify). Both the
  first-object bug and the missing per-item validation were caught by swarm
  reviewing its own diff. composer is
  ~2× faster than grok-build. It is same-family-correlated, so consumers must
  count consensus by **model family**, not backend (composer + grok-build
  agreeing is one grok vote — see [[swarm-review-pipeline]]).
- **Headless tool execution**: both CLIs run read-only commands (e.g.
  `git diff`) without extra approval flags — codex inside `-s read-only`
  sandbox, grok headless `-p` auto-approves read-only tools. So lens prompts
  may either inline the diff or instruct the agent to read it itself.

## Gotchas (found in E2E testing, fixed in the adapter)

- **codex hangs on inherited stdin.** With an open non-TTY stdin, `codex exec`
  waits for "additional input from stdin" *in addition to* the positional
  prompt — in a background shell this hangs forever. Always call it with
  `</dev/null` (the adapter does).
- **`set -u` + EXIT trap + `local`**: a trap like `trap 'rm -f "$out"' EXIT`
  referencing a function-`local` variable fires after the function returned —
  under `set -u` the script then dies with "unbound variable" and **exit 1
  despite a fully successful run** (a pipeline would misread the backend as
  failed). Keep trap-referenced temp paths global.
- **Exit-code discipline matters** because the ensemble treats non-zero `run`
  as "backend dropped": stdout must stay pure findings-JSON (all CLI noise to
  stderr or /dev/null), and success must exit 0.
