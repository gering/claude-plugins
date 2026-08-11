#!/usr/bin/env python3
"""Tests for herdr-launch.sh — run standalone (`python3 test_herdr_launch.py`) or
via check-structure.py's "plugin tests" check.

A fake `herdr` binary on PATH returns canned JSON/text per subcommand (and can
return a DIFFERENT canned response per call, so a detection poll can change its
answer), so these tests exercise the REAL herdr-launch.sh end to end against the
exact integration surface skills call.

Covers:
  * error diagnostics — the JSON error-schema extraction, the ws_relevant-gated +
    clause-bounded stale-workspace hint (the real w9 incident, the "unrelated
    clause" false positive a loose AND-of-substrings would trip,
    case-insensitivity, an ERE-metachar workspace id), the double control-byte
    strip, and the tab-close cleanup diagnostic;
  * launch API capability detection — modern (--kind/--pane), legacy
    (--workspace/--cwd), and an unrecognizable help that must mutate NOTHING;
  * the legacy path's unchanged sequence and stdout contract;
  * the modern tab-create → agent-start path for claude/codex/grok, incl. exact
    kind/pane, argv boundaries, no duplicated executable and no `pane move`;
  * the modern pane-run path for wrapper workers (kimi): one-command delivery,
    bounded expected-kind detection, the seed-failure marker, wrong-kind and
    timeout → blocked=unverified with no retry;
  * rollback — exactly-once tab close on definitive failures, no rollback on
    ambiguous ones, and an unconfirmed rollback downgrading to unverified.
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

# --- canned herdr responses ------------------------------------------------ #
MODERN_HELP = (
    "Start a supported interactive agent in an existing pane\n\n"
    "Usage: herdr agent start <NAME> --kind <KIND> --pane <ID> [-- [AGENT_ARG]...]\n"
    "      --kind <KIND>\n      --pane <ID>\n      --timeout <MS>\n"
)
LEGACY_HELP = (
    "Start an agent\n\n"
    "Usage: herdr agent start <NAME> [OPTIONS] [-- [AGENT_ARG]...]\n"
    "      --workspace <WORKSPACE_ID>\n      --cwd <PATH>\n      --no-focus\n"
)
UNKNOWN_HELP = "Usage: herdr agent start <NAME>\n      --wat <WAT>\n"

TAB_CREATED = jsonlib.dumps({"result": {
    "root_pane": {"pane_id": "w1:p7", "tab_id": "w1:t7"},
    "tab": {"tab_id": "w1:t7", "label": "t"},
}})
AGENT_STARTED = jsonlib.dumps({"result": {"agent": {
    "agent": "claude", "pane_id": "w1:p7", "interactive_ready": True}}})
LEGACY_STARTED = jsonlib.dumps({"result": {"agent": {"pane_id": "w1:p5"}}})
LEGACY_MOVED = jsonlib.dumps({"result": {"move_result": {"created_tab": {"tab_id": "w1:t9"}}}})
SHELL_READY = jsonlib.dumps({"result": {"process_info": {
    "foreground_process_group_id": 4242, "shell_pid": 4242}}})
SHELL_BUSY = jsonlib.dumps({"result": {"process_info": {
    "foreground_process_group_id": 4243, "shell_pid": 4242}}})
BUSY_ERR = jsonlib.dumps({"error": {
    "code": "agent_pane_busy", "message": "pane w1:p7 is not at a shell prompt"}})
NO_PANE_ERR = jsonlib.dumps({"error": {
    "code": "agent_pane_not_found", "message": "agent target pane w1:p7 not found"}})


def pane_get(agent=None):
    p = {"pane_id": "w1:p7", "tab_id": "w1:t7", "agent_status": "unknown"}
    if agent:
        p["agent"] = agent
        p["agent_status"] = "idle"
    return jsonlib.dumps({"result": {"pane": p}})


# `pane list` for herdr-teardown.sh's close-tab verification: a populated list
# that does NOT contain w1:t7 reads as "gone" → the close is CONFIRMED.
PANE_LIST_GONE = jsonlib.dumps({"result": {"panes": [
    {"pane_id": "w1:p1", "tab_id": "w1:t1", "cwd": "/nowhere"}]}})
# ...and one that still holds it reads as "still-open" → rollback unconfirmed.
PANE_LIST_STILL = jsonlib.dumps({"result": {"panes": [
    {"pane_id": "w1:p7", "tab_id": "w1:t7", "cwd": "/nowhere"}]}})


def check(name, cond):
    if not cond:
        FAILS.append(name)


def shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def herdr_stub(cases, help_text=None):
    """Build a fake `herdr` from {"<argv1> <argv2>": (stdout, stderr, exit)}.

    A case value may instead be a LIST of such triples: the Nth call to that
    subcommand gets the Nth entry and the last one repeats — which is how a
    detection poll (`pane get` returning a shell, then the worker) or a bounded
    busy-retry is modelled.

    `help_text` answers `agent start --help` before anything else, so capability
    detection can be exercised for real instead of only via the env override.

    When $HERDR_ARGV_LOG is set, every call appends a `=== <subcmd>` header line
    followed by each received arg on its own line — so a test can assert the exact
    argv herdr-launch.sh execs (e.g. the worker argv after `agent start … --`)."""
    lines = ["#!/usr/bin/env bash"]
    if help_text is not None:
        lines += [
            'if [ "$1 $2" = "agent start" ] && [ "$3" = "--help" ]; then',
            f"  printf '%s' {shquote(help_text)}",
            "  exit 0",
            "fi",
        ]
    lines += [
        'if [ -n "${HERDR_ARGV_LOG:-}" ]; then',
        '  { printf \'=== %s\\n\' "$1 $2"; printf \'%s\\n\' "$@"; } >> "$HERDR_ARGV_LOG"',
        "fi",
        'case "$1 $2" in',
    ]
    for key, value in cases.items():
        seq = value if isinstance(value, list) else [value]
        lines.append(f'  "{key}")')
        if len(seq) == 1:
            out, err, rc = seq[0]
            if out:
                lines.append(f"    printf '%s' {shquote(out)}")
            if err:
                lines.append(f"    printf '%s' {shquote(err)} >&2")
            lines.append(f"    exit {rc}")
        else:
            slug = key.replace(" ", "_")
            lines += [
                f'    n=$(cat "$HERDR_STUB_STATE/{slug}" 2>/dev/null || echo 0)',
                "    n=$((n + 1))",
                f'    printf %s "$n" > "$HERDR_STUB_STATE/{slug}"',
                "    case $n in",
            ]
            for i, (out, err, rc) in enumerate(seq, start=1):
                pat = f"{i})" if i < len(seq) else "*)"
                lines.append(f"      {pat}")
                if out:
                    lines.append(f"        printf '%s' {shquote(out)}")
                if err:
                    lines.append(f"        printf '%s' {shquote(err)} >&2")
                lines.append(f"        exit {rc}")
                lines.append("        ;;")
            lines.append("    esac")
        lines.append("    ;;")
    lines.append('  *) echo "unhandled herdr stub call: $*" >&2; exit 9 ;;')
    lines.append("esac")
    return "\n".join(lines)


class Env:
    """A throwaway PATH with a fake `herdr` stub, plus a worktree dir for
    herdr-launch.sh to target.

    `api` pins $WORK_SYSTEM_HERDR_API so a test states which contract it is
    exercising; pass api=None to let the script probe `agent start --help` for
    real (then give the stub a `help_text`)."""

    def __init__(self, cases, log_argv=False, api="legacy", help_text=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.worktree = root / "wt"
        self.worktree.mkdir()
        state = root / "state"
        state.mkdir()
        bindir = root / "bin"
        bindir.mkdir()
        herdr = bindir / "herdr"
        herdr.write_text(herdr_stub(cases, help_text))
        herdr.chmod(0o755)
        # Stub the worker CLIs too, so a launch test never depends on which agents
        # happen to be installed and authenticated on the machine running it — the
        # registry's live probes would otherwise make these tests skip on CI and go
        # flaky locally (a slow `grok models` alone changes the outcome).
        for name, body in (
            ("codex", 'if [ "$1" = "login" ]; then exit 0; fi\nexit 0\n'),
            ("grok", 'if [ "$1" = "models" ]; then echo "grok-4.5"; fi\nexit 0\n'),
            ("kimi", 'if [ "$1" = "provider" ]; then '
                     "echo '{\"models\": {\"kimi-code/k3-256k\": {}}}'; fi\nexit 0\n"),
        ):
            stub = bindir / name
            stub.write_text("#!/bin/sh\n" + body)
            stub.chmod(0o755)
        auth = root / "auth.json"
        auth.write_text("{}\n")
        self.env = dict(os.environ)
        self.env["PATH"] = f"{bindir}:{self.env['PATH']}"
        self.env["GROK_AUTH_FILE"] = str(auth)
        self.env["KIMI_CREDENTIALS_FILE"] = str(auth)
        self.env["HERDR_STUB_STATE"] = str(state)
        # Keep every bounded wait fast and deterministic.
        self.env["WORK_SYSTEM_HERDR_RETRY_DELAY"] = "0"
        self.env["WORK_SYSTEM_HERDR_BUSY_TRIES"] = "3"
        self.env["WORK_SYSTEM_HERDR_READY_TRIES"] = "3"
        self.env["WORK_SYSTEM_HERDR_DETECT_TRIES"] = "3"
        if api is not None:
            self.env["WORK_SYSTEM_HERDR_API"] = api
        else:
            self.env.pop("WORK_SYSTEM_HERDR_API", None)
        self.argv_log = root / "argv.log" if log_argv else None
        if self.argv_log is not None:
            self.env["HERDR_ARGV_LOG"] = str(self.argv_log)

    def logged_argv(self):
        """Lines the herdr stub recorded (headers + one arg per line); [] if none."""
        if self.argv_log is None or not self.argv_log.exists():
            return []
        return self.argv_log.read_text().splitlines()

    def calls(self):
        """The recorded log as [(subcmd, [arg, ...]), ...], in call order."""
        out, cur = [], None
        for ln in self.logged_argv():
            if ln.startswith("=== "):
                cur = (ln[4:], [])
                out.append(cur)
            elif cur is not None:
                cur[1].append(ln)
        return out

    def call_args(self, subcmd):
        """Every recorded arg list for one subcommand, in order."""
        return [args for name, args in self.calls() if name == subcmd]

    def run(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env, capture_output=True, text=True, timeout=30,
        )

    def close(self):
        self.tmp.cleanup()


def kv(out):
    """Parse the launcher's key=value stdout into a dict."""
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


# A complete modern-path stub; individual tests override single keys.
def modern_cases(**over):
    cases = {
        "tab create": (TAB_CREATED, "", 0),
        "agent start": (AGENT_STARTED, "", 0),
        "pane list": (PANE_LIST_GONE, "", 0),
        "tab close": ('{"result":{"type":"ok"}}', "", 0),
    }
    cases.update(over)
    return cases


def wrapper_cases(**over):
    cases = modern_cases(**{
        "pane process-info": (SHELL_READY, "", 0),
        "pane run": ('{"result":{"type":"ok"}}', "", 0),
        "pane read": ("$ ", "", 0),
        "pane get": [(pane_get(), "", 0), (pane_get("kimi"), "", 0)],
    })
    cases.pop("agent start")
    cases.update(over)
    return cases


# ========================================================================== #
# error diagnostics (legacy path — unchanged behaviour)
# ========================================================================== #

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
    "agent start": (LEGACY_STARTED, "", 0),
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

# --- resume still parses a flat result.tab_id (pre-0.8 shape) -------------- #
# The create extractor is shared with the modern launch now; its fallbacks must
# keep working for the older response shape.
e = Env({
    "pane list": (jsonlib.dumps(
        {"result": {"panes": [{"tab_id": "w1:t1", "cwd": "/nowhere"}]}}), "", 0),
    "tab create": (jsonlib.dumps(
        {"result": {"tab_id": "w1:t9", "root_pane": {"pane_id": "w1:p9"}}}), "", 0),
    "pane run": ("", "", 0),
    "tab focus": ("", "", 0),
})
r = e.run("resume", "t", str(e.worktree), "w1")
check("resume: flat tab_id fallback still parses",
      kv(r.stdout).get("tab") == "w1:t9" and kv(r.stdout).get("pane") == "w1:p9")
check("resume: reports a fresh resume", kv(r.stdout).get("resumed") == "yes")
e.close()


# ========================================================================== #
# launch API capability detection
# ========================================================================== #

# --- modern help -> tab create first, agent start --kind, no pane move ----- #
e = Env(modern_cases(), log_argv=True, api=None, help_text=MODERN_HELP)
r = e.run("launch", "t", str(e.worktree), "w1")
names = [n for n, _ in e.calls()]
check("detect modern: exit 0", r.returncode == 0)
check("detect modern: tab create precedes agent start",
      "tab create" in names and "agent start" in names
      and names.index("tab create") < names.index("agent start"))
check("detect modern: no pane move", "pane move" not in names)
check("detect modern: agent start carries --kind",
      any("--kind" in a for a in e.call_args("agent start")))
e.close()

# --- legacy help -> the untouched agent start --workspace + pane move ------ #
e = Env({"agent start": (LEGACY_STARTED, "", 0), "pane move": (LEGACY_MOVED, "", 0)},
        log_argv=True, api=None, help_text=LEGACY_HELP)
r = e.run("launch", "t", str(e.worktree), "w1")
names = [n for n, _ in e.calls()]
check("detect legacy: exit 0", r.returncode == 0)
check("detect legacy: no tab create", "tab create" not in names)
check("detect legacy: pane move used", "pane move" in names)
check("detect legacy: agent start carries --workspace",
      any("--workspace" in a for a in e.call_args("agent start")))
check("detect legacy: stdout contract unchanged",
      r.stdout == "pane=w1:p5\ntab=w1:t9\nmoved=yes\nagent=claude\n")
e.close()

# --- unrecognizable help -> fail BEFORE any mutation ----------------------- #
e = Env(modern_cases(), log_argv=True, api=None, help_text=UNKNOWN_HELP)
r = e.run("launch", "t", str(e.worktree), "w1")
names = [n for n, _ in e.calls()]
check("detect unknown: exit 1", r.returncode == 1)
check("detect unknown: nothing on stdout", r.stdout == "")
check("detect unknown: diagnostic names both contracts",
      "--kind/--pane" in r.stderr and "--workspace/--cwd" in r.stderr)
check("detect unknown: NOTHING was created or started",
      "tab create" not in names and "agent start" not in names
      and "pane run" not in names)
e.close()


# ========================================================================== #
# legacy path (herdr 0.7.0-0.7.4) — output contract must not drift
# ========================================================================== #

# --- legacy no-selector launch: worker argv is the plugin-qualified skill --- #
# Guards the shadowing fix: an empty selector takes the legacy path, whose worker
# argv MUST be `claude -n <session> /work-system:continue` — the qualified form as
# ONE argv token (a bare `/continue` would be shadowed by a CC built-in/alias).
e = Env({"agent start": (LEGACY_STARTED, "", 0), "pane move": (LEGACY_MOVED, "", 0)},
        log_argv=True)
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
e = Env({"agent start": (LEGACY_STARTED, "", 0), "pane move": (LEGACY_MOVED, "", 0)})
r = e.run("launch", "t", str(e.worktree), "w1")
check("success: exit 0", r.returncode == 0)
check("success: stdout contract unchanged",
      r.stdout == "pane=w1:p5\ntab=w1:t9\nmoved=yes\nagent=claude\n")
check("success: no stderr diagnostics", r.stderr == "")
e.close()


# ========================================================================== #
# modern path — native workers (herdr_mode=agent-start)
# ========================================================================== #

# --- claude (no selector), codex and grok all start through --kind --------- #
for selector, kind, expect_head, expect_len in (
    ("", "claude", ["-n", "sess1", "/work-system:continue"], 3),
    # codex/grok take `-m <model> <bootstrap-prompt>`; the prompt must survive as
    # exactly ONE word, which is what the length assertion pins down.
    ("--codex", "codex", ["-m", "gpt-5.6-terra"], 3),
    ("--grok", "grok", ["-m", "grok-4.5"], 3),
):
    started = jsonlib.dumps({"result": {"agent": {"agent": kind, "pane_id": "w1:p7"}}})
    e = Env(modern_cases(**{"agent start": (started, "", 0)}),
            log_argv=True, api="modern")
    r = e.run("launch", "t", str(e.worktree), "w1", selector, "sess1")
    label = selector or "claude(default)"
    starts = e.call_args("agent start")
    check(f"modern {label}: exit 0", r.returncode == 0)
    check(f"modern {label}: stdout is the stable contract",
          r.stdout == f"pane=w1:p7\ntab=w1:t7\nmoved=yes\nagent={kv(r.stdout).get('agent')}\n")
    check(f"modern {label}: moved=yes (dedicated tab, no physical move)",
          kv(r.stdout).get("moved") == "yes")
    check(f"modern {label}: exactly one agent start", len(starts) == 1)
    if starts:
        a = starts[0]
        check(f"modern {label}: --kind is exactly the declared kind",
              a[a.index("--kind") + 1] == kind)
        check(f"modern {label}: --pane is the tab's root pane",
              a[a.index("--pane") + 1] == "w1:p7")
        tail = a[a.index("--") + 1:] if "--" in a else []
        check(f"modern {label}: the executable word is NOT repeated after --",
              kind not in tail)
        check(f"modern {label}: native args keep order and boundaries",
              tail[:len(expect_head)] == expect_head and len(tail) == expect_len)
    check(f"modern {label}: no pane move", "pane move" not in [n for n, _ in e.calls()])
    check(f"modern {label}: tab created with --no-focus",
          any("--no-focus" in c for c in e.call_args("tab create")))
    e.close()

# --- tab create receives the glyph-stamped label and the worktree cwd ------ #
e = Env(modern_cases(), log_argv=True, api="modern")
r = e.run("launch", "mylabel", str(e.worktree), "w1")
create = e.call_args("tab create")[0]
check("modern: tab create targets the worktree",
      create[create.index("--cwd") + 1] == str(e.worktree))
check("modern: tab create passes the workspace",
      create[create.index("--workspace") + 1] == "w1")
check("modern: agent name stays plain (no glyph)",
      e.call_args("agent start")[0][2] == "mylabel")
e.close()

# --- agent_pane_busy is retried, bounded, and ONLY that error -------------- #
e = Env(modern_cases(**{"agent start": [
    ("", BUSY_ERR, 1), ("", BUSY_ERR, 1), (AGENT_STARTED, "", 0)]}),
    log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
check("busy retry: eventually succeeds", r.returncode == 0 and "moved=yes" in r.stdout)
check("busy retry: retried exactly until it took", len(e.call_args("agent start")) == 3)
check("busy retry: the tab was not rolled back", "tab close" not in [n for n, _ in e.calls()])
e.close()

# --- busy forever -> definitive: nothing started, tab rolled back ---------- #
e = Env(modern_cases(**{"agent start": ("", BUSY_ERR, 1)}), log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
closes = e.call_args("tab close")
check("busy forever: exit 1", r.returncode == 1)
check("busy forever: bounded (tries+1 attempts, no infinite loop)",
      len(e.call_args("agent start")) == 4)
check("busy forever: tab closed exactly once", len(closes) == 1)
check("busy forever: the closed tab is the one we created",
      closes and closes[0][2] == "w1:t7")
check("busy forever: no stdout (caller shows the manual block)", r.stdout == "")
e.close()

# --- a pre-start rejection is definitive -> rollback, exit 1 --------------- #
e = Env(modern_cases(**{"agent start": ("", NO_PANE_ERR, 1)}), log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
check("definitive start failure: exit 1", r.returncode == 1)
check("definitive start failure: herdr's own error is relayed",
      "agent_pane_not_found" in r.stderr)
check("definitive start failure: tab closed exactly once",
      len(e.call_args("tab close")) == 1)
check("definitive start failure: no stale-workspace hint (no --workspace sent)",
      "is not a valid workspace" not in r.stderr)
e.close()

# --- a usage error (exit 2, plain text) is definitive too ------------------ #
e = Env(modern_cases(**{"agent start": ("", "unsupported interactive agent kind: nope", 2)}),
        log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
check("usage error: exit 1", r.returncode == 1)
check("usage error: tab closed exactly once", len(e.call_args("tab close")) == 1)
check("usage error: the raw text is relayed", "unsupported interactive agent kind" in r.stderr)
e.close()

# --- an UNRECOGNIZED error is ambiguous: never rolled back, never retried -- #
weird = jsonlib.dumps({"error": {"code": "agent_start_timeout", "message": "gave up waiting"}})
e = Env(modern_cases(**{"agent start": ("", weird, 1)}), log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
d = kv(r.stdout)
check("ambiguous start: exit 0", r.returncode == 0)
check("ambiguous start: blocked=unverified is the FIRST key",
      r.stdout.startswith("blocked=unverified\n"))
check("ambiguous start: pane/tab/agent reported for inspection",
      d.get("pane") == "w1:p7" and d.get("tab") == "w1:t7" and d.get("agent") == "claude")
check("ambiguous start: no moved= key (callers must branch on blocked first)",
      "moved" not in d)
check("ambiguous start: the tab is LEFT ALONE", "tab close" not in [n for n, _ in e.calls()])
check("ambiguous start: tried exactly once (no retry)",
      len(e.call_args("agent start")) == 1)
e.close()

# --- pane-id mismatch on a "successful" start is unverified, not rollback -- #
mismatch = jsonlib.dumps({"result": {"agent": {"pane_id": "w1:pOTHER"}}})
e = Env(modern_cases(**{"agent start": (mismatch, "", 0)}), log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
check("pane mismatch: blocked=unverified", r.stdout.startswith("blocked=unverified\n"))
check("pane mismatch: exit 0", r.returncode == 0)
check("pane mismatch: names both panes", "w1:pOTHER" in r.stderr and "w1:p7" in r.stderr)
check("pane mismatch: tab left alone (something may be running)",
      "tab close" not in [n for n, _ in e.calls()])
e.close()

# --- a start response with NO pane id is the same mismatch case ------------ #
e = Env(modern_cases(**{"agent start": ('{"result":{"agent":{}}}', "", 0)}), api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
check("missing pane id: blocked=unverified", r.stdout.startswith("blocked=unverified\n"))
e.close()

# --- partial tab create: a pane-less response must not orphan its tab ------ #
e = Env(modern_cases(**{"tab create": (jsonlib.dumps({"result": {"tab": {"tab_id": "w1:t7"}}}), "", 0)}),
        log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
check("partial create: exit 1", r.returncode == 1)
check("partial create: nothing was started", "agent start" not in [n for n, _ in e.calls()])
check("partial create: the orphan tab is closed exactly once",
      len(e.call_args("tab close")) == 1)
e.close()

# --- an unusable tab create (no ids at all) closes nothing ----------------- #
e = Env(modern_cases(**{"tab create": ("", jsonlib.dumps(
    {"error": {"code": "workspace_not_found", "message": "workspace w9 not found"}}), 1)}),
    log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w9")
check("failed create: exit 1", r.returncode == 1)
check("failed create: herdr's error is relayed", "workspace_not_found" in r.stderr)
check("failed create: stale-workspace hint IS offered here (--workspace was sent)",
      "HERDR_WORKSPACE_ID=w9 is not a valid workspace" in r.stderr)
check("failed create: no tab close attempted", "tab close" not in [n for n, _ in e.calls()])
e.close()

# --- a rollback that cannot be confirmed downgrades to unverified ---------- #
e = Env(modern_cases(**{
    "agent start": ("", NO_PANE_ERR, 1),
    "pane list": (PANE_LIST_STILL, "", 0),   # tab still there after the close
}), log_argv=True, api="modern")
r = e.run("launch", "t", str(e.worktree), "w1")
check("unconfirmed rollback: exit 0 with blocked=unverified",
      r.returncode == 0 and r.stdout.startswith("blocked=unverified\n"))
check("unconfirmed rollback: says the tab could not be confirmed closed",
      "could not confirm" in r.stderr)
check("unconfirmed rollback: still closed only once", len(e.call_args("tab close")) == 1)
e.close()


# ========================================================================== #
# modern path — wrapper workers (herdr_mode=pane-run)
# ========================================================================== #

def kimi_run(cases, log_argv=True):
    """Run a --kimi launch against a wrapper stub; returns (env, result)."""
    env = Env(cases, log_argv=log_argv, api="modern")
    res = env.run("launch", "t", str(env.worktree), "w1", "--kimi", "sess1")
    check("wrapper: the stubbed kimi resolves (not an availability skip)",
          res.returncode != 3)
    return env, res


# --- happy path: one pane run, then detection of the expected kind --------- #
e, r = kimi_run(wrapper_cases())
runs = e.call_args("pane run")
check("wrapper: exit 0", r.returncode == 0)
check("wrapper: stdout is the stable contract",
      kv(r.stdout).get("moved") == "yes" and kv(r.stdout).get("pane") == "w1:p7")
check("wrapper: exactly one pane run", len(runs) == 1)
if runs:
    # `pane run <PANE> <COMMAND>` — the whole wrapper must arrive as ONE word,
    # not re-split or re-quoted by the launcher.
    check("wrapper: the command is a single argv word", len(runs[0]) == 4)
    cmd = runs[0][3]
    check("wrapper: the registry's argv_shell is sent verbatim",
          cmd.startswith("sh -c ") and "exec kimi -c --auto" in cmd)
    check("wrapper: no agent start on this path",
          "agent start" not in [n for n, _ in e.calls()])
check("wrapper: waited for the shell prompt before typing",
      len(e.call_args("pane process-info")) >= 1)
check("wrapper: the tab was not rolled back",
      "tab close" not in [n for n, _ in e.calls()])
e.close()

# --- detection never sees the worker -> unverified, no retry, tab kept ----- #
e, r = kimi_run(wrapper_cases(**{"pane get": (pane_get(), "", 0)}))
check("wrapper timeout: exit 0 with blocked=unverified",
      r.returncode == 0 and r.stdout.startswith("blocked=unverified\n"))
check("wrapper timeout: bounded polling", len(e.call_args("pane get")) == 3)
check("wrapper timeout: the command was sent exactly once (no second start)",
      len(e.call_args("pane run")) == 1)
check("wrapper timeout: the tab is left for inspection",
      "tab close" not in [n for n, _ in e.calls()])
e.close()

# --- a DIFFERENT kind is detected -> unverified, not success --------------- #
e, r = kimi_run(wrapper_cases(**{"pane get": (pane_get("claude"), "", 0)}))
check("wrapper wrong kind: blocked=unverified", r.stdout.startswith("blocked=unverified\n"))
check("wrapper wrong kind: names what was detected",
      "'claude'" in r.stderr and "'kimi'" in r.stderr)
check("wrapper wrong kind: stops immediately", len(e.call_args("pane get")) == 1)
check("wrapper wrong kind: no rollback", "tab close" not in [n for n, _ in e.calls()])
e.close()

# --- the seed-failure marker is DEFINITIVE -> roll the tab back ------------ #
e, r = kimi_run(wrapper_cases(**{
    "pane get": (pane_get(), "", 0),
    "pane read": ("$ sh -c ...\n[work-system] WORKER_SEED_FAILED: kimi seed exited 1 - "
                  "TASK.md was NOT started and no session was opened.\n$ ", "", 0),
}))
check("wrapper seed failure: exit 1", r.returncode == 1)
check("wrapper seed failure: names the marker", "WORKER_SEED_FAILED" in r.stderr)
check("wrapper seed failure: says TASK.md was not started",
      "TASK.md was not started" in r.stderr)
check("wrapper seed failure: tab closed exactly once",
      len(e.call_args("tab close")) == 1)
check("wrapper seed failure: no stdout", r.stdout == "")
e.close()

# --- the ECHOED command must not read as a seed failure -------------------- #
# A pane shows the command it was given. The wrapper therefore assembles its marker
# at runtime, and this pins the consequence: a pane whose only content is that
# command must still be treated as "starting", never as a failed seed.
e, r = kimi_run(wrapper_cases(**{
    "pane read": ("$ sh -c 'if kimi -m \"$1\" -p \"$2\"; then exec kimi -c --auto; "
                  'else rc=$?; m=WORKER_SEED; echo; echo "[work-system] ${m}_FAILED: '
                  "kimi seed exited $rc - TASK.md was NOT started and no session was "
                  "opened.\"; exit $rc; fi' kimi-worker kimi-code/k3-256k '...'\n", "", 0),
}))
check("echoed command: still a normal launch, not a seed failure", r.returncode == 0)
check("echoed command: the tab was NOT rolled back",
      "tab close" not in [n for n, _ in e.calls()])
e.close()

# --- the shell never reaches a prompt -> nothing typed, tab rolled back ---- #
e, r = kimi_run(wrapper_cases(**{"pane process-info": (SHELL_BUSY, "", 0)}))
check("wrapper shell busy: exit 1", r.returncode == 1)
check("wrapper shell busy: nothing was typed into the pane",
      "pane run" not in [n for n, _ in e.calls()])
check("wrapper shell busy: tab closed exactly once",
      len(e.call_args("tab close")) == 1)
check("wrapper shell busy: bounded wait", len(e.call_args("pane process-info")) == 3)
e.close()

# --- an unreadable readiness answer degrades to "proceed", not to a block -- #
e, r = kimi_run(wrapper_cases(**{"pane process-info": ("not json at all", "", 0)}))
check("wrapper readiness unknown: still launches", r.returncode == 0)
check("wrapper readiness unknown: asked once, then proceeded",
      len(e.call_args("pane process-info")) == 1)
e.close()

# --- pane run itself is rejected -> definitive, tab rolled back ------------ #
e, r = kimi_run(wrapper_cases(**{
    "pane run": ("", jsonlib.dumps(
        {"error": {"code": "pane_not_found", "message": "pane w1:p7 not found"}}), 1)}))
check("wrapper run rejected: exit 1", r.returncode == 1)
check("wrapper run rejected: tab closed exactly once",
      len(e.call_args("tab close")) == 1)
check("wrapper run rejected: never polled for detection",
      "pane get" not in [n for n, _ in e.calls()])
e.close()


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("herdr-launch.sh: all tests passed")
