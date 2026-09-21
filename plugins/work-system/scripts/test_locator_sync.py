#!/usr/bin/env python3
"""Pins work-system's insights locator in sync with pr-flow's work-system locator.

Two plugins each need to find the other, and they CANNOT share a file: a shared
library would make one depend on the other, which is the exact coupling the
locator exists to avoid (plugins stay independently installable — see
.claude/knowledge/architecture/skill-composition.md §4). So the duplication is
deliberate, and this test is the guard that keeps it from rotting.

It pins the part that actually broke once: the SemVer `vkey` comparator embedded
in both. Its own comment records a prerelease-ordering bug already fixed in
pr-flow; the next fix (prerelease ranking, a 2-component version, a manifest
schema change) must land in BOTH files or the one left behind silently resolves
the wrong installed version — pr-flow running the enabled work-system while
/close runs a rolled-back insights and calls it "unusable".

Deliberately NOT pinned: the surrounding shell. The two differ by design (plugin
name, anchor variable, which relative directory is tried first), and asserting
byte-equality there would fail on every legitimate edit — a guard whose false
positives cost more than the case it guards.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent.parent
SOURCES = {
    "work-system/insights-handoff.sh": HERE / "insights-handoff.sh",
    "pr-flow/lib-work-system.sh": REPO / "plugins" / "pr-flow" / "scripts" / "lib-work-system.sh",
}

VKEY = re.compile(r"^def vkey\(v\):\n(?:[ \t].*\n|\n)*", re.M)
FAILS = []


def comparator(path):
    """The `vkey` function body, comments stripped (they explain, they don't run)."""
    m = VKEY.search(path.read_text())
    if not m:
        return None
    lines = []
    for line in m.group(0).splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


bodies = {}
for label, path in SOURCES.items():
    if not path.is_file():
        FAILS.append(f"{label}: file not found at {path}")
        continue
    body = comparator(path)
    if body is None:
        FAILS.append(f"{label}: no `def vkey(v):` found — did the locator change shape?")
        continue
    bodies[label] = body

if len(bodies) == len(SOURCES):
    labels = list(bodies)
    a, b = bodies[labels[0]], bodies[labels[1]]
    if a != b:
        FAILS.append(
            "the two plugin locators' SemVer comparators have drifted — fix BOTH "
            f"({labels[0]} and {labels[1]}), they cannot share a file:\n"
            + "\n".join(
                f"  {labels[0]}| {x}\n  {labels[1]}| {y}"
                for x, y in zip(a.splitlines(), b.splitlines()) if x != y
            )
        )
    # A comparator that no longer reflects SemVer precedence is the actual bug
    # class; assert the two properties the fix established, so a rewrite that
    # keeps both copies identical but wrong still fails here.
    elif "split(\"+\", 1)[0]" not in a or "partition(\"-\")" not in a:
        FAILS.append("the comparator no longer strips build metadata / splits the "
                     "prerelease — SemVer precedence is not being implemented")

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("plugin locator sync: all tests passed")
