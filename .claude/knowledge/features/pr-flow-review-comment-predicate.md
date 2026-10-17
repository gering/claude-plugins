---
title: "pr-flow: one predicate for a Claude review comment"
createdAt: 2026-10-17
updatedAt: 2026-10-17
createdFrom: "branch: task/unify-pr-flow-review-comment-predicate"
updatedFrom: "branch: task/unify-pr-flow-review-comment-predicate"
pluginVersion: 1.9.0
prime: false
---

# pr-flow: one predicate for a Claude review comment

## Decision

"Is this PR comment a Claude review, and is it finished?" is answered in
exactly one place: the `CLAUDE_JQ` jq definitions at the top of
`plugins/pr-flow/scripts/claude-review.sh` (`claude_author`, `claude_review`,
`newest_review`, `newer_pending`, `created_after`). Every consumer goes through
that script — `poll`, `latest`, `latest-after`, `comment-ids`, the `route`
evidence read, and the `/fix`, `/check`, `/merge` and `/cycle` skills, which
call subcommands (`latest --done [--json]`, `comment-ids`) instead of carrying
inline `--jq` filters. "Is a newer run still going?" is the `pending` field of
`latest --done --json`, computed in the same fetch — a skill comparing two
reads in prose raced and false-stopped (swarm round 2 on PR #76). `test_claude_review.py` feeds one comment fixture set to every
consumer and asserts they agree, and fails if any `.md` in the plugin grows its
own author filter or completion marker again.

**Why:** each place used to carry its own filter. Widening only the poll's
author set in a fix round (PR #71) caused 4 of 14 findings in the next review
round and was reverted; the split also hid a user-visible bug (below).

## Gotchas

- **Two author shapes for the same App.** `gh pr view --json comments`
  (GraphQL) reports the Claude App as login `claude` — the `[bot]` suffix is
  stripped. The REST API (`issues/comments`, used by `route`) reports
  `claude[bot]` with `user.type == "Bot"`. `claude_author` accepts both; a
  filter written for one silently matches nothing in the other.
- **"Finished" is not a header.** The `@claude` comment bot writes a
  tracking comment (`Claude Code is working…`) and edits it into
  `**Claude finished …**`. An auto-review workflow (`claude-code-review.yml`)
  posts its review directly as `## Review` / `**Review**`. A poll that waited
  for the `**Claude finished` header timed out every round on such repos while
  `latest-after` returned that very comment. "Done" now means: a Claude
  comment that is non-blank and does not *start* with the working or error
  marker — anchored, so a review that quotes a marker still counts (the swarm
  review of PR #76 caught the unanchored first version). A positive header
  gate was rejected: workflow bodies vary (`## Review`, `**Review**`,
  `Review:`), and the header gate is what caused the timeout.
- **An error is not a review.** A `Claude encountered an error` comment must
  not count as done: `/cycle --loop` would read it as a review with no
  findings and converge clean. It counts as "the bot exists" for route
  memory, never as the review.
- **Workflow-token comments do not count.** A workflow posting with the
  default `GITHUB_TOKEN` comments as `github-actions` — the same account every
  CI job (coverage, lint bots) uses, so accepting it would pass any CI comment
  off as a review.
- **`gh --jq` is gojq**, with no `--arg`/`-L`: the defs are prepended to the
  program text, and `SINCE_ISO` is spliced in only after `require_iso`
  validated its format.

## Known residuals

- `gh pr view` exposes no author type, so a *user* account named `claude`
  (one exists) passes the GraphQL arm of `claude_author`. Pre-existing; closing
  it needs a `gh api graphql` read with `author { __typename }`.
- Sticky comments that a workflow updates in place (`use_sticky_comment`) keep
  their original `createdAt`, so the since-window never sees a new round.
  Detecting them would need the workflow run's conclusion for the head SHA (or
  comment `updatedAt`, which `gh pr view` does not expose) — deliberately not
  built yet.
