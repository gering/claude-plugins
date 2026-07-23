---
title: "Plugin settings system"
createdAt: 2026-07-23
updatedAt: 2026-07-23
createdFrom: "PR #40"
updatedFrom: "PR #40"
pluginVersion: 1.9.0
prime: true
---

# Plugin settings system

A config layer that makes the plugins' hardcoded conventions (`tasks/`,
`.claude/worktrees/`, `task/` prefix, …) explicit, overrideable, and
validatable. The `settings` plugin owns the *infrastructure*; each consuming
plugin owns its *own* config. Shipped in phase 1 (PR #40) as the config surface
only — no runtime consumers yet.

Authoritative sources (read these, don't trust this file for mutable detail):
`plugins/settings/scripts/settings.py` (the loader/resolver/validator/CLI, Python
3.11 stdlib `tomllib`), `plugins/settings/README.md` (schema format + the consumer
contract), and each plugin's `schema/settings.schema.json`.

## Ownership model (the core idea)

Split by who owns what:

- **Each plugin owns** a `schema/settings.schema.json` — a JSON-Schema subset
  carrying types, enums, `required`, **defaults**, and the config filename
  (`x-config-file`, e.g. `.work-system.toml`). A plugin adds settings by dropping
  that file in; the settings plugin needs no change.
- **The settings plugin owns** only the machinery: discover schemas → resolve
  (defaults ⊕ user overrides) → validate → read/write. It never encodes the
  semantic meaning of any plugin's keys.

## Resolve model

- **Defaults live in the schema, never in the config TOML.** A user config holds
  *only* overrides; a missing config file is fully valid and resolves entirely to
  schema defaults. `get`/`show` return the resolved (merged) view by default.
- **Defaults encode CURRENT behavior, not a redesign.** e.g. `worktrees_dir`
  defaults to `.claude/worktrees` (today's value), with the neutral `.worktrees`
  target gated behind `[compat]` toggles / `mode.knowledge_mode` — stored now,
  acted on later. Migrations flip a default deliberately; consumers keep reading
  the same key.

## `[related_projects]` — the dynamic-map exception

A section annotated `x-semantic: related-projects` is a free-form map of sibling
projects (entries are a bare path string *or* a `{path, role, tags}` table). A
path that doesn't exist locally is a **warning, not an error** — machines differ.
It is the cross-project manager-peering address book consumed by
[manager-worker-orchestration.md](manager-worker-orchestration.md) (the repo path
is the durable, transport-independent peer address).

## Consumer contract (phase 2, not yet wired)

When a plugin adopts settings, it must: read via `settings.py get <plugin>.<…>
--json` (or `show <plugin> --json`) — **never the TOML directly**, which skips
defaults + validation — and always fall back to the schema default (the resolver
guarantees a value even with no file). Change behavior by editing the schema
default, not by hardcoding the old constant beside the lookup. First queued
consumers: work-system paths, and migrating kickoff's committed agent default
(see [../features/kickoff-agent-selection.md](../features/kickoff-agent-selection.md))
into a `[agents]` section.

## Hardening lessons (from the swarm + Codex reviews)

The write path is the risk surface — a naive TOML serializer and an under-vetted
config path both bit. The durable rules that emerged:

- **TOML serialization must round-trip through `tomllib`**: escape control chars
  in strings and quote table/key segments that aren't bare-key-safe. A plain
  `"…"` writer produced files `tomllib` then refused to parse.
- **Vet the config file, not just its name.** `config_filename()` rejects a
  traversing/absolute `x-config-file` (a hostile *schema*), but a shared
  `config_path()` helper must also reject a **symlink** at that name (a hostile
  *repo*): a checked-out `.work-system.toml -> ../../secret` otherwise leaks an
  outside file on read and writes out of the project root. Two distinct vectors.
- **`set` gates the write on schema validity** (rejects a coerced array with
  wrong element types / a mistyped dynamic field) and **classifies the path
  target** — setting a section node or descending past a scalar leaf is a clear
  error, not a crash or silent mis-nest.

## Known residual (phase-1 limitation)

Schema discovery targets the monorepo `plugins/*/schema` layout; an *installed*
marketplace (versioned cache dirs) finds nothing unless `SETTINGS_PLUGINS_DIR` is
set — tracked as its own task. Required before installed plugins consume settings.
