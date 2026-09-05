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
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(data.encode("utf-8"))
    sys.stdout.buffer.flush()


def log(record):
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def tool_update(kind_of_update, tool_id, kind, status):
    update = {"sessionUpdate": kind_of_update, "toolCallId": tool_id, "title": "T", "status": status}
    if kind is not None:
        update["kind"] = kind
    return {"jsonrpc": "2.0", "method": "session/update",
            "params": {"sessionId": "fake-session", "update": update}}


def agent_text(text):
    return {"jsonrpc": "2.0", "method": "session/update",
            "params": {"sessionId": "fake-session",
                       "update": {"sessionUpdate": "agent_message_chunk",
                                  "content": {"type": "text", "text": text}}}}


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
    if scenario == "bad-utf8":
        sys.stdout.buffer.write(b"\xff\xfe not-json\n")
        sys.stdout.buffer.flush()
        continue
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
            emit(tool_update("tool_call", "tool-1", "execute", "pending"))
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
            emit(tool_update("tool_call_update", "tool-1", None, "failed"))
        elif scenario == "unsafe-failed-unrejected":
            # The command RAN and exited non-zero; nobody asked for approval.
            emit(tool_update("tool_call", "tool-9", "execute", "pending"))
            emit(tool_update("tool_call_update", "tool-9", None, "failed"))
        elif scenario == "kind-rewrite":
            # Announced as execute, "completed" as read: the unsafe kind sticks.
            emit(tool_update("tool_call", "tool-8", "execute", "pending"))
            emit(tool_update("tool_call_update", "tool-8", "read", "completed"))
        elif scenario == "unsafe-in-progress":
            emit(tool_update("tool_call", "tool-7", "execute", "in_progress"))
            # Keep streaming: the client must NOT wait for end_turn.
            for _ in range(50):
                emit(agent_text("still running "))
        elif scenario == "unknown-kind-pending":
            # Announced with an unlisted kind and never resolved: unsettled.
            emit(tool_update("tool_call", "tool-6", "teleport", "pending"))
        elif scenario == "kind-late":
            # Announced WITHOUT a kind, later "completed" as read: the missing
            # kind must stay missing (= unsafe), not be laundered by the update.
            emit(tool_update("tool_call", "tool-5", None, "pending"))
            emit(tool_update("tool_call_update", "tool-5", "read", "completed"))
        elif scenario == "read-credentials":
            u = tool_update("tool_call", "tool-4", "read", "pending")
            u["params"]["update"]["locations"] = [
                {"path": os.environ["FAKE_DENY"] + "/credentials/kimi-code.json"}]
            emit(u)
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
        elif scenario == "auto-approved-write":
            emit({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "tool-write",
                        "title": "Write",
                        "kind": "edit",
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
        elif scenario == "unicode":
            answer = json.dumps({
                "findings": [{
                    "file": "café.py",
                    "line": 1,
                    "severity": "warning",
                    "summary": "[security] naïve 日本語 round-trip",
                    "failure_scenario": "locale C must keep café",
                    "confidence": "high",
                    "recommendation": "keep utf-8",
                }]
            }, ensure_ascii=False)
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
        log({"prompt_done": True})
    else:
        emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}})
'''


# unittest (not the siblings' check()/FAILS idiom) on purpose: every case drives
# a fake ACP server subprocess through run_helper's fixture, and unittest's
# addCleanup/TemporaryDirectory handling is what keeps those fixtures hermetic.
class KimiAcpTests(unittest.TestCase):
    def run_helper(self, scenario: str = "valid", *, schema: Path = SCHEMA,
                   env_extra: dict | None = None):
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
            "FAKE_SCENARIO": scenario,
            "FAKE_DENY": str(root / ".kimi-code"),
            "FAKE_LOG": str(log),
        })
        if env_extra:
            env.update(env_extra)
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--prompt-file", str(prompt),
                "--schema", str(schema),
                "--cwd", str(root),
                "--model", MODEL,
                "--effort", "max",
                "--kimi-bin", str(fake),
                "--deny-path", str(root / ".kimi-code"),
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
        self.assertIn("unsafe tool ran", result.stderr)

    def test_auto_approved_write_fails_review(self):
        result, _ = self.run_helper("auto-approved-write")
        self.assertEqual(result.returncode, 13)
        self.assertIn("unsafe tool ran", result.stderr)
        self.assertIn("edit", result.stderr)

    def test_rejected_tool_that_then_fails_is_fine(self):
        # tool_call pending → request_permission (rejected) → failed: the
        # legitimate reject flow must not be mistaken for a run, and the turn
        # must run to completion.
        result, records = self.run_helper("permission")
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = next(r["permission_response"] for r in records if "permission_response" in r)
        self.assertEqual(reply["result"]["outcome"]["optionId"], "reject")
        self.assertTrue(any("prompt_done" in r for r in records), "turn did not complete")

    def test_failed_unsafe_tool_without_rejection_fails_review(self):
        # Denylist-era gate only fired on status == completed: a shell whose
        # command exited non-zero had run and slipped through.
        result, _ = self.run_helper("unsafe-failed-unrejected")
        self.assertEqual(result.returncode, 13)
        self.assertIn("unsafe tool ran", result.stderr)
        self.assertIn("status=failed", result.stderr)

    def test_kind_rewrite_cannot_launder_an_unsafe_tool(self):
        result, _ = self.run_helper("kind-rewrite")
        self.assertEqual(result.returncode, 13)
        self.assertIn("kind=execute", result.stderr)

    def test_in_progress_unsafe_tool_aborts_immediately(self):
        # The fake keeps streaming after the tool_call; the client must kill the
        # session on first sight rather than wait for end_turn.
        result, records = self.run_helper("unsafe-in-progress")
        self.assertEqual(result.returncode, 13)
        self.assertIn("status=in_progress", result.stderr)
        # The fake logs prompt_done only after its end_turn response; a client
        # that waited for end_turn would have let it get there.
        self.assertFalse(any("prompt_done" in r for r in records), "client waited for end_turn")

    def test_missing_kind_is_not_laundered_by_a_later_update(self):
        result, _ = self.run_helper("kind-late")
        self.assertEqual(result.returncode, 13)
        self.assertIn("no known kind", result.stderr)

    def test_read_under_the_runtime_store_aborts(self):
        # A `read` is a safe KIND, but not of the linked credential file.
        result, records = self.run_helper("read-credentials")
        self.assertEqual(result.returncode, 13)
        self.assertIn("denied path", result.stderr)
        # (No prompt_done assertion: the fake writes its whole turn at once, so
        # whether the kill lands before its last log line is a race.)

    def test_unknown_kind_left_pending_is_unsettled(self):
        result, _ = self.run_helper("unknown-kind-pending")
        self.assertEqual(result.returncode, 13)
        self.assertIn("never rejected", result.stderr)
        self.assertIn("teleport", result.stderr)

    def test_unicode_round_trips_under_ascii_locale(self):
        ascii_env = {
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONUTF8": "0",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONIOENCODING": "ascii",
        }
        result, _ = self.run_helper("unicode", env_extra=ascii_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["findings"][0]["file"], "café.py")
        self.assertIn("naïve 日本語", payload["findings"][0]["summary"])
        self.assertTrue(result.stdout.isascii())

    def test_invalid_utf8_frame_is_protocol_error(self):
        result, _ = self.run_helper("bad-utf8")
        self.assertEqual(result.returncode, 12)
        self.assertIn("not valid UTF-8", result.stderr)
        self.assertEqual(result.stdout, "")

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
