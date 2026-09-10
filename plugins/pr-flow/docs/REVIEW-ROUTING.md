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

## 1. Probe

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" has-bot
```

Repo-root anchored (a cwd-relative check is wrong from a subdirectory, or from
the main repo while the PR belongs to a worktree). No network. Always emits the
same four keys — `has_bot=`, `why=`, `workflows_dir=`, `matched=` (empty when not
applicable) — and exits 0 for every answer. It reads workflow *structure*: a
`uses:` inside a `#` comment or a `run:` block, or a file in a subdirectory
GitHub never reads, is not a bot.

| `has_bot` | means | consumer does |
|---|---|---|
| `yes` | a workflow both `uses:` `anthropics/claude-code-action` **and** is triggered by `issue_comment` — the comment will reach it | trigger (`/cycle`) or recommend `/cycle` |
| `no` | workflow files exist and none can answer a comment (nothing references the bot, or only push-triggered claude workflows) | do **not** trigger, do **not** recommend `/cycle`; go to §2 |
| `unknown` | cannot be told locally — see below | relay `why=`, name both routes, **ask**; never pick silently |

`unknown` is its own answer, not a soft `no`. It covers: no `.github/workflows`
at all (a repo with no CI *or* one served only by the Claude GitHub App — the App
answers comments with no workflow file, and the two are indistinguishable
locally), an unreadable directory or file, a comment-triggered workflow that
mentions `@claude` without using the action or delegates to a reusable workflow,
and a claude workflow with a custom `trigger_phrase` (which `@claude review` may
not fire). Guessing `no` there permanently reroutes a working bot; guessing `yes`
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
| `0` | the lane's mandate pre-authorized a local review | run `/swarm:review --pr <N>` now; say which route was taken and why ("no review bot on this repo — ran the local review, which your mandate covers") |
| `1` | recorded as out of bounds, or never granted | **offer**, don't run: "No `@claude` review bot is configured on this repo. `/swarm:review --pr <N>` reviews it locally — want me to?" |
| `3` | no mandate recorded, or work-system not installed | same as `1` — offer. A missing record is an unasked question, not a refusal |
| `2` | the mandate file is corrupt (duplicate key, open fence, symlink) | show stderr, stop, ask — never read as `1` or `3` |

swarm not installed → name both gaps plainly (no bot, no local reviewer). Never
leave the user with a recommendation to run something that cannot work here.

## 3. What each consumer adds

- **`/cycle` step 7** — on `yes`, posts `@claude review` and polls. On `no` with
  exit `0`, treats the swarm findings as this round's review (loop mode
  included: the loop cares about findings, not where they came from).
- **`/open` step 10** — never triggers (creation, not triggering, is its job):
  on `yes` it recommends `/cycle`; on `no` it applies §2.
- **`/check`, `/rebase`** — recommend-only skills. Where they would say "run
  `/cycle`", they run the probe first and recommend per the table; they never
  run the local review themselves.
