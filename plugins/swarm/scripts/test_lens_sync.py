#!/usr/bin/env python3
"""Lens-set sync test — run standalone or via check-structure.py's plugin-test
hook (plugins/*/scripts/test_*.py).

The 11-lens set is defined ONCE in swarm-review.js (LENS_CLUSTERS; the file
derives CANDIDATE_LENSES from it and asserts LENS_BRIEF coverage at startup),
and since 0.7.0 the external voices receive it through the adapter's
--lens-instr (per gated cluster) instead of a hand-mirrored SKILL.md list.
What still hand-mirrors the set, and cannot be derived at runtime:

  - swarm-review.js's METHODOLOGICAL_LENSES — the hand-maintained verify-gating
    subset of the breakage cluster; a methodological lens missing here stops
    being verified on a cross-family external consensus;
  - pr-post.py's DESIGN_LENS_TAGS — the publish path's design-tag guard.

Plus the structural checks that keep the single-source path intact (the
--lens-instr wiring, and the absence of a reintroduced SKILL.md lens list).

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

# SKILL.md external-prompt HDR: NO lens mirror since 0.7.0. The external voices
# run per gated CLUSTER and the workflow ships that cluster's briefs through the
# adapter's --lens-instr, so LENS_BRIEF is single-source in swarm-review.js. This
# is now a NEGATIVE check: reintroducing a broad lens list in the HDR would both
# recreate the drift this test existed to catch AND fight the per-cluster
# instruction (a "cover ALL lenses" line contradicts "review ONLY these").
skill = SKILL.read_text(encoding="utf-8")
check("skill: HDR carries no lens-list mirror", not re.search(r"^- Cover ALL of these lenses:", skill, re.M))
# The adapter flag the single-source design depends on must exist: without it the
# workflow's per-cluster briefs would be silently dropped and every external voice
# would review lens-free.
ADAPTER = PLUGIN / "scripts" / "agents.sh"
sh = ADAPTER.read_text(encoding="utf-8")
check("adapter: --lens-instr flag present", "--lens-instr)" in sh)
check("workflow: passes --lens-instr to the adapter", "--lens-instr" in js)

# Oversize headroom: the skill refuses to run the externals above a threshold in
# PROSE, but the real per-call cap (`max_bytes`) lives in agents.sh, and what
# exec() sees is lens-instruction + diff. Nothing but this check ties the two
# numbers together, so a brief that grows past the headroom — or a changed cap —
# would surface only as a per-call backend error at review time.
mb = re.search(r"local max_bytes=(\d+)", sh)
check("adapter: max_bytes found", mb)
sk = re.search(r"`PROMPT_BYTES` > (\d+)", skill)
check("skill: oversize threshold found", sk)
if mb and sk:
    max_bytes, threshold = int(mb.group(1)), int(sk.group(1))
    check("skill threshold is below the adapter cap", threshold < max_bytes)
    # Largest instruction the workflow can build: the biggest cluster's briefs
    # plus the fixed wrapper prose. Mirrors lensInstr()/unitBrief() closely enough
    # to bound it (exactness is not the point — the headroom margin is).
    briefs = {}
    for m in re.finditer(r"^  (?:'([a-z-]+)'|([a-z]+)): '(.*)',$", bm.group(1) if bm else "", re.M):
        briefs[m.group(1) or m.group(2)] = m.group(3)
    check("LENS_BRIEF values parsed", set(briefs) == set(cluster_lenses))
    worst = 0
    for lenses in clusters.values():
        body = "; ".join(f"{l}: {briefs.get(l, '')}" for l in lenses)
        tags = " / ".join(f'"[{l}] "' for l in lenses)
        worst = max(worst, len(("Review ONLY through these lens(es) — report nothing outside them. "
                                f"Lenses — {body}. One finding per distinct issue (defect or substantive improvement), "
                                "each with a concrete falsifiable failure_scenario. "
                                f"Prefix each summary with the ONE lens it belongs to: {tags}. "
                                "An empty findings list is valid.").encode("utf-8")))
    check(
        f"oversize headroom ({max_bytes - threshold} B) covers the largest lens instruction ({worst} B)",
        max_bytes - threshold >= worst,
    )

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

# pr-post.py DESIGN_LENS_TAGS mirror: the publish path prefixes design rows with
# "[lens] " and guards against doubling an already-present tag; that guard keys
# on this hand-mirrored tuple, so a design lens added to the cluster but not here
# would slip past the guard and could re-post as "[reuse] [reuse] …".
PR_POST = PLUGIN / "scripts" / "pr-post.py"
pp = PR_POST.read_text(encoding="utf-8")
dm = re.search(r"DESIGN_LENS_TAGS = \(([^)]*)\)", pp)
check("pr-post: DESIGN_LENS_TAGS found", dm)
pr_design = set(re.findall(r"'([a-z][a-z-]*)'|\"([a-z][a-z-]*)\"", dm.group(1) if dm else ""))
pr_design = {a or b for a, b in pr_design}
check("pr-post DESIGN_LENS_TAGS == design cluster", pr_design == set(clusters.get("design", [])))

if FAILS:
    print("lens-sync tests FAILED:", file=sys.stderr)
    for f in FAILS:
        print("  -", f, file=sys.stderr)
    sys.exit(1)
print("lens-sync: all lens mirrors in sync")
