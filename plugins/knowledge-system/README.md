# Knowledge System Plugin

Lightweight, native knowledge management for Claude Code projects. Three layers of persistent knowledge: Rules (always active), Knowledge (on demand), and Memory (automatic).

## Commands

| Command | Description |
|---------|-------------|
| `/init` | Scaffold a knowledge system in the current project |
| `/query` | Search the project knowledge base |
| `/curate` | Store new learnings in project knowledge or rules |
| `/migrate` | Migrate from ByteRover to native knowledge system |

## How It Works

### Three Layers

1. **Rules** (`.claude/rules/`) — Always loaded. Short directives for coding style, patterns, dos/don'ts.
2. **Knowledge** (`.claude/knowledge/`) — On demand. Detailed docs about architecture, features, deployment queried via `/query`.
3. **Memory** — Automatic. Claude Code's built-in memory system for preferences and context.

### Getting Started

```
> /init
```

This creates the directory structure and starter files:

```
.claude/knowledge/
  _index.md
  architecture/
  features/
  deployment/
```

### Storing Knowledge

After implementing a feature or fixing a bug, capture what you learned:

```
> /curate "The auth middleware validates JWT tokens before reaching route handlers" src/middleware/auth.ts
```

Claude decides whether it belongs as a rule, knowledge file, or CLAUDE.md entry.

### Querying Knowledge

```
> /query "How does the notification system work?"
```

Claude searches the knowledge index, reads relevant files, and returns a concise answer.

## Installation

Part of the [gering-plugins](https://github.com/gering/claude-plugins) marketplace:

```
/plugin marketplace add gering/claude-plugins
/plugin install knowledge-system
```
