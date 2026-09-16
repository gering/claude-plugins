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
verbatim; without text, the report is an agent-authored snapshot.

## Boundaries

- **Record only.** Don't complete, pause, restart, or edit the task; don't
  commit; don't touch `TASK.md` or `MANDATE.md`.
- **No questionnaire.** Don't ask the user anything. Fill fields from evidence
  already in context; leave the rest explicitly unknown.
- **No extra cost.** Don't run reviews, builds, tests, or other model calls
  just to populate fields, and don't read transcript files.

## Instructions

1. **Mode.** `$ARGUMENTS` non-empty → **feedback report**: store the text
   verbatim as `user_feedback[0]` (`attribution: "user"`,
   `captured_via: "/insights:report argument"`), then add context around it
   without changing its meaning. Empty → **snapshot report**: write a concise
   agent-authored report of the work so far.

2. **Collect observable facts** (project identity, branch, task hints, runtime
   env, store):
   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/insights.py" context
   ```

3. **Read the contract** `${CLAUDE_PLUGIN_ROOT}/docs/REPORT-CONTRACT.md` for
   the schema and provenance rules. Read the matching section of
   `${CLAUDE_PLUGIN_ROOT}/docs/RETROSPECTIVE.md` only for plugins whose skills
   actually ran.

4. **Draft the report JSON.** Write agent-authored text in English; keep user
   feedback in its original language. Sources for the harder fields:
   - `reporter.model`: your exact model ID from your system prompt
     (`source: "system prompt"`). `reporter.runtime`, `reasoning_effort`, and
     `session_id`: from `context.runtime`, with the env variable as the source.
     `reporter.role`: `worker` in a task worktree lane, `manager` at a repo root
     coordinating lanes, `advisor` when advising without owning the work, `user`
     when you only transcribe the user's own report, `unknown` otherwise.
   - `usage.skills`: every skill actually invoked in this conversation. Take its
     version from the `Base directory for this skill: …/<plugin>/<version>/…`
     line shown at invocation (`source: "skill base directory at invocation"`).
     Never use the currently installed or checked-out version as evidence.
     `completeness` is `partial` if the context was compacted or resumed, or if
     other sessions did part of the work.
   - `work`: the task name from `context.task_hints` (`mandate_task`,
     `task_title`) or the branch; a PR only if it is already known in context.
     `summary` must stand alone once the worktree and task file are gone.
   - `report_trigger`: `manual`. `task_status`: from what you observe
     (`in_progress` mid-task, `blocked` when waiting on something outside the
     lane, `unknown` with no task).
   - Everything unavailable: `{"value": null, "reason": "…"}`.

5. **Write it** through the helper (quoted heredoc: no shell expansion inside):
   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/insights.py" write - <<'INSIGHTS_REPORT_EOF'
   { …report JSON… }
   INSIGHTS_REPORT_EOF
   ```
   - Exit **1** (invalid, nothing saved): fix the fields stderr names and write
     again, at most twice.
   - Exit **3** or **4**, or a third validation failure: say the report was
     **not** saved, show the helper's error, and stop. Never describe an unsaved
     report as recorded.

6. **Confirm** in a few lines:
   ```
   Insights report saved: <report_id>
   Path: <path>
   Unknown metadata (<n>): <field: reason; …>   (group repetitive gaps, e.g. "model of 3 participants")
   ```
   For a feedback report, add one line on what context was attached to it.

## Notes

- Correcting or extending a saved report means writing a new report that lists
  the old ID in `work.related_reports`. Stored reports are never modified.
- Finding reports again: `insights.py list --here`, `insights.py read <id>`.
  See the plugin README for location, export, and deletion.
