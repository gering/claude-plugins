#!/usr/bin/env python3
"""Tests for pr-flow's mandate-shim.sh and the lib-work-system.sh locator it
shares with refresh-task-glyphs.sh — run standalone or via
scripts/check-structure.py's "plugin tests" check.

The property that matters: pr-flow must never turn "work-system isn't
installed" into "you may not do that". Exit 1 (the user decided against it) and
exit 3 (nobody was ever asked) stay distinguishable through the shim, and a
missing work-system yields 3, never 1 and never a silent 0.

The dev layout (plugins/pr-flow + plugins/work-system as siblings) is the one
resolution layer that can be exercised hermetically; the manifest and cache
layers depend on the user's real ~/.claude and are covered by the glyph shim's
production use. Every run therefore gets an ISOLATED $HOME — otherwise the
manifest layer resolves the user's actually-installed work-system and the
"absent" assertions below start passing or failing depending on what the
machine happens to have installed.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SHIM = HERE / "mandate-shim.sh"
REAL_MANDATE = HERE.parent.parent / "work-system" / "scripts" / "mandate.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def layout(with_work_system=True):
    """A fake plugin tree: <root>/pr-flow (+ optionally <root>/work-system)."""
    root = Path(tempfile.mkdtemp())
    prf = root / "pr-flow" / "scripts"
    prf.mkdir(parents=True)
    for f in ("mandate-shim.sh", "lib-work-system.sh"):
        (prf / f).write_text((HERE / f).read_text())
    if with_work_system:
        ws = root / "work-system" / "scripts"
        ws.mkdir(parents=True)
        (ws / "mandate.sh").write_text(REAL_MANDATE.read_text())
    return root


ISOLATED_HOME = tempfile.mkdtemp()   # empty: no ~/.claude/plugins manifest at all


def run(root, *args, cwd=None, plugin_root=True, env_extra=None):
    env = dict(os.environ, HOME=ISOLATED_HOME)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    if plugin_root:
        env["CLAUDE_PLUGIN_ROOT"] = str(root / "pr-flow")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(root / "pr-flow" / "scripts" / "mandate-shim.sh"), *args],
        capture_output=True, text=True, env=env, cwd=cwd,
    )


def make_repo():
    repo = Path(tempfile.mkdtemp())
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


# --- work-system present: verdicts pass through unchanged -------------------
root = layout()
repo = make_repo()
subprocess.run(
    ["bash", str(REAL_MANDATE), "init", str(repo), "task=t", "authorized_by=user",
     "allow=commit,open-pr,agreed-fixes", "deny=merge", "review_budget=1"],
    capture_output=True, check=True,
)

r = run(root, "allows", "open-pr", str(repo))
check("allowed action passes through as 0", r.returncode == 0)
check("allowed action keeps its verdict line", "allowed" in r.stdout)

r = run(root, "allows", "merge", str(repo))
check("denied action passes through as 1", r.returncode == 1)
check("denied stays distinguishable from unknown", "denied" in r.stdout)

r = run(root, "allows", "deploy", str(repo))
check("unlisted action passes through as 1", r.returncode == 1)
check("unlisted says unlisted", "unlisted" in r.stdout)

r = run(root, "show", str(repo))
check("show passes through", r.returncode == 0)
check("show carries the mandate fields", "mandate_exists=yes" in r.stdout)

r = run(root, "round", str(repo))
check("round passes through", r.returncode == 0)
check("round exhausts a 1-round budget", "review_budget_exhausted=yes" in r.stdout)

# A dir argument is optional — the shim must resolve $PWD like the real script.
r = run(root, "allows", "commit", cwd=str(repo))
check("no dir argument uses the cwd", r.returncode == 0)

# --- a repo with no mandate: unknown, not denied ----------------------------
bare = make_repo()
r = run(root, "allows", "commit", str(bare))
check("missing mandate is exit 3", r.returncode == 3)
check("missing mandate says no-mandate", "no-mandate" in r.stdout)

# --- work-system absent: still unknown, never denied, never silent ----------
lonely = layout(with_work_system=False)
r = run(lonely, "allows", "open-pr", str(repo))
check("missing work-system is exit 3, not 1", r.returncode == 3)
check("missing work-system is not a silent success", r.returncode != 0)
check("missing work-system names itself", "no-work-system" in r.stdout)

# An installed-but-older work-system (no mandate.sh) is the same case.
(lonely / "work-system" / "scripts").mkdir(parents=True)
(lonely / "work-system" / "scripts" / "herdr-tab-glyph.sh").write_text("#!/bin/sh\n")
r = run(lonely, "allows", "open-pr", str(repo))
check("work-system without mandate.sh is exit 3", r.returncode == 3)
check("work-system without mandate.sh names itself", "no-work-system" in r.stdout)

# --- the locator never executes code from the working directory -------------
# `${CLAUDE_PLUGIN_ROOT:-.}` used to make the fallback "wherever the shell is",
# so any repo carrying scripts/lib-work-system.sh got it sourced — in the main
# session, unsandboxed — while the shim still printed a plausible verdict.
hostile = Path(tempfile.mkdtemp())
(hostile / "scripts").mkdir()
marker = hostile / "EXECUTED"
(hostile / "scripts" / "lib-work-system.sh").write_text(
    f'touch "{marker}"\nws_find() {{ :; }}\n'
)
r = run(root, "allows", "open-pr", str(repo), cwd=str(hostile), plugin_root=False)
check("a cwd lib is never sourced", not marker.exists())
check("the real sibling lib is used instead", r.returncode == 0)
# Same from a layout with no work-system: the hostile lib must not be reached
# even when the shim has nothing of its own to find.
r = run(lonely, "allows", "open-pr", str(repo), cwd=str(hostile), plugin_root=False)
check("a cwd lib is not sourced on the not-found path either", not marker.exists())
check("and the answer stays 'unknown', not 'denied'", r.returncode == 3)

# --- the dev layout resolves without CLAUDE_PLUGIN_ROOT ---------------------
# A shim invoked outside a skill's ${CLAUDE_PLUGIN_ROOT} expansion (a hook, an
# absolute-path call) must still find its sibling work-system, or a granted
# action is silently re-asked.
r = run(root, "allows", "open-pr", str(repo), plugin_root=False)
check("dev layout resolves from the script's own location", r.returncode == 0)
check("and reports the real verdict", "allowed" in r.stdout)

# --- lane and actions pass through; exit 2 is not collapsed -------------------
r = run(root, "actions")
check("actions passes through", r.returncode == 0 and "open-pr" in r.stdout)
r = run(root, "lane", "no-such-branch", str(repo))
check("lane with no worktree is exit 3", r.returncode == 3)
# The shim exec()s the real script, so a corrupt file's exit 2 must arrive as 2
# — a caller mapping "anything else" to 3 would read a duplicate `allow:` as
# "nothing recorded" and proceed.
corrupt = make_repo()
(corrupt / "MANDATE.md").write_text("---\nallow: merge\nallow: commit\n---\n")
r = run(root, "allows", "merge", str(corrupt))
check("a corrupt mandate is exit 2 through the shim", r.returncode == 2)
check("its stderr survives the shim", "duplicate" in r.stderr)

# --- deploy-owner: /merge asks it before a project's post-merge deploy -------
# The fake layout carries no ws-settings.py, so the settings fall back to the
# side on which nothing deploys from a lane unasked (manager), with a note.
r = run(root, "deploy-owner", str(repo))
check("deploy-owner passes through", r.returncode == 0)
check("a main checkout is not a lane", "in_lane=no" in r.stdout)
check("an unreadable setting falls back to manager", "deploy_by=manager" in r.stdout)
subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"],
               check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
wt = Path(tempfile.mkdtemp()) / "lane"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "task/x", str(wt)],
               check=True, capture_output=True)
r = run(root, "deploy-owner", str(wt))
check("a linked worktree is a lane", r.returncode == 0 and "in_lane=yes" in r.stdout)
check("outside git deploy-owner is unknown (exit 3)",
      run(root, "deploy-owner", tempfile.mkdtemp()).returncode == 3)

# --- covered: /merge's local-review gate passes through ----------------------
r = run(root, "covered", str(wt))
check("covered with no mandate is unknown (exit 3)", r.returncode == 3)
subprocess.run(["bash", str(root / "work-system" / "scripts" / "mandate.sh"), "init", str(wt),
                "allow=commit", "authorized_by=user", "task=x"], check=True, capture_output=True)
r = run(root, "covered", "--head", "HEAD", str(wt))
check("covered before a review is exit 1 with its reason",
      r.returncode == 1 and "reason=no-review" in r.stdout)
# A work-system too old to know the verb must read as unknown (3), never as the
# exit-2 usage error that /merge would hard-stop on.
old = layout()
(old / "work-system" / "scripts" / "mandate.sh").write_text(
    'echo "usage: mandate.sh {path|lane|show|init|allows|round|actions}" >&2; exit 2\n')
for verb in ("covered", "reviewed", "fixed"):
    r = run(old, verb, str(wt))
    check(f"{verb} on an old work-system is unknown (exit 3)",
          r.returncode == 3 and "verdict=old-work-system" in r.stdout)

# --- usage ------------------------------------------------------------------
check("unknown verb exits 2", run(root, "bogus").returncode == 2)
check("the usage line lists lane", "lane" in run(root, "bogus").stderr)
check("no verb exits 2", run(root).returncode == 2)
check("a usage error is not mistaken for a verdict",
      "verdict=" not in run(root, "bogus").stdout)


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("mandate-shim.sh: all tests passed")
