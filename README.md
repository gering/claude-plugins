# Claude Code Plugins by gering

A collection of [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugins for enhanced development workflows.

## Plugins

### Knowledge System

Lightweight, native knowledge management for Claude Code projects. Three layers of persistent knowledge: Rules (always active), Knowledge (on demand), and Memory (automatic).

**Commands:** `/init`, `/query`, `/prime`, `/curate`, `/reindex`, `/backfill-knowledge`, `/migrate`, `/statusline`

[Documentation →](plugins/knowledge-system/)

### Work System

Generic task and worktree workflow system. Manage tasks as markdown files, work in isolated git worktrees, and track progress through the full lifecycle. `/kickoff` runs the repo's default worker agent (Claude, codex, or grok — a single committed per-project default), or, when none is set, shows a picker and offers to save your choice; override per run with flags like `--opus`/`--sol` or `--pick`. Inside a [herdr](plugins/work-system/README.md#herdr-integration) session it auto-opens a tab (named after the task, shortened for the sidebar and prefixed with the task's state glyph — `●` active, `◇` in review, `◆` approved, `✓` merged) with the worktree as cwd, starts the chosen worker, and — for a Claude worker — runs `/work-system:continue` for you (plugin-qualified, since a Claude Code built-in `/continue` shadows the bare skill); `/work-system:continue <task>` from the main session reopens that tab and resumes it if a stray `/exit` closed it; and `/close` tears the tab down again when the task is merged.

**Commands:** `/define`, `/kickoff`, `/adopt`, `/continue`, `/status`, `/close`, `/list`, `/statusline`

[Documentation →](plugins/work-system/)

### PR Flow

PR review feedback loop. Create PRs with readiness checks, commit + push + trigger `@claude` review, inspect status, work through review issues interactively, and merge safely with pre-merge documentation checks.

**Commands:** `/open`, `/cycle`, `/check`, `/fix`, `/rebase`, `/merge`

[Documentation →](plugins/pr-flow/)

### Swarm

Local mixture-of-agents code review. Fans out one review across Claude lens subagents (11 lenses in 4 clusters — breakage, threat, design, consistency) plus the `codex` and `grok` CLIs (grok-4.5), merges and verifies their findings, and presents a single ranked report — defects and design suggestions kept apart — before anything is pushed. With `--fix` / `--loop` it also applies the findings you agreed with (only Claude edits; the external agents stay review-only). `--pr [<number>]` runs the same ensemble against a GitHub PR's diff and posts the gated result as a PR comment (via your own `gh` auth, after one confirmation) — no CI or API-token setup. Complementary to PR Flow's GitHub-side loop. *(Phase 5: `/swarm:review` pipeline + fix loop + PR posting.)*

**Commands:** `/swarm:review [--fix | --loop[=N]] [--max]`, `/swarm:review --pr [<number>]`, `/swarm:agents` *(planned: `/swarm:adversarial`, `/swarm:style`, `/swarm:security` — thin subset presets of the default lens set)*

[Documentation →](plugins/swarm/)

### Settings

Plugin settings system: per-plugin TOML config resolved over schema defaults. Each plugin owns its config file (`.work-system.toml`, `.knowledge-system.toml`, `.pr-flow.toml`), defaults, and validation schema; users override only what they need. `list`, `show`, `get`, `set`, `validate` via one script and skill. Includes a `[related_projects]` sibling-project address book for cross-project orchestration. *(Phase 1: config surface only — consumer wiring lands next.)*

**Commands:** `/settings` *(subcommands: `list`, `show`, `get`, `set`, `unset`, `validate`, `defaults`)*

[Documentation →](plugins/settings/)

## Installation

### 1. Add the marketplace

```
/plugin marketplace add gering/claude-plugins
```

### 2. Install plugins

```
/plugin install knowledge-system
/plugin install work-system
/plugin install pr-flow
/plugin install swarm
/plugin install settings
```

### 3. Reload plugins

```
/reload-plugins
```

## Updates

Plugins update automatically when the marketplace is refreshed. To manually update:

```
/plugin marketplace update gering-plugins
/reload-plugins
```

## Development

Structure invariants (JSON validity, version sync, skill frontmatter, internal
references, shell syntax) are enforced by CI on every PR. Run the same checks
locally before pushing:

```
python3 scripts/check-structure.py
```

## License

MIT
