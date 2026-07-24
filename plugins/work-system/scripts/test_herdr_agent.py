#!/usr/bin/env python3
"""Tests for herdr-agent.sh — the wrapper over the herdr agent primitives.

A fake `herdr` on PATH returns canned JSON/text per subcommand, so these run the
REAL herdr-agent.sh end to end (the exact surface lanes.sh and the future
Manager tooling call). Covers: list validation (ok / malformed → 5 / herdr
failure → 4), the tools-absent degrade (→ 3, no hang), get validation, read as
best-effort passthrough (herdr rc + text forwarded, no schema), and that wait is
BOUNDED — a caller-omitted --timeout is injected, a caller-supplied one is left
alone.
"""
import json as jsonlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "herdr-agent.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def herdr_stub(cases):
    """{'<argv1> <argv2>': (stdout, stderr, exit)} → a fake `herdr` script. With
    $HERDR_ARGV_LOG set it appends every received arg (one per line) so a test
    can assert the exact argv herdr-agent.sh execs."""
    lines = [
        "#!/usr/bin/env bash",
        'if [ -n "${HERDR_ARGV_LOG:-}" ]; then',
        '  printf \'%s\\n\' "$@" >> "$HERDR_ARGV_LOG"',
        "fi",
        'case "$1 $2" in',
    ]
    for key, (out, err, rc) in cases.items():
        lines.append(f'  "{key}")')
        if out:
            lines.append(f"    printf '%s' {shquote(out)}")
        if err:
            lines.append(f"    printf '%s' {shquote(err)} >&2")
        lines.append(f"    exit {rc}")
        lines.append("    ;;")
    lines.append('  *) echo "unhandled herdr stub call: $*" >&2; exit 9 ;;')
    lines.append("esac")
    return "\n".join(lines)


class Env:
    def __init__(self, cases, log_argv=False):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        bindir = root / "bin"
        bindir.mkdir()
        herdr = bindir / "herdr"
        herdr.write_text(herdr_stub(cases))
        herdr.chmod(0o755)
        self.env = dict(os.environ)
        self.env["PATH"] = f"{bindir}:{self.env['PATH']}"
        self.argv_log = root / "argv.log" if log_argv else None
        if self.argv_log is not None:
            self.env["HERDR_ARGV_LOG"] = str(self.argv_log)

    def logged_argv(self):
        if self.argv_log is None or not self.argv_log.exists():
            return []
        return self.argv_log.read_text().splitlines()

    def run(self, *args, path=None):
        env = dict(self.env)
        if path is not None:
            env["PATH"] = path
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env, capture_output=True, text=True, timeout=20,
        )

    def close(self):
        self.tmp.cleanup()


AGENTS = jsonlib.dumps({"result": {"agents": [{"agent": "claude", "cwd": "/x"}]}})

# --- list: valid JSON passes through, exit 0 ------------------------------- #
e = Env({"agent list": (AGENTS, "", 0)})
r = e.run("list")
check("list: exit 0", r.returncode == 0)
check("list: JSON forwarded", jsonlib.loads(r.stdout)["result"]["agents"][0]["agent"] == "claude")
e.close()

# --- list: malformed JSON → code 5, empty stdout --------------------------- #
e = Env({"agent list": ("not json{", "", 0)})
r = e.run("list")
check("list: malformed → 5", r.returncode == 5)
check("list: malformed → no stdout", r.stdout == "")
e.close()

# --- list: valid JSON but wrong shape (agents not a list) → code 5 --------- #
e = Env({"agent list": (jsonlib.dumps({"result": {"agents": {}}}), "", 0)})
r = e.run("list")
check("list: wrong-shape → 5", r.returncode == 5)
e.close()

# --- list: herdr itself fails (rc != 0) → code 4 --------------------------- #
e = Env({"agent list": ("", "boom", 1)})
r = e.run("list")
check("list: herdr failure → 4", r.returncode == 4)
e.close()

# --- tools absent (no herdr on PATH) → code 3, no hang --------------------- #
e = Env({"agent list": (AGENTS, "", 0)})
r = e.run("list", path="/usr/bin:/bin")
check("degrade: no herdr → 3", r.returncode == 3)
check("degrade: no stdout", r.stdout == "")
e.close()

# --- get: validates a result object ---------------------------------------- #
e = Env({"agent get": (jsonlib.dumps({"result": {"agent": {"pane_id": "w1:p1"}}}), "", 0)})
r = e.run("get", "w1:p1")
check("get: exit 0", r.returncode == 0)
check("get: JSON forwarded", jsonlib.loads(r.stdout)["result"]["agent"]["pane_id"] == "w1:p1")
e.close()

# --- get: malformed → code 5 ----------------------------------------------- #
e = Env({"agent get": ("nope", "", 0)})
r = e.run("get", "w1:p1")
check("get: malformed → 5", r.returncode == 5)
e.close()

# --- read: best-effort passthrough (free-form text + herdr rc, no schema) -- #
e = Env({"agent read": ("some pane text, not json\n", "", 0)})
r = e.run("read", "w1:p1")
check("read: exit 0", r.returncode == 0)
check("read: text forwarded verbatim", r.stdout == "some pane text, not json\n")
e.close()

# read forwards herdr's non-zero rc unchanged (best-effort, never masks failure)
e = Env({"agent read": ("", "gone", 4)})
r = e.run("read", "w1:pX")
check("read: herdr rc forwarded", r.returncode == 4)
e.close()

# --- wait: caller omits --timeout → the default is injected (bounded) ------ #
e = Env({"agent wait": ("", "", 0)}, log_argv=True)
r = e.run("wait", "w1:p1", "--status", "idle")
argv = e.logged_argv()
check("wait: exit 0", r.returncode == 0)
check("wait: --timeout injected when omitted", "--timeout" in argv)
check("wait: default value injected", "10000" in argv)
e.close()

# --- wait: caller's own --timeout is preserved, not doubled ---------------- #
e = Env({"agent wait": ("", "", 0)}, log_argv=True)
r = e.run("wait", "w1:p1", "--status", "idle", "--timeout", "500")
argv = e.logged_argv()
check("wait: caller timeout preserved", "500" in argv)
check("wait: default not appended over caller's", "10000" not in argv)
check("wait: exactly one --timeout", argv.count("--timeout") == 1)
e.close()


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("herdr-agent.sh: all tests passed")
