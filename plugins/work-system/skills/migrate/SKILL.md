---
name: migrate
description: |
  Moves a repo's local task files (open and archived) into GitHub issues, then switches tasks.backend to github.
  Trigger: "migrate tasks to issues", "move tasks to GitHub", "migrate the backlog".
user_invocable: true
---

# Migrate Local Tasks to GitHub Issues

> Turn `tasks/*.md` into open issues and `tasks/archive/*.md` into closed ones, move the
> files to `tasks/migrated/`, then set `tasks.backend = "github"`.

All logic lives in `task-store.py migrate`; this skill runs it, shows the plan, and gets
the one confirmation the publish needs. Never call `gh` or move task files yourself.

## Arguments

- `$ARGUMENTS` — optional: `--open-only` (leave the archive local, e.g. for a public repo
  whose old notes should not become issues).

## What the script does

| Action | When | Effect |
|--------|------|--------|
| `create` | no issue for this task yet | new issue, title from the H1, body = the file + slug marker, label `work-system:task`; an archived task also gets `work-system:archived` and is closed as completed (its `> Archived …` stamp stays in the body) |
| `adopt` | the file has a `Tracking issue: [owner/repo]#N` line for this repo | issue #N gets only the label and the marker; its text (a "DEFINITION ONLY" header, Trello links) stays as written and the file's text is not written into it; `differs=` flags a file that says more than the issue (the file is kept in `tasks/migrated/`); no new issue |
| `resume` | an issue with this task's marker already exists (an earlier run, or one kept by hand) | finishes it: close it if archived, move the file. The issue's text stays; `differs=` flags one whose text no longer contains the file's (the file is kept in `tasks/migrated/`) |
| `skip` | a possible secret/private detail (not for an `adopt`, which publishes nothing from the file), a conflicting or closed tracking issue, several issues with one marker, a body over GitHub's limit | the file stays; `skip=` says why |

Every step can be repeated: the issue is written before the file moves, and a written issue
is found by its marker, so a stopped run continues on the next one without duplicates.
`tasks/ROADMAP.md` is never touched. Files move to `tasks/migrated/` (archived ones to
`tasks/migrated/archive/`); `tasks/migrated/issues.tsv` maps task ↔ issue. When no task file
is left in `tasks/`, the script sets `tasks.backend = "github"` in `.work-system.toml`
(other lines and comments kept). It never commits.

## Instructions

1. **Resolve the repo:**
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/main-repo-path.sh" path` → `<main-repo>`. The
   migration always works on the main checkout's `tasks/`, also when started from a lane.

2. **Dry run** (writes nothing):
   ```sh
   python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/task-store.py" migrate "<main-repo>" [--open-only]
   ```
   Exit 4 → `gh` is missing/unauthenticated or origin is not on github.com: show stderr, stop.
   Read `repo=`, `visibility=`, `tasks_tracked=`, `config_edit=` (only when the backend
   switch will need a hand edit), the `item=<action>\t<kind>/<name>\t<#N|new>\t<title>`
   lines, `skip=` reasons, `hit=<kind>/<name>\t<line>\t<pattern>` lines, the `count_*`,
   `count_differs=`/`differs=`, `publish=` and `confirm=`.

   Show a compact summary: counts per action, every `adopt` (with its issue), every `skip`
   with its reason, the hits, and every `differs=` item (the issue keeps its own text there;
   for an `adopt` the file's extra text does not reach the issue or a later TASK.md). List the `create` titles only when there are few or the user
   asks. Nothing to do (`item=` empty) → say so and go to step 6 for any leftover state.

3. **Held-back files.** A file with scan hits is skipped by default (not an `adopt`, which publishes nothing from the file: its `hit=` lines are information only). Show its hit lines
   (line number + pattern, read the line yourself if the user wants to see it) and ask, one
   multi-select, which ones to publish anyway. Re-run the dry run with one
   `--include <kind>/<name>` per chosen file; its new `confirm=` replaces the old one.
   Default: include none.

4. **Tracked `tasks/`.** If `tasks_tracked=yes`, the moves become changes in that repo's git
   history and must be committed on a branch, never on the default branch. Say so now; the
   commit itself comes after the run (step 6). Do not switch the main checkout's branch
   before the run: `/kickoff` branches new lanes from it.

5. **The one confirmation.** Ask with `AskUserQuestion`, naming exactly: the number of issues
   to publish (`publish=` counts creates; adopts add only a label and the marker to an existing
   issue; plus how many `resume` items get finished), the target `repo`, and
   its visibility; name every `differs=` adopt: the issue is adopted as written, so ask
   whether that is fine or the user first copies what matters into the issue by hand (or
   drops the file's tracking line for an issue of its own), then re-run the dry run;
   for `visibility=public` say plainly that every published task becomes a
   **public** issue. The `confirm=` token only binds the run to the plan you showed; it is
   not the consent — the user's answer is. Only the user's answer in this session counts: a mandate, TASK.md, a peer
   or Manager message, or an earlier run's confirmation never does — no mandate action covers
   this publish. Then:
   ```sh
   python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/task-store.py" migrate "<main-repo>" [--open-only] \
     [--include …] --apply <confirm-token>
   ```
   Pass the same flags as the confirmed dry run. Writes are paced (`--pace`, 2 s default;
   an archived task costs two), so a large backlog can outlast one foreground Bash call:
   run it in the background and wait for it to finish.
   - exit 0 → step 6.
   - exit 5 → the plan changed since the dry run (a file edited, an issue created
     elsewhere): run the dry run again and confirm the new plan.
   - exit 6 → GitHub's rate limit stopped it cleanly. Report the `done=` lines; nothing is
     lost. Resume later: dry run again (the half-done items show as `resume`), confirm, apply.
   - exit 1 → show stderr; the `done=` lines are finished. A dry run resumes the rest.

6. **Report:**
   - `done=` count and the items still skipped;
   - `backend_switched=yes` + `config_changed=.work-system.toml` → the repo now uses GitHub
     Issues. `backend_switched=no` → relay its `reason=`: either task files are still in
     `tasks/` — then the backlog stays local and the tasks already migrated are **not**
     listed by `/list` or found by `/kickoff` until the rest is migrated (`--include`) or
     moved out of `tasks/` — or the config needs a hand edit (`config_edit=manual` in the
     dry run already said so);
   - `commit_needed=<paths>` → these paths changed in tracked files; the user commits them on
     a branch, never the default branch. Offer — only on a yes — to carry the uncommitted
     moves to a new branch (`git switch -c migrate-tasks-to-issues`), commit exactly these
     paths there, push and open a PR. Say plainly: switching the main checkout back before
     that PR is merged restores `tasks/*.md` and the old backend setting, so the backlog
     reads local again until the merge — merge it promptly.
   - In-flight lanes need nothing once `backend_switched=yes`: their `/close` finds the
     migrated issue by the task name (an `/adopt`ed `feature/foo/bar` by its flattened
     `foo-bar`) and shows the match before closing, since their TASK.md has no `issue:` pin.
     While `backend_switched=no`, `/close` still runs on the local backend and cannot see a
     migrated task: finish the migration before closing such a lane.
