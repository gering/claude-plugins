#!/usr/bin/env python3
"""Behavioural test for the swarm voice accounting in swarm-review.js.

WHY THIS LOOKS ODD. `swarm-review.js` is the repository's only JavaScript file
(the Workflow runtime accepts nothing else), and it is also the largest logic
file here — so the Python test suite that guards everything else cannot reach
it. Three consecutive reviews lost most of their external voices and still
reported `ok: true`, `backendErrors: []` and full family coverage; the accounting
that now prevents that lives in exactly the untested file.

So this test lifts the two marked regions out of the source VERBATIM, runs them
under node against synthetic voice results, and asserts on what they compute.
It tests behaviour, not the presence of source lines: a refactor that keeps the
lines but breaks the logic must fail here.

Deliberately NOT skippable. A test written against silent loss that silently
skips itself when node is missing would reproduce the very bug it guards. If
node is absent this fails, and CI installs node for that reason.
"""

import functools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "workflows" / "swarm-review.js"

failures = []


def fail(case, msg):
    failures.append(f"{case}: {msg}")


# --- extraction --------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _source():
    """Read swarm-review.js once. Every case used to re-read ~1450 lines."""
    return SOURCE.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=None)
def region(name):
    """Lift one `// swarm-test-region: <name>` block out of the source."""
    text = _source()
    start = f"// swarm-test-region: {name}\n"
    if start not in text:
        sys.exit(
            f"FATAL: region '{name}' not found in {SOURCE.name}.\n"
            "The marker was renamed or deleted. Restore it (or update this test) "
            "— do not delete this test to make the error go away: that would "
            "leave the voice accounting with no coverage at all, which is the "
            "failure mode it exists to prevent."
        )
    body = text.split(start, 1)[1]
    end = "// swarm-test-region-end"
    if end not in body:
        sys.exit(f"FATAL: region '{name}' has no closing marker in {SOURCE.name}.")
    return body.split(end, 1)[0]


@functools.lru_cache(maxsize=None)
def family_map():
    """Reuse the real FAMILY map instead of copying it into the test."""
    text = _source()
    m = re.search(r"^const FAMILY = \{.*?\}$", text, re.M)
    if not m:
        sys.exit(
            f"FATAL: could not find the one-line `const FAMILY = {{...}}` in "
            f"{SOURCE.name}. A copy in this test would silently drift from it, "
            "so update this extractor instead of hardcoding the map."
        )
    return m.group(0)


# --- harness -----------------------------------------------------------------


def run(planned, settled, run_claude=True, live_externals=()):
    """Execute the extracted accounting against synthetic voices."""
    prelude = f"""
const logs = []
const log = (m) => logs.push(String(m))
{family_map()}
const runClaude = {json.dumps(run_claude)}
const liveExternals = {json.dumps([{"backend": b} for b in live_externals])}
const plannedVoices = {json.dumps(planned)}
const settled = {json.dumps(settled)}
"""
    epilogue = """
console.log(JSON.stringify({
  voices: voices.map((v) => ({ backend: v.backend, unit: v.unit, ok: v.ok, error: v.error, findings: v.findings.length })),
  voicesReturned,
  backendErrors,
  familiesExpected, familiesPresent, familiesLost, familiesPartial,
  unitsDegraded, consensusReachable, coverageNotes, logs,
}))
"""
    return _node(prelude + region("voice-mapping") + region("voice-accounting") + epilogue)


def _node(script):
    node = shutil.which("node")
    if not node:
        sys.exit(
            "FATAL: `node` is not on PATH, so the voice accounting cannot be "
            "executed and this test would check nothing.\n"
            "This is a hard failure on purpose — see the module docstring. "
            "Install node (CI does so via actions/setup-node)."
        )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        sys.exit(
            "FATAL: the extracted regions failed to run under node — the "
            "accounting may reference something outside the markers.\n"
            + (proc.stderr.strip() or "(no stderr)")
        )
    return json.loads(proc.stdout)


def run_mapping(calls):
    """Drive shapeClaudeResult / shapeExternalResult with raw agent results."""
    prelude = f"""
const logs = []
const log = (m) => logs.push(String(m))
const CALLS = {json.dumps(calls)}
"""
    epilogue = """
console.log(JSON.stringify(CALLS.map((c) => c.kind === 'claude'
  ? shapeClaudeResult(c.result, { name: c.unit, lenses: [c.unit] })
  : shapeExternalResult(c.result, { backend: c.backend, unit: c.unit, lenses: [c.unit] }))))
"""
    return _node(prelude + region("voice-mapping") + epilogue)


def voice(backend, unit):
    return {"backend": backend, "unit": unit, "lenses": [unit]}


def ok_result(backend, unit, n=0):
    return {"backend": backend, "unit": unit, "lenses": [unit],
            "ok": True, "error": "", "findings": [{"summary": f"[{unit}] x"}] * n}


def errored(v):
    """Was this voice counted as a failure?"""
    return v["ok"] is False


# --- cases -------------------------------------------------------------------

CLUSTERS = ["correctness", "threat", "design", "consistency"]


def case_resultless_shapes():
    """null, {} and a non-array `findings` must each reach backendErrors."""
    for label, bad in [
        ("null", None),
        ("empty object", {}),
        ("non-array findings", {"backend": "grok", "unit": "threat",
                                "lenses": ["threat"], "ok": True, "findings": "none"}),
    ]:
        planned = [voice("claude", "correctness"), voice("grok", "threat")]
        settled = [ok_result("claude", "correctness", 1), bad]
        out = run(planned, settled, live_externals=["grok"])

        lost = [e for e in out["backendErrors"] if e["backend"] == "grok"]
        if not lost:
            fail(label, "a resultless grok voice did not reach backendErrors")
            continue
        if "grok" in out["familiesPresent"]:
            fail(label, "grok still counted as a present family")
        if out["familiesLost"] != ["grok"]:
            fail(label, f"familiesLost = {out['familiesLost']}, expected ['grok']")
        if out["consensusReachable"]:
            fail(label, "consensusReachable stayed true with one family left")
        if out["voicesReturned"] != 1:
            fail(label, f"voicesReturned = {out['voicesReturned']}, expected 1")

    # The three shapes must be distinguishable in the report, not collapsed into
    # one generic string: an operator debugging a denied spawn needs to know
    # whether the agent answered at all.
    planned = [voice("grok", "threat")]
    null_err = run(planned, [None], run_claude=False,
                   live_externals=["grok"])["backendErrors"][0]["error"]
    schema_err = run(planned, [{"backend": "grok", "unit": "threat", "lenses": ["threat"],
                                "ok": True, "findings": "none"}], run_claude=False,
                     live_externals=["grok"])["backendErrors"][0]["error"]
    if null_err == schema_err:
        fail("shape distinction", "a null result and a schema-invalid one report the same reason")
    # A resolve-to-nothing carries NO diagnostic — the agent never spoke. The
    # reason must say that and must NOT name a cause. The first version of this
    # test asserted the opposite (it required the literal "blocked by permission
    # classifier"), which locked in a string that made every timeout read as a
    # permission denial. Assert the absence now, so the tautology cannot return.
    if "no diagnostic" not in null_err:
        fail("shape distinction", f"null result lost its no-evidence wording: {null_err!r}")
    for claimed in ("blocked by permission classifier", "timed out", "schema-invalid"):
        if claimed in null_err:
            fail("shape distinction",
                 f"null result asserts a cause it cannot know ({claimed!r}): {null_err!r}")


def case_total_family_loss():
    """Every call of a family gone: the note must name the scale, not just the family."""
    planned = [voice("claude", c) for c in CLUSTERS] + [voice("grok", c) for c in CLUSTERS]
    settled = [ok_result("claude", c, 1) for c in CLUSTERS] + [None] * len(CLUSTERS)
    out = run(planned, settled, live_externals=["grok"])

    if len(out["backendErrors"]) != len(CLUSTERS):
        fail("total loss", f"{len(out['backendErrors'])} backendErrors, expected {len(CLUSTERS)}")
    if out["familiesLost"] != ["grok"]:
        fail("total loss", f"familiesLost = {out['familiesLost']}")
    if out["consensusReachable"]:
        fail("total loss", "consensusReachable stayed true")
    notes = " ".join(out["coverageNotes"])
    if f"grok ({len(CLUSTERS)}/{len(CLUSTERS)}" not in notes:
        fail("total loss", f"coverage note does not name the lost call count: {notes!r}")
    if out["voicesReturned"] != len(CLUSTERS):
        fail("total loss", f"voicesReturned = {out['voicesReturned']}")
    if out["voicesReturned"] == len(out["voices"]):
        fail("total loss", "voicesReturned still equals the planned voice count")


def case_partial_family_loss():
    """2 of 8 through: the family survives, so only a PARTIAL note can report it."""
    planned = [voice("claude", c) for c in CLUSTERS] + [voice("grok", c) for c in CLUSTERS]
    settled = ([ok_result("claude", c, 1) for c in CLUSTERS]
               + [ok_result("grok", CLUSTERS[0], 1), None, None, None])
    out = run(planned, settled, live_externals=["grok"])

    if out["familiesLost"]:
        fail("partial loss", f"familiesLost = {out['familiesLost']}, expected none")
    if "grok" not in out["familiesPresent"]:
        fail("partial loss", "grok dropped out of familiesPresent despite one live call")
    if not out["consensusReachable"]:
        fail("partial loss", "consensusReachable went false though two families returned")
    if out["familiesPartial"] != ["grok"]:
        fail("partial loss", f"familiesPartial = {out['familiesPartial']}, expected ['grok']")
    notes = " ".join(out["coverageNotes"])
    if "Teilausfall" not in notes:
        fail("partial loss", f"no partial-loss note was emitted: {out['coverageNotes']!r}")
    if "grok (3/4" not in notes:
        fail("partial loss", f"partial note does not name 3 of 4 dead calls: {notes!r}")
    if len(out["backendErrors"]) != 3:
        fail("partial loss", f"{len(out['backendErrors'])} backendErrors, expected 3")


def case_voice_count_note():
    """The "ran with X of Y voices" line belongs to the workflow, and must not
    invent a cause. It was presenter prose that diagnosed a permission denial
    from a substring the generic reason always carried."""
    planned = [voice("claude", c) for c in CLUSTERS] + [voice("grok", c) for c in CLUSTERS]
    # One grok call timed out (it says so), one vanished without a word.
    settled = ([ok_result("claude", c, 1) for c in CLUSTERS]
               + [ok_result("grok", CLUSTERS[0], 1),
                  {"backend": "grok", "unit": CLUSTERS[1], "lenses": [CLUSTERS[1]],
                   "ok": False, "error": "Exit code 1: grok timed out after 540s", "findings": []},
                  None,
                  ok_result("grok", CLUSTERS[3], 1)])
    out = run(planned, settled, live_externals=["grok"])

    notes = " ".join(out["coverageNotes"])
    if "6 von 8 Stimmen" not in notes:
        fail("voice count", f"the note does not state 6 of 8 voices: {notes!r}")
    if "1 per Timeout" not in notes:
        fail("voice count", f"the timed-out call was not classed as a timeout: {notes!r}")
    if "1 ohne jede Rückmeldung" not in notes:
        fail("voice count", f"the silent call was not classed as unexplained: {notes!r}")
    # The counts must lead — they reframe every number in the balance line.
    if "Stimmen" not in out["coverageNotes"][0]:
        fail("voice count", f"the voice-count note is not first: {out['coverageNotes']!r}")


def case_healthy_run_is_quiet():
    """The counter-test: a run that lost nothing must produce NO coverage note."""
    planned = [voice("claude", c) for c in CLUSTERS] + [voice("grok", c) for c in CLUSTERS]
    settled = [ok_result("claude", c, 1) for c in CLUSTERS] + [ok_result("grok", c, 1) for c in CLUSTERS]
    out = run(planned, settled, live_externals=["grok"])

    if out["backendErrors"]:
        fail("healthy", f"a clean run produced backendErrors: {out['backendErrors']!r}")
    if out["coverageNotes"]:
        fail("healthy", f"a clean run produced coverage notes: {out['coverageNotes']!r}")
    if not out["consensusReachable"]:
        fail("healthy", "consensusReachable false on a clean two-family run")
    if out["voicesReturned"] != len(planned):
        fail("healthy", f"voicesReturned = {out['voicesReturned']}, expected {len(planned)}")
    if out["familiesPartial"]:
        fail("healthy", f"familiesPartial non-empty on a clean run: {out['familiesPartial']!r}")
    if any("Stimmen" in n for n in out["coverageNotes"]):
        fail("healthy", "a clean run announced a voice count it did not lose")


def case_claude_only_is_quiet():
    """A stock single-family install ran as configured — it is not 'degraded'."""
    planned = [voice("claude", c) for c in CLUSTERS]
    settled = [ok_result("claude", c, 1) for c in CLUSTERS]
    out = run(planned, settled)

    if out["unitsDegraded"]:
        fail("claude-only", f"unitsDegraded non-empty: {out['unitsDegraded']!r}")
    if out["familiesLost"]:
        fail("claude-only", f"familiesLost non-empty: {out['familiesLost']!r}")
    notes = " ".join(out["coverageNotes"])
    if "reduziert" in notes or "Teilausfall" in notes:
        fail("claude-only", f"a single-family run was reported as reduced: {notes!r}")


def case_identity_mismatch_fails_closed():
    """A shifted result must not be attributed to the wrong voice."""
    planned = [voice("claude", "correctness"), voice("grok", "threat")]
    # grok's slot carries a codex result — index drift, however it arose.
    settled = [ok_result("claude", "correctness", 1), ok_result("codex", "threat", 5)]
    out = run(planned, settled, live_externals=["grok"])

    grok = [v for v in out["voices"] if v["backend"] == "grok"]
    if not grok or not errored(grok[0]):
        fail("identity", "a mismatched result was accepted for the planned voice")
    if any(v["backend"] == "codex" for v in out["voices"]):
        fail("identity", "an unplanned backend leaked into the voice list")
    if any(v["findings"] == 5 for v in out["voices"]):
        fail("identity", "findings were attributed to a voice that did not produce them")
    # And it must be REPORTED as a skew. Delegating to noResultReason published a
    # mismatch as "schema-invalid" — or, when the stray result carried an error,
    # printed one backend's failure text under another backend's name.
    err = [e for e in out["backendErrors"] if e["backend"] == "grok"][0]["error"]
    if "identity mismatch" not in err:
        fail("identity", f"a skew was not reported as a skew: {err!r}")
    if "schema-invalid" in err:
        fail("identity", f"a skew was reported as a schema error: {err!r}")
    # A mismatched result that carries its OWN error text must not be republished
    # under the planned voice's name.
    out2 = run([voice("claude", "correctness"), voice("grok", "threat")],
               [ok_result("claude", "correctness", 1),
                {"backend": "codex", "unit": "threat", "lenses": ["threat"],
                 "ok": False, "error": "exit 7 quota exceeded", "findings": []}],
               live_externals=["grok"])
    err2 = [e for e in out2["backendErrors"] if e["backend"] == "grok"][0]["error"]
    if "quota exceeded" in err2:
        fail("identity", f"codex's error text was published under grok's name: {err2!r}")


def case_mapping_fails_closed():
    """Requirement 2 lives in the shapers, not the join — drive them directly.

    A mutation test caught this gap: with only the join under test, restoring
    `ok: r?.ok !== false` or dropping the Claude finder's explicit `ok` passed
    unnoticed, because the harness fed results that were already well-shaped.
    """
    calls = [
        {"kind": "external", "backend": "grok", "unit": "threat", "result": None},
        {"kind": "external", "backend": "grok", "unit": "threat", "result": {}},
        {"kind": "external", "backend": "grok", "unit": "threat",
         "result": {"ok": True, "error": "", "findings": "none"}},
        {"kind": "external", "backend": "grok", "unit": "threat",
         "result": {"ok": False, "error": "exit 3: adapter refused", "findings": []}},
        {"kind": "claude", "unit": "correctness", "result": None},
        {"kind": "claude", "unit": "correctness", "result": {}},
        {"kind": "claude", "unit": "correctness", "result": {"findings": "none"}},
    ]
    for got, call in zip(run_mapping(calls), calls):
        what = f"{call['kind']} <- {call['result']!r}"
        if got["ok"] is not False:
            fail("mapping", f"{what} was mapped to ok={got['ok']!r}, expected False")
        if got["findings"]:
            fail("mapping", f"{what} carried findings through: {got['findings']!r}")
        if not got["error"]:
            fail("mapping", f"{what} produced no error reason")

    # An adapter failure must keep ITS message, not be flattened to the generic one.
    adapter = run_mapping([calls[3]])[0]
    if "exit 3" not in adapter["error"]:
        fail("mapping", f"adapter error text was lost: {adapter['error']!r}")

    # And the success path must still work, with ok set explicitly on both sides.
    good = run_mapping([
        {"kind": "external", "backend": "grok", "unit": "threat",
         "result": {"ok": True, "error": "", "findings": [{"summary": "[threat] x"}]}},
        {"kind": "claude", "unit": "correctness",
         "result": {"findings": [{"summary": "[correctness] y"}]}},
    ])
    for got in good:
        if got["ok"] is not True:
            fail("mapping", f"a valid result was mapped to ok={got['ok']!r}, expected True")
        if len(got["findings"]) != 1:
            fail("mapping", f"a valid result lost its findings: {got['findings']!r}")
    # An empty findings list is a REVIEW, not a failure — the distinction the
    # whole fix rests on.
    empty = run_mapping([{"kind": "claude", "unit": "correctness", "result": {"findings": []}}])[0]
    if empty["ok"] is not True:
        fail("mapping", "a genuine empty review was misreported as a failure")


def main():
    if not SOURCE.exists():
        sys.exit(f"FATAL: {SOURCE} not found")
    for case in (
        case_mapping_fails_closed,
        case_resultless_shapes,
        case_total_family_loss,
        case_partial_family_loss,
        case_voice_count_note,
        case_healthy_run_is_quiet,
        case_claude_only_is_quiet,
        case_identity_mismatch_fails_closed,
    ):
        case()
    if failures:
        print("voice-accounting: FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("voice-accounting: all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
