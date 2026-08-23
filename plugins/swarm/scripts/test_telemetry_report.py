#!/usr/bin/env python3
"""Tests for telemetry-report.py.

The load-bearing property is NOT the formatting — it is that a review never
fails because of its own diagnostics, and that a near-wall call is impossible to
miss. Both are asserted here.
"""
import atexit
import importlib.util
import itertools
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "telemetry-report.py"

spec = importlib.util.spec_from_file_location("telemetry_report", SCRIPT)
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args, capture_output=True, text=True
    )


# One directory for every fixture, removed when the process exits. The previous
# form used delete=False and never unlinked, so each run left ~11 .jsonl files
# behind in TMPDIR — a test suite that quietly accumulates garbage.
_FIXTURES = tempfile.TemporaryDirectory()
atexit.register(_FIXTURES.cleanup)
_seq = itertools.count()


def write(lines):
    path = pathlib.Path(_FIXTURES.name) / f"fixture-{next(_seq)}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


REC_FAST = '{"backend":"codex","unit":"threat","effort":"high","model":"m","prompt_bytes":1024,"seconds":30,"backend_rc":0,"adapter_rc":0,"timed_out":false}'
REC_NEAR = '{"backend":"grok","unit":"breakage","effort":"high","model":"m","prompt_bytes":42665,"seconds":374,"backend_rc":0,"adapter_rc":0,"timed_out":false}'
REC_DEAD = '{"backend":"grok","unit":"threat","effort":"high","model":"m","prompt_bytes":42665,"seconds":600,"backend_rc":124,"adapter_rc":1,"timed_out":true}'

# --- diagnostics must never fail a review -----------------------------------
r = run([str(HERE / "does-not-exist.jsonl")])
check("missing file exits 0", r.returncode == 0)
check("missing file prints nothing on stdout", r.stdout.strip() == "")

r = run([write(["", "   ", "not json", "[1,2,3]"])])
check("garbage-only file exits 0", r.returncode == 0)
check("garbage-only file prints nothing on stdout", r.stdout.strip() == "")

r = run([write([REC_FAST, "not json", REC_NEAR])])
check("a malformed line does not drop the valid ones", r.returncode == 0)
check("valid records still rendered around a malformed line",
      "codex:threat" in r.stdout and "grok:breakage" in r.stdout)
check("skipped lines are disclosed, not silently swallowed", "malformed" in r.stdout)

# --- the near-wall signal ----------------------------------------------------
r = run([write([REC_FAST, REC_NEAR])])
check("a surviving near-wall call is flagged", "62%" in r.stdout)
check("a fast call is not flagged", "30s" in r.stdout and r.stdout.count("⚠️") == 1)
check("longest call is listed first",
      r.stdout.index("grok:breakage") < r.stdout.index("codex:threat"))

# A run entirely below the threshold must stay quiet — a warning that fires
# always is a warning nobody reads.
r = run([write([REC_FAST])])
check("an all-fast run raises no warning", "⚠️" not in r.stdout)

# --- timeouts ---------------------------------------------------------------
r = run([write([REC_DEAD])])
check("a timed-out call is marked", "TIMED OUT" in r.stdout)
check("a timeout names the lost coverage", "reviewed without grok" in r.stdout)

# The wall is configurable, and the percentages must follow it: with a 1200s
# wall the same 374s call is only 31% and must NOT be flagged.
r = run([write([REC_NEAR]), "--timeout-seconds", "1200"])
check("threshold follows --timeout-seconds", "⚠️" not in r.stdout)
r = run([write([REC_NEAR]), "--timeout-seconds", "500"])
check("a tighter wall flags the same call", "⚠️" in r.stdout)

# A record that carries its OWN wall wins over the CLI fallback: SWARM_TIMEOUT is
# overridable, so reporting "% of 600s" for a call that ran under a different
# limit would be a plain lie about how close it came.
REC_OWN_WALL = '{"backend":"grok","unit":"breakage","effort":"low","model":"m","prompt_bytes":1024,"seconds":90,"timeout_seconds":120,"backend_rc":0,"adapter_rc":0,"timed_out":false}'
r = run([write([REC_OWN_WALL])])
check("per-record wall beats the default", "120s wall" in r.stdout)
check("percentage uses the record's own wall", "75%" in r.stdout)
r = run([write([REC_OWN_WALL]), "--timeout-seconds", "9999"])
check("an explicit --timeout-seconds does not override a record's own wall",
      "120s wall" in r.stdout)
# The fallback and a record's own wall must coexist in ONE run: REC_NEAR (no wall
# of its own) follows --timeout-seconds, REC_OWN_WALL keeps its 120s. The previous
# version of this check re-ran the byte-identical command from 20 lines up, so it
# reported the same behaviour twice and never exercised the mixed case.
r = run([write([REC_NEAR, REC_OWN_WALL]), "--timeout-seconds", "1200"])
# Each record is judged against ITS OWN wall: REC_OWN_WALL (90s of 120s = 75%)
# flags, REC_NEAR (374s of the 1200s fallback = 31%) does not. One run, two
# different walls — which is the property a second identical run could not show.
check("mixed run: the record's own wall is kept", "120s wall" in r.stdout)
check("mixed run: exactly the over-threshold record flags", r.stdout.count("⚠️") == 1)
check("mixed run: the fallback keeps the other record quiet",
      "374s" in r.stdout and "62%" not in r.stdout)

# --- usage ------------------------------------------------------------------
check("no args is a usage error", run([]).returncode == 2)
check("bad --timeout-seconds is a usage error",
      run([write([REC_FAST]), "--timeout-seconds", "abc"]).returncode == 2)
check("unknown flag is a usage error",
      run([write([REC_FAST]), "--nope"]).returncode == 2)

# --- unit-level -------------------------------------------------------------
check("_secs tolerates a missing/garbage value",
      tr._secs({}) == 0 and tr._secs({"seconds": "x"}) == 0)
check("label falls back when unit is absent", tr.label({"backend": "grok"}) == "grok:-")

if FAILS:
    print("telemetry-report tests FAILED:")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("telemetry-report: all tests passed")
