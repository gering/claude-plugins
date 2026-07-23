---
name: settings
description: |
  Read/write per-plugin TOML settings resolved over schema defaults.
  Trigger: "show settings", "settings for work-system", "set a setting", "validate config".
user_invocable: true
---

# Plugin Settings

> List, read, write, and validate per-plugin settings. Each plugin owns a config
> file (`.work-system.toml`, `.knowledge-system.toml`, `.pr-flow.toml`), its
> defaults, and its validation schema. Only user overrides are stored; defaults
> live in the schema and are merged in on read.

## Arguments

`$ARGUMENTS` is a settings subcommand. Map the user's intent to one of:

| Intent | Command |
|--------|---------|
| what's configurable | `list` |
| see the effective config | `show <plugin>` |
| see only a plugin's overrides | `show <plugin> --overrides` |
| see built-in defaults | `defaults <plugin>` |
| read one value | `get <plugin>.<section>.<key>` |
| change one value | `set <plugin>.<section>.<key> <value>` |
| remove an override | `unset <plugin>.<section>.<key>` |
| check config health | `validate` |

`<plugin>` is optional for `show`/`defaults`/`validate` (omit = all plugins).

## Instructions

1. **Run the script** — all logic lives in it; don't reimplement it in prose:
   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/settings.py" <subcommand> [args...]
   ```
   Add `--json` to any read command (`list`, `show`, `defaults`, `get`,
   `validate`) when you need to parse the output; the default is human-readable.

2. **Pick the subcommand** from the table above based on what the user asked.
   - For a read ("what's the tasks dir?"), prefer `get <plugin>.<section>.<key>`
     for one value, or `show <plugin>` for the whole effective config (add
     `--overrides` to see only what the user changed).
   - For a write, use `set`. The script coerces the value to the schema type and
     rejects out-of-enum values, so pass the raw string (`set
     knowledge-system.mode.knowledge_mode neutral`). It writes only the override
     into the plugin's TOML file at the repo root.

3. **Report results plainly.** For `validate`, surface errors vs warnings as the
   script prints them (exit code is non-zero when there are errors). A missing
   config file is not an error — every plugin resolves to its defaults.

## Notes

- **Discovery:** the script finds `plugins/*/schema/settings.schema.json` by
  walking up to a `plugins/` dir, and config files at the git repo root. Override
  with `SETTINGS_PLUGINS_DIR` / `SETTINGS_PROJECT_ROOT` if running outside the
  monorepo layout.
- **`set`/`unset` rewrite the override file** from parsed values — TOML comments
  and original formatting are not preserved. Override files are small (defaults
  come from the schema), so this stays readable.
- **Consumers** (teaching `work-system` / `knowledge-system` to read resolved
  settings) are a follow-up; this skill only manages the config surface.
- See `plugins/settings/README.md` for the schema format and design rationale.
