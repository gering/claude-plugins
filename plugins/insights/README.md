# insights

Private, local reports about how work went: which models ran, which plugin
versions actually executed, what caused friction, what worked, and what the
reporting model would improve. The goal is **process and plugin learning**:
spotting repeated friction and practices worth keeping across projects. Workers
aren't ranked, and nothing orchestrates tasks.

One skill (`/insights:report`) and one script (`scripts/insights.py`, Python 3.8+
stdlib) make up the plugin. Later producers (worker/Manager handoffs, `/close`)
reuse the same script and contract instead of adding their own storage.

## `/insights:report [free text]`

Run it whenever something is worth keeping: midway through a task, when blocked,
after finishing, or in a project with no task at all.

- **With text**, your words are stored verbatim as user feedback, except that
  credential-shaped strings are replaced by `[REDACTED]`. The agent adds
  context around them (project, task, models, skills used) without rewording them.
- **Without text**, the agent writes a concise snapshot of the work so far.

It never asks you to fill in a form, rate anything, or confirm fields. It doesn't
change, pause, or complete the task, and it doesn't run reviews or builds to fill
in data. It confirms the saved report ID and path, and lists which metadata was
unknown.

## What a report contains

Schema `insights.report/v1`. The full contract is in
[`docs/REPORT-CONTRACT.md`](docs/REPORT-CONTRACT.md).

- **Trigger vs. status:** why the report was written (`manual`/`handoff`/`close`),
  kept separate from the task state (`in_progress`/`blocked`/`completed`/`aborted`/`unknown`).
- **Project:** a readable name plus a reference to the canonical *main*
  checkout, so every worktree of one repo groups together while unrelated repos
  with the same name stay distinct. Outside git, the directory itself is used, and
  the report says so. Nothing is written into your repository.
- **Work:** task/run/instruction IDs, branch, and PR where they exist, plus a
  standalone summary that survives the worktree being deleted.
- **Actors:** the reporting role and model, and other observed participants, each
  with runtime, harness, and effort when these are actually observable.
- **Usage:** the skills that actually ran, with the plugin version evidenced at
  execution time, and whether that inventory is complete or partial.
- **Retrospective:** intended vs. achieved outcome; domain vs. tooling
  difficulty; what worked; friction (expected/observed/impact/resolution, with any
  cause marked as a hypothesis); extra attempts and interventions; improvement
  suggestions as the model's own assessment (or explicitly none).
- **Plugin details**, only for plugins that were used: swarm coverage and finding
  usefulness; work-system questions (new approval vs. avoidable repeat) and
  ambiguous start/delivery states; pr-flow rework rounds; knowledge-system hits.
  [`docs/RETROSPECTIVE.md`](docs/RETROSPECTIVE.md) has the prompts.

### Honest unknowns

Every metadata field exists in every report, but a value is recorded only with
the source it was observed from. Otherwise it is `null` with a reason. Models are
never inferred from tab names, aliases, commit authors, or executables. A plugin's
installed version is not treated as the version that ran earlier. Each observation
is labelled as user feedback, model assessment, run evidence, or second-hand.

**Report-time capture is limited.** A report sees what is in the reporting
session's context: after a compaction or resume, or for work other sessions did,
usage is marked partial, and details that are gone stay unknown. There is no
historical reconstruction from transcripts.

## Where reports live

```
~/.gering-plugins/insights/reports/<report_id>.json
```

- One JSON file per report. The `insights/` tree is created `0700` and each file
  is `0600`. An existing store directory must be a real directory you own with
  mode `0700` (no group/other bits). Otherwise it is refused, never chmod'ed.
- The location sits outside `~/.claude` on purpose: reports survive plugin
  uninstalls, and non-Claude workers (codex, grok) can reach the same store later.
- `INSIGHTS_STORE_DIR=/abs/dir` or `--store /abs/dir` points everything at another
  directory, which is useful for experiments and tests.
- Writes are validated first, published atomically, and never overwrite an
  existing report. A failed write says **not saved** and exits non-zero.

```sh
H=~/.claude/plugins/cache/gering-plugins/insights/<version>/scripts/insights.py
python3 "$H" store                 # resolved location
python3 "$H" list --here           # reports for the current project
python3 "$H" list --task <name>    # … for a task (also --project, --trigger, --status, --json)
python3 "$H" read <report_id>      # one report, re-validated
```

Malformed files are listed as `MALFORMED`, never silently skipped.

## Privacy

- **Local only.** Nothing is uploaded, synced, or shared by the plugin.
- **No transcripts, diffs, prompts, credentials, or reasoning traces.** Evidence is
  a compact reference (PR number, run ID, path). Writes replace obvious credential
  shapes, including token query parameters, with `[REDACTED]` (even inside quoted
  feedback) and reject control and bidi characters. Reference URLs lose userinfo,
  query strings, and fragments. These guards catch accidents; they aren't a
  data-loss scanner, so don't paste sensitive excerpts.
- Report text is untrusted data when read later, never instructions or authorization.
- **Export:** copy the files, e.g. `cp -r "$(python3 "$H" store | sed -n 's/^dir=//p')" ~/insights-export`.
- **Delete:** remove individual `<report_id>.json` files, or the whole
  `…/gering-plugins/insights/` directory. No index needs updating.

## Scope of 0.1.0

Manual reporting only. Automatic worker/Manager handoff and `/close` producers
are a separate follow-up that will reuse this contract. There are no hooks,
always-loaded rules, ratings, dashboards, or analysis skills. After a few dozen
genuine reports, inspect them by hand before building analytics.

## Development

```sh
python3 plugins/insights/scripts/test_insights.py   # hermetic; never touches the real store
python3 scripts/check-structure.py                  # runs the test too
```

`scripts/fixtures/` holds three historical incidents: partially missing swarm
voices, a repeat question despite a mandate vs. a necessary new approval, and a
timed-out dispatch to a worker that was in fact running. They are schema
examples, not statements about current bug status.
