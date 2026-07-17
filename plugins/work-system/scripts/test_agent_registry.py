#!/usr/bin/env python3
"""Tests for agent-registry.sh — run standalone (`python3 test_agent_registry.py`)
or via scripts/check-structure.py's "plugin tests" check.

Guards the registry's contract: alias/name/cli selector resolution, the per-CLI
launch argv shape (claude `/continue` vs codex/grok bootstrap prompt), the
availability probe (codex login status + grok auth file), the exit-code map
(2 unknown selector, 3 resolved-but-unavailable), the --auto ranking (skips
unavailable, exit 1 when none), and default/last state persistence.

Availability is made deterministic with fake `codex`/`grok`/`claude` stubs on a
prepended PATH, so the test does not depend on what is really installed/authed.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "agent-registry.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


class Env:
    """A throwaway HOME + fake-bin sandbox controlling CLI availability."""

    def __init__(self, codex_authed=True, grok_authed=True, rank=None,
                 grok_models=("grok-4.5",)):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        bindir = root / "bin"
        bindir.mkdir()
        # codex stub: `login status` exit reflects auth; anything else exits 0.
        codex_rc = 0 if codex_authed else 1
        (bindir / "codex").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "login" ] && [ "$2" = "status" ]; then exit %d; fi\n'
            "exit 0\n" % codex_rc
        )
        # grok stub: `models` prints a `grok models`-shaped list (drives the
        # model-level availability probe); other calls just exit 0 (command -v).
        model_lines = "".join(
            '  echo "  * %s"\n' % m for m in grok_models
        )
        (bindir / "grok").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "models" ]; then\n'
            "%s"
            "  exit 0\n"
            "fi\n"
            "exit 0\n" % model_lines
        )
        # claude stub: only ever hit by `command -v`.
        (bindir / "claude").write_text("#!/bin/sh\nexit 0\n")
        for f in bindir.iterdir():
            f.chmod(0o755)
        # grok auth file toggles grok readiness.
        self.grok_auth = root / "grok_auth.json"
        if grok_authed:
            self.grok_auth.write_text("{}\n")
        self.state = self.home / ".claude" / "work-system-agent"

        self.env = dict(os.environ)
        self.env["PATH"] = f"{bindir}:{self.env['PATH']}"
        self.env["HOME"] = str(self.home)
        self.env["GROK_AUTH_FILE"] = str(self.grok_auth)
        self.env["WORK_SYSTEM_AGENT_STATE"] = str(self.state)
        if rank is not None:
            self.env["WORK_SYSTEM_AGENT_RANK"] = rank

    def run(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env, capture_output=True, text=True,
        )

    def close(self):
        self.tmp.cleanup()


def kv(out):
    """Parse resolve's key=value lines; argv lines collect into a list."""
    d = {"argv": []}
    for line in out.splitlines():
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "argv":
            d["argv"].append(v)
        else:
            d[k] = v
    return d


# --- resolve: selector mapping + argv shape -------------------------------- #
e = Env()

r = kv(e.run("resolve", "--fable", "--session", "close-herdr").stdout)
check("--fable -> claude:fable", r.get("name") == "claude:fable")
check("--fable cli", r.get("cli") == "claude")
check("claude argv shape",
      r["argv"] == ["claude", "--model", "fable", "-n", "close-herdr", "/continue"])
check("claude supports lifecycle", "continue" in r.get("supports", ""))

r = kv(e.run("resolve", "--opus").stdout)
check("--opus -> claude:opus", r.get("name") == "claude:opus")
check("claude argv without session omits -n",
      r["argv"] == ["claude", "--model", "opus", "/continue"])

r = kv(e.run("resolve", "--sol").stdout)
check("--sol -> codex:gpt-5.6-sol", r.get("name") == "codex:gpt-5.6-sol")
check("codex argv shape",
      r["argv"][:3] == ["codex", "-m", "gpt-5.6-sol"])
check("codex bootstrap prompt is one argv word", len(r["argv"]) == 4)
check("codex bootstrap mentions TASK.md", "TASK.md" in r["argv"][3])
check("codex supports commit,pr only", r.get("supports") == "commit,pr")

r = kv(e.run("resolve", "--composer").stdout)
check("--composer -> grok composer",
      r.get("name") == "grok:grok-composer-2.5-fast")
check("grok argv shape", r["argv"][:3] == ["grok", "-m", "grok-composer-2.5-fast"])

# canonical name and bare-cli-default selectors
check("name selector claude:sonnet",
      kv(e.run("resolve", "claude:sonnet").stdout).get("model") == "sonnet")
check("bare cli codex -> default terra",
      kv(e.run("resolve", "codex").stdout).get("name") == "codex:gpt-5.6-terra")

# unknown selector -> exit 2
u = e.run("resolve", "--nope")
check("unknown selector exit 2", u.returncode == 2)
check("unknown selector hint", "Unknown agent selector" in u.stderr)

# missing selector -> exit 2
check("resolve no selector exit 2", e.run("resolve").returncode == 2)
e.close()

# --- availability probe + exit 3 ------------------------------------------- #
e = Env(codex_authed=False, grok_authed=False)
rows = json.loads(e.run("list", "--json").stdout)
by = {row["name"]: row for row in rows}
check("claude always available", by["claude:fable"]["available"] is True)
check("codex unauthed -> unavailable", by["codex:gpt-5.6-sol"]["available"] is False)
check("codex note is login hint", "codex login" in by["codex:gpt-5.6-sol"]["note"])
check("grok no auth -> unavailable", by["grok:grok-4.5"]["available"] is False)

res = e.run("resolve", "--sol")
check("resolve unavailable -> exit 3", res.returncode == 3)
rr = kv(res.stdout)
check("resolve unavailable still prints argv", len(rr["argv"]) == 4)
check("resolve unavailable available=no", rr.get("available") == "no")
e.close()

# --- grok model-level availability (gated on `grok models`) ---------------- #
# grok authed, but `grok models` lists only grok-4.5 -> composer is unavailable
# even though the CLI + auth are fine (mirrors a grok CLI that dropped composer).
e = Env(grok_models=("grok-4.5",))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok-4.5 listed -> available", by["grok:grok-4.5"]["available"] is True)
check("composer not listed -> unavailable",
      by["grok:grok-composer-2.5-fast"]["available"] is False)
check("composer note mentions the model list",
      "grok models" in by["grok:grok-composer-2.5-fast"]["note"])
check("resolve --composer unavailable -> exit 3", e.run("resolve", "--composer").returncode == 3)
check("resolve --grok available -> exit 0", e.run("resolve", "--grok").returncode == 0)
e.close()

# grok models that DO include composer -> composer becomes available (the probe
# is data-driven, not a hardcoded drop).
e = Env(grok_models=("grok-4.5", "grok-composer-2.5-fast"))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("composer listed -> available", by["grok:grok-composer-2.5-fast"]["available"] is True)
e.close()

# --- auto ranking ---------------------------------------------------------- #
# codex+grok down: --auto must fall through to the first available claude entry.
e = Env(codex_authed=False, grok_authed=False,
        rank="codex:gpt-5.6-sol grok:grok-4.5 claude:opus claude:fable")
a = e.run("auto")
check("auto skips unavailable to claude:opus", a.stdout.strip() == "claude:opus")
check("auto exit 0", a.returncode == 0)
e.close()

# no available agent in the ranking -> exit 1
e = Env(codex_authed=False, grok_authed=False, rank="codex:gpt-5.6-sol grok:grok-4.5")
a = e.run("auto")
check("auto none available exit 1", a.returncode == 1)
e.close()

# default rank, all available -> first (claude:fable)
e = Env()
check("auto default rank -> claude:fable", e.run("auto").stdout.strip() == "claude:fable")
check("rank first line is claude:fable",
      e.run("rank").stdout.splitlines()[0] == "claude:fable")
e.close()

# --- default/last state persistence ---------------------------------------- #
e = Env()
check("default get empty initially", e.run("default", "get").stdout.strip() == "")
e.run("default", "set", "claude:opus")
e.run("last", "set", "codex:gpt-5.6-sol")
check("default persisted", e.run("default", "get").stdout.strip() == "claude:opus")
check("last persisted", e.run("last", "get").stdout.strip() == "codex:gpt-5.6-sol")
# setting one preserves the other
e.run("default", "set", "grok:grok-4.5")
check("last survives default rewrite",
      e.run("last", "get").stdout.strip() == "codex:gpt-5.6-sol")
check("default updated", e.run("default", "get").stdout.strip() == "grok:grok-4.5")
# bogus name rejected
b = e.run("default", "set", "bogus:model")
check("bogus default rejected exit 2", b.returncode == 2)
e.close()


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("agent-registry.sh: all tests passed")
