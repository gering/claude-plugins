---
title: "Swarm Review Pipeline (/swarm:review)"
createdAt: 2026-07-08
updatedAt: 2026-08-12
createdFrom: "PR #24"
updatedFrom: "fix-swarm-timeout-ceiling"
pluginVersion: 1.9.0
prime: false
reindexedAt: 2026-07-12
---

# Swarm Review Pipeline (`/swarm:review`)

P2 turns the blueprint into a working review: a **Workflow-tool script**
(`plugins/swarm/workflows/swarm-review.js`) launched by the `/swarm:review`
skill. Shape: `scope+gate → fan-out (3 voices) → merge (file,mechanism) →
verify solos + design clusters → output-gated synthesis`. Three voices: Claude
lenses ∥ codex ∥ grok (model discovered, not pinned — see [swarm-backend-adapter](swarm-backend-adapter.md)).
A fourth, `grok-composer-2.5-fast`, was removed in swarm 0.4.3 — the grok CLI
dropped the model.

## Lens set: 11 lenses in 5 clusters (0.5.0; `reach` split off 0.9.0)

Grown from 5 topical lenses by importing `/code-review`'s other two
decomposition axes — methodological (HOW to look) and design quality — all
**default-on** (user directive 2026-07-15: maintainability reviewed on every
run, not opt-in). `LENS_CLUSTERS` in the workflow is the **single source of
truth** — every voice's fan-out units come from it, Claude and externals alike:

| cluster | lenses | guiding question |
|---|---|---|
| `breakage` | correctness, removed-behavior | what breaks? |
| `reach` | cross-file-trace | what else does this touch? |
| `threat` | security, adversarial | what's exploitable / which assumption fails? |
| `design` | reuse, simplification, efficiency, altitude | is this good, maintainable code? |
| `consistency` | style, conventions | does it fit the codebase? |

- **`reach` is a deliberate ONE-lens cluster** (0.9.0), split out of `breakage`
  on measurement. The reason is **lens crowd-out**, NOT runtime — the split was
  proposed as a speed fix and the measurement corrected that:

  | run | duration | findings |
  |---|---|---|
  | old: one 3-lens `breakage` call | 374 s | 4 — **three of them `cross-file-trace`** |
  | new: `breakage` (correctness, removed-behavior) | 313 s | 4 — *none* of which the combined call reported |
  | new: `reach` (cross-file-trace) | 126 s | 4 — ≈ the combined call's cross-file set |

  One lens was consuming the call's attention while the diff-local lenses barely
  reported; splitting recovered four findings (three confirmed real against this
  repo, incl. a silent config-validation gap). **What it does not buy: speed.**
  The longest single call drops only 374 → 313 s (16%) and TOTAL work rises to
  439 s, so `cross-file-trace` is the most exploration-heavy lens but not the
  sole cost — this alone does not clear the 600 s wall. Two further effects,
  neither reachable by lowering effort: a timeout now costs ONE lens instead of
  three (`correctness`/`removed-behavior` survive it), and — carrying no
  MANDATORY lens — the gate may prune the whole call on a diff with no
  cross-file surface, where the old layout kept it alive because `correctness`
  held the cluster open. Cost when kept: one extra call per live backend.
- **The cluster is the fan-out unit for EVERY voice** since 0.7.0 — Claude
  finders (≤5) *and* codex/grok (one CLI call per gated cluster each);
  `--max` splits all of them to one call per lens (≤11 units → ≤22 external
  calls) — the granularity ladder is `--quick` (future) =
  one broad pass → default = per-cluster → `--max` = per-lens. The **gate
  stays per-lens** (a fully-pruned cluster spawns no agent for anyone); design lenses are
  first-class in the gate prompt, skipped only when the diff can't pay off.
  **Accepted tradeoff of the cluster default:** per-lens failure isolation is
  gone — one crashed cluster call drops that whole cluster's coverage *for that
  voice* (a visible `backendError` carrying the unit + its lenses, never
  silent); `--max` restores per-lens isolation. Documented, not retried per-lens (minimal — the default
  trades isolation for fewer agents).
- **`kind` is derived from the lens name** (`design` vs `defect`) — no
  finding-schema change, so the 3-place schema mirror is untouched. A merged
  cluster's kind comes from its TAGGED members (design only when every tagged
  member is design — a design suggestion merged with a real defect must not
  leave the defect ranking); untagged (`unspecified`) members don't vote, and
  an **all-untagged cluster is never auto-accepted** — its "consensus" is
  backed by no lens, so it is verified like a solo (the `--max` dogfooding
  round found exactly this hole). **Untagged findings stay `kind: "defect"`**
  (the safe bucket), NOT inferred as design from cluster homogeneity: 0.5.1
  tried that inference (to keep a dropped-`[reuse]` design suggestion out of the
  defect table) and reverted it — a design finder is invited to report defects
  too, so an untagged finding may be a real off-lens BUG, and inferring `design`
  routes that bug to applicability verify (wrong rubric → can drop a real defect)
  and out of the `--loop` defect tally. Dropping a bug outweighs mis-filing a
  suggestion (the branch's own external-only self-review caught this).
- **Verify path decision: kind-aware prompt, not bypass.** Design findings are
  suggestion-shaped, but each has a falsifiable applicability core (reuse
  target exists? simpler form behavior-identical? claimed waste real?) — the
  same 3-state verifier runs with an applicability prompt, **even for design
  clusters with cross-family consensus** (agreement ≠ applicability: two voices
  can agree on a nonexistent reuse target — the first live swarm run over this
  very feature caught that gap. That was originally because externals were
  diff-only; since 0.6.0 they read the repo, so the residual reason is
  correlation, not blindness: the voices share a prompt frame, a cluster scope
  and — per-cluster since 0.7.0 — the same lens briefs). Bypassing
  into an unverified "maintainability" section would have surfaced unchecked
  suggestions from precisely the noisiest lenses. Methodological lenses are
  factual → normal defect verify; defect consensus stays auto-accepted.
- **Report keeps kinds apart**: defects table first, then a same-format
  `Design` table (shared numbering — the workflow sorts defects first);
  `balance.design` counts the subset. Lens prefixes are parsed with `[\w-]`
  (hyphenated names like `removed-behavior` — plain `\w` misses them). The Design
  table has no lens column; instead each design row's finding cell is prefixed
  `[lens]` (0.5.1) — the SAME in the in-session table and the PR comment, so lens
  attribution reads identically on both surfaces.
- Effort: design lenses run at the **same effort** as defect lenses (`xhigh`
  under `--max` — user call: depth applies to design thinking too).

## The skill ↔ workflow wiring (the non-obvious parts)

- **`args` reaches the workflow script as a JSON *string*, not an object.**
  `args?.adapter` was `undefined` until the script normalized:
  `let INPUT = typeof args === 'string' ? JSON.parse(args) : (args || {})`.
  This cost a run to discover (the first smoke test tripped the input guard with
  0 agents). Always parse-if-string at the top of a plugin workflow.
- **`${CLAUDE_PLUGIN_ROOT}` is NOT substituted inside a `.js` file** (only in
  SKILL.md/markdown). So the adapter path and the temp-file paths must be passed
  **via `args`** from the skill (which *does* get the substitution), e.g.
  `Workflow({scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/swarm-review.js", args: {…}})`.
  The shipped skill passes these fields (no count — a count is the part that
  goes stale first), and each absent one degrades silently rather than loudly,
  which is why the list is worth stating in full:
  `adapter`, `diffFile`, `externalPromptFile`, `externalVoices` (the originals),
  plus `findingNonce` (absent → the second-order finding fence is disabled,
  `fenceDegraded`), `telemetryFile` (absent → no `--telemetry/--unit`, so the
  report's voice-timing section silently prints nothing), and
  `probeBudgetSeconds` / `probeTimeoutSeconds` / `maxPromptBytes` (absent → a
  "config handshake" warning plus a fallback, so the workflow's margin and cap
  can disagree with the skill's oversize gate) — the probe bound and budget are
  taken as a PAIR, so supplying one without the other makes BOTH fall back.
  Two more are conditional: `timeoutSeconds` only when the user set
  `SWARM_TIMEOUT`, and `max: true` only for `--max`. Re-deriving the call from
  this entry is the documented use, so an out-of-date list here *is* the bug.
- **Workflow JS has no Bash/filesystem access**, so the diff never enters the
  script. The **skill** builds two temp files in deterministic Bash — the raw
  diff (Claude finders `Read` it) and a **fenced external prompt** (review
  instructions + the diff wrapped in untrusted-data markers) — and passes their
  paths. The external CLIs get the fenced prompt via `agents.sh run … --prompt-file`.
- The skill invoking `Workflow` is the explicit **opt-in** the Workflow tool
  requires; a plugin skill may not otherwise trigger it.

## Design decisions

- **Consensus counts model *families*, not backends.** A cross-family cluster
  (≥2 of claude / openai / grok) of TAGGED defect findings is CONFIRMED without
  extra verify; everything else goes through the 3-state verifier — solos
  (adversarial), all design clusters (applicability — consensus included, see
  the 0.5.0 lens-set section), and all-untagged clusters (no lens backs their
  "consensus"). With composer removed (0.4.3) the backend→family map is 1:1, so
  for consensus counting it is a no-op today — but the fan-out is
  many-voices-per-family (one Claude finder per gated lens), so "same vendor
  agreeing with itself is one vote, not a cross-check" stays the load-bearing
  invariant the day a second same-vendor voice returns.
- **Security is intentionally minimal** (user directive: no cannons-at-sparrows).
  The P1 adapter floor stays (secret-jail sandbox, jailed read+web externals
  since 0.6.0 — see [[swarm-backend-adapter]], secret scrub, env filter,
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

- **`args.claude: false`** runs an **external-only control** (codex + grok-4.5,
  no Claude finder lenses, no gate; merge/verify still in-session).
  Proven useful: a control run found real bugs the with-Claude run missed (an
  `aws_secret_access_key` scrub-list drift, `git diff` omitting untracked files)
  — the "different models catch different defects" premise, live.

## P5: `--fix` / `--loop` actions (swarm 0.3.0)

`/swarm:review` can now **act**, not just advise — but the loop is **orchestrated
in-session by Claude between Workflow runs**, one workflow run per review round,
because workflow JS has no Bash and can't edit files (same constraint that keeps
the diff out of the script, above). Claude applies edits between rounds.

- **Only Claude edits.** External agents stay review-only — never `codex apply`,
  never hand them edit authority (also the security posture: they run jailed,
  read-only — read+web tools but no write/shell). Act only on ✅-agree +
  🟨-partial findings; 🟨 = apply the
  session's own variant, not the reviewer's `recommendation` verbatim;
  ❌-disagree is never touched and stays visible in the report.
- **Re-confirm claim-vs-code before every edit** — a stale finding (comment rot,
  already-fixed, line drift) is reported as skipped, never fabricated into an edit.
  **Kind-aware (0.5.1):** a `kind:"design"` finding has no line-local defect to
  re-find, so re-confirm the *suggestion still applies* (reuse target / duplication
  / simpler form / waste still present), not "defect still present" — else an
  agreed design fix is silently dropped as skipped-stale.
- **Deterministic bits live in `scripts/loop-closeout.py`, not skill prose**
  (per the project's prose-drift memory — stateful skill logic drifts as
  prose): `step` = the termination decision in **fixed order** (0-findings /
  nothing-agreed / no-change / **design-only** / cap, default 10), `box` = the OPEN-findings
  close-out visualization that **shows a legitimate rise** (a fix surfaced new
  findings) instead of hiding it. Stateless — Claude passes the per-round counts
  in; no state file, so no cwd footgun. The determinism is **the arithmetic, not
  the inputs**: `F/A/C/D/pending/OPEN[]` are Claude's in-session tallies, so a
  miscount still feeds a wrong reason in (garbage-in) — the script can't make a
  judged count reproducible, only the branch logic over it.
  **`design-only` (0.5.1, via `--defects D`):** design suggestions are subjective
  and self-spawning (each applied simplification surfaces a fresh one), so a
  defect/design-blind tally ran the loop to the cap on them. The loop now
  converges once no *defect* finding remains — design is advisory and never holds
  it open; `--pending` is defect-scoped for the same reason. Omitting `--defects`
  disables the reason (legacy callers see the original four). **Accepted residual
  (the branch's own self-review flagged it):** like `cap`, design-only fires
  BEFORE the round's re-review, so this round's design fixes are applied but not
  re-reviewed — a simplification could introduce a defect the loop never catches.
  Forcing a re-review would re-open the very churn design-only closes (design
  findings diverge), so the close-out instead flags the residual and recommends a
  fresh `/swarm:review` over the result.
- Loop mechanics mirror pr-flow `/cycle` run locally (no push / no `@claude`
  poll); the `Status` column (🔧/⏭️/🔁) and stable `#` across rounds come from
  the report table contract this entry defines above (P2 reserved them).
- **`--max` profile** (`INPUT.max` in the workflow): lifts every voice to its
  ceiling — codex `gpt-5.6-sol`@`xhigh` (codex has NO `max` tier, xhigh is its
  top), Claude finder lenses + verifier `xhigh`; gate/merge and grok (`high` is
  its ceiling since grok 0.2.101 dropped `max` — it runs there on both
  profiles) unchanged. Orthogonal to `--fix`/`--loop`,
  composes with both. The profile's live settings are verified end-to-end
  (`gpt-5.6-sol`@`xhigh` at wiring time; grok re-verified at `--effort high` on
  0.2.101) — the "no silent fail on a non-existent model/effort" rule.

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
- **The publish path is a deterministic script, not skill prose** (swarm 0.4.1,
  `scripts/pr-post.py` + `test_pr_post.py`) — the accepted residual from the
  `--loop` review that spawned this. Three prose iterations each regressed the
  same way (a forgeable heredoc terminator, an advisory-echo "stale check" that
  posted anyway, `$PR_NUM`/`$PR_HEAD_OID` referenced across tool calls that don't
  share shell state, per-cell sanitize rules with no enforcer), so it moved to
  code: `build` renders the exact body from structured gated rows + balance + PR
  meta through a **per-cell sanitizer** that **entity-encodes** every markdown-
  active char (`|`→`&#124;`, `[`/`]`, backtick, `* _ ~`, `<>`, `@`→`&#64;`,
  `://`/`www.`→entities). **Entity-encoding, not backslash-escaping** — a review
  (external-only, "ohne opus") caught that `\|` becomes `\\|` under backslash-
  escaping and frees a live pipe / re-opens `\[..\](url)`; a numeric entity carries
  no literal metacharacter, so the table delimiter / link / mention can never
  re-form (the table splits on a literal `|` *before* inline parsing). `ort` is an
  inert code span (backtick + `|` **stripped**, since entities don't decode inside
  a span and escaping is bypassable there too). Header `pr_num`/`head_oid` are
  validated in `render_body` (digits / hex-only), not just at the gh-target seam —
  else a JSON `pr_num` like `"29\n\n**evil**"` injects markdown past the cell
  sanitizer. `post` owns the **real stale-head gate**, built body-last so the gate
  is the final step before the comment: it fails **closed** on both a mismatch
  (`SWARM_PR_STALE`) *and* an unreadable live head (`SWARM_PR_HEAD_UNVERIFIED`) —
  publishing a possibly stale review under the user's identity is worse than a
  retry. Then `gh pr comment --body-file`, **rebuilt from the same JSON** so what
  the confirm gate showed is what is sent, with a self-cleaning temp file. SKILL
  step 5 shrank to: assemble JSON (cells raw, script sanitizes — never pre-escape)
  → `build` → human injection-scan + confirm once → `post`, branching on one token.
  Same two-tier exit as `loop-closeout.py` (operational → token + exit 0; misuse,
  incl. non-list `rows`, → stderr + exit 2). Pure functions
  (`sanitize_prose`/`sanitize_code`/`stale_gate`/`render_body`) keep it unit-tested.
  0.5.0 moved one more rule from prose to code the same way: rows carry optional
  `kind`/`lens`, and the script orders defect rows before design rows and
  prefixes design finding cells with `[lens]` — the caller passes findings
  through verbatim, never hand-orders or hand-prefixes. 0.5.1 added an
  **idempotency guard**: the finder's summary may already carry its own
  `[lens]` self-tag (the workflow doesn't strip it, and a merge can leave a
  *different* design lens as the representative), so prefixing unconditionally
  posted `[reuse] [reuse] …` / `[reuse] [simplification] …`. If the cell already
  opens with a known design-lens tag (`DESIGN_LENS_TAGS`, sync-checked by
  `test_lens_sync.py`) it's kept as-is. Note it is **not** a kind fallback (the
  retired `DESIGN_LENSES` was): it runs only inside the already-`kind`-decided
  design branch and never moves a row between tables.

## Per-cluster external prompts (shipped 0.7.0)

Externals no longer run ONE broad multi-lens review each: codex and grok fan out
over the **same gated clusters** as the Claude finders (`unitsFor()` builds the
units once; `externalUnits` reuses `finderUnits` whenever a gate ran, so the two
sides cannot drift). Cost is `live-backends × units` — ≤2×5 default, ≤2×11 under
`--max` — logged at fan-out, never silently capped.

Decisions worth keeping:

- **Where the prompt is assembled.** `LENS_BRIEF` stays single-source in the
  workflow, so the briefs must *travel*. The workflow sandbox cannot write files
  and the skill's Bash prep runs **before** the gate exists (so it cannot know the
  surviving clusters) — so the adapter grew `run --lens-instr <s>`, which prepends
  the briefs to the fenced-diff prompt in **deterministic shell**. Handing that
  write to the transport agent was the rejected alternative: it would put prompt
  assembly inside an LLM, breaking the same "fencing/assembly is never an LLM step"
  contract the diff fencing follows. Being on the adapter also makes it
  backend-agnostic — a future voice (Kimi) inherits per-cluster prompts for free.
  Consequence: the instruction rides as one single-quoted argv word in a command a
  haiku transport retypes, so briefs are guarded apostrophe-/control-char-free and
  the command is kept to one line. E2E: 8/8 calls round-tripped byte-exact.
- **The SKILL.md HDR is now LENS-FREE** — the hand-mirrored lens list is gone
  (that was the drift risk `test_lens_sync.py` existed to catch; the check flipped
  to a *negative* one). The HDR must also stay lens-*agnostic* about finding kinds:
  a leftover "report every design-quality improvement" line contradicts a
  defect-only cluster's "one finding per distinct defect" instruction and invites
  off-cluster tags.
- **A gate that prunes for everyone needs a floor.** With externals gated too, a
  lens the low-effort haiku gate drops is reviewed by *nobody* — the full-width
  external calls used to absorb a mis-gate. `MANDATORY_LENSES`
  (`security`, `adversarial`, `correctness`) is the code-level backstop, since the
  gate's only other protection is a sentence in its own prompt (injection-reachable
  via the diff it classifies). **Accepted cost:** flooring these pins the
  `breakage` and `threat` clusters always-on, so the gate can only prune `design`
  and `consistency` — a doc-only diff still pays 2 clusters × live voices. Chosen
  deliberately: a clean report on the dimensions most costly to miss is worse than
  the calls saved.
- **The transport retype is guarded by a content checksum, not trust.** The instruction
  rides as one argv word a haiku agent retypes; an EMPTY value is refused, and
  `--lens-instr-sum` (an FNV-1a/32 of the exact text, REQUIRED alongside it) makes
  a *reworded, paraphrased or truncated* one fail too — otherwise the backend would review a narrower scope
  than the findings get labelled with, hollowing out "the voice IS its cluster".
  8 hex chars survive a retype far better than 1 KB of prose. A byte COUNT was the first attempt and was not enough — `security`/`altitude`, `ONLY`/`ALSO` are same-length swaps that change the scope while the count still matches; the check must bind content, and must be refused-if-absent or a dropped flag voids it.
- **Accepted residuals** (re-found by every review round — decided, not
  overlooked): (a) [RESOLVED 0.7.0] the oversize skip is now
  decided in the prep Bash (`EXTERNALS_OVERSIZE`), not by model arithmetic — the
  constant stays in the skill but is pinned to `max_bytes` by `test_lens_sync.py`;
  (b) one adapter process per unit re-runs
  grok's process-local model probe per cluster (4× instead of 1×) — wasted
  network calls, but they overlap the review calls, so wall-clock cost is ~0 and
  caching would add staleness for no user-visible gain.
- **The coverage line must partition the lens set.** Both halves are easy to get
  wrong and neither fails loudly: lenses the gate lists in neither `run` nor
  `skip` are dropped silently (seen live — a real run swallowed `adversarial`),
  and a floored-in lens left out of `gate.run` *runs* while appearing in neither
  report column (the external-only control run caught exactly that regression in
  the floor's first version). The workflow now rewrites both fields and asserts
  they partition `CANDIDATE_LENSES`.

For routine depth prefer higher external `--effort` / grok `--best-of-n` (one
call, more thinking) over more calls.

## Verified end-to-end (2026-07-05)

Real background runs on this branch: a Claude-only smoke run proved the wiring
(6 agents, correct return shape); the review **found a real bug in its own
grok-composer parser** (first-object-vs-findings-object), which was then fixed
— that parser has since been removed with the composer backend (0.4.3), but it
stands as the proof that swarm can catch a genuine bug in its own diff. Only
`REFUTED` solos are dropped; consensus/solo/refuted counts + per-lens raw→
surviving ship in the `balance` block the skill renders. Iterating a
`/swarm:review` loop over the branch caught several fix-induced regressions
(a pipefail abort, an unconditional untracked-append, incomplete scrub coverage)
— the loop's real value is catching incomplete fixes, but it diverges
(marginal findings grow), so cap the rounds.
