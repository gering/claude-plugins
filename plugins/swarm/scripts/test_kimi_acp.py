#!/usr/bin/env python3
"""Hermetic ACP transport and schema-gate tests for the Kimi backend helper."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "kimi-acp.py"
SCHEMA = HERE / "schema" / "finding.schema.json"
MODEL = "kimi-code/k3-256k"

_FAKE_KIMI = r'''#!/usr/bin/env python3
import json
import os
import sys

scenario = os.environ.get("FAKE_SCENARIO", "valid")
log_path = os.environ["FAKE_LOG"]
state = {"model": "kimi-code/other", "thinking": "high", "mode": "auto"}


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log(record):
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def config_options():
    models = [
        {"value": "kimi-code/other", "name": "Other"},
        {"value": "kimi-code/k3-256k", "name": "K3-256k"},
    ]
    if scenario == "model-missing":
        models = models[:1]
    return [
        {"type": "select", "id": "model", "name": "Model", "currentValue": state["model"], "options": models},
        {"type": "select", "id": "thinking", "name": "Thinking", "currentValue": state["thinking"], "options": [
            {"value": "low", "name": "Low"},
            {"value": "high", "name": "High"},
            {"value": "max", "name": "Max"},
        ]},
        {"type": "select", "id": "mode", "name": "Mode", "currentValue": state["mode"], "options": [
            {"value": "default", "name": "Default"},
            {"value": "auto", "name": "Auto"},
        ]},
    ]


def response(request_id, result):
    emit({"jsonrpc": "2.0", "id": request_id, "result": result})


log({"argv": sys.argv[1:]})
for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})
    if method == "initialize":
        response(request_id, {"protocolVersion": 1})
    elif method == "session/new":
        log({"new": params})
        response(request_id, {"sessionId": "fake-session", "configOptions": config_options()})
    elif method == "session/set_config_option":
        state[params["configId"]] = params["value"]
        log({"set": params})
        response(request_id, {"configOptions": config_options()})
    elif method == "session/prompt":
        log({"prompt": params["prompt"], "state": state.copy()})
        if scenario == "permission":
            emit({
                "jsonrpc": "2.0",
                "id": 700,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "fake-session",
                    "toolCall": {"toolCallId": "tool-1"},
                    "options": [
                        {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            })
            log({"permission_response": json.loads(sys.stdin.readline())})
        elif scenario == "unexpected-client-request":
            emit({
                "jsonrpc": "2.0",
                "id": 701,
                "method": "terminal/create",
                "params": {"sessionId": "fake-session", "command": "pwd"},
            })
            log({"client_error_response": json.loads(sys.stdin.readline())})
        elif scenario == "unsafe-completed":
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "tool-2",
                        "title": "Bash",
                        "kind": "execute",
                        "status": "completed",
                    },
                },
            })
        elif scenario == "orphan-completed-update":
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "orphan-tool",
                        "status": "completed",
                    },
                },
            })

        if scenario == "invalid-json":
            answer = "RAW_SECRET_SHOULD_NOT_LEAK"
        elif scenario == "wrong-shape":
            answer = '{"findings":[],"extra":true}'
        elif scenario == "missing-field":
            answer = '{"findings":[{"file":"x","line":1}]}'
        elif scenario == "no-output":
            answer = ""
        else:
            answer = '{"findings":[]}'
        if answer:
            split = max(1, len(answer) // 2)
            for chunk in (answer[:split], answer[split:]):
                emit({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "fake-session",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": chunk},
                        },
                    },
                })
        response(request_id, {"stopReason": "end_turn"})
    else:
        emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}})
'''


class KimiAcpTests(unittest.TestCase):
    def run_helper(self, scenario: str = "valid", *, schema: Path = SCHEMA):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        fake = root / "kimi"
        fake.write_text(_FAKE_KIMI, encoding="utf-8")
        fake.chmod(0o755)
        prompt = root / "prompt.txt"
        prompt.write_text("PROMPT_SENTINEL_7f0ac9\n", encoding="utf-8")
        log = root / "fake.log"
        env = os.environ.copy()
        env.update({
            "SWARM_KIMI_BIN": str(fake),
            "FAKE_SCENARIO": scenario,
            "FAKE_LOG": str(log),
        })
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--prompt-file", str(prompt),
                "--schema", str(schema),
                "--cwd", str(root),
                "--model", MODEL,
                "--effort", "max",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        records = []
        if log.exists():
            records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return result, records

    def test_valid_prompt_is_out_of_band_and_configured(self):
        result, records = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"findings": []})
        self.assertEqual(records[0]["argv"], ["acp"])
        prompt_record = next(record for record in records if "prompt" in record)
        self.assertIn("PROMPT_SENTINEL_7f0ac9", prompt_record["prompt"][0]["text"])
        self.assertNotIn("PROMPT_SENTINEL_7f0ac9", " ".join(records[0]["argv"]))
        self.assertEqual(
            prompt_record["state"],
            {"model": MODEL, "thinking": "max", "mode": "default"},
        )

    def test_permission_requests_are_rejected(self):
        result, records = self.run_helper("permission")
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = next(record["permission_response"] for record in records if "permission_response" in record)
        self.assertEqual(
            reply["result"]["outcome"],
            {"outcome": "selected", "optionId": "reject"},
        )

    def test_invalid_json_fails_without_echoing_content(self):
        result, _ = self.run_helper("invalid-json")
        self.assertEqual(result.returncode, 11)
        self.assertIn("invalid JSON", result.stderr)
        self.assertNotIn("RAW_SECRET_SHOULD_NOT_LEAK", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_schema_rejects_extra_fields_and_missing_fields(self):
        for scenario, fragment in (("wrong-shape", "unexpected field"), ("missing-field", "missing required")):
            with self.subTest(scenario=scenario):
                result, _ = self.run_helper(scenario)
                self.assertEqual(result.returncode, 11)
                self.assertIn(fragment, result.stderr)

    def test_empty_assistant_output_fails_closed(self):
        result, _ = self.run_helper("no-output")
        self.assertEqual(result.returncode, 11)
        self.assertIn("no assistant text", result.stderr)

    def test_model_must_be_offered(self):
        result, _ = self.run_helper("model-missing")
        self.assertEqual(result.returncode, 12)
        self.assertIn("does not offer model", result.stderr)

    def test_unexpected_client_request_fails_review(self):
        result, records = self.run_helper("unexpected-client-request")
        self.assertEqual(result.returncode, 13)
        self.assertIn("terminal/create", result.stderr)
        reply = next(record["client_error_response"] for record in records if "client_error_response" in record)
        self.assertEqual(reply["error"]["code"], -32601)

    def test_completed_unsafe_tool_fails_review(self):
        result, _ = self.run_helper("unsafe-completed")
        self.assertEqual(result.returncode, 13)
        self.assertIn("unsafe tool completed", result.stderr)

    def test_completed_tool_update_without_known_kind_fails_review(self):
        result, _ = self.run_helper("orphan-completed-update")
        self.assertEqual(result.returncode, 13)
        self.assertIn("malformed ACP tool update", result.stderr)
        self.assertIn("no known kind", result.stderr)

    def test_unsupported_schema_keyword_fails_before_backend(self):
        with tempfile.TemporaryDirectory() as td:
            schema = Path(td) / "schema.json"
            schema.write_text(
                json.dumps({"type": "object", "patternProperties": {}}),
                encoding="utf-8",
            )
            result, records = self.run_helper(schema=schema)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported schema keyword", result.stderr)
        self.assertEqual(records, [])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
