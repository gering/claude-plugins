---
title: "Insights Lifecycle Producers (/continue handoff, /close report)"
createdAt: 2026-09-20
updatedAt: 2026-09-20
createdFrom: "branch: task/integrate-insights-handoffs"
updatedFrom: "branch: task/integrate-insights-handoffs"
pluginVersion: 1.9.0
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
  and it silently points at nothing. `check-structure.py` catches the literal
  form, which is how this was found.
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
- **The failed-write fallback is the archived task file**, via
  `archive-task.sh --note-file` (see
  [task-archiving-on-close](task-archiving-on-close.md)). There is deliberately
  **no** second store: a fallback store would be a second thing to find, trust
  and redact. That archive can be committed and pushed, which drove two
  decisions the first cut got wrong. The note is **redacted mechanically**
  (`insights.py redact`, added in 0.1.1 so the patterns are not copied into a
  second plugin) rather than by telling the model not to paste a secret — an
  instruction is not a boundary. And the path is constrained by a purpose-made
  `note-*` **name** as well as a resolved location: the location rule alone is
  near-vacuous on a checkout that lives under `/tmp`, and testing `[ -L ]` on the
  last component says nothing about a symlinked parent.
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

## Known coverage gap

Both producers need a session that reaches a handoff. A crashed or killed worker
writes nothing, and a Manager-run close can only report the *Manager's*
perspective — the worker's model, skills and friction stay unknown unless that
worker left its own handoff report to link. Documented as a limitation rather
than papered over; a guaranteed-capture claim here would be false.
