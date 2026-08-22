#!/usr/bin/env python3
"""Lens-set sync test — run standalone or via check-structure.py's plugin-test
hook (plugins/*/scripts/test_*.py).

The 11-lens set is defined ONCE in swarm-review.js (LENS_CLUSTERS; the file
derives CANDIDATE_LENSES from it and asserts LENS_BRIEF coverage at startup),
and since 0.7.0 the external voices receive it through the adapter's
--lens-instr (per gated cluster) instead of a hand-mirrored SKILL.md list.
What still hand-mirrors the set, and cannot be derived at runtime:

  - swarm-review.js's METHODOLOGICAL_LENSES — the hand-maintained verify-gating
    subset of the fact-asserting clusters (breakage + reach); a methodological
    lens missing here stops being verified on a cross-family external consensus;
  - pr-post.py's DESIGN_LENS_TAGS — the publish path's design-tag guard.

Plus the structural checks that keep the single-source path intact (the
--lens-instr wiring, and the absence of a reintroduced SKILL.md lens list).

Prose DRIFT WARNINGs mark both mirrors; this test makes the sync mechanical
(the same pattern as test_pr_post.py for the publish path).
"""
import json
import re
import shutil
import subprocess
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
# A count, not a name list: the names are asserted where they carry meaning
# (FACT_CLUSTERS below, DESIGN_LENS_TAGS further down), so repeating them here
# would add a mirror instead of a check. This guards the PARSE — a regex that
# stopped matching would otherwise leave every downstream set comparison
# trivially passing against empty data.
check(f"workflow: 5 clusters parsed (got {len(clusters)})", len(clusters) == 5)
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
# Must match the ARGUMENT the workflow builds, not merely the string appearing
# somewhere: prose about --lens-instr (there is plenty) would otherwise keep this
# green after the actual flag was dropped from the command.
check(
    "workflow: passes --lens-instr into the transport command",
    re.search(r"--lens-instr \$\{shQuote\([A-Za-z_]+\(u\)\)\}", js),
)
# The integrity check is only worth anything if the declared length travels with
# the text — and it must be DERIVED from the same expression, never a literal.
check("adapter: --lens-instr-sum flag present", "--lens-instr-sum)" in sh)
check(
    "workflow: declares --lens-instr-sum from the built instruction",
    re.search(r"--lens-instr-sum \$\{utf8Checksum\([A-Za-z_]+\(u\)\)\}", js),
)
# The checksum only guards anything if the adapter REFUSES an instruction that
# arrives without one — otherwise dropping a flag silently voids the check.
check(
    "adapter: --lens-instr without --lens-instr-sum is refused",
    re.search(r'lens_instr_set" == 1 && -z "\$lens_instr_sum', sh),
)

# FNV-1a/32 EQUIVALENCE. The checksum is implemented TWICE — hand-rolled JS in the
# workflow (no Buffer/crypto in the sandbox) and python3 in the adapter — and the
# checks above only prove both flags exist. A one-sided edit to either (algorithm,
# hex padding, UTF-8 handling) would make every external call fail its own
# integrity check: exit 2 per unit, i.e. the whole external half of the ensemble
# collapses into backendErrors while Claude still runs. Pin them against a
# reference implementation of the published FNV-1a spec (an oracle, not a third
# mirror), over inputs that exercise the multi-byte paths the briefs actually use.
def fnv1a32(text):
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return "%08x" % h


# "f8" hashes to 0d226273 — a LEADING ZERO, so it is the vector that catches a
# dropped zero-pad on either side (JS `padStart(8,'0')` vs python `%08x`). Without
# it every other vector still matches while the two sides disagree on short
# hashes; found by search, kept deliberately. The rest cover the multi-byte
# encoding paths (2-, 3- and 4-byte code points) the briefs actually contain.
VECTORS = ["", "f8", "plain ascii", "em — dash", "ä ö ü", "emoji 🐝", "Review ONLY through these lens(es) — report nothing outside them."]

# Adapter side: run its OWN inlined python snippet, not a copy of it.
py_snippet = re.search(r"actual_sum=\$\(printf '%s' \"\$lens_instr\" \| python3 -c '\n(.*?)'\)", sh, re.S)
check("adapter: FNV python snippet found", py_snippet)
if py_snippet:
    for v in VECTORS:
        got = subprocess.run(
            [sys.executable, "-c", py_snippet.group(1)],
            input=v.encode("utf-8"), capture_output=True,
        ).stdout.decode().strip()
        check(f"adapter FNV matches reference for {v!r}", got == fnv1a32(v))

# Workflow side: extract utf8Checksum() and run it under node. node ships with the
# CI image and is a hard requirement here rather than a skip — a silently skipped
# equivalence check is exactly the false assurance this test exists to prevent.
js_fn = re.search(r"const utf8Checksum = \(s\) => \{.*?\n\}", js, re.S)
check("workflow: utf8Checksum() found", js_fn)
if js_fn and shutil.which("node"):
    prog = js_fn.group(0) + "\n" + "console.log(JSON.parse(process.argv[1]).map(utf8Checksum).join(','))"
    out = subprocess.run(
        ["node", "-e", prog, json.dumps(VECTORS)], capture_output=True,
    ).stdout.decode().strip()
    check(
        "workflow FNV matches reference (all vectors)",
        out == ",".join(fnv1a32(v) for v in VECTORS),
    )
elif js_fn:
    FAILS.append("node not found — cannot verify the workflow/adapter checksum implementations agree")

# Oversize headroom: the skill skips the externals above a threshold, but the
# real per-call cap (`max_bytes`) lives in agents.sh, and what the backend
# ingests is lens-instruction + diff. Nothing but this check ties the two
# numbers together, so a brief that grows past the headroom — or a changed cap —
# would surface only as a per-call backend error at review time.
# Both sides read the SAME env knob (SWARM_MAX_PROMPT_BYTES), so what has to
# agree is the DEFAULT each falls back to: a skill default below the adapter's
# would skip externals the adapter would have accepted, one above it would send
# calls the adapter then rejects. Read both from the EXECUTABLE code (the
# adapter's assignment, the skill's `-gt` guard), never from prose: prose can
# drift, and it is the code that decides.
mb = re.search(r'local max_bytes="\$\{SWARM_MAX_PROMPT_BYTES:-(\d+)\}"', sh)
check("adapter: max_bytes default found", mb)
sk = re.search(
    r'SWARM_CAP="\$\{SWARM_MAX_PROMPT_BYTES:-(\d+)\}"(?s:.*?)'
    r'-gt "\$\(\( SWARM_CAP - (\d+) \)\)" \]; then echo "EXTERNALS_OVERSIZE=1"',
    skill,
)
check("skill: EXTERNALS_OVERSIZE guard + shared cap default found", sk)
# The two timeouts must derive from ONE value, with the adapter's cap strictly
# below the Bash window. If they tie (both 600 s, the pre-0.9 state), the outer
# kill can win and the run loses rc=124 — no "timed out after Ns", no telemetry
# timeout flag, just a dead command. That lost diagnosis is what made
# SWARM_TIMEOUT look useless in the first place.
check(
    "workflow: sets SWARM_TIMEOUT on the transport command",
    re.search(r"cmd: `SWARM_TIMEOUT=\$\{EFFECTIVE_TIMEOUT_S\} bash ", js),
)
check(
    "workflow: the Bash window is derived, not a second hard-coded literal",
    re.search(r"Bash tool \(timeout \$\{BASH_TIMEOUT_MS\}\)", js)
    and not re.search(r"Bash tool \(timeout 600000\)", js),
)
check(
    "workflow: the adapter cap keeps a margin below the Bash window",
    re.search(r"MAX_INNER_S = BASH_TIMEOUT_MS / 1000 - TIMEOUT_MARGIN_S", js),
)

# Family coverage must be computed in the WORKFLOW and rendered by the skill.
# Consensus means ">=2 agreeing families", so a lost family changes what every
# verdict means while the numbers look unchanged — the presenter cannot re-derive
# that from backendErrors (a backend with one dead cluster and one live one has
# NOT lost its family), and a run that degrades silently is the bug this whole
# area exists to prevent.
check(
    "workflow: computes familiesLost",
    re.search(r"const familiesLost = familiesExpected\.filter", js),
)
check(
    "workflow: exposes family coverage in balance",
    all(k in js for k in ("familiesExpected,", "familiesPresent,", "familiesLost,", "consensusReachable,")),
)
check(
    "skill: renders the reduced-consensus warning",
    "balance.familiesLost" in skill and "Konsens-Basis reduziert" in skill,
)

# Sharing the env knob means sharing its CONTRACT. The adapter refuses a
# non-positive-integer SWARM_MAX_PROMPT_BYTES; without the same guard in the
# skill, `abc` expands to 0 in its arithmetic, the threshold goes negative, and
# EVERY diff counts as oversize — dropping all external voices SILENTLY, which is
# the one failure mode the oversize path exists to make explicit. Found by the
# review this split's own first run produced; pinned so it cannot regress.
check(
    "skill: validates SWARM_MAX_PROMPT_BYTES like the adapter does",
    re.search(r"case \"\$SWARM_CAP\" in\s*\n\s*''\|\*\[!0-9\]\*\|0\)[^\n]*SWARM_CFG_ERR", skill),
)
check(
    "adapter: rejects a non-positive-integer SWARM_MAX_PROMPT_BYTES",
    re.search(r'max_bytes" =~ \^\[0-9\]\+\$', sh) and re.search(r"\(\( max_bytes > 0 \)\)", sh),
)
# BOTH sides must parse the shared knob identically. They read the same env var
# and gate the same decision, so a difference is not cosmetic: with `0100000`
# the skill saw decimal 100000 and let every voice through, while the adapter
# read octal 32768 and rejected each one — turning the deterministic single skip
# into the per-call error storm it exists to prevent. Require the decimal force
# on both sides, not just one.
check(
    "skill decimal-forces the cap",
    re.search(r"SWARM_CAP=\$\(\(10#\$SWARM_CAP\)\)", skill),
)
check(
    "adapter decimal-forces the cap too",
    re.search(r"max_bytes=\$\(\(10#\$max_bytes\)\)", sh),
)

if mb and sk:
    max_bytes = int(mb.group(1))
    skill_default, headroom = int(sk.group(1)), int(sk.group(2))
    check(
        f"skill cap default ({skill_default}) equals the adapter's ({max_bytes})",
        skill_default == max_bytes,
    )
    threshold = skill_default - headroom
    check("skill threshold is below the adapter cap", threshold < max_bytes)
    # Largest instruction the workflow can build. The FIXED prose is DERIVED from
    # the source (the literal chunks of lensInstr()/unitBrief()'s template
    # strings, with every ${...} expression removed) rather than copied here — a
    # Python copy of the template would go stale the moment someone adds a
    # sentence to unitBrief(), and this check would then bound the wrong string
    # while reporting green. Only the interpolated parts are modelled below.
    briefs = {}
    for m in re.finditer(r"^  (?:'([a-z-]+)'|([a-z]+)): '(.*)',$", bm.group(1) if bm else "", re.M):
        briefs[m.group(1) or m.group(2)] = m.group(3)
    check("LENS_BRIEF values parsed", set(briefs) == set(cluster_lenses))

    def literal_len(fn_src):
        """Bytes of fixed prose in a JS template-literal body (drops ${...})."""
        return sum(
            len(re.sub(r"\$\{[^}]*\}", "", chunk).encode("utf-8"))
            for chunk in re.findall(r"`([^`]*)`", fn_src)
        )

    ub = re.search(r"const unitBrief = \(u, \{ inline \}\) =>(.*?)\nconst ", js, re.S)
    li = re.search(r"const lensInstr = \(u\) =>(.*?)\n(?:const|// )", js, re.S)
    check("workflow: unitBrief() found", ub)
    check("workflow: lensInstr() found", li)
    fixed = literal_len(ub.group(1) if ub else "") + literal_len(li.group(1) if li else "")
    worst = 0
    for lenses in clusters.values():
        # inline form: "<lens>: <brief>" joined by "; ", plus the '"[lens] "'
        # tag list joined by " / " — the two ${...} expansions that scale.
        body = len("; ".join(f"{l}: {briefs.get(l, '')}" for l in lenses).encode("utf-8"))
        tags = len(" / ".join(f'"[{l}] "' for l in lenses).encode("utf-8"))
        worst = max(worst, fixed + body + tags)
    check(
        f"oversize headroom ({max_bytes - threshold} B) covers the largest lens instruction (<= {worst} B)",
        max_bytes - threshold >= worst,
    )

# METHODOLOGICAL_LENSES: the verify-gating list of lenses that assert repo-wide
# facts — everything in the FACT-ASSERTING clusters except the diff-local topical
# `correctness`. A COMPLETENESS check, not just a subset: a new methodological
# lens added to one of those clusters but forgotten here would silently stop
# being verified on a cross-family external consensus (the correlated-
# hallucination hole the constant exists to close), and green CI would give false
# assurance. Coupling it to the clusters forces a conscious test edit either way —
# add a methodological lens and it must appear here; add a topical one and it must
# be named in the exclusion below.
# `reach` joined `breakage` here in 0.9.0: splitting `cross-file-trace` into its
# own cluster changed WHERE the lens lives, not WHAT it is — it still asserts
# repo-wide facts and must still be verified. Listing the clusters (rather than
# hardcoding the lens names) keeps that property tied to meaning, not to layout.
# MANDATORY_LENSES: the gate floor. Deliberately an explicit list (which lenses
# are non-negotiable is a judgement call, not a consequence of cluster
# membership), which makes it a MIRROR — a lens renamed in LENS_CLUSTERS leaves a
# stale entry here that can never match, silently voiding the floor. The workflow
# throws on that at startup; this catches it in CI, before any run.
mand = re.search(r"const MANDATORY_LENSES = \[([^\]]*)\]", js)
check("workflow: MANDATORY_LENSES found", mand)
mandatory = set(re.findall(r"'([a-z][a-z-]*)'", mand.group(1) if mand else ""))
check("MANDATORY_LENSES non-empty", bool(mandatory))
check(
    f"MANDATORY_LENSES ⊆ LENS_CLUSTERS lenses (stale: {sorted(mandatory - set(cluster_lenses))})",
    mandatory <= set(cluster_lenses),
)

FACT_CLUSTERS = ("breakage", "reach")
TOPICAL_BREAKAGE = {"correctness"}
mm = re.search(r"const METHODOLOGICAL_LENSES = \[([^\]]*)\]", js)
check("workflow: METHODOLOGICAL_LENSES found", mm)
methodological = set(re.findall(r"'([a-z][a-z-]*)'", mm.group(1) if mm else ""))
check("METHODOLOGICAL_LENSES non-empty", bool(methodological))
fact_lenses = set()
for _c in FACT_CLUSTERS:
    check(f"LENS_CLUSTERS has a '{_c}' cluster", _c in clusters)
    fact_lenses |= set(clusters.get(_c, []))
check(
    "METHODOLOGICAL_LENSES == fact-asserting clusters minus topical lenses",
    methodological == fact_lenses - TOPICAL_BREAKAGE,
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
