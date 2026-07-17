---
title: "Swarm Backend Adapter Layer"
createdAt: 2026-07-03
updatedAt: 2026-07-17
createdFrom: "PR #21"
updatedFrom: "PR #37"
pluginVersion: 1.8.2
prime: false
reindexedAt: 2026-07-12
---

# Swarm Backend Adapter Layer

The `swarm` plugin reviews locally with a mixture-of-agents ensemble: Claude
subagents plus the external `codex` and `grok` CLIs. All deterministic backend
logic lives in one script — `plugins/swarm/scripts/agents.sh` (verbs: `list`,
`available`, `ready`, `run`) — so skills never call an external CLI directly.
The script header documents the per-backend mechanics; this entry captures the
*verified* CLI behavior the adapter is built on and the gotchas that cost a
debugging round.

## Verified CLI facts (codex 0.128 / grok 0.2.101, 2026-07)

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
- **The adapter pins `-m grok-4.5`** — the schema-capable model, and since
  swarm 0.4.3 the *only* grok model it supports. grok 0.2.101 renamed it from
  `grok-build` (same upstream pin-rename class as codex's `gpt-5.6-terra`;
  verified drop-in: identical envelope/`structuredOutput` shape, `--single`
  unchanged). Any other `--model` is preflight-rejected with a usage error —
  only grok-4.5 enforces `--json-schema`, and an unlisted model fails late with
  `structuredOutput: null` after burning a full review.
- **Effort ladders**: grok is `low|medium|high` since 0.2.101 (the `max` tier
  is gone) → the adapter maps `xhigh`/`max`→`high`; codex has no `max` tier →
  map `max`→`xhigh` (`-c model_reasoning_effort=…`). Both mappings degrade a
  stale caller instead of erroring.
- **codex model is pinned** to `CODEX_DEFAULT_MODEL` (`gpt-5.6-terra`, the adapter
  passes `-m` on every call), overridable per call via `--model` — so a review is
  reproducible instead of tracking the user's ambient `~/.codex/config` default.
  The pipeline runs codex at `high` normally; the `--max` profile overrides both
  model and effort (`gpt-5.6-sol` @ `xhigh`) — see
  [swarm-review-pipeline](swarm-review-pipeline.md).
- **Model-aware readiness beats an auth-only check** (swarm 0.4.3). grok drops
  and renames models between releases — 0.2.101 removed
  `grok-composer-2.5-fast`, which swarm had shipped as a second grok voice; the
  auth-only probe kept reporting it Ready until it failed mid-review with
  `Invalid params: "unknown model id"`. `ready`/`list` now also require
  grok-4.5 in `grok models` (grok is the one backend with a usable model-list
  command; codex has none, so its model is trusted). The gotchas, all live-
  verified:
  - **Parse the bullet list by SHAPE, not position — and match the id
    SUBSTRING, not the field.** Lines read `  * grok-4.5 (default)`, but keying
    on `$2` turns a reworded line (`  * default: grok-4.5`) into garbage tokens
    that read as "the model is gone" and fail closed. Matching whole
    whitespace-fields starting `grok-` fixes that but still drags glued-on
    punctuation along (`grok-4.5,`, `` `grok-4.5` ``, ANSI codes), which breaks
    the exact-match the same way. Extract with a pattern ending on
    alphanumerics (`grok-[A-Za-z0-9]+([._-][A-Za-z0-9]+)*`); a line with no
    id-shaped token then contributes nothing, landing in the trust-auth degrade.
  - **Match without a pipe to `grep -q`**: an early-exiting `grep -q` can
    SIGPIPE the writer, and under `set -o pipefail` a *hit* would then report
    failure. Newline-fence the list and use a `case` substring match.
  - **An empty model list must NOT fail closed.** Offline, a timeout, or a
    future CLI renaming the subcommand would otherwise silently drop grok from
    every fan-out. Empty/unparseable → trust auth and let `run_grok` surface the
    explicit error; a non-empty list *without* grok-4.5 → an honest "not ready"
    plus an update-the-CLI hint.
  - **A probe added to a local path must not make it hang — and must not lie
    when it can't run.** `ready`/`list` were purely local (stat the auth file)
    before this; the probe puts a network call in every `/swarm:agents` and
    review start. With no coreutils `timeout`/`gtimeout` to bound it (stock
    macOS), the probe is **skipped** rather than run uncapped, degrading to
    trust-auth in ~25ms — but it **warns on stderr**, because a silent skip
    would make the documented model-aware guarantee false on that host: the
    same "promise that doesn't hold at runtime" bug the composer removal exists
    to fix.
  - **Route every degrade through ONE audible exit.** This one spot was fixed
    across FOUR consecutive swarm rounds, each catching the previous round's
    miss: (1) the no-timeout branch ran uncapped; (2) it was capped but skipped
    *silently*, while the docs were tightened to "never silently"; (3) `|| true`
    still swallowed a *failed* probe, so the strengthened promise was false on
    two of three routes; (4) with warnings finally on every route, `rc` was only
    read **when the list came back empty** — so a probe killed mid-stream with
    partial output skipped the degrade entirely and its truncated list was
    treated as authoritative ("model gone", update an already-current CLI).
    The invariants that survive:
      - If N routes end in the same degrade, they need **one shared exit**
        (`_probe_degraded`), not N hand-written warnings. The same rule applies
        to the no-jail warning: it belongs in `_build_jail` (where the condition
        arises), not in one of its two callers.
      - **Check `rc` before the output, and discard partial output.** Only a
        clean exit is an answer; anything else is a degrade.
      - "The probe answered honestly" (model genuinely gone → `not ready` +
        update-the-CLI hint) must stay distinguishable from "the probe never
        answered" (→ trust auth + warning).
    The meta-lesson: a fix that keeps coming back is a shape problem, not a
    patch problem — this converged only once the branching collapsed to
    rc-first + one exit.
  - **Bound it with its own knob, not `SWARM_TIMEOUT`.** That caps a *review*
    (600s, and `0` disables it entirely) — useless for a probe that `list`
    blocks on. `SWARM_PROBE_TIMEOUT` (10s) is separate, and a malformed or `0`
    value falls back to 10 rather than becoming uncapped.
  - **Jail the probe like any other external call, minus the timeout.** It runs
    a networked third-party binary on a path that previously ran none, so it
    goes through the same env-secret filter + read-deny jail. `sandboxed()`
    can't be reused as-is (it hard-wires the review-length cap), hence
    `_build_jail` — the jail prefix without the timeout, shared by both. Two
    traps this split created, both caught by review: the no-jail warning must
    live in `_build_jail`, or the probe runs unjailed and silent on a host
    without sandbox-exec/bwrap; and `_init_sandbox`'s memo must key on the
    **backend** (with a sentinel no backend name equals), or the second entry
    point pins the jail to whichever backend built it first — handing one
    backend another's cred dir.
  - **Memoize by call convention, not by wishing.** `list="$(grok_model_list)"`
    runs the function in a *subshell*, so its cache-global assignments vanish
    and every caller silently re-pays the network call. The cache only works if
    the fetch is called directly and callers read the global
    (`grok_model_fetch; local list="$_grok_models"`).
  This mirrors work-system's `agent-registry.sh`, which learned the same lesson
  at task-launch time.
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
