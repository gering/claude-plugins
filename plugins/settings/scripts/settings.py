#!/usr/bin/env python3
"""Plugin settings system: load, resolve, and validate per-plugin TOML config.

Each plugin owns a `schema/settings.schema.json` file (a JSON-Schema subset that
also carries defaults). This tool discovers those schemas, reads the optional
user override file per plugin (e.g. `.work-system.toml` at the project root),
merges the two, and validates the result.

Design invariants (see plugins/settings/README.md):
  - Defaults live in the schema, never duplicated into config TOML files.
  - Only user overrides are written; a missing config file is fully valid.
  - `show --resolved` is the merged view consumers should read.
  - Validation covers syntax (TOML) and semantics (types, enums, unknown keys,
    related-project paths).

Runtime deps: Python 3.11+ stdlib only (`tomllib`). No third-party packages, no
build step — matching the rest of this build-less marketplace repo.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def find_project_root() -> Path:
    """Directory that holds the user config TOML files.

    `SETTINGS_PROJECT_ROOT` overrides everything; otherwise the git toplevel of
    the cwd; otherwise the cwd. Config files (`.work-system.toml`, …) live here.
    """
    env = os.environ.get("SETTINGS_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if out:
            return Path(out).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return Path.cwd().resolve()


def find_plugins_dirs() -> list[Path]:
    """Directories to scan for `*/schema/settings.schema.json`.

    `SETTINGS_PLUGINS_DIR` (os.pathsep-separated) overrides discovery. Otherwise
    we walk up from this script and from the cwd looking for a `plugins/` dir —
    which is how this monorepo lays plugins out during development. Cross-plugin
    discovery for an *installed* marketplace (versioned cache dirs) is a phase-2
    concern; set SETTINGS_PLUGINS_DIR there.
    """
    env = os.environ.get("SETTINGS_PLUGINS_DIR")
    if env:
        return [Path(p).resolve() for p in env.split(os.pathsep) if p]

    found: list[Path] = []
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in [start, *start.parents]:
            candidate = parent / "plugins"
            if candidate.is_dir():
                resolved = candidate.resolve()
                if resolved not in found:
                    found.append(resolved)
                break
    return found


def load_schemas() -> dict[str, dict]:
    """Map plugin name -> parsed schema, discovered across all plugins dirs.

    Plugin name comes from the schema's `x-plugin`, falling back to the owning
    `plugins/<name>/` directory. First win on duplicate names (dev dir before
    any installed copy).
    """
    schemas: dict[str, dict] = {}
    for plugins_dir in find_plugins_dirs():
        for schema_path in sorted(plugins_dir.glob("*/schema/settings.schema.json")):
            try:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise SettingsError(f"{schema_path}: invalid schema JSON — {exc}")
            plugin = schema.get("x-plugin") or schema_path.parent.parent.name
            schema.setdefault("_source", str(schema_path))
            schemas.setdefault(plugin, schema)
    return schemas


class SettingsError(Exception):
    """A user-facing error (bad plugin name, unreadable config, …)."""


# --------------------------------------------------------------------------- #
# Schema helpers
# --------------------------------------------------------------------------- #


def config_filename(schema: dict) -> str:
    name = schema.get("x-config-file")
    if not name:
        raise SettingsError(f"schema {schema.get('x-plugin')} lacks x-config-file")
    # x-config-file comes from a (plugin-authored) schema but is joined onto the
    # project root for read/write/unlink — a plain filename only, never a path
    # that could traverse out (`../…`), be absolute, or expand `~`. Reject
    # anything but a single basename so a malformed/hostile schema cannot touch
    # files outside the project root.
    if (
        name in (".", "..")
        or name != os.path.basename(name)
        or os.path.isabs(name)
        or name.startswith("~")
    ):
        raise SettingsError(
            f"schema {schema.get('x-plugin')}: x-config-file must be a plain "
            f"filename, got {name!r}"
        )
    return name


def extract_defaults(node: dict) -> object:
    """Collect the default value tree from a schema node.

    An object node recurses into `properties`; a leaf contributes its `default`
    if present. Object nodes with an explicit `default` (e.g. related_projects
    `{}`) use it directly.
    """
    if node.get("type") == "object" and "properties" in node:
        result: dict[str, object] = {}
        for key, sub in node["properties"].items():
            value = extract_defaults(sub)
            if value is not _NO_DEFAULT:
                result[key] = value
        return result
    return node.get("default", _NO_DEFAULT)


_NO_DEFAULT = object()


def schema_defaults(schema: dict) -> dict:
    out = extract_defaults(schema)
    return out if isinstance(out, dict) else {}


# --------------------------------------------------------------------------- #
# Resolve
# --------------------------------------------------------------------------- #


def deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` onto a copy of `base`; scalars/arrays replace."""
    result = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve(schema: dict, user: dict) -> dict:
    return deep_merge(schema_defaults(schema), user)


# --------------------------------------------------------------------------- #
# User config IO
# --------------------------------------------------------------------------- #


def config_path(schema: dict, project_root: Path) -> Path:
    """Resolve the override file path, rejecting a symlink at that name.

    `config_filename()` vets the *name* (a schema-supplied path can't traverse
    out), but the file on disk is a separate vector: a repo-controlled symlink at
    the config name (`.work-system.toml -> ../../secret`) would make read leak an
    outside file's contents (e.g. `~/.aws/credentials` is valid TOML) and write
    follow the link out of the project root. A settings override is never
    legitimately a symlink — refuse it for both read and write.
    """
    path = project_root / config_filename(schema)
    if path.is_symlink():
        raise SettingsError(
            f"{path}: config file is a symlink — refusing to follow it"
        )
    return path


def load_user_config(schema: dict, project_root: Path) -> dict:
    """Parse the plugin's override TOML, or {} when absent."""
    path = config_path(schema, project_root)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise SettingsError(f"{path}: cannot read config — {exc}")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

_TYPE_LABELS = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "table",
}


def _matches_type(value: object, typ: str) -> bool:
    if typ == "string":
        return isinstance(value, str)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "array":
        return isinstance(value, list)
    if typ == "object":
        return isinstance(value, dict)
    return True


def validate(schema: dict, user: dict, project_root: Path) -> list[tuple[str, str]]:
    """Return (level, message) findings for a plugin's user config.

    level is "error" (invalid config) or "warn" (suspect but tolerated, e.g. an
    unknown key or a related-project path that doesn't exist locally).
    """
    findings: list[tuple[str, str]] = []
    _validate_node(schema, user, "", findings, project_root)
    return findings


def _validate_node(node: dict, value: object, path: str, findings, project_root):
    semantic = node.get("x-semantic")
    if semantic == "related-projects":
        _validate_related_projects(value, path, findings, project_root)
        return

    typ = node.get("type")
    if typ and not _matches_type(value, typ):
        findings.append(
            ("error", f"{path or '<root>'}: expected {_TYPE_LABELS.get(typ, typ)}, "
                      f"got {type(value).__name__}")
        )
        return

    if "enum" in node and value not in node["enum"]:
        allowed = ", ".join(json.dumps(v) for v in node["enum"])
        findings.append(("error", f"{path}: {value!r} not in allowed values [{allowed}]"))

    if typ == "object" and isinstance(value, dict):
        props = node.get("properties", {})
        for key in node.get("required", []):
            if key not in value:
                findings.append(("error", f"{path or '<root>'}: missing required key '{key}'"))
        for key, sub in value.items():
            child = f"{path}.{key}" if path else key
            if key in props:
                _validate_node(props[key], sub, child, findings, project_root)
            else:
                findings.append(("warn", f"{child}: unknown key (not in schema)"))

    if typ == "array" and isinstance(value, list) and "items" in node:
        for i, item in enumerate(value):
            _validate_node(node["items"], item, f"{path}[{i}]", findings, project_root)


def _validate_related_projects(value, path, findings, project_root):
    if not isinstance(value, dict):
        findings.append(("error", f"{path}: expected a table of project entries"))
        return
    for name, entry in value.items():
        child = f"{path}.{name}"
        if isinstance(entry, str):
            proj_path = entry
        elif isinstance(entry, dict):
            if "path" not in entry:
                findings.append(("error", f"{child}: table entry missing 'path'"))
                continue
            proj_path = entry["path"]
            if not isinstance(proj_path, str):
                findings.append(("error", f"{child}.path: expected string"))
                continue
            if "role" in entry and not isinstance(entry["role"], str):
                findings.append(("error", f"{child}.role: expected string"))
            if "tags" in entry and not (
                isinstance(entry["tags"], list)
                and all(isinstance(t, str) for t in entry["tags"])
            ):
                findings.append(("error", f"{child}.tags: expected array of strings"))
            for extra in set(entry) - {"path", "role", "tags"}:
                findings.append(("warn", f"{child}.{extra}: unknown key (not in schema)"))
        else:
            findings.append(("error", f"{child}: expected a path string or table"))
            continue
        candidate = Path(proj_path)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        if not candidate.exists():
            findings.append(("warn", f"{child}: path does not exist locally ({proj_path})"))


def normalize_related_projects(value: dict) -> dict:
    """Resolve string/table entries to a uniform {name: {path, role?, tags?}}."""
    out: dict[str, dict] = {}
    for name, entry in value.items():
        if isinstance(entry, str):
            out[name] = {"path": entry}
        elif isinstance(entry, dict):
            out[name] = dict(entry)
    return out


# --------------------------------------------------------------------------- #
# TOML serialization (write path)
# --------------------------------------------------------------------------- #


_TOML_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _toml_basic_string(s: str) -> str:
    """A TOML basic string with control chars escaped, so it always round-trips.

    Plain `"…"` quoting that left a raw newline/tab inside produced a file tomllib
    then refused to parse. Escape the short forms and \\uXXXX everything else in
    the C0 range (+ DEL), which TOML basic strings forbid unescaped.
    """
    out = []
    for ch in s:
        if ch in _TOML_SHORT_ESCAPES:
            out.append(_TOML_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _is_bare_key(k: str) -> bool:
    """TOML bare-key charset: ASCII letters, digits, `_`, `-`."""
    return bool(k) and all(
        ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or c in "_-"
        for c in k
    )


def _toml_key(k: str) -> str:
    """A key/table-name segment: bare where legal, else a quoted string."""
    return k if _is_bare_key(k) else _toml_basic_string(k)


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _toml_basic_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    raise SettingsError(f"cannot serialize value of type {type(value).__name__}")


def dump_toml(data: dict) -> str:
    """Serialize a two-level config dict to TOML.

    Handles section -> {key: scalar/array} plus one level of sub-tables (used by
    related_projects table entries). Comments and original formatting are NOT
    preserved — `set`/`unset` rewrite the override file from parsed values. This
    is an accepted phase-1 limitation; override files are small and defaults live
    in the schema, so a regenerated file stays readable.
    """
    lines: list[str] = []
    for section, body in data.items():
        if not isinstance(body, dict):
            raise SettingsError(f"top-level key '{section}' must be a table")
        scalars = {k: v for k, v in body.items() if not isinstance(v, dict)}
        tables = {k: v for k, v in body.items() if isinstance(v, dict)}
        if scalars or not tables:
            if lines:
                lines.append("")
            lines.append(f"[{_toml_key(section)}]")
            for key, value in scalars.items():
                lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
        for name, sub in tables.items():
            if lines:
                lines.append("")
            lines.append(f"[{_toml_key(section)}.{_toml_key(name)}]")
            for key, value in sub.items():
                lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    return "\n".join(lines) + "\n" if lines else ""


def write_user_config(schema: dict, project_root: Path, data: dict) -> Path:
    path = config_path(schema, project_root)  # rejects a symlink at the config name
    text = dump_toml(data)
    if text:
        path.write_text(text, encoding="utf-8")
    elif path.exists():
        path.unlink()  # emptied config → remove the file entirely
    return path


# --------------------------------------------------------------------------- #
# Path navigation for get/set/unset
# --------------------------------------------------------------------------- #


def split_dotted(path: str) -> list[str]:
    parts = [p for p in path.split(".") if p]
    if not parts:
        raise SettingsError("empty setting path")
    return parts


def dig(data: dict, keys: list[str]):
    cur: object = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return _NO_DEFAULT
        cur = cur[key]
    return cur


def resolve_set_target(schema: dict, keys: list[str]) -> tuple[str, object]:
    """Classify a dotted set-path against the schema. Returns (kind, detail):

    - ("leaf", spec)        settable scalar/array leaf → coerce + enum-check
    - ("section", node)     path stops at an object/section → reject (#5)
    - ("scalar-prefix", i)  key[i] descends past a scalar leaf → reject (#8)
    - ("dynamic", None)     under a dynamic-map (related_projects) → free-form
    - ("unknown", None)     key not in schema → written with a warning
    """
    node = schema
    for i, key in enumerate(keys):
        if node.get("x-semantic") == "related-projects":
            return ("dynamic", None)  # entries are free-form project names
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties")
            if not isinstance(props, dict) or key not in props:
                return ("unknown", None)
            node = props[key]
        else:
            return ("scalar-prefix", i)  # node is a scalar leaf, but keys remain
    if node.get("type") == "object" or "properties" in node:
        return ("section", node)
    return ("leaf", node)


def coerce_value(raw: str, spec: dict | None) -> object:
    """Coerce a CLI string to the schema type; enum-checked by the caller."""
    typ = spec.get("type") if spec else None
    if typ == "boolean":
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise SettingsError(f"cannot read {raw!r} as boolean")
    if typ == "integer":
        try:
            return int(raw)
        except ValueError:
            raise SettingsError(f"cannot read {raw!r} as integer")
    if typ == "number":
        try:
            return float(raw)
        except ValueError:
            raise SettingsError(f"cannot read {raw!r} as number")
    if typ == "array":
        raw = raw.strip()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        return [p.strip() for p in raw.split(",") if p.strip()]
    return raw


# --------------------------------------------------------------------------- #
# Command implementations
# --------------------------------------------------------------------------- #


def _pick_schema(schemas: dict[str, dict], plugin: str) -> dict:
    if plugin not in schemas:
        known = ", ".join(sorted(schemas)) or "(none discovered)"
        raise SettingsError(f"unknown plugin '{plugin}'. Known: {known}")
    return schemas[plugin]


def cmd_list(args, schemas, project_root):
    rows = []
    for plugin in sorted(schemas):
        schema = schemas[plugin]
        cfg = config_filename(schema)
        exists = (project_root / cfg).exists()
        rows.append({"plugin": plugin, "config_file": cfg, "override_present": exists})
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        width = max((len(r["plugin"]) for r in rows), default=6)
        for r in rows:
            mark = "override" if r["override_present"] else "defaults"
            print(f"{r['plugin']:<{width}}  {r['config_file']:<24}  [{mark}]")


def cmd_show(args, schemas, project_root):
    plugins = [args.plugin] if args.plugin else sorted(schemas)
    result = {}
    for plugin in plugins:
        schema = _pick_schema(schemas, plugin)
        user = load_user_config(schema, project_root)
        result[plugin] = user if args.overrides else resolve(schema, user)
    if args.json:
        print(json.dumps(result if args.plugin is None else result[args.plugin], indent=2))
        return
    for plugin in plugins:
        header = f"# {plugin}" + (" (overrides)" if args.overrides else " (resolved)")
        print(header)
        body = dump_toml(result[plugin])
        print(body if body.strip() else "# (no settings)")
        if plugin != plugins[-1]:
            print()


def cmd_defaults(args, schemas, project_root):
    plugins = [args.plugin] if args.plugin else sorted(schemas)
    result = {p: schema_defaults(_pick_schema(schemas, p)) for p in plugins}
    if args.json:
        print(json.dumps(result if args.plugin is None else result[args.plugin], indent=2))
        return
    for plugin in plugins:
        print(f"# {plugin} (defaults)")
        print(dump_toml(result[plugin]) or "# (no defaults)")
        if plugin != plugins[-1]:
            print()


def cmd_get(args, schemas, project_root):
    keys = split_dotted(args.path)
    plugin, rest = keys[0], keys[1:]
    schema = _pick_schema(schemas, plugin)
    resolved = resolve(schema, load_user_config(schema, project_root))
    value = dig(resolved, rest)
    if value is _NO_DEFAULT:
        raise SettingsError(f"no setting at '{args.path}'")
    if args.json:
        print(json.dumps(value))
    elif isinstance(value, (dict, list)):
        print(json.dumps(value))
    elif isinstance(value, bool):
        print("true" if value else "false")
    else:
        print(value)


def cmd_set(args, schemas, project_root):
    keys = split_dotted(args.path)
    plugin, rest = keys[0], keys[1:]
    if not rest:
        raise SettingsError("set path must include a section and key, e.g. work-system.paths.tasks_dir")
    schema = _pick_schema(schemas, plugin)
    kind, detail = resolve_set_target(schema, rest)
    if kind == "section":
        raise SettingsError(
            f"{args.path} is a section, not a value — specify a section and key, "
            f"e.g. {args.path}.<key>"
        )
    if kind == "scalar-prefix":
        prefix = ".".join([plugin] + rest[: detail + 1])
        raise SettingsError(f"{prefix} is a value, not a section — cannot set a key beneath it")

    spec = detail if kind == "leaf" else None
    if kind == "dynamic":
        value = args.value  # free-form entry (e.g. a related_projects path) — no coercion
    else:
        value = coerce_value(args.value, spec)
        if spec and "enum" in spec and value not in spec["enum"]:
            allowed = ", ".join(json.dumps(v) for v in spec["enum"])
            raise SettingsError(f"{args.path}: {value!r} not in allowed values [{allowed}]")

    user = load_user_config(schema, project_root)
    cur = user
    for key in rest[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[rest[-1]] = value

    # Gate the write on schema validity: a coerced array with wrong element types
    # (#4) or a dynamic related_projects entry given a mistyped field (#6) must
    # not silently write an invalid config. Only errors block; warnings (unknown
    # key, non-existent related-project path) are tolerated as before.
    errors = [msg for lvl, msg in validate(schema, user, project_root) if lvl == "error"]
    if errors:
        raise SettingsError(
            f"{args.path}: refusing to write an invalid config — "
            + "; ".join(errors)
        )

    path = write_user_config(schema, project_root, user)
    if kind == "unknown":
        print(f"set {args.path} = {json.dumps(value)}  (warning: not in schema)  -> {path}")
    else:
        print(f"set {args.path} = {json.dumps(value)}  -> {path}")


def cmd_unset(args, schemas, project_root):
    keys = split_dotted(args.path)
    plugin, rest = keys[0], keys[1:]
    schema = _pick_schema(schemas, plugin)
    user = load_user_config(schema, project_root)
    cur = user
    trail = [cur]
    for key in rest[:-1]:
        if not isinstance(cur.get(key), dict):
            raise SettingsError(f"no override at '{args.path}'")
        cur = cur[key]
        trail.append(cur)
    if not rest or rest[-1] not in cur:
        raise SettingsError(f"no override at '{args.path}'")
    del cur[rest[-1]]
    # prune now-empty parent tables so the file stays minimal
    for key, parent in zip(reversed(rest[:-1]), reversed(trail[:-1])):
        if isinstance(parent.get(key), dict) and not parent[key]:
            del parent[key]
    path = write_user_config(schema, project_root, user)
    print(f"unset {args.path}  -> {path}")


def cmd_validate(args, schemas, project_root):
    plugins = [args.plugin] if args.plugin else sorted(schemas)
    all_findings: dict[str, list[tuple[str, str]]] = {}
    errors = 0
    warns = 0
    for plugin in plugins:
        schema = _pick_schema(schemas, plugin)
        try:
            user = load_user_config(schema, project_root)
        except SettingsError as exc:
            all_findings[plugin] = [("error", str(exc))]
            errors += 1
            continue
        findings = validate(schema, user, project_root)
        all_findings[plugin] = findings
        errors += sum(1 for lvl, _ in findings if lvl == "error")
        warns += sum(1 for lvl, _ in findings if lvl == "warn")

    if args.json:
        print(json.dumps(
            {p: [{"level": l, "message": m} for l, m in f] for p, f in all_findings.items()},
            indent=2,
        ))
    else:
        for plugin in plugins:
            findings = all_findings[plugin]
            if not findings:
                print(f"{plugin}: OK")
                continue
            print(f"{plugin}:")
            for lvl, msg in findings:
                print(f"  {lvl.upper():5} {msg}")
        print()
        print(f"{errors} error(s), {warns} warning(s)")
    return 1 if errors else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="settings", description="Plugin settings system")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list plugins and their config files")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show effective config (defaults + overrides)")
    p.add_argument("plugin", nargs="?", help="restrict to one plugin")
    p.add_argument("--overrides", action="store_true", help="show only user overrides, not the merged result")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("defaults", help="show schema defaults")
    p.add_argument("plugin", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_defaults)

    p = sub.add_parser("get", help="read a resolved value: <plugin>.<section>.<key>")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("set", help="write an override: <plugin>.<section>.<key> <value>")
    p.add_argument("path")
    p.add_argument("value")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("unset", help="remove an override: <plugin>.<section>.<key>")
    p.add_argument("path")
    p.set_defaults(func=cmd_unset)

    p = sub.add_parser("validate", help="validate config syntax + semantics")
    p.add_argument("plugin", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        schemas = load_schemas()
        if not schemas:
            print("no plugin schemas found (set SETTINGS_PLUGINS_DIR?)", file=sys.stderr)
            return 1
        project_root = find_project_root()
        rc = args.func(args, schemas, project_root)
        return rc if isinstance(rc, int) else 0
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
