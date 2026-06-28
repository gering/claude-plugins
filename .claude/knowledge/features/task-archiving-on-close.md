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

- **Adaptive committability, no `.gitignore` surgery.** The archive inherits
  whatever `tasks/` does: the helper checks `git check-ignore` on the archive path
  and reports `committable=yes/no`. Gitignored `tasks/` → the archived file is
  ignored too (local-only); otherwise the move is a committable change. The key is
  named `committable`, not `tracked`, on purpose: "not ignored" ≠ "git-tracked", so
  an untracked-by-omission `tasks/` still reports `committable=yes` (the project
  opts task files into git on first archive). This was the central open question —
  resolved by making behavior *follow the project* rather than hardcoding it.
- **The script never commits; `/close` asks first.** Honoring the
  never-commit-without-approval rule, `archive-task.sh archive` only does the
  filesystem move + index append. When `committable=yes`, `/close` prompts, then
  calls `archive-task.sh stage` — which stages **only** the new file, `_index.md`,
  and the original's removal (a `git add -A` no-op for an untracked-by-omission
  original), never a blanket `git add tasks/` that would sweep in unrelated
  *pending* task files. Keeping the staging in the tested `stage` subcommand (not
  SKILL.md prose) holds the precise-scoping rule where it can't drift; the commit
  itself, which needs approval, stays in `/close`.
- **Commit + fast-forward push, so `main` never diverges.** The archive commit
  lands on the main repo's `main`; left local+unpushed it would diverge from
  `origin/main` and break the *next* `/close`'s `--ff-only` sync (step 5). So the
  single approval covers commit **and** push: step 5 already ff'd local `main` to
  `origin/main`, the archive commit sits one commit on top (clean ff), and `/close`
  pushes it. Push failure (protected/offline/pre-existing divergence) is non-fatal —
  the commit stays local with a "push when ready" note; never a force-push. The
  archive is metadata (a moved markdown file), so a direct ff-push to `main` is
  appropriate and bypasses no meaningful review.
- **Never clobber on a name collision.** A re-close of the same task name suffixes
  the archived file `-2`, `-3`, … rather than overwriting a prior archive; a fresh
  `_index.md` line is appended either way, so every close is recorded.
- **Form: both file + log.** The full task file is preserved (move + stamp) *and* a
  condensed `_index.md` line gives a scannable overview — chosen over either alone.
- **Stamp carries merge provenance.** Date · PR + short merge SHA · branch for a
  merged close, or "closed manually (no merged PR)" otherwise. The SHA is fetched
  best-effort via a *separate* `gh pr view … mergeCommit` — deliberately not folded
  into `task-status.sh assess`'s PR lookup, so an older `gh` lacking the
  `mergeCommit` json field only loses the cosmetic SHA, never the safety-critical
  `pr_state` merge gate. Exact format lives in the script.

`/list` surfaces an archived count in its summary; the pending glob stays the
non-recursive `tasks/*.md`, which already excludes `tasks/archive/`. The "never
persistent `cd`" footgun the helper's explicit paths avoid is a rule — see
`.claude/rules/cwd-safety.md`.
