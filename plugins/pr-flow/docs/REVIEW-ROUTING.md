# Shared Review Routing

> Canonical rule for "which review should this PR get?" — shared by `/open`
> (step 10), `/cycle` (step 7), `/check` and `/rebase` wherever they would
> otherwise recommend `/cycle`. Defines the probe, the route (setting + remembered
> answer), and the mandate-gated hand-off to the local review. Consumers add only their
> stage-specific behavior (trigger vs. recommend); they do not restate the tree.

## Why this exists

`@claude review` is a PR comment. It succeeds whether or not anything is
listening, and the poll that follows runs to its timeout either way. Before this
spec, `/open` and `/cycle` each carried their own copy of the decision; a repo
with no review bot was sent to `/cycle`, which commented into the void and polled
for ten minutes, every time.

## 0. The lane is a flag, not a variable

Every call here concerns the **lane** — the worktree holding the PR's branch —
because the session cwd is often not that worktree (`/cycle` run from the main
repo for a task branch).

**Pass `--branch "$(git branch --show-current)"`. That is the whole rule.** Both
scripts resolve the worktree themselves and report which one answered:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" allows open-pr --branch "$(git branch --show-current)"
```

This replaced a two-line incantation (`LANE="$(… lane …)" || LANE=.`, then a
`"$LANE"` argument) that appeared at about ten sites. Three consecutive review
rounds each found a site that had dropped one of the three pieces — and a dropped
piece is silent: the script falls back to the cwd and answers with a **different
lane's mandate**, which is the wrong-lane read this mechanism exists to prevent.
A flag cannot be half-copied.

Every verb that takes `--branch` emits two extra lines:

- `lane=` — the directory that actually answered.
- `lane_source=branch|cwd` — `cwd` means no worktree holds that branch. That is
  normal in a plain repo with no lanes, and suspicious anywhere else: on `cwd`,
  compare the `task=` line (see §2) before acting on the verdict.

A detached HEAD makes `git branch --show-current` empty, which is not an error —
it resolves to the cwd and says so via `lane_source=cwd`.

## 1. Probe

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" has-bot "$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" lane "$(git branch --show-current)" 2>/dev/null || echo .)"
```

Anchored on the lane's repo root (a cwd-relative check is wrong from a
subdirectory, or from the main repo while the PR belongs to a worktree). No
network. Always emits the same four keys — `has_bot=`, `why=`, `workflows_dir=`,
`matched=` (empty when not applicable) — and exits 0 for every answer.

It reads the **default branch**, not the checkout: GitHub runs an
`issue_comment` workflow from the default branch, so a task branch that adds one
would otherwise probe `yes` and poll into the void. `why=` and `workflows_dir=`
name the ref actually inspected. It reads workflow *structure*: a `uses:` inside
a `#` comment or a `run:` block, a file in a subdirectory GitHub never reads, a
branch or input merely *named* `issue_comment` — none of those is a bot.

| `has_bot` | means | consumer does |
|---|---|---|
| `yes` | a workflow both `uses:` `anthropics/claude-code-action` **and** is triggered by `issue_comment` as a direct child of `on:` | trigger (`/cycle`) or recommend `/cycle` |
| `unknown` | cannot be told locally — the normal answer | **depends on the consumer, see below** |
| `no` | reserved; not emitted today | — |

### `unknown` is the normal answer, and it is not "ask"

A local scan can prove a bot is there. It can **never** prove one is absent: the
Claude GitHub App answers `@claude review` with no workflow file of its own, so
nothing on disk distinguishes "no bot" from "App installed". That makes `unknown`
the answer for every repo without a comment-triggered claude workflow — including
a repo whose only claude workflow is `pull_request`-triggered (Anthropic ships
one), an unreadable directory, a reusable-workflow delegate, and a custom
`trigger_phrase`. `no` stays in the vocabulary for a future authoritative source
(an API probe) and is not emitted.

Because `unknown` is normal, what to do with it **splits by consumer**, and this
is the split — do not restate it elsewhere:

- **A consumer that TRIGGERS (`/cycle`) does not ask.** It posts `@claude review`
  and lets its bounded poll settle the question empirically; that is strictly
  more information than the probe can give. If the poll times out, nothing was
  listening: say so and fall through to §2 for this round. Asking instead would
  stop `--loop` on every iteration, since its steps re-run each round.
- **A recommend-only consumer (`/open`, `/check`, `/rebase`) names both routes**
  and lets the user pick. It has no poll to learn from.

That empirical answer used to be thrown away: every round and every lane of a
bot-less repo paid the ten-minute poll again. The **route** below remembers it.

### Route — what consumers actually branch on

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" route "$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" lane "$(git branch --show-current)" 2>/dev/null || echo .)"
```

Recommend-only consumers add `--offline` (no network; skips the evidence step).
Emits `route=bot|local`, `record=yes|no`, `source=`, `why=`, `has_bot=` (the
probe's verdict, vocabulary unchanged) and exits 0. Decided in this order:

| `source` | when | `route` |
|---|---|---|
| `setting` | `.pr-flow.toml` sets `review.route = local` | `local` |
| `setting` | `review.route = github` — also clears the memory | `bot` |
| `probe` | `auto` and `has_bot=yes` — also clears the memory | `bot` |
| `memory` | `auto`, a recorded "no bot answered on <date> (PR #N)" | `local` |
| `evidence` | `auto`, not `--offline`: in the repo's last 100 comments `@claude` was asked ≥2× and **no** bot account ever wrote | `local` |
| `default` | everything else (`auto` + `unknown`) | `bot`, `record=yes` |

`review.route` defaults to `auto` (the schema owns the default and the enum).
The script reads `.pr-flow.toml` itself against that schema — the settings plugin
is optional and does not yet discover installed plugins — and ignores a symlinked
or invalid file with a note in `why=`.

**Memory.** `record=yes` means: if the poll times out, record it —

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" route-record "<lane>" --pr <N>
```

It lives in the git **common** dir (`<common>/pr-flow/review-route`), so every
worktree of the repo shares it and it can never be committed. It is never written
into the user's settings. It is cleared by a poll that sees a finished Claude
review, by `has_bot=yes`, by `review.route = github`, or by hand with
`claude-review.sh route-clear`. Only `auto` records; evidence is never recorded.

Evidence and memory are **not proof** — `has_bot` stays `unknown`. Their
weakness is shared and accepted: once local, nothing posts `@claude review`
again, so a Claude GitHub App installed later is only noticed through `has_bot`,
a manual mention, `route-clear`, or `review.route = github`. The `why=` line
names those exits, and **every round report states the route and its `why=`**.

So the consumer split above reads, concretely:

- **`/cycle`**: `route=local` → skip post and poll, go to §2. `route=bot` → post,
  poll; on timeout, if `record=yes` run `route-record`, then §2 for this round.
- **Recommend-only**: `route --offline`; `local` → recommend
  `/swarm:review --pr <N>` with the `why=`; `bot` with `has_bot=yes` → `/cycle`;
  otherwise name both routes.

## 2. Local route

Reached on `route=local` and from `/cycle`'s timed-out poll. The local review is
`/swarm:review --pr <N>` (the swarm plugin). Whether to run it *unasked* is a
mandate question:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" allows local-review --branch "$(git branch --show-current)"
```

| exit | meaning | consumer does |
|---|---|---|
| `0` | the lane's mandate pre-authorized a local review | **book a round** (below), then run `/swarm:review --pr <N>`; say which route was taken and why |
| `1` | recorded as out of bounds, or never granted | **offer**, don't run: "No `@claude` review answered this PR. `/swarm:review --pr <N>` reviews it locally — want me to?" |
| `3` | no mandate recorded, or work-system not installed | same as `1` — offer. A missing record is an unasked question, not a refusal |
| `2` | the record cannot be vouched for, or the question was malformed | show stderr, stop, ask — never read as `1` or `3` |

The authoritative list of what each code covers is `mandate.sh`'s own header —
**read it there rather than trusting this gloss**, which exists only to say what
a consumer does. Exit 2 has grown three times already, and every prose copy that
enumerated causes went stale within a release.

**Check `task=` when `lane_source=cwd`.** The verdict lines carry the record's
own `task:`. If the lane fell back to the cwd, a leftover `MANDATE.md` from a
removed worktree can answer for a branch it was never recorded for — compare the
task before acting, and treat a mismatch as "no mandate" (offer, don't run).

**Booking the round.** An autonomous review consumes the lane's `review_budget`:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" round --branch "$(git branch --show-current)"
```

Branch on **`round_authorized`**, not on the exhaustion flag:

- `round_authorized=yes` → the round is yours; run the review. A
  `review_budget_exhausted=yes` alongside it means "this one may run, no further
  ones" — finish, then stop.
- `round_authorized=no` → the budget is spent and **nothing was charged**. Stop
  and ask; do not review.
- exit `4` → the round could not be persisted (read-only tree, or a lock another
  session holds). Say so and do not review on an in-session count. The message
  names the lock and the command to clear an abandoned one.

Book **once per review**, and only if your own stage does not already book — §3
says which consumer books where.

swarm not installed → name both gaps plainly (no bot answered, no local
reviewer). Never leave the user with a recommendation to run something that
cannot work here.

## 3. What each consumer adds

- **`/cycle` step 7** — branches on `route`: `local` goes to §2 without posting;
  `bot` posts `@claude review` and polls, and a timed-out poll records (when
  `record=yes`) and falls through to §2, whose swarm findings are then this
  round's review (loop mode included: the loop cares about findings,
  not where they came from). **Booking:** `--loop` books once per iteration in
  the loop body; a plain `/cycle` books once, before it triggers. Either way the
  round is booked **once per review**, on whichever route it takes.
- **`/open` step 10** — never triggers (creation, not triggering, is its job):
  it reads `route` (with network — it is not a never-block skill): `local` → §2;
  `has_bot=yes` → recommend `/cycle`; otherwise both routes. It **books the
  round** only when it actually runs the local review.
- **`/check`, `/rebase`** — recommend-only skills. Where they would say "run
  `/cycle`", they run `route --offline` first and recommend per §1; they never run the
  local review themselves, and therefore **never book a round**.
