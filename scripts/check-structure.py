#!/usr/bin/env python3
"""Structure invariant checks for the gering-plugins marketplace.

The plugins are declarative Markdown + JSON with no build step, so structural
regressions (broken JSON, version drift, dangling references) are otherwise only
caught in live use. This script mechanically verifies the invariants documented
in CLAUDE.md.

Run locally before pushing:

    python3 scripts/check-structure.py

Exit code 0 = no errors (warnings allowed), 1 = at least one error.
Dependencies: python3 stdlib + bash only.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Word budget for skill `description` frontmatter (loaded into every session).
DESC_WORDS_ERROR = 40
DESC_WORDS_WARN = 30

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def load_json(path: Path):
    """Parse JSON, recording an error on failure. Returns data or None."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        err(f"{rel(path)}: file not found")
    except json.JSONDecodeError as e:
        err(f"{rel(path)}: invalid JSON — {e}")
    return None


def parse_frontmatter(text: str):
    """Minimal YAML frontmatter parser (no PyYAML dependency).

    Handles the subset used by SKILL.md: top-level `key: value` scalars and
    `key: |` block scalars. Block-scalar lines are joined with single spaces.
    Returns a dict, or None if no frontmatter block is present.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None

    body = lines[1:end]
    data: dict[str, str] = {}
    i = 0
    while i < len(body):
        line = body[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2)
        if val in ("|", ">", "|-", ">-", "|+", ">+"):
            block: list[str] = []
            i += 1
            while i < len(body):
                nxt = body[i]
                if nxt.strip() == "":
                    i += 1
                    continue
                if len(nxt) - len(nxt.lstrip()) == 0:  # de-dented = new key
                    break
                block.append(nxt.strip())
                i += 1
            data[key] = " ".join(block)
        else:
            data[key] = val.strip().strip('"').strip("'")
            i += 1
    return data


def check_json_and_versions():
    """JSON validity for all manifests + version/name sync across both sources."""
    market = load_json(REPO / ".claude-plugin" / "marketplace.json")

    plugin_jsons = sorted((REPO / "plugins").glob("*/.claude-plugin/plugin.json"))
    plugins_by_name: dict[str, dict] = {}
    for pj in plugin_jsons:
        data = load_json(pj)
        if data is None:
            continue
        plugin_dir = pj.parent.parent  # plugins/<name>/
        name = data.get("name")
        if name != plugin_dir.name:
            err(f"{rel(pj)}: name '{name}' does not match directory '{plugin_dir.name}'")
        if name:
            plugins_by_name[name] = data

    if not isinstance(market, dict):
        return
    entries = market.get("plugins", [])
    if not isinstance(entries, list):
        err(".claude-plugin/marketplace.json: 'plugins' must be a list")
        return

    registered = set()
    for entry in entries:
        name = entry.get("name")
        registered.add(name)
        source = entry.get("source", "")
        src_dir = (REPO / source).resolve()
        pj = src_dir / ".claude-plugin" / "plugin.json"
        if not pj.exists():
            err(f"marketplace.json: plugin '{name}' source '{source}' has no plugin.json")
            continue
        plugin_data = plugins_by_name.get(name)
        if plugin_data is None:
            continue
        mv, pv = entry.get("version"), plugin_data.get("version")
        if mv != pv:
            err(
                f"version drift for '{name}': marketplace.json={mv} "
                f"!= plugin.json={pv}"
            )

    # Every plugins/<name> dir must be registered in the marketplace.
    for pj in plugin_jsons:
        name = pj.parent.parent.name
        if name not in registered:
            err(f"plugin '{name}' is not registered in marketplace.json")


def check_skill_frontmatter():
    """Required fields, name==dirname, and description word budget for SKILL.md."""
    for skill in sorted((REPO / "plugins").glob("*/skills/*/SKILL.md")):
        fm = parse_frontmatter(skill.read_text(encoding="utf-8"))
        if fm is None:
            err(f"{rel(skill)}: missing or malformed frontmatter block")
            continue

        name = fm.get("name", "").strip()
        if not name:
            err(f"{rel(skill)}: frontmatter missing required field 'name'")
        elif name != skill.parent.name:
            err(f"{rel(skill)}: name '{name}' does not match directory '{skill.parent.name}'")

        desc = fm.get("description", "").strip()
        if not desc:
            err(f"{rel(skill)}: frontmatter missing required field 'description'")
            continue

        words = len(desc.split())
        if words > DESC_WORDS_ERROR:
            err(f"{rel(skill)}: description is {words} words (max {DESC_WORDS_ERROR})")
        elif words > DESC_WORDS_WARN:
            warn(f"{rel(skill)}: description is {words} words (aim <= {DESC_WORDS_WARN})")


def check_internal_refs():
    """Every ${CLAUDE_PLUGIN_ROOT}/<path> referenced in a plugin must exist."""
    pattern = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}(/[^\s\"'`)>,]+)")
    for md in sorted((REPO / "plugins").rglob("*.md")):
        # Plugin root = plugins/<name>/ — first two path components under plugins/.
        parts = md.relative_to(REPO / "plugins").parts
        plugin_root = REPO / "plugins" / parts[0]
        text = md.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in pattern.finditer(line):
                ref = m.group(1).lstrip("/")
                target = plugin_root / ref
                if not target.exists():
                    err(
                        f"{rel(md)}:{lineno}: references "
                        f"${{CLAUDE_PLUGIN_ROOT}}/{ref} which does not exist"
                    )


def check_shell_scripts():
    """bash -n syntax check on every *.sh in the repo."""
    for script in sorted(REPO.rglob("*.sh")):
        if ".git" in script.parts:
            continue
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            err(f"{rel(script)}: bash syntax error — {detail}")


def main() -> int:
    checks = [
        ("JSON validity + version sync", check_json_and_versions),
        ("SKILL.md frontmatter", check_skill_frontmatter),
        ("internal ${CLAUDE_PLUGIN_ROOT} references", check_internal_refs),
        ("shell script syntax", check_shell_scripts),
    ]
    for label, fn in checks:
        fn()
        print(f"  ran: {label}")

    print()
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    print()
    if errors:
        print(f"FAILED — {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"OK — 0 errors, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
