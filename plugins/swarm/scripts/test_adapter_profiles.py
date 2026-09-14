#!/usr/bin/env python3
"""Hermetic adapter profile contracts; never invoke a backend CLI or the network."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ADAPTER = HERE / "agents.sh"
SCHEMA = HERE / "schema" / "finding.schema.json"

# Every external boundary is denied unless a test explicitly supplies a fake.
# PATH contains only this fixture's Python and OS utilities, not host CLI bins.
STUBS = r'''
_probe_or_bare() { printf 'unexpected probe\n' >> "$PROBES"; return 98; }
backend_installed() { printf 'unexpected installed check\n' >> "$PROBES"; return 98; }
available_version() { printf 'unexpected version check\n' >> "$PROBES"; return 98; }
sandboxed() { printf 'unexpected sandbox call\n' >> "$PROBES"; return 98; }
_probe_setup_lenient() { _probe_timeout=1; }
_probe_setup_strict() { _probe_timeout=1; }
_scratch_dir_ok() { return 0; }
_read_web_safe() { _REPO_ROOT_MEMO="$FIXTURE"; return 0; }
_assert_prompt_readable_in_jail() { return 0; }
_note_prompt_dir() { return 0; }
_kimi_prepare_runtime() { TMP_KIMI_HOME=$(mktemp -d "$FIXTURE/runtime.XXXXXX"); }
'''

CATALOG_STUBS = r'''
backend_installed() { [[ "$1" == codex ]]; }
available_version() { [[ "$1" == codex ]] || return 1; printf 'fixture-version\n'; }
_probe_or_bare() {
  printf '%s\n' "$*" >> "$PROBES"
  if [[ "$*" == 'codex login status' ]]; then
    return "$AUTH_RC"
  fi
  if [[ "$1" == python3 && "$2" == "$SCRIPT_DIR/codex-models.py" && "$3" == --timeout ]]; then
    cat "$CATALOG"
    return "$CATALOG_RC"
  fi
  printf 'unexpected probe\n' >> "$PROBES"
  return 98
}
'''

KIMI_STUB = r'''
sandboxed() {
  printf '%s\n' "$@" > "$ARGV_FILE"
  local previous='' arg prompt='' metrics=''
  for arg in "$@"; do
    [[ "$previous" != --prompt-file ]] || prompt="$arg"
    [[ "$previous" != --metrics-file ]] || metrics="$arg"
    previous="$arg"
  done
  cp "$prompt" "$CAPTURE"
  printf '%s\n' "$metrics" > "$METRICS_PATH"
  [[ "$METRICS" == missing ]] || printf '%s' "$METRICS" > "$metrics"
  printf '{"findings":[]}'
  return "$HELPER_RC"
}
'''


def quote(value):
    return shlex.quote(str(value))


def checksum(text):
    value = 0x811C9DC5
    for byte in text.encode():
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


class AdapterProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        for backend in ("codex", "grok", "kimi"):
            path = self.bin / backend
            path.write_text('#!/bin/sh\nprintf "REAL CLI ATTEMPT\\n" >> "$PROBES"\nexit 98\n')
            path.chmod(0o700)
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("SUPPLIED DIFF\n")
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps({"models": [{"id": "picker-id", "model": "gpt-6-astra"}],
                                           "complete": True, "authoritative": False}))
        self.probes = self.root / "probes"
        self.capture = self.root / "captured.txt"
        self.argv_file = self.root / "argv.txt"
        self.telemetry = self.root / "telemetry.jsonl"
        self.metrics_path = self.root / "metrics-path.txt"
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "HOME": str(self.root), "TMPDIR": str(self.root), "FIXTURE": str(self.root),
            "PROBES": str(self.probes), "CATALOG": str(self.catalog), "CATALOG_RC": "0",
            "AUTH_RC": "0", "CAPTURE": str(self.capture), "ARGV_FILE": str(self.argv_file),
            "METRICS_PATH": str(self.metrics_path), "METRICS": "missing", "HELPER_RC": "0",
            "SWARM_KIMI": "1",
        }

    def shell(self, lines, stubs="", env=None):
        script = "source " + quote(ADAPTER) + "\n" + STUBS + "\n" + stubs + "\n" + lines
        result = subprocess.run(["/bin/bash", "-c", script], cwd=self.root,
                                env={**self.env, **(env or {})}, text=True,
                                capture_output=True, timeout=10)
        if self.probes.exists():
            self.assertNotIn("unexpected", self.probes.read_text())
            self.assertNotIn("REAL CLI ATTEMPT", self.probes.read_text())
        return result

    def run_args(self, backend, *args):
        return "main run " + " ".join(map(quote, [
            backend, "--prompt-file", self.prompt, *args,
        ]))

    def test_ready_uses_selected_model_not_default_or_picker_id(self):
        result = self.shell("main ready codex --model gpt-6-astra", CATALOG_STUBS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ready")
        self.assertNotIn("unverified", result.stderr)
        self.assertEqual(len(self.probes.read_text().splitlines()), 2)
        result = self.shell("main ready codex --model picker-id", CATALOG_STUBS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("picker-id", result.stderr)
        self.assertIn("unverified", result.stderr)

    def test_custom_alias_remains_ready_with_audible_and_json_hint(self):
        result = self.shell("main list --json --codex-model custom/preview+v2", CATALOG_STUBS)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = next(row for row in json.loads(result.stdout) if row["backend"] == "codex")
        self.assertIs(row["ready"], True)
        self.assertIn("custom/preview+v2", row["hint"])
        self.assertIn("auth-only", row["hint"])
        self.assertIn("unverified", result.stderr)

    def test_auth_failure_does_not_spend_a_model_probe(self):
        result = self.shell("main ready codex --model gpt-6-astra", CATALOG_STUBS,
                            env={"AUTH_RC": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.probes.read_text().splitlines(), ["codex login status"])

    def test_catalog_is_memoized_and_hint_is_reset_per_selection(self):
        result = self.shell(
            'codex_model_offered custom/unlisted; codex_model_offered gpt-6-astra; '
            'printf "hint=%s\\n" "$_codex_model_hint"', CATALOG_STUBS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "hint=")
        self.assertEqual(len(self.probes.read_text().splitlines()), 1)

    def test_invalid_failed_and_partial_catalogs_degrade_without_rejection(self):
        for text, rc in (
            ('{"complete":true,"models":[{"model":"gpt-6-astra"}]}', "124"),
            ('{"complete":false,"models":[{"model":"gpt-6-astra"}]}', "0"),
            ('{"complete":true,"models":[{"model":"gpt-6-astra"},{}]}', "0"),
            ('{"complete":true,"models":[{"model":"bad name"}]}', "0"),
            ('{"complete":true,"models":null}', "0"),
            ('{"models":[{"model":"gpt-6-astra"}]}', "0"),
            ('{"complete":true,"models":[]}', "0"),
            ('not json', "0"),
        ):
            with self.subTest(text=text, rc=rc):
                self.catalog.write_text(text)
                result = self.shell('main list --json --codex-model gpt-6-astra',
                                    CATALOG_STUBS, env={"CATALOG_RC": rc})
                self.assertEqual(result.returncode, 0, result.stderr)
                row = next(r for r in json.loads(result.stdout) if r["backend"] == "codex")
                self.assertTrue(row["ready"])
                self.assertIn("unverified", row["hint"])
                self.assertIn("auth-only", result.stderr)

    def test_invalid_flags_fail_before_any_probe(self):
        invalid = [
            ("codex", "--tools", "false"), ("grok", "--tools", "false"),
            ("kimi", "--tools", "maybe"),
            ("kimi", "--tools", "false", "--tool-budget", "1"),
            ("kimi", "--tools", "true", "--tool-budget", "0"),
            ("codex", "--tool-budget", "-1"), ("codex", "--tool-budget", "1001"),
            ("codex", "--tool-budget", "bad"), ("codex", "--tool-budget", "1.5"),
            ("codex", "--tools"), ("codex", "--tool-budget"),
            ("codex", "--model", "two words"), ("codex", "--model", "$(bad)"),
            ("codex", "--model", "-option"),
        ]
        for backend, *args in invalid:
            with self.subTest(backend=backend, args=args):
                result = self.shell(self.run_args(backend, *args))
                self.assertEqual(result.returncode, 2, result.stderr)
        for command in ("main ready codex --model 'bad name'",
                        "main list --json --codex-model 'bad name'",
                        "main ready codex --model", "main list --codex-model"):
            result = self.shell(command)
            self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.probes.exists())

    def test_codex_and_grok_budget_is_after_lens_before_immutable_diff(self):
        stubs = r'''
require_usable() { printf '%s\n' "$@" > "$ARGV_FILE"; }
run_codex() { cp "$1" "$CAPTURE"; }
run_grok() { cp "$1" "$CAPTURE"; }
'''
        for backend in ("codex", "grok"):
            for budget in (1, 8, 1000):
                with self.subTest(backend=backend, budget=budget):
                    lens = "ONLY SELECTED LENSES"
                    result = self.shell(self.run_args(
                        backend, "--model", "selected-model", "--tools", "true",
                        "--tool-budget", str(budget), "--lens-instr", lens,
                        "--lens-instr-sum", checksum(lens)), stubs)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    text = self.capture.read_text()
                    self.assertLess(text.index(lens), text.index("TOOL BUDGET"))
                    self.assertLess(text.index("TOOL BUDGET"), text.index("SUPPLIED DIFF"))
                    self.assertIn(f"at most {budget} tool calls", text)
                    self.assertIn("advisory", text)
                    self.assertEqual(self.prompt.read_text(), "SUPPLIED DIFF\n")
                    self.assertEqual(self.argv_file.read_text().splitlines(),
                                     [backend, "selected-model"])

    def test_tool_policy_defaults_follow_backend_mode(self):
        stubs = r'''
require_usable() { return 0; }
run_codex() { cp "$1" "$CAPTURE"; }
run_grok() { cp "$1" "$CAPTURE"; }
run_kimi() { printf '%s %s\n' "$5" "$6" > "$CAPTURE"; }
'''
        for backend in ("codex", "grok"):
            result = self.shell(self.run_args(backend), stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("at most 8 tool calls", self.capture.read_text())
        for flags, expected in (((), "true 8"), (("--tools", "false"), "false 0")):
            result = self.shell(self.run_args("kimi", *flags), stubs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.capture.read_text().strip(), expected)

    def kimi_run(self, tools="false", budget="0", metrics="missing", rc="0"):
        command = self.run_args("kimi", "--tools", tools, "--tool-budget", budget,
                                "--telemetry", self.telemetry, "--effort", "low")
        return self.shell(command, KIMI_STUB + '\nrequire_usable() { return 0; }',
                          env={"METRICS": metrics, "HELPER_RC": rc})

    def test_kimi_disabled_tools_reach_acp_and_prompt_without_grants(self):
        result = self.kimi_run()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.argv_file.read_text().splitlines()
        self.assertEqual(args[args.index("--tools") + 1], "false")
        self.assertEqual(args[args.index("--schema") + 1], str(SCHEMA))
        text = self.capture.read_text()
        self.assertIn("TOOLS: disabled", text)
        self.assertIn("TOOL BUDGET: 0", text)
        self.assertNotIn("TOOLS: read-only", text)
        self.assertNotIn("File read/search and public web research", text)
        self.assertEqual(self.prompt.read_text(), "SUPPLIED DIFF\n")

    def test_kimi_enabled_tools_keep_requested_advisory_budget(self):
        result = self.kimi_run(tools="true", budget="12")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.argv_file.read_text().splitlines()
        self.assertEqual(args[args.index("--tools") + 1], "true")
        self.assertIn("TOOLS: read-only", self.capture.read_text())
        self.assertIn("TOOL BUDGET: at most 12 calls", self.capture.read_text())

    def test_metrics_are_imported_before_runtime_cleanup(self):
        fixtures = [
            ('{"tool_calls":0,"complete":true}', 0, True),
            ('{"tool_calls":3,"complete":false}', 3, False),
            ('{"tool_calls":2,"complete":true}', 2, True),
            ('{"tool_calls":true,"complete":true}', None, False),
            ('{"tool_calls":-1,"complete":true}', None, False),
            ('{"tool_calls":9007199254740992,"complete":true}', None, False),
            ('{"tool_calls":"2","complete":true}', None, False),
            ('{"tool_calls":2,"complete":"true"}', 2, False),
            ('missing', None, False), ('not json', None, False), ('[]', None, False),
        ]
        for metrics, count, complete in fixtures:
            with self.subTest(metrics=metrics):
                result = self.kimi_run(metrics=metrics)
                self.assertEqual(result.returncode, 0, result.stderr)
                row = json.loads(self.telemetry.read_text().splitlines()[-1])
                self.assertEqual(row["tool_calls"], count)
                self.assertIs(row["tool_calls_complete"], complete)
                self.assertFalse(Path(self.metrics_path.read_text().strip()).parent.exists())
        result = self.kimi_run(metrics='{"tool_calls":1,"complete":false}', rc="13")
        self.assertEqual(result.returncode, 1)
        row = json.loads(self.telemetry.read_text().splitlines()[-1])
        self.assertEqual(row["backend_rc"], 13)
        self.assertEqual(row["tool_calls"], 1)
        self.assertFalse(row["tool_calls_complete"])

    def test_schema_compaction_removes_annotations_not_instance_data(self):
        literal = {"description": "literal", "title": "keep", "examples": [1], "$comment": "keep"}
        schema = {
            "title": "annotation", "description": "annotation", "$comment": "annotation",
            "examples": ["annotation"], "type": "object", "required": ["description"],
            "properties": {
                "description": {"type": "string", "description": "annotation", "const": "exact"},
                "const": {"const": literal, "title": "annotation"},
                "enum": {"enum": [literal], "examples": ["annotation"]},
            },
            "$defs": {"description": {"type": "integer", "minimum": 2, "title": "annotation"}},
            "allOf": [{"description": "annotation", "additionalProperties": False}],
            "items": [{"type": "string", "description": "annotation"}],
            "if": {"properties": {"title": {"const": "literal", "title": "annotation"}}},
        }
        path = self.root / "caller-schema.json"
        original = json.dumps(schema, indent=2)
        path.write_text(original)
        result = self.shell(f"_kimi_output_contract {quote(path)} false 0")
        self.assertEqual(result.returncode, 0, result.stderr)
        compact = json.loads(result.stdout.splitlines()[-1])
        for key in ("description", "title", "$comment", "examples"):
            self.assertNotIn(key, compact)
        self.assertEqual(compact["properties"]["description"], {"type": "string", "const": "exact"})
        self.assertEqual(compact["properties"]["const"]["const"], literal)
        self.assertEqual(compact["properties"]["enum"]["enum"], [literal])
        self.assertEqual(compact["$defs"]["description"], {"type": "integer", "minimum": 2})
        self.assertEqual(compact["allOf"], [{"additionalProperties": False}])
        self.assertEqual(compact["items"], [{"type": "string"}])
        self.assertEqual(compact["if"]["properties"]["title"], {"const": "literal"})
        self.assertEqual(path.read_text(), original)
        self.assertLess(len(result.stdout.splitlines()[-1]), len(original))


if __name__ == "__main__":
    unittest.main()
