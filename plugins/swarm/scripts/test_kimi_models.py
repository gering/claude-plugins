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


def run_ready(*, help_text: str, models: str, help_rc: int = 0,
              models_rc: int = 0, credentials: bool = True):
    with tempfile.TemporaryDirectory() as td:
        cred = Path(td) / "credentials.json"
        if credentials:
            cred.write_text("{}\n", encoding="utf-8")
        env = os.environ.copy()
        env.update({
            "KIMI_CREDENTIALS_FILE": str(cred),
            "FAKE_HELP": help_text,
            "FAKE_MODELS": models,
            "FAKE_HELP_RC": str(help_rc),
            "FAKE_MODELS_RC": str(models_rc),
        })
        script = f'''set -euo pipefail
source {str(AGENTS)!r}
_bounded_probe() {{
  if [[ "$*" == *"acp --help"* ]]; then
    printf '%s' "$FAKE_HELP"
    return "$FAKE_HELP_RC"
  fi
  if [[ "$*" == *"provider list --json"* ]]; then
    printf '%s' "$FAKE_MODELS"
    return "$FAKE_MODELS_RC"
  fi
  return 99
}}
if ready_check kimi; then printf 'ready\n'; else printf 'not-ready\n'; fi
'''
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )


GOOD_HELP = "Run kimi-code as an Agent Client Protocol (ACP) server over stdio."
GOOD_MODELS = json.dumps({"models": {"kimi-code/k3-256k": {"model": "k3-256k"}}})
OTHER_MODELS = json.dumps({"models": {"kimi-code/other": {"model": "other"}}})

r = run_ready(help_text=GOOD_HELP, models=GOOD_MODELS)
check("ACP + pinned model + credentials is ready", r.returncode == 0 and r.stdout.strip() == "ready")

r = run_ready(help_text="no ACP transport here", models=GOOD_MODELS)
check("missing ACP capability is not ready", r.stdout.strip() == "not-ready")

r = run_ready(help_text=GOOD_HELP, models=OTHER_MODELS)
check("known catalog without pinned model is not ready", r.stdout.strip() == "not-ready")

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

# Production constants: the task depends on the real token path (the similarly
# named oauth/ path can stay empty while logged in) and a qualified model alias.
script = f'''set -euo pipefail
source {str(AGENTS)!r}
printf '%s\n%s\n' "$KIMI_CREDENTIALS_FILE" "$KIMI_DEFAULT_MODEL"
'''
r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
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
