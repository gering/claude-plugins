#!/usr/bin/env python3
"""kv.sh and the review prep's lookup helpers survive skill-argument substitution.

Claude Code replaces `$<digit>` in SKILL.md content with the skill's arguments
(0-based: `$0` = first) when it renders the skill. The prep block's helpers once
read `sed -n "s/^$1=//p"`, so `/swarm:review --pr 65` looked up the key `65` and
every codex/grok model token came back empty. These tests run the helpers as the
model would see them after that substitution.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PLUGIN = SCRIPTS.parent
KV = SCRIPTS / "kv.sh"
SKILL = PLUGIN / "skills/review/SKILL.md"


def kv(stdin: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(KV), *args], input=stdin, capture_output=True, text=True)


def render(text: str, arguments: list[str]) -> str:
    """Apply the documented `$N` substitution (an index without an argument stays)."""
    def sub(m: re.Match) -> str:
        i = int(m.group(1))
        return arguments[i] if i < len(arguments) else m.group(0)
    return re.sub(r"\$([0-9]+)", sub, text)


class KvScript(unittest.TestCase):
    def test_first_exact_key_wins(self):
        out = kv("familyx=no\nfamily=terra\nfamily=sol\n", "get", "family")
        self.assertEqual((out.returncode, out.stdout), (0, "terra\n"))

    def test_missing_key_is_empty_not_an_error(self):
        self.assertEqual(kv("a=1\n", "get", "b").stdout, "\n")

    def test_charset_filters_value(self):
        self.assertEqual(kv("selected=gpt-5;rm\n", "get", "selected", "A-Za-z0-9._-").stdout, "gpt-5rm\n")

    def test_malformed_calls_are_refused(self):
        for args in ([], ["get"], ["put", "a"], ["get", "a.*"], ["get", ""], ["get", "a", "b", "c"]):
            self.assertEqual(kv("a=1\n", *args).returncode, 2, args)


class RenderedPrepHelpers(unittest.TestCase):
    HELPERS = ("_cp", "_gr", "_gk", "_cr", "_ck")

    def helper_lines(self, text: str) -> list[str]:
        lines = [l for l in text.splitlines() if re.match(r"(%s)\(\) \{" % "|".join(self.HELPERS), l)]
        self.assertEqual(len(lines), len(self.HELPERS), "every prep lookup helper is defined once")
        return lines

    def test_helpers_resolve_keys_after_substitution(self):
        rendered = render(SKILL.read_text(encoding="utf-8"), ["--pr", "65"])
        script = "\n".join([
            'CODEX_POLICY="family=terra\nmodel=\neffort=high"',
            'GROK_KV="selected=grok-5\ncli_version=1.2\ndegraded=pinned; not latest"',
            'CODEX_KV="selected=gpt-6-terra\nsource=catalog-latest"',
            *self.helper_lines(rendered),
            'echo "$(_cp family)|$(_cp effort)|$(_gk selected)|$(_ck selected)|$(_ck source)|$(_gr degraded)"',
        ])
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                             env={"CLAUDE_PLUGIN_ROOT": str(PLUGIN), "PATH": "/usr/bin:/bin"})
        self.assertEqual(out.stdout.strip(), "terra|high|grok-5|gpt-6-terra|catalog-latest|pinned; not latest", out.stderr)

    def test_empty_lookups_stop_the_prep(self):
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn('if [ -z "$CODEX_FAMILY" ] || [ -z "$CODEX_EFFORT" ]; then', skill)
        self.assertIn('if [ "$(_gr selected)" != "$GROK_MODEL" ]; then', skill)
        self.assertIn('if [ "$(_cr selected)" != "$CODEX_MODEL" ]; then', skill)
        self.assertIn("SWARM_PREP_ERR=grok-model selected a model id", skill)
        self.assertIn("SWARM_PREP_ERR=codex-model selected a model id", skill)
        self.assertIn('[ -r "${CLAUDE_PLUGIN_ROOT}/scripts/kv.sh" ]', skill)
        self.assertIn("- `SWARM_PREP_ERR=…` → surface it **verbatim** and **stop**", skill)


if __name__ == "__main__":
    unittest.main()
