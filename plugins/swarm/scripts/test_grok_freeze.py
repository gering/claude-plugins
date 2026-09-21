#!/usr/bin/env python3
"""The workflow freezes ONE concrete grok model per run — behavioural test.

Lifts the GROK_RUN block out of swarm-review.js VERBATIM and runs it under node
(same approach and same reason as test_voice_accounting.py: the workflow is the
repo's only JavaScript, and a presence-of-lines check would survive a refactor
that breaks the logic). args.grok is a string a model transcribed out of prose
and it ends up on a shell command line, so the hostile cases matter as much as
the happy one. Not skippable: node is a CI dependency for exactly this.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = (HERE.parent / "workflows" / "swarm-review.js").read_text()
SKILL = (HERE.parent / "skills" / "review" / "SKILL.md").read_text()

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


m = re.search(r"^const GROK_RUN = \(\(\) => \{.*?^const GROK_FROZEN_FLAG = [^\n]*\n", SOURCE, re.S | re.M)
q = re.search(r"^const shQuote = [^\n]*\n", SOURCE, re.M)
if not (m and q):
    print("grok-freeze tests FAILED:\n  - could not find the GROK_RUN block / shQuote in swarm-review.js "
          "(it moved or was renamed — fix this test, do not ignore it)")
    sys.exit(1)
if not shutil.which("node"):
    print("grok-freeze tests FAILED:\n  - node is required (not skippable by design)")
    sys.exit(1)


def freeze(token):
    js = (f"const INPUT = {json.dumps({'grok': token} if token is not None else {})}\n"
          + q.group(0) + m.group(0)
          + "console.log(JSON.stringify({run: GROK_RUN, env: GROK_FROZEN_ENV, flag: GROK_FROZEN_FLAG}))\n")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        FAILS.append(f"node failed for {token!r}: {r.stderr[:200]}")
        return {"run": {}, "env": "", "flag": ""}
    return json.loads(r.stdout)


GOOD = "selected=grok-4.7;latest_candidate=grok-4.7;source=latest;catalog=ok;cli_version=1.0.40"
f = freeze(GOOD)
check("the selected id becomes an explicit --model on every grok voice", f["flag"] == " --model 'grok-4.7'")
check("voices never probe and get the CLI version the cache is keyed by",
      f["env"] == "SWARM_GROK_PROBE=0 SWARM_GROK_CLI_VERSION=1.0.40 ")
check("provenance is carried as data", f["run"] == {"model": "grok-4.7", "latest": "grok-4.7",
                                                     "source": "latest", "cliVersion": "1.0.40"})

f = freeze("selected=grok-4.6;latest_candidate=grok-4.7;source=older-compatible;catalog=ok;cli_version=1.0.40")
check("a degraded selection keeps BOTH ids apart (selected vs latest)",
      f["run"]["model"] == "grok-4.6" and f["run"]["latest"] == "grok-4.7"
      and f["run"]["source"] == "older-compatible")

f = freeze("selected=grok-4.7;source=latest;cli_version=unknown")
check("unknown CLI version → still frozen, just no version pin",
      f["flag"] == " --model 'grok-4.7'" and f["env"] == "SWARM_GROK_PROBE=0 ")

for label, token in [("absent", None), ("empty", ""), ("no selection", "selected=;source=none;catalog=unreachable"),
                     ("not a grok id", "selected=gpt-5;source=latest"),
                     ("command substitution", "selected=grok-4.$(id);source=latest"),
                     ("quote break", "selected=grok-4.7' ; rm -rf ~ ; ';source=latest"),
                     ("space/flag smuggling", "selected=grok-4.7 --tools all;source=latest"),
                     ("overlong", "selected=grok-" + "a" * 80 + ";source=latest")]:
    f = freeze(token)
    check(f"{label}: nothing is frozen and nothing reaches the command line",
          f["flag"] == "" and f["env"] == "" and f["run"].get("model") == "")

f = freeze("selected=grok-4.7;source=latest;cli_version=1.0.40 SWARM_TIMEOUT=1")
check("a hostile cli_version is dropped, not interpolated", "SWARM_TIMEOUT" not in f["env"])
f = freeze("selected=grok-4.7;source=totally-latest")
check("an unknown source is not echoed as if it were a known one", f["run"]["source"] == "unknown")

# --- the two ends of the hand-over must name the same things ---------------------------
check("workflow: the frozen flag/env are applied to the grok backend",
      "GROK_FROZEN_FLAG, env: GROK_FROZEN_ENV" in SOURCE)
check("workflow: the run's grok model is returned as data", "grokModel:" in SOURCE)
check("skill: prep selects once via `agents.sh grok-model`, BEFORE `list`",
      0 < SKILL.find("agents.sh\" grok-model") < SKILL.find("agents.sh\" list --json"))
for field in ("selected", "latest_candidate", "source", "cli_version"):
    check(f"skill token and workflow parser agree on `{field}`",
          f"{field}=$(_gk {field})" in SKILL and f"kv.{field}" in SOURCE)
check("skill: the token is passed as args.grok", 'grok: "<GROK_RUN>"' in SKILL)
check("skill: a resumed/looped run keeps its first token", "resumeFromRunId" in SKILL and "FIRST prep block" in SKILL)

if FAILS:
    print("grok-freeze tests FAILED:")
    for x in FAILS:
        print(f"  - {x}")
    sys.exit(1)
print("grok-freeze tests passed")
