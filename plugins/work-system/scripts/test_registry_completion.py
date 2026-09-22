#!/usr/bin/env python3
"""Registry producers must finish before their records are consumed or emitted."""
import os
import select
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = root / "agent-registry.sh"
        source = (HERE / "agent-registry.sh").read_text()
        # Load the actual functions without executing the CLI dispatcher. No
        # behavior is replaced except the explicitly stubbed data producers.
        self.assertTrue(source.endswith('main "$@"\n'))
        self.registry.write_text(source.removesuffix('main "$@"\n'))
        # Every sibling library the registry sources must travel with it, or the
        # copy dies on its own source guard and every case fails as exit 1.
        for lib in ("lib-bounded.sh", "lib-grok-latest.sh"):
            (root / lib).write_text((HERE / lib).read_text())
        self.env = dict(os.environ)
        self.env["HOME"] = str(root)
        self.env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.env["GIT_CONFIG_SYSTEM"] = os.devnull
        self.env.pop("BASH_ENV", None)
        self.env.pop("ENV", None)
        self.prefix = '''set -eu
. "$1"
entry_status() { printf 'yes\\t\\n'; }
harness_rows() { :; }
harness_on_path() { return 1; }
'''

    def tearDown(self):
        self.tmp.cleanup()

    def run_shell(self, body):
        return subprocess.run(
            ["bash", "-c", self.prefix + body, "test", str(self.registry)],
            cwd=self.tmp.name, env=self.env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10,
        )

    def test_failed_status_cannot_publish_partial_success(self):
        # read < <(producer) sees the valid line and loses the producer's exit
        # status. Waiting via command substitution must reject that partial data.
        for invocation in ("subcmd_resolve --kimi", "subcmd_list --tsv"):
            with self.subTest(invocation=invocation):
                result = self.run_shell(
                    "entry_status() { printf 'yes\\tlooks-ready\\n'; return 41; }\n"
                    + invocation + "\n"
                )
                self.assertEqual(result.returncode, 41, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_failed_status_after_grok_resolution_publishes_nothing(self):
        # grok resolves its concrete model BEFORE the status producer runs, and
        # it reports provenance lines. Printed eagerly, those lines reached stdout
        # ahead of a failed status: a partial record under a non-zero exit.
        resolver = ("grok_resolve_latest() { GROK_CONCRETE=grok-4.7; GROK_CATALOG=ok; GROK_WHY=; }\n")
        failing = resolver + "entry_status() { printf 'yes\\tlooks-ready\\n'; return 41; }\n"
        result = self.run_shell(failing + "subcmd_resolve --grok\n")
        self.assertEqual(result.returncode, 41, result.stderr)
        self.assertEqual(result.stdout, "")
        # ...and on success the provenance still arrives, with the record.
        result = self.run_shell(resolver + "subcmd_resolve --grok --session example\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in ("model_requested=latest\n", "model_source=latest\n",
                     "model_latest=grok-4.7\n", "model_catalog=ok\n", "available=yes\n"):
            self.assertIn(line, result.stdout)

    def test_failed_row_producers_cannot_publish_partial_records(self):
        cases = (
            ("registry_rows() { printf '%s\\n' '--kimi|kimi|kimi-code/k3-256k|resume|pane-run|kimi'; return 42; }\n",
             "find_row flag --kimi", 42),
            ("registry_rows() { printf '%s\\n' '--kimi|kimi|kimi-code/k3-256k|resume|pane-run|kimi'; return 42; }\n",
             "subcmd_list --tsv", 42),
            ("harness_rows() { printf 'cc-harness:fake\\tcc-harness\\tmodel\\tyes\\t\\n'; return 43; }\n",
             "harness_lookup cc-harness:fake", 43),
            ("harness_lookup() { printf 'cc-harness:fake\\tcc-harness\\tmodel\\tyes\\t\\n'; return 44; }\n",
             "subcmd_resolve cc-harness:fake", 2),
        )
        for producer, invocation, status in cases:
            with self.subTest(invocation=invocation):
                result = self.run_shell(producer + invocation + "\n")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_completed_status_preserves_available_and_unavailable_contracts(self):
        for available, status in (("yes", 0), ("no", 3)):
            with self.subTest(available=available):
                result = self.run_shell(
                    f"entry_status() {{ printf '{available}\\tfixture note\\n'; }}\n"
                    "subcmd_resolve --kimi --session example\n"
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn(f"available={available}\n", result.stdout)
                self.assertIn("note=fixture note\n", result.stdout)
                self.assertIn("argv=sh\n", result.stdout)
                self.assertIn("argv_shell=", result.stdout)

    def test_producer_exit_cannot_interrupt_a_blocked_record_write(self):
        # On macOS Bash 3.2, a process-substitution child's late SIGCHLD can
        # interrupt printf after a partial pipe write (EINTR). Keep the producer
        # alive briefly AFTER its status line and exert bounded backpressure on
        # the eventual record. Correct code waits for the producer first.
        body = '''TEST_RECORD=x
for n in {1..18}; do TEST_RECORD="$TEST_RECORD$TEST_RECORD"; done
entry_status() {
  printf 'yes\\t\\n'
  sleep 0.2
}
emit_record() { builtin printf '%s' "$TEST_RECORD"; }
subcmd_resolve --kimi
'''
        process = subprocess.Popen(
            ["bash", "-c", self.prefix + body, "test", str(self.registry)],
            cwd=self.tmp.name, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            ready, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(ready, "record writer did not start")
            time.sleep(0.35)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
            self.assertEqual(len(stdout), 262144)
            self.assertEqual(set(stdout), {ord("x")})
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
