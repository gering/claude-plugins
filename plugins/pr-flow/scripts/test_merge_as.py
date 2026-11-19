#!/usr/bin/env python3
"""Contract tests for /pr-flow:merge --as <app>.

The flag lives in SKILL.md prose; these assertions pin the parts whose drift
would merge under the wrong identity or an unchecked head.
"""

import sys
from pathlib import Path

SKILL = " ".join((Path(__file__).parent.parent / "skills" / "merge" / "SKILL.md").read_text().split())
FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def at(needle):
    i = SKILL.find(needle)
    assert i >= 0, f"missing anchor: {needle!r}"
    return i


args = SKILL[at("## Arguments"):at("## Instructions")]
step0 = SKILL[at("0. **Preflight**"):at("1. **Identify PR**")]
step14 = SKILL[at("14. **Execute merge**"):at("15. **Post-merge actions**")]
step15 = SKILL[at("15. **Post-merge actions**"):at("16. **Final summary**")]

check("--as only from the user's typed line", "only** from the command line the user typed" in args)
check("never from a mandate or a message", "Never take it from a mandate" in args)
check("app name is a slug", "`^[a-z0-9][a-z0-9-]*$`" in args)
check("any other argument stops", "`/merge` only takes `--as <app>`" in args)
check("re-run hints keep --as", "append ` --as <MERGE_AS>`" in args)
check("probe runs in preflight, before step 1 can flip a draft",
      "gh __shim-probe" in step0 and "exit 0" in step0)
check("no fallback without --as", "Never fall back to a merge without `--as`" in step0)
check("prefix on the merge command only",
      "prefix **that one command** with `GH_APP_MERGE='<MERGE_AS>' `" in step14
      and "No other command gets the prefix" in step14)
check("--as always binds the head",
      "With `MERGE_AS`, `--match-head-commit` is **always** added" in step14)
check("the bound head must equal the local HEAD", "must equal `git rev-parse HEAD`" in step14)
check("mergedBy compared only on MERGED", "Only on `MERGED` compare the login" in step15)
check("a queued merge skips cleanup", "no branch cleanup" in step15)

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("merge --as: all tests passed")
