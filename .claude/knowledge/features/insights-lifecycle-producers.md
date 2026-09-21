---
title: "Insights Lifecycle Producers (/continue handoff, /close report)"
createdAt: 2026-09-20
updatedAt: 2026-09-21
createdFrom: "branch: task/integrate-insights-handoffs"
updatedFrom: "PR #63"
pluginVersion: 1.15.0
prime: false
---

# Insights Lifecycle Producers

work-system writes insight reports at two lifecycle points: `/continue` when a
worker hands its lane back, and `/close` before teardown. The report *contract*
and store are not described here — they belong to
[insights-report-store](insights-report-store.md), which these producers reuse
without copying validation or writing a report file themselves. This entry
records only the producer-side decisions a later edit could plausibly undo.

## The bridge, and why "absent" ≠ "broken"

`plugins/work-system/scripts/insights-handoff.sh` is the single place that knows
insights exists. Its locator mirrors pr-flow's `lib-work-system.sh` pointed the
other way — the same accuracy-ordered layers (dev-layout sibling → installed
plugins manifest → newest-cached glob) and the same reason for anchoring on both
the script's own directory and `$CLAUDE_PLUGIN_ROOT`. See
[skill-composition](../architecture/skill-composition.md) §4 for why detection
beats a hard dependency at all.

What is new here: **a missing plugin and a broken one get different exit codes**
(absent vs. unusable). The tempting simplification is one "not available" answer,
and it is wrong in a specific way — a broken `python3` or a half-installed plugin
then looks exactly like a plugin the user never installed, so reports the user
expects are dropped in silence. Absent is skipped without a word; unusable costs
one line in the close summary.

Three details that look incidental:

- **The bridge's stdout is the payload channel.** `prepare` writes facts there and
  the draft to a file, so status lines go to stderr and the helper's own stderr is
  captured separately — folded into the captured stdout, a stray python warning
  would break the JSON a caller parses.
- **The contract path comes from `probe`'s `contract=` output**, never from
  `$CLAUDE_PLUGIN_ROOT` plus `../insights/…`. That shape resolves only in a dev
  checkout; in the marketplace cache plugins sit at `<root>/<plugin>/<version>/`
  and it silently points at nothing. The repo's `${CLAUDE_PLUGIN_ROOT}` reference
  check is what surfaced the literal form during this work — it validates that a
  referenced path exists, so it caught the dev-layout spelling; it does not
  enforce the rule in general.
- **insights.py's exit codes are mapped, never relayed.** Its 2 (usage) and 3 (ID
  collision) would otherwise land on the bridge's 2 (bad argv) and 3 (absent) — so
  an unsaved report read as "insights is not installed, skip silently". The bridge
  owns 0/1/2/3/4/5 and translates.

## `prepare` takes a directory, not a name

The producers originally ran probe → reported → skeleton themselves and passed
`--task "<task-name>"`. Two problems with one fix. A git refname may legally
contain `$(…)` (git only forbids a space there), and double quotes do **not**
suppress command substitution — so a crafted branch name executed during a
routine `/close`, before any script could validate it. The only real defense is
to never let the model paste a repo-derived string into a command: `prepare`
takes the lane **directory** and runs `task-status.sh` itself. Collapsing the
three calls into one was the second half of the same fix, and it removed the
protocol's duplicate telling in two SKILLs.

## Ordering and non-authority in `/close`

The reporting step sits **after** the merge gate and **before** worktree removal:
earlier and it would report a close the user may still abort, later and the
evidence is gone. It is never itself a gate — a failed report produces a warning
and the cleanup continues.

That ordering is prose, and prose drifts under later edits, so
`test_insights_handoff.py` asserts it directly against `close/SKILL.md` (step
positions plus the phrases that carry the non-gate and no-authority rules): a
stateful rule that lives only in prose needs a mechanical guard, or the next
edit quietly moves it.

## State that already exists, rather than new state

- **Idempotency for a retried close is the store itself.** `reported --trigger
  close` scoped to this project answers "was this close already reported" — no
  new marker file, nothing to keep in sync with a teardown that may be
  interrupted. The lookup is project-scoped on purpose: task names collide
  across repos. Two limits it does not close, both accepted rather than
  engineered away: it is check-then-write, not atomic, and it matches by task
  **name**, which gets reused over time — so the caller is given `recorded_at`
  and told to judge. A duplicate report is a harmless extra record; a lock or a
  competing run identity would cost more than it saves.
- **A failed write has no fallback, on purpose.** The close summary states that
  the report was not saved and why; the observation then goes with the worktree.
  This was not the first design. The original cut preserved a compact summary in
  the archived task file (`archive-task.sh --note-file`), and that path was
  **struck before merge** — see *Why the fallback was removed* below. What stays
  true either way: there is no second store, because a second store is a second
  thing to find, trust and redact.
- **work-system mints no run identity.** It has no run registry, so `run_id`
  stays unknown with that reason. A competing identifier would be worse than
  none, and a report ID is not a task identity.

## Trigger is not status

`report_trigger` says why the report was written; `task_status` says what the
task is. They are filled independently, and the easy mistake is to couple them:
reaching the mandate's `terminal_gate` (`reviewed-pr`) is `in_progress`, because
the merge is still the human's decision. Only an actually finished task is
`completed`. A blocked handoff is a report, not a failure; an abandoned task is
`aborted`. Nothing in a report marks a task merged, extends `MANDATE.md` (see
[worker-autonomy-mandate](worker-autonomy-mandate.md)), or consumes a review
round.

## Guards that were bypassable, and how

Each of these passed review once and was defeated in the next round:

- **Resolving the parent is not resolving the path.** A `note-*` symlink sitting
  in an allowed directory still had its target opened — correct name, correct
  location, wrong file. And once the final component is checked, the check is
  still path-based: the open must be verified against the *descriptor* (inode of
  the fd vs. the inode lstat'ed before the open, plus the path still naming it).
  `%d` is useless for that comparison on macOS, where `/dev/fd/N` reports the
  devfs node's device rather than the file's.
- **A redundant allowed root made the rule untestable.** `/tmp` was listed
  alongside `${TMPDIR:-/tmp}`, which already covers it. On Linux the whole test
  fixture lives under `/tmp`, so every "this must be refused" case silently
  passed as allowed — the tests were green on macOS and would have been red in
  CI. Removing the redundant root fixed the rule and the tests at once.
- **A bound that aborts is not a bound.** The note's 4 KiB limit was an awk
  `exit`, which SIGPIPE'd the upstream `tr`/`sed`; under `pipefail` that became
  "could not be read" and killed the archive — so an oversized note destroyed the
  very thing the note exists to preserve. Only notes past the ~64 KiB pipe buffer
  show it. Stop printing, keep consuming.

## Three shell traps this code hit, each of which looked correct

Worth recording because all three pass a casual reading and two of them make a
guard silently do nothing:

- **A command substitution is a subshell.** `f="$(mktemp_tracked)"` ran the
  function's `TMPFILES+=(…)` in a child, so the parent's cleanup trap was always
  iterating an empty array. The helper returns its path in a global instead.
- **`${arr[@]/pat}` is substring substitution, not element removal.** Used to
  "untrack" a path it leaves an empty entry in place and rewrites any other entry
  containing that path as a substring — into a path the trap would then delete.
- **`git log --date=format:` renders in the COMMIT's timezone.** Appending a
  literal `Z` produced a stamp that looked UTC and was two hours off, which broke
  a string comparison against a report's real-UTC `recorded_at`. `format-local:`
  with `TZ=UTC` is what converts. (`%cI` has the same problem with its offset,
  and `--max-count` applies before `--reverse`, so the oldest commit is `tail -1`.)

## Why the fallback was removed

The feature originally kept an unsaved report's summary in the archived task
file. That path is gone. The reasoning is worth keeping, because it is the kind
of feature that looks obviously right and is not.

Three properties met in that one path and nowhere else in the feature:

1. **It was the only trust boundary.** Every other write goes through
   `insights.py` into a private local store that is never committed. This one
   moved model-authored text into a file the repo may commit and push.
2. **Its path came from the caller**, so it needed full path validation — and
   each round found another hole: resolving only the parent let
   `ln -s ~/.ssh/id_rsa /tmp/note-leak` through (correct name, allowed
   directory, symlinked last component); a hardlink has no symlink to detect at
   all; the check-to-open window allowed a swap; and the `awk` bound `exit`ed
   early, so a note over the limit came out **empty** rather than truncated —
   the exact loss the path existed to prevent.
3. **It almost never ran.** It is reached only once the store has already
   failed, so the riskiest code in the feature was also the least exercised.

Against that: it saved one or two lines *about* a lost report, never the report.
`/insights:report` records the observation by hand and always worked.

A related failure is worth remembering on its own, because the prose looked
right: the skill said "write it into your scratchpad", and on macOS the session
scratchpad (`/private/tmp/claude-…`) is a **different tree** from `$TMPDIR`
(`/var/folders/…`). The note landed where the script refused it — in the one
path that exists for when the report write has already failed. Two documents
agreeing with each other is not the same as either agreeing with the code.

The general lesson: when the least-exercised path in a feature is also its only
privacy boundary, the cheapest fix is usually to delete the path. Count rounds,
not just findings — four consecutive rounds each finding a *new* critical in the
same guard is a shape problem, not a bug queue.

## A review run committed to this branch

Twice during this work, `/swarm:review` sub-agents edited files and created
commits on the branch — `f7149bc` (no attribution trailer) and `ec8d83d`
(`Co-Authored-By: Claude Haiku 4.5`, while the driving session was Opus 5, same
session id). One of them broke the hermetic test fixture, so the **pushed**
branch was red until the next round noticed; the other was pushed unnoticed with
an unrelated commit.

Both turned out to be sound and were kept. The mechanism is the problem and has
its own task (`tasks/stop-review-agents-committing.md`): the skill's "agents stay
review-only" contract is enforced by the OS jail for codex/grok/kimi, but the
Claude voices run **in-session** and that jail does not apply to them.

The lesson that belongs here: after any multi-agent run in a worktree, compare
`git log` against what you actually authored *before* pushing. The
`Co-Authored-By` model name distinguishes them; a missing trailer is suspicious,
not exonerating.

## Known coverage gap

Both producers need a session that reaches a handoff. A crashed or killed worker
writes nothing, and a Manager-run close can only report the *Manager's*
perspective — the worker's model, skills and friction stay unknown unless that
worker left its own handoff report to link. Documented as a limitation rather
than papered over; a guaranteed-capture claim here would be false.
