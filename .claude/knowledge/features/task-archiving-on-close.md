---
title: "Task Archiving on /close"
createdAt: 2026-06-29
updatedAt: 2026-07-24
createdFrom: "PR #19"
updatedFrom: "session: 2026-07-24"
pluginVersion: 1.10.0
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
  push?") — **unless the repo carries the committed per-repo autocommit opt-in, which
  replaces that per-close ask with a durable authorization (see the 1.10.0 section
  below)** — and then delegates the entire stage→commit→push to `archive-task.sh
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

## Per-repo autocommit opt-in (1.10.0)

- **The commit+push ask is skippable, per repo.** In a repo where
  archiving-to-main is the norm (dotfiles, this repo), the always-yes y/n prompt
  is pure friction — observed repeatedly across close runs. A committed
  `.claude/work-system-close-autocommit` flag (content `yes`/`true`) routes
  `/close` straight to `commit-push`, no `AskUserQuestion`, still reporting the
  result exactly as the manual path does. Mirrors the `.claude/work-system-agent`
  default precedent: a committed per-repo file, not a settings-plugin entry
  (Phase-1, not yet consumed elsewhere) and not local git config (wouldn't travel
  with the repo). `archive-task.sh` grew an `autocommit get|set|unset` subcommand
  as the single source of truth for the flag, mirroring the script-owns-the-logic
  split the rest of this feature already follows.
- **The global-rule framing.** The user's standing rule is "ask before pushing to
  the default branch." This flag is the durable per-repo **authorization** for
  exactly ONE narrow action — the archive-metadata commit+push in step 10 — not a
  general loosening of that rule. What makes it safe to grant is that
  `commit-push`'s existing guards (above) already bound the blast radius: an
  exact pathspec commit (never a blanket `tasks/` add), fast-forward only, never
  a force-push, and a hard refusal (`unpushed-history`) when `main` carries other
  unpushed commits. The opt-in only removes the prompt; it does not weaken any of
  those guards.
- **Off by default, per-repo only.** No global default and no shipped fallback —
  the same shape as the agent-default. A repo that wants this opts in explicitly
  by committing the flag file; a global toggle was explicitly deferred as a
  separate follow-up, not built speculatively.
- **Presence is not authorization; the committed value is (swarm-review).** The
  first cut honored the flag if the file merely *existed* — too weak for a flag
  whose job is waiving an approval gate for a push to the default branch: any
  tool, or a prompt-injected worker agent in a task worktree, can write a file
  with nobody approving a push. So `get` reads the value from the **committed
  object** (`git show HEAD:<rel>`), never from the working tree. That one call is
  both the tracked-check and the value read, and it is immune to the working-tree
  tricks that fool a diff-based guard (`git update-index --assume-unchanged` /
  `--skip-worktree` make `ls-files`/`diff --quiet` report a dirty file as clean —
  round 2 of the review reproduced that bypass against the diff-based version).
  A dirty flag additionally falls back to asking, so a local edit can still
  *disable* the opt-in deliberately. Every unresolved case (not a git repo,
  symlinked flag or `.claude`, unreadable git state) reports `enabled=no`, which
  merely restores the prompt.
- **What the guard is worth — stated honestly, because the docs are the claim.**
  It stops a flag that was merely *written*. It does **not** stop an actor who
  can already commit in your repo: they can commit the flag like any other
  change, and an agent-authored flag merged through a normal PR is indistinguishable
  from a human one. The guard raises the bar from "any file write" to "a commit",
  not to "reviewed by a human". What actually bounds the damage is `commit-push`
  itself — archive-scoped pathspec, fast-forward only, never a force-push, refusal
  on `unpushed-history` — so the worst case is inert archived markdown reaching
  `main` unreviewed. **Accepted residual:** requiring the flag's commit to be an
  ancestor of `origin/<default>` would make "reviewed" true, but it breaks
  origin-less repos, depends on freshly-fetched refs, and is disproportionate to
  that blast radius. Revisit only if the flag ever authorizes more than this.
- **Resolution delegates to `main-repo-path.sh`** — the plugin's one resolver, and
  the same helper that produces the `<main-repo-path>` `/close` passes in. Sharing
  it is the point: an independent second resolver could disagree with `/close`'s,
  so `set` would write a flag `get` never reads. An earlier cut open-coded a
  `--git-common-dir` + `basename == .git` heuristic (copied from
  `agent-registry.sh`) and broke on separate-git-dir/submodule layouts. The
  resolver's answer is then **cross-checked** (`--is-inside-work-tree` +
  `--show-toplevel`): under `git init --separate-git-dir`, `git worktree list`
  reports the *git dir* as the first worktree (verified), and without the check
  `set` cheerfully created `.claude/` inside `.git` — a flag that can never be
  committed, reported as success. Now such a layout is refused with a diagnostic
  naming it. **Caveat to a claim made earlier here:** `agent-registry.sh` still
  uses the old heuristic, so the two per-repo files do *not* share one resolver
  yet — migrating it is follow-up work, deliberately out of this task's scope
  (it sits on the `/kickoff` hot path with its own tests).
- **Sharp edges the script owns** (the accepted-token list, `reason=` codes and
  their meanings live in `archive-task.sh`'s header — do not restate them here or
  in the README; that vocabulary drifted across five surfaces once already):
  - Trim, *not* `tr -d '[:space:]'` — deleting all whitespace collapses `y e s`
    into an accepted token, waiving the gate on undocumented content.
  - `set`/`unset` refuse a symlinked flag **or `.claude` parent**, and re-validate
    after `mkdir -p` — guarding only the leaf let a symlinked `.claude` make
    `mkdir` succeed through the link and land the write outside the repo while the
    reported path still read in-repo. Residual: a shell can't do this race-free
    (no `openat`/`O_NOFOLLOW`); the re-checks narrow the window, they don't close it.
  - `set` writes a `mktemp`'d file **in the target directory**, `chmod`s it *before*
    the rename, and renames. A fixed `$file.tmp.$$` is PID-predictable (pre-plant
    it as a symlink → the redirect clobbers an arbitrary file and `mv` installs the
    symlink *as* the flag), and a post-rename `chmod` can follow a swapped-in link.
  - `set` warns when `.claude/` is **gitignored** — otherwise `git add` refuses the
    path and `get` reports `untracked` forever while `set` claims success.
  - `unset` reminds you to commit the *deletion*: removing the file makes `get`
    report disabled at once, but HEAD still carries the flag for every other clone.
  - The dirty-check compares **blob hashes**, not `git diff --quiet`, so the index
    bits (`--assume-unchanged`/`--skip-worktree`) can't hide a deliberate local
    disable either.
  - The verdict is read from **`refs/heads/<main-branch>`**, not `HEAD` and not the
    bare branch name. Reading HEAD made the answer depend on which branch the main
    checkout sat on; reading the BARE name was worse — git resolves `refs/tags/`
    before `refs/heads/`, so a tag named like the default branch (tags auto-follow
    on fetch) supplied an authorization value the branch never carried. Verified.
    `get` also declines when the checkout is not on that branch, since
    `commit_push` would refuse anyway, and fails **closed** when the branch cannot
    be resolved rather than falling back to HEAD.
  - `commit_push` passes `:(literal)` pathspecs: a task named `x*` would otherwise
    glob `tasks/archive/x*.md` and commit every matching archive. **Not** for
    `check-ignore`, which rejects pathspec magic outright — prefixing it there
    silently broke the gitignored-archive detection (caught by a smoke test, not
    the suite).
  - A repo that never opted in must answer a **bare** `enabled=no`: callers relay
    any `note=` verbatim, so ordering the symlink check before the existence check
    made every close in a symlinked-`.claude` repo warn about an opt-in nobody
    configured. "Nothing configured" means nothing here **and** nothing on the
    authorization ref — a flag deleted locally but still committed there is
    `locally-deleted`, since every other clone keeps auto-committing until the
    deletion is committed.
  - `git hash-object <path>` applies that path's gitattributes/clean filters by
    default (that is what `--no-filters` disables), so the blob-hash dirty-check
    stays correct under `core.autocrlf`/`eol=crlf` — checked directly after a
    review round claimed otherwise. Verify such claims against git rather than
    taking them on trust; the reviewer was wrong here.
  - Sibling helpers are addressed via an absolute `SCRIPT_DIR` captured at load:
    a `$(dirname "$0")` expanded inside `( cd "$repo" && … )` resolves against the
    *target* repo when `$0` is relative — and runs whatever sits there.
- **`get` ships the human sentence with the machine code.** Alongside `reason=` it
  emits a ready-to-print `note=`, and callers relay that instead of re-spelling the
  vocabulary — the prose-drift failure this project already recorded.
