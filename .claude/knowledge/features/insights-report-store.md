---
title: "Insights Report Store (insights.report/v1)"
createdAt: 2026-09-16
updatedAt: 2026-09-21
createdFrom: "branch: task/add-plugin-insights-report"
updatedFrom: "PR #63"
pluginVersion: 1.9.0
prime: false
---

# Insights Report Store (insights.report/v1)

`plugins/insights/scripts/insights.py` is the **only** write/read path for
insights reports: the manual `/insights:report` skill, plus worker/Manager/
close handoff producers through the insights plugin. Producers never write report
files or copy validation — all paths go through insights.py. The contract is in
`plugins/insights/docs/REPORT-CONTRACT.md`. This entry records the *why* behind
decisions a producer or future change could easily undo.

## Decisions and the failures they prevent

- **Project identity = the main checkout.** The ref comes from the first
  `git worktree list` entry, realpath'd, so every worktree of one repo groups
  together while same-named unrelated repos stay distinct. The helper strips
  `GIT_*` env first, because a hook-leaked `GIT_DIR` would describe another repo.
  Nothing is written into the user's repo.
- **Fact fields are strictly one of two shapes:** `{value, source}` or
  `{value: null, reason}`. Mixing them made provenance ambiguous. The honesty
  rules (no model from an alias/tab/author; no installed version as evidence of
  an earlier run; the `Base directory for this skill` line is the
  execution-time version evidence) are producer duties. The validator can only
  enforce the shape.
- **No-overwrite publish via `link()`, not `rename()`.** Write a 0600 temp file,
  fsync it, then hard-link it to `<id>.json`, which fails if the name exists.
  Same ID with identical canonical JSON returns `unchanged` (retry-safe); different
  content exits 3. A mutation test that swapped in `os.replace` was caught by
  three tests.
- **Store dirs are refused, never chmod'ed.** An override (`--store`,
  `INSIGHTS_STORE_DIR`) may point at a directory the user uses for something
  else. Symlinks, foreign owners, and group/other bits exit 4.
- **Drafts travel as files, never through a shell heredoc.** Report text holds
  user input; a line equal to the terminator would run the rest as shell commands
  (review round 1, cross-family consensus). The skill writes the draft with the
  host's Write tool and removes it right after `write`, saved or not.
- **Credentials are redacted, not rejected.** Rejecting forced the model to
  either edit "verbatim" user feedback or fail three times. Redaction runs to a
  fixpoint, because one substitution can unblock another pattern. It runs
  *before* URL sanitizing so stripped credentials still count. It **skips
  identifier fields** (`project.*`, `branch`, `task_name`, IDs): an
  `sk-learn-…` repo path matched the `sk-` token pattern, broke `project.key`,
  and made every write fail. The marker `[REDACTED]` has no `:`/`=` so it never
  re-matches.
- **URLs are parsed with an anchored regex, not `urlsplit`.** `.port` raises on
  a bad port, `.hostname` drops IPv6 brackets, and a `[REDACTED]@host` netloc
  makes `urlsplit` reject the URL entirely.
- **`skeleton` output fails validation until filled.** Undecided fields are
  empty strings or unknowns with an empty reason, so an untouched skeleton can't
  be stored as a report. It embeds the resolved `project`, so a `write` from
  another cwd doesn't re-derive it.
- **Bounded reads:** a single fd with `O_NOFOLLOW|O_NONBLOCK`, fstat, regular
  files only, and at most 256 KiB, so a FIFO in the store can't hang `list`.

## Scope boundary

This entry covers the store and contract only. The lifecycle producers that use
them — work-system's `handoff` and `close` reports — are
[insights-lifecycle-producers](insights-lifecycle-producers.md); they reuse this
helper and never write a report file themselves. Every subcommand here operates
on a report: a `redact` entry point for non-report text existed briefly in 0.1.1
for a `/close` fallback note, and went out with that fallback. Fixtures in
`scripts/fixtures/` are historical examples, not current bug status.
