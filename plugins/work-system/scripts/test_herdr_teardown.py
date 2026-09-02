#!/usr/bin/env python3
"""Tests for herdr-teardown.sh manager-session — the Manager detection that
gates /close delegation.

The subcommand has no env seam: it goes through the real `ha_list` wrapper, so
these tests put a STUB `herdr` on PATH (the pattern test_lanes.py uses for its
integration case) and drive the whole shell+python path end to end.

The contract under test is TRI-STATE and fail-closed:
    name=<session-name>   exactly one live claude agent sits at the repo root
    none                  a populated, fully readable list has none there
    unverified            anything we cannot rule out — malformed/empty list,
                          two candidates, a non-claude or not-live root agent,
                          an unreadable cwd, no derivable name, tools missing
A wrong `none` only costs the delegation offer; a wrong `name=` would send a
close request to a stranger session — hence every doubt lands on unverified.
"""
import json as jsonlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "herdr-teardown.sh"
BASH = shutil.which("bash") or "/bin/bash"   # absolute: the no-herdr case strips PATH

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


ROOT = "/teardown-test-root"        # fake, need not exist — realpath just normalizes
WT = f"{ROOT}/.claude/worktrees"
WS = "w7"


def agent(cwd, *, kind="claude", status="idle", title="Manager", ws=WS, **extra):
    a = {"agent": kind, "agent_status": status, "cwd": cwd,
         "pane_id": "w7:p1", "tab_id": "w7:t1"}
    if ws is not None:          # ws=None omits the field, as a malformed herdr row would
        a["workspace_id"] = ws
    if title is not None:
        a["terminal_title_stripped"] = title
    a.update(extra)
    return a


def run(agents, *, ws=WS, main=ROOT, raw=None, herdr_rc=0, no_herdr=False):
    """Run `manager-session <ws> <main>` against a stub herdr. `agents` is a
    list of agent dicts (wrapped in the herdr envelope); `raw` overrides the
    stub payload verbatim (for malformed JSON)."""
    env = dict(os.environ)
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)
    bindir = d / "bin"
    bindir.mkdir()
    payload = raw if raw is not None else jsonlib.dumps({"result": {"agents": agents}})
    stub = bindir / "herdr"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1 $2" = "agent list" ]; then\n'
        f"  cat <<'JSON'\n{payload}\nJSON\n"
        f"  exit {herdr_rc}\n"
        "fi\n"
        "exit 9\n"
    )
    stub.chmod(0o755)
    if no_herdr:
        # A PATH with python3/dirname but NO herdr — the tools-absent degrade.
        stub.unlink()
        for tool in ("python3", "dirname", "bash"):
            src = shutil.which(tool)
            if src:
                (bindir / tool).symlink_to(src)
        env["PATH"] = str(bindir)
    else:
        env["PATH"] = f"{bindir}:{env['PATH']}"
    r = subprocess.run([BASH, str(SCRIPT), "manager-session", ws, main],
                       env=env, capture_output=True, text=True, timeout=30)
    tmp.cleanup()
    return r


def out(r):
    return r.stdout.strip()


# --- happy path: one live claude agent AT the repo root -------------------- #
r = run([agent(ROOT), agent(f"{WT}/alpha", title="alpha")])
check("root claude agent → name=", out(r) == "name=Manager")
check("happy path exits 0", r.returncode == 0)

# the derived name comes from the TERMINAL TITLE (the claude session name), not
# from herdr's own agent name — they differ in practice.
r = run([agent(ROOT, title="repo-manager", name="mgr-tab")])
check("title wins over herdr agent name", out(r) == "name=repo-manager")

# herdr leaves the working-spinner glyph in the title; we strip one symbol+space.
r = run([agent(ROOT, title="◐ Manager")])
check("leading spinner glyph stripped", out(r) == "name=Manager")
r = run([agent(ROOT, title="✳ My Repo Manager")])
check("glyph strip keeps inner spaces", out(r) == "name=My Repo Manager")
# …but the strip REQUIRES the space, so a punctuation-led name survives intact.
r = run([agent(ROOT, title="/habemus-agentem")])
check("punctuation-led name kept (no space → no strip)", out(r) == "name=/habemus-agentem")

# fallback: the unstripped title (herdr omits the stripped field on some rows)
r = run([agent(ROOT, title=None, terminal_title="✳ From Title")])
check("falls back to terminal_title", out(r) == "name=From Title")
# herdr's agent `name` is a launch label, NOT the session address — it must never
# be emitted as a candidate (a namesake in another repo would pass the caller uniqueness check).
r = run([agent(ROOT, title=None, name="from-agent-name")])
check("herdr agent name is NOT used as an address", out(r) == "unverified")

# --- confident `none`: a populated, readable list with nobody at the root --- #
r = run([agent(f"{WT}/alpha", title="alpha"), agent(f"{WT}/beta", title="beta")])
check("only worktree agents → none", out(r) == "none")
r = run([agent(f"{ROOT}/subdir", title="sub")])
check("a SUBDIR of the root is not the root → none", out(r) == "none")
r = run([agent("/somewhere/else", title="other")])
check("unrelated repo → none", out(r) == "none")

# --- ambiguity and unusable candidates → unverified ------------------------ #
r = run([agent(ROOT, title="Manager"), agent(ROOT, title="Manager 2", pane_id="w7:p2")])
check("TWO root agents → unverified (never guess)", out(r) == "unverified")
r = run([agent(ROOT, kind="codex", title="codex-mgr")])
check("non-claude at the root → unverified", out(r) == "unverified")
r = run([agent(ROOT, status="unknown")])
check("status unknown at the root → unverified", out(r) == "unverified")
r = run([agent(ROOT, status="")])
check("empty status at the root → unverified", out(r) == "unverified")
r = run([agent(ROOT, title="", terminal_title="", name="")])
check("no derivable name → unverified", out(r) == "unverified")
r = run([agent(ROOT, title="   ", terminal_title="\u202e")])
check("a title of only blanks/control chars → unverified", out(r) == "unverified")
r = run([agent(ROOT, title="x" * 201)])
check("absurdly long name → unverified", out(r) == "unverified")
# The cap is a prompt-injection guard, not a formatting nicety: the value reaches the
# caller prompt before any gate runs, so prose-length titles must not pass as an address.
r = run([agent(ROOT, title="x" * 65)])
check("title over the 64-char cap → unverified", out(r) == "unverified")
r = run([agent(ROOT, title="x" * 64)])
check("title at exactly 64 chars still passes", out(r) == "name=" + "x" * 64)

# a live claude Manager is found in each of the four LIVE states
for st in ("idle", "working", "blocked", "done"):
    r = run([agent(ROOT, status=st)])
    check(f"status {st} counts as live", out(r) == "name=Manager")

# --- unreadable / junk rows poison the answer (fail closed) ---------------- #
r = run([agent(ROOT), agent("", title="cwd-less")])
check("an agent with an empty cwd → unverified", out(r) == "unverified")
r = run([agent(f"{WT}/alpha", title="alpha"), None])
check("a junk (non-dict) element → unverified", out(r) == "unverified")

# --- list-level degradation ------------------------------------------------ #
r = run([])
check("empty agent list (repopulating) → unverified", out(r) == "unverified")
check("empty list still exits 0", r.returncode == 0)
r = run(None, raw="broken{")
check("malformed JSON → unverified", out(r) == "unverified")
r = run(None, raw=jsonlib.dumps({"result": {"agents": "nope"}}))
check("agents not a list → unverified", out(r) == "unverified")
r = run([agent(ROOT)], herdr_rc=1)
check("herdr call fails → unverified", out(r) == "unverified")
r = run([agent(ROOT)], no_herdr=True)
check("herdr not on PATH → unverified", out(r) == "unverified")
check("tools-absent still exits 0", r.returncode == 0)

# --- workspace scoping ----------------------------------------------------- #
r = run([agent(ROOT, ws="w9")])
check("root agent in another workspace → none", out(r) == "none")
# A row AT THE ROOT with no readable workspace_id must not be filtered away silently —
# that would let the answer read `none` where the contract requires fail-closed.
r = run([agent(ROOT, ws=None)])
check("root agent with no workspace_id → unverified", out(r) == "unverified")
r = run([agent(f"{WT}/alpha", title="alpha", ws=None)])
check("non-root row with no workspace_id is irrelevant → none", out(r) == "none")
r = run([agent(ROOT, ws="w9")], ws="")
check("empty workspace searches all workspaces", out(r) == "name=Manager")
r = run([agent(ROOT, ws="w9", title="other-ws"), agent(ROOT, title="mine")], ws=WS)
check("scoping disambiguates two root agents", out(r) == "name=mine")

# --- untrusted fields: a title cannot forge extra output lines ------------- #
r = run([agent(ROOT, title="Manager\nnone")])
check("newline in the title is scrubbed", out(r) == "name=Manager none")
check("output stays a single line", len(r.stdout.strip().splitlines()) == 1)
# ANSI escapes and bidi overrides are control/format chars too: they must never
# reach the printed line (they could reorder or repaint what the reader sees).
r = run([agent(ROOT, title="\x1b[31mManager\x1b[0m")])
check("ANSI escape stripped from the name", "\x1b" not in r.stdout)
r = run([agent(ROOT, title="Man\u202eager")])
check("bidi override stripped from the name", "\u202e" not in r.stdout)
check("bidi case still yields a single line", len(r.stdout.strip().splitlines()) == 1)

# --- argument guards ------------------------------------------------------- #
r = run([agent(ROOT)], main="")
check("empty main-repo path → unverified (never fail open)", out(r) == "unverified")
check("empty main-repo path exits 0", r.returncode == 0)
r = subprocess.run([BASH, str(SCRIPT), "manager-session", WS],
                   capture_output=True, text=True, timeout=30)
check("missing argument → usage, exit 2", r.returncode == 2 and not r.stdout.strip())

# --- realpath: a symlinked repo root still matches ------------------------- #
with tempfile.TemporaryDirectory() as td:
    real = Path(td) / "real-root"
    real.mkdir()
    link = Path(td) / "linked-root"
    link.symlink_to(real)
    r = run([agent(str(real))], main=str(link))
    check("symlinked root matches the resolved cwd", out(r) == "name=Manager")


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("herdr-teardown.sh manager-session: all tests passed")
