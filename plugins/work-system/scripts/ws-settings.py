#!/usr/bin/env python3
"""ws-settings.py — read one resolved work-system setting for a repo.

Usage:  ws-settings.py get <dir> <dotted.key>

The one read path for work-system's own settings (`.work-system.toml`, schema
in ../schema/settings.schema.json). Read here rather than through the settings
plugin's settings.py: that plugin is optional, and its discovery does not find
installed plugins yet — the same deliberate exception pr-flow's `review.route`
makes. The schema stays the one place a key's type, constraints, default and
the config file name live; nothing is restated in a caller.

The config is read at SETTINGS_PROJECT_ROOT, else the git toplevel of <dir> —
the settings plugin's own root rule, so `/settings get|set` and this reader
mean the same file. For a lane that is the worktree's own checkout: a branch
(e.g. one `/adopt` took over) brings its committed .work-system.toml with it.

Output (stdout, key=value):
  value=<v>    the resolved value; empty when the key has no default and the
               repo does not set it (the caller's own default applies)
  source=file|default|unset
  note=<text>  only when the file could not be used — the value is then the
               schema default (or unset). A setting never blocks the caller.

Exit 0 whenever a value (or "unset") was resolved, also on a fallback.
Exit 2 on a usage error: bad arguments or a key the schema does not define.
"""
import errno
import json
import os
import stat
import subprocess
import sys

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema",
                      "settings.schema.json")


def die(msg):
    print(f"ws-settings.py: {msg}", file=sys.stderr)
    sys.exit(2)


def project_root(d):
    # settings.py's find_project_root(), anchored on <dir> instead of the cwd:
    # SETTINGS_PROJECT_ROOT first, then the git toplevel. --show-toplevel prints
    # the raw path (no C-quoting), so only the trailing newline is stripped.
    env = os.environ.get("SETTINGS_PROJECT_ROOT")
    if env:
        return env
    try:
        out = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return d
    return out[:-1] if out.endswith("\n") else out or d


class NotRegularFile(Exception):
    pass


def read_regular(path):
    """Bytes of a regular file, opened without following a symlink and without
    blocking — one open, no check before it: an islink() pre-check leaves a
    window to swap a link in, and a FIFO would hang the caller's kickoff."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFile
        chunks = []
        while True:
            b = os.read(fd, 65536)
            if not b:
                return b"".join(chunks)
            chunks.append(b)
    finally:
        os.close(fd)


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
    if "maximum" in spec and val > spec["maximum"]:
        return f"must be <= {spec['maximum']}"
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
    # value= is one line of text: only scalars have a faithful rendering.
    if spec.get("type") not in ("integer", "boolean", "string"):
        die(f"{key} is not a scalar setting (type {spec.get('type')})")
    default = spec.get("default")
    fallback = "default" if "default" in spec else "unset"

    name = schema.get("x-config-file", ".work-system.toml")
    if "/" in name or name in ("", ".", ".."):
        emit(default, fallback, "schema x-config-file is not a plain file name — using the default")
    cfg = os.path.join(project_root(d), name)
    try:
        raw = read_regular(cfg)
    except FileNotFoundError:
        emit(default, fallback)
    except NotRegularFile:
        emit(default, fallback, f"{name} is not a regular file — ignored, using the default")
    except OSError as e:
        # A checked-out link would read outside the repo; O_NOFOLLOW refuses it.
        if e.errno == errno.ELOOP:
            emit(default, fallback, f"{name} is a symlink — ignored, using the default")
        emit(default, fallback, f"{name} unreadable ({e.strerror}) — using the default")
    try:
        import tomllib
    except ModuleNotFoundError:
        emit(default, fallback, f"{name} not read: needs Python 3.11+ (tomllib) — using the default")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — any unparsable file falls back
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
