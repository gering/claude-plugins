---
title: "Status-Line Integration"
createdAt: 2026-06-18
updatedAt: 2026-06-18
createdFrom: "PR #3"
updatedFrom: "PR #3"
pluginVersion: 1.7.0
prime: false
---

# Status-Line Integration

How the `/statusline` skill surfaces knowledge-system info (`[cks rules|knowledge]`)
in the Claude Code status line, and the constraint that shapes its design.

## The constraint

A Claude Code plugin **cannot own** the `statusLine.command` setting. That entry
point can be claimed only once, and it is typically already held by the user's
own `~/.claude/statusline.sh`. So the skill cannot just install its own command —
it has to coexist with whatever is already there.

## The pattern

Two-layer design that threads between full-ownership and manual-wrapper:

- **Marker-block injection**: the skill injects a delimited block into the
  user's existing `statusline.sh` rather than replacing it. Re-running is
  idempotent (replace in place between markers); uninstall removes the block.
- **Standalone renderer** (`statusline-cks.sh`): a separate script that renders
  just the `[cks …]` segment. Other tools can call it directly, without the
  marker-block wrapper — the rendering logic is decoupled from the injection
  mechanism.
- **Per-project opt-out via sentinel**: a `.cks-statusline-off` file in a
  project disables the segment there. A filesystem sentinel needs no global
  config mutation — opt-out is local and self-cleaning.

## Why this matters beyond statusline

The "inject a marked block into a user-owned file you don't control" pattern
recurs — `/init` does the same to `CLAUDE.md`. Idempotent marker blocks are the
plugin's standard mechanism for editing files the user also edits.

Related: [[skill-composition]] (shared-script single-source), [[ci-structure-checks]].
