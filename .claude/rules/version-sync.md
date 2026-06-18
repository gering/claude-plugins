---
description: Bump plugin version in both plugin.json and marketplace.json together
globs: plugins/**/.claude-plugin/plugin.json,.claude-plugin/marketplace.json
---

# Version Sync

A plugin's version lives in **two** files that must always match:

1. `plugins/<name>/.claude-plugin/plugin.json` — the canonical version
2. `.claude-plugin/marketplace.json` — the marketplace registry entry

When bumping a version, update **both** in the same change. CI
(`scripts/check-structure.py`) fails on version drift, so a mismatch blocks the
PR. SemVer: patch for small fixes, minor for new features.
