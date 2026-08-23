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
check("skill omits Kimi when jail is unavailable",
      "include `\"kimi\"`" in SKILL and "`JAIL=jail=yes`" in SKILL)

kimi_backend = re.search(r"\{ backend: 'kimi', flags: ([^}]+)\}", WORKFLOW)
check("workflow registers Kimi effort flags", bool(kimi_backend))
if kimi_backend:
    check("Kimi normal profile uses high", "--effort high" in kimi_backend.group(1))
    check("Kimi max profile uses max", "--effort max" in kimi_backend.group(1))

if FAILS:
    print("backend-sync tests FAILED:")
    for failure in FAILS:
        print(f"  - {failure}")
    sys.exit(1)
print("backend-sync: all tests passed")
