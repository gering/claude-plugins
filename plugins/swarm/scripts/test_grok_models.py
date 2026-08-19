#!/usr/bin/env python3
"""Tests for the `grok models` list parser in agents.sh.

WHY THIS EXISTS: the parser reads a HUMAN-FORMATTED CLI listing, and that format
has already changed twice — 0.2.101 renamed the model, 1.0.3 changed the bullet
marker so only the DEFAULT keeps `*`. The second change made the parser report
"this CLI does not offer grok-4.5" for a CLI that offers it, dropping grok from
every review: the third model family gone, silently, which is the exact failure
mode the swarm timeout work exists to prevent. A format the parser mis-reads
costs a whole voice and looks like nothing at all, so pin the shapes.

The awk program is extracted from agents.sh and run as-is — never re-typed here,
or the test would validate a copy while the shipped parser drifted.
"""
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ADAPTER = HERE / "agents.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


sh = ADAPTER.read_text(encoding="utf-8")

# Pull the awk program out of the assignment, exactly as shipped. Anchor on the
# variable name and stop at `| awk '` rather than re-typing the printf in between:
# matching backslashes through a Python regex into a shell string is its own
# escaping puzzle, and getting it wrong makes the extraction silently return
# nothing — which would leave every assertion below passing over empty output.
# (That is not hypothetical: the first version of this test did exactly that.)
m = re.search(r"_grok_models=\"\$\(printf.*?\| awk '\n(.*?)'\)\"", sh, re.S)
check("adapter: the grok-models awk program was found", m)
# Fail LOUD rather than vacuously green if the extraction breaks.
if not m:
    print("grok-models tests FAILED:\n  - could not extract the awk program from agents.sh "
          "(the assignment shape changed — fix this test's anchor, do not ignore it)")
    sys.exit(1)


def parse(listing):
    """Run the shipped awk program over a raw `grok models` listing."""
    if not m:
        return []
    out = subprocess.run(
        ["awk", m.group(1)], input=listing, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


# --- the format that shipped before 1.0.3: every model marked with `*` --------
OLD = """You are logged in with grok.com.

Available models:
  * grok-4.5 (default)
  * grok-build
"""
check("0.2.x format: both models parsed", parse(OLD) == ["grok-4.5", "grok-build"])

# --- grok 1.0.3: `*` marks ONLY the default, others use `-` ------------------
# Verbatim shape from the installed CLI (2026-08-16). This is the regression:
# a `*`-only matcher returns just grok-4.6, so the pinned grok-4.5 reads as
# "not offered" and grok is dropped from the ensemble.
NEW = """You are logged in with grok.com.

Default model: grok-4.6

Available models:
  * grok-4.6 (default)
  - grok-4.5
"""
check("1.0.3 format: the non-default model is seen", "grok-4.5" in parse(NEW))
check("1.0.3 format: the default is seen too", "grok-4.6" in parse(NEW))
check("1.0.3 format: exactly the two listed models", sorted(parse(NEW)) == ["grok-4.5", "grok-4.6"])

# --- the guard the marker anchor was protecting -------------------------------
# Only ONE id per bullet line, and prose ABOUT another model must not register it
# as offered — otherwise a retired model reads as available and the adapter pins
# a model the CLI will reject at launch.
PROSE = """Available models:
  * grok-5 (successor to grok-4.5)
"""
check("prose naming a retired model does not make it 'offered'", parse(PROSE) == ["grok-5"])

# Non-bullet lines are not model entries; a bare mention in a header or footer
# must not count, or "Default model: grok-4.6" alone would satisfy the check.
NO_BULLETS = """You are logged in with grok.com.

Default model: grok-4.6

Some note mentioning grok-4.5 in passing.
"""
check("non-bullet lines are ignored", parse(NO_BULLETS) == [])

# An empty/unparseable list must yield nothing, so the caller takes its documented
# degrade path (trust auth) instead of asserting a model is gone.
check("empty input yields no ids", parse("") == [])
check("header-only input yields no ids", parse("Available models:\n") == [])

# Punctuation glued to an id must not ride along — the exact-match downstream
# would fail and report a present model as missing.
PUNCT = """Available models:
  - grok-4.5,
  * grok-4.6.
"""
check("trailing punctuation is not captured", sorted(parse(PUNCT)) == ["grok-4.5", "grok-4.6"])

# A hyphen inside the id must not be confused with the bullet marker.
check("ids with dots/dashes survive", "grok-4.5" in parse("  - grok-4.5\n"))

# =============================================================================
# Canonical model discovery
# =============================================================================
# The parser above answers "what does the CLI list"; this half answers "which of
# those may we RUN". Both gates are load-bearing and fail in opposite directions:
# too strict drops grok from the ensemble (a whole model family, silently), too
# loose picks a model that accepts --json-schema but returns structuredOutput:
# null — which fails only AFTER a full review has been paid for.
import os
import subprocess as _sp

REPO = HERE.parents[2]


def sh(*lines, models=None, env=None):
    """Source agents.sh and run helper lines against a faked model list.

    `_grok_models` is normally filled by a network call; overriding it (and the
    memo flag) keeps these tests hermetic and lets us assert on catalogs that do
    not exist yet — which is the whole point of a discovery mechanism.
    """
    pre = []
    if models is not None:
        pre = [f'_grok_models_done=1', f'_grok_models={_q(models)}']
    harness = "set -euo pipefail\nsource '%s'\n%s\n" % (
        ADAPTER, "\n".join(pre + list(lines)))
    e = os.environ.copy()
    if env:
        e.update(env)
    return _sp.run(["bash", "-c", harness], cwd=str(REPO), env=e,
                   capture_output=True, text=True, timeout=30)


def _q(text):
    return "'" + text.replace("'", "'\\''") + "'"


def newer(a, b):
    r = sh(f'_grok_version_newer {a} {b} && echo yes || echo no')
    return r.stdout.strip() == "yes"


# --- version ordering is COMPONENT-WISE, not decimal --------------------------
# This is the subtle one: read as a fraction, 4.20 < 4.6. The provider means the
# 20th minor release, and its catalog already ships 4.20-derived ids — so a
# decimal comparison would pin the ensemble to an older model forever.
check("4.20 is newer than 4.6 (component-wise, not decimal)", newer("grok-4.20", "grok-4.6"))
check("4.6 is newer than 4.5", newer("grok-4.6", "grok-4.5"))
check("5 is newer than 4.20 (major wins)", newer("grok-5", "grok-4.20"))
check("4.5 is NOT newer than 4.6", not newer("grok-4.5", "grok-4.6"))
check("a model is not newer than itself", not newer("grok-4.6", "grok-4.6"))
check("bare major compares against a minor", newer("grok-5", "grok-4.6"))
# A non-numeric component must read as "not newer" rather than crash the adapter
# under `set -e` mid-review.
check("garbage version does not abort", not newer("grok-4.x", "grok-4.6"))

# --- the canonical filter: only bare version ids ------------------------------
LIVE_CATALOG = "\n".join([
    "grok-4.6", "grok-4.5", "grok-4.3",
    "grok-3-mini", "grok-3-mini-fast",
    "grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
    "grok-4.20-multi-agent-0309",
    "grok-build-0.1", "grok-composer-2.5-fast",
    "grok-imagine-image", "grok-imagine-video-1.5-preview",
])
r = sh('_grok_highest_canonical', models=LIVE_CATALOG)
check("live catalog: the highest canonical id wins", r.stdout.strip() == "grok-4.6")

for rejected in ("grok-3-mini", "grok-4.20-0309-reasoning", "grok-4.20-multi-agent-0309",
                 "grok-build-0.1", "grok-composer-2.5-fast", "grok-imagine-image"):
    rr = sh(f'if [[ {_q(rejected)} =~ $GROK_CANONICAL_RE ]]; then echo match; else echo no; fi')
    check(f"filter rejects {rejected}", rr.stdout.strip() == "no")
for accepted in ("grok-4.3", "grok-4.5", "grok-4.6", "grok-5", "grok-4.20"):
    rr = sh(f'if [[ {_q(accepted)} =~ $GROK_CANONICAL_RE ]]; then echo match; else echo no; fi')
    check(f"filter accepts {accepted}", rr.stdout.strip() == "match")

# A catalog that only regresses to grok-3 must not pull the adapter backwards.
r = sh('_grok_highest_canonical', models="grok-3-mini\ngrok-3-mini-fast")
check("a grok-3-only catalog yields no canonical model", r.stdout.strip() == "")

# --- the schema gate: verified selects, unverified only REPORTS ---------------
def select(models, override=""):
    r = sh(f'grok_select_model {_q(override)}',
           'printf "%s|%s" "$GROK_SELECTED_MODEL" "$GROK_SELECT_NOTE"',
           models=models)
    model, _, note = r.stdout.partition("|")
    return model, note


m, note = select(LIVE_CATALOG)
check("selects the newest VERIFIED model", m == "grok-4.6")
check("nothing to report when the newest is verified", note == "")

# The upgrade prompt: a newer canonical model appears that nobody has verified.
# It must be NAMED but never selected — silently adopting it is what burns a
# review on structuredOutput:null.
m, note = select("grok-7\n" + LIVE_CATALOG)
check("an unverified newer model is NOT selected", m == "grok-4.6")
check("an unverified newer model IS reported", "grok-7" in note)

# Only older verified models on offer → take the newest of those, no note.
m, note = select("grok-4.5\ngrok-4.3")
check("falls back to the newest verified model on offer", m == "grok-4.5")
check("no note when nothing newer exists", note == "")

# Canonical models exist but none verified → keep the pin and say so, rather than
# run something unproven.
m, note = select("grok-9\ngrok-8")
check("no verified model → keeps the pin", m == "grok-4.6")
check("no verified model → reports why", "no schema-verified model" in note)

# An empty/unusable list must keep the pin: dropping grok entirely is worse than
# running the known-good model (grok_model_fetch already reported the degrade).
m, note = select("")
check("empty model list keeps the pin", m == "grok-4.6")

# An explicit override wins over discovery — but the run_grok preflight still
# gates it on the verified table (asserted live elsewhere).
m, _ = select(LIVE_CATALOG, override="grok-4.5")
check("explicit override beats discovery", m == "grok-4.5")

# --- readiness must agree with what would actually RUN ------------------------
# The 1.0.3 regression: readiness said "grok-4.5 not offered" for a CLI that
# offered it, and grok vanished from every review. Readiness now asks whether ANY
# verified model is on offer, which is exactly what grok_select_model resolves.
r = sh('grok_model_offered && echo ready || echo not-ready', models=LIVE_CATALOG)
check("readiness: verified model on offer → ready", r.stdout.strip() == "ready")
r = sh('grok_model_offered && echo ready || echo not-ready', models="grok-9\ngrok-3-mini")
check("readiness: no verified model → not ready", r.stdout.strip() == "not-ready")
r = sh('grok_model_offered && echo ready || echo not-ready', models="")
check("readiness: unusable list trusts auth (ready)", r.stdout.strip() == "ready")

# The pin itself must be verified, or the fallback path selects a model that
# run_grok then refuses — a self-inflicted outage on every degraded run.
r = sh('_grok_schema_verified "$GROK_DEFAULT_MODEL" && echo yes || echo no')
check("the pinned fallback model is itself schema-verified", r.stdout.strip() == "yes")


# One verdict for the whole file. It has to be the LAST statement: an earlier
# copy of this block sat between the two halves, so every discovery check below
# it recorded failures into FAILS that nothing ever read — the exact
# vacuously-green failure this file warns about at the top.
if FAILS:
    print("grok-models tests FAILED:")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("grok-models: all tests passed")
