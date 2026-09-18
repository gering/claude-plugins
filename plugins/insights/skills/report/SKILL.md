---
name: report
description: |
  Records a private local insight report on the current work: models, plugin versions, friction, improvement ideas.
  Trigger: "insights report", "record this friction", "report plugin feedback".
user_invocable: true
---

# Insights Report

> Save one report about the work so far: mid-task, blocked, finished, or with no
> task at all. The report stays on this machine. Recording it changes nothing
> about the task.

## Usage
`/insights:report [free-text feedback]`. With text, the user's words are stored
verbatim (apart from the helper's credential redaction); without text, the report
is an agent-authored snapshot.

## Boundaries

- **Record only.** Don't complete, pause, restart, or edit the task; don't
  commit; don't touch `TASK.md` or `MANDATE.md`.
- **No questionnaire.** Don't ask the user anything. Fill fields from evidence
  already in context; leave the rest explicitly unknown.
- **No extra cost.** Don't run reviews, builds, tests, or other model calls
  just to populate fields, and don't read transcript files.

## Instructions

1. **Mode.** `$ARGUMENTS` non-empty → **feedback report**: store the text
   verbatim (the helper redacts credentials itself) as `user_feedback[0]` (`attribution: "user"`,
   `captured_via: "/insights:report argument"`), then add context around it
   without changing its meaning. Empty → **snapshot report**: write a concise
   agent-authored report of the work so far.

2. **Get a skeleton draft.** Every required field is present; values the helper
   can observe (branch, task name, runtime, effort, session) are prefilled with
   their source. The rest is empty and fails validation by name until you fill it:
   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/insights.py" skeleton
   ```

3. **Read the contract** `${CLAUDE_PLUGIN_ROOT}/docs/REPORT-CONTRACT.md` for
   field meanings and provenance rules. Read the matching section of
   `${CLAUDE_PLUGIN_ROOT}/docs/RETROSPECTIVE.md` only for plugins whose skills
   actually ran.

4. **Fill the draft.** Write agent-authored text in English; keep user
   feedback in its original language. Every unknown is `{"value": null,
   "reason": "…"}` (no `source`); every known value is `{"value": …, "source":
   "…"}` (no `reason`). Sources for the harder fields:
   - `reporter.model`: your exact model ID from your system prompt
     (`source: "system prompt"`). `reporter.role`: `worker` in a task worktree
     lane, `manager` at a repo root coordinating lanes, `advisor` when advising
     without owning the work, `user` when you only transcribe the user's own
     report, `unknown` otherwise.
   - `usage.skills`: every skill actually invoked in this conversation. Take its
     version from the `Base directory for this skill: …/<plugin>/<version>/…`
     line shown at invocation (`source: "skill base directory at invocation"`).
     Never use the currently installed or checked-out version as evidence.
     `completeness` is `partial` if the context was compacted or resumed, or if
     other sessions did part of the work.
   - `work`: if the skeleton left `task_name` unknown, a task name may still be
     evident from `TASK.md` or the conversation (give that as its source). A PR
     only if it is already known in context. `summary` must stand
     alone once the worktree and task file are gone.
   - `task_status`: from what you observe (`in_progress` mid-task, `blocked` when
     waiting on something outside the lane, `unknown` with no task).

5. **Write it.** Save the finished JSON with the **Write tool** as a new file in
   your scratchpad directory (or `$TMPDIR`), e.g. `insights-draft-<random>.json`.
   Never pass it through a shell heredoc or `echo`: report text contains user
   input, and a shell would treat a stray terminator line as commands. Then:
   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/insights.py" write '<draft file>'
   ```
   - **Always** remove the draft file right after this call, saved or not
     (`rm '<draft file>'`): it holds the unredacted text.
   - Exit **1** (invalid, nothing saved): fix the fields stderr names, save the
     draft again, and retry, at most twice (removing the draft each time).
   - Exit **3** or **4**, or a third validation failure: say the report was
     **not** saved, show the helper's error, and stop. Never describe an unsaved
     report as recorded.

6. **Confirm** in a few lines:
   ```
   Insights report saved: <report_id>
   Path: <path>
   Unknown metadata (<n>): <field: reason; …>   (group repetitive gaps, e.g. "model of 3 participants")
   ```
   If `redactions` is above 0, say that many credential-shaped strings were
   replaced by `[REDACTED]`. For a feedback report, add one line on what context
   was attached to it.

## Notes

- Correcting or extending a saved report means writing a new report that lists
  the old ID in `work.related_reports`. Stored reports are never modified.
- Finding reports again: `insights.py list --here`, `insights.py read <id>`.
  See the plugin README for location, export, and deletion.
