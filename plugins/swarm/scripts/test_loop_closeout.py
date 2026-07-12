#!/usr/bin/env python3
"""Tests for loop-closeout.py — run standalone (`python3 test_loop_closeout.py`)
or via scripts/check-structure.py's "plugin tests" check.

Guards the drift-prone bits: the fixed evaluation ORDER of `step`'s four
termination reasons (a reorder is a silent regression), the `--pending` gate
that keeps the loop alive past every convergence reason but the cap, input
range-checking, and `box`'s rejection of bad input.
"""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("loop-closeout.py")
FAILS = []


def run(args):
    p = subprocess.run(["python3", str(SCRIPT), *args], capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def expect(name, args, *, out=None, code=0):
    rc, so, se = run(args)
    if code is not None and rc != code:
        FAILS.append(f"{name}: exit {rc} != {code} (stderr: {se})")
    if out is not None and so != out:
        FAILS.append(f"{name}: stdout {so!r} != {out!r}")


def step(**kw):
    return [x for k, v in kw.items() for x in (f"--{k}", str(v))]


# --- termination reasons fire in the fixed order 0-findings > nothing-agreed >
#     no-change > cap; each earlier one wins even when a later one also holds ---
expect("0-findings wins", ["step", *step(round=0, cap=10, findings=0, agreed=0, changed=0)],
       out="terminate=0-findings")
expect("nothing-agreed", ["step", *step(round=0, cap=10, findings=5, agreed=0, changed=0)],
       out="terminate=nothing-agreed")
expect("no-change", ["step", *step(round=1, cap=10, findings=5, agreed=3, changed=0)],
       out="terminate=no-change")
expect("cap", ["step", *step(round=9, cap=10, findings=5, agreed=3, changed=2)],
       out="terminate=cap")
expect("continue", ["step", *step(round=1, cap=10, findings=5, agreed=3, changed=2)],
       out="continue")

# --- --pending > 0 suppresses ALL three convergence reasons, but NOT cap ---
expect("pending blocks 0-findings", ["step", *step(round=0, cap=10, findings=0, agreed=0, changed=0, pending=1)],
       out="continue")
expect("pending blocks nothing-agreed", ["step", *step(round=0, cap=10, findings=5, agreed=0, changed=0, pending=1)],
       out="continue")
expect("pending blocks no-change", ["step", *step(round=1, cap=10, findings=5, agreed=2, changed=0, pending=1)],
       out="continue")
expect("cap fires despite pending", ["step", *step(round=9, cap=10, findings=5, agreed=0, changed=0, pending=1)],
       out="terminate=cap")
expect("pending defaults to 0", ["step", *step(round=1, cap=10, findings=5, agreed=2, changed=0)],
       out="terminate=no-change")

# --- input range checks: bad values fail loudly (exit 2), no stdout token ---
expect("cap<1 rejected", ["step", *step(round=0, cap=0, findings=3, agreed=2, changed=1)], out="", code=2)
expect("negative changed rejected", ["step", *step(round=0, cap=10, findings=3, agreed=2, changed=-1)], out="", code=2)

# --- box renders, and rejects malformed / negative counts ---
rc, so, se = run(["box", "15 7 4 4 5 4 3 1 1 0", "--reason", "cap"])
if rc != 0 or "R0" not in so or "│ 15 " not in so or "cap reached" not in so:
    FAILS.append(f"box render: rc={rc} out={so!r}")
expect("box rejects negative", ["box", "2 -1 0", "--reason", "cap"], out="", code=2)
expect("box rejects non-int", ["box", "2 x 0", "--reason", "cap"], out="", code=2)

if FAILS:
    print("loop-closeout tests FAILED:", file=sys.stderr)
    for f in FAILS:
        print("  -", f, file=sys.stderr)
    sys.exit(1)
print("loop-closeout: all tests passed")
