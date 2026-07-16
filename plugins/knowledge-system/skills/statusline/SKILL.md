---
name: statusline
description: |
  Manages a `[ks §N ◈M]` status-line block showing `.claude/rules/` and
  `.claude/knowledge/` file counts with dirty-state modifiers.
  Subcommands: install, enable, disable, uninstall, status.
  Trigger: "statusline ks", "show/hide ks", "knowledge status indicator".
user_invocable: true
---

# Knowledge System Status Line Integration

> Append `[ks §RULES ◈KNOW]` to Claude Code's status line, with `*N` (tracked changes) / `+N` (untracked) modifiers when files are dirty.

All install/enable/disable/uninstall/status logic lives in a deterministic, locally-testable script — `scripts/statusline-install.sh`. This skill parses the argument, runs the script, and relays its output. **Do not re-implement the logic here**; the script is the source of truth.

## Arguments

`$ARGUMENTS` — one of `install`, `enable`, `disable`, `uninstall`, `status` (default: `status`). `install` accepts a trailing `--force` to overwrite a pre-existing manual ks block or to force a downgrade of the installed renderer.

## How to run

Pass the arguments straight through:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/statusline-install.sh" $ARGUMENTS
```

- Run it from the user's current project directory so per-project `status`/`enable`/`disable` resolve the right project. The script honours `$CLAUDE_PROJECT_DIR`, falling back to `pwd`.
- The script prints a human-readable result (with any warnings) to stdout; preflight failures and aborts go to stderr with a non-zero exit. **Relay its output to the user** — it already explains what happened and the next step.
- On a non-zero exit, surface the stderr message. Do **not** retry or hand-edit the user's `statusline.sh` — the script refuses to mutate in exactly the cases where mutation would be unsafe.

## Output format

`[ks §12 ◈34]` — one type-glyph per count: `§` = `.claude/rules/**/*.md` count, `◈` = `.claude/knowledge/**/*.md` count (both recursive, excluding `_index.md` / `README.md`). A third `❖` column appears when a project-level `knowledge/_index.md` exists at the repo root (a legacy layout predating `.claude/knowledge`).

Each column may carry `*N` (tracked changes) and `+N` (untracked) suffixes from `git status --porcelain`. When a project has neither `.claude/rules/` nor `.claude/knowledge/`, the renderer emits nothing — no ks block appears in unrelated projects.

## What each subcommand does

- **`status`** (default) — report plugin vs installed renderer version, marker-block presence in the host `statusline.sh`, and this project's disable sentinel; hint the next step.
- **`install`** — copy the renderer to `~/.claude/cks-statusline.sh` (version-gated) and inject a marker block into `~/.claude/statusline.sh`. Requires an existing custom status line; aborts with guidance otherwise. Atomic and restorable (session backup + post-write verify).
- **`enable` / `disable`** — toggle the per-project sentinel `<project>/.claude/.cks-statusline-off`. Does not touch the global install.
- **`uninstall`** — strip the marker block and delete the installed renderer. Leaves per-project sentinels in place.

## Custom placement

Drop a `# {{cks}}` comment into `~/.claude/statusline.sh` exactly where you want the block — it must sit **after** your last `OUT=` assignment, otherwise a later `OUT=` would overwrite the ks output. Then run `install`; the placeholder line is replaced in place. Without a placeholder, `install` falls back to inserting before the last line that prints `$OUT`. `uninstall` strips the block but does not restore the placeholder.

## Other statusline tools

The renderer at `~/.claude/cks-statusline.sh` is independently usable — one arg (workspace dir), an ANSI-coloured block on stdout, no trailing newline. With a third-party statusline tool (ccstatusline, CCometixLine, ccusage), call it directly from the tool's custom-command slot:

```bash
bash "$HOME/.claude/cks-statusline.sh" "$DIR"
```

Still run `install` once to copy the renderer into place (and keep version updates flowing); you can skip the marker-block injection.

## Notes

- Renderer is read-only — never writes to the project. Counts come from `find`; modifier flags from `git status --porcelain`.
- `scripts/statusline-cks.sh` is the single source of truth for renderer behaviour and holds the `readonly CKS_STATUSLINE_VERSION` line that gates upgrades. When changing renderer behaviour, bump that version **and** the plugin `version` in `plugin.json`.
- The install script requires `python3` (symlink resolution + atomic file mutation); it preflights and aborts cleanly if missing.
- `/init` mentions this skill as an optional enhancement.
