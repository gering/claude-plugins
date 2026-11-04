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
    "cc-harness:rung\topus\tyes\t-\n"
    "cc-harness:wide\tgpt-5.6-sol [1m]\tyes\t-\n"
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
cp "$CLAUDE_CONFIG_DIR/settings.json" "$STUB_DIR/settings" 2>/dev/null
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
        self.assertEqual(sol["model"], "gpt-6.1-sol", "the routed model is its own field, not parsed from a label")
        self.assertEqual(sol["family"], "openai")
        self.assertEqual(rows["cc-harness:grok"]["family"], "grok")
        self.assertEqual(rows["cc-harness:odd"]["family"], "unknown:odd")
        self.assertEqual(rows["cc-harness:rung"]["family"], "unknown:rung", "a helper rung name is not Claude")
        self.assertEqual(rows["cc-harness:wide"]["model"], "gpt-5.6-sol1m", "identity charset at load, one place")
        luna = rows["cc-harness:luna"]
        self.assertFalse(luna["ready"])
        self.assertIn("cliproxyapi -codex-login", luna["hint"])

    def test_unconfigured_or_absent_helper_adds_no_rows(self):
        self.assertFalse([b for b in self.rows(STUB_LIST_RC="3") if b.startswith("cc-harness:")])
        absent = self.rows(SWARM_CC_HARNESS_BIN=str(self.stub / "nope"))
        self.assertFalse([b for b in absent if b.startswith("cc-harness:")])

    def test_unconfigured_helper_says_why(self):
        r = self.sh("ready", "cc-harness:sol", STUB_LIST_RC="3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not configured here (no gateway token)", r.stderr)

    def test_list_runs_once_per_run(self):
        counter = self.stub / "list-calls"
        helper = (self.stub / "cc-harness-agents")
        helper.write_text(HELPER.replace('list) ', f'list) echo x >> "{counter}"; '))
        r = self.run_voice()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(counter.read_text().splitlines()), 1, "one `list` probe per run, not one per lookup")

    def test_shell_function_claude_does_not_count(self):
        (self.stub / "claude").unlink()
        # An EXPORTED function named claude (the cc-router shape) must not count.
        r = self.sh("ready", "cc-harness:sol", PATH=f"{self.stub}:/usr/bin:/bin",
                    **{"BASH_FUNC_claude%%": "() {  echo fn; }"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("no claude executable on PATH", r.stderr)

    def test_invalid_id_is_a_usage_error(self):
        r = self.sh("ready", "cc-harness:Bad;id")
        self.assertEqual(r.returncode, 2)

    # --- voices policy ----------------------------------------------------

    def voices(self, live, **env):
        rows = [{"backend": b, "available": True, "ready": True, "family": f,
                 "version": "1.0", "model": m}
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
            # Anchored: a vendor prefix wins over a tier word inside the id.
            ({"ANTHROPIC_MODEL": "gpt-5.4-haiku"}, "model=gpt-5.4-haiku;family=openai"),
            ({"ANTHROPIC_MODEL": "grok-4-codex"}, "model=grok-4-codex;family=grok"),
            # A tier alias resolves the way Claude Code does — a gateway maps it.
            ({"ANTHROPIC_MODEL": "opus", "ANTHROPIC_DEFAULT_OPUS_MODEL": "gpt-6.1-sol",
              "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}, "model=gpt-6.1-sol;family=openai"),
            ({"ANTHROPIC_MODEL": "opus", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"},
             "model=opus;family=unknown:session"),
            ({"ANTHROPIC_MODEL": "opus"}, "model=opus;family=claude"),
            # A context suffix is not part of the id; plan/default aliases are Claude natively.
            ({"ANTHROPIC_MODEL": "opus[1m]"}, "model=opus;family=claude"),
            ({"ANTHROPIC_MODEL": "claude-opus-5-5[1m]"}, "model=claude-opus-5-5;family=claude"),
            ({"ANTHROPIC_MODEL": "opusplan"}, "model=opusplan;family=claude"),
            ({"ANTHROPIC_MODEL": "default"}, "model=default;family=claude"),
            ({"ANTHROPIC_MODEL": "default", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"},
             "model=default;family=unknown:session"),
        ]
        for env, want in cases:
            env = {"ANTHROPIC_DEFAULT_OPUS_MODEL": "", **env}
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
        settings = json.loads((self.stub / "settings").read_text())
        self.assertIn("Read(//proc/**)", settings["permissions"]["deny"],
                      "the routing token is in the voice's env: /proc/self/environ must not be readable")

    def test_scrub_redacts_underscore_token_forms(self):
        r = self.source('printf \'{"s":"ANTHROPIC_AUTH_TOKEN=abcdefghijklmnopqrstuv"}\' | scrub_secrets')
        self.assertNotIn("abcdefghijklmnopqrstuv", r.stdout)
        self.assertIn("TOKEN=[REDACTED]", r.stdout)

    SCRUB_CASES = [
        ("CLIPROXY_API_KEY_SOL=abcdefghijklmnopqrstuv", "CLIPROXY_API_KEY_SOL=[REDACTED]"),
        ("CLIPROXY_CF_ACCESS_SOL_CLIENT_ID=abcdefghijklmnopqrstuv", "CLIPROXY_CF_ACCESS_SOL_CLIENT_ID=[REDACTED]"),
        ("ANTHROPIC_AUTH_TOKEN=abcdefghijklmnopqrstuv", "ANTHROPIC_AUTH_TOKEN=[REDACTED]"),
        ("see token_path: plugins/swarm/scripts/agents.sh line 3", None),
        ("password_hash: abcdefghijklmnopqrstuvwx", None),
        ("SWARM_SECRET_PATHS=/Users/someone/foo/bar/baz", None),
    ]

    def test_scrub_profile_suffixed_names_but_not_identifiers(self):
        for raw, want in self.SCRUB_CASES:
            r = self.source("printf '%s' " + json.dumps(json.dumps({"s": raw})) + " | scrub_secrets")
            self.assertEqual(json.loads(r.stdout)["s"], want or raw, raw)

    def test_js_output_gate_scrubs_like_the_adapter(self):
        # scrubField (workflow) hand-mirrors scrub_secrets (adapter): same inputs,
        # same output, or a lens finding leaks what an external one would not.
        src = (HERE.parent / "workflows" / "swarm-review.js").read_text()
        start = src.index("function scrubField(s)")
        fn = src[start:src.index("\n}\n", start) + 2]
        cases = [raw for raw, _ in self.SCRUB_CASES]
        js = subprocess.run(["node", "-e", fn + "\nconsole.log(JSON.stringify(" + json.dumps(cases)
                             + ".map(c => scrubField(c).s)))"], capture_output=True, text=True, timeout=60)
        self.assertEqual(js.returncode, 0, js.stderr)
        self.assertEqual(json.loads(js.stdout), [want or raw for raw, want in self.SCRUB_CASES])

    def test_oversize_listing_truncates_instead_of_failing(self):
        many = "".join(f"cc-harness:v{i}\tgpt-6.1-sol\tyes\t{'x' * 200}\n" for i in range(400))
        rows = self.rows(STUB_ROWS=many)
        harness = [b for b in rows if b.startswith("cc-harness:")]
        self.assertEqual(len(harness), 50, "a long listing is capped, not read as a failed one (SIGPIPE)")

    def test_helper_runs_outside_the_jail(self):
        # The helper must exec the jail wrapper, not run inside it: it may read
        # its gateway config, which the jail denies to the model process.
        r = self.source(
            "_jail_available() { return 0; }",
            "_init_sandbox() { SANDBOX_CMD=(env SWARM_STUB_JAILED=1); }",
            f'main run cc-harness:sol --prompt-file "{self.prompt}" --effort high')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SWARM_STUB_JAILED=1", (self.stub / "env").read_text())
        self.assertEqual(self.argv()[0], "-p", "the jail wraps claude, after the helper")

    # --- env filter split around the helper --------------------------------

    SECRET = "CLIPROXY_CF_ACCESS_STUB_CLIENT_SECRET"

    def secret_helper(self):
        # A helper that routes only with an env credential (like cc-router's
        # remote route), injects its own routing token, and records the env
        # NAMES it sees per subcommand (values never: dummy or not).
        helper = self.stub / "cc-harness-agents"
        helper.write_text(f"""#!/bin/bash
env | cut -d= -f1 | sort > "$STUB_DIR/helper-env-$1"
[ -n "${{{self.SECRET}:-}}" ] || exit 3
case "$1" in
  list) printf '%s' "$STUB_ROWS"; exit 0 ;;
  exec) shift; shift; [ "$1" = -- ] && shift
        export ANTHROPIC_AUTH_TOKEN=helper-routing-dummy; exec "$@" ;;
esac
exit 2
""")

    def claude_env(self):
        return dict(line.split("=", 1) for line in (self.stub / "env").read_text().splitlines()
                    if "=" in line)

    def test_helper_sees_env_credentials_but_claude_does_not(self):
        self.secret_helper()
        r = self.run_voice(**{self.SECRET: "dummy-secret", "GH_TOKEN": "dummy-gh",
                              "CLIPROXY_API_KEY_STUB": "dummy-key",
                              "CLIPROXY_CF_ACCESS_STUB_CLIENT_ID": "dummy-id",
                              "ANTHROPIC_AUTH_TOKEN": "parent-dummy", "ANTHROPIC_API_KEY": "parent-key"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"findings": []})
        env = self.claude_env()
        for name in (self.SECRET, "GH_TOKEN", "CLIPROXY_API_KEY_STUB",
                     "CLIPROXY_CF_ACCESS_STUB_CLIENT_ID", "ANTHROPIC_API_KEY", "CLAUDECODE"):
            self.assertNotIn(name, env, f"{name} must not reach the jailed claude")
        self.assertEqual(env.get("ANTHROPIC_AUTH_TOKEN"), "helper-routing-dummy",
                         "the helper's routing token survives; the parent's never does")
        self.assertEqual(env.get("GIT_CONFIG_GLOBAL"), "/dev/null")

    def test_readiness_and_exec_see_the_same_env(self):
        self.secret_helper()
        env = {self.SECRET: "dummy-secret", "GH_TOKEN": "dummy-gh", "ANTHROPIC_AUTH_TOKEN": "parent-dummy"}
        self.assertTrue(self.rows(**env)["cc-harness:sol"]["ready"])
        r = self.run_voice(**env)
        self.assertEqual(r.returncode, 0, r.stderr)
        listed = (self.stub / "helper-env-list").read_text()
        execd = (self.stub / "helper-env-exec").read_text()
        self.assertEqual(listed, execd, "readiness must probe the helper under the exec env")
        self.assertIn(self.SECRET, listed.split())
        self.assertNotIn("OLDPWD", execd.split(), "exec's cd must not leak the repo path")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", listed.split())

    def test_helper_that_cannot_route_is_not_ready(self):
        self.secret_helper()
        r = self.sh("ready", "cc-harness:sol")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no gateway token", r.stderr)

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

    def test_rerouted_id_fails_the_voice(self):
        r = self.run_voice(SWARM_HARNESS_EXPECT="grok-4.7")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not the 'grok-4.7' this run was planned with", r.stderr)
        self.assertEqual(self.run_voice(SWARM_HARNESS_EXPECT="gpt-6.1-sol").returncode, 0)

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
        self.assertIn(str(home / ".cli-proxy-api"), paths, "the jailed claude never needs gateway credentials")
        self.assertIn(str(home / ".config" / "cc-router"), paths, "the helper's client token stays outside the jail")
        sib = self.source("_sandbox_deny_paths codex", HOME=str(home), SWARM_HOST_HOME=str(home))
        self.assertIn(str(home / ".cli-proxy-api"), sib.stdout.splitlines(), "siblings never read gateway credentials")
        self.assertIn(str(self.repo.resolve() / "CLAUDE.md"), [str(Path(p).resolve()) for p in paths])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
