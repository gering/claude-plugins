#!/usr/bin/env python3
"""Regression tests for the OS secret-jail denylist (agents.sh).

Security core for swarm 0.6.0: with file-read now ON for external voices, an
injected request to read a secret must still be blocked. These tests assert
`_sandbox_deny_paths` still emits HOME secret stores AND the new repo-local
paths when they exist, and — when sandbox-exec is available — that a sandboxed
`cat` of a temp `.env` does not emit the marker.

Run: python3 plugins/swarm/scripts/test_sandbox_deny.py
     (also discovered by scripts/check-structure.py's plugin tests check)

The e2e class exercises the sandbox-exec (macOS) path only; the bwrap (Linux)
enforcement path has no e2e here — extending coverage there belongs to the
add-sandbox-regression-tests task (coordinate, don't fork).
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


def _sandbox_exec_works() -> bool:
    """The SAME functional probe production uses (agents.sh _init_sandbox).

    A present-but-broken sandbox-exec (on PATH yet unable to apply a profile) is
    treated as jail-less at runtime — so the e2e must gate on the wrapper
    actually WORKING, not on PATH presence, or CI fails on a host the runtime
    degrades cleanly.
    """
    if shutil.which("sandbox-exec") is None:
        return False
    try:
        r = subprocess.run(
            ["sandbox-exec", "-p", "(version 1)(allow default)", "true"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _source(*shell_lines: str, cwd: Path | None = None,
            env_extra: dict | None = None, timeout: int = 30):
    """Run a bash harness that `source`s agents.sh for its helpers.

    agents.sh's source guard (`[[ ${BASH_SOURCE[0]} == $0 ]]`) keeps `main`
    from running on source, so no sed-extraction surgery is needed.
    """
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    harness = "set -euo pipefail\nsource '%s'\n%s\n" % (AGENTS, "\n".join(shell_lines))
    return subprocess.run(
        ["bash", "-c", harness],
        cwd=str(cwd or REPO), env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def _bash_deny_paths(backend: str = "codex", cwd: Path | None = None,
                     env_extra: dict | None = None) -> list[str]:
    """Source agents.sh and print _sandbox_deny_paths output as lines."""
    r = _source(f'_sandbox_deny_paths "{backend}"', cwd=cwd, env_extra=env_extra)
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
        # Incl. the 0.6.0 additions (git/cargo config) — without asserting them,
        # a regression that drops the new entries would pass unnoticed.
        for name in (".aws", ".ssh", ".gnupg", ".netrc", ".git-credentials",
                     ".gitconfig", ".config/git", ".cargo/credentials.toml"):
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

    def test_worktree_denies_main_checkout_secrets(self):
        """From a linked worktree, the MAIN checkout's root secrets are denied too.

        Untracked .env/data/ never propagate into a worktree, so in the standard
        /kickoff layout the real secrets sit in the main checkout — a readable
        sibling path unless _sandbox_deny_paths walks up via --git-common-dir.
        """
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "main"
            main.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=main, check=True)
            (main / ".env").write_text("SECRET=main-checkout\n")
            subprocess.run(
                ["git", "-C", str(main), "commit", "--allow-empty", "-m", "x", "-q"],
                check=True, env=env)
            wt = Path(td) / "wt"
            subprocess.run(
                ["git", "-C", str(main), "worktree", "add", "-q", str(wt)],
                check=True, env=env)

            paths = _bash_deny_paths("codex", cwd=wt)
            expected = main / ".env"
            resolved = os.path.realpath(expected)
            self.assertTrue(
                resolved in paths or str(expected) in paths,
                f"main-checkout .env missing from worktree denylist; got {paths!r}",
            )

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
    _sandbox_exec_works(),
    "no WORKING sandbox-exec (absent, or present-but-can't-apply — the runtime "
    "degrades on such a host, so the e2e would false-fail; denylist units still run)",
)
class TestSandboxE2E(unittest.TestCase):
    """End-to-end: sandboxed cat of a temp .env must not emit the marker.

    Gates on a FUNCTIONAL sandbox-exec probe (not mere PATH presence) so Linux/CI
    and broken-wrapper hosts skip cleanly — matching the runtime's own degrade
    condition. Exercises the full sandboxed() path (profile build + env filter).
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

            # Three sandboxed calls (dummy backend name; the repo .env is denied
            # by the repo-local rule):
            #  1. POSITIVE CONTROL — a non-denied file MUST come through (under
            #     set -e), proving the jail ran and allows normal reads. Without
            #     it, "wrapper broke before cat" and "jail denied the read" are
            #     indistinguishable (both leave the marker absent).
            #  2. git must WORK inside the jail — guards the GIT_CONFIG_GLOBAL/
            #     SYSTEM=/dev/null redirect that keeps git alive while the global
            #     config paths are denied (a regression there breaks exploration).
            #  3. The denied .env — `|| true`, the DENIED read is expected to
            #     fail; the assertion is marker absence.
            r = _source(
                f'sandboxed codex cat "{ok_file}"',
                f'sandboxed codex git -C "{repo}" rev-parse --is-inside-work-tree',
                f'sandboxed codex cat "{env_file}" || true',
                cwd=repo,
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
            self.assertIn(
                "true", r.stdout,
                f"git broke inside the jail (config redirect regressed?):\n{combined!r}",
            )
            self.assertNotIn(
                marker, combined,
                f"secret marker leaked through sandboxed cat:\n{combined!r}",
            )


SCHEMA = HERE / "schema" / "finding.schema.json"

# Override sandboxed() to record the exact backend argv and emit canned valid
# output (grok envelope on stdout; codex JSON into its --output-last-message
# file), so run_codex/run_grok complete without a real CLI or jail. `_source`
# writes the argv to $ARGV.
_RECORD_SANDBOXED = r'''
sandboxed() {
  shift  # drop the backend name arg
  printf '%s\n' "$@" > "$ARGV"
  local a prev="" out=""
  for a in "$@"; do [ "$prev" = "--output-last-message" ] && out="$a"; prev="$a"; done
  [ -n "$out" ] && printf '{"findings":[]}' > "$out"
  printf '{"structuredOutput":{"findings":[]}}'
}
'''


class TestFailClosedDegrade(unittest.TestCase):
    """The load-bearing fail-closed contract: a jail-less host must strip the
    read+web tools (grok) and hard-disable web (codex); a jailed host must grant
    them. Asserted on the actual argv run_grok/run_codex build."""

    def _argv(self, backend: str, jail: bool) -> str:
        with tempfile.NamedTemporaryFile("r", suffix=".argv") as tf:
            jail_fn = "_jail_available() { return 0; }" if jail \
                else "_jail_available() { return 1; }"
            r = _source(
                jail_fn,
                _RECORD_SANDBOXED,
                f'run_{backend} "prompt text" high "" "{SCHEMA}" >/dev/null 2>&1 || true',
                env_extra={"ARGV": tf.name},
            )
            self.assertEqual(r.returncode, 0, f"harness failed: {r.stderr!r}")
            return Path(tf.name).read_text()

    def test_grok_degrades_toolless_noweb(self):
        argv = self._argv("grok", jail=False)
        self.assertIn("--disable-web-search", argv,
                      f"jail-less grok must disable web; argv:\n{argv}")
        self.assertNotIn("web_search", argv,
                         f"jail-less grok must NOT grant web tools; argv:\n{argv}")
        self.assertNotIn("read_file", argv,
                         f"jail-less grok must be tool-less; argv:\n{argv}")

    def test_grok_grants_read_web_when_jailed(self):
        argv = self._argv("grok", jail=True)
        self.assertIn("read_file", argv, f"jailed grok must grant read; argv:\n{argv}")
        self.assertIn("web_search", argv, f"jailed grok must grant web; argv:\n{argv}")
        self.assertNotIn("--disable-web-search", argv, f"jailed grok argv:\n{argv}")

    def test_codex_hard_disables_web_when_jailless(self):
        argv = self._argv("codex", jail=False)
        self.assertIn("tools.web_search=false", argv,
                      f"jail-less codex must HARD-disable web (=false, not omit); argv:\n{argv}")
        self.assertNotIn("tools.web_search=true", argv, f"argv:\n{argv}")

    def test_codex_enables_web_when_jailed(self):
        argv = self._argv("codex", jail=True)
        self.assertIn("tools.web_search=true", argv,
                      f"jailed codex must enable web; argv:\n{argv}")


if __name__ == "__main__":
    # unittest (deliberately diverging from the siblings' plain check()/FAILS
    # style): skipUnless cleanly gates the host-dependent sandbox-exec e2e.
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
