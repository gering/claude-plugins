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


m = re.search(r"^const GROK_RUN = \(\(\) => \{.*?^const GROK_DROPPED = [^\n]*\n", SOURCE, re.S | re.M)
q = re.search(r"^const shQuote = [^\n]*\n", SOURCE, re.M)
if not (m and q):
    print("grok-freeze tests FAILED:\n  - could not find the GROK_RUN block / shQuote in swarm-review.js "
          "(it moved or was renamed — fix this test, do not ignore it)")
    sys.exit(1)
if not shutil.which("node"):
    print("grok-freeze tests FAILED:\n  - node is required (not skippable by design)")
    sys.exit(1)


def freeze(token, voices=("codex", "grok")):
    js = (f"const INPUT = {json.dumps({'grok': token} if token is not None else {})}\n"
          f"const wantVoices = {json.dumps(list(voices))}\n"
          + q.group(0) + m.group(0)
          + "console.log(JSON.stringify({run: GROK_RUN, env: GROK_FROZEN_ENV, flag: GROK_FROZEN_FLAG, dropped: GROK_DROPPED}))\n")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        FAILS.append(f"node failed for {token!r}: {r.stderr[:200]}")
        return {"run": {}, "env": "", "flag": "", "dropped": None}
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
    check(f"{label}: grok is DROPPED, never run unfrozen (a failed pin must not become latest)",
          f["dropped"] is True)
check("a valid token does not drop grok", freeze(GOOD)["dropped"] is False)
check("grok not requested → nothing to drop", freeze(None, voices=("codex",))["dropped"] is False)
check("workflow: a dropped grok is filtered out of the live externals and reported",
      "b.backend === 'grok' && GROK_DROPPED" in SOURCE and "grokDropped: GROK_DROPPED" in SOURCE
      and "if (GROK_DROPPED) coverageNotes.push" in SOURCE)

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
# --- the prep fragment, EXECUTED under the shells a user actually has -------------------
# The block runs in the user's shell. `${VAR:+--model "$VAR"}` was one word under
# zsh: the adapter answered "Unknown flag", the token came back empty and the pin
# ran as "latest". The fragment now passes no argument at all; pin that here.
import os, tempfile
frag = re.search(r'^GROK_KV=.*?^echo "GROK_DEGRADED=[^\n]*\n', SKILL, re.S | re.M)
check("skill: the prep fragment is extractable", bool(frag))
if frag:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "plug"; (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "agents.sh").write_text(
            '#!/bin/bash\nprintf "%s\\n" "$#" "$@" >"$ARGLOG"\n'
            'printf "selected=%s\\nlatest_candidate=grok-4.7\\nsource=pinned\\ncatalog=ok\\ncli_version=1.0.40\\n" '
            '"${SWARM_GROK_MODEL:-grok-4.7}"\n'
            # single-quoted: the STUB must not run it either — only the fragment is under test
            "printf '%s\\n' 'degraded=pinned; $(id) `id`'\n")
        for shell in ("bash", "zsh", "sh"):
            if not shutil.which(shell):
                continue
            arglog = Path(td) / f"args.{shell}"
            r = subprocess.run([shell, "-c", frag.group(0)], capture_output=True, text=True, timeout=30,
                               env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(root), ARGLOG=str(arglog),
                                        SWARM_GROK_MODEL="grok-4.5", PROMPT_BYTES="100",
                                        OVERSIZE_THRESHOLD="200"))
            argv = arglog.read_text().split("\n") if arglog.exists() else ["?"]
            check(f"{shell}: the adapter is called with exactly `grok-model` (the pin travels by env)",
                  argv[:2] == ["1", "grok-model"])
            out = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
            check(f"{shell}: the pin arrives in the token",
                  out.get("GROK_RUN", "").startswith("selected=grok-4.5;latest_candidate=grok-4.7;source=pinned"))
            check(f"{shell}: the token round-trips through the workflow parser",
                  freeze(out.get("GROK_RUN", ""))["flag"] == " --model 'grok-4.5'")
            check(f"{shell}: the free-text reason is printed, never executed",
                  "$(id)" in out.get("GROK_DEGRADED", "") and "uid=" not in r.stdout)
            # Oversize: the externals will be skipped, so nothing may be selected (or paid for).
            arglog.unlink()
            r = subprocess.run([shell, "-c", frag.group(0)], capture_output=True, text=True, timeout=30,
                               env=dict(os.environ, CLAUDE_PLUGIN_ROOT=str(root), ARGLOG=str(arglog),
                                        PROMPT_BYTES="300", OVERSIZE_THRESHOLD="200"))
            check(f"{shell}: an oversize diff never calls grok-model (no probe for a skipped voice)",
                  not arglog.exists() and "GROK_RUN=selected=;" in r.stdout)
check("skill: the report layout renders the run's grok model and a dropped grok",
      "balance.grokModel.model" in SKILL and "balance.grokDropped" in SKILL)
check("skill: no `${VAR:+--flag …}` argument splicing in the prep fragment",
      not re.search(r"grok-model\s+\$\{", SKILL))
check("skill: the token is passed as args.grok", 'grok: "<GROK_RUN>"' in SKILL)
check("skill: a resumed/looped run keeps its first token", "resumeFromRunId" in SKILL and "FIRST prep block" in SKILL)

if FAILS:
    print("grok-freeze tests FAILED:")
    for x in FAILS:
        print(f"  - {x}")
    sys.exit(1)
print("grok-freeze tests passed")
