---
title: "Status-Line Integration"
createdAt: 2026-06-18
updatedAt: 2026-07-12
createdFrom: "PR #3"
updatedFrom: "session: 2026-07-12"
pluginVersion: 1.8.2
prime: false
reindexedAt: 2026-07-12
---

# Status-Line Integration

How the `/statusline` skill surfaces knowledge-system info (`[cks rules|knowledge]`)
in the Claude Code status line.

## The design

The status line is owned by the user's own `~/.claude/statusline.sh`. The
`/statusline` skill does **not** replace it — it requires an existing custom
status line and coexists with it:

- **Marker-block injection**: the install step injects a delimited block into
  the user's existing `~/.claude/statusline.sh` rather than overwriting it.
  Re-running is idempotent (replace in place between markers); uninstall removes
  the block. The same install step copies the renderer to
  `~/.claude/cks-statusline.sh` (version-gated via a `CKS_STATUSLINE_VERSION`
  line) and is atomic/restorable (session backup + post-write verify).
- **Standalone renderer**: the installed renderer at `~/.claude/cks-statusline.sh`
  is independently usable — one arg (workspace dir), an ANSI-coloured block on
  stdout, no trailing newline. A third-party status-line tool (ccstatusline,
  CCometixLine, ccusage) can call it directly from its custom-command slot:
  `bash "$HOME/.claude/cks-statusline.sh" "$DIR"`. (Its source lives in the
  plugin at `scripts/statusline-cks.sh`.)
- **Per-project opt-out via sentinel**: a sentinel file disables the segment in
  a given project, so opt-out is local and needs no global config mutation.

## Where the logic lives

All install/enable/disable/uninstall/status logic lives in one deterministic,
locally-testable script — `plugins/knowledge-system/scripts/statusline-install.sh`
— and the skill is a thin wrapper that parses the argument, runs the script, and
relays its output. The script is the source of truth; don't look in the SKILL.md
prose for the behavior.

## Why this matters beyond statusline

The "inject a marked block into a user-owned file you don't control" pattern
recurs — `/init` does the same to `CLAUDE.md`. Idempotent marker blocks are the
plugin's standard mechanism for editing files the user also edits.

Related: [skill-composition](../architecture/skill-composition.md) (shared-script single-source), [ci-structure-checks](../deployment/ci-structure-checks.md).
