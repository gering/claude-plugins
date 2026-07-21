#!/usr/bin/env python3
"""Tests for herdr-launch.sh's error-diagnostics path (herdr_diag) — run
standalone (`python3 test_herdr_launch.py`) or via check-structure.py's
"plugin tests" check.

A fake `herdr` binary on PATH returns canned JSON/text on stderr for each
subcommand, so these tests exercise the REAL herdr-launch.sh end to end (not
herdr_diag in isolation) against the exact integration surface skills call.
Covers: the JSON error-schema extraction, the ws_relevant-gated + clause-
bounded stale-workspace hint (the real w9 incident, the "unrelated clause"
false-positive a loose AND-of-substrings would trip, case-insensitivity, and
an ERE-metachar workspace id), the double control-byte strip (pre- AND
post-JSON-decode, plus $ws itself), the tab-close cleanup diagnostic, and that
the stdout key=value contract is untouched by any of this on the success path.
"""
import json as jsonlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "herdr-launch.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def herdr_stub(cases):
    """Build a fake `herdr` script from {"<argv1> <argv2>": (stdout, stderr, exit)}.

    When $HERDR_ARGV_LOG is set, every call appends a `=== <subcmd>` header line
    followed by each received arg on its own line — so a test can assert the exact
    argv herdr-launch.sh execs (e.g. the worker argv after `agent start … --`)."""
    lines = [
        "#!/usr/bin/env bash",
        'if [ -n "${HERDR_ARGV_LOG:-}" ]; then',
        '  { printf \'=== %s\\n\' "$1 $2"; printf \'%s\\n\' "$@"; } >> "$HERDR_ARGV_LOG"',
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
    """A throwaway PATH with a fake `herdr` stub, plus a worktree dir for
    herdr-launch.sh to target."""

    def __init__(self, cases, log_argv=False):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.worktree = root / "wt"
        self.worktree.mkdir()
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
        """Lines the herdr stub recorded (headers + one arg per line); [] if none."""
        if self.argv_log is None or not self.argv_log.exists():
            return []
        return self.argv_log.read_text().splitlines()

    def run(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env, capture_output=True, text=True, timeout=20,
        )

    def close(self):
        self.tmp.cleanup()


# --- the real incident: agent_placement_not_found names the workspace ------ #
e = Env({"agent start": ("", jsonlib.dumps(
    {"error": {"code": "agent_placement_not_found",
               "message": "agent placement target w9 not found"}}), 1)})
r = e.run("launch", "t", str(e.worktree), "w9")
check("incident: exit 1", r.returncode == 1)
check("incident: code/message shown",
      "agent_placement_not_found" in r.stderr and "target w9 not found" in r.stderr)
check("incident: stale hint shown",
      "HERDR_WORKSPACE_ID=w9 is not a valid workspace" in r.stderr)
check("incident: generic last-resort message still present",
      "did not return a pane id" in r.stderr)
check("incident: no stdout on failure", r.stdout == "")
e.close()

# --- token present + keyword present, but in UNRELATED clauses -> no hint -- #
e = Env({"agent start": ("", jsonlib.dumps(
    {"error": {"code": "some_code",
               "message": "workspace w1 is healthy; agent placement is unavailable"}}), 1)})
r = e.run("launch", "t", str(e.worktree), "w1")
check("unrelated clauses: no stale hint", "is not a valid workspace" not in r.stderr)
check("unrelated clauses: code/message still shown", "some_code" in r.stderr)
e.close()

# --- case-insensitive match ------------------------------------------------ #
e = Env({"agent start": ("", jsonlib.dumps(
    {"error": {"code": "weird", "message": "Workspace w1 Not Found on server"}}), 1)})
r = e.run("launch", "t", str(e.worktree), "w1")
check("case-insensitive: stale hint shown", "HERDR_WORKSPACE_ID=w1 is not a valid" in r.stderr)
e.close()

# --- ERE-metachar workspace id must not false-match ------------------------ #
e = Env({"agent start": ("", jsonlib.dumps(
    {"error": {"code": "other", "message": "aaab placement not found somewhere"}}), 1)})
r = e.run("launch", "t", str(e.worktree), "a+b")
check("ere-metachar: no false stale hint", "is not a valid workspace" not in r.stderr)
e.close()

# --- ws_relevant=0 (pane move) never gets the hint, even with the trigger code #
e = Env({
    "agent start": (jsonlib.dumps({"result": {"agent": {"pane_id": "w1:p5"}}}), "", 0),
    "pane move": ("", jsonlib.dumps(
        {"error": {"code": "agent_placement_not_found", "message": "pane target gone"}}), 1),
})
r = e.run("launch", "t", str(e.worktree), "w1")
check("pane move: exit 0 (moved=no is not a hard failure)", r.returncode == 0)
check("pane move: code/message shown", "agent_placement_not_found" in r.stderr)
check("pane move: no stale hint (ws not relevant here)", "is not a valid workspace" not in r.stderr)
check("pane move: moved=no on stdout", "moved=no" in r.stdout)
check("pane move: pane= still reported", "pane=w1:p5" in r.stdout)
e.close()

# --- control bytes stripped AFTER json-decoding too (not just the raw blob) #
esc_payload = jsonlib.dumps({"error": {"code": "x", "message": "hi \x1b[31mRED\x1b[0m end"}})
e = Env({"agent start": ("", esc_payload, 1)})
r = e.run("launch", "t", str(e.worktree), "w1")
check("json-escaped ESC stripped post-decode", "\x1b" not in r.stderr)
check("surrounding text preserved", "RED" in r.stderr and "end" in r.stderr)
e.close()

# --- $HERDR_WORKSPACE_ID itself is sanitized before interpolation ---------- #
evil_ws = "w1\x1bevil"
e = Env({"agent start": ("", jsonlib.dumps(
    {"error": {"code": "agent_placement_not_found", "message": "target gone"}}), 1)})
r = e.run("launch", "t", str(e.worktree), evil_ws)
check("evil $ws: ESC stripped from the printed hint", "\x1b" not in r.stderr)
check("evil $ws: remaining text still present", "evil" in r.stderr)
e.close()

# --- tab-close cleanup failure surfaces its own diagnostic (resume path) --- #
e = Env({
    "pane list": (jsonlib.dumps(
        {"result": {"panes": [{"tab_id": "w1:t1", "cwd": "/nowhere"}]}}), "", 0),
    "tab create": (jsonlib.dumps({"result": {"tab_id": "w1:t9"}}), "", 0),
    "tab close": ("", jsonlib.dumps(
        {"error": {"code": "tab_not_found", "message": "tab w1:t9 already gone"}}), 1),
})
r = e.run("resume", "t", str(e.worktree), "w1")
check("tab-close cleanup: exit 1", r.returncode == 1)
check("tab-close cleanup: close diag surfaced",
      "tab_not_found" in r.stderr and "also failed" in r.stderr)
check("tab-close cleanup: create diag also surfaced",
      "herdr tab create did not return a pane id" in r.stderr)
e.close()

# --- legacy no-selector launch: worker argv is the plugin-qualified skill --- #
# Guards the shadowing fix: an empty selector takes the legacy path, whose worker
# argv MUST be `claude -n <session> /work-system:continue` — the qualified form as
# ONE argv token (a bare `/continue` would be shadowed by a CC built-in/alias).
e = Env({
    "agent start": (jsonlib.dumps({"result": {"agent": {"pane_id": "w1:p5"}}}), "", 0),
    "pane move": (jsonlib.dumps(
        {"result": {"move_result": {"created_tab": {"tab_id": "w1:t9"}}}}), "", 0),
}, log_argv=True)
r = e.run("launch", "t", str(e.worktree), "w1", "", "sess1")  # "" selector = legacy path
argv = e.logged_argv()
check("legacy: exit 0", r.returncode == 0)
check("legacy: agent=claude on stdout", "agent=claude\n" in r.stdout)
check("legacy: worker argv carries the qualified skill as one token",
      "/work-system:continue" in argv)
check("legacy: bare /continue is NOT emitted", "/continue" not in argv)
if "/work-system:continue" in argv:
    i = argv.index("/work-system:continue")
    check("legacy: `-n <session>` precedes the skill token",
          argv[i - 2:i] == ["-n", "sess1"])
e.close()

# --- success path: stdout contract untouched by any of the above ----------- #
e = Env({
    "agent start": (jsonlib.dumps({"result": {"agent": {"pane_id": "w1:p5"}}}), "", 0),
    "pane move": (jsonlib.dumps(
        {"result": {"move_result": {"created_tab": {"tab_id": "w1:t9"}}}}), "", 0),
})
r = e.run("launch", "t", str(e.worktree), "w1")
check("success: exit 0", r.returncode == 0)
check("success: stdout contract unchanged",
      r.stdout == "pane=w1:p5\ntab=w1:t9\nmoved=yes\nagent=claude\n")
check("success: no stderr diagnostics", r.stderr == "")
e.close()


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("herdr-launch.sh (herdr_diag): all tests passed")
