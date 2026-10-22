#!/usr/bin/env python3
"""Tests for telemetry-report.py.

The load-bearing property is NOT the formatting — it is that a review never
fails because of its own diagnostics, and that a near-wall call is impossible to
miss. Both are asserted here.
"""
import atexit
import importlib.util
import itertools
import json
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
REC_NEVER_RAN = '{"backend":"kimi","unit":"design","effort":"high","model":"m","prompt_bytes":1024,"seconds":0,"backend_rc":null,"adapter_rc":2,"timed_out":false}'
REC_REJECTED = '{"backend":"kimi","unit":"design","effort":"high","model":"m","prompt_bytes":1024,"seconds":30,"backend_rc":0,"adapter_rc":1,"timed_out":false}'

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

# A local schema/policy rejection happens AFTER a healthy backend response. It
# must not be mislabeled as "never reached the backend", which sends debugging
# toward install/auth instead of the malformed answer.
r = run([write([REC_NEVER_RAN, REC_REJECTED])])
check("null backend rc means the backend never ran", "never reached the backend" in r.stdout)
check("zero backend rc plus adapter failure means response rejected",
      "adapter rejected the response" in r.stdout)

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

# A call the backend ANSWERED but the adapter then rejected (empty output, schema
# validation) must not read as "never reached the backend": that sends the
# operator after a config problem instead of a malformed response, and hides that
# the run burned real wall-clock.
REC_POST = '{"backend":"codex","unit":"reach","effort":"high","model":"m","prompt_bytes":2048,"seconds":97,"timeout_seconds":540,"backend_rc":0,"adapter_rc":1,"timed_out":false}'
r = run([write([REC_POST])])
check("a post-call rejection names the backend as having returned",
      "adapter rejected the response" in r.stdout)
check("a post-call rejection reports the time it spent", "97s" in r.stdout)
check("a post-call rejection is not 'never reached the backend'",
      "never reached" not in r.stdout)

# The pre-call abort keeps its own wording (backend_rc absent).
REC_PRE = '{"backend":"grok","unit":"reach","effort":"high","model":"m","prompt_bytes":2048,"seconds":0,"adapter_rc":2,"timed_out":false}'
r = run([write([REC_PRE])])
check("a pre-call abort still says the backend was never reached",
      "never reached the backend" in r.stdout)

# A large prompt answered in seconds is the shape of a call that never received
# it (masked prompt file, empty transport). The out-of-band transport removed the
# old size/exec error, so this is the only place that smell is visible.
REC_FAST_BIG = '{"backend":"grok","unit":"breakage","effort":"high","model":"grok-4.6","prompt_bytes":204800,"seconds":3,"timeout_seconds":540,"backend_rc":0,"adapter_rc":0,"timed_out":false}'
r = run([write([REC_FAST_BIG])])
check("an implausibly fast large-prompt call is flagged",
      "verify the backend actually received the prompt" in r.stdout)
# A small prompt answered fast is normal — the smell is size-relative.
REC_FAST_SMALL = '{"backend":"grok","unit":"reach","effort":"high","model":"grok-4.6","prompt_bytes":1024,"seconds":3,"timeout_seconds":540,"backend_rc":0,"adapter_rc":0,"timed_out":false}'
r = run([write([REC_FAST_SMALL])])
check("a fast SMALL call is not flagged", "⚠️" not in r.stdout)

# The concrete model belongs in the report: the balance line only carries the
# family, so an unrendered model field left "which model reviewed this?"
# answerable only from the raw jsonl. (Same run as above — re-running an
# identical command would report one behaviour as two passes.)
check("the record's model is rendered", "grok-4.6" in r.stdout)

# A wall is a duration; 0 or negative made every percentage nonsense.
check("a non-positive --timeout-seconds is a usage error",
      run([write([REC_FAST]), "--timeout-seconds", "0"]).returncode == 2
      and run([write([REC_FAST]), "--timeout-seconds", "-600"]).returncode == 2)

# --- tool-attempt measurements ----------------------------------------------
legacy = json.loads(REC_FAST)
legacy_line = tr.render([legacy], 600)[0]
for count in (0, 1, 8):
    measured = dict(legacy, backend="kimi", tool_calls=count, tool_calls_complete=True)
    r = run([write([json.dumps(measured)])])
    check(f"available count {count} is rendered", f"{count} tools" in r.stdout)
    check(f"complete count {count} is not labeled partial", "partial" not in r.stdout)
    check("observed tool counts do not claim tokens or billing",
          "token" not in r.stdout.lower() and "bill" not in r.stdout.lower())

partial = dict(legacy, tool_calls=3, tool_calls_complete=False, backend_rc=13, adapter_rc=1)
r = run([write([json.dumps(partial)])])
check("partial failed-call count stays visible", "3 tools (partial)" in r.stdout)
check("partial metrics do not hide failure", "failed (rc=13)" in r.stdout)
check("count without completeness remains compatible",
      "2 tools" in tr.render([dict(legacy, tool_calls=2)], 600)[0])
for value in (None, -1, True, False, 1.5, "3", [], {}):
    check(f"missing or invalid count {value!r} never becomes zero",
          tr.render([dict(legacy, tool_calls=value)], 600)[0] == legacy_line)
check("legacy records with no count keep their existing rendering", "tools" not in legacy_line)

# --- grok turns vs findings --------------------------------------------------
def grok_rec(unit, turns, findings, secs, **extra):
    g = {"source": "stream", "complete": True, "num_turns": turns, "findings": findings,
         "structured_output": findings is not None, "stop_reason": "end_turn",
         "tool_calls": 5, "tool_kinds": {"read": 3, "shell": 1, "web": 1, "other": 0},
         "turn_s": {"min": 4.0, "median": 14.2, "max": 31.0}, **extra}
    return json.dumps({"backend": "grok", "unit": unit, "effort": "low", "model": "grok-4.7",
                       "prompt_bytes": 9000, "seconds": secs, "backend_rc": 0, "adapter_rc": 0,
                       "timed_out": False, "grok": g})

r = run([write([grok_rec("breakage", 20, 4, 280)]), write([grok_rec("threat", 8, 2, 112), REC_FAST])])
check("several files are read as one report", "grok:breakage" in r.stdout and "grok:threat" in r.stdout
      and "codex:threat" in r.stdout and r.returncode == 0)
section = r.stdout.split("grok turns vs findings:", 1)[-1]
check("the grok turns view is rendered", "grok turns vs findings:" in r.stdout)
rows = [line for line in section.splitlines() if line.strip().startswith("grok:")]
check("most turns first", len(rows) == 2 and "breakage" in rows[0])
check("turns, tool kinds and turn time are shown",
      " 20 " in rows[0] and "5 3/1/1" in rows[0] and "14.2/31.0" in rows[0])
check("aggregate counts findings per turn over reporting calls",
      "2 grok call(s): median 14 turns" in r.stdout and "0.21 findings per turn" in r.stdout)
r = run([write([grok_rec("reach", 3, None, 60, structured_output=False, complete=False,
                         stop_reason=None, turn_s=None, turn_s_unavailable="buffered")])])
check("a missing structuredOutput and a cut stream are named",
      "no structuredOutput" in r.stdout and "stream cut" in r.stdout)
check("unavailable turn time says why, never 0", "buffered" in r.stdout)
check("a single call prints no aggregate line", "grok call(s)" not in r.stdout)
r = run([write([REC_NEAR, REC_FAST])])
check("records without grok metrics add no grok view", "grok turns vs findings" not in r.stdout)
for bad in (None, [], "x", 3):
    check(f"a malformed grok field {bad!r} is skipped",
          tr.render_grok_turns([dict(json.loads(REC_NEAR), grok=bad)]) == [])

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
