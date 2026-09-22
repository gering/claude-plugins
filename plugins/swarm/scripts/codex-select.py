#!/usr/bin/env python3
"""Pick the Codex model for ONE review from codex-models.py output.

Usage: codex-models.py ... | codex-select.py --family F [--pin ID] [--effort E]
       codex-select.py --family F --catalog-rc N      (probe failed; no stdin)

The single selector: prep (`agents.sh codex-model`), readiness (`ready`/`list`)
and a bare `run codex` all call this, so they cannot resolve different models.

Family policy: the newest listed, visible `gpt-<major>[.<minor>]-<family>` whose
supported efforts include the requested effort, compared numerically per family
(6.10 > 6.9; a missing minor is .0). Families never borrow from each other and
there is no common major across families. Dated, preview, fast, spark or other
suffixed IDs are not family models. A --pin is exact: it is never replaced and
never rejected for being unlisted (model/list is advisory, not exhaustive).

A listed model is not proof of usable inference, and a complete catalog may
still come from Codex's own cache: `source=catalog-latest` means "newest the
native catalog listed", never "definitively the latest that exists". When the
catalog is unusable or lists no member of the family, the family's FLOOR (the
last pin shipped before family resolution) is returned as `source=fallback`,
with the reason in `degraded`.

Output key=value lines: selected, requested, family, source
(catalog-latest|older-compatible|fallback|pinned|none), latest_candidate,
catalog (complete|unavailable|malformed), listed (yes|no|unknown),
effort_supported (yes|no|unknown), degraded. Exit 0 = a model was selected.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

FAMILIES = ("astra", "sol", "terra", "luna")
# Last concrete pins before family resolution. Used ONLY when the catalog cannot
# establish a choice, and always reported as source=fallback.
FLOOR = {"astra": "gpt-6-astra", "sol": "gpt-5.6-sol", "terra": "gpt-5.6-terra", "luna": "gpt-5.6-luna"}
# Components: no leading zeros, at most 3 digits — anything else is malformed.
_NUM = r"(0|[1-9][0-9]{0,2})"
FAMILY_ID = re.compile(rf"gpt-{_NUM}(?:\.{_NUM})?-({'|'.join(FAMILIES)})\Z")
# The shared transport grammar (profiles.py/externalFlags/validate_model agree;
# test_model_id_grammar_agrees_across_layers). Family candidates are narrower.
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*\Z")
EFFORTS = ("low", "medium", "high", "xhigh")


def family_version(model_id: object) -> tuple[str, tuple[int, int, int]] | None:
    """(family, sort key) for an eligible family ID, else None.

    The key's third element prefers an explicit `.0` over a bare major when a
    catalog lists both spellings, so the choice stays deterministic."""
    if not isinstance(model_id, str):
        return None
    match = FAMILY_ID.fullmatch(model_id)
    if not match:
        return None
    major, minor, family = match.groups()
    return family, (int(major), int(minor or 0), 1 if minor is not None else 0)


def parse_catalog(text: str) -> list[dict]:
    """Validated model rows of a COMPLETE catalog envelope; ValueError otherwise."""
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("complete") is not True:
        raise ValueError("incomplete catalog")
    models = data.get("models")
    if not isinstance(models, list):
        raise ValueError("invalid model list")
    rows = []
    for row in models:
        if not isinstance(row, dict):
            raise ValueError("invalid model entry")
        name = row.get("model")
        if not isinstance(name, str) or not MODEL_ID.fullmatch(name):
            raise ValueError("invalid model ID")
        efforts = row.get("supportedReasoningEfforts")
        names = None
        if isinstance(efforts, list):
            names = {e.get("reasoningEffort") for e in efforts if isinstance(e, dict)}
        rows.append({"model": name, "hidden": row.get("hidden") is True, "efforts": names})
    return rows


def _effort_state(row: dict | None, effort: str | None) -> str:
    if row is None or not effort or row["efforts"] is None:
        return "unknown"
    return "yes" if effort in row["efforts"] else "no"


def select(family: str, pin: str | None, effort: str | None,
           rows: list[dict] | None, catalog: str) -> dict:
    """Pure selection. rows is None whenever catalog != 'complete'."""
    by_name = {r["model"]: r for r in rows or []}
    result = {"selected": "", "requested": f"family:{family}", "family": family,
              "source": "none", "latest_candidate": "", "catalog": catalog,
              "listed": "unknown", "effort_supported": "unknown", "degraded": ""}
    if pin:
        parsed = family_version(pin)
        row = by_name.get(pin)
        result.update(selected=pin, requested=pin, source="pinned",
                      family=parsed[0] if parsed else "custom",
                      listed="unknown" if rows is None else ("yes" if row else "no"),
                      effort_supported=_effort_state(row, effort))
        if rows is None:
            result["degraded"] = f"{pin}: availability unverified (catalog {catalog})"
        elif not row:
            result["degraded"] = f"{pin}: not in the advisory catalog; custom aliases may still work"
        elif result["effort_supported"] == "no":
            result["degraded"] = f"{pin}: catalog does not list effort {effort} for this model"
        return result
    if rows is None:
        result.update(selected=FLOOR[family], source="fallback",
                      degraded=f"catalog {catalog}: {family} not resolved; using fallback {FLOOR[family]}, not verified latest")
        return result
    members = sorted(
        (family_version(r["model"])[1], r) for r in rows
        if not r["hidden"] and (family_version(r["model"]) or ("",))[0] == family
    )
    if not members:
        result.update(selected=FLOOR[family], source="fallback",
                      listed="yes" if FLOOR[family] in by_name else "no",
                      effort_supported=_effort_state(by_name.get(FLOOR[family]), effort),
                      degraded=f"catalog lists no {family} model; using fallback {FLOOR[family]}")
        return result
    newest = members[-1][1]
    result["latest_candidate"] = newest["model"]
    for _key, row in reversed(members):
        state = _effort_state(row, effort)
        if state != "no":
            result.update(selected=row["model"], listed="yes", effort_supported=state,
                          source="catalog-latest" if row is newest else "older-compatible")
            if row is not newest:
                result["degraded"] = f"{newest['model']} does not list effort {effort}; using {row['model']}"
            return result
    result["degraded"] = f"no listed {family} model supports effort {effort}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--family", required=True, choices=FAMILIES)
    parser.add_argument("--pin", default="")
    parser.add_argument("--effort", default="", choices=("",) + EFFORTS)
    parser.add_argument("--catalog-rc", type=int, default=0,
                        help="exit status of codex-models.py; non-zero skips stdin")
    args = parser.parse_args()
    if args.pin and not MODEL_ID.fullmatch(args.pin):
        parser.error("invalid --pin model ID")
    rows, catalog = None, "unavailable"
    if args.catalog_rc == 0:
        try:
            rows, catalog = parse_catalog(sys.stdin.read(8 * 1024 * 1024 + 1)), "complete"
        except (ValueError, TypeError, RecursionError):
            catalog = "malformed"
    result = select(args.family, args.pin or None, args.effort or None, rows, catalog)
    for key in ("selected", "requested", "family", "source", "latest_candidate",
                "catalog", "listed", "effort_supported", "degraded"):
        print(f"{key}={' '.join(str(result[key]).split())}")
    return 0 if result["selected"] else 1


if __name__ == "__main__":
    sys.exit(main())
