#!/usr/bin/env python3
"""Tests for agent-registry.sh — run standalone (`python3 test_agent_registry.py`)
or via scripts/check-structure.py's "plugin tests" check.

Guards the registry's contract: alias/name/cli selector resolution, the per-CLI
launch argv shape (claude `/work-system:continue` vs the codex/grok/kimi bootstrap
prompt, incl. kimi's two-phase seed+continue argv and its argument-order
regression; plus the optional cc-harness-agents PATH helper: list merge, the
`exec … -- claude …` resolve shape, clean degrade when absent, exit-3
capability-absent), the availability probe (codex login status + grok/kimi auth
file + grok/kimi model-list), the exit-code map (2 unknown selector, 3
resolved-but-unavailable), and the project-default state (set/get, bogus
rejection, no-git-repo error).

Availability is made deterministic with fake `codex`/`grok`/`kimi`/`claude`
stubs (and an optional `cc-harness-agents` stub) on a prepended PATH, so the
test does not depend on what is really installed/authed. The kimi stub also
logs every invocation's argv, so the resolved launch argv can be executed for
real and each phase asserted.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "agent-registry.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


class Env:
    """A throwaway HOME + fake-bin sandbox controlling CLI availability."""

    def __init__(self, codex_authed=True, grok_authed=True,
                 grok_models=("grok-4.5",), grok_models_ok=True,
                 kimi_authed=True, kimi_models=("kimi-code/k3-256k",),
                 kimi_models_ok=True, kimi_schema_ok=True,
                 harness_agents=None, harness_list_rc=0):
        # harness_agents: None → no helper on PATH (today's default).
        # Otherwise a list of (name, model, available, note) tuples the stub
        # prints as TSV. harness_list_rc=3 simulates "capability absent".
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        bindir = root / "bin"
        bindir.mkdir()
        # codex stub: `login status` exit reflects auth; anything else exits 0.
        codex_rc = 0 if codex_authed else 1
        (bindir / "codex").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "login" ] && [ "$2" = "status" ]; then exit %d; fi\n'
            "exit 0\n" % codex_rc
        )
        # grok stub: `models` prints a `grok models`-shaped list (drives the
        # model-level availability probe) and exits ok/non-ok to simulate a
        # reachable vs unreachable fetch; other calls just exit 0 (command -v).
        model_lines = "".join(
            '  echo "  * %s"\n' % m for m in grok_models
        )
        models_rc = 0 if grok_models_ok else 1
        (bindir / "grok").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "models" ]; then\n'
            "%s"
            "  exit %d\n"
            "fi\n"
            "exit 0\n" % (model_lines, models_rc)
        )
        # claude stub: only ever hit by `command -v`.
        (bindir / "claude").write_text("#!/bin/sh\nexit 0\n")
        # kimi stub: `provider list --json` drives the model-level probe (raw
        # JSON, substring-matched). Every invocation also appends its full argv
        # (one tab-joined line per call) to KIMI_ARGLOG, so a test can execute
        # the resolved launch argv for real and assert what each phase received.
        self.kimi_arglog = root / "kimi_args.log"
        # kimi_schema_ok=False simulates format drift: a valid but differently
        # shaped document (no `models` section) — distinct from a well-formed
        # listing that genuinely offers nothing.
        kimi_section = "models" if kimi_schema_ok else "aliases"
        kimi_model_json = ",".join('\\"%s\\": {}' % m for m in kimi_models)
        (bindir / "kimi").write_text(
            "#!/bin/sh\n"
            '[ -n "$KIMI_ARGLOG" ] && { printf \'%%s\\t\' "$@" >> "$KIMI_ARGLOG"; '
            'printf \'\\n\' >> "$KIMI_ARGLOG"; }\n'
            'if [ "$1" = "provider" ] && [ "$2" = "list" ]; then\n'
            '  echo "{\\"%s\\": {%s}}"\n'
            "  exit %d\n"
            "fi\n"
            "exit 0\n" % (kimi_section, kimi_model_json, 0 if kimi_models_ok else 1)
        )
        # Optional cc-harness-agents stub. The contract is four TSV columns
        # (name/model/available/note); names are already namespaced. `exec`
        # just re-execs the remaining argv so a resolved launch can be run.
        self.harness_bin = bindir / "cc-harness-agents"
        if harness_agents is not None:
            # `printf`, never `echo "…\t…"`: escape expansion by `echo` is not
            # POSIX-guaranteed, so on a host whose /bin/sh is bash without
            # xpg_echo (RHEL/Fedora/Amazon Linux) the row would arrive as ONE
            # literal field and every harness assertion below would fail — while
            # CI (ubuntu/dash) stayed green. The kimi stub already uses printf.
            rows = "".join(
                "  printf '%%s\\t%%s\\t%%s\\t%%s\\n' '%s' '%s' '%s' '%s'\n"
                % (n, m, a, note)
                for (n, m, a, note) in harness_agents
            )
            (self.harness_bin).write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "list" ]; then\n'
                "%s"
                "  exit %d\n"
                "fi\n"
                'if [ "$1" = "exec" ]; then\n'
                '  shift\n'                          # drop "exec"
                '  [ "$2" = "--" ] && shift 2 || shift\n'  # name, optional --
                '  exec "$@"\n'
                "fi\n"
                "exit 2\n" % (rows, harness_list_rc)
            )
        for f in bindir.iterdir():
            f.chmod(0o755)
        # grok auth file toggles grok readiness.
        self.grok_auth = root / "grok_auth.json"
        if grok_authed:
            self.grok_auth.write_text("{}\n")
        # kimi's real tokens live in credentials/, not the same-named oauth/ dir.
        self.kimi_creds = root / "kimi_credentials.json"
        if kimi_authed:
            self.kimi_creds.write_text("{}\n")
        self.project_state = root / "repo" / ".claude" / "work-system-agent"

        self.env = dict(os.environ)
        self.env["PATH"] = f"{bindir}:{self.env['PATH']}"
        self.env["HOME"] = str(self.home)
        self.env["GROK_AUTH_FILE"] = str(self.grok_auth)
        self.env["KIMI_CREDENTIALS_FILE"] = str(self.kimi_creds)
        self.env["WORK_SYSTEM_AGENT_PROJECT_STATE"] = str(self.project_state)
        # Always pin the harness seam, exactly like the auth/state overrides
        # above. Without this the "no helper" cases keep the host PATH, so a
        # contributor who actually HAS cc-harness-agents installed (i.e. the very
        # audience for this feature) would run those assertions against their
        # real helper. `harness_agents=None` points at a path guaranteed absent.
        self.env["WORK_SYSTEM_CC_HARNESS_AGENTS"] = (
            str(self.harness_bin) if harness_agents is not None
            else str(root / "no-such-cc-harness-agents")
        )

    def run(self, *args, project_state=True):
        env = dict(self.env)
        if not project_state:
            # Force the "no project config location" path: drop the override and
            # run from a non-git cwd so `git rev-parse` finds no repo root.
            env.pop("WORK_SYSTEM_AGENT_PROJECT_STATE", None)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env, cwd=str(self.home), capture_output=True, text=True,
        )

    def run_argv(self, argv):
        """Execute a resolved launch argv against the stubs; return the per-call
        argv lines the kimi stub recorded. The CompletedProcess is kept on
        `self.last_proc` so a test can also assert the wrapper's exit code and
        output (the seed-failure contract is exactly that)."""
        env = dict(self.env)
        env["KIMI_ARGLOG"] = str(self.kimi_arglog)
        self.kimi_arglog.write_text("")
        self.last_proc = subprocess.run(
            argv, env=env, cwd=str(self.home), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30)
        return [line.split("\t")[:-1]
                for line in self.kimi_arglog.read_text().splitlines()]

    def close(self):
        self.tmp.cleanup()


def kv(out):
    """Parse resolve's key=value lines; argv lines collect into a list."""
    d = {"argv": []}
    for line in out.splitlines():
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "argv":
            d["argv"].append(v)
        else:
            d[k] = v
    return d


# --- resolve: selector mapping + argv shape -------------------------------- #
e = Env()

r = kv(e.run("resolve", "--fable", "--session", "close-herdr").stdout)
check("--fable -> claude:fable", r.get("name") == "claude:fable")
check("--fable cli", r.get("cli") == "claude")
check("claude argv shape",
      r["argv"] == ["claude", "--model", "fable", "-n", "close-herdr", "/work-system:continue"])
check("claude supports lifecycle", "continue" in r.get("supports", ""))

r = kv(e.run("resolve", "--opus").stdout)
check("--opus -> claude:opus", r.get("name") == "claude:opus")
check("claude argv without session omits -n",
      r["argv"] == ["claude", "--model", "opus", "/work-system:continue"])

r = kv(e.run("resolve", "--sol").stdout)
check("--sol -> codex:gpt-5.6-sol", r.get("name") == "codex:gpt-5.6-sol")
check("codex argv shape",
      r["argv"][:3] == ["codex", "-m", "gpt-5.6-sol"])
check("codex bootstrap prompt is one argv word", len(r["argv"]) == 4)
check("codex bootstrap mentions TASK.md", "TASK.md" in r["argv"][3])
check("codex supports commit,pr only", r.get("supports") == "commit,pr")

r = kv(e.run("resolve", "--grok").stdout)
check("--grok -> grok:grok-4.5", r.get("name") == "grok:grok-4.5")
check("grok argv shape", r["argv"][:3] == ["grok", "-m", "grok-4.5"])

# --- kimi: the two-phase seed+continue launch argv ------------------------- #
# kimi has no positional launch prompt and `-p` cannot be combined with --auto/-y,
# so a worker is `-p` (seed, runs tools unattended) then `exec kimi -c --auto`
# (interactive + autonomous, inheriting the seed's session history).
SEED_MARKER = "WORKER_SEED_FAILED"
KIMI_SCRIPT = (
    'if kimi -m "$1" -p "$2"; then exec kimi -c --auto; else rc=$?; echo; '
    f'echo "[work-system] {SEED_MARKER}: kimi seed exited $rc - '
    'TASK.md was NOT started and no session was opened."; exit $rc; fi'
)

r = kv(e.run("resolve", "--kimi").stdout)
check("--kimi -> kimi:kimi-code/k3-256k", r.get("name") == "kimi:kimi-code/k3-256k")
check("kimi model is the QUALIFIED alias (bare name aborts at startup)",
      r.get("model") == "kimi-code/k3-256k")
check("kimi supports commit,pr only", r.get("supports") == "commit,pr")
# Exact word list — the whole point of this test.
check("kimi argv shape",
      r["argv"][:5] == ["sh", "-c", KIMI_SCRIPT, "kimi-worker", "kimi-code/k3-256k"])
check("kimi argv is exactly 6 words", len(r["argv"]) == 6)
check("kimi bootstrap is the last word and mentions TASK.md",
      "TASK.md" in r["argv"][5])

# Argument-order regression: `-p <value>` consumes the NEXT token, so an argv
# built by concatenation can silently swallow a flag (`kimi -p --auto "…"` ->
# --auto becomes the prompt and the prompt becomes an unknown subcommand). Two
# structural guarantees prevent that, and both are asserted:
#   1. the model and the prompt are passed as positionals, never spliced into
#      the script text (so their content cannot reorder anything), and
#   2. --auto lives in a different command than -p (after the `;`), so it can
#      never land in -p's value position.
script = r["argv"][2]
check("model is not spliced into the script text", "kimi-code/k3-256k" not in script)
# Compare against the actual prompt word, not a substring of it: the script text
# legitimately mentions TASK.md in its seed-failure message.
check("prompt is not spliced into the script text", r["argv"][5] not in script)
check("-p takes the positional as its value", '-p "$2"' in script)
check("--auto is only reachable from the SUCCESS branch (never in -p's value slot)",
      "--auto" not in script.split("then", 1)[0])
check("no bare -p/--prompt argv word (it stays bound inside the script)",
      "-p" not in r["argv"] and "--prompt" not in r["argv"])
# A failed seed must be unambiguous: a stable marker, an explicit "not started", no
# phase 2, and no wait for a keypress nobody is there to press. The marker is
# human-facing only — see the note further down and herdr-launch.sh.
# The literal token must NOT be in the script text: a pane echoes the command it
# was given, so a launcher grepping that pane for the marker would "detect" a seed
# failure the moment it typed the command — killing a healthy launch (found live).
# The script assembles the token at runtime instead; the executed-output assertions
# further down prove it still reaches the screen intact.
# The marker is for the human reading the tab, not a machine signal — the launcher
# detects a dead wrapper from process state (see herdr-launch.sh), so nothing here
# needs to be greppable, salted, or assembled at runtime to dodge a grep.
check("the marker is a plain literal, with no launcher-side plumbing",
      SEED_MARKER in script and "SEED_TOKEN" not in script and "${m}_" not in script)
check("seed failure states TASK.md was not started", "TASK.md was NOT started" in script)
check("seed failure does not wait for input", "read " not in script)
check("phase 2 is gated behind the seed's success",
      script.startswith("if kimi ") and "then exec kimi -c --auto" in script)
check("seed failure exits with the seed's own code", "exit $rc" in script)

# Execute the resolved argv for real against the stub and assert what each phase
# actually received — string checks alone can't prove the shell binds the values
# the way we think it does.
calls = e.run_argv(r["argv"])
check("kimi launch runs exactly two phases", len(calls) == 2)
if len(calls) == 2:
    seed, cont = calls
    check("phase 1 is the -p seed with the model and prompt intact",
          seed[:3] == ["-m", "kimi-code/k3-256k", "-p"] and "TASK.md" in seed[3])
    check("phase 1 got exactly 4 args (nothing swallowed, nothing extra)",
          len(seed) == 4)
    check("phase 1 never carries --auto/-y (mutually exclusive with -p)",
          "--auto" not in seed and "-y" not in seed)
    check("phase 2 is the interactive autonomous continue", cont == ["-c", "--auto"])

# A FAILING seed must be a hard, self-announcing stop: marker on stdout, the
# seed's own exit code, and NO phase 2 — an empty `kimi -c --auto` session that
# never read TASK.md is indistinguishable from a healthy worker to herdr's
# detection (and to the user), which is exactly the trap this replaces. Runs the
# real resolved argv against a stub whose non-`provider` calls exit 7, so code
# propagation is asserted on a value nothing else could produce.
e_fail = Env()
(Path(e_fail.env["PATH"].split(":")[0]) / "kimi").write_text(
    "#!/bin/sh\n"
    '[ -n "$KIMI_ARGLOG" ] && { printf \'%s\\t\' "$@" >> "$KIMI_ARGLOG"; '
    'printf \'\\n\' >> "$KIMI_ARGLOG"; }\n'
    'if [ "$1" = "provider" ]; then echo \'{"models": {"kimi-code/k3-256k": {}}}\'; exit 0; fi\n'
    "exit 7\n"
)
(Path(e_fail.env["PATH"].split(":")[0]) / "kimi").chmod(0o755)
fail_calls = e_fail.run_argv(kv(e_fail.run("resolve", "--kimi").stdout)["argv"])
check("a failed seed runs the seed and NOTHING else", len(fail_calls) == 1)
if fail_calls:
    check("the one call was the -p seed", "-p" in fail_calls[0])
check("no phase-2 `-c --auto` after a failed seed",
      ["-c", "--auto"] not in fail_calls)
check("the seed's exit code propagates out of the wrapper",
      e_fail.last_proc.returncode == 7)
check("the wrapper prints the machine-readable marker",
      SEED_MARKER in e_fail.last_proc.stdout)
check("the marker line names the failing seed's code",
      "exited 7" in e_fail.last_proc.stdout)
e_fail.close()

# --- herdr transport metadata (modern `agent start --kind --pane`) --------- #
# The launcher must not infer transport from a selector name or by parsing argv[0]
# — every entry declares it. Native CLIs are `agent-start` with a kind that EQUALS
# their argv[0] (the launcher drops that word and hands the rest to --kind); kimi's
# wrapper is `pane-run` and carries the seed-failure marker.
for sel, mode, kind in (("--fable", "agent-start", "claude"),
                        ("--opus", "agent-start", "claude"),
                        ("claude:sonnet", "agent-start", "claude"),
                        ("--codex", "agent-start", "codex"),
                        ("--sol", "agent-start", "codex"),
                        ("--grok", "agent-start", "grok"),
                        ("--kimi", "pane-run", "kimi")):
    t = kv(e.run("resolve", sel).stdout)
    check(f"{sel}: herdr_mode={mode}", t.get("herdr_mode") == mode)
    check(f"{sel}: herdr_kind={kind}", t.get("herdr_kind") == kind)
    if mode == "agent-start":
        check(f"{sel}: argv[0] equals the declared kind (no rebuild needed)",
              t["argv"][0] == kind)
    else:
        check(f"{sel}: wrapper argv[0] is NOT the kind (needs pane-run)",
              t["argv"][0] != kind)
# `supports` must not absorb the new trailing fields (field-order regression).
check("supports is unchanged by the added transport columns",
      kv(e.run("resolve", "--grok").stdout).get("supports") == "commit,pr")

# canonical name and bare-cli-default selectors
check("name selector claude:sonnet",
      kv(e.run("resolve", "claude:sonnet").stdout).get("model") == "sonnet")
check("bare cli codex -> default terra",
      kv(e.run("resolve", "codex").stdout).get("name") == "codex:gpt-5.6-terra")

# unknown selector -> exit 2
u = e.run("resolve", "--nope")
check("unknown selector exit 2", u.returncode == 2)
check("unknown selector hint", "Unknown agent selector" in u.stderr)

# missing selector -> exit 2
check("resolve no selector exit 2", e.run("resolve").returncode == 2)
e.close()

# --- availability probe + exit 3 ------------------------------------------- #
e = Env(codex_authed=False, grok_authed=False)
rows = json.loads(e.run("list", "--json").stdout)
by = {row["name"]: row for row in rows}
check("claude always available", by["claude:fable"]["available"] is True)
check("codex unauthed -> unavailable", by["codex:gpt-5.6-sol"]["available"] is False)
check("codex note is login hint", "codex login" in by["codex:gpt-5.6-sol"]["note"])
check("grok no auth -> unavailable", by["grok:grok-4.5"]["available"] is False)

res = e.run("resolve", "--sol")
check("resolve unavailable -> exit 3", res.returncode == 3)
rr = kv(res.stdout)
check("resolve unavailable still prints argv", len(rr["argv"]) == 4)
check("resolve unavailable available=no", rr.get("available") == "no")
e.close()

# --- grok model-level availability (gated on `grok models`) ---------------- #
# grok authed + `grok models` lists grok-4.5 -> available.
e = Env(grok_models=("grok-4.5",))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok-4.5 listed -> available", by["grok:grok-4.5"]["available"] is True)
check("resolve --grok available -> exit 0", e.run("resolve", "--grok").returncode == 0)
e.close()

# grok authed but the model is NOT in `grok models` (a model dropped/renamed
# between releases) -> unavailable at probe time, so the launch is refused
# cleanly instead of erroring at runtime with "unknown model id". Data-driven,
# not a hardcoded drop.
e = Env(grok_models=("grok-9.9-imaginary",))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok-4.5 not listed -> unavailable", by["grok:grok-4.5"]["available"] is False)
check("unlisted-model note mentions the model list",
      "grok models" in by["grok:grok-4.5"]["note"])
check("resolve --grok unavailable -> exit 3", e.run("resolve", "--grok").returncode == 3)
e.close()

# grok authed but `grok models` fetch FAILS (unreachable/offline/timed out) ->
# inconclusive, not a drop: trust auth so a network hiccup can't wrongly block a
# launch. (A global flag can't carry this out of the command-substitution
# subshell, so the fetch status must ride the function's exit code.)
e = Env(grok_models=(), grok_models_ok=False)
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok models unreachable -> assumed available", by["grok:grok-4.5"]["available"] is True)
check("unreachable note is soft", "unreachable" in by["grok:grok-4.5"]["note"])
check("resolve --grok available when fetch fails", e.run("resolve", "--grok").returncode == 0)
e.close()

# grok `models` SUCCEEDS (exit 0) but the parser extracts nothing (a reformatted
# listing that dropped the `*` bullet) -> inconclusive, NOT "model gone": trust
# auth so a format-drift release doesn't silently disable the whole grok backend.
e = Env(grok_models=(), grok_models_ok=True)
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("grok models empty-but-ok -> assumed available", by["grok:grok-4.5"]["available"] is True)
check("empty note is soft", "assumed" in by["grok:grok-4.5"]["note"])
e.close()

# --- kimi model-level availability (same contract as grok) ----------------- #
# Model-aware for a sharper reason than grok's: an unconfigured `-m` id aborts
# kimi at startup, so a stale model would hand the user a tab that dies on sight.
e = Env(kimi_models=("kimi-code/k3-256k",))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("kimi model listed -> available", by["kimi:kimi-code/k3-256k"]["available"] is True)
check("resolve --kimi available -> exit 0", e.run("resolve", "--kimi").returncode == 0)
e.close()

# authed, but the registry's model is not in the provider listing -> refuse now.
e = Env(kimi_models=("kimi-code/k9-imaginary",))
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("kimi model not listed -> unavailable", by["kimi:kimi-code/k3-256k"]["available"] is False)
check("kimi unlisted note points at the listing",
      "kimi provider list" in by["kimi:kimi-code/k3-256k"]["note"])
check("resolve --kimi unavailable -> exit 3", e.run("resolve", "--kimi").returncode == 3)
e.close()

# no credentials file -> logged out. (Probing ~/.kimi-code/oauth/ instead would
# report every authenticated install as logged out: that file stays 0 bytes.)
e = Env(kimi_authed=False)
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("kimi unauthed -> unavailable", by["kimi:kimi-code/k3-256k"]["available"] is False)
check("kimi note is login hint", "kimi login" in by["kimi:kimi-code/k3-256k"]["note"])
e.close()

# listing unreachable / empty-but-ok -> inconclusive, trust auth (mirrors grok).
e = Env(kimi_models=(), kimi_models_ok=False)
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("kimi listing unreachable -> assumed available",
      by["kimi:kimi-code/k3-256k"]["available"] is True)
check("kimi unreachable note is soft", "unreachable" in by["kimi:kimi-code/k3-256k"]["note"])
e.close()

# A well-formed listing that offers NO models is a real answer, not drift ->
# unavailable (the launch would abort at startup anyway).
e = Env(kimi_models=(), kimi_models_ok=True)
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("kimi listing with zero models -> unavailable",
      by["kimi:kimi-code/k3-256k"]["available"] is False)
e.close()

# Schema drift (the `models` section renamed/moved) is NOT a real answer: the
# match would fail for a reason that says nothing about the model, so trust auth
# rather than silently disabling the whole kimi backend on a format change.
e = Env(kimi_schema_ok=False)
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("kimi schema drift -> assumed available",
      by["kimi:kimi-code/k3-256k"]["available"] is True)
check("kimi drift note is soft", "assumed" in by["kimi:kimi-code/k3-256k"]["note"])
e.close()

# --- project default (the only persisted state) ---------------------------- #
e = Env()
# nothing set -> empty (no-flag /kickoff then shows the picker)
check("no default set -> empty", e.run("default", "get").stdout.strip() == "")
# set -> get round-trips, and it lands in the project state file
e.run("default", "set", "codex:gpt-5.6-sol")
check("default set persisted", e.run("default", "get").stdout.strip() == "codex:gpt-5.6-sol")
check("default lives in the project file",
      "default=codex:gpt-5.6-sol" in e.project_state.read_text())
# overwriting replaces it
e.run("default", "set", "claude:opus")
check("default overwrite", e.run("default", "get").stdout.strip() == "claude:opus")
# bogus name rejected
check("bogus default rejected", e.run("default", "set", "bogus:model").returncode == 2)
# `default get` VALIDATES the stored value: a committed default that no longer
# maps to a real entry (stale/removed/attacker-supplied from a cloned repo) is
# treated as "no default" -> empty -> caller shows the picker, never routes on it.
e.project_state.write_text("default=grok:composer\n")   # a removed name
check("stale committed default -> empty", e.run("default", "get").stdout.strip() == "")
e.project_state.write_text("default=--grok\n")           # a flag, not a name
check("flag as committed default -> empty", e.run("default", "get").stdout.strip() == "")
e.project_state.write_text("default=claude:opus\n")      # valid again
check("valid committed default -> returned", e.run("default", "get").stdout.strip() == "claude:opus")
# no project location (not a git repo, no override) -> clear error, exit 2
r = e.run("default", "set", "claude:opus", project_state=False)
check("no project location -> exit 2", r.returncode == 2)
check("no project location message", "no project config location" in r.stderr)
e.close()

# --- removed subcommands (auto / rank / last are gone) --------------------- #
e = Env()
check("auto removed -> exit 2", e.run("auto").returncode == 2)
check("rank removed -> exit 2", e.run("rank").returncode == 2)
check("last removed -> exit 2", e.run("last", "get").returncode == 2)
e.close()

# --- optional cc-harness-agents PATH helper -------------------------------- #
# Absent helper → today's behaviour: one `command -v`, no extra rows, and a
# cc-harness:… selector is unknown (exit 2). Never hardcodes a gateway.
e = Env(harness_agents=None)
rows = json.loads(e.run("list", "--json").stdout)
check("no helper → no harness rows",
      all(not r["name"].startswith("cc-harness:") for r in rows))
check("native rows still present without helper",
      any(r["name"] == "claude:fable" for r in rows))
u = e.run("resolve", "cc-harness:grok")
check("no helper → cc-harness selector exit 2", u.returncode == 2)
check("no helper → hint mentions the helper",
      "cc-harness-agents" in u.stderr)
e.close()

# Present helper → merge its rows (incl. a name the plugin has never heard of)
# and resolve to `cc-harness-agents exec <id> -- claude … /work-system:continue`.
# No --model: the helper sets ANTHROPIC_MODEL via env before exec'ing.
HARNESS_ROWS = [
    ("cc-harness:grok", "grok-4.5", "yes", "-"),
    ("cc-harness:kimi", "kimi-k3", "no", "run: cliproxyapi -kimi-login"),
    # Agent the plugin has never shipped a row for — whatever list prints wins.
    ("cc-harness:sol", "gpt-5.6-sol", "yes", "-"),
    ("cc-harness:never-seen", "imaginary-9.9", "yes", "-"),
]
e = Env(harness_agents=HARNESS_ROWS)
rows = json.loads(e.run("list", "--json").stdout)
by = {r["name"]: r for r in rows}
check("helper merges cc-harness:grok", "cc-harness:grok" in by)
check("helper merges never-seen agent (no per-agent code)",
      "cc-harness:never-seen" in by)
check("harness cli column is cc-harness", by["cc-harness:grok"]["cli"] == "cc-harness")
check("harness model comes from the helper",
      by["cc-harness:grok"]["model"] == "grok-4.5")
check("harness available=yes from helper", by["cc-harness:grok"]["available"] is True)
check("harness available=no from helper (no re-probe)",
      by["cc-harness:kimi"]["available"] is False)
check("harness note is the helper's fix hint",
      "kimi-login" in by["cc-harness:kimi"]["note"])
check("native rows still present with helper", "claude:fable" in by)

r = kv(e.run("resolve", "cc-harness:grok", "--session", "close-herdr").stdout)
check("resolve harness name", r.get("name") == "cc-harness:grok")
check("resolve harness cli", r.get("cli") == "cc-harness")
check("resolve harness model is the real model (display)",
      r.get("model") == "grok-4.5")
check("resolve harness supports full CC lifecycle",
      "continue" in r.get("supports", "") and "close-exit" in r.get("supports", ""))
# Transport: a harness entry is the "dynamically-registered wrapper" the metadata
# was designed for. argv[0] is the HELPER, not herdr's canonical `claude`, so
# agent-start (which asserts argv[0] == kind) cannot express it — it must be
# pane-run, and the kind is `claude` because the helper execs into claude.
check("harness declares pane-run transport", r.get("herdr_mode") == "pane-run")
check("harness kind is claude (what the helper execs into)",
      r.get("herdr_kind") == "claude")
check("harness argv[0] is NOT the kind (why agent-start is impossible)",
      r["argv"][0] != "claude" and "claude" in r["argv"])
# argv[0] is HARNESS_BIN — here the test seam's path, in production the bare
# `cc-harness-agents` name resolved from PATH (same shape as every other worker,
# which the registry also invokes by bare name).
HB = str(e.harness_bin)
check("resolve harness argv shape",
      r["argv"] == [
          HB, "exec", "grok", "--",
          "claude", "-n", "close-herdr", "/work-system:continue",
      ])
check("resolve harness has no --model (helper owns it)",
      "--model" not in r["argv"])
check("resolve harness argv_shell is set",
      "argv_shell" in r and "exec grok -- claude" in r["argv_shell"])
check("resolve available harness -> exit 0",
      e.run("resolve", "cc-harness:grok").returncode == 0)

# Never-seen agent resolves the same way — no plugin-side allow-list.
r = kv(e.run("resolve", "cc-harness:never-seen").stdout)
check("never-seen harness argv uses bare id",
      r["argv"][:4] == [HB, "exec", "never-seen", "--"])
check("never-seen still lands on claude + qualified continue",
      r["argv"][-2:] == ["claude", "/work-system:continue"])

# Listed-but-unavailable still emits argv and exits 3 (mirrors native).
res = e.run("resolve", "cc-harness:kimi")
check("unavailable harness -> exit 3", res.returncode == 3)
rr = kv(res.stdout)
check("unavailable harness still prints argv",
      rr["argv"][:4] == [HB, "exec", "kimi", "--"])
check("unavailable harness available=no", rr.get("available") == "no")
check("unavailable harness note is the fix hint",
      "kimi-login" in rr.get("note", ""))

# Unknown harness id (helper present, name not listed) → exit 2.
u = e.run("resolve", "cc-harness:nope")
check("unknown harness id -> exit 2", u.returncode == 2)

# The PICKER reads the human table (`list`, no --json), so it must carry every
# row too. Regression: `$( )` strips the trailing newline off the merged harness
# block, and a plain `while read` then drops the LAST line — silently, and only
# in the table, since the --json path parses a newline-less final line fine. The
# dropped row is an agent the user can never pick, so assert the table directly.
table = e.run("list").stdout
for nm in ("cc-harness:grok", "cc-harness:kimi", "cc-harness:sol",
           "cc-harness:never-seen"):
    check("human table lists %s (last-row regression)" % nm, nm in table)
check("human table still lists native rows", "kimi:kimi-code/k3-256k" in table)
check("human table has no blank data line",
      all(line.strip() for line in table.splitlines()))
# Table and JSON must agree on the row set — neither view may silently drop one.
check("table and --json agree on row count",
      len(table.strip().splitlines()) - 1  # minus the header
      == len(json.loads(e.run("list", "--json").stdout)))

# A committed harness default is accepted when the helper lists it, and falls
# through to "no default" (→ picker) when the helper is gone — same validation
# path as a stale native name.
e.run("default", "set", "cc-harness:sol")
check("harness default set/get",
      e.run("default", "get").stdout.strip() == "cc-harness:sol")
e.close()

# Helper present but capability absent (exit 3, no token) → silent degrade,
# no harness rows, native list untouched. Distinct from "provider not logged in".
e = Env(harness_agents=[("cc-harness:grok", "grok-4.5", "yes", "-")],
        harness_list_rc=3)
rows = json.loads(e.run("list", "--json").stdout)
check("capability-absent → no harness rows",
      all(not r["name"].startswith("cc-harness:") for r in rows))
check("capability-absent still lists native",
      any(r["name"] == "claude:fable" for r in rows))
check("capability-absent resolve is unknown (not exit 3)",
      e.run("resolve", "cc-harness:grok").returncode == 2)
e.close()

# Helper's "unknown" available cell maps to no (fail closed for launch).
e = Env(harness_agents=[("cc-harness:grok", "grok-4.5", "unknown", "probe inconclusive")])
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("harness available=unknown → treated as no",
      by["cc-harness:grok"]["available"] is False)
check("resolve unknown-avail harness -> exit 3",
      e.run("resolve", "cc-harness:grok").returncode == 3)
e.close()

# `list` and `resolve` must agree on which rows EXIST. A helper row that forgot
# the namespace is rejected by the list builder; resolve must reject it too —
# otherwise it is invisible in list/--json/the picker yet still launchable AND
# storable as a committed repo default, breaking "whatever list prints is what
# you can choose".
e = Env(harness_agents=[("grok", "grok-4.5", "yes", "-")])
rows = json.loads(e.run("list", "--json").stdout)
check("non-namespaced helper row not listed",
      all(not r["name"].startswith("cc-harness") and r["cli"] != "cc-harness"
          for r in rows))
check("non-namespaced row does not resolve",
      e.run("resolve", "cc-harness:grok").returncode == 2)
check("non-namespaced row rejected as project default",
      e.run("default", "set", "cc-harness:grok").returncode == 2)
e.close()

# An EMPTY middle cell must not shift the remaining columns. `IFS=$'\t' read`
# collapses consecutive tabs (tab is IFS whitespace), which silently turned an
# empty model into model="yes"/avail="-" — fail-closing a working agent.
e = Env(harness_agents=[("cc-harness:x", "", "yes", "-")])
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("empty model cell does not shift columns",
      by["cc-harness:x"]["model"] == "" and by["cc-harness:x"]["available"] is True)
check("resolve with empty model stays available",
      e.run("resolve", "cc-harness:x").returncode == 0)
e.close()

# The mirror case: an empty `available` cell must read as NOT available, not
# borrow the note's text. This is what makes harness_map_avail's fail-closed
# rule actually hold.
e = Env(harness_agents=[("cc-harness:y", "m", "", "yes")])
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
check("empty available cell → unavailable (note must not shift in)",
      by["cc-harness:y"]["available"] is False)
e.close()

# Helper fields are untrusted: control characters (ANSI escapes, embedded
# newlines that could forge extra key=value lines in resolve's output) must be
# stripped before the text reaches the picker or a resolve consumer.
e = Env(harness_agents=[
    ("cc-harness:z", "m", "no", "run: \033[31mfix\033[0m\\nname=cc-harness:evil"),
])
by = {r["name"]: r for r in json.loads(e.run("list", "--json").stdout)}
note = by["cc-harness:z"]["note"]
check("helper note keeps its readable text", "run:" in note and "fix" in note)
check("helper note has no control characters",
      not any(ord(c) < 32 or ord(c) == 127 for c in note))
res = e.run("resolve", "cc-harness:z")
check("sanitized note cannot forge extra resolve keys",
      len([ln for ln in res.stdout.splitlines() if ln.startswith("name=")]) == 1)
e.close()


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("agent-registry.sh: all tests passed")
