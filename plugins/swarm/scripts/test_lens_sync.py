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
import os
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

# --- the prep block's heredocs must not evaluate their own text --------------
# `cat <<HDR` is UNQUOTED on purpose: $NONCE and $CAP_RULES have to expand. But
# an unquoted heredoc also runs backticks and $(...) inside its body — so a
# markdown-styled `[lens]` in the prompt text was executed as a command. The
# words vanished from the prompt the backends actually receive (leaving "the
# exact  prefixes you may use") and every run printed "[lens]: command not
# found" on stderr. Shipped in 0.7.0; three review rounds never saw it, because
# reading the block is not running it.
#
# Checked as a CLASS, not as that one line: any backtick or $(...) inside an
# unquoted heredoc body here is either dead text or an execution the author did
# not intend. Quote the delimiter (<<'X') when the body needs neither.
_prep = re.search(r"^```sh\n(.*?)^```", skill, re.S | re.M)
check("skill: prep block found", _prep)
_body = _prep.group(1) if _prep else ""
_bad = []
for _hd in re.finditer(r"<<(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1\n(.*?)^\2$", _body, re.S | re.M):
    if _hd.group(1):          # <<'X' — body is literal, nothing to evaluate
        continue
    for _line in _hd.group(3).splitlines():
        if "`" in _line or "$(" in _line:
            _bad.append(_line.strip()[:60])
check(f"skill: no backticks/$( inside an unquoted heredoc ({_bad})", not _bad)
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
mb = re.search(r"_resolve_int SWARM_MAX_PROMPT_BYTES [^\n]*? (\d+) ", sh)
check("adapter: cap default found in the resolver call", mb)
# The skill's oversize decision must still be DETERMINISTIC shell (never left to
# the model) and must compare against the adapter-reported threshold.
sk = re.search(r'-gt "\$OVERSIZE_THRESHOLD" \]; then echo "EXTERNALS_OVERSIZE=1"', skill)
check("skill: EXTERNALS_OVERSIZE decided in shell against the adapter threshold", sk)
# The two timeouts must derive from ONE value, with the adapter's cap strictly
# below the Bash window. If they tie (both 600 s, the pre-0.9 state), the outer
# kill can win and the run loses rc=124 — no "timed out after Ns", no telemetry
# timeout flag, just a dead command. That lost diagnosis is what made
# SWARM_TIMEOUT look useless in the first place.
check(
    "workflow: sets SWARM_TIMEOUT on the transport command",
    re.search(r"cmd: `SWARM_TIMEOUT=\$\{EFFECTIVE_TIMEOUT_S\} ", js),
)
# The prompt cap travels the same way, and for the same reason: the skill decides
# the oversize skip from the adapter-reported cap, so the adapter process must
# enforce THAT value. Left to environment inheritance, a subagent shell with a
# different env would put the two halves of one gate back out of sync.
check(
    "workflow: pins SWARM_MAX_PROMPT_BYTES on the transport command",
    re.search(r"SWARM_MAX_PROMPT_BYTES=\$\{MAX_PROMPT_BYTES\} ", js),
)
# The probe bound travels the same way and for a stronger reason: the adapter
# derives probe_budget_seconds from the value it RESOLVES, so a run that budgets
# one bound and enforces another puts the timeout race back where it started.
check(
    "workflow: pins SWARM_PROBE_TIMEOUT on the transport command",
    re.search(r"SWARM_PROBE_TIMEOUT=\$\{PROBE_TIMEOUT_S\} bash ", js),
)
# The four numeric knobs travel as ONE opaque token the model copies verbatim, so
# what has to be pinned is that the token is built from the adapter's own config
# and carried into the call — not four separate placeholder names.
check("skill: builds the config token from the adapter's config",
      re.search(r'SWARM_CFG_LINE="max_prompt_bytes=\$CFG_MAX;probe_timeout_seconds=\$CFG_PROBE_TO;probe_budget_seconds=\$CFG_PROBE"', skill))
check("skill: appends timeout_seconds only when the user set SWARM_TIMEOUT",
      "SWARM_CFG_LINE;timeout_seconds=$CFG_TO" in skill)
check("skill: reads probe_timeout_seconds from the adapter config",
      "probe_timeout_seconds)" in skill)
check("skill: the workflow call carries the config token",
      'config: "<SWARM_CFG_LINE>"' in skill)
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
# The reduced-consensus WORDING lives in the workflow now — header included. The
# skill prints coverageNotes verbatim and templates nothing: a header gated there
# on "coverageNotes is non-empty" announced "reduziert: 1 von 1 Modellfamilien" on
# every Claude-only run, and "2 von 2" for a cluster-only degradation.
check(
    "skill: prints coverageNotes and templates no header of its own",
    "coverageNotes" in skill and "Konsens-Basis reduziert" not in skill,
)
check(
    "workflow: owns the reduced-consensus header",
    "Konsens-Basis reduziert" in js,
)
# The scoped/global distinction is DECIDED in the workflow now: prose that
# re-derives it from the three raw flags is what got the scoping wrong (a single
# degraded cluster reported as a run-wide loss of consensus).
check(
    "workflow: emits the coverage sentences itself",
    "const coverageNotes = []" in js and "coverageNotes," in js,
)
check(
    "workflow: consensusReachable is the GLOBAL question only",
    re.search(r"const consensusReachable = familiesPresent\.length >= 2\s*$", js, re.M),
)

# Sharing the env knob means sharing its CONTRACT. The adapter refuses a
# non-positive-integer SWARM_MAX_PROMPT_BYTES; without the same guard in the
# skill, `abc` expands to 0 in its arithmetic, the threshold goes negative, and
# EVERY diff counts as oversize — dropping all external voices SILENTLY, which is
# the one failure mode the oversize path exists to make explicit. Found by the
# review this split's own first run produced; pinned so it cannot regress.
# A non-zero `config` exit must stop the run with the adapter's own message,
# rather than falling through to a review that silently drops every external.
check(
    "skill: surfaces a config error and stops",
    re.search(r'SWARM_CFG_ERR=\$\(printf', skill),
)
# ONE PARSER. Cap/timeout resolution used to live on both sides — the adapter and
# the skill's prep block each read the same SWARM_* vars — and three review rounds
# found three separate instances of one bug class: the cap decimal-forced on one
# side only, the timeout decimal-forced on one side only, a positivity check that
# ran before conversion in one place and after it in the other. Each produced two
# DIFFERENT numbers from one string, silently, with the skill deciding whether the
# externals run at all and the adapter deciding whether each call is accepted.
# The resolution now lives solely in agents.sh and the skill READS it, so these
# checks guard the structure rather than the wording of a duplicate.
check(
    "adapter: has one integer resolver",
    re.search(r"^_resolve_int\(\) \{", sh, re.M),
)
check(
    "adapter: exposes the resolved config",
    re.search(r"^subcmd_config\(\) \{", sh, re.M) and re.search(r"^\s*config\)\s+subcmd_config", sh, re.M),
)
check(
    "adapter: config reports the oversize threshold the skill needs",
    "oversize_threshold=" in sh,
)
check(
    "skill: reads the adapter config instead of parsing SWARM_* itself",
    re.search(r'scripts/agents\.sh" config', skill),
)
# The negative half: a reintroduced parse in the skill is exactly the regression
# this consolidation removed, so fail on one appearing again.
check(
    "skill: does not re-derive the cap",
    not re.search(r"SWARM_MAX_PROMPT_BYTES:-\d+", skill),
)
check(
    "skill: does not re-derive the oversize headroom",
    not re.search(r"SWARM_CAP\s*-\s*4096", skill),
)
# Every knob must go through the one resolver, never straight into arithmetic.
for knob in ("SWARM_TIMEOUT", "SWARM_PROBE_TIMEOUT", "SWARM_MAX_PROMPT_BYTES"):
    check(
        f"adapter: {knob} is resolved by _resolve_int",
        re.search(rf"_resolve_int {knob} ", sh),
    )

# Both numbers now come from the adapter alone, so there is no cross-file default
# to compare — what still has to hold is that the headroom actually covers the
# largest lens instruction the workflow can build. A brief that outgrows it would
# surface only as a per-call backend error at review time.
hr = re.search(r"^SWARM_CAP_HEADROOM=(\d+)", sh, re.M)
check("adapter: cap headroom found", hr)
if mb and hr:
    max_bytes = int(mb.group(1))
    headroom = int(hr.group(1))
    check("headroom leaves a usable cap", headroom < max_bytes)
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
        f"oversize headroom ({headroom} B) covers the largest lens instruction (<= {worst} B)",
        headroom >= worst,
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

# --- the probe budget travels, it is not re-derived --------------------------
# TIMEOUT_MARGIN_S used to be a hand-derived literal restating the adapter's
# SWARM_PROBE_TIMEOUT_MAX + TIMEOUT_KILL_GRACE in a comment. 0.10.0 then added a
# third bounded probe without touching it, and the margin silently stopped
# covering the worst case — the outer Bash window would win the race and destroy
# the rc=124 + telemetry evidence. The adapter reports the number now; these
# checks keep it that way end to end.
check("adapter: config reports probe_budget_seconds",
      "probe_budget_seconds=" in sh and "SWARM_MAX_PROBES_PER_RUN" in sh)
# From the RESOLVED probe timeout, not its ceiling: budgeting the ceiling charged
# every run for probes twice as long as the ones it actually enforces, and the
# workflow handed that margin back as lost wall time on every external call.
check("adapter: probe budget is derived from the resolved bound + slop + kill grace",
      re.search(r"SWARM_MAX_PROBES_PER_RUN \* \(_probe_timeout \+ TIMEOUT_EXPIRY_SLOP \+ TIMEOUT_KILL_GRACE\)", sh))
# SWARM_MAX_PROBES_PER_RUN is hand-maintained, and it is the ONLY link between
# "how much pre-timer work exists" and the margin the workflow derives from it. A
# new _bounded_probe call site that nobody counted is invisible to that margin —
# exactly how 0.10.0 overran it.
#
# The call sites and the constant are NOT equal on purpose: the constant is the
# worst case for ONE backend (grok: --version, models, --help), while the sites
# include codex's login-status probe that grok never runs. So pin the site COUNT
# itself — adding one forces this number to be touched, and touching it forces a
# decision about whether the worst case moved too.
# The definition lines end in `() {`, so the trailing space in the two markers
# already excludes them — an extra `"_probe_or_bare() {" not in l` clause could
# never fire and only suggested the opposite.
_probe_sites = len([l for l in sh.splitlines()
                    if ("_bounded_probe " in l or "_probe_or_bare " in l)
                    and not l.lstrip().startswith("#")])
_declared = re.search(r"SWARM_MAX_PROBES_PER_RUN=(\d+)", sh)
# A comment inside a `\`-continued command silently truncates it: the shell ends
# the logical line at the `#`, so every remaining argument disappears while
# `bash -n` still reports valid syntax. Adding a note above `--prompt-file` this
# way turned the grok invocation into a bare `--prompt-file …` (rc=127) — caught
# only because test_sandbox_deny.py asserts on the built argv. Cheap to check
# mechanically, invisible to review otherwise.
_cont = []
_sh_lines = sh.splitlines()
for _i, _l in enumerate(_sh_lines[:-1]):
    if _l.rstrip().endswith("\\") and _sh_lines[_i + 1].lstrip().startswith("#"):
        _cont.append(f"{_i + 2}: {_sh_lines[_i + 1].strip()[:50]}")
check(f"adapter: no comment inside a line continuation ({_cont})", not _cont)

# The `grok models` parser is an awk program wrapped in single quotes, so a single
# quote ANYWHERE inside it — comment text included — ends the shell quoting and
# corrupts the parser. That failure is quiet at the shell level (`bash -n` stays
# happy) and shows up only as "output format may have changed", i.e. as a lost
# grok family. It has now happened twice while editing this very block.
_awk = re.search(r"grok_parse_models\(\) \{\n  awk '\n(.*?)\n'\n\}", sh, re.S)
check("adapter: the models awk program is delimited", _awk)
check("adapter: no apostrophe inside the awk program",
      bool(_awk) and "'" not in _awk.group(1))

# --- the subshell-memo class -------------------------------------------------
# Five times on this branch a function assigned a global AND printed its result,
# while every call site invoked it as `$(...)`. The assignment dies with the
# substitution, so the memo never reaches a second caller and — worse for
# _adapter_timeout / _enforced_wall — the EXIT trap in the PARENT reads an unset
# value and writes a telemetry record that denies the cap existed.
#
# The rule that ends it: a function may EITHER print a result OR cache into a
# global, never both. Checked mechanically, since reading the code has now failed
# five times.
_bad_memo = []
_fn = None
_body = []
for _l in sh.splitlines() + ["}"]:
    _m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", _l)
    if _m:
        _fn, _body = _m.group(1), []
        continue
    if _fn is not None and _l == "}":
        _src = "\n".join(_body)
        # prints a result on stdout (not a warning: those go to >&2)
        # printf/echo ANYWHERE on a statement, not just at line start: the
        # regression that motivated this check hid inside
        # `[[ -n "$x" ]] && { printf …; return 0; }`. Skip comment lines and
        # anything redirected to stderr (warnings are not results).
        def _is_result_print(_ln):
            _m2 = re.search(r"(?:^|[;&|{]\s*)(printf|echo)\b", _ln)
            if not _m2:
                return False
            if ">&2" in _ln or _ln.lstrip().startswith("#") or "printf -v" in _ln:
                return False  # stderr warnings and printf -v are not results
            # A printf INSIDE a command substitution feeds that substitution, not
            # the function's stdout: `rp="$(readlink … || printf '%s' "$p")"`.
            return "$(" not in _ln[: _m2.start(1)]

        _prints = any(_is_result_print(_ln) for _ln in _src.splitlines())
        # caches into a module-level global (leading underscore, not `local`)
        _caches = re.search(r"^\s*_[A-Za-z0-9_]*(_done|_memo[A-Za-z0-9_]*|_rc|_wall|_timeout|_bin)?=", _src, re.M)
        if _prints and _caches and _fn not in ("usage", "print_usage"):
            # `local _b _u _e _m` declares four names on one line, so a plain
            # "local <name>" substring test misses all but the first.
            _locals = set()
            for _ld in re.findall(r"^\s*local\s+([^;&|]+)", _src, re.M):
                for _tok in _ld.split():
                    _locals.add(_tok.split("=")[0])
            _decl = [g for g in re.findall(r"^\s*(_[A-Za-z0-9_]+)=", _src, re.M)
                     if g not in _locals]
            if _decl:
                _bad_memo.append(f"{_fn}() sets {_decl[0]} and prints")
        _fn = None
        continue
    if _fn is not None:
        _body.append(_l)
check(f"adapter: no function both prints and caches into a global ({_bad_memo})",
      not _bad_memo)

check("adapter: SWARM_MAX_PROBES_PER_RUN is declared", _declared)
check(f"adapter: pre-timer probe call sites still number 5 (got {_probe_sites}) — "
      f"if you added one, re-derive SWARM_MAX_PROBES_PER_RUN "
      f"(currently {_declared.group(1) if _declared else '?'}, the worst case for a "
      f"single backend) and update this pin",
      _probe_sites == 5)
check("adapter: the declared worst case covers grok's three probes",
      bool(_declared) and int(_declared.group(1)) >= 3)

check("adapter: the --help capability probe is memoized",
      "_grok_help_done" in sh and "_grok_help_rc" in sh)
check("skill: the probe budget is echoed for the workflow",
      "probe_budget_seconds=$CFG_PROBE" in skill)
check("workflow: the margin is built from the reported budget, not a literal",
      re.search(r"const TIMEOUT_MARGIN_S = PROBE_BUDGET_S \+ TIMEOUT_SLACK_S \+ WALL_OVERSHOOT_S", js))
# The backend call runs under the same bound as a probe, so its own overshoot must
# be reserved too — leaving it to the slack put the worst case within seconds of
# the outer window.
check("workflow: the margin reserves the backend call's own overshoot",
      re.search(r"const WALL_OVERSHOOT_S = EXPIRY_SLOP_S \+ KILL_GRACE_S", js))
check("workflow: the probe budget is read from the config token",
      "cfgPick('probe_budget_seconds', 'probeBudgetSeconds')" in js)
# The expiry slop is a copy of the adapter's, like every other rail here.
_es_sh = re.search(r"TIMEOUT_EXPIRY_SLOP=(\d+)", sh)
_es_js = re.search(r"const EXPIRY_SLOP_S = (\d+)", js)
check("adapter+workflow: expiry slop matches",
      all([_es_sh, _es_js]) and _es_sh.group(1) == _es_js.group(1))

# The workflow keeps ONE hand-copied number: the fallback used when it runs
# without the skill in front of it. Pin it against what the adapter actually
# reports — an unpinned copy of `max_probes x (ceiling + grace)` is the same
# split-brain that already overran the margin once, and a comment cannot fail CI.
# Scrubbed env: `config` prints RESOLVED values, so an exported SWARM_* knob in
# the developer's shell would make this pin report drift in a constant nobody
# touched — or, with SWARM_TIMEOUT=600s, make `config` exit 2 and fail three
# checks naming the wrong cause. check-structure.py runs this in CI with the
# ambient environment, so the scrub belongs here.
_env = {k: v for k, v in os.environ.items() if not k.startswith("SWARM_")}
_cfg = subprocess.run(["bash", str(ADAPTER), "config"],
                      capture_output=True, text=True, env=_env)
check("adapter: config runs with a clean env (unset SWARM_* to reproduce)",
      _cfg.returncode == 0)

_reported = ""
for _line in _cfg.stdout.splitlines():
    if _line.startswith("probe_budget_seconds="):
        _reported = _line.split("=", 1)[1].strip()
_fb = re.search(r"const PROBE_BUDGET_FALLBACK_S = (\d+)", js)
check("workflow: a named fallback constant exists", _fb)
_cap_reported = ""
for _line in _cfg.stdout.splitlines():
    if _line.startswith("max_prompt_bytes="):
        _cap_reported = _line.split("=", 1)[1].strip()
_cap_fb = re.search(r"const MAX_PROMPT_BYTES_FALLBACK = (\d+)", js)
check("workflow: a named prompt-cap fallback exists", _cap_fb)
check(f"workflow cap fallback == adapter max_prompt_bytes "
      f"(js={_cap_fb.group(1) if _cap_fb else '?'} adapter={_cap_reported or '?'})",
      bool(_cap_fb) and bool(_cap_reported) and _cap_fb.group(1) == _cap_reported)
# The workflow pins this value onto every adapter call, so its bounds must be the
# adapter's bounds — validating only "> 0" let a refused value through and killed
# every external voice at launch.
check("workflow: the prompt cap is bounded on both sides",
      "MAX_PROMPT_BYTES_MIN" in js and "MAX_PROMPT_BYTES_MAX" in js)
# Those two rails are COPIES of the adapter's (it cannot run shell), so pin them —
# an unchecked copy of a bound is the same split-brain as an unchecked copy of a
# default, and this branch has paid for that lesson repeatedly.
_hdr = re.search(r"SWARM_CAP_HEADROOM=(\d+)", sh)
_capmax = re.search(r"SWARM_MAX_PROMPT_BYTES_MAX=(\d+)", sh)
_jsmin = re.search(r"const MAX_PROMPT_BYTES_MIN = (\d+)", js)
_jsmax = re.search(r"const MAX_PROMPT_BYTES_MAX = (\d+)", js)
check("adapter+workflow: prompt-cap floor matches (headroom + 1)",
      all([_hdr, _jsmin]) and int(_jsmin.group(1)) == int(_hdr.group(1)) + 1)
check("adapter+workflow: prompt-cap ceiling matches",
      all([_capmax, _jsmax]) and _jsmax.group(1) == _capmax.group(1))
# Same pin for the probe bound the workflow now pins onto every call: its
# fallback must be the adapter's resolved default and its ceiling the adapter's
# rail, or a direct workflow invocation pins a value the adapter refuses.
_pt_reported = ""
for _line in _cfg.stdout.splitlines():
    if _line.startswith("probe_timeout_seconds="):
        _pt_reported = _line.split("=", 1)[1].strip()
_pt_fb = re.search(r"const PROBE_TIMEOUT_FALLBACK_S = (\d+)", js)
_pt_max_js = re.search(r"const PROBE_TIMEOUT_MAX_S = (\d+)", js)
_pt_max_sh = re.search(r"SWARM_PROBE_TIMEOUT_MAX=(\d+)", sh)
check(f"workflow probe-timeout fallback == adapter probe_timeout_seconds "
      f"(js={_pt_fb.group(1) if _pt_fb else '?'} adapter={_pt_reported or '?'})",
      bool(_pt_fb) and bool(_pt_reported) and _pt_fb.group(1) == _pt_reported)
# The workflow checks the probe pair against these two, so a drift here would let
# an inconsistent pair through — the overrun the check exists to catch.
_mp_sh = re.search(r"SWARM_MAX_PROBES_PER_RUN=(\d+)", sh)
_mp_js = re.search(r"const MAX_PROBES = (\d+)", js)
_kg_sh = re.search(r"TIMEOUT_KILL_GRACE=(\d+)", sh)
_kg_js = re.search(r"const KILL_GRACE_S = (\d+)", js)
check("adapter+workflow: max-probes matches",
      all([_mp_sh, _mp_js]) and _mp_sh.group(1) == _mp_js.group(1))
check("adapter+workflow: kill grace matches",
      all([_kg_sh, _kg_js]) and _kg_sh.group(1) == _kg_js.group(1))
check("adapter+workflow: probe-timeout ceiling matches",
      all([_pt_max_js, _pt_max_sh]) and _pt_max_js.group(1) == _pt_max_sh.group(1))
check(f"workflow fallback == adapter probe_budget_seconds "
      f"(js={_fb.group(1) if _fb else '?'} adapter={_reported or '?'})",
      bool(_fb) and bool(_reported) and _fb.group(1) == _reported)

# --- the presenter must not restate a configurable byte count ----------------
# The prep block asks the adapter for the real threshold; prose that still says
# "512 KiB (524288-byte)" tells the user the wrong number in the one message they
# act on, and points them away from the override that actually fired.
_lit = [l.strip()[:60] for l in skill.splitlines()
        if not l.lstrip().startswith("#") and ("524288" in l or "512 KiB" in l)]
check(f"skill: no hard-coded prompt cap in the prose ({_lit})", not _lit)

if FAILS:
    print("lens-sync tests FAILED:", file=sys.stderr)
    for f in FAILS:
        print("  -", f, file=sys.stderr)
    sys.exit(1)
print("lens-sync: all lens mirrors in sync")
