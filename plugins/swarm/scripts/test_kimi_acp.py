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
import time

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


def response_error(request_id, code, message):
    emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


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
            {"value": "plan", "name": "Plan"},
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
    if scenario == "tools-event" and method == os.environ.get("FAKE_TOOL_STAGE", "session/prompt"):
        emit(json.loads(os.environ["FAKE_TOOL_EVENT"]))
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
        if scenario == "many-tools":
            for index in range(12):
                emit(tool_update("tool_call", f"read-{index}", "read", "completed"))
        elif scenario in {"metrics-mixed", "metrics-mixed-error"}:
            emit(tool_update("tool_call", "m1", "read", "pending"))
            for tool_id in ("m1", "m2", "permission-only"):
                emit({"jsonrpc": "2.0", "id": 799, "method": "session/request_permission",
                      "params": {"sessionId": "fake-session", "toolCall": {"toolCallId": tool_id},
                                 "options": []}})
                log({"permission_response": json.loads(sys.stdin.readline())})
                metrics_path = os.environ.get("FAKE_METRICS")
                if metrics_path:
                    with open(metrics_path, encoding="utf-8") as handle:
                        log({"metrics_progress": json.load(handle)})
            # Permission-first m2 and notification-only m3, with repeated
            # announcements/status snapshots: updates never inflate attempts.
            for tool_id in ("m1", "m2", "m3", "m3"):
                emit(tool_update("tool_call", tool_id, "read", "pending"))
                emit(tool_update("tool_call_update", tool_id, None, "in_progress"))
                emit(tool_update("tool_call_update", tool_id, None, "completed"))
            if scenario == "metrics-mixed-error":
                response_error(request_id, -32000, "fake failure after tools")
                continue
        elif scenario == "metrics-invalid-permission-id":
            emit({"jsonrpc": "2.0", "id": 799, "method": "session/request_permission",
                  "params": {"sessionId": "fake-session", "toolCall": {}, "options": []}})
            log({"permission_response": json.loads(sys.stdin.readline())})
        elif scenario == "metrics-invalid-notification-id":
            emit(tool_update("tool_call", ["not-a-string"], "read", "pending"))
        elif scenario == "permission":
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
        elif scenario == "exec-allowed":
            # Kimi auto-ran a read-only command (no permission request): the
            # policy vets it after the fact and the review completes.
            u = tool_update("tool_call", "tool-x1", "execute", "in_progress")
            u["params"]["update"]["rawInput"] = {"command": os.environ.get("FAKE_COMMAND", "git log --oneline -5 | head -3")}
            emit(u)
            emit(tool_update("tool_call_update", "tool-x1", None, "completed"))
        elif scenario == "exec-rewrite":
            # Vetted as `git log`, then the same id carries a different command.
            u = tool_update("tool_call", "tool-x9", "execute", "in_progress")
            u["params"]["update"]["rawInput"] = {"command": "git log -1"}
            emit(u)
            u = tool_update("tool_call_update", "tool-x9", None, "in_progress")
            u["params"]["update"]["rawInput"] = {"command": "git config user.email x"}
            emit(u)
            time.sleep(1.0)
            for _ in range(50):
                emit(agent_text("still running "))
        elif scenario == "exec-denied":
            u = tool_update("tool_call", "tool-x2", "execute", "in_progress")
            u["params"]["update"]["rawInput"] = {"command": os.environ.get("FAKE_COMMAND", "git log > /tmp/out")}
            emit(u)
            time.sleep(1.0)   # let the kill land (see unsafe-in-progress)
            for _ in range(50):
                emit(agent_text("still running "))
        elif scenario == "exec-permission":
            # Kimi ASKS before running: an allowlisted command is approved once,
            # anything else rejected — the fake logs which option came back.
            u = tool_update("tool_call", "tool-x3", "execute", "pending")
            u["params"]["update"]["rawInput"] = {"command": os.environ.get("FAKE_COMMAND", "git blame -L 1,5 README.md")}
            emit(u)
            emit({
                "jsonrpc": "2.0",
                "id": 701,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "fake-session",
                    "toolCall": {"toolCallId": "tool-x3", "kind": "execute",
                                 "rawInput": {"command": os.environ.get("FAKE_COMMAND", "git blame -L 1,5 README.md")}},
                    "options": [
                        {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            })
            reply = json.loads(sys.stdin.readline())
            log({"permission_response": reply})
            chosen = reply.get("result", {}).get("outcome", {}).get("optionId")
            emit(tool_update("tool_call_update", "tool-x3", None, "completed" if chosen == "allow" else "failed"))
        elif scenario == "unsafe-failed-unrejected":
            # The command RAN and exited non-zero; nobody asked for approval.
            emit(tool_update("tool_call", "tool-9", "execute", "pending"))
            emit(tool_update("tool_call_update", "tool-9", None, "failed"))
        elif scenario == "kind-rewrite":
            # Announced as execute, "completed" as read: the unsafe kind sticks.
            emit(tool_update("tool_call", "tool-8", "execute", "pending"))
            emit(tool_update("tool_call_update", "tool-8", "read", "completed"))
        elif scenario == "exec-no-command":
            # An execute whose command never becomes visible (no rawInput, no
            # argument text): nothing to vet, so the end-of-turn sweep fails it.
            emit(tool_update("tool_call", "tool-7x", "execute", "in_progress"))
        elif scenario == "exec-snapshots":
            # kimi-code 0.41 shape: argument JSON streamed as cumulative text
            # snapshots, then a rawInput frame, then a permission request.
            u = tool_update("tool_call", "tool-7s", "execute", "pending")
            u["params"]["update"]["content"] = [{"type": "content", "content": {"type": "text", "text": ""}}]
            emit(u)
            cmd = os.environ.get("FAKE_COMMAND", "git log --oneline -3")
            full = json.dumps({"command": cmd})
            for cut in list(range(1, len(full), 4)) + [len(full)]:
                u = tool_update("tool_call_update", "tool-7s", None, "in_progress")
                u["params"]["update"]["content"] = [{"type": "content", "content": {"type": "text", "text": full[:cut]}}]
                emit(u)
            emit({
                "jsonrpc": "2.0", "id": 702, "method": "session/request_permission",
                "params": {"sessionId": "fake-session", "toolCall": {"toolCallId": "tool-7s"},
                           "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                       {"optionId": "reject", "name": "Reject", "kind": "reject_once"}]},
            })
            reply = json.loads(sys.stdin.readline())
            log({"permission_response": reply})
            chosen = reply.get("result", {}).get("outcome", {}).get("optionId")
            emit(tool_update("tool_call_update", "tool-7s", None, "completed" if chosen == "allow" else "failed"))
        elif scenario == "unsafe-in-progress":
            # An in-progress EDIT: never approvable, so the client kills on
            # sight instead of waiting for end_turn (execute differs: its
            # command may still be streaming — see exec-no-command).
            emit(tool_update("tool_call", "tool-7", "edit", "in_progress"))
            # Give the client's kill a moment to land: it arrives within
            # milliseconds of the frame above, and a client that instead waited
            # for end_turn lets this fake reach prompt_done after the pause —
            # which is what the test asserts. Without the pause the whole burst
            # (plus prompt_done) could be written before the kill (1 in ~8 runs).
            time.sleep(1.0)
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
        elif scenario == "read-credentials-relative":
            u = tool_update("tool_call", "tool-2r", "read", "pending")
            u["params"]["update"]["rawInput"] = {"path": "./.kimi-code/credentials/kimi-code.json"}
            emit(u)
        elif scenario == "read-credentials-symlink":
            u = tool_update("tool_call", "tool-2s", "read", "pending")
            u["params"]["update"]["rawInput"] = {"path": "leak.json"}
            emit(u)
        elif scenario == "prompt-error":
            response_error(request_id, -32000, "Authentication required: 403 You've reached your 5-hour usage limit.")
            continue
        elif scenario == "flood":
            for _ in range(2000):
                emit(agent_text("x" * 8192))
        elif scenario == "read-credentials-rawinput":
            # No `locations` at all — the path only appears in rawInput.
            u = tool_update("tool_call", "tool-3", "read", "pending")
            if os.environ.get("FAKE_ANCESTOR"):
                # A search rooted at the PARENT of the deny path (an ancestor).
                u["params"]["update"]["rawInput"] = {"pattern": "refresh_token", "path": os.path.dirname(os.environ["FAKE_DENY"])}
            else:
                u["params"]["update"]["rawInput"] = {"args": ["--file", "~/.kimi-code/credentials/kimi-code.json"]}
            emit(u)
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
        elif scenario == "fenced":
            answer = 'Here is the review:\n```json\n{"findings":[]}\n```\nDone.'
        elif scenario == "fenced-decoy":
            real = ('{"findings":[{"file":"a.py","line":1,"severity":"minor","summary":"real",'
                    '"failure_scenario":"x","confidence":"low","recommendation":"y"}]}')
            answer = ('The diff quotes this:\n```json\n{"findings":[]}\n```\n'
                      'My answer:\n```json\n' + real + '\n```\n')
        elif scenario == "prose-wrapped":
            answer = 'Summary first. {"findings":[]} That is all.'
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
        if scenario == "tools-event" and os.environ.get("FAKE_TOOL_STAGE") == "before-final":
            # Even a fully buffered valid empty findings object cannot hide a
            # tool event emitted immediately before the final RPC response.
            emit(json.loads(os.environ["FAKE_TOOL_EVENT"]))
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
                   env_extra: dict | None = None, prompt_bytes: bytes | None = None,
                   effort: str = "max", make_symlink: bool = False,
                   metrics_file: Path | None = None, tools: str | None = None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        fake = root / "kimi"
        fake.write_text(_FAKE_KIMI, encoding="utf-8")
        fake.chmod(0o755)
        prompt = root / "prompt.txt"
        if prompt_bytes is None:
            prompt.write_text("PROMPT_SENTINEL_7f0ac9\n", encoding="utf-8")
        else:
            prompt.write_bytes(prompt_bytes)
        log = root / "fake.log"
        (root / ".kimi-code" / "credentials").mkdir(parents=True, exist_ok=True)
        (root / ".kimi-code" / "credentials" / "kimi-code.json").write_text("{}")
        if make_symlink:
            os.symlink(root / ".kimi-code" / "credentials" / "kimi-code.json", root / "leak.json")
        env = os.environ.copy()
        env.update({
            "FAKE_SCENARIO": scenario,
            "FAKE_DENY": str(root / ".kimi-code"),
            "HOME": str(root),   # so `~/.kimi-code/...` in a tool input resolves under the deny path
            "FAKE_LOG": str(log),
        })
        if env_extra:
            env.update(env_extra)
        if metrics_file is not None:
            env["FAKE_METRICS"] = str(metrics_file)
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--prompt-file", str(prompt),
                "--schema", str(schema),
                "--cwd", str(root),
                "--model", MODEL,
                "--effort", effort,
                "--kimi-bin", str(fake),
                "--deny-path", str(root / ".kimi-code"),
            ] + (["--metrics-file", str(metrics_file)] if metrics_file is not None else [])
              + (["--tools", tools] if tools is not None else []),
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        records = []
        if log.exists():
            records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return result, records

    def run_measured(self, scenario="valid", **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "metrics.json"
        result, records = self.run_helper(scenario, metrics_file=path, **kwargs)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"tool_calls", "complete"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(path.parent.iterdir()), [path], "temporary sidecar leaked")
        return result, records, payload

    def test_metrics_deduplicate_mixed_events_and_preserve_stdout(self):
        plain, _ = self.run_helper("metrics-mixed")
        result, records, metrics = self.run_measured("metrics-mixed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(metrics, {"tool_calls": 4, "complete": True})
        self.assertEqual(result.stdout, plain.stdout)
        self.assertEqual(result.stdout, '{"findings":[]}\n')
        self.assertEqual(result.stderr, plain.stderr)
        self.assertEqual([r["metrics_progress"] for r in records if "metrics_progress" in r], [
            {"tool_calls": 1, "complete": False},
            {"tool_calls": 2, "complete": False},
            {"tool_calls": 3, "complete": False},
        ])

    def test_metrics_zero_is_measured_not_missing(self):
        result, _, metrics = self.run_measured()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(metrics, {"tool_calls": 0, "complete": True})

    def test_metrics_keep_partial_attempts_on_failures(self):
        for scenario, count, rc in (("metrics-mixed-error", 4, 10),
                                    ("unsafe-in-progress", 1, 13),
                                    ("orphan-completed-update", 1, 13),
                                    ("exec-rewrite", 1, 13),
                                    ("read-credentials", 1, 13)):
            with self.subTest(scenario=scenario):
                result, _, metrics = self.run_measured(scenario)
                self.assertEqual(result.returncode, rc, result.stderr)
                self.assertEqual(metrics, {"tool_calls": count, "complete": False})
                self.assertEqual(result.stdout, "")

    def test_metrics_count_rejected_and_streamed_attempts_once(self):
        for scenario in ("permission", "exec-snapshots", "exec-permission"):
            with self.subTest(scenario=scenario):
                result, _, metrics = self.run_measured(scenario)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(metrics, {"tool_calls": 1, "complete": True})

    def test_metrics_invalid_ids_are_unknown_not_zero(self):
        for scenario, rc in (("metrics-invalid-permission-id", 0),
                             ("metrics-invalid-notification-id", 13)):
            with self.subTest(scenario=scenario):
                result, _, metrics = self.run_measured(scenario)
                self.assertEqual(result.returncode, rc, result.stderr)
                self.assertEqual(metrics, {"tool_calls": None, "complete": False})

    def test_metrics_survive_response_schema_rejection(self):
        result, _, metrics = self.run_measured("wrong-shape")
        self.assertEqual(result.returncode, 11)
        self.assertEqual(metrics, {"tool_calls": 0, "complete": True})
        self.assertEqual(result.stdout, "")

    def test_metrics_before_prompt_are_unknown(self):
        result, _, metrics = self.run_measured("model-missing")
        self.assertEqual(result.returncode, 12)
        self.assertEqual(metrics, {"tool_calls": None, "complete": False})
        with tempfile.TemporaryDirectory() as td:
            schema = Path(td) / "schema.json"
            schema.write_text('{"type":"object","patternProperties":{}}')
            result, records, metrics = self.run_measured(schema=schema)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(records, [])
        self.assertEqual(metrics, {"tool_calls": None, "complete": False})

    def test_metrics_write_failure_does_not_change_findings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "existing.txt"
            target.write_text("must remain unchanged")
            link = root / "metrics.json"
            link.symlink_to(target)
            for path in (link, root / "missing" / "metrics.json"):
                with self.subTest(path=path):
                    result, _ = self.run_helper(metrics_file=path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, '{"findings":[]}\n')
                    self.assertEqual(result.stderr.count("metrics unavailable"), 1)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), "must remain unchanged")

    @staticmethod
    def tool_event(update_type="tool_call", tool_id="no-tools-1", kind="read", status="pending"):
        return {"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": "fake-session", "update": {
                "sessionUpdate": update_type, "toolCallId": tool_id, "kind": kind, "status": status,
            },
        }}

    def assert_tools_event_rejected(self, event, *, stage="session/prompt", count=1):
        result, _, metrics = self.run_measured("tools-event", tools="false", env_extra={
            "FAKE_TOOL_EVENT": json.dumps(event), "FAKE_TOOL_STAGE": stage,
        })
        expected_rc = 13 if stage in {"session/prompt", "before-final"} else 12
        self.assertEqual(result.returncode, expected_rc, result.stderr)
        self.assertIn("tool use is disabled (--tools false)", result.stderr)
        self.assertEqual(result.stdout, "", "empty valid final findings hid a tool violation")
        self.assertEqual(metrics, {"tool_calls": count, "complete": False})

    def test_no_tools_rejects_every_kind_status_and_update(self):
        # No safe kind, pending/failed status or update-only orphan exemption.
        for update_type in ("tool_call", "tool_call_update"):
            for kind in ("read", "search", "fetch", "think", "execute", "edit", "teleport", None):
                for status in ("pending", "in_progress", "completed", "failed", "cancelled", None):
                    with self.subTest(update_type=update_type, kind=kind, status=status):
                        self.assert_tools_event_rejected(self.tool_event(update_type, kind=kind, status=status))

    def test_no_tools_rejects_malformed_events_without_claiming_zero(self):
        for update_type in ("tool_call", "tool_call_update"):
            for tool_id in (None, "", "  ", 1, [], {}):
                with self.subTest(update_type=update_type, tool_id=tool_id):
                    self.assert_tools_event_rejected(self.tool_event(update_type, tool_id=tool_id), count=None)
        for params in (None, [], {}, {"update": None}, {"update": []}, {"update": {}},
                       {"update": {"sessionUpdate": []}}):
            with self.subTest(params=params):
                self.assert_tools_event_rejected(
                    {"jsonrpc": "2.0", "method": "session/update", "params": params}, count=None)

    def test_no_tools_rejects_permission_only_even_allowlisted_shell(self):
        for kind in ("read", "execute", None):
            for options in ([], [{"kind": "allow_once", "optionId": "allow"}],
                            [{"kind": "reject_once", "optionId": "reject"}],
                            [{"kind": [], "optionId": "bad"}], {"malformed": True}):
                event = {"jsonrpc": "2.0", "id": 800, "method": "session/request_permission", "params": {
                    "sessionId": "fake-session", "options": options,
                    "toolCall": {"toolCallId": "permission-only", "kind": kind,
                                 "rawInput": {"command": "git log -1"}},
                }}
                with self.subTest(kind=kind, options=options):
                    self.assert_tools_event_rejected(event)
        for params in (None, [], {}, {"toolCall": []}, {"toolCall": {"toolCallId": []}}):
            with self.subTest(params=params):
                self.assert_tools_event_rejected({"jsonrpc": "2.0", "id": 800,
                    "method": "session/request_permission", "params": params}, count=None)

    def test_no_tools_rejects_tool_events_with_wrong_rpc_envelopes(self):
        notification_with_id = dict(self.tool_event(), id=800)
        permission_without_id = {"jsonrpc": "2.0", "method": "session/request_permission",
                                 "params": {"toolCall": {"toolCallId": "p"}}}
        for event in (notification_with_id, permission_without_id):
            with self.subTest(event=event):
                self.assert_tools_event_rejected(event)

    def test_no_tools_applies_before_collect_output_and_before_final_response(self):
        events = [self.tool_event(), self.tool_event("tool_call_update"), {
            "jsonrpc": "2.0", "id": 800, "method": "session/request_permission",
            "params": {"toolCall": {"toolCallId": "early-permission"}, "options": []},
        }]
        for stage in ("initialize", "session/new", "session/set_config_option", "before-final"):
            for event in events:
                with self.subTest(stage=stage, event=event):
                    self.assert_tools_event_rejected(event, stage=stage)

    def test_no_tools_sends_rejection_and_ignores_previous_approval_exemptions(self):
        # Capture the client's writes directly: an immediately killed fake peer
        # need not get CPU time to log the rejection even though it was flushed.
        import importlib.util
        spec = importlib.util.spec_from_file_location("kimi_acp_no_tools", HELPER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for options, outcome in (([{"kind": "reject_once", "optionId": "reject"}],
                                  {"outcome": "selected", "optionId": "reject"}),
                                 ([{"kind": "allow_once", "optionId": "allow"}],
                                  {"outcome": "cancelled"})):
            client = module.AcpClient("unused", tools=False)
            sent = []
            client._send = sent.append
            with self.assertRaises(module.ProtocolError):
                client._handle_server_request({"id": 800, "method": "session/request_permission", "params": {
                    "options": options, "toolCall": {"toolCallId": "p", "kind": "execute",
                                                     "rawInput": {"command": "git log -1"}},
                }})
            self.assertEqual(sent, [{"jsonrpc": "2.0", "id": 800, "result": {"outcome": outcome}}])
            self.assertEqual(client.metrics.ids, {"p"})
        for exemption in ("allowed_exec_ids", "rejected_tool_ids"):
            client = module.AcpClient("unused", tools=False)
            getattr(client, exemption).add("no-tools-1")
            with self.assertRaises(module.ProtocolError):
                client._handle_notification(self.tool_event("tool_call_update", kind="execute", status="failed"))
            self.assertEqual(client.metrics.ids, {"no-tools-1"})

    def test_no_tools_accepts_tool_free_output_without_server_mode_changes(self):
        plain, plain_records = self.run_helper()
        result, records, metrics = self.run_measured(tools="false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, plain.stdout)
        self.assertEqual(metrics, {"tool_calls": 0, "complete": True})
        self.assertEqual(records[0]["argv"], ["acp"])
        # Only the same model/thinking/default-mode configuration is sent.
        self.assertEqual([r["set"] for r in records if "set" in r],
                         [r["set"] for r in plain_records if "set" in r])

    def test_tools_true_preserves_default_policy_and_has_no_positive_budget_cap(self):
        for scenario in ("exec-snapshots", "exec-permission", "permission", "many-tools"):
            with self.subTest(scenario=scenario):
                plain, _ = self.run_helper(scenario)
                result, _, metrics = self.run_measured(scenario, tools="true")
                self.assertEqual(result.returncode, plain.returncode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, plain.stdout)
                self.assertEqual(metrics, {"tool_calls": 12 if scenario == "many-tools" else 1,
                                           "complete": True})

    def test_tools_cli_values_are_strict(self):
        for value in ("False", "0", "1", "yes", "", "TRUE"):
            with self.subTest(value=value):
                result, records = self.run_helper(tools=value)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid choice", result.stderr)
                self.assertEqual(records, [])
                self.assertEqual(result.stdout, "")

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

    def test_execute_without_a_visible_command_fails_at_end_of_turn(self):
        result, _ = self.run_helper("exec-no-command")
        self.assertEqual(result.returncode, 13)
        self.assertIn("never rejected: execute:tool-7x", result.stderr)

    def test_streamed_argument_snapshots_are_vetted_and_approved(self):
        result, records = self.run_helper("exec-snapshots")
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = next(r["permission_response"] for r in records if "permission_response" in r)
        self.assertEqual(reply["result"]["outcome"]["optionId"], "allow")

    def test_streamed_disallowed_command_is_rejected_not_fatal(self):
        result, records = self.run_helper("exec-snapshots", env_extra={"FAKE_COMMAND": "git push --force"})
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = next(r["permission_response"] for r in records if "permission_response" in r)
        self.assertEqual(reply["result"]["outcome"]["optionId"], "reject")

    def test_changed_rawinput_under_a_vetted_id_is_re_vetted(self):
        result, _ = self.run_helper("exec-rewrite")
        self.assertEqual(result.returncode, 13)
        self.assertIn("outside the read-only allowlist", result.stderr)

    def test_last_fenced_object_wins_over_a_quoted_decoy(self):
        result, _ = self.run_helper("fenced-decoy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["findings"][0]["summary"], "real")

    def test_read_only_shell_command_is_allowed(self):
        result, _ = self.run_helper("exec-allowed")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shell_command_outside_the_allowlist_aborts(self):
        result, records = self.run_helper("exec-denied")
        self.assertEqual(result.returncode, 13)
        self.assertIn("outside the read-only allowlist", result.stderr)
        self.assertIn("redirection", result.stderr)
        self.assertFalse(any("prompt_done" in r for r in records), "client waited for end_turn")

    def test_shell_command_rooted_at_an_ancestor_of_the_store_aborts(self):
        # `grep -r` from `/` walks into the linked credentials: the ancestor
        # rule fires on the path token even though the program is allowlisted.
        result, _ = self.run_helper("exec-allowed", env_extra={"FAKE_COMMAND": "grep -r refresh_token /"})
        self.assertEqual(result.returncode, 13)
        self.assertIn("denied path", result.stderr)

    def test_search_rooted_at_an_ancestor_of_the_store_aborts(self):
        result, _ = self.run_helper("read-credentials-rawinput",
                                    env_extra={"FAKE_ANCESTOR": "1"})
        self.assertEqual(result.returncode, 13)

    def test_allowlisted_command_is_approved_once_on_request(self):
        result, records = self.run_helper("exec-permission")
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = next(r["permission_response"] for r in records if "permission_response" in r)
        self.assertEqual(reply["result"]["outcome"], {"outcome": "selected", "optionId": "allow"})

    def test_non_allowlisted_command_is_rejected_on_request(self):
        result, records = self.run_helper("exec-permission", env_extra={"FAKE_COMMAND": "rm -rf build"})
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = next(r["permission_response"] for r in records if "permission_response" in r)
        self.assertEqual(reply["result"]["outcome"]["optionId"], "reject")

    def test_read_only_command_policy(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("kimi_acp", HELPER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        allowed = [
            "git log --oneline -20", "git show HEAD~1 -- a.py", "git blame -L 3,9 a.py",
            "git diff main...HEAD --stat", "git branch -a", "git stash list",
            "git worktree list",
            # read-only flags that a substring match had mistaken for exec options
            "git log --pretty=format:%h -n 20", "git diff --no-prefix", "grep --binary-files=text x f",
            "git grep -o TODO", "git log -c", "git grep -c x",
            "grep -rn TODO src | head -50", "rg -n 'def x' src", "find . -name '*.py'",
            "ls -la plugins", "cat README.md", "wc -l a.py",
        ]
        allowed += [
            # quoted pipes are data, not chaining
            "grep -E 'foo|bar' x", 'git log --format="%h|%s" -5', 'rg "a|b" src',
        ]
        rejected = [
            # exec-capable options of allowlisted programs (review round 2, #1)
            "git grep --open-files-in-pager=curl x", "git grep -O curl x",
            "sort --compress-program=sh f", "rg --hostname-bin=x y", "rg --pre curl x",
            "git log --foo=/usr/bin/curl",
            # listing flag next to a mutating one (#2)
            "git branch -a -D topic", "git tag -l -d v1", "git remote -v add o u",
            "git config --list --edit", "git stash list drop",
            # remote/config print URLs and helpers that can carry tokens
            "git remote -v", "git config --list", "git config --get user.name",
            "git branch foo", "git tag v1", "git config user.name x", "git remote add o u",
            "git -c core.pager=evil log", "git log --output=/tmp/x", "git log > /tmp/x",
            "ls; rm -rf /", "echo hi && rm x", "echo hi || rm x", "cat $(echo x)",
            "cat `echo x`", "find . -exec rm {} \\;", "rg --pre evil x", "tail -f log",
            "sort -o out in", "awk 1 f", "sed -i s/a/b/ f", "./git log", "FOO=1 git log",
            "bash -c ls", "xargs rm", "python3 x.py", "", "x" * 3000, "cat a |& rm b",
        ]
        for command in allowed:
            self.assertTrue(module._read_only_command(command)[0], command)
        for command in rejected:
            self.assertFalse(module._read_only_command(command)[0], command)
        self.assertEqual(module._command_of({"command": "git log"}), "git log")
        # deny scan: an apostrophe is not a deny hit, a glob in a path-like token is
        client = module.AcpClient.__new__(module.AcpClient)
        client.deny_paths = ["/tmp/swarm-deny-probe"]; client.cwd = "/"
        self.assertFalse(client._denied_tokens("Fetch Moonshot's ACP docs"))
        self.assertFalse(client._denied_tokens("what's the ACP spec say"))
        self.assertTrue(client._denied("/Users/*/.kimi-code/credentials/kimi-code.json"))
        self.assertFalse(client._denied("*.py"))
        self.assertEqual(module._command_of({"cmd": ["git", "log"]}), "git log")
        self.assertIsNone(module._command_of({"path": "x"}))

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
        self.assertIn("not a JSON object", result.stderr)
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
        self.assertIn("kind=edit status=in_progress", result.stderr)
        # The fake logs prompt_done only after its end_turn response; a client
        # that waited for end_turn would have let it get there.
        self.assertFalse(any("prompt_done" in r for r in records), "client waited for end_turn")

    def test_missing_kind_is_not_laundered_by_a_later_update(self):
        result, _ = self.run_helper("kind-late")
        self.assertEqual(result.returncode, 13)
        self.assertIn("no known kind", result.stderr)

    def test_read_of_the_store_via_rawinput_only_aborts(self):
        result, _ = self.run_helper("read-credentials-rawinput")
        self.assertEqual(result.returncode, 13)
        self.assertIn("denied path", result.stderr)

    def test_undecodable_prompt_bytes_do_not_abort(self):
        result, records = self.run_helper("valid", prompt_bytes=b"diff\n\xe9\xff\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_matching_option_is_not_re_set(self):
        # The fake starts with thinking=high; requesting "high" must not send a
        # set_config_option for it (model and mode still change).
        result, records = self.run_helper("valid", effort="high")
        self.assertEqual(result.returncode, 0, result.stderr)
        sets = [r["set"] for r in records if "set" in r]
        self.assertNotIn("thinking", [x.get("configId") for x in sets])
        self.assertIn("model", [x.get("configId") for x in sets])

    def test_relative_path_into_the_store_aborts(self):
        result, _ = self.run_helper("read-credentials-relative")
        self.assertEqual(result.returncode, 13)
        self.assertIn("denied path", result.stderr)

    def test_symlink_into_the_store_aborts(self):
        result, _ = self.run_helper("read-credentials-symlink", make_symlink=True)
        self.assertEqual(result.returncode, 13)
        self.assertIn("denied path", result.stderr)

    def test_peer_error_message_is_surfaced(self):
        result, _ = self.run_helper("prompt-error")
        self.assertEqual(result.returncode, 10)
        self.assertIn("5-hour usage limit", result.stderr)

    def test_fenced_and_prose_wrapped_json_are_accepted(self):
        for scenario in ("fenced", "prose-wrapped"):
            with self.subTest(scenario=scenario):
                result, _ = self.run_helper(scenario)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"findings": []})

    def test_response_flood_is_capped(self):
        result, _ = self.run_helper("flood")
        self.assertEqual(result.returncode, 13)
        self.assertIn("exceeded", result.stderr)

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
