#!/usr/bin/env python3
"""Hermetic Codex model/list peer tests: no installed CLI, network or credentials."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HELPER = Path(__file__).with_name("codex-models.py")

PEER = r'''
import json, os, signal, subprocess, sys, time
assert sys.argv[1:] == ["app-server", "--listen", "stdio://"]
case = os.environ["CASE"]
def send(value):
    print(json.dumps(value), flush=True)
def request(method):
    value = json.loads(sys.stdin.readline())
    assert value["method"] == method, value
    return value
def row(name="test-model", hidden=False):
    return {"id": name + "-picker", "model": name, "displayName": name,
            "hidden": hidden, "defaultReasoningEffort": "medium",
            "supportedReasoningEfforts": [
                {"reasoningEffort": e, "description": e}
                for e in ("low", "medium", "future-effort")]}
def descendants():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c",
        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])
    with open(os.environ["PIDS"], "w") as f:
        json.dump([os.getpid(), child.pid], f)
init = request("initialize")
assert init["params"]["clientInfo"]["name"] == "swarm_model_probe"
if case == "init-error":
    send({"id": init["id"], "error": {"message": "private-error-content"}})
    time.sleep(60)
if case == "init-hang":
    time.sleep(60)
send({"id": init["id"], "result": {"userAgent": "codex-cli/test"}})
assert request("initialized") == {"method": "initialized"}
page = 0
while True:
    req = request("model/list")
    assert req["params"]["includeHidden"] is True
    assert req["params"]["limit"] == 100
    assert req["params"]["cursor"] == (None if page == 0 else str(page))
    if case in ("timeout", "term", "success-child", "leader-exit") and page == 0:
        descendants()
        if case == "leader-exit":
            sys.exit(0)
        if case in ("timeout", "term"):
            time.sleep(60)
    if case == "noise":
        sys.stdout.write("not json\n"); sys.stdout.flush(); time.sleep(60)
    if case == "truncated":
        sys.stdout.write('{"id":'); sys.stdout.flush(); sys.exit(0)
    if case == "oversized":
        sys.stdout.write("x" * (8 * 1024 * 1024 + 1)); sys.stdout.flush(); time.sleep(60)
    if case == "notification-flood":
        while True:
            send({"method": "notice", "params": {}})
    if case == "partial-timeout" and page == 1:
        time.sleep(60)
    if case == "shared-deadline":
        time.sleep(0.22)
    model = row("hidden-model" if page else "test-model", hidden=bool(page))
    result = {"data": [model], "nextCursor": None}
    if case in ("pages", "partial-timeout", "shared-deadline", "duplicate") and page == 0:
        result["nextCursor"] = "1"
    if case == "duplicate" and page:
        result["data"] = [row()]
    if case == "cycle":
        result["data"] = [row(str(page))]
        result["nextCursor"] = "1"
    if case == "many-pages":
        result["data"] = [row(str(page))]
        result["nextCursor"] = str(page + 1)
    if case == "empty":
        result["data"] = []
    if case == "empty-page":
        result = {"data": [], "nextCursor": "1"}
    if case == "missing-cursor":
        del result["nextCursor"]
    if case == "bad-cursor":
        result["nextCursor"] = 1
    if case == "bad-model":
        del model["model"]
    if case == "bad-effort":
        model["supportedReasoningEfforts"] = [{"reasoningEffort": None}]
    if case == "bad-default":
        model["defaultReasoningEffort"] = "absent"
    if case == "bad-visibility":
        model["hidden"] = "false"
    if case == "error":
        send({"id": req["id"], "error": {"message": "private-error-content"}})
        time.sleep(60)
    if case == "server-request":
        send({"id": req["id"], "method": "account/login/start", "params": {}})
        time.sleep(60)
    if case == "wrong-id":
        req["id"] += 100
    if case == "duplicate-key":
        sys.stdout.write('{"id":3,"id":2,"result":{}}\n'); sys.stdout.flush()
        time.sleep(60)
    if case == "non-json-number":
        sys.stdout.write('{"id":2,"result":{"data":[],"nextCursor":null,"extra":NaN}}\n')
        sys.stdout.flush(); time.sleep(60)
    send({"method": "notice", "params": {"ignored": True}})
    # Split JSONL across writes to exercise framing, not one-write assumptions.
    payload = json.dumps({"id": req["id"], "result": result}) + "\n"
    sys.stdout.write(payload[:10]); sys.stdout.flush()
    sys.stdout.write(payload[10:]); sys.stdout.flush()
    page += 1
    if result.get("nextCursor") is None:
        time.sleep(60)
'''


class CodexModelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.peer = self.root / "codex"
        self.peer.write_text("#!" + sys.executable + "\n" + PEER)
        self.peer.chmod(0o700)
        self.pids = self.root / "pids.json"

    def command(self, timeout=2):
        return [sys.executable, str(HELPER), "--codex", str(self.peer),
                "--timeout", str(timeout)]

    def env(self, case):
        # Do not inherit any provider credentials, config paths or user home.
        return {"HOME": str(self.root), "CODEX_HOME": str(self.root / "codex-home"),
                "PATH": os.defpath, "CASE": case, "PIDS": str(self.pids)}

    def run_peer(self, case, timeout=2):
        return subprocess.run(self.command(timeout), env=self.env(case),
                              capture_output=True, text=True, timeout=5)

    def assert_failed(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("codex-models:", result.stderr)
        self.assertNotIn("private-error-content", result.stderr)

    def test_complete_catalog_keeps_exact_model_and_effort_names(self):
        result = self.run_peer("pages")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIs(payload["complete"], True)
        self.assertIs(payload["authoritative"], False)
        self.assertTrue(payload["reason"])
        self.assertEqual([m["model"] for m in payload["models"]],
                         ["test-model", "hidden-model"])
        self.assertEqual(payload["models"][0]["id"], "test-model-picker")
        self.assertTrue(payload["models"][1]["hidden"])
        self.assertEqual(payload["models"][0]["supportedReasoningEfforts"][-1]["reasoningEffort"],
                         "future-effort")

    def test_empty_catalog_is_complete_but_not_authoritative(self):
        result = self.run_peer("empty")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["models"], [])
        self.assertIs(payload["complete"], True)
        self.assertIs(payload["authoritative"], False)

    def test_invalid_responses_never_publish_partial_catalog(self):
        for case in ("init-error", "noise", "truncated", "duplicate", "cycle",
                     "many-pages", "empty-page", "missing-cursor", "bad-cursor",
                     "bad-model", "bad-effort", "bad-default", "bad-visibility",
                     "error", "server-request", "wrong-id", "duplicate-key",
                     "non-json-number", "oversized"):
            with self.subTest(case=case):
                self.assert_failed(self.run_peer(case))

    def test_timeout_bounds_handshake_and_entire_pagination(self):
        for case in ("init-hang", "partial-timeout", "shared-deadline", "notification-flood"):
            with self.subTest(case=case):
                start = time.monotonic()
                self.assert_failed(self.run_peer(case, timeout=0.35))
                self.assertLess(time.monotonic() - start, 2)

    def assert_descendants_stopped(self):
        pids = json.loads(self.pids.read_text())
        def alive(pid):
            try:
                # Linux may retain an orphan zombie until PID 1 reaps it.
                stat = Path(f"/proc/{pid}/stat")
                if stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"):
                    return False
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
        deadline = time.monotonic() + 2
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(any(alive(pid) for pid in pids), pids)

    def test_group_cleanup_on_success_timeout_and_orphaned_pipe(self):
        for case in ("success-child", "timeout", "leader-exit"):
            with self.subTest(case=case):
                result = self.run_peer(case, timeout=0.4)
                if case == "success-child":
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assert_failed(result)
                self.assert_descendants_stopped()

    def test_parent_term_cleans_sigterm_ignoring_descendant_group(self):
        proc = subprocess.Popen(self.command(), env=self.env("term"),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 2
            while not self.pids.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.pids.exists())
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=2)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(stdout, "")
            self.assertIn("interrupted", stderr)
            self.assert_descendants_stopped()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_missing_cli_and_invalid_timeouts(self):
        self.peer.unlink()
        self.assert_failed(self.run_peer("unused"))
        for timeout in (0, -1, 21, "nan", "inf", "invalid"):
            with self.subTest(timeout=timeout):
                result = self.run_peer("unused", timeout=timeout)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
