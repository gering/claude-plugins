# settings

A plugin settings system for the `gering-plugins` marketplace. Each plugin
declares a small schema with defaults; users override only what they need in a
plugin-local TOML file; the resolver merges the two. One skill (`/settings`) and
one script (`scripts/settings.py`, Python 3.11+ stdlib only) expose the whole
surface.

## Why

The plugins hardcode conventions — `tasks/`, `.claude/worktrees/`, `task/<name>`
branches, `.claude/knowledge/`, `CLAUDE.md`. This makes those conventions
**explicit, readable, overrideable, and validatable** without duplicating
defaults into every repo. It also prepares a future neutral / Codex layout: the
schema carries a migration path (`[compat]`, `mode.knowledge_mode`) as storage
today, behavior later.

## Ownership model

Each plugin owns its settings, the settings plugin owns only the infrastructure:

| Owned by each plugin | Owned by `settings` |
|----------------------|---------------------|
| `schema/settings.schema.json` (types, enums, defaults, config filename) | discovery, resolve, validate, read/write CLI |
| the semantic meaning of its keys | the merge + validation engine |

A plugin adds settings by dropping a `schema/settings.schema.json` into its own
directory. No change to the settings plugin is needed.

## Config files

One TOML file per plugin at the **repo root**, holding only overrides:

```
.work-system.toml
.knowledge-system.toml
.pr-flow.toml
```

A missing file is fully valid — the plugin resolves to its schema defaults.

## Commands

```sh
python3 plugins/settings/scripts/settings.py <subcommand>
```

| Command | Does |
|---------|------|
| `list` | plugins + config filenames, whether an override file exists |
| `show [plugin]` | effective config: defaults merged with overrides |
| `show [plugin] --overrides` | only the user overrides (raw) |
| `defaults [plugin]` | schema defaults only |
| `get <plugin>.<section>.<key>` | one resolved value |
| `set <plugin>.<section>.<key> <value>` | write one override (type-coerced, enum-checked) |
| `unset <plugin>.<section>.<key>` | remove one override, pruning emptied sections |
| `validate [plugin]` | check TOML syntax + schema semantics; non-zero exit on errors |

Add `--json` to any read command for machine output. Discovery of schemas and
config location can be overridden with `SETTINGS_PLUGINS_DIR` (os.pathsep-
separated roots to scan for `*/schema/settings.schema.json`) and
`SETTINGS_PROJECT_ROOT` (where config files live).

## Schema format

A `settings.schema.json` is a **subset of JSON Schema** plus two custom keys.
The subset the validator understands: `type` (`string` / `integer` / `number` /
`boolean` / `array` / `object`), `properties`, `items`, `enum`, `required`, and
`default`. Defaults live here — never in the config TOML.

```jsonc
{
  "x-plugin": "work-system",          // plugin name (defaults to the dir name)
  "x-config-file": ".work-system.toml", // override file at the repo root
  "type": "object",
  "properties": {
    "paths": {
      "type": "object",
      "properties": {
        "tasks_dir": { "type": "string", "default": "tasks" }
      }
    },
    "mode": {
      "type": "object",
      "properties": {
        "knowledge_mode": {
          "type": "string",
          "enum": ["claude", "neutral", "symlinked"],
          "default": "claude"
        }
      }
    }
  }
}
```

### Dynamic sections (`related_projects`)

A section annotated `"x-semantic": "related-projects"` is a free-form map of
sibling projects this repo coordinates with — the address book cross-project
orchestration consumes. Entries take either form:

```toml
[related_projects]
backend = "/abs/path/to/backend"                 # shorthand: name = path

[related_projects.frontend]                       # table: adds role/tags
path = "/abs/path/to/frontend"
role = "ui"
tags = ["web", "spa"]
```

Validation checks the shape (a `path` string is required in the table form) and
**warns** — never errors — when a path doesn't exist locally, since machines
differ. `normalize_related_projects()` resolves both forms to
`{name: {path, role?, tags?}}` for consumers. Storage/resolve only for now; no
plugin acts on it yet.

## How plugins should consume resolved settings (contract)

Consumer wiring lands in a follow-up. When a plugin adopts settings, it should:

1. **Read once, resolved.** Call
   `settings.py get <plugin>.<section>.<key> --json` (or `show <plugin> --json`
   for the whole effective config) and use the value. Never read the TOML file
   directly — that skips defaults and validation.
2. **Fall back to the default, always.** The resolver guarantees a value even
   with no config file. A consumer must not require the file to exist.
3. **Don't hardcode the old constant beside the lookup.** Replace
   `tasks/` with the resolved `paths.tasks_dir`; the schema default (`"tasks"`)
   preserves today's behavior.
4. **Change behavior via defaults, not code.** To migrate a convention (e.g. to
   a neutral worktrees dir), flip the schema default and the `[compat]` toggle
   in one deliberate step — consumers keep reading the same key.

## Defaults reflect current behavior

Per the task's guiding rule, defaults are **what the plugins assume today**, not
a speculative redesign — e.g. `worktrees_dir` defaults to `.claude/worktrees`
(current) with the neutral `.worktrees` target gated behind
`[compat].prefer_neutral_worktrees` (off). Migrations change defaults
deliberately, later.

## Limitations (phase 1)

- `set` / `unset` rewrite the override file from parsed values — **TOML comments
  and formatting are not preserved**. Files are small, so this stays readable.
- Schema discovery targets the **monorepo `plugins/` layout**. For an installed
  marketplace (versioned cache dirs), point `SETTINGS_PLUGINS_DIR` at the plugin
  roots. A discovery story for installed marketplaces is a follow-up.
- No config-version field yet — added only when versioned migrations exist.

## Tests

`scripts/test_settings.py` (run by `scripts/check-structure.py` in CI) covers
default extraction, resolve merge, validation (type / enum / unknown key /
related-project paths), TOML round-trip, value coercion, and the `set`/`get`/
`unset` CLI cycle against a throwaway schema and project root.
