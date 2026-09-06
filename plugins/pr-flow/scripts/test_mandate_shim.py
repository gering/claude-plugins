#!/usr/bin/env python3
"""Tests for pr-flow's mandate-shim.sh and the lib-work-system.sh locator it
shares with refresh-task-glyphs.sh — run standalone or via
scripts/check-structure.py's "plugin tests" check.

The property that matters: pr-flow must never turn "work-system isn't
installed" into "you may not do that". Exit 1 (the user decided against it) and
exit 3 (nobody was ever asked) stay distinguishable through the shim, and a
missing work-system yields 3, never 1 and never a silent 0.

The dev layout (plugins/pr-flow + plugins/work-system as siblings) is the one
resolution layer that can be exercised hermetically; the manifest and cache
layers depend on the user's real ~/.claude and are covered by the glyph shim's
production use.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SHIM = HERE / "mandate-shim.sh"
REAL_MANDATE = HERE.parent.parent / "work-system" / "scripts" / "mandate.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def layout(with_work_system=True):
    """A fake plugin tree: <root>/pr-flow (+ optionally <root>/work-system)."""
    root = Path(tempfile.mkdtemp())
    prf = root / "pr-flow" / "scripts"
    prf.mkdir(parents=True)
    for f in ("mandate-shim.sh", "lib-work-system.sh"):
        (prf / f).write_text((HERE / f).read_text())
    if with_work_system:
        ws = root / "work-system" / "scripts"
        ws.mkdir(parents=True)
        (ws / "mandate.sh").write_text(REAL_MANDATE.read_text())
    return root


def run(root, *args, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(root / "pr-flow"))
    return subprocess.run(
        ["bash", str(root / "pr-flow" / "scripts" / "mandate-shim.sh"), *args],
        capture_output=True, text=True, env=env, cwd=cwd,
    )


def make_repo():
    repo = Path(tempfile.mkdtemp())
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


# --- work-system present: verdicts pass through unchanged -------------------
root = layout()
repo = make_repo()
subprocess.run(
    ["bash", str(REAL_MANDATE), "init", str(repo), "task=t", "authorized_by=user",
     "allow=commit,open-pr,agreed-fixes", "deny=merge", "review_budget=1"],
    capture_output=True, check=True,
)

r = run(root, "allows", "open-pr", str(repo))
check("allowed action passes through as 0", r.returncode == 0)
check("allowed action keeps its verdict line", "allowed" in r.stdout)

r = run(root, "allows", "merge", str(repo))
check("denied action passes through as 1", r.returncode == 1)
check("denied stays distinguishable from unknown", "denied" in r.stdout)

r = run(root, "allows", "deploy", str(repo))
check("unlisted action passes through as 1", r.returncode == 1)
check("unlisted says unlisted", "unlisted" in r.stdout)

r = run(root, "show", str(repo))
check("show passes through", r.returncode == 0)
check("show carries the mandate fields", "mandate_exists=yes" in r.stdout)

r = run(root, "round", str(repo))
check("round passes through", r.returncode == 0)
check("round exhausts a 1-round budget", "review_budget_exhausted=yes" in r.stdout)

# A dir argument is optional — the shim must resolve $PWD like the real script.
r = run(root, "allows", "commit", cwd=str(repo))
check("no dir argument uses the cwd", r.returncode == 0)

# --- a repo with no mandate: unknown, not denied ----------------------------
bare = make_repo()
r = run(root, "allows", "commit", str(bare))
check("missing mandate is exit 3", r.returncode == 3)
check("missing mandate says no-mandate", "no-mandate" in r.stdout)

# --- work-system absent: still unknown, never denied, never silent ----------
lonely = layout(with_work_system=False)
r = run(lonely, "allows", "open-pr", str(repo))
check("missing work-system is exit 3, not 1", r.returncode == 3)
check("missing work-system is not a silent success", r.returncode != 0)
check("missing work-system names itself", "no-work-system" in r.stdout)

# An installed-but-older work-system (no mandate.sh) is the same case.
(lonely / "work-system" / "scripts").mkdir(parents=True)
(lonely / "work-system" / "scripts" / "herdr-tab-glyph.sh").write_text("#!/bin/sh\n")
r = run(lonely, "allows", "open-pr", str(repo))
check("work-system without mandate.sh is exit 3", r.returncode == 3)
check("work-system without mandate.sh names itself", "no-work-system" in r.stdout)

# --- usage ------------------------------------------------------------------
check("unknown verb exits 2", run(root, "bogus").returncode == 2)
check("no verb exits 2", run(root).returncode == 2)
check("a usage error is not mistaken for a verdict",
      "verdict=" not in run(root, "bogus").stdout)


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("mandate-shim.sh: all tests passed")
