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

# Kimi is OPT-IN (metered quota): the workflow's fallback voices are the STOCK
# ensemble without it, the adapter's readiness arm requires the opt-in before
# any probe, and the transport command carries the opt-in so a listed Kimi
# voice is not refused by the adapter's own gate one process later.
OPT_IN = {"kimi"}
want = re.search(r"const wantVoices = .*? : \[([^\]]+)\]", WORKFLOW)
want_set = set(re.findall(r"'([^']+)'", want.group(1))) if want else set()
check("workflow default voices are the non-opt-in externals", want_set == externals - OPT_IN)
kimi_ready_arm = re.search(r"^\s*kimi\)\s*(.*?);;", ready_block.group(1), re.S | re.M) if ready_block else None
check("adapter readiness gates Kimi on the opt-in first",
      bool(kimi_ready_arm) and kimi_ready_arm.group(1).lstrip().startswith("_kimi_opted_in &&"))
check("adapter opt-in reads SWARM_KIMI", 'KIMI_OPT_IN="${SWARM_KIMI:-0}"' in ADAPTER)
check("workflow carries the Kimi opt-in onto the transport command",
      "env: 'SWARM_KIMI=1 '" in WORKFLOW and "cmd: `${b.env || ''}" in WORKFLOW)
check("skill documents --kimi", "- `--kimi` —" in SKILL)
check("skill block exports the opt-in for the readiness probe",
      'export SWARM_KIMI="${SWARM_KIMI:-0}"' in SKILL)
check("agent status names the opt-in hint", "opt-in only" in AGENTS_SKILL)

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
    # Kimi's k3 thinking ladder is low|high|max (no medium); `high` ran 99–458 s
    # per ~290 KiB cluster, so it is --max only. Pin the exact arms.
    check("Kimi normal profile uses low", ": '--effort low'" in kimi_backend.group(1))
    check("Kimi reviews only breakage + threat (quota)", "clusters: ['breakage', 'threat']" in kimi_backend.group(1))
    check("Kimi max profile uses high", "MAX ? '--effort high'" in kimi_backend.group(1))

PR_POST = (HERE / "pr-post.py").read_text(encoding="utf-8")
_labels = re.search(r"_AGENT_LABELS = \{(.*?)\}", PR_POST, re.S)
_label_set = set(re.findall(r'"([a-z]+)":', _labels.group(1))) if _labels else set()
check("pr-post footer labels cover every adapter backend", _label_set == validated_set)

# grok's shell: run_terminal_command in the tool allowlist, headless dontAsk,
# and a --deny prefix list (defense-in-depth; the OS jail's inverted write
# model is the boundary). The isolated HOME/GROK_HOME keeps ambient Claude
# settings, hooks and plugins out.
check("grok tool list carries the shell",
      'GROK_SHELL_TOOL="run_terminal_command"' in ADAPTER
      and 'GROK_TOOLS="${GROK_READ_TOOLS},${GROK_SHELL_TOOL},${GROK_WEB_TOOLS}"' in ADAPTER)
check("run_grok pins --permission-mode dontAsk", "--permission-mode dontAsk" in ADAPTER)
check("grok deny rules cover egress", "'Bash(curl:*)'" in ADAPTER and "'Bash(git push:*)'" in ADAPTER)
check("run_grok runs from the isolated HOME", 'HOME="$TMP_GROK_HOME" GROK_HOME="$TMP_GROK_HOME/grok"' in ADAPTER)
# codex: its own seatbelt cannot nest inside the OS jail, so under the jail it
# runs with the jail as the boundary and its ambient config ignored; without
# a jail it keeps its own read-only sandbox.
check("codex under the jail bypasses its own sandbox", "sandbox_args=(-s danger-full-access --ignore-user-config --ignore-rules)" in ADAPTER)
check("codex without a jail keeps its own read-only sandbox", "sandbox_args=(-s read-only --ignore-user-config --ignore-rules)" in ADAPTER)

# The read-only shell brief Kimi is shown (agents.sh _kimi_output_contract)
# hand-mirrors the policy in kimi-acp.py; every program and git subcommand the
# brief NAMES must be one the policy accepts, or the model is told a command is
# fine that the gate then kills the session for.
_brief = re.search(r"TOOLS: read-only session\.(.*?)\\n' &&", ADAPTER, re.S)
check("Kimi tool brief present", bool(_brief))
if _brief:
    text = _brief.group(1)
    _pol = (HERE / "kimi-acp.py").read_text(encoding="utf-8")
    def _set(name):
        m = re.search(name + r" = frozenset\(\{(.*?)\}\)", _pol, re.S)
        return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
    progs, subs = _set("READ_ONLY_PROGRAMS"), _set("GIT_READ_SUBCOMMANDS")
    listing = set(re.findall(r'^\s+"([a-z-]+)": \{', _pol[_pol.find("GIT_LISTING_ONLY"):], re.M))
    named_programs = {w for w in re.findall(r"\b(grep|rg|find|ls|cat|head|tail|wc|sort|uniq|cut|tr|diff|stat)\b", text)}
    named_subs = set(re.search(r"git ([a-z/-]+)", text).group(1).split("/")) if re.search(r"git ([a-z/-]+)", text) else set()
    check("brief programs are on the policy allowlist", named_programs <= progs)
    check("brief git subcommands are on the policy read list", named_subs <= (subs | listing))

grok_backend = re.search(r"\{ backend: 'grok', flags: ([^}]+)\}", WORKFLOW)
check("workflow registers grok effort flags", bool(grok_backend))
if grok_backend:
    # `high` blew the 540 s wall on a ~190 KiB cluster prompt and `medium` on a
    # ~290 KiB one; the normal profile runs `low`, `medium` is --max only.
    check("grok normal profile uses low", ": '--effort low'" in grok_backend.group(1))
    check("grok max profile uses medium", "MAX ? '--effort medium'" in grok_backend.group(1))

if FAILS:
    print("backend-sync tests FAILED:")
    for failure in FAILS:
        print(f"  - {failure}")
    sys.exit(1)
print("backend-sync: all tests passed")
