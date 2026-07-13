---
title: "Swarm Review Pipeline (/swarm:review)"
createdAt: 2026-07-08
updatedAt: 2026-07-13
createdFrom: "PR #24"
updatedFrom: "session: 2026-07-13"
pluginVersion: 1.8.2
prime: false
reindexedAt: 2026-07-12
---

# Swarm Review Pipeline (`/swarm:review`)

P2 turns the blueprint into a working review: a **Workflow-tool script**
(`plugins/swarm/workflows/swarm-review.js`) launched by the `/swarm:review`
skill. Shape: `scope+gate → fan-out (4 voices) → merge (file,mechanism) →
verify solos → output-gated synthesis`. Four voices: Claude lenses ∥ codex ∥
grok-build ∥ composer (see [swarm-backend-adapter](swarm-backend-adapter.md)).

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
  A later patch extends the fence to the **second hop** (T6): finding free-text
  is re-fed to the merge/verify agents, so it is fenced there too with a
  **separate** nonce. Key constraint — the Workflow sandbox has no RNG
  (`Math.random`/`Date.now` throw), so security nonces are minted in the skill's
  Bash prep (`secrets.token_hex`) and passed via `args.findingNonce`, **never**
  written into the external prompt (backends must not see it, or they could forge
  the delimiter); the workflow only collision-checks it against the returned
  findings and extends it deterministically (`nonce-1`, `-2`…) on collision.

- **`args.claude: false`** runs an **external-only control** (codex + grok-build
  + composer, no Claude finder lenses, no gate; merge/verify still in-session).
  Proven useful: a control run found real bugs the with-Claude run missed (an
  `aws_secret_access_key` scrub-list drift, `git diff` omitting untracked files)
  — the "different models catch different defects" premise, live.

## P5: `--fix` / `--loop` actions (swarm 0.3.0)

`/swarm:review` can now **act**, not just advise — but the loop is **orchestrated
in-session by Claude between Workflow runs**, one workflow run per review round,
because workflow JS has no Bash and can't edit files (same constraint that keeps
the diff out of the script, above). Claude applies edits between rounds.

- **Only Claude edits.** External agents stay review-only — never `codex apply`,
  never hand them edit authority (also the security posture: they run jailed +
  tool-less). Act only on ✅-agree + 🟨-partial findings; 🟨 = apply the
  session's own variant, not the reviewer's `recommendation` verbatim;
  ❌-disagree is never touched and stays visible in the report.
- **Re-confirm claim-vs-code before every edit** — a stale finding (comment rot,
  already-fixed, line drift) is reported as skipped, never fabricated into an edit.
- **Deterministic bits live in `scripts/loop-closeout.py`, not skill prose**
  (per the project's prose-drift memory — stateful skill logic drifts as
  prose): `step` = the 4-part termination decision in **fixed order** (0-findings
  / nothing-agreed / no-change / cap, default 10), `box` = the OPEN-findings
  close-out visualization that **shows a legitimate rise** (a fix surfaced new
  findings) instead of hiding it. Stateless — Claude passes the per-round counts
  in; no state file, so no cwd footgun. The determinism is **the arithmetic, not
  the inputs**: `F/A/C/pending/OPEN[]` are Claude's in-session tallies, so a
  miscount still feeds a wrong reason in (garbage-in) — the script can't make a
  judged count reproducible, only the branch logic over it.
- Loop mechanics mirror pr-flow `/cycle` run locally (no push / no `@claude`
  poll); the `Status` column (🔧/⏭️/🔁) and stable `#` across rounds come from
  the report table contract this entry defines above (P2 reserved them).
- **`--max` profile** (`INPUT.max` in the workflow): lifts every voice to its
  ceiling — codex `gpt-5.6-sol`@`xhigh` (codex has NO `max` tier, xhigh is its
  top), grok-build `max`, Claude finder lenses + verifier `xhigh`; gate/merge
  and composer (no effort control) unchanged. Orthogonal to `--fix`/`--loop`,
  composes with both. Both max-tier settings were verified live before wiring
  (`gpt-5.6-sol`@xhigh + grok `--effort max` end-to-end) — the "no silent fail
  on a non-existent model/effort" rule.

## `--pr`: review a PR diff and post the result (swarm 0.4.0)

`/swarm:review --pr [<number>]` runs the **same** pipeline against a GitHub PR's
diff instead of the local tree. It rides the existing seam: the diff already
arrives as a temp-file path (above), so `--pr` only swaps *how that file is
filled* — `gh pr diff <n>` (bare `--pr` resolves the current branch's PR via
`gh pr view`) instead of `git diff`. The **workflow is untouched**; only step 1
(diff source) and a new **step 5 (publish)** differ.

- **pr-flow compatibility is the load-bearing design point.** The comment is
  posted with `gh pr comment` under the **user's own gh identity**, not
  `author.login == "claude"`. pr-flow's `claude-review.sh` polls *only* for
  `claude`-authored comments, so a swarm comment is invisible to `/cycle`/`/check`
  — it can't be mistaken for an `@claude` review or stall a running PR loop. The
  `## 🐝 Swarm review (local ensemble)` marker header keeps it visually distinct too.
- **Only output-gated findings are ever posted** — the body is built from the
  gated `findings`/`balance`, never raw backend output. Posting is outward-facing,
  so it **confirms once** before publishing (the flag authorizes the review, not
  silent publishing).
- **`--pr` is read-only and mutually exclusive with `--fix`/`--loop`** — a
  local-edit loop has no meaning against a remote diff; the two lifecycles need
  their own design (deferred). Auto-review-on-push (a self-built Action running
  `agents.sh` with `XAI_API_KEY`) stays a deliberate non-goal — only the user's
  machine triggers a review.

## Future idea (P3+): per-lens external prompts

Today externals run ONE broad multi-lens review each; Claude fans out per lens.
Running externals per-lens too would add depth-per-lens + symmetry + authoritative
lens tags + let the gate prune external calls. **But** it multiplies external CLI
calls ~5× (backends × lenses) — steep cost + CLI overhead, against the efficiency
goal. Verdict: make it an **opt-in `lensMode`**, gate the external lenses when on,
and for routine depth prefer higher external `--effort` / grok `--best-of-n` (one
call, more thinking) over N calls. Not a default.

## Verified end-to-end (2026-07-05)

Real background runs on this branch: a Claude-only smoke run proved the wiring
(6 agents, correct return shape); the review **found a real bug in its own
composer parser** (first-object-vs-findings-object), which was then fixed. Only
`REFUTED` solos are dropped; consensus/solo/refuted counts + per-lens raw→
surviving ship in the `balance` block the skill renders. Iterating a
`/swarm:review` loop over the branch caught several fix-induced regressions
(a pipefail abort, an unconditional untracked-append, incomplete scrub coverage)
— the loop's real value is catching incomplete fixes, but it diverges
(marginal findings grow), so cap the rounds.
