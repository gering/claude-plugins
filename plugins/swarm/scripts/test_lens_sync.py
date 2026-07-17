#!/usr/bin/env python3
"""Lens-set sync test — run standalone or via check-structure.py's plugin-test
hook (plugins/*/scripts/test_*.py).

The 11-lens set is defined ONCE in swarm-review.js (LENS_CLUSTERS; the file
derives CANDIDATE_LENSES from it and asserts LENS_BRIEF coverage at startup),
but two runtime surfaces hand-mirror it and cannot be derived at runtime:

  - the SKILL.md external-prompt HDR ("Cover ALL of these lenses: ...") — a
    lens missing there is never reviewed by codex/grok, so cross-family
    consensus can silently never form on it;
  - swarm-review.js's METHODOLOGICAL_LENSES — the hand-maintained verify-gating
    subset of the breakage cluster; a methodological lens missing here stops
    being verified on a cross-family external consensus.

Prose DRIFT WARNINGs mark both mirrors; this test makes the sync mechanical
(the same pattern as test_pr_post.py for the publish path).
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
PLUGIN = HERE.parent
WORKFLOW = PLUGIN / "workflows" / "swarm-review.js"
SKILL = PLUGIN / "skills" / "review" / "SKILL.md"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


js = WORKFLOW.read_text(encoding="utf-8")

# LENS_CLUSTERS: cluster keys are bare identifiers, lens names are the only
# quoted strings inside the object literal (comments carry no quotes).
m = re.search(r"const LENS_CLUSTERS = \{(.*?)\n\}", js, re.S)
check("workflow: LENS_CLUSTERS block found", m)
cluster_block = m.group(1) if m else ""
cluster_lenses = re.findall(r"'([a-z][a-z-]*)'", cluster_block)
clusters = {}
for line in cluster_block.splitlines():
    km = re.match(r"\s*([a-z]+):\s*\[(.*?)\]", line)
    if km:
        clusters[km.group(1)] = re.findall(r"'([a-z][a-z-]*)'", km.group(2))
check("workflow: 4 clusters parsed", len(clusters) == 4)
check(
    "workflow: lens names unique",
    len(cluster_lenses) == len(set(cluster_lenses)) and cluster_lenses,
)

# LENS_BRIEF: one entry per line, key at 2-space indent (bare or quoted).
# The workflow asserts brief coverage at startup too, but that only fires on a
# live run — this catches the drift in CI.
bm = re.search(r"const LENS_BRIEF = \{(.*?)\n\}", js, re.S)
check("workflow: LENS_BRIEF block found", bm)
brief_pairs = re.findall(r"^  (?:'([a-z-]+)'|([a-z]+)): '", bm.group(1) if bm else "", re.M)
brief_keys = {a or b for a, b in brief_pairs}
check("LENS_BRIEF keys == LENS_CLUSTERS lenses", brief_keys == set(cluster_lenses))

# SKILL.md external-prompt HDR mirror: "- Cover ALL of these lenses: a; b (…); …"
skill = SKILL.read_text(encoding="utf-8")
hm = re.search(r"^- Cover ALL of these lenses: (.+)$", skill, re.M)
check("skill: HDR lens line found", hm)
hdr_lenses = set()
if hm:
    for seg in hm.group(1).split(";"):
        lm = re.match(r"\s*([a-z][a-z-]*)", seg)
        if lm:
            hdr_lenses.add(lm.group(1))
check("SKILL.md HDR lenses == LENS_CLUSTERS lenses", hdr_lenses == set(cluster_lenses))

# METHODOLOGICAL_LENSES: the verify-gating list of breakage-cluster lenses that
# assert repo-wide facts (everything in `breakage` EXCEPT the diff-local topical
# `correctness`). A COMPLETENESS check, not just a subset: a new methodological
# lens added to `breakage` but forgotten here would silently stop being verified
# on a cross-family external consensus (the correlated-hallucination hole the
# constant exists to close), and green CI would give false assurance. Coupling it
# to `breakage - {correctness}` forces a conscious test edit either way — add a
# methodological lens and it must appear here; add a topical one and it must be
# named in the exclusion below.
TOPICAL_BREAKAGE = {"correctness"}
mm = re.search(r"const METHODOLOGICAL_LENSES = \[([^\]]*)\]", js)
check("workflow: METHODOLOGICAL_LENSES found", mm)
methodological = set(re.findall(r"'([a-z][a-z-]*)'", mm.group(1) if mm else ""))
check("METHODOLOGICAL_LENSES non-empty", bool(methodological))
check(
    "METHODOLOGICAL_LENSES == breakage cluster minus topical lenses",
    methodological == set(clusters.get("breakage", [])) - TOPICAL_BREAKAGE,
)

if FAILS:
    print("lens-sync tests FAILED:", file=sys.stderr)
    for f in FAILS:
        print("  -", f, file=sys.stderr)
    sys.exit(1)
print("lens-sync: all lens mirrors in sync")
