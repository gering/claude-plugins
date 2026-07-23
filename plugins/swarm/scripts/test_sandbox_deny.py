#!/usr/bin/env python3
"""Regression tests for the OS secret-jail denylist (agents.sh).

Security core for swarm 0.6.0: with file-read now ON for external voices, an
injected request to read a secret must still be blocked. These tests assert
`_sandbox_deny_paths` still emits HOME secret stores AND the new repo-local
paths when they exist, and — when sandbox-exec is available — that a sandboxed
`cat` of a temp `.env` does not emit the marker.

Run: python3 plugins/swarm/scripts/test_sandbox_deny.py
     (also discovered by scripts/check-structure.py's plugin tests check)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENTS = HERE / "agents.sh"
REPO = HERE.parents[2]  # worktree root (…/plugins/swarm/scripts → …)


def _bash_deny_paths(backend: str = "codex", cwd: Path | None = None,
                     env_extra: dict | None = None) -> list[str]:
    """Source agents.sh and print _sandbox_deny_paths output as lines."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    # agents.sh ends with `main "$@"` — sourcing it outright would run the CLI.
    # Load only the function/variable definitions by eval'ing everything up to
    # (but not including) the `main() {` line, then call the helper directly.
    harness = f'''
set -euo pipefail
eval "$(sed -n '1,/^main() {{/p' "{AGENTS}" | sed '$d')"
_sandbox_deny_paths "{backend}"
'''
    r = subprocess.run(
        ["bash", "-c", harness],
        cwd=str(cwd or REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"_sandbox_deny_paths failed (rc={r.returncode}):\n"
            f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
        )
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


class TestSandboxDenyPaths(unittest.TestCase):
    def test_home_secrets_always_denied(self):
        home = os.path.expanduser("~")
        paths = _bash_deny_paths("codex")
        for name in (".aws", ".ssh", ".gnupg", ".netrc", ".git-credentials"):
            target = f"{home}/{name}"
            self.assertIn(
                target, paths,
                f"expected HOME secret {target!r} in denylist; got {paths!r}",
            )

    def test_own_backend_cred_dir_stays_readable(self):
        home = os.path.expanduser("~")
        # codex keeps ~/.codex; denies ~/.grok
        codex_paths = _bash_deny_paths("codex")
        self.assertNotIn(f"{home}/.codex", codex_paths)
        self.assertIn(f"{home}/.grok", codex_paths)
        # grok keeps ~/.grok; denies ~/.codex
        grok_paths = _bash_deny_paths("grok")
        self.assertNotIn(f"{home}/.grok", grok_paths)
        self.assertIn(f"{home}/.codex", grok_paths)

    def test_repo_local_secrets_when_present(self):
        """When repo-local secret paths exist, they must appear in the denylist."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Make it a git work tree so _sandbox_deny_paths resolves repo root.
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            env_file = repo / ".env"
            env_file.write_text("MARKER=sandbox-deny-test-secret\n")
            data_dir = repo / "data"
            data_dir.mkdir()
            pem = repo / "test.pem"
            pem.write_text("-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n")
            key = repo / "service.key"
            key.write_text("key-material\n")
            id_rsa = repo / "id_rsa"
            id_rsa.write_text("ssh-key-material\n")

            paths = _bash_deny_paths("codex", cwd=repo)
            # git rev-parse may realpath the worktree (macOS /var → /private/var).
            for expected in (env_file, data_dir, pem, key, id_rsa):
                resolved = os.path.realpath(expected)
                self.assertTrue(
                    resolved in paths or str(expected) in paths,
                    f"expected repo-local {expected} (or {resolved}) in denylist; got {paths!r}",
                )
            # HOME secrets still present even from a temp repo cwd
            home = os.path.expanduser("~")
            self.assertIn(f"{home}/.aws", paths)

    def test_swarm_deny_paths_extra(self):
        with tempfile.TemporaryDirectory() as td:
            extra = Path(td) / "custom-secret"
            extra.write_text("x\n")
            paths = _bash_deny_paths(
                "codex",
                env_extra={"SWARM_DENY_PATHS": str(extra)},
            )
            self.assertIn(str(extra), paths)

    def test_missing_repo_local_not_emitted_as_literal_glob(self):
        """nullglob: a missing .env* must not emit a literal '.env*' path."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            # No .env*, no data/, no keys.
            paths = _bash_deny_paths("codex", cwd=repo)
            for p in paths:
                self.assertNotIn(".env*", p, f"literal glob leaked: {p!r}")
                self.assertFalse(p.endswith("/*.pem"), f"literal glob leaked: {p!r}")
                self.assertFalse(p.endswith("/id_*"), f"literal glob leaked: {p!r}")


@unittest.skipUnless(
    shutil.which("sandbox-exec") is not None,
    "sandbox-exec not available (macOS host-dependent; denylist unit asserts still run)",
)
class TestSandboxE2E(unittest.TestCase):
    """End-to-end: sandboxed cat of a temp .env must not emit the marker.

    Gates on sandbox-exec so Linux/CI without it skips cleanly. This exercises
    the full sandboxed() path including profile build + env filter.
    """

    def test_sandboxed_cat_env_blocked(self):
        marker = "SANDBOX_E2E_MARKER_9f3a2c1b"
        ok_marker = "SANDBOX_E2E_READABLE_5d1c7e0a"
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            env_file = repo / ".env"
            env_file.write_text(f"SECRET={marker}\n")
            ok_file = repo / "readable.txt"
            ok_file.write_text(f"OK={ok_marker}\n")

            # Source agents.sh helpers, then run TWO sandboxed cats:
            #  1. POSITIVE CONTROL — a non-denied file MUST come through (under
            #     set -e), proving the jail actually ran and allows normal reads.
            #     Without it, "wrapper broke before cat" and "jail denied the
            #     read" are indistinguishable (both leave the marker absent).
            #  2. The denied .env — `|| true` only here, because the DENIED read
            #     is expected to fail; the assertion is marker absence.
            # Use a dummy backend name so both ~/.codex and ~/.grok stay denied
            # (irrelevant here); the repo .env must be denied by the new rule.
            harness = f'''
set -euo pipefail
eval "$(sed -n '1,/^main() {{/p' "{AGENTS}" | sed '$d')"
# Force re-init for this backend in this process.
_sandbox_ready="<none>"
sandboxed codex cat "{ok_file}"
sandboxed codex cat "{env_file}" || true
'''
            r = subprocess.run(
                ["bash", "-c", harness],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )
            combined = r.stdout + r.stderr
            self.assertEqual(
                r.returncode, 0,
                f"sandbox harness failed before the denied read (rc={r.returncode}):\n{combined!r}",
            )
            self.assertIn(
                ok_marker, r.stdout,
                f"positive control missing — jail blocked (or never ran) a non-denied read:\n{combined!r}",
            )
            self.assertNotIn(
                marker, combined,
                f"secret marker leaked through sandboxed cat:\n{combined!r}",
            )


if __name__ == "__main__":
    # unittest (deliberately diverging from the siblings' plain check()/FAILS
    # style): skipUnless cleanly gates the host-dependent sandbox-exec e2e.
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
