---
title: "Swarm Backend Adapter Layer"
createdAt: 2026-07-02
updatedAt: 2026-07-02
createdFrom: "branch: task/add-swarm-plugin"
updatedFrom: "branch: task/add-swarm-plugin"
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
- **`grok-composer-2.5-fast` also does not enforce `--json-schema`**: it
  returns plain text with `structuredOutput: null` +
  `structuredOutputError: "model output was not valid JSON"`. So it cannot
  serve as a second grok ensemble voice (besides being same-family-correlated,
  which would dilute the ≥2-backend consensus signal). `grok-build` is the
  only schema-capable grok model; for "more grok" at high effort, prefer its
  native `--best-of-n N` over a second model voice.
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
