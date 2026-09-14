---
title: "Worker Autonomy Mandate (MANDATE.md)"
createdAt: 2026-09-07
updatedAt: 2026-09-14
createdFrom: "branch: task/extend-worker-autonomy"
updatedFrom: "branch: task/extend-worker-autonomy"
pluginVersion: 1.9.0
prime: false
---

# Worker Autonomy Mandate (MANDATE.md)

Slice A of the autonomy arc sketched in
[manager-worker-orchestration](../architecture/manager-worker-orchestration.md):
the arc says kickoff *pre-authorizes* a set of milestones — this is the record
that makes that survive a process boundary. `/kickoff` writes `MANDATE.md`
beside `TASK.md`; `/continue`, `/open` and `/cycle` read it instead of
re-deriving what the worker may do. Format and semantics live in
`plugins/work-system/scripts/mandate.sh`; the skills only call it.

## Authorization is a record, never an inference

The whole point is a hard line between *describing work* and *granting
permission*. A worker must not conclude it may merge because TASK.md says the
task ends in a merged PR, because a previous session did it, or because someone
started it. So only `MANDATE.md`'s **leading frontmatter block** grants
anything — the parser stops at the closing `---`, and a body that quotes
`allow: merge` out of a task file grants nothing. That case is not theoretical:
task prose gets pasted into these files, which is why `test_mandate.py` asserts
it.

## The 0/1/3 exit contract

`mandate.sh allows <action>` is the only question callers should ask, and its
three answers must stay three:

- **0** — allowed
- **1** — `denied` (in `deny`) or `unlisted` (in neither list)
- **3** — no mandate recorded at all, or (through pr-flow's shim) work-system
  is not installed

Two more codes joined after the second review pass, and both exist so the
first three stay honest: **2** means the file is there but cannot be read as a
record (duplicate key, an unterminated frontmatter, a symlink at the path) —
a record nobody can vouch for grants nothing, and a caller mapping "anything
else" to 3 would proceed on it; **4** (from `round`) means the increment could
not be persisted, and looping on the in-session count instead is exactly the
budget restart the file exists to prevent.

Collapsing 1 and 3 into "not allowed" is the defect this contract exists to
prevent: **1 is a decision the user made, 3 is a question they were never
asked.** Hence "no mandate" is *not* a lockdown — it is exactly the pre-mandate
behavior where the worker asks before each milestone. `deny` beats `allow`,
matching is whole-action, and silence is not consent.

Two follow-on rules fall straight out of that contract, and both were review
findings rather than design foresight. Matching must be **literal**: an anchored
`grep -qx` is still a regex, so `allows '.*'` satisfied every list. And the action
vocabulary must be **closed** (`KNOWN_ACTIONS`) — at write time *and* at ask
time: a token `allows` does not recognize is a usage error (exit 2), because
answering `unlisted` (exit 1) would report a typo as a denial the user actually
chose — the exact collapse the three exit codes exist to prevent, re-entering
through the door. The third pass found the same collapse two more ways: a
comma-joined `allows 'a,b'` matched as a *sublist* of the allow line even with
`b` denied, and the membership test itself was a substring `case` over the
space-joined vocabulary, so two actions glued by a space passed as one token.
Membership is an exact-word loop now (`in_vocab`).

## What has to be durable, and why

`review_budget` bounds review→fix rounds, and `review_rounds_used` is
incremented **in the file**, not in a loop variable. An in-session counter dies
with the session, so a worker resumed via `claude -c` after a context loss would
silently restart its allowance and loop as long again. Anything that bounds
autonomy has to outlive the process it bounds.

Storage is the worktree root (a deliberate choice over a per-worktree git-dir or
a central per-repo store): visible and hand-editable, so widening a running
lane is just an edit. That visibility has a cost the design has to pay for
explicitly: `/kickoff` adds `/MANDATE.md` to the repo's **git exclude**, because
a worker instructed to commit as it goes will otherwise commit its own
authorization record — and once it reaches main, every later worktree starts
holding a grant recorded for a different task, with a spent budget and nobody
re-asked. `init` refuses a mandate whose `task:` does not match the lane for the
same reason. See
[worktree-task-file-copy](../architecture/worktree-task-file-copy.md) for why
that file is ephemeral in the first place.

Hand-editability also means the file must survive being edited *wrongly*: `round`
inserts `review_rounds_used` when a hand-edit removed it (a substitute-only
rewrite silently made the budget unbounded), values may not contain a newline
(they would inject further keys — and `scope` is model-authored from TASK.md,
which under `/adopt` comes from someone else's commits), and a duplicate key is
refused rather than resolved first-one-wins. The worst edit is the invisible
one: a file saved with CRLF (or a BOM) made the first line miss `---`, and the
parser returned an **empty mandate with exit 0** — every action `unlisted`, and
`round` printing a count it never persisted. Both are normalized now, and a file
with no leading fence is exit 2, not empty.

**A symlink is nobody's record either, and the guard has to sit on every
verb.** `init` refused one from the first review pass, and four rounds of docs
repeated "exit 2 covers a symlink" — while `show`, `allows` and `round` happily
followed the link, so a file outside the lane answered `allowed`. A *dangling*
link was worse: it failed the `-f` test and came back as exit 3, "nobody was
asked", sending the caller to legacy prompting instead of reporting a record it
cannot vouch for. The lesson is not about symlinks: **a guard that lives in the
writer is not a guard on the record.** Every read path has to ask the same
question, which is why `refuse_tracked` and `refuse_symlink` are now both called
by all four verbs, before the existence test.

**A tracked file is nobody's record.** The exclude and the task guard only
protect a `MANDATE.md` that is not yet in the index. One that git tracks came in
with a branch — an adopted fork PR, or main after someone committed theirs — and
its `task:` is guessable, so the guard cannot tell it from the lane's own. Every
verb refuses a tracked file (`git ls-files --error-unmatch`), `--force` included:
overwriting it just leaves a tracked, modified file for the next `git add -A`.
Untrack first, then re-ask. Same reason `init` now requires `task=` — omitted,
the guard never ran.

Match the mandate to the worker — from the registry's `supports=` field, not by
matching CLI names: an agent that cannot run the review skills gets an allow list
without `local-review`, because recording an authority the worker cannot exercise
is worse than recording nothing. Their bootstrap prompt (`agent-registry.sh`)
points at `mandate.sh`, not at `MANDATE.md`, precisely because they have no
`/continue` to read it for them. Naming the file handed the worker the bytes and
none of the guards — a record committed on an adopted fork branch, a symlink, a
duplicate key, a block-scalar grant, a `deny` that must beat a matching `allow` —
all of which a `cat` honors and the recorder refuses. The prompt also names
*only* the mechanism: an earlier version ended with "open a PR when the work is
complete", a concrete instruction that overrode the mandate the same prompt had
just told the worker to obey. And the capability→mandate mapping moved into
`agent-registry.sh mandate-flags`, since prose in `kickoff` that `/adopt` reached
by cross-reference is the shape that goes missing.

## The lane is not the cwd

`/cycle` and `/open` may run from the main repo for a PR that belongs to a
task worktree. The has-bot probe was anchored on the repo root for exactly that
case — while the mandate reads beside it still resolved from the cwd, i.e. a
different lane's record or a stale committed one at the main root. Hence
`mandate.sh lane <branch>` (the worktree holding a branch, exit 3 if none) and
the rule that every shim call takes `"$LANE"`. The fallback `|| LANE=.` is the
cwd, which is right exactly when no worktree holds the branch.

## Soft coupling, and what "missing" means

pr-flow reaches work-system through `scripts/mandate-shim.sh`, which shares the
plugin locator (`scripts/lib-work-system.sh`) with `refresh-task-glyphs.sh` —
the same dev/manifest/cache cascade described in
[statusline-integration](statusline-integration.md)'s sibling shims. The two
shims differ deliberately: a missing work-system makes the glyph shim a silent
no-op, but makes the mandate shim answer **3**. A capability question must never
be answered by silence, or an absent plugin reads as a refusal.

## Recommend only what can work

Shipped alongside, and the same class of bug: `/open` and `/cycle` used to send
bot-less repos to `/cycle`, which comments `@claude review` into the void and
polls until timeout. Both now ask `claude-review.sh has-bot` before recommending
or triggering one, and route a repo without one to the local `/swarm:review --pr
<N>` (run when the mandate allows it, offered otherwise) — see
[swarm-review-pipeline](swarm-review-pipeline.md). Reporting a missing capability
beats recommending a command that cannot succeed here.

The probe lives in the adapter, not in skill prose, for two reasons a first
attempt got wrong: a bare `grep -rlie claude .github/workflows/` is **cwd-relative**
(and `/cycle` legitimately runs from a subdirectory, or from the main repo while
the PR belongs to a worktree), and mentioning claude is not the same as reacting
to a comment — `@claude review` needs an `issue_comment` trigger. It answers
`yes`/`no`/**`unknown`**; unknown is its own answer, because a probe that cannot
tell must make the caller ask rather than pick a direction. The second pass
sharpened what "cannot tell" covers: **no workflows dir at all** is `unknown`,
not `no` — a repo served only by the Claude GitHub App looks exactly like that
and does answer `@claude review`, so `no` there silently removed a working
path; `yes` needs both `uses: anthropics/claude-code-action` and an
`issue_comment` trigger, a loose `@claude` mention in a comment workflow is
`unknown` too. The whole probe → answer → local-route tree now lives once in
`plugins/pr-flow/docs/REVIEW-ROUTING.md`, followed by `/open`, `/cycle`,
`/check` and `/rebase`.

## A reader rule is half a rule until the writer knows it

The pass that taught the parser to read keys only at column 0 left the *writer*
matching `review_rounds_used:` at any indentation. So a frontmatter carrying
nested data with that name parsed cleanly, and the first ordinary `round`
rewrote the nested line unindented — two top-level keys, and every later verb
died on "duplicate key". One booking bricked the lane. The same pass also let
`init` write a bare `|` as a value, which its own reader then refused: a record
reported as `written=yes` that nothing could ever read.

Both are the same shape: **a format rule added to one side of a
parse/serialize pair.** Three defenses, in order of value: share the code the
two sides must agree on (one normalization prelude, used by both awk programs),
make the writer refuse what the reader refuses (`check_value` rejects the
block-scalar indicator), and — the one that catches whatever the first two
miss — have the writer **read its work back** and fail loudly if it is not
there. `round` now re-parses after `mv`, because reporting a round the file
never recorded is precisely the failure the persisted counter exists to prevent,
and it happens silently.

## The invariant that the fix itself broke

"One lane, one worker" justified rejecting a lock on `round` four times. It is
false, and `mandate.sh lane <branch>` is why: that resolver exists so a Manager
session running `/cycle` from the main repo can act on a *worker's* worktree. The
worker's own review loop and the Manager's can therefore book a round at the same
instant, both read the same count, and a budget of two funds an unbounded number
of reviews. The lock is eight lines of `mkdir`. Worth re-reading a standing
rejection whenever the surrounding design moves under it — the argument was sound
when first made and quietly stopped being true.

## Indentation is structure too

The same pass that taught `has-bot` to skip block scalars found the mandate
parser reading them: it trimmed leading whitespace off a key name, so a
hand-edited `scope: |` with an indented `allow: merge` under it recorded a grant
the YAML does not make. Top-level keys live at column 0; anything indented
belongs to whatever stands above it. Two parsers, the same bug, found a round
apart — worth remembering that "trim the whitespace and match" is exactly how a
nested value gets promoted to a top-level one.

## "Cannot tell" has to stay cheap for the caller

Making `unknown` the honest answer made it the *common* answer — and the routing
spec said `unknown` means "ask the user". That turned `/cycle --loop`, whose
steps re-run every iteration, into something that stopped and asked every round:
the exact re-confirmation this whole feature exists to remove, reintroduced by a
correctness fix two passes later. The resolution separates the two kinds of
caller. A skill that *triggers* can settle the question empirically — post the
comment, let the bounded poll answer, fall back to the local route on a timeout —
so for it `unknown` means "find out". A recommend-only skill has nothing to run,
so for it `unknown` still means "name both routes". Whenever a tri-state answer
gets more honest, check what the new distribution does to every consumer's
control flow, not just to the truth of the answer.

## A scan proves presence, never absence

`has-bot` answered `no` whenever no workflow mentioned claude. That reads as a
proof of absence, and it is not one: the Claude GitHub **App** answers
`@claude review` with no workflow file at all, so a repo with unrelated CI looks
identical to a bot-less one. This very repo is the counterexample — one
`structure-checks.yml`, no claude workflow, and `claude[bot]` answering PR
comments. Since `no` is the one answer no consumer asks about, that guess
permanently and silently rerouted a working bot, which is the failure
[REVIEW-ROUTING.md](../../../plugins/pr-flow/docs/REVIEW-ROUTING.md) was written
to prevent. `no` now needs positive evidence (a claude workflow that cannot
answer a comment); everything else is `unknown`.

The same probe was also reading the wrong **ref**. GitHub runs an
`issue_comment` workflow from the **default branch**, so scanning the checked-out
task branch answered a question nobody asked — a branch that adds the workflow
probed `yes` and polled into the void, one that removes it probed `no`. Anchoring
on the repo root fixed the *directory*; the ref dimension was never considered
until a reviewer named it.

## Shell variables do not survive a tool call

(And knowing the rule is not the same as applying it. The pass that wrote this
section for `$LANE` introduced a fresh instance of the identical bug one file
over, in `/kickoff`: `WITHOUT=` assigned in step 13a, consumed in 13b's separate
call, silently empty — recording a `local-review` grant for a worker with no such
skill. The durable fix is not a rule to remember but a shape to copy: **the
assignment and its use live in one code block**, always, and a review should grep
for `"$VAR"` in a snippet whose `VAR=` is not visible three lines above it.)

The skills resolved `LANE=` in step 1 and wrote `"$LANE"` in step 7 — a
different Bash call, where the variable is simply unset. It expanded to the empty
string, and both `mandate-shim.sh` and `claude-review.sh` fall back to `.`, so
the whole lane mechanism silently resolved the cwd: exactly the bug `lane` was
added to fix, reintroduced by the prose that consumes it. Anything a skill
computes in one tool call and uses in another has to be recomputed in the second
one. It is now a numbered section (§0) of the routing spec rather than a habit.

Its sibling: the bot-less local route ran `/swarm:review` without calling
`round`, so `review_budget` bounded nothing on that path while the doc explained
at length why the counter must outlive the session. A budget is only a budget
where it is booked.

## What the third pass taught: text is not structure

`has-bot`'s second version matched *text*: `grep -qi issue_comment` said yes to a
TODO comment, and a `uses: anthropics/claude-code-action` inside a `run: |`
heredoc counted as a step. So did an archived copy under
`.github/workflows/old/`, which GitHub never reads. The probe now does one awk
pass per top-level file that skips `#` comments and block scalars before testing
the keys, and it takes two more "cannot tell" answers rather than guess: a
custom `trigger_phrase` (`@claude review` may not fire it) and a job that
delegates to a reusable workflow. The raw *mention* is still tracked — it only
ever lowers a `no` to `unknown`, which is the cheap direction.

On the mandate side the same pass caught the parser trusting its own markers:
the open-fence verdict was a substring match over the whole parsed output, so a
`scope` that happened to contain `__open=1` made a well-formed file unreadable.
A sentinel must live on its own line (or its own channel), never be searched for
in data.

## What the second review pass taught about the first

Round 1's 25 fixes produced 27 new findings in round 2, most of them in code
the fixes had introduced (`has-bot` alone: six). Two lessons worth keeping.
Prose that carries logic drifted *within one review round*, not over months:
the preset allow list "minus local-review" was being retyped by hand, and the
git-exclude recipe was a sub-step `/adopt` reached by cross-reference and could
skip — both moved into `mandate.sh` (`--without`, exclude-in-init), and the
routing tree into a `docs/` spec. And a scoped re-review beats a third full
round: at 139 KiB grok lost two of five clusters; the follow-up over just the
two scripts that held the real defects runs at 33 KiB with every family intact.

## Three bash traps found building this

**An unquoted `$list` of paths is a word-split waiting for a space.** `has-bot`
handed its candidate files to a second grep as `$hits`; a checkout under
"My Projects" split into two non-existent paths and reported a working bot as
absent — permanently, on every run. Paths go NUL-delimited end to end, and
every grep gets `--` before its operand.

**`${VAR:-.}` is not a safe default for a path you are about to source.**
pr-flow's shims fell back to `"${CLAUDE_PLUGIN_ROOT:-.}/scripts/lib-work-system.sh"`,
which means "whatever directory the shell happens to be in" — so any checked-out
repo carrying that path got its code executed, unsandboxed, the moment a shim
ran, while the shim still printed a plausible verdict. A default that silently
widens a lookup into the working directory is worse than no default: resolve
from `$0`/`BASH_SOURCE` and treat "not found" as the documented outcome.

**`die` inside a command substitution kills only the subshell.** `mandate_path()`
printed its result, so a failing `git rev-parse` left the caller with a
truncated `/MANDATE.md` and exit 0 — it would have written to the filesystem
root. The fix is the general one for this shape: resolve into a global
(`resolve_mandate_path`) and call it directly, so `die` can actually exit. Same
family as the print-vs-cache trap in
[swarm-backend-adapter](swarm-backend-adapter.md).
