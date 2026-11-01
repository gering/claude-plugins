#!/usr/bin/env python3
"""Hermetic tests for the cc-harness voices in agents.sh.

A stub `cc-harness-agents` (list rows, exec → the target argv) and a stub
`claude` (records argv/cwd/stdin, prints a `claude -p --output-format json`
envelope) stand in for the gateway, so nothing here reaches a model.

Run: python3 plugins/swarm/scripts/test_cc_harness.py
     (also discovered by scripts/check-structure.py's plugin tests check)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENTS = HERE / "agents.sh"

ROWS = (
    "cc-harness:sol\tgpt-6.1-sol\tyes\t-\n"
    "cc-harness:grok\tgrok-4.7\tyes\t-\n"
    "cc-harness:luna\tgpt-6-luna\tno\trun: cliproxyapi -codex-login\n"
    "cc-harness:odd\tmystery-9\tyes\t-\n"
    "bad name\tx\tyes\t-\n"
    "cc-harness:short\tonly-three\tyes\n"
)

HELPER = """#!/bin/bash
case "$1" in
  list) [ -n "$STUB_LIST_RC" ] && exit "$STUB_LIST_RC"; printf '%s' "$STUB_ROWS"; exit 0 ;;
  exec) shift; id="$1"; shift; [ "$1" = -- ] && shift
        echo "$id" > "$STUB_DIR/exec-id"; exec "$@" ;;
esac
exit 2
"""

CLAUDE = """#!/bin/bash
printf '%s\\n' "$@" > "$STUB_DIR/argv"
pwd -P > "$STUB_DIR/cwd"
ls -A | wc -l | tr -d ' ' > "$STUB_DIR/cwd-entries"
echo "$CLAUDE_CONFIG_DIR" > "$STUB_DIR/config"
env > "$STUB_DIR/env"
cat > "$STUB_DIR/stdin"
printf '%s\\n' "$STUB_ENVELOPE"
"""

OK_ENVELOPE = json.dumps({"type": "result", "is_error": False, "num_turns": 2,
                          "structured_output": {"findings": []}})


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.stub = root / "stub"
        self.stub.mkdir()
        for name, body in (("cc-harness-agents", HELPER), ("claude", CLAUDE)):
            f = self.stub / name
            f.write_text(body)
            f.chmod(0o755)
        self.repo = root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / "CLAUDE.md").write_text("repo instructions\n")
        self.scratch = root / "scratch"
        self.scratch.mkdir()
        self.prompt = self.scratch / "p.txt"
        self.prompt.write_text("REVIEW ME\n")

    def tearDown(self):
        self.td.cleanup()

    def env(self, **extra):
        env = os.environ.copy()
        for k in ("CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL",
                  "SWARM_HARNESS", "SWARM_HARNESS_VOICES"):
            env.pop(k, None)
        env.update({
            "PATH": f"{self.stub}:{env['PATH']}",
            "SWARM_CC_HARNESS_BIN": str(self.stub / "cc-harness-agents"),
            "STUB_DIR": str(self.stub), "STUB_ROWS": ROWS, "STUB_ENVELOPE": OK_ENVELOPE,
            "TMPDIR": str(self.scratch),
        })
        env.update(extra)
        return env

    def sh(self, *args, stdin=None, **env):
        return subprocess.run(["bash", str(AGENTS), *args], cwd=self.repo, env=self.env(**env),
                              input=stdin, capture_output=True, text=True, timeout=120)

    def source(self, *lines, **env):
        script = "set -euo pipefail\nsource '%s'\n%s\n" % (AGENTS, "\n".join(lines))
        return subprocess.run(["bash", "-c", script], cwd=self.repo, env=self.env(**env),
                              capture_output=True, text=True, timeout=120)

    # --- list / readiness -------------------------------------------------

    def rows(self, **env):
        r = self.sh("list", "--json", **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {row["backend"]: row for row in json.loads(r.stdout)}

    def test_list_appends_valid_helper_rows_only(self):
        rows = self.rows()
        self.assertIn("cc-harness:sol", rows)
        self.assertNotIn("bad name", rows)
        self.assertNotIn("cc-harness:short", rows, "a 3-column row breaks the contract")
        sol = rows["cc-harness:sol"]
        self.assertTrue(sol["available"] and sol["ready"])
        self.assertEqual(sol["family"], "openai")
        self.assertEqual(rows["cc-harness:grok"]["family"], "grok")
        self.assertEqual(rows["cc-harness:odd"]["family"], "unknown:odd")
        luna = rows["cc-harness:luna"]
        self.assertFalse(luna["ready"])
        self.assertIn("cliproxyapi -codex-login", luna["hint"])

    def test_unconfigured_or_absent_helper_adds_no_rows(self):
        self.assertFalse([b for b in self.rows(STUB_LIST_RC="3") if b.startswith("cc-harness:")])
        absent = self.rows(SWARM_CC_HARNESS_BIN=str(self.stub / "nope"))
        self.assertFalse([b for b in absent if b.startswith("cc-harness:")])

    def test_shell_function_claude_does_not_count(self):
        (self.stub / "claude").unlink()
        r = self.sh("ready", "cc-harness:sol", PATH=f"{self.stub}:/usr/bin:/bin")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no claude executable on PATH", r.stderr)

    def test_invalid_id_is_a_usage_error(self):
        r = self.sh("ready", "cc-harness:Bad;id")
        self.assertEqual(r.returncode, 2)

    # --- voices policy ----------------------------------------------------

    def voices(self, live, **env):
        rows = [{"backend": b, "available": True, "ready": True, "family": f,
                 "version": f"via cc-harness: {m}" if b.startswith("cc-harness:") else "1.0"}
                for b, f, m in live]
        r = self.sh("voices", stdin=json.dumps(rows), **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return dict(line.split("=", 1) for line in r.stdout.splitlines())

    NATIVES = [("codex", "openai", ""), ("grok", "grok", ""), ("kimi", "moonshot", "")]
    HARN = [("cc-harness:sol", "openai", "gpt-6.1-sol"), ("cc-harness:grok", "grok", "grok-4.7")]

    def test_harness_replaces_same_family_natives(self):
        out = self.voices(self.NATIVES + self.HARN)
        self.assertEqual(out["voices"], "kimi,cc-harness:sol,cc-harness:grok")
        self.assertEqual(out["harness"], "sol=openai,gpt-6.1-sol;grok=grok,grok-4.7")
        self.assertEqual(out["replaced"], "codex>cc-harness:sol,grok>cc-harness:grok")

    def test_not_live_harness_keeps_the_native(self):
        out = self.voices(self.NATIVES[:2] + self.HARN[:1])
        self.assertEqual(out["voices"], "grok,cc-harness:sol")

    def test_off_keeps_natives_and_bad_mode_refuses(self):
        out = self.voices(self.NATIVES[:2] + self.HARN, SWARM_HARNESS="off")
        self.assertEqual(out["voices"], "codex,grok")
        self.assertEqual(out["harness"], "")
        r = self.sh("voices", stdin="[]", SWARM_HARNESS="yes")
        self.assertEqual(r.returncode, 2)

    def test_custom_voice_list(self):
        out = self.voices(self.NATIVES[:2] + self.HARN, SWARM_HARNESS_VOICES="grok")
        self.assertEqual(out["voices"], "codex,cc-harness:grok")

    # --- session family ---------------------------------------------------

    def test_session_family(self):
        cases = [
            ({}, "model=;family=claude"),
            ({"ANTHROPIC_MODEL": "claude-opus-5-5"}, "model=claude-opus-5-5;family=claude"),
            ({"ANTHROPIC_MODEL": "gpt-6.1-sol"}, "model=gpt-6.1-sol;family=openai"),
            ({"ANTHROPIC_MODEL": "x", "CLAUDE_CODE_SUBAGENT_MODEL": "grok-4.7"}, "model=grok-4.7;family=grok"),
            ({"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}, "model=;family=unknown:session"),
            ({"ANTHROPIC_MODEL": "mystery-9"}, "model=mystery-9;family=unknown:session"),
            ({"ANTHROPIC_MODEL": "gpt-6$(id)"}, "model=gpt-6id;family=openai"),
        ]
        for env, want in cases:
            r = self.sh("session", **env)
            self.assertEqual(r.stdout.strip(), want, env)

    # --- run --------------------------------------------------------------

    def run_voice(self, jail=True, **env):
        jail_fn = "_jail_available() { return 0; }" if jail else "_jail_available() { return 1; }"
        return self.source(
            jail_fn, "_init_sandbox() { SANDBOX_CMD=(); }",
            f'main run cc-harness:sol --prompt-file "{self.prompt}" --effort high', **env)

    def argv(self):
        return (self.stub / "argv").read_text().splitlines()

    def test_jailed_run_reads_the_repo_through_add_dir_only(self):
        r = self.run_voice()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"findings": []})
        argv = self.argv()
        self.assertEqual((self.stub / "exec-id").read_text().strip(), "sol")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(argv[argv.index("--add-dir") + 1], str(self.repo.resolve()))
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        for flag in ("--strict-mcp-config", "--no-session-persistence", "-p"):
            self.assertIn(flag, argv)
        self.assertNotIn("--bare", argv, "--bare refuses ANTHROPIC_AUTH_TOKEN, the helper's auth")
        self.assertNotIn("REVIEW ME", "\n".join(argv), "the prompt must never travel on argv")
        self.assertIn("REVIEW ME", (self.stub / "stdin").read_text())
        cwd = (self.stub / "cwd").read_text().strip()
        self.assertNotEqual(cwd, str(self.repo.resolve()), "the voice must not start in the repo")
        self.assertEqual((self.stub / "cwd-entries").read_text().strip(), "0", "the voice's cwd must be empty")
        self.assertFalse(Path(cwd).exists(), "the ephemeral runtime must be removed after the run")
        config = (self.stub / "config").read_text().strip()
        self.assertEqual(Path(os.path.realpath(config)).parent.parent, self.scratch.resolve(),
                         "CLAUDE_CONFIG_DIR must be the ephemeral dir beside the prompt")
        env = (self.stub / "env").read_text()
        self.assertNotIn("CLAUDECODE=", env)

    def test_jailless_run_is_toolless(self):
        r = self.run_voice(jail=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = self.argv()
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--add-dir", argv)
        self.assertIn("tool-less", r.stderr)

    def test_error_envelope_is_a_failed_voice_not_empty_findings(self):
        for envelope in (json.dumps({"is_error": True, "subtype": "error_max_turns"}),
                         json.dumps({"is_error": False, "result": "text only"}),
                         "not json"):
            r = self.run_voice(STUB_ENVELOPE=envelope)
            self.assertEqual(r.returncode, 1, envelope)
            self.assertEqual(r.stdout.strip(), "", envelope)

    def test_model_flag_is_refused(self):
        r = self.sh("run", "cc-harness:sol", "--model", "gpt-x", "--prompt-file", str(self.prompt))
        self.assertEqual(r.returncode, 2)
        self.assertIn("helper sets the routed model", r.stderr)

    # --- jail -------------------------------------------------------------

    def test_jail_denies_claude_config_surfaces_but_not_siblings(self):
        home = Path(self.td.name) / "home"
        (home / ".claude" / "plugins").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}")
        r = self.source("_sandbox_deny_paths cc-harness:sol", HOME=str(home), SWARM_HOST_HOME=str(home))
        self.assertEqual(r.returncode, 0, r.stderr)
        paths = r.stdout.splitlines()
        self.assertIn(str(home / ".claude" / "plugins"), paths)
        self.assertIn(str(home / ".claude" / "settings.json"), paths)
        self.assertIn(str(home / ".codex"), paths)
        self.assertIn(str(self.repo.resolve() / "CLAUDE.md"), [str(Path(p).resolve()) for p in paths])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
