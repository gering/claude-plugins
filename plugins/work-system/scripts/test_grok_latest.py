#!/usr/bin/env python3
"""Tests for lib-grok-latest.sh — the automatic "newest canonical Grok" rule.

Hermetic: the library is pure text-in/text-out, so nothing here calls `grok` or
the network. The point of these fixtures is that a FUTURE release (grok-4.8,
grok-4.20, grok-5.0) is adopted with no code edit, while every variant that is
not a drop-in substitute is never chosen automatically.

This file and the library ship identically in the swarm and work-system plugins;
the last check pins that the two copies have not drifted.
"""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LIB = HERE / "lib-grok-latest.sh"
PLUGINS = HERE.parents[1]
HOMES = ("swarm", "work-system")

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def sh(script, stdin=""):
    # /bin/bash where present: macOS ships 3.2 there, the floor this must run on.
    bash = "/bin/bash" if pathlib.Path("/bin/bash").exists() else "bash"
    return subprocess.run(
        [bash, "-c", f'set -eu; . "{LIB}"; {script}'],
        input=stdin, capture_output=True, text=True,
    )


def sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def canonical(model_id):
    return sh(f"grok_latest_is_canonical {sh_quote(model_id)}").returncode == 0


def newer(a, b):
    return sh(f"grok_latest_newer {sh_quote(a)} {sh_quote(b)}").returncode == 0


def pick(ids):
    return sh("grok_latest_pick", "\n".join(ids) + "\n").stdout


def from_listing(listing, rc=0):
    out = sh(f"grok_latest_from_listing {rc}", listing)
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)


# --- canonical ids ------------------------------------------------------------
for ok in ["grok-4.5", "grok-4.6", "grok-4.7", "grok-4.0", "grok-4.20", "grok-5.0", "grok-5.13"]:
    check(f"canonical accepted: {ok}", canonical(ok))

REJECTED = [
    "grok-4", "grok-5",                          # bare majors
    "grok-4.7.1", "grok-5.0.0",                  # patch versions
    "grok-4.7-build-fast", "grok-4.7-build", "grok-4.7-fast", "grok-4.7-preview",
    "grok-4.20-0309-reasoning", "grok-4.20-multi-agent-0309", "grok-4.7-2026-09-01",
    "grok-build-0.1", "grok-composer-2.5-fast", "grok-imagine-1.0", "grok-code-fast-1",
    "grok-3.5", "grok-3-mini", "grok-6.0", "grok-9.1", "grok-10.2", "grok-45.1",
    "grok-4.", "grok-.7", "grok-4.x", "grok-4.7 ", " grok-4.7", "grok-4.7\r",
    "Grok-4.7", "grok4.7", "xgrok-4.7", "grok-4.7;rm", "grok-4.$(id)", "grok-4.-1",
    "grok-4.+7", "grok-4.7e1", "grok-4.99999", "", "grok-", "-m",
]
for bad in REJECTED:
    check(f"canonical rejected: {bad!r}", not canonical(bad))

# --- ordering: integers, never strings or decimals -----------------------------
check("4.7 > 4.6", newer("grok-4.7", "grok-4.6"))
check("4.20 > 4.9 (20th minor, not the decimal 4.2)", newer("grok-4.20", "grok-4.9"))
check("4.10 > 4.9", newer("grok-4.10", "grok-4.9"))
check("5.0 > 4.20", newer("grok-5.0", "grok-4.20"))
check("4.9 not > 4.20", not newer("grok-4.9", "grok-4.20"))
check("equal is not newer", not newer("grok-4.7", "grok-4.7"))
check("leading zero compares as a number (4.08 > 4.7), no octal abort",
      newer("grok-4.08", "grok-4.7"))
check("4.09 vs 4.9: equal value, not newer either way",
      not newer("grok-4.09", "grok-4.9") and not newer("grok-4.9", "grok-4.09"))
check("a variant is never 'newer'", not newer("grok-4.8-build-fast", "grok-4.7"))
check("nothing is newer than a non-canonical baseline (caller keeps what it had)",
      not newer("grok-4.7", "grok-6.0"))

# --- pick -----------------------------------------------------------------------
check("adopts 4.7 from today's catalog",
      pick(["grok-4.7", "grok-4.7-build-fast", "grok-4.6", "grok-4.5"]) == "grok-4.7")
check("adopts a later 4.x with no code edit",
      pick(["grok-4.5", "grok-4.8", "grok-4.7"]) == "grok-4.8")
check("numeric, order-independent: 4.20 beats 4.9",
      pick(["grok-4.9", "grok-4.20", "grok-4.6"]) == "grok-4.20")
check("adopts 5.0 over any 4.x", pick(["grok-4.20", "grok-5.0", "grok-4.9"]) == "grok-5.0")
check("a newer VARIANT never wins",
      pick(["grok-4.7", "grok-4.9-build-fast", "grok-5.1-preview", "grok-5"]) == "grok-4.7")
check("major 6+ never wins", pick(["grok-4.7", "grok-6.0", "grok-10.1"]) == "grok-4.7")
check("only variants → nothing", pick(["grok-4.7-build-fast", "grok-build"]) == "")
check("empty input → nothing", pick([]) == "")
check("last line without newline still counts",
      sh("grok_latest_pick", "grok-4.5\ngrok-4.7").stdout == "grok-4.7")

# --- listing → state -------------------------------------------------------------
LIVE_1_0_40 = """You are logged in with grok.com.

Default model: grok-4.7

Available models:
  * grok-4.7 (default)
  - grok-4.7-build-fast
  - grok-4.6
  - grok-4.5
"""
r = from_listing(LIVE_1_0_40)
check("live 1.0.40 listing: ok + latest 4.7", r.get("catalog") == "ok" and r.get("latest") == "grok-4.7")
check("live 1.0.40 listing: every offered id reported (pins incl. variants)",
      r.get("offered") == "grok-4.7 grok-4.7-build-fast grok-4.6 grok-4.5")

FUTURE = """Available models:
  * grok-4.20 (default)
  - grok-5.0 [stable]
  - grok-5.1 (coming soon)
  - grok-5.2-preview
  - grok-4.9
"""
r = from_listing(FUTURE)
check("future listing: 5.0 adopted; withdrawn 5.1 and preview 5.2 ignored",
      r.get("latest") == "grok-5.0")

check("the DEFAULT marker is not the selector (default 4.5, latest 4.7)",
      from_listing("  * grok-4.5 (default)\n  - grok-4.7\n").get("latest") == "grok-4.7")
check("prose mentioning a newer id is not an offer",
      from_listing("  * grok-4.6 (default)\n  - grok-4.9 reaches general availability soon\n"
                   "Note: grok-5.0 is planned.\n").get("latest") == "grok-4.6")

r = from_listing("  * grok-4.7-build-fast (default)\n  - grok-3-mini\n  - grok-6.0\n")
check("valid catalog, no supported candidate → no-candidate (NOT unreachable)",
      r.get("catalog") == "no-candidate" and "latest" not in r)
check("no-candidate still reports what IS offered", r.get("offered", "").startswith("grok-4.7-build-fast"))

for name, listing in [("empty", ""), ("whitespace", "\n\n"), ("html error page", "<html>502</html>\n"),
                      ("json blob", '{"models": ["grok-4.7"]}\n'), ("binary-ish", "\x00\x01 grok-4.7\n")]:
    r = from_listing(listing)
    check(f"malformed catalog ({name}) → unparseable, no latest",
          r.get("catalog") == "unparseable" and "latest" not in r)

r = from_listing(LIVE_1_0_40, rc=124)
check("failed fetch → unreachable, and a partial listing is NOT trusted",
      r.get("catalog") == "unreachable" and "latest" not in r and r.get("fetch_rc") == "124")

inj = from_listing("  * grok-4.7 (default)\n  - grok-4.$(touch${IFS}/tmp/x)\n  - `grok-4.8`\n")
check("hostile id is dropped, backticked id is read as data", inj.get("latest") == "grok-4.8")

# --- the two copies must not drift -----------------------------------------------
for name in (LIB.name, pathlib.Path(__file__).name):
    copies = [PLUGINS / home / "scripts" / name for home in HOMES]
    present = [p for p in copies if p.exists()]
    # A plugin installed alone has one copy; that is plugin independence, not drift.
    if len(present) == 2:
        check(f"{name}: swarm and work-system copies are byte-identical",
              present[0].read_bytes() == present[1].read_bytes())

if FAILS:
    print("grok-latest tests FAILED:")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("grok-latest tests passed")
