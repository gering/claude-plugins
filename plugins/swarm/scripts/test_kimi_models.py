#!/usr/bin/env python3
"""Hermetic readiness tests for Kimi ACP capability and model discovery."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENTS = HERE / "agents.sh"
FAILS: list[str] = []


def check(name: str, condition: bool) -> None:
    if not condition:
        FAILS.append(name)


_PROBE_STUB = r'''
_fake_probe() {
  if [[ "$*" == *"acp --help"* ]]; then
    printf '%s' "$FAKE_HELP"
    return "$FAKE_HELP_RC"
  fi
  if [[ "$*" == *"provider list --json"* ]]; then
    printf '%s' "$FAKE_MODELS"
    return "$FAKE_MODELS_RC"
  fi
  return 99
}
_bounded_probe() { _fake_probe "$@"; }
_probe_or_bare() { _fake_probe "$@"; }
'''


def run_ready(*, help_text: str, models: str, help_rc: int = 0,
              models_rc: int = 0, credentials: bool = True,
              requested_model: str = ""):
    with tempfile.TemporaryDirectory() as td:
        cred = Path(td) / "credentials.json"
        if credentials:
            cred.write_text('{"access_token":"tok","refresh_token":"ref"}\n', encoding="utf-8")
        env = os.environ.copy()
        env.update({
            "KIMI_CREDENTIALS_FILE": str(cred),
            "FAKE_HELP": help_text,
            "FAKE_MODELS": models,
            "FAKE_HELP_RC": str(help_rc),
            "FAKE_MODELS_RC": str(models_rc),
            "FAKE_REQUESTED_MODEL": requested_model,
        })
        script = f'''set -euo pipefail
source {str(AGENTS)!r}
{_PROBE_STUB}
_read_web_safe() {{ return 0; }}
if ready_check kimi "$FAKE_REQUESTED_MODEL"; then printf 'ready\\n'; else printf 'not-ready\\n'; fi
'''
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )


def run_install_gate(*, display_version: bool):
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "kimi"
        log = Path(td) / "invocations.log"
        fake.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$FAKE_LOG\"\nprintf 'kimi 0.test\\n'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = os.environ.copy()
        env.update({"SWARM_KIMI_BIN": str(fake), "FAKE_LOG": str(log)})
        action = "available_version kimi" if display_version else "require_usable kimi"
        script = f'''set -euo pipefail
source {str(AGENTS)!r}
ready_check() {{ return 0; }}
_bounded_probe() {{ printf 'bounded:%s\\n' "$*" >>"$FAKE_LOG"; "$@"; }}
_probe_or_bare() {{ _bounded_probe "$@"; }}
{action}
'''
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        invocations = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, invocations


GOOD_HELP = "Run kimi-code as an Agent Client Protocol (ACP) server over stdio."
GOOD_MODELS = json.dumps({"models": {"kimi-code/k3-256k": {"model": "k3-256k"}}})
OTHER_MODELS = json.dumps({"models": {"kimi-code/other": {"model": "other"}}})

r = run_ready(help_text=GOOD_HELP, models=GOOD_MODELS)
check("ACP + pinned model + credentials is ready", r.returncode == 0 and r.stdout.strip() == "ready")

r = run_ready(help_text="unknown command", models=GOOD_MODELS, help_rc=2)
check("missing ACP capability is not ready", r.stdout.strip() == "not-ready")

r = run_ready(help_text="ACP server over standard I/O", models=GOOD_MODELS)
check("ACP help wording drift degrades to ready", r.stdout.strip() == "ready")
check("ACP help wording drift is audible", "unrecognized help text" in r.stderr)

r = run_ready(help_text=GOOD_HELP, models=OTHER_MODELS)
check("known catalog without pinned model is not ready", r.stdout.strip() == "not-ready")

r = run_ready(
    help_text=GOOD_HELP,
    models=OTHER_MODELS,
    requested_model="kimi-code/other",
)
check("explicit offered model override is ready", r.stdout.strip() == "ready")

r = run_ready(help_text=GOOD_HELP, models=json.dumps({"models": {}}))
check("known empty model catalog is not ready", r.stdout.strip() == "not-ready")

r = run_ready(help_text=GOOD_HELP, models="{}")
check("unrecognized catalog degrades to trusting auth", r.stdout.strip() == "ready")
check("unrecognized catalog degrade is audible", "unrecognized document" in r.stderr)

r = run_ready(help_text=GOOD_HELP, models="partial", models_rc=124)
check("failed model probe degrades to trusting auth", r.stdout.strip() == "ready")
check("failed model probe names its rc", "rc=124" in r.stderr)

r = run_ready(help_text="partial", models=GOOD_MODELS, help_rc=124)
check("failed ACP probe degrades to trusting install", r.stdout.strip() == "ready")
check("failed ACP probe is audible", "acp --help" in r.stderr and "rc=124" in r.stderr)

r = run_ready(help_text=GOOD_HELP, models=GOOD_MODELS, credentials=False)
check("missing credentials is not ready", r.stdout.strip() == "not-ready")

r, invocations = run_install_gate(display_version=False)
check("run gate accepts an installed ready backend", r.returncode == 0)
check("run gate does not execute cosmetic version probe", invocations == [])

r, invocations = run_install_gate(display_version=True)
check("available version remains best effort", r.returncode == 0 and r.stdout.strip() == "kimi 0.test")
check(
    "available version uses bounded probe",
    len(invocations) == 2
    and invocations[0].startswith("bounded:")
    and invocations[0].endswith(" --version")
    and invocations[1] == "--version",
)

# Production constants: the task depends on the real token path (the similarly
# named oauth/ path can stay empty while logged in) and a qualified model alias.
script = f'''set -euo pipefail
source {str(AGENTS)!r}
printf '%s\\n%s\\n' "$KIMI_CREDENTIALS_FILE" "$KIMI_DEFAULT_MODEL"
'''
env = os.environ.copy()
env.pop("KIMI_CREDENTIALS_FILE", None)
r = subprocess.run(
    ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=20
)
lines = r.stdout.splitlines()
check("credential path uses credentials/kimi-code.json",
      bool(lines) and lines[0].endswith("/.kimi-code/credentials/kimi-code.json"))
check("default model is a qualified kimi-code alias",
      len(lines) > 1 and lines[1].startswith("kimi-code/") and "/" in lines[1])

if FAILS:
    print("kimi-model tests FAILED:")
    for failure in FAILS:
        print(f"  - {failure}")
    sys.exit(1)
print("kimi-models: all tests passed")
