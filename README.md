# Claude Plugins by gering

A collection of [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugins for enhanced development workflows.

## Plugins

| Plugin | Description |
|--------|-------------|
| [knowledge-system](https://github.com/gering/claude-knowledge-system) | Lightweight, native knowledge management for Claude Code projects |
| [work-system](https://github.com/gering/claude-work-system) | Task and worktree workflow system for parallel development |

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
/plugin marketplace update claude-plugins
/reload-plugins
```

## License

MIT
