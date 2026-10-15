#!/usr/bin/env python3
"""ws-settings.py — read one resolved work-system setting for a repo.

Usage:  ws-settings.py get <dir> <dotted.key>

The one read path for work-system's own settings (`.work-system.toml`, schema
in ../schema/settings.schema.json). Read here rather than through the settings
plugin's settings.py: that plugin is optional, and its discovery does not find
installed plugins yet — the same deliberate exception pr-flow's `review.route`
makes. The schema stays the one place a key's type, constraints, default and
the config file name live; nothing is restated in a caller.

The config is read from the repo's MAIN checkout (first `git worktree list`
entry), never from the linked worktree <dir> may be: every lane of a repo then
sees the same value, and a branch — e.g. one `/adopt` took over from someone
else — cannot change the settings its own lane is set up with.

Output (stdout, key=value):
  value=<v>    the resolved value; empty when the key has no default and the
               repo does not set it (the caller's own default applies)
  source=file|default|unset
  note=<text>  only when the file could not be used — the value is then the
               schema default. A setting never blocks the caller.

Exit 0 whenever a value (or "unset") was resolved, also on a fallback.
Exit 2 on a usage error: bad arguments or a key the schema does not define.
"""
import json
import os
import subprocess
import sys

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema",
                      "settings.schema.json")


def die(msg):
    print(f"ws-settings.py: {msg}", file=sys.stderr)
    sys.exit(2)


def main_checkout(d):
    try:
        out = subprocess.run(["git", "-C", d, "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    first = out.split("\n", 1)[0]
    return first[len("worktree "):] if first.startswith("worktree ") else None


def check(spec, val):
    """None if val satisfies the schema leaf, else a one-line reason."""
    typ = spec.get("type")
    ok = {
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "string": lambda v: isinstance(v, str),
    }.get(typ, lambda v: True)
    if not ok(val):
        return f"must be {typ}, got {type(val).__name__}"
    if "enum" in spec and val not in spec["enum"]:
        return f"must be one of {'|'.join(map(str, spec['enum']))}"
    if "minimum" in spec and val < spec["minimum"]:
        return f"must be >= {spec['minimum']}"
    return None


def emit(value, source, note=""):
    v = "" if value is None else (str(value).lower() if isinstance(value, bool) else str(value))
    print(f"value={v}\nsource={source}")
    if note:
        print(f"note={note}")
    sys.exit(0)


def main(argv):
    if len(argv) != 4 or argv[1] != "get":
        die("usage: ws-settings.py get <dir> <dotted.key>")
    d, key = argv[2], argv[3]
    with open(SCHEMA) as f:
        schema = json.load(f)
    spec = schema
    for part in key.split("."):
        spec = spec.get("properties", {}).get(part)
        if spec is None:
            die(f"unknown setting: {key}")
    default = spec.get("default")
    fallback = "default" if "default" in spec else "unset"

    name = schema.get("x-config-file", ".work-system.toml")
    if "/" in name or name in ("", ".", ".."):
        emit(default, fallback, "schema x-config-file is not a plain file name — using the default")
    root = main_checkout(d)
    if root is None:
        emit(default, fallback)
    cfg = os.path.join(root, name)
    if not os.path.lexists(cfg):
        emit(default, fallback)
    # A checked-out link would read outside the repo.
    if os.path.islink(cfg):
        emit(default, fallback, f"{name} is a symlink — ignored, using the default")
    try:
        import tomllib
    except ModuleNotFoundError:
        emit(default, fallback, f"{name} not read: needs Python 3.11+ (tomllib) — using the default")
    try:
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:  # noqa: BLE001 — any unreadable file falls back
        emit(default, fallback, f"{name} unreadable ({type(e).__name__}) — using the default")
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            emit(default, fallback)
        node = node[part]
    bad = check(spec, node)
    if bad:
        emit(default, fallback, f"{key} in {name} {bad} — using the default")
    emit(node, "file")


if __name__ == "__main__":
    main(sys.argv)
