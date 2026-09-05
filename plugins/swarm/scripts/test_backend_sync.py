#!/usr/bin/env python3
"""Keep the external backend registry mirrored across adapter, workflow and skill."""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
ADAPTER = (HERE / "agents.sh").read_text(encoding="utf-8")
WORKFLOW = (PLUGIN / "workflows" / "swarm-review.js").read_text(encoding="utf-8")
SKILL = (PLUGIN / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")
AGENTS_SKILL = (PLUGIN / "skills" / "agents" / "SKILL.md").read_text(encoding="utf-8")
FAILS: list[str] = []


def check(name: str, condition: bool) -> None:
    if not condition:
        FAILS.append(name)


validated = re.search(r"validate_backend\(\).*?\n\s*([a-z|]+)\)\s*;;", ADAPTER, re.S)
validated_set = set(validated.group(1).split("|")) if validated else set()
externals = validated_set - {"claude"}
check("adapter backend enum found", bool(validated))

rows = re.search(r"for b in ([a-z ]+); do", ADAPTER)
row_set = set(rows.group(1).split()) if rows else set()
check("adapter list rows equal backend enum", row_set == validated_set)

ready_block = re.search(r"ready_check\(\).*?case .*?\n(.*?)\n\s*esac", ADAPTER, re.S)
ready_set = set(re.findall(r"^\s*([a-z]+)\)", ready_block.group(1), re.M)) if ready_block else set()
check("adapter readiness arms equal backend enum", ready_set == validated_set)

run_blocks = re.findall(r"case \"\$backend\" in\n(.*?)\n\s*esac", ADAPTER, re.S)
run_block = next((block for block in run_blocks if "run_codex" in block), "")
run_set = set(re.findall(r"^\s*([a-z]+)\)\s+run_", run_block, re.M))
check("adapter run dispatch equals external enum", run_set == externals)

backend_block = re.search(r"const EXTERNAL_BACKENDS = \[(.*?)\n\]", WORKFLOW, re.S)
workflow_set = set(re.findall(r"backend:\s*'([^']+)'", backend_block.group(1))) if backend_block else set()
check("workflow EXTERNAL_BACKENDS found", bool(backend_block))
check("workflow externals equal adapter externals", workflow_set == externals)

want = re.search(r"const wantVoices = .*? : \[([^\]]+)\]", WORKFLOW)
want_set = set(re.findall(r"'([^']+)'", want.group(1))) if want else set()
check("workflow default voices equal adapter externals", want_set == externals)

family = re.search(r"const FAMILY = \{([^}]+)\}", WORKFLOW)
family_map = dict(re.findall(r"([a-z]+):\s*'([^']+)'", family.group(1))) if family else {}
check("every workflow backend has an explicit family", validated_set <= set(family_map))
check("Kimi consensus family is Moonshot", family_map.get("kimi") == "moonshot")

for backend in sorted(externals):
    check(f"skill builds {backend} from LIVE_JSON", f'`"{backend}"`' in SKILL)
# The jail is part of Kimi's READINESS in the adapter (ready_check), not a rule
# the skills re-derive in prose — a prose AND that a compaction can drop.
check("skill does not gate Kimi on JAIL in prose",
      "`available && ready` **and** `JAIL=jail=yes`" not in SKILL)
check("adapter ready_check gates Kimi on the jail",
      "&& _read_web_safe kimi" in ADAPTER)
check("agent status does not re-derive a Kimi jail gate",
      "plus `jail=yes` for Kimi" not in AGENTS_SKILL)

kimi_backend = re.search(r"\{ backend: 'kimi', flags: ([^}]+)\}", WORKFLOW)
check("workflow registers Kimi effort flags", bool(kimi_backend))
if kimi_backend:
    check("Kimi normal profile uses high", "--effort high" in kimi_backend.group(1))
    check("Kimi max profile uses max", "--effort max" in kimi_backend.group(1))

PR_POST = (HERE / "pr-post.py").read_text(encoding="utf-8")
_labels = re.search(r"_AGENT_LABELS = \{(.*?)\}", PR_POST, re.S)
_label_set = set(re.findall(r'"([a-z]+)":', _labels.group(1))) if _labels else set()
check("pr-post footer labels cover every adapter backend", _label_set == validated_set)

grok_backend = re.search(r"\{ backend: 'grok', flags: ([^}]+)\}", WORKFLOW)
check("workflow registers grok effort flags", bool(grok_backend))
if grok_backend:
    # `high` blew the 540 s wall on a ~190 KiB cluster prompt; it is --max only.
    check("grok normal profile uses medium", "'--effort medium'" in grok_backend.group(1))
    check("grok max profile uses high", "MAX ? '--effort high'" in grok_backend.group(1))

if FAILS:
    print("backend-sync tests FAILED:")
    for failure in FAILS:
        print(f"  - {failure}")
    sys.exit(1)
print("backend-sync: all tests passed")
