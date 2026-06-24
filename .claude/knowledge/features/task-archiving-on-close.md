---
title: "Task Archiving on /close"
createdAt: 2026-06-24
updatedAt: 2026-06-24
createdFrom: "branch: task/archive-tasks-on-close"
updatedFrom: "branch: task/archive-tasks-on-close"
pluginVersion: 1.8.2
prime: false
---

# Task Archiving on /close

`/close` **archives** the finished task file instead of deleting it: it moves
`tasks/<name>.md` into `tasks/archive/<name>.md` with a closed-stamp header and
appends a one-line entry to an append-only `tasks/archive/_index.md` log. Rationale:
`tasks/` is untracked by design (no git history to fall back on), so the old `rm`
left a closed task gone for good. Archiving keeps finished-task context (goal,
acceptance criteria, shipping PR) and turns the closed set into a queryable record.
Companion to [[herdr-close-automation]] (the other half of `/close` cleanup).

The deterministic logic lives in one helper — `plugins/work-system/scripts/archive-task.sh`
(`archive` subcommand, called from `skills/close/SKILL.md` step 10) — mirroring the
[[herdr-close-automation]] / herdr-teardown.sh split: the script is the source of
truth for stamp format, collision handling, and the index; SKILL.md only branches
on its `key=value` output. This follows the prose-drift convention (stateful skill
logic belongs in a tested script, not SKILL.md prose). See also [[skill-composition]].

## Design decisions

- **Adaptive tracking, no `.gitignore` surgery.** The archive inherits whatever
  `tasks/` does: the helper checks `git check-ignore` on the archive path and
  reports `tracked=yes/no`. Gitignored `tasks/` → the archived file is ignored too
  (local-only); a tracked/committable `tasks/` → the move is a committable change.
  This was the central open question — resolved by making behavior *follow the
  project* rather than hardcoding committed-vs-local.
- **The script never commits; `/close` asks first.** Honoring the
  never-commit-without-approval rule, `archive-task.sh` only does the filesystem
  move + index append and reports committability. When `tracked=yes`, `/close`
  prompts, then stages **only** `tasks/archive/` plus the one removed file — never a
  blanket `git add tasks/`, which would sweep in unrelated *pending* task files.
  Staging the removal uses `git add -A -- "tasks/<name>.md" 2>/dev/null || true` so
  it's a no-op (not an error) when the original was untracked-by-omission.
- **Never clobber on a name collision.** A re-close of the same task name suffixes
  the archived file `-2`, `-3`, … rather than overwriting a prior archive; a fresh
  `_index.md` line is appended either way, so every close is recorded.
- **Form: both file + log.** The full task file is preserved (move + stamp) *and* a
  condensed `_index.md` line gives a scannable overview — chosen over either alone.
- **Stamp carries merge provenance.** Date · PR + short merge SHA · branch for a
  merged close (the SHA fetched best-effort via `gh pr view … mergeCommit`), or
  "closed manually (no merged PR)" otherwise. Exact format lives in the script.

`/list` surfaces an archived count in its summary; the pending glob stays the
non-recursive `tasks/*.md`, which already excludes `tasks/archive/`. The "never
persistent `cd`" footgun the helper's explicit paths avoid is a rule — see
`.claude/rules/cwd-safety.md`.
