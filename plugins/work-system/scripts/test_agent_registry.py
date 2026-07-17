#!/usr/bin/env python3
"""Tests for agent-registry.sh — run standalone (`python3 test_agent_registry.py`)
or via scripts/check-structure.py's "plugin tests" check.

Guards the registry's contract: alias/name/cli selector resolution, the per-CLI
launch argv shape (claude `/continue` vs codex/grok bootstrap prompt), the
availability probe (codex login status + grok auth file + grok model-list), the
exit-code map (2 unknown selector, 3 resolved-but-unavailable), and the
project-default state (set/get, bogus rejection, no-git-repo error).

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

    def __init__(self, codex_authed=True, grok_authed=True,
                 grok_models=("grok-4.5",), grok_models_ok=True):
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
        # model-level availability probe) and exits ok/non-ok to simulate a
        # reachable vs unreachable fetch; other calls just exit 0 (command -v).
        model_lines = "".join(
            '  echo "  * %s"\n' % m for m in grok_models
        )
        models_rc = 0 if grok_models_ok else 1
        (bindir / "grok").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "models" ]; then\n'
            "%s"
            "  exit %d\n"
            "fi\n"
            "exit 0\n" % (model_lines, models_rc)
        )
        # claude stub: only ever hit by `command -v`.
        (bindir / "claude").write_text("#!/bin/sh\nexit 0\n")
        for f in bindir.iterdir():
            f.chmod(0o755)
        # grok auth file toggles grok readiness.
        self.grok_auth = root / "grok_auth.json"
        if grok_authed:
            self.grok_auth.write_text("{}\n")
        self.project_state = root / "repo" / ".claude" / "work-system-agent"

        self.env = dict(os.environ)
        self.env["PATH"] = f"{bindir}:{self.env['PATH']}"
        self.env["HOME"] = str(self.home)
        self.env["GROK_AUTH_FILE"] = str(self.grok_auth)
        self.env["WORK_SYSTEM_AGENT_PROJECT_STATE"] = str(self.project_state)

    def run(self, *args, project_state=True):
        env = dict(self.env)
        if not project_state:
            # Force the "no project config location" path: drop the override and
            # run from a non-git cwd so `git rev-parse` finds no repo root.
            env.pop("WORK_SYSTEM_AGENT_PROJECT_STATE", None)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env, cwd=str(self.home), capture_output=True, text=True,
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

r = kv(e.run("resolve", "--grok").stdout)
check("--grok -> grok:grok-4.5", r.get("name") == "grok:grok-4.5")
check("grok argv shape", r["argv"][:3] == ["grok", "-m", "grok-4.5"])

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
# grok authed + `grok models` lists grok-4.5 -> available.
e = Env(grok_models=("grok-4.5",))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok-4.5 listed -> available", by["grok:grok-4.5"]["available"] is True)
check("resolve --grok available -> exit 0", e.run("resolve", "--grok").returncode == 0)
e.close()

# grok authed but the model is NOT in `grok models` (a model dropped/renamed
# between releases) -> unavailable at probe time, so the launch is refused
# cleanly instead of erroring at runtime with "unknown model id". Data-driven,
# not a hardcoded drop.
e = Env(grok_models=("grok-9.9-imaginary",))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok-4.5 not listed -> unavailable", by["grok:grok-4.5"]["available"] is False)
check("unlisted-model note mentions the model list",
      "grok models" in by["grok:grok-4.5"]["note"])
check("resolve --grok unavailable -> exit 3", e.run("resolve", "--grok").returncode == 3)
e.close()

# grok authed but `grok models` fetch FAILS (unreachable/offline/timed out) ->
# inconclusive, not a drop: trust auth so a network hiccup can't wrongly block a
# launch. (A global flag can't carry this out of the command-substitution
# subshell, so the fetch status must ride the function's exit code.)
e = Env(grok_models=(), grok_models_ok=False)
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok models unreachable -> assumed available", by["grok:grok-4.5"]["available"] is True)
check("unreachable note is soft", "unreachable" in by["grok:grok-4.5"]["note"])
check("resolve --grok available when fetch fails", e.run("resolve", "--grok").returncode == 0)
e.close()

# --- project default (the only persisted state) ---------------------------- #
e = Env()
# nothing set -> empty (no-flag /kickoff then shows the picker)
check("no default set -> empty", e.run("default", "get").stdout.strip() == "")
# set -> get round-trips, and it lands in the project state file
e.run("default", "set", "codex:gpt-5.6-sol")
check("default set persisted", e.run("default", "get").stdout.strip() == "codex:gpt-5.6-sol")
check("default lives in the project file",
      "default=codex:gpt-5.6-sol" in e.project_state.read_text())
# overwriting replaces it
e.run("default", "set", "claude:opus")
check("default overwrite", e.run("default", "get").stdout.strip() == "claude:opus")
# bogus name rejected
check("bogus default rejected", e.run("default", "set", "bogus:model").returncode == 2)
# no project location (not a git repo, no override) -> clear error, exit 2
r = e.run("default", "set", "claude:opus", project_state=False)
check("no project location -> exit 2", r.returncode == 2)
check("no project location message", "no project config location" in r.stderr)
e.close()

# --- removed subcommands (auto / rank / last are gone) --------------------- #
e = Env()
check("auto removed -> exit 2", e.run("auto").returncode == 2)
check("rank removed -> exit 2", e.run("rank").returncode == 2)
check("last removed -> exit 2", e.run("last", "get").returncode == 2)
e.close()


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("agent-registry.sh: all tests passed")
