#!/usr/bin/env python3
"""grok streaming-json capture: envelope identity, telemetry, transcript, degrade.

The stream exists to make grok's runtime observable WITHOUT changing what a
review returns, so the load-bearing assertions are:
  - the findings a stream run prints are byte-identical to the json-envelope run;
  - the telemetry record carries the parsed turn/tool/usage fields;
  - a CLI without streaming-json (or an inconclusive probe) runs today's path;
  - a failed voice keeps its transcript and names it; a good one leaves nothing.
The fixture mirrors the event shapes grok 1.0.46 emitted in a real probe
(2026-10-19): `usage` closes each model call, `end` carries the envelope fields.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENTS = HERE / "agents.sh"
HELPER = HERE / "grok-stream.py"
SCHEMA = HERE / "schema" / "finding.schema.json"
REPO = HERE.parents[2]

FINDINGS = {"findings": [{"file": "a.sh", "line": 3, "summary": "[correctness] x", "severity": "major"}]}
END = {"type": "end", "stopReason": "end_turn", "sessionId": "s", "requestId": "r",
       "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0,
                 "output_tokens": 20, "reasoning_tokens": 10, "total_tokens": 180},
       "num_turns": 3, "total_cost_usd": 0.0123, "modelUsage": {}, "structuredOutput": FINDINGS}
EVENTS = [
    {"type": "available_commands", "tools": ["read_file"], "commands": []},
    {"type": "thought", "data": "look"},
    {"type": "usage", "usage": {"input_tokens": 1}},
    {"type": "tool_call", "toolCallId": "c1", "toolName": "read_file", "kind": "read", "status": "pending"},
    {"type": "tool_call_update", "toolCallId": "c1", "status": "completed"},
    {"type": "tool_call", "toolCallId": "c2", "toolName": "run_terminal_command", "kind": "execute", "status": "pending"},
    {"type": "tool_call_update", "toolCallId": "c2", "status": "failed"},
    {"type": "tool_call", "toolCallId": "c3", "toolName": "Web search:", "kind": "search", "status": "in_progress"},
    {"type": "tool_call", "toolCallId": "c3", "toolName": "Web search:", "kind": "search", "status": "in_progress"},
    {"type": "tool_call", "toolCallId": "c4", "toolName": "grep", "kind": "search", "status": "pending"},
    {"type": "usage", "usage": {"input_tokens": 1}},
    {"type": "text", "data": "{\"findings\":[]}"},
    {"type": "usage", "usage": {"input_tokens": 1}},
    END,
]
# The json envelope the same call returns without --output-format.
ENVELOPE = {"text": "{}", **{k: v for k, v in END.items() if k != "type"}}


def ndjson(events):
    return "".join(json.dumps(e) + "\n" for e in events)


def helper(*args, stdin=""):
    return subprocess.run([sys.executable, str(HELPER), *args], input=stdin,
                          capture_output=True, text=True, timeout=30)


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def stream(self, events, raw_extra=""):
        path = self.dir / "s.ndjson"
        path.write_text(ndjson(events) + raw_extra)
        return path

    def metrics(self, *args):
        r = helper("metrics", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        first, second = r.stdout.splitlines()
        return first, json.loads(second)

    def test_stamp_copies_bytes_and_times_every_line(self):
        out, times = self.dir / "o", self.dir / "t"
        data = ndjson(EVENTS) + "not json but kept\n"
        r = helper("stamp", str(out), str(times), stdin=data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_text(), data)
        stamps = [float(x) for x in times.read_text().split()]
        self.assertEqual(len(stamps), len(EVENTS) + 1)
        self.assertEqual(stamps, sorted(stamps))

    def test_envelope_is_the_end_event_else_the_error_event(self):
        r = helper("envelope", str(self.stream(EVENTS)))
        self.assertEqual(json.loads(r.stdout), END)
        err = {"type": "error", "message": "unknown model id"}
        r = helper("envelope", str(self.stream(EVENTS[:3] + [err])))
        self.assertEqual(json.loads(r.stdout), err)

    def test_no_end_event_fails_without_echoing_content(self):
        r = helper("envelope", str(self.stream(EVENTS[:5], "SECRET-ish raw line\n")))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("no end event (5 events", r.stderr)
        self.assertNotIn("SECRET", r.stderr)

    def test_stream_metrics(self):
        path = self.stream(EVENTS)
        times = self.dir / "t"
        times.write_text("".join(f"{t}\n" for t in (0.1, 0.2, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.5, 2.6,
                                                     10.0, 10.1, 12.5, 12.6)))
        first, m = self.metrics("--stream", str(path), "--times", str(times))
        self.assertEqual(first, "4 true")
        self.assertEqual(m["source"], "stream")
        self.assertEqual(m["num_turns"], 3)
        self.assertEqual(m["tool_calls"], 4, "a repeated tool_call id counts once")
        self.assertEqual(m["tool_failed"], 1)
        self.assertEqual(m["tools"], {"read_file": 1, "run_terminal_command": 1, "Web_search": 1, "grep": 1})
        # web search shares ACP kind `search` with grep: the name decides.
        self.assertEqual(m["tool_kinds"], {"read": 2, "shell": 1, "web": 1, "other": 0})
        self.assertEqual(m["turn_s"], {"min": 2.0, "median": 2.5, "max": 8.0})
        self.assertEqual(m["stop_reason"], "end_turn")
        self.assertTrue(m["structured_output"])
        self.assertEqual(m["findings"], 1)
        self.assertEqual(m["usage"], {"input_tokens": 100, "cache_read_tokens": 50, "output_tokens": 20,
                                      "reasoning_tokens": 10, "total_tokens": 180})
        self.assertEqual(m["cost_usd"], 0.0123)

    def test_latency_is_unavailable_rather_than_invented(self):
        path = self.stream(EVENTS)
        _, m = self.metrics("--stream", str(path), "--no-latency")
        self.assertIsNone(m["turn_s"])
        self.assertEqual(m["turn_s_unavailable"], "buffered")
        _, m = self.metrics("--stream", str(path), "--times", str(self.dir / "missing"))
        self.assertEqual(m["turn_s_unavailable"], "no-times")

    def test_cut_stream_counts_turns_from_usage_and_says_incomplete(self):
        first, m = self.metrics("--stream", str(self.stream(EVENTS[:11], "{broken\n")))
        self.assertEqual(first, "4 false")
        self.assertFalse(m["complete"])
        self.assertEqual(m["num_turns"], 2)
        self.assertFalse(m["structured_output"])
        self.assertIsNone(m["findings"])
        self.assertEqual(m["malformed"], 1)

    def test_untrusted_names_are_reduced_and_bounded(self):
        evil = [{"type": "tool_call", "toolCallId": f"x{i}", "toolName": f'a"b\\n{i}$(rm)é' + "z" * 99}
                for i in range(40)]
        r = helper("metrics", "--stream", str(self.stream(evil + [END])))
        line = r.stdout.splitlines()[1]
        self.assertTrue(line.isascii() and line.isprintable())
        m = json.loads(line)
        self.assertLessEqual(len(m["tools"]), 17)
        self.assertEqual(m["tools"]["other"], 40 - 16)
        for name in m["tools"]:
            self.assertRegex(name, r"\A[A-Za-z0-9_.:-]{1,48}\Z")
        self.assertLess(len(line), 4096, "must fit the adapter's embed bound")

    def test_envelope_metrics_for_the_degraded_path(self):
        first, m = (lambda r: (r.stdout.splitlines()[0], json.loads(r.stdout.splitlines()[1])))(
            helper("metrics", "--envelope", "-", stdin=json.dumps(ENVELOPE)))
        self.assertEqual(first, "null false", "no tool observation without a stream")
        self.assertEqual((m["source"], m["num_turns"], m["findings"], m["stop_reason"]),
                         ("envelope", 3, 1, "end_turn"))
        self.assertNotIn("tools", m)
        _, m = (lambda r: (None, json.loads(r.stdout.splitlines()[1])))(
            helper("metrics", "--envelope", "-", stdin="garbage"))
        self.assertFalse(m["complete"])

    def test_usage_errors(self):
        for args in ([], ["metrics"], ["metrics", "--stream", "a", "--envelope", "-"], ["stamp", "a"], ["nope"]):
            self.assertEqual(helper(*args).returncode, 2, args)


# Stubs shared with test_sandbox_deny's argv harness: no real CLI, jail or
# network. `sandboxed` records argv and plays the fixture for the mode asked.
PRELUDE = r'''
_jail_available() { return 0; }
_grok_models_done=1; _grok_models=grok-4.7
_grok_compat() { printf 'compat=ok\nsource=cache\n'; }
_probe_or_bare() { return 98; }
_assert_prompt_readable_in_jail() { return 0; }
_grok_has_prompt_file() { _grok_stream_json="$STREAM_SUPPORT"; return 0; }
sandboxed() {
  shift
  printf '%s\n' "$@" > "$ARGV"
  case " $* " in
    *" streaming-json "*) cat "$STREAM_FIXTURE" ;;
    *) cat "$JSON_FIXTURE" ;;
  esac
  return "${FAKE_RC:-0}"
}
'''


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.run_dir = self.dir / "run"
        self.run_dir.mkdir()
        self.prompt = self.run_dir / "external-prompt.txt"
        self.prompt.write_text("prompt text\n")
        self.auth = self.dir / "auth.json"
        self.auth.write_text('{"access_token":"tok"}\n')
        (self.dir / "stream.ndjson").write_text(ndjson(EVENTS))
        (self.dir / "envelope.json").write_text(json.dumps(ENVELOPE, indent=2))

    def tearDown(self):
        self.tmp.cleanup()

    def run_grok(self, support="yes", rc=0, stream=None, env_extra=None):
        if stream is not None:
            (self.dir / "stream.ndjson").write_text(stream)
        telemetry = self.dir / "telemetry.jsonl"
        if telemetry.exists():
            telemetry.unlink()
        harness = ("set -euo pipefail\nsource '%s'\n%s\n"
                   'TELEMETRY_FILE="$TFILE"; TELEMETRY_BACKEND=grok; TELEMETRY_UNIT=breakage; '
                   'TELEMETRY_START="$(date +%%s)"; _adapter_timeout=30; _enforced_wall=30\n'
                   '( trap cleanup EXIT; run_grok "$PROMPT" low "" "%s" )\n') % (AGENTS, PRELUDE, SCHEMA)
        env = {**os.environ, "ARGV": str(self.dir / "argv"), "PROMPT": str(self.prompt),
               "TFILE": str(telemetry), "GROK_AUTH_FILE": str(self.auth),
               "STREAM_FIXTURE": str(self.dir / "stream.ndjson"),
               "JSON_FIXTURE": str(self.dir / "envelope.json"),
               "STREAM_SUPPORT": support, "FAKE_RC": str(rc), **(env_extra or {})}
        env.pop("SWARM_GROK_TRACE", None)
        r = subprocess.run(["bash", "-c", harness], cwd=str(REPO), env=env,
                           capture_output=True, text=True, timeout=60)
        # The first record is run_grok's own subshell; the harness shell's EXIT
        # trap appends a second, empty one.
        record = json.loads(telemetry.read_text().splitlines()[0]) if telemetry.exists() else None
        return r, (self.dir / "argv").read_text(), record

    def transcripts(self):
        return sorted(self.run_dir.glob("swarm-grok-transcript.*"))

    def test_stream_result_is_byte_identical_to_the_envelope_result(self):
        streamed, argv_s, _ = self.run_grok("yes")
        plain, argv_p, _ = self.run_grok("no")
        self.assertEqual(streamed.returncode, 0, streamed.stderr)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(streamed.stdout, plain.stdout)
        self.assertEqual(json.loads(streamed.stdout), FINDINGS)
        self.assertIn("streaming-json", argv_s.split("\n"))
        self.assertNotIn("--output-format", argv_p)
        self.assertNotIn("--max-turns", argv_s, "no turn cap: findings outrank speed")

    def test_stream_telemetry_is_recorded(self):
        r, _, rec = self.run_grok("yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((rec["tool_calls"], rec["tool_calls_complete"]), (4, True))
        g = rec["grok"]
        self.assertEqual(g["source"], "stream")
        self.assertEqual((g["num_turns"], g["findings"], g["stop_reason"]), (3, 1, "end_turn"))
        self.assertTrue(g["structured_output"])
        self.assertEqual(g["tool_kinds"], {"read": 2, "shell": 1, "web": 1, "other": 0})
        self.assertIn("min", g["turn_s"])
        self.assertEqual(g["usage"]["reasoning_tokens"], 10)

    def test_missing_stream_support_degrades_to_the_envelope_path(self):
        for support in ("no", ""):
            with self.subTest(support=support):
                r, argv, rec = self.run_grok(support)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("--output-format", argv)
                self.assertEqual(rec["grok"]["source"], "envelope")
                self.assertEqual(rec["grok"]["num_turns"], 3)
                self.assertIsNone(rec["tool_calls"])
                self.assertEqual(self.transcripts(), [])
                self.assertNotIn("transcript=", r.stderr)

    def test_success_leaves_no_transcript(self):
        r, _, _ = self.run_grok("yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.transcripts(), [])
        self.assertNotIn("transcript=", r.stderr)

    def test_timeout_keeps_and_names_the_transcript(self):
        cut = ndjson(EVENTS[:11])
        r, _, rec = self.run_grok("yes", rc=124, stream=cut)
        self.assertNotEqual(r.returncode, 0)
        kept = self.transcripts()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), cut)
        self.assertEqual(kept[0].stat().st_mode & 0o777, 0o600)
        self.assertIn(f"transcript={kept[0]}\n", r.stderr)
        self.assertTrue(rec["timed_out"])
        self.assertEqual((rec["grok"]["complete"], rec["grok"]["num_turns"]), (False, 2))
        self.assertEqual((rec["tool_calls"], rec["tool_calls_complete"]), (4, False))
        self.assertEqual(sorted(self.run_dir.glob("swarm-grok-times.*")), [])

    def test_prompt_directly_in_tmpdir_keeps_no_transcript(self):
        # A stdin prompt is the adapter's own scratch directly in $TMPDIR: no
        # run dir will ever remove a transcript left beside it.
        cut = ndjson(EVENTS[:11])
        r, _, rec = self.run_grok("yes", rc=124, stream=cut,
                                  env_extra={"TMPDIR": str(self.run_dir) + "/"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.transcripts(), [])
        self.assertNotIn("transcript=", r.stderr)
        self.assertEqual(rec["grok"]["num_turns"], 2, "telemetry still read the stream")

    def test_answer_without_structured_output_keeps_the_transcript(self):
        no_so = {k: v for k, v in END.items() if k != "structuredOutput"}
        r, _, rec = self.run_grok("yes", stream=ndjson(EVENTS[:-1] + [no_so]))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no structuredOutput", r.stderr)
        self.assertEqual(len(self.transcripts()), 1)
        self.assertEqual(rec["backend_rc"], 0)
        self.assertFalse(rec["grok"]["structured_output"])

    def test_inherited_grok_telemetry_cannot_forge_json(self):
        # Only grok-stream.py's json.dumps output is ever assigned. An inherited
        # variable must lose to the adapter's own init, and a value with control
        # characters or no object shape must never reach the line raw.
        tfile = self.dir / "t.jsonl"
        write = ('source %s\nTELEMETRY_FILE="$TFILE"; TELEMETRY_START="$(date +%%s)"; ' % AGENTS)
        cases = [(write + "_write_telemetry 0\n", {"TELEMETRY_GROK": '{"a":1},"x":{"b":2}'}),
                 (write + "TELEMETRY_GROK=$'{\"a\":\"\\x01\"}'; _write_telemetry 0\n", {}),
                 (write + "TELEMETRY_GROK='nope'; _write_telemetry 0\n", {}),
                 (write + 'TELEMETRY_GROK="{$(printf "%5000s" "")}"; _write_telemetry 0\n', {}),
                 (write + "TELEMETRY_GROK='{\"a\":1}'; _write_telemetry 0\n", {})]
        for script, env in cases:
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + script],
                               env={**os.environ, "TFILE": str(tfile), **env},
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
        text = tfile.read_text()
        self.assertNotIn("\x01", text)
        records = [json.loads(line) for line in text.splitlines()]
        self.assertGreaterEqual(len(records), len(cases))
        self.assertTrue(all("x" not in rec for rec in records))
        # Each shell writes twice (the explicit call, then its EXIT trap); only
        # the last case's well-formed object may come through.
        self.assertTrue(all(rec["grok"] is None for rec in records[:-2]))
        self.assertEqual([rec["grok"] for rec in records[-2:]], [{"a": 1}, {"a": 1}])

    def test_help_probe_detects_stream_support(self):
        for help_text, expected in (("--prompt-file --output-format streaming-json", "yes"),
                                    ("--prompt-file --output-format plain json", "no")):
            r = subprocess.run(["bash", "-c", (
                "set -euo pipefail\nsource '%s'\n"
                "_probe_setup_lenient() { :; }\n"
                "_probe_or_bare() { printf '%%s' \"$HELP\"; }\n"
                "_grok_has_prompt_file; echo \"stream=$_grok_stream_json\"\n") % AGENTS],
                env={**os.environ, "HELP": help_text}, capture_output=True, text=True, timeout=30)
            self.assertIn(f"stream={expected}", r.stdout, r.stderr)


if __name__ == "__main__":
    unittest.main()
