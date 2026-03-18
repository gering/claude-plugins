# Claude Code Plugins by gering

A collection of [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugins for enhanced development workflows.

## Plugins

### Knowledge System

Lightweight, native knowledge management for Claude Code projects. Three layers of persistent knowledge: Rules (always active), Knowledge (on demand), and Memory (automatic).

**Commands:** `/init`, `/query`, `/curate`, `/migrate`

[Documentation →](plugins/knowledge-system/)

### Work System

Generic task and worktree workflow system. Manage tasks as markdown files, work in isolated git worktrees, and track progress through the full lifecycle.

**Commands:** `/work-create`, `/work-start`, `/work-continue`, `/work-check`, `/work-close`, `/work-list`

[Documentation →](plugins/work-system/)

## Installation

### 1. Add the marketplace

```
/plugin marketplace add gering/claude-plugins
```

### 2. Install plugins

```
/plugin install knowledge-system
/plugin install work-system
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
