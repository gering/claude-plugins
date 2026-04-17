# Claude Code Plugins by gering

A collection of [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugins for enhanced development workflows.

## Plugins

### Knowledge System

Lightweight, native knowledge management for Claude Code projects. Three layers of persistent knowledge: Rules (always active), Knowledge (on demand), and Memory (automatic).

**Commands:** `/init`, `/query`, `/curate`, `/reindex`, `/migrate`

[Documentation →](plugins/knowledge-system/)

### Work System

Generic task and worktree workflow system. Manage tasks as markdown files, work in isolated git worktrees, and track progress through the full lifecycle.

**Commands:** `/define`, `/kickoff`, `/adopt`, `/continue`, `/status`, `/close`, `/list`

[Documentation →](plugins/work-system/)

### PR Flow

PR review feedback loop. Create PRs with readiness checks, commit + push + trigger `@claude` review, inspect status, work through review issues interactively, and merge safely with pre-merge documentation checks.

**Commands:** `/open`, `/cycle`, `/check`, `/fix`, `/rebase`, `/merge`

[Documentation →](plugins/pr-flow/)

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

## License

MIT
