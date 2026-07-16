---
name: statusline
description: |
  Manages a `[ws …]` task-backlog segment in Claude Code's status line — task
  counts by state for the repo's tasks/ backlog.
  Trigger: "statusline ws", "task status indicator", "show/hide ws".
user_invocable: true
---

# Work System Status Line Integration

> Append `[ws ○… ●… ◇… ✓…]` to Claude Code's status line — a glanceable count of the current repo's task backlog by state.

All install/enable/disable/uninstall/status logic lives in a deterministic, locally-testable script — `scripts/ws-statusline-install.sh`. This skill parses the argument, runs the script, and relays its output. **Do not re-implement the logic here**; the script is the source of truth.

## Arguments

`$ARGUMENTS` — one of `install`, `enable`, `disable`, `uninstall`, `status` (default: `status`). `install` accepts a trailing `--force` to overwrite a pre-existing manual ws block or to force a downgrade of the installed renderer.

## How to run

Pass the arguments straight through:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/ws-statusline-install.sh" $ARGUMENTS
```

- Run it from the user's current project directory so per-project `status`/`enable`/`disable` resolve the right project. The script honours `$CLAUDE_PROJECT_DIR`, falling back to `pwd`.
- The script prints a human-readable result (with any warnings) to stdout; preflight failures and aborts go to stderr with a non-zero exit. **Relay its output to the user** — it already explains what happened and the next step.
- On a non-zero exit, surface the stderr message. Do **not** retry or hand-edit the user's `statusline.sh` — the script refuses to mutate in exactly the cases where mutation would be unsafe.

## Output format

`[ws ○17 ●3 ◇1 ✓1]` — one single-width glyph per state, each with its count; **zero-count columns are dropped** to stay compact (so a pure backlog reads `[ws ○17]`). Glyphs are muted and coloured so stacked statusline segments stay calm:

- **○ not started** (grey) — a task file with no worktree and no PR.
- **● active** (blue) — the task's `task/<name>` branch has a linked worktree.
- **◇ in review** (amber) — the task's branch has an **open** PR.
- **✓ merged** (green) — the task's branch has a **merged** PR (ready to `/close`).

State precedence is PR → worktree → not-started (a merged/open PR wins over a still-present worktree). When a project has no `tasks/` backlog (or it's empty), the renderer emits nothing — no ws block appears in unrelated projects.

PR state comes from a short-TTL cache (`<git-dir>/ws-statusline-prs`) refreshed by a **detached** background `gh` call — the render itself never blocks on the network, so the first render after a change may show PR columns one refresh late. Without `gh`, the ◇/✓ columns simply don't appear.

## What each subcommand does

- **`status`** (default) — report plugin vs installed renderer version, marker-block presence in the host `statusline.sh`, and this project's disable sentinel; hint the next step.
- **`install`** — copy the renderer to `~/.claude/ws-statusline.sh` (version-gated) and inject a marker block into `~/.claude/statusline.sh`. Requires an existing custom status line; aborts with guidance otherwise. Atomic and restorable (session backup + post-write verify).
- **`enable` / `disable`** — toggle the per-project sentinel `<project>/.claude/.ws-statusline-off`. Does not touch the global install.
- **`uninstall`** — strip the marker block and delete the installed renderer. Leaves per-project sentinels in place.

## Composing with other segments

The ws block owns its own marker pair (`# >>> work-system:ws-statusline >>>`) and never touches another plugin's block — it coexists with the knowledge-system `[ks …]` segment in one `~/.claude/statusline.sh`. Drop a `# {{ws}}` comment where you want the block — it must sit **after** your last `OUT=` assignment, otherwise a later `OUT=` would overwrite the ws output. Then run `install`; the placeholder line is replaced in place. Without a placeholder, `install` falls back to inserting before the last line that prints `$OUT`. `uninstall` strips the block but does not restore the placeholder.

## Other statusline tools

The renderer at `~/.claude/ws-statusline.sh` is independently usable — one arg (workspace dir), an ANSI-coloured block on stdout, no trailing newline. With a third-party statusline tool (ccstatusline, CCometixLine, ccusage), call it directly from the tool's custom-command slot:

```bash
bash "$HOME/.claude/ws-statusline.sh" "$DIR"
```

Still run `install` once to copy the renderer into place (and keep version updates flowing); you can skip the marker-block injection.

## Notes

- Renderer is read-only — never writes to the project (the PR cache lives in `.git/`, outside the working tree). Counts come from `tasks/*.md`, worktree state from `git worktree list`, PR state from the cached `gh pr list`.
- Convention-based: a task binds to its `task/<name>` branch (the `/kickoff` naming). An `/adopt`-renamed branch that kept a non-`task/` name is not matched by the segment — an accepted blind spot for a glance indicator.
- `scripts/ws-statusline.sh` is the single source of truth for renderer behaviour and holds the `readonly WS_STATUSLINE_VERSION` line that gates upgrades. When changing renderer behaviour, bump that version **and** the plugin `version` in `plugin.json`.
- The install script requires `python3` (symlink resolution + atomic file mutation); it preflights and aborts cleanly if missing.
