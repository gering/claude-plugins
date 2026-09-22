#!/usr/bin/env python3
"""Read validated profile data from the staged workflow without executing JS."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BEGIN = "// BEGIN SWARM PROFILES JSON\n"
END = "// END SWARM PROFILES JSON"
NAMES = ("quick", "default", "max")
STAGES = {"gate", "finder", "transport", "merge", "verify"}
EFFORTS = {
    "stage": {"low", "medium", "high", "xhigh", "max"},
    "codex": {"low", "medium", "high", "xhigh"},
    "grok": {"low", "medium", "high"},
    "kimi": {"low", "high", "max"},
}
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*\Z")


def profile_name(value: object) -> str:
    return value if type(value) is str and value in NAMES else "default"


def _keys(value: object, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"expected keys {sorted(expected)}")


def _unique(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate profile key: {key}")
        result[key] = value
    return result


def load_profiles(workflow: Path) -> dict:
    source = workflow.read_text(encoding="utf-8")
    if source.count(BEGIN) != 1 or source.count(END) != 1:
        raise ValueError("expected one marked PROFILES block")
    block = source.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    prefix = "const PROFILES = "
    if not block.startswith(prefix):
        raise ValueError("marked block must declare const PROFILES")
    profiles = json.loads(block[len(prefix):], object_pairs_hook=_unique)
    _keys(profiles, set(NAMES))
    for name, profile in profiles.items():
        _keys(profile, {"unit", "stages", "externals"})
        if profile["unit"] not in ("cluster", "lens"):
            raise ValueError(f"invalid unit in {name}")
        _keys(profile["stages"], STAGES)
        _keys(profile["externals"], {"codex", "grok", "kimi"})
        for backend, config in [*(('stage', v) for v in profile["stages"].values()), *profile["externals"].items()]:
            keys = {"model", "effort"} if backend == "stage" else {"model", "effort", "tools", "toolBudget"}
            _keys(config, keys)
            model = config["model"]
            if model is None:
                if backend not in ("stage", "grok"):
                    raise ValueError(f"{backend} requires an explicit model")
            elif not isinstance(model, str) or not MODEL.fullmatch(model):
                raise ValueError(f"invalid model in {name}/{backend}")
            if not isinstance(config["effort"], str) or config["effort"] not in EFFORTS[backend]:
                raise ValueError(f"invalid effort in {name}/{backend}")
            if backend == "stage":
                continue
            tools, budget = config["tools"], config["toolBudget"]
            if type(tools) is not bool or type(budget) is not int or not 0 <= budget <= 1000:
                raise ValueError(f"invalid tool policy in {name}/{backend}")
            if (not tools and (backend != "kimi" or budget != 0)) or (tools and budget == 0):
                raise ValueError(f"contradictory tool policy in {name}/{backend}")
    return profiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True, type=Path)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--codex-model", action="store_true", help="print only the selected model ID")
    args = parser.parse_args()
    try:
        profile = load_profiles(args.workflow)[profile_name(args.profile)]
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(profile["externals"]["codex"]["model"] if args.codex_model else json.dumps(profile))


if __name__ == "__main__":
    main()
