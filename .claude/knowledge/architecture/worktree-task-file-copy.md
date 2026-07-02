---
title: "Worktree task file: copy, not symlink"
createdAt: 2026-07-02
updatedAt: 2026-07-02
createdFrom: "session: 2026-07-02"
updatedFrom: "session: 2026-07-02"
pluginVersion: 1.8.2
prime: false
---

# Worktree task file: copy, not symlink

`/kickoff` copies `tasks/<name>.md` into the new worktree as `TASK.md` (a plain
`cp`). A recurring question is whether a symlink would be smarter and simplify the
other skills. **Decision: keep the copy.** Rationale, so it doesn't get relitigated:

## Why the copy exists
A fresh worktree checkout of `task/<name>` does **not** contain `tasks/<name>.md`
when `tasks/` is untracked (as in this repo) — untracked files don't propagate to a
new worktree. `kickoff` copies it in so `/continue` has local context at a stable
path. The canonical file stays `<main-repo>/tasks/<name>.md`; that's what `/close`
archives (see `plugins/work-system/skills/close/SKILL.md`, "Archive the task file"),
never the worktree copy.

## A symlink does not simplify the other skills
- `/continue` still needs its "read `TASK.md`, else fall back to the main-repo task
  file" branch for robustness (broken link, `/adopt`'d worktree with no kickoff).
- `/close` treats `TASK.md` as an expected untracked file (`--force` on worktree
  remove). A symlink is *also* untracked → same handling, no simplification.

Both skills already treat the main-repo file as the single source of truth, so
there is nothing to simplify.

## Why the copy wins on the merits
- The symlink's only real benefit — edits to `TASK.md` flowing back to the archived
  file — **doesn't apply**: no skill writes to `TASK.md` (`/continue` builds its
  todo list in-session, not into the file). Purely speculative for manual edits.
- **Accidental commit poisons the repo:** in a project where `TASK.md` isn't
  gitignored, a `git add .` commits it. A copy = harmless markdown duplicate; a
  symlink = git stores the *link target* as blob content (a relative `../../../…`
  or host-absolute path pointing outside the tree), broken in every clone after
  merge.
- **Cross-platform:** work-system is a generic plugin; `ln -s` + git `core.symlinks`
  are off/restricted on Windows by default. `cp` runs everywhere.
- **Surprising tree coupling:** with tracked `tasks/`, a symlinked edit in the
  worktree would dirty the *main branch* working tree "out of nowhere".

If the "living task doc" property is ever wanted, the targeted fix is to fold
worktree `TASK.md` edits into the archive at `/close` time (before worktree remove)
— not a symlink.
