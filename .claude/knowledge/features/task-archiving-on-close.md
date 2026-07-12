---
title: "Task Archiving on /close"
createdAt: 2026-06-29
updatedAt: 2026-07-12
createdFrom: "PR #19"
updatedFrom: "session: 2026-07-12"
pluginVersion: 1.8.2
prime: false
reindexedAt: 2026-07-12
---

# Task Archiving on /close

`/close` **archives** the finished task file instead of deleting it: it moves
`tasks/<name>.md` into `tasks/archive/<name>.md` with a closed-stamp header and
appends a one-line entry to an append-only `tasks/archive/_index.md` log. Rationale:
`tasks/` is untracked by design (no git history to fall back on), so the old `rm`
left a closed task gone for good. Archiving keeps finished-task context (goal,
acceptance criteria, shipping PR) and turns the closed set into a queryable record.
Companion to [herdr-close-automation](herdr-close-automation.md) (the other half of `/close` cleanup);
the worktree's `TASK.md` copy is deliberately *not* archived — see
[worktree-task-file-copy](../architecture/worktree-task-file-copy.md).

The deterministic logic lives in one helper — `plugins/work-system/scripts/archive-task.sh`
(`archive` subcommand, called from `skills/close/SKILL.md` step 10) — mirroring the
[herdr-close-automation](herdr-close-automation.md) / herdr-teardown.sh split: the script is the source of
truth for stamp format, collision handling, and the index; SKILL.md only branches
on its `key=value` output. This follows the prose-drift convention (stateful skill
logic belongs in a tested script, not SKILL.md prose). See also [skill-composition](../architecture/skill-composition.md).

## Design decisions

- **Adaptive committability, no `.gitignore` surgery.** The archive inherits
  whatever `tasks/` does: the helper reports `committable=yes/no`. Gitignored
  `tasks/` → the archived file is ignored too (local-only); otherwise the move is a
  committable change. The key is named `committable`, not `tracked`, on purpose:
  "not ignored" ≠ "git-tracked", so an untracked-by-omission `tasks/` still reports
  `committable=yes` (the project opts task files into git on first archive). It also
  reports `yes` when the archive path *is* ignored but the **source** file was
  tracked — its removal is a real change that must still be committed, not left
  dangling. This was the central open question — resolved by making behavior
  *follow the project* rather than hardcoding it.
- **All git-stateful work is in `commit-push`, gated by `/close`.** Honoring the
  never-commit-without-approval rule, `archive-task.sh archive` only moves the file
  + appends the index. When `committable=yes`, `/close` asks **once** ("commit and
  push?") and then delegates the entire stage→commit→push to `archive-task.sh
  commit-push` — kept out of SKILL.md prose so the multi-step git logic can't drift
  (the project's prose-drift lesson). Two correctness guards earned in review:
  it commits with an explicit **pathspec** (`git commit -- <archive> <index>
  <removal>`), so unrelated work the user happened to `git add` is never swept into
  the "Archive task" commit and pushed; and a non-zero commit exit is reported as
  `result=commit-failed` (rejecting hook / GPG / locked index), not silently masked
  as `nothing-to-commit`. It also **refuses to commit when the main repo isn't on
  `<main-branch>`** (`main_branch` is just the default branch *name*; the working
  tree may have another branch checked out) — reporting `result=wrong-branch`.
- **Commit + fast-forward push, so `main` never diverges.** Left local+unpushed the
  archive commit would diverge `main` from `origin/main` and break the *next*
  `/close`'s `--ff-only` sync (step 5). So the one approval covers commit **and**
  push: step 5 already ff'd local `main` to `origin/main`, the commit sits one
  commit on top (clean ff), and `commit-push` pushes it (`result=committed-pushed`).
  Critically the push is **scoped to that single commit**: it only pushes when the
  archive commit is the *sole* commit ahead of `origin/<main>` (ahead == 1) — if the
  user has other unpushed commits on `main`, `git push` would publish them too under
  this archive-only approval, so it declines (`reason=unpushed-history`) and leaves
  them. Push failure (protected/offline/origin-moved) or no origin is likewise
  non-fatal — `result=committed-local`, the commit stays put; never a force-push.
  The archive is metadata (a moved markdown file), so a direct ff-push of just that
  one commit to `main` is appropriate and bypasses no meaningful review.
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
