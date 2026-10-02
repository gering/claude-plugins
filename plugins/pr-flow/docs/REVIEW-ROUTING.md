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
bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" has-bot "<lane>"
```

Consumers do not call this directly — `route` (below) runs it. Anchored on the
lane's repo root (a cwd-relative check is wrong from a
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

Because `unknown` is normal, a consumer never branches on `has_bot` alone — it
branches on the **route** below, which adds the user's pin and what earlier
rounds already learned. Before the route existed, `/cycle` posted and polled on
every `unknown`, and a bot-less repo paid the ten-minute poll on every round and
every lane.

### Route — what consumers branch on

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" route --branch "$(git branch --show-current)"
```

`--branch` resolves the lane from `git worktree list` itself (§0's rule — no
wrapper, and no work-system needed). Emits `route=bot|local`, `record=yes|no`,
`source=`, `why=`, `has_bot=` (the probe's verdict, vocabulary unchanged; empty
when a `local` pin skipped the probe) and `lane=`, and exits 0. Decided in this
order:

| `source` | when | `route` |
|---|---|---|
| `setting` | `.pr-flow.toml` has `[review]` / `route = "local"` | `local` |
| `setting` | `route = "github"` — clears the memory | `bot` |
| `probe` | `auto` and `has_bot=yes` — clears the memory | `bot` |
| `memory` | `auto`, a recorded "no bot answered on <date> (PR #N)", and no Claude bot comment in the repo since | `local` |
| `evidence` | `auto`: in the repo's last 100 comments, owners/members/collaborators asked `@claude` ≥2× and **no** bot account ever wrote | `local` |
| `default` | everything else (`auto` + `unknown`) | `bot`, `record=yes` |

`--offline` (the never-block consumers) skips the network step — no evidence, no
reply check — and **never writes**: the memory is shared by every lane, so a
read-only `/check` must not clear it. The clearing actions in the table happen
only on a networked call.

`review.route` defaults to `auto` (the schema owns the default, the enum and the
file name). The script reads the TOML itself against that schema — the settings
plugin is optional and does not yet discover installed plugins — and ignores a
symlinked or invalid file with a note in `why=`.

**Memory.** On `record=yes`, `/cycle` passes `--record "<lane=>"` to `poll`, and
the **script** books a timeout itself — never the caller's recollection, which a
compaction or an interjection during a ten-minute background poll can lose. It
does **not** book when the bot acknowledged (`Claude Code is working`) and was
only slow. It reports `route_recorded=yes|no` on stderr. By hand:
`claude-review.sh route-record "<lane>" --pr <N>`.

The record lives in the git **common** dir (`<common>/pr-flow/review-route`),
shared by every worktree and never committed; it is never written into the
user's settings. It clears itself — the repo switches back to `auto` — when a
poll sees a finished Claude review, or a networked `route` sees a Claude bot
comment newer than the record (a late reply, a manual mention, an App installed
later), `has_bot=yes`, or `route = "github"`. By hand: `claude-review.sh
route-clear`. Only `auto` records; evidence is never recorded.

Evidence and memory are **not proof** — `has_bot` stays `unknown`. **Every round
report states the route and its `why=`** (see `REVIEW-OUTPUT-FORMAT.md`).

**The consumer split — the one copy; skills point here, they do not restate it:**

| | `route=local` | `route=bot`, `source` = `setting`/`probe` | `route=bot`, `source=default` |
|---|---|---|---|
| **`/cycle`** (triggers; networked) | §2 now — no post, no poll | post, poll; timeout → §2 | post, `poll --record`; timeout → §2. **Never ask** — asking would stop `--loop` every round |
| **`/open`** (networked, never triggers) | §2 | recommend `/cycle` | name both routes |
| **`/check`, `/rebase`** (`--offline`) | recommend `/swarm:review --pr <N>` | recommend `/cycle` | name both routes |

Always quote the `why=`.

## 2. Local route

Reached on `route=local` and from a timed-out `/cycle` poll. The local review is
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

Routing itself is the §1 table. Stage-specific additions only:

- **`/cycle`** — run `route` in step 7, **before** the auto-trigger check, so a
  round that finds an auto-triggered review still carries a `why=`. An
  auto-triggered review proves a bot: poll it **without** `--record`. A §2 round's
  swarm findings are that round's review (`--loop` included). **Booking:**
  `--loop` books once per iteration in the loop body; a plain `/cycle` books once,
  before it triggers. Either way **once per review**, on whichever route.
- **`/open` step 10** — never triggers; it **books the round** only when it
  actually runs the local review.
- **`/check`, `/rebase`** — never run the local review, so **never book a round**.
