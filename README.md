# Claude Code Plugins by gering

A collection of [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugins for enhanced development workflows.

## Plugins

### Knowledge System

Lightweight, native knowledge management for Claude Code projects. Three layers of persistent knowledge: Rules (always active), Knowledge (on demand), and Memory (automatic).

**Commands:** `/init`, `/query`, `/prime`, `/curate`, `/reindex`, `/backfill-knowledge`, `/migrate`, `/statusline`

[Documentation →](plugins/knowledge-system/)

### Work System

Generic task and worktree workflow system. Manage tasks as markdown files, work in isolated git worktrees, and track progress through the full lifecycle. Inside a [herdr](plugins/work-system/README.md#herdr-integration) session, `/kickoff` auto-opens a tab (named after the task, shortened for the sidebar) with the worktree as cwd, starts Claude, and runs `/continue` for you; `/continue <task>` from the main session reopens that tab and resumes it if a stray `/exit` closed it; and `/close` tears the tab down again when the task is merged.

**Commands:** `/define`, `/kickoff`, `/adopt`, `/continue`, `/status`, `/close`, `/list`

[Documentation →](plugins/work-system/)

### PR Flow

PR review feedback loop. Create PRs with readiness checks, commit + push + trigger `@claude` review, inspect status, work through review issues interactively, and merge safely with pre-merge documentation checks.

**Commands:** `/open`, `/cycle`, `/check`, `/fix`, `/rebase`, `/merge`

[Documentation →](plugins/pr-flow/)

### Swarm

Local mixture-of-agents code review. Fans out one review across Claude lens subagents plus the `codex` and `grok` CLIs (grok in `grok-build` + `composer` modes), merges and verifies their findings, and presents a single ranked report — before anything is pushed. Complementary to PR Flow's GitHub-side loop. *(Phase 2: `/swarm:review` pipeline.)*

**Commands:** `/swarm:review`, `/swarm:agents` *(more to come: `/swarm:adversarial`, `/swarm:style`, `/swarm:security`)*

[Documentation →](plugins/swarm/)

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
