# Shared Review Routing

> Canonical rule for "which review should this PR get?" — shared by `/open`
> (step 10), `/cycle` (step 7), `/check` and `/rebase` wherever they would
> otherwise recommend `/cycle`. Defines the probe, the three answers, and the
> mandate-gated hand-off to the local review. Consumers add only their
> stage-specific behavior (trigger vs. recommend); they do not restate the tree.

## Why this exists

`@claude review` is a PR comment. It succeeds whether or not anything is
listening, and the poll that follows runs to its timeout either way. Before this
spec, `/open` and `/cycle` each carried their own copy of the decision; a repo
with no review bot was sent to `/cycle`, which commented into the void and polled
for ten minutes, every time.

## 0. `$LANE`, and why every block re-resolves it

Every snippet here takes the **lane** — the worktree holding the PR's branch —
because the session cwd is often not that worktree (`/cycle` run from the main
repo for a task branch). **Shell variables do not survive between Bash tool
calls.** A skill that sets `LANE` in step 1 and writes `"$LANE"` in step 7 sends
an *empty* argument, and both scripts then silently resolve the cwd — the exact
wrong-lane read `lane` was added to prevent, with no error to notice. So every
block below resolves it **in the same call** that uses it:

```sh
LANE="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" lane "$(git branch --show-current)")" || LANE=.
```

`lane` exits 3 when no worktree holds the branch (a plain repo with no
work-system lanes); `|| LANE=.` is the cwd, which is right exactly then. Never
carry `$LANE` across tool calls, and never drop the argument.

## 1. Probe

```sh
LANE="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" lane "$(git branch --show-current)")" || LANE=.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" has-bot "$LANE"
```

Anchored on the lane's repo root (a cwd-relative check is wrong from a
subdirectory, or from the main repo while the PR belongs to a worktree) — hence
the `"$LANE"` argument, which is not optional. No network. Always emits the same
four keys — `has_bot=`, `why=`, `workflows_dir=`, `matched=` (empty when not
applicable) — and exits 0 for every answer.

It reads the **default branch**, not the checkout: GitHub runs an
`issue_comment` workflow from the default branch, so a task branch that adds one
would otherwise probe `yes` and poll into the void, and one that removes it would
probe `no`. `why=` and `workflows_dir=` name the ref actually inspected. It reads
workflow *structure*: a `uses:` inside a `#` comment or a `run:` block, or a file
in a subdirectory GitHub never reads, is not a bot.

| `has_bot` | means | consumer does |
|---|---|---|
| `yes` | a workflow both `uses:` `anthropics/claude-code-action` **and** is triggered by `issue_comment` — the comment will reach it | trigger (`/cycle`) or recommend `/cycle` |
| `no` | a claude workflow is there and demonstrably cannot answer a comment (push-triggered only) | do **not** trigger, do **not** recommend `/cycle`; go to §2 |
| `unknown` | cannot be told locally — see below | relay `why=`, name both routes, **ask**; never pick silently |

`unknown` is its own answer, not a soft `no` — and it is the **common** one. A
local scan can prove a bot is there; it can never prove one is absent, because
the Claude GitHub App answers `@claude review` with no workflow file of its own
and unrelated CI in the same directory says nothing about whether it is
installed. So `no` is reserved for the single case with positive evidence
(a claude workflow exists and is not comment-triggered); everything else —
**including "no workflow mentions claude"** — is `unknown`, together with: no
`.github/workflows` at all, an unreadable directory or file, a comment-triggered
workflow that mentions `@claude` without using the action or delegates to a
reusable workflow, and a claude workflow with a custom `trigger_phrase` (which
`@claude review` may not fire). Guessing `no` permanently reroutes a working bot,
silently, because `no` is the one answer no consumer asks about; guessing `yes`
polls for ten minutes.

## 2. Local route (only on `has_bot=no`)

The local review is `/swarm:review --pr <N>` (the swarm plugin). Whether to run
it *unasked* is a mandate question:

```sh
LANE="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" lane "$(git branch --show-current)")" || LANE=.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" allows local-review "$LANE"
```

| exit | meaning | consumer does |
|---|---|---|
| `0` | the lane's mandate pre-authorized a local review | **book a round** (below), then run `/swarm:review --pr <N>`; say which route was taken and why ("no review bot on this repo — ran the local review, which your mandate covers") |
| `1` | recorded as out of bounds, or never granted | **offer**, don't run: "No `@claude` review bot is configured on this repo. `/swarm:review --pr <N>` reviews it locally — want me to?" |
| `3` | no mandate recorded, or work-system not installed | same as `1` — offer. A missing record is an unasked question, not a refusal |
| `2` | the record cannot be vouched for, or the question was malformed | show stderr, stop, ask — never read as `1` or `3` |

The authoritative list of what each code covers is `mandate.sh`'s own header —
**read it there rather than trusting this gloss**, which exists only to say what
a consumer does. Exit 2 has grown twice already (a git-tracked file, a symlink on
every verb, a block scalar, an action outside the vocabulary), and every prose
copy that enumerated causes went stale within a release.

**Booking the round.** A local review consumes the lane's `review_budget` exactly
as a bot review does — otherwise the budget bounds nothing on this path and
`review_rounds_used` stays 0 forever:

```sh
LANE="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" lane "$(git branch --show-current)")" || LANE=.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/mandate-shim.sh" round "$LANE"
```

Book it **once per review**, and only if your own stage does not already book —
`/cycle --loop` books each iteration in its Setup, so it must not book again
here. §3 says which consumer books.

**Then branch on what `round` reported**, exactly as the bot route does:
`review_budget_exhausted=yes` means the lane has spent its allowance, so **stop
and ask instead of running the review** — booking a round and reviewing anyway
lets a spent budget fund one more review every time and walks the counter past
its own limit. Exit 4 = the round could not be persisted (read-only tree, a lock
held by another session): say so and do not review on an in-session count.

swarm not installed → name both gaps plainly (no bot, no local reviewer). Never
leave the user with a recommendation to run something that cannot work here.

## 3. What each consumer adds

- **`/cycle` step 7** — on `yes`, posts `@claude review` and polls. On `no` with
  exit `0`, treats the swarm findings as this round's review (loop mode
  included: the loop cares about findings, not where they came from).
  **Books the round** on a plain `/cycle`; under `--loop` it does **not** book
  here, because Setup already booked this iteration.
- **`/open` step 10** — never triggers (creation, not triggering, is its job):
  on `yes` it recommends `/cycle`; on `no` it applies §2 and **books the round**
  when it actually runs the local review.
- **`/check`, `/rebase`** — recommend-only skills. Where they would say "run
  `/cycle`", they run the probe first and recommend per the table; they never
  run the local review themselves, and therefore **never book a round**.
