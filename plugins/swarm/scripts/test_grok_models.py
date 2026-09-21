#!/usr/bin/env python3
"""Tests for the `grok models` list parser in agents.sh.

WHY THIS EXISTS: the parser reads a HUMAN-FORMATTED CLI listing, and that format
has already changed twice — 0.2.101 renamed the model, 1.0.3 changed the bullet
marker so only the DEFAULT keeps `*`. The second change made the parser report
"this CLI does not offer grok-4.5" for a CLI that offers it, dropping grok from
every review: the third model family gone, silently, which is the exact failure
mode the swarm timeout work exists to prevent. A format the parser mis-reads
costs a whole voice and looks like nothing at all, so pin the shapes.

The parser is the shipped `grok_parse_models` function, sourced from agents.sh
and driven directly — never re-typed here, and no longer scraped out with a
regex anchor that a reformat can silently break.
"""
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ADAPTER = HERE / "agents.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


# Drive the SHIPPED function, do not scrape it. Both test files used to pull the
# awk program out of agents.sh with their own regex anchor, and the two anchors
# differed in strictness: a reformat of the assignment (moving the printf, a
# here-string, an added pipe stage) kept one matching and left this file exiting
# 1 with "could not extract" — 20+ format regressions off, silently, until
# someone repaired the anchor. Its own function needs no anchor at all.
_HAVE_FN = subprocess.run(
    ["bash", "-c", f'source "{ADAPTER}"; declare -f grok_parse_models >/dev/null'],
    capture_output=True, text=True,
)
if _HAVE_FN.returncode != 0:
    print("grok-models tests FAILED:\n  - agents.sh does not define grok_parse_models "
          "(the parser moved or was renamed — fix this test, do not ignore it)")
    sys.exit(1)


def parse(listing):
    """Run the shipped parser over a raw `grok models` listing."""
    out = subprocess.run(
        ["bash", "-c", f'source "{ADAPTER}"; grok_parse_models'],
        input=listing, capture_output=True, text=True,
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
REPO = HERE.parents[2]


def run_bash(*lines, models=None, env=None):
    """Source agents.sh and run helper lines against a faked model list.

    `_grok_models` is normally filled by a network call; overriding it (and the
    memo flag) keeps these tests hermetic and lets us assert on catalogs that do
    not exist yet — which is the whole point of a discovery mechanism.
    """
    # Stub BOTH memoized probes. Faking only the model list left the
    # `--prompt-file` capability probe live, so `grok_model_offered` shelled out
    # to whatever `grok` happened to be on the host's PATH: green on a machine
    # with a current CLI, red on one without, and a network call inside a test
    # that advertises itself as hermetic. Pre-setting the memo flags is the same
    # mechanism the adapter uses, so nothing is monkey-patched.
    pre = ['_grok_help_done=1', '_grok_help_rc=0']
    if models is not None:
        pre += [f'_grok_models_done=1', f'_grok_models={_q(models)}']
    harness = "set -euo pipefail\nsource '%s'\n%s\n" % (
        ADAPTER, "\n".join(pre + list(lines)))
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(["bash", "-c", harness], cwd=str(REPO), env=e,
                   capture_output=True, text=True, timeout=30)


def _q(text):
    return "'" + text.replace("'", "'\\''") + "'"


FAKE_COMPAT = r"""
_grok_compat() {
  # Hermetic stand-in for grok-compat.py: verdicts come from the environment and
  # every call is logged, so "how many probes would have been paid" is asserted.
  echo "$1 ${2:-}" >>"$COMPAT_LOG"
  if [[ "$1" == "known" ]]; then printf '%s\n' ${COMPAT_KNOWN:-}; return 0; fi
  # COMPAT_CACHED = verdicts already in the cache (free); anything else costs a
  # probe under `ensure` and has no verdict under `check`.
  local src=probe
  case " ${COMPAT_CACHED:-} " in *" $2 "*) src=cache ;; esac
  if [[ "$1" == "check" && "$src" == "probe" ]]; then printf 'compat=unknown\nsource=none\nreason=not cached\n'; return 3; fi
  [[ -n "${COMPAT_SRC:-}" ]] && src="$COMPAT_SRC"
  case " ${COMPAT_OK:-} " in *" $2 "*) printf 'compat=ok\nsource=%s\n%s' "$src" "${COMPAT_NOTE:+cache_note=$COMPAT_NOTE
}"; return 0 ;; esac
  case " ${COMPAT_FAIL:-} " in *" $2 "*) printf 'compat=failed\nsource=%s\nreason=structuredOutput is null\n' "$src"; return 1 ;; esac
  printf 'compat=unknown\nsource=probe\nreason=probe timed out\n'; return 3
}
"""


def newer(a, b):
    r = run_bash(f'grok_latest_newer {a} {b} && echo yes || echo no')
    return r.stdout.strip() == "yes"


# --- version ordering is COMPONENT-WISE, not decimal --------------------------
# The rule itself is pinned in test_grok_latest.py; these confirm the ADAPTER is
# wired to it (a sourcing failure would otherwise leave every check vacuous).
check("4.20 is newer than 4.6 (component-wise, not decimal)", newer("grok-4.20", "grok-4.6"))
check("5.0 is newer than 4.20 (major wins)", newer("grok-5.0", "grok-4.20"))
check("garbage version does not abort", not newer("grok-4.x", "grok-4.6"))

LIVE_CATALOG = "\n".join([
    "grok-4.7", "grok-4.7-build-fast", "grok-4.6", "grok-4.5", "grok-4.3",
    "grok-3-mini", "grok-3-mini-fast",
    "grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
    "grok-4.20-multi-agent-0309",
    "grok-build-0.1", "grok-composer-2.5-fast",
    "grok-imagine-image", "grok-imagine-video-1.5-preview",
])
r = run_bash('_grok_canonical_desc', models=LIVE_CATALOG)
check("candidates are the canonical ids only, newest first",
      r.stdout.split() == ["grok-4.7", "grok-4.6", "grok-4.5", "grok-4.3"])

# A prose bullet mentioning a model id must NOT be harvested as an offered model:
# discovery would select it, and every call would then die at launch with
# "unknown model id" — the whole grok family gone, silently.
PROSE_LIST = """Available models:
  * grok-4.5 (default)
  - grok-4.6 reaches end of life on 2026-12-01
"""
check("a prose bullet is not parsed as an offered model",
      parse(PROSE_LIST) == ["grok-4.5"])

ANNOTATED = """Available models:
  * grok-4.6 [stable]
  - `grok-4.5` (legacy)
"""
check("bracketed annotations and backticked ids still parse",
      sorted(parse(ANNOTATED)) == ["grok-4.5", "grok-4.6"])

# --- selection: latest canonical + MEASURED compatibility ----------------------
import tempfile
_LOGDIR = tempfile.mkdtemp(prefix="grok-models-test-")
_n = [0]


def select(models, pin="", state="", **compat):
    """Returns (fields dict, list of compat calls)."""
    _n[0] += 1
    log = os.path.join(_LOGDIR, f"compat-{_n[0]}.log")
    open(log, "w").close()
    env = {"COMPAT_LOG": log}
    env.update({f"COMPAT_{k.upper()}": v for k, v in compat.items()})
    r = run_bash(FAKE_COMPAT, f'_grok_catalog_state={_q(state)}',
                 f'grok_select_model {_q(pin)}',
                 'printf "sel=%s\nlatest=%s\nsrc=%s\ncat=%s\ncsrc=%s\ndeg=%s\n" '
                 '"$GROK_SELECTED_MODEL" "$GROK_LATEST_CANDIDATE" "$GROK_SELECT_SOURCE" '
                 '"$GROK_CATALOG" "$GROK_COMPAT_SOURCE" "$GROK_SELECT_DEGRADED"',
                 models=models, env=env)
    f = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    return f, open(log).read().split("\n")[:-1]


f, calls = select(LIVE_CATALOG, ok="grok-4.7 grok-4.6 grok-4.5")
check("4.7 adoption: the newest canonical model is selected", f["sel"] == "grok-4.7")
check("…as source=latest with nothing degraded", f["src"] == "latest" and f["deg"] == "")
check("…after exactly ONE compat lookup (no walk down the list)", calls == ["ensure grok-4.7"])

f, _ = select("grok-4.7\ngrok-4.8\ngrok-5.0\ngrok-5.1-preview\ngrok-6.0", ok="grok-5.0")
check("a later 5.x is adopted with no code edit", f["sel"] == "grok-5.0" and f["latest"] == "grok-5.0")

f, calls = select(LIVE_CATALOG, ok="grok-4.6", fail="grok-4.7")
check("incompatible latest → explicit older-compatible fallback",
      f["sel"] == "grok-4.6" and f["src"] == "older-compatible")
check("…the latest candidate is still REPORTED, and the reason names it",
      f["latest"] == "grok-4.7" and "grok-4.7" in f["deg"] and "NOT enforced" in f["deg"]
      and "using grok-4.6" in f["deg"])

f, calls = select(LIVE_CATALOG, ok="grok-4.3")
check("PAID probing is bounded: two probes, then the rest are cache-only lookups",
      f["sel"] == "" and f["src"] == "none"
      and calls == ["ensure grok-4.7", "ensure grok-4.6", "check grok-4.5", "check grok-4.3"])
f, calls = select(LIVE_CATALOG, fail="grok-4.7 grok-4.6", ok="grok-4.5", cached="grok-4.7 grok-4.6 grok-4.5")
check("cached failures are FREE: they must not hide an older model known to be fine",
      f["sel"] == "grok-4.5" and f["src"] == "older-compatible")
f, calls = select("\n".join(f"grok-4.{i}" for i in range(30)), cached=" ".join(f"grok-4.{i}" for i in range(30)),
                  fail=" ".join(f"grok-4.{i}" for i in range(30)))
check("a huge catalog cannot drive an unbounded walk", len(calls) == 6 and f["sel"] == "")
f, calls = select(LIVE_CATALOG, ok="grok-4.3")
check("…and an inconclusive probe is reported as not established, not as a failure of the model",
      "not established" in f["deg"] and "timed out" in f["deg"])

f, calls = select("grok-4.7-build-fast\ngrok-3-mini\ngrok-6.0", ok="grok-4.7-build-fast")
check("valid catalog, no canonical candidate → nothing selected, no probe, no last-known guess",
      f["sel"] == "" and f["cat"] == "no-candidate" and calls == [])
check("…and the reason lists what IS offered", "grok-4.7-build-fast" in f["deg"])

for state in ("unreachable", "unparseable"):
    f, calls = select("", state=state, known="grok-4.6 grok-4.7 grok-4.7-build-fast")
    check(f"{state} catalog → LAST-KNOWN measured model, labelled as such",
          f["sel"] == "grok-4.7" and f["src"] == "last-known" and f["cat"] == state
          and "last-known" in f["deg"] and calls == ["known "])
    f, _ = select("", state=state)
    check(f"{state} catalog and no last-known → nothing selected (no silent default id)",
          f["sel"] == "" and "no last-known" in f["deg"])
check("unreachable and no-candidate are DISTINCT states",
      select("", state="unreachable")[0]["cat"] != select("grok-3-mini")[0]["cat"])

# --- an explicit pin is never reinterpreted ----------------------------------------
f, calls = select(LIVE_CATALOG, pin="grok-4.5", ok="grok-4.7 grok-4.5")
check("explicit pin beats discovery", f["sel"] == "grok-4.5" and f["src"] == "pinned")
check("…only the PIN is checked, and the newer latest is reported alongside",
      calls == ["ensure grok-4.5"] and f["latest"] == "grok-4.7" and "grok-4.7" in f["deg"])
f, _ = select(LIVE_CATALOG, pin="grok-4.7-build-fast", ok="grok-4.7-build-fast")
check("a variant may be pinned deliberately", f["sel"] == "grok-4.7-build-fast")
f, calls = select(LIVE_CATALOG, pin="grok-4.4", ok="grok-4.4 grok-4.7")
check("a pin the CLI does not offer → nothing runs (NOT silently the latest), no probe spent",
      f["sel"] == "" and "not offered" in f["deg"] and calls == [])
f, _ = select(LIVE_CATALOG, pin="grok-4.5", fail="grok-4.5", ok="grok-4.7")
check("an incompatible pin → nothing runs (NOT silently the latest)",
      f["sel"] == "" and f["src"] == "pinned" and "NOT enforced" in f["deg"])
f, _ = select("", state="unreachable", pin="grok-4.5", ok="grok-4.5")
check("a pin still runs when the catalog is unreadable (absence of evidence)", f["sel"] == "grok-4.5")

# --- the pin is read by the ADAPTER, so every entry point judges the same request ---------
def select_env(models, swarm_model, **compat):
    _n[0] += 1
    log = os.path.join(_LOGDIR, f"compat-{_n[0]}.log"); open(log, "w").close()
    env = {"COMPAT_LOG": log, "SWARM_GROK_MODEL": swarm_model}
    env.update({f"COMPAT_{k.upper()}": v for k, v in compat.items()})
    r = run_bash(FAKE_COMPAT, 'grok_model_offered && echo READY || echo NOT-READY',
                 'printf "sel=%s\nsrc=%s\nreq=%s\ndeg=%s\n" "$GROK_SELECTED_MODEL" "$GROK_SELECT_SOURCE" '
                 '"$GROK_REQUESTED" "$GROK_SELECT_DEGRADED"', models=models, env=env)
    f = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    f["ready"] = "NOT-READY" not in r.stdout
    return f


f = select_env(LIVE_CATALOG, "grok-4.5", ok="grok-4.7 grok-4.5")
check("SWARM_GROK_MODEL pins READINESS too (list/ready judge the pin, not latest)",
      f["ready"] and f["sel"] == "grok-4.5" and f["src"] == "pinned")
f = select_env(LIVE_CATALOG, "grok-4.5", ok="grok-4.7", fail="grok-4.5")
check("a failed env pin → NOT ready, nothing selected (never silently latest)",
      not f["ready"] and f["sel"] == "" and f["src"] == "pinned")
for bad in ("grok-4.5\nselected=grok-9.9", "gpt-5", "grok-", "grok-4.5 --tools all", "$(id)"):
    f = select_env(LIVE_CATALOG, bad, ok="grok-4.7")
    check(f"malformed pin {bad!r} → nothing runs, and the raw text is never echoed",
          not f["ready"] and f["sel"] == "" and f["req"] == "(malformed)" and "malformed" in f["deg"])

# --- voices never pay: SWARM_GROK_PROBE=0 reads the cache only ------------------------
_n[0] += 1
_log = os.path.join(_LOGDIR, "voice.log"); open(_log, "w").close()
r = run_bash(FAKE_COMPAT, 'grok_select_model grok-4.7', 'grok_select_model grok-4.7',
             'printf "%s" "$GROK_SELECTED_MODEL"', models=LIVE_CATALOG,
             env={"COMPAT_LOG": _log, "COMPAT_OK": "grok-4.7", "COMPAT_CACHED": "grok-4.7",
                  "SWARM_GROK_PROBE": "0"})
check("a workflow voice uses `check` (never probes) and the selection is memoized",
      r.stdout == "grok-4.7" and open(_log).read() == "check grok-4.7\n")

# --- readiness must agree with what would actually RUN ------------------------
def ready(models, pin="", **compat):
    env = {"COMPAT_LOG": os.devnull}
    env.update({f"COMPAT_{k.upper()}": v for k, v in compat.items()})
    r = run_bash(FAKE_COMPAT, f'grok_model_offered {_q(pin)} && echo ready || echo not-ready',
                 models=models, env=env)
    return r.stdout.strip()


check("readiness: a selectable model → ready", ready(LIVE_CATALOG, ok="grok-4.7") == "ready")
check("readiness: nothing compatible → not ready", ready(LIVE_CATALOG) == "not-ready")
check("readiness: no canonical model → not ready", ready("grok-6.0\ngrok-3-mini", ok="grok-6.0") == "not-ready")
check("readiness judges the PIN when one is given",
      ready(LIVE_CATALOG, pin="grok-4.5", ok="grok-4.7") == "not-ready")

# --- the retired allowlist must stay retired ---------------------------------------------
_src = ADAPTER.read_text()
for gone in ("GROK_SCHEMA_VERIFIED=", "GROK_DEFAULT_MODEL=", "GROK_CANONICAL_RE="):
    check(f"adapter no longer defines {gone.rstrip('=')} (no per-version allowlist, no baked-in id)",
          gone not in _src)
import re as _re
_ids = set(_re.findall(r"^[^#\n]*\b(grok-[0-9]+\.[0-9]+)\b", _src, _re.M))
check(f"no concrete grok version id in adapter CODE (found {sorted(_ids)})", not _ids)

# --- grok-model: the selection as data ---------------------------------------------------
r = run_bash(FAKE_COMPAT, 'backend_installed() { return 0; }', 'available_version() { echo "grok 1.0.40 (x)"; }',
             f'GROK_AUTH_FILE={_q(str(ADAPTER))}', 'subcmd_grok_model; echo "rc=$?"', models=LIVE_CATALOG,
             env={"COMPAT_LOG": os.devnull, "COMPAT_OK": "grok-4.6", "COMPAT_FAIL": "grok-4.7",
                  "COMPAT_SRC": "probe"})
kv = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
check("grok-model reports selected / latest candidate / source / provenance as data",
      kv.get("selected") == "grok-4.6" and kv.get("latest_candidate") == "grok-4.7"
      and kv.get("source") == "older-compatible" and kv.get("compat_source") == "probe"
      and kv.get("requested") == "latest" and kv.get("catalog") == "ok"
      and kv.get("cli_version") == "1.0.40" and "grok-4.7" in kv.get("degraded", "") and kv.get("rc") == "0")
r = run_bash(FAKE_COMPAT, 'backend_installed() { return 0; }', 'available_version() { echo "grok 1.0.40"; }',
             f'GROK_AUTH_FILE={_q(str(ADAPTER))}', 'subcmd_grok_model || echo "rc=$?"', models=LIVE_CATALOG,
             env={"COMPAT_LOG": os.devnull})
kv = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
r = run_bash(FAKE_COMPAT, 'backend_installed() { return 0; }', 'available_version() { echo "grok 1.0.40"; }',
             f'GROK_AUTH_FILE={_q(str(ADAPTER))}', 'subcmd_grok_model --model grok-4.5; echo "rc=$?"',
             models=LIVE_CATALOG, env={"COMPAT_LOG": os.devnull, "COMPAT_OK": "grok-4.5 grok-4.7"})
kvp = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
check("grok-model --model <pin>: pinned, requested echoed, latest still reported",
      kvp.get("selected") == "grok-4.5" and kvp.get("requested") == "grok-4.5"
      and kvp.get("source") == "pinned" and kvp.get("latest_candidate") == "grok-4.7")
r = run_bash(FAKE_COMPAT, 'backend_installed() { return 0; }', 'available_version() { echo "grok 1.0.40"; }',
             f'GROK_AUTH_FILE={_q(str(ADAPTER))}', 'subcmd_grok_model || echo "rc=$?"', models=LIVE_CATALOG,
             env={"COMPAT_LOG": os.devnull, "COMPAT_OK": "grok-4.7",
                  "COMPAT_NOTE": "verdict could not be cached (EACCES)"})
kvp = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
check("grok-model: a verdict that could not be CACHED cannot be frozen (voices only read the cache)",
      kvp.get("selected") == "" and kvp.get("rc") == "1" and "could not be cached" in kvp.get("degraded", ""))
check("grok-model: nothing selectable → selected empty, exit 1, reason present",
      kv.get("selected") == "" and kv.get("rc") == "1" and kv.get("degraded"))


# One verdict for the whole file. It has to be the LAST statement: an earlier
# copy of this block sat between the two halves, so every discovery check below
# it recorded failures into FAILS that nothing ever read — the exact
# vacuously-green failure this file warns about at the top.
if FAILS:
    print("grok-models tests FAILED:", file=sys.stderr)
    for f in FAILS:
        print(f"  - {f}", file=sys.stderr)
    sys.exit(1)
print("grok-models: all tests passed")
