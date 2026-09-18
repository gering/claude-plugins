# Retrospective guide

How a producer fills `retrospective` and `plugin_details` in an
[`insights.report/v1`](REPORT-CONTRACT.md) report. These are prompts **for the
reporting model to answer from evidence it already has**, not a questionnaire
for the human. The user is never asked to fill fields or to rate anything.

**Default short.** One or two sentences per field. Expand only for an incident
that matters: a lost review, a wrong result, repeated user interruptions, a
blocked lane. An empty list is a valid answer; `[]` beats a padded item.

**Only what was used.** Answer a plugin section only if that plugin's skills
actually ran in the period the report covers. Unused plugins get no key in
`plugin_details` and no questions.

## Core (every report)

| Field | Answer from evidence |
|-------|----------------------|
| `outcome.intended` / `.achieved` | What the work set out to do vs. what exists now. Mid-task, "achieved" is the current state, not a forecast. |
| `difficulty.domain` | How hard the problem itself was, and why. |
| `difficulty.tooling` | How much difficulty the tools, plugins, or harness *added*. Keep it separate from domain difficulty: a hard problem with smooth tooling is `domain: high, tooling: low`. |
| `worked_well` | Practices or tool behavior worth repeating. Concrete, not "things went fine". |
| `friction` | Each item: `expected` vs. `observed`, the `impact`, and a `resolution` (or `unresolved`). A guessed cause goes in `suspected_cause` with a confidence. |
| `interventions` | Extra attempts, manual fixes, restarts, questions the user had to answer. Say why each was needed. |
| `suggestions` | The reporting model's proposals: the change, the observation behind it, the expected benefit, and its uncertainty. `status: "none"` when there is nothing grounded to suggest. |

## Swarm (`plugin_details.swarm`, when `/swarm:review` ran)

Consume the review's own summary or telemetry. Don't recompute coverage and
don't re-run a review to fill gaps.

- `profile`: the review profile, when the run reported one.
- `voices`: planned, started, accepted-result counts. A voice that returned
  **nothing** (blocked, cancelled, schema-invalid) goes in `missing_results`. A
  voice that returned a valid result with zero findings goes in
  `empty_results`. Never merge the two.
- `failures`: each lost voice and its reason. Use `null` when the reason is not
  known; never guess one.
- `restarts`: how often the review was rerun or resumed.
- `findings`: how many were useful vs. rejected, and short rejection reasons.
- `handoff`: what happened next (`--fix` applied N, posted to PR #M).
- `benefit_vs_effort`: one sentence on whether the review paid for its time and cost.

## work-system and Manager coordination (`plugin_details.work-system`)

- `questions`: each question the user had to answer. Include its reason, the
  mandate that was in force (`mandate_source`, `mandate_scope`, e.g. from
  `MANDATE.md`), and whether the answer was **already available**. Classify it:
  - `new_approval`: the answer was not on record (a merge the mandate does not
    grant, scope drift). A legitimate stop.
  - `avoidable_repeat`: the answer was already recorded or given
    (requires `answer_already_available: "yes"`).
  - `clarification`: a genuinely missing requirement.
- `handoff_gaps`: information the next actor needed but did not receive.
- `ambiguous_states`: start or delivery states that could not be confirmed
  at the time (a launch or send that timed out, an `unverified` tab), with the
  eventual resolution if one was observed. **A timeout is not proof of a failed
  dispatch**, and no report authorizes a retry.

## pr-flow (`plugin_details.pr-flow`)

- `review_rounds`: how many review/fix rounds ran.
- `rework_transitions`: where work bounced back (review → fix → re-review), and why.

## knowledge-system (`plugin_details.knowledge-system`)

- `useful_knowledge_found`: `yes`, `no`, `partial`, or `unknown`. Did a `/query`
  or an index entry actually help?
- `stale_or_missing`: knowledge that was outdated or absent when it was needed.
