#!/usr/bin/env python3
"""Tests for insights-handoff.sh — work-system's bridge to the optional
`insights` plugin. Run standalone (`python3 test_insights_handoff.py`) or via
scripts/check-structure.py's "plugin tests" check.

Hermetic by construction, because the failures this bridge exists to prevent are
exactly the ones a sloppy test would cause: every case runs against a temporary
plugin tree and a temporary store (`INSIGHTS_STORE_DIR`), with `HOME` redirected
so the installed-plugins manifest can never be consulted. Nothing here touches
the user's real report store, a real worktree, or a live worker.

Covered: absent vs. unusable insights (the distinction /close warns on), the
project-scoped idempotency lookup a close retry depends on, the lifecycle-fact
overlay and its provenance, and the write passthrough's exit codes — including
that a storage failure never looks like a stored report.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "insights-handoff.sh"
REAL_INSIGHTS = HERE.parent.parent / "insights" / "scripts" / "insights.py"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def kv(out):
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def make_tree(root, insights="real"):
    """A minimal plugin tree: plugins/work-system next to plugins/insights.

    `insights`: "real" (the shipped helper), "broken" (a stub that fails every
    call, i.e. installed-but-unusable) or None (not installed).
    """
    ws = root / "plugins" / "work-system" / "scripts"
    ws.mkdir(parents=True)
    shutil.copy(SCRIPT, ws / SCRIPT.name)
    shutil.copy(HERE / "lib-bounded.sh", ws / "lib-bounded.sh")
    if insights == "real":
        ins = root / "plugins" / "insights" / "scripts"
        ins.mkdir(parents=True)
        shutil.copy(REAL_INSIGHTS, ins / "insights.py")
        # The contract ships beside the helper; probe derives its path from there.
        shutil.copytree(REAL_INSIGHTS.parent.parent / "docs", ins.parent / "docs")
    elif insights == "broken":
        ins = root / "plugins" / "insights" / "scripts"
        ins.mkdir(parents=True)
        (ins / "insights.py").write_text(
            "import sys\nsys.stderr.write('store unavailable\\n')\nsys.exit(4)\n"
        )
    return ws / SCRIPT.name


def run(script, *args, store=None, home=None, cwd=None):
    """Invoke the bridge with a fully isolated environment."""
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["HOME"] = str(home)
    if store is None:
        env.pop("INSIGHTS_STORE_DIR", None)
    else:
        env["INSIGHTS_STORE_DIR"] = str(store)
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, env=env, cwd=str(cwd) if cwd else None,
    )


def new_store(root, name="store", mode=0o700):
    d = root / name
    d.mkdir(parents=True)
    os.chmod(d, mode)
    return d


def new_project(root, name="proj"):
    """A real git repo: the store groups reports by the main checkout."""
    d = root / name
    d.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(d), *args], capture_output=True)
    (d / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(d), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-qm", "init"], capture_output=True)
    return d


def fill(draft):
    """Turn a skeleton into the minimal report the validator accepts.

    Mirrors what a reporting model must supply: every undecided field the
    skeleton deliberately leaves failing. If this ever stops producing a valid
    report, the schema grew a required field and the producers must learn it —
    that is a real signal, not test noise.
    """
    d = json.loads(draft) if isinstance(draft, str) else draft
    if not d["task_status"]:
        d["task_status"] = "completed"
    w = d["work"]
    w["summary"] = "Test report: stands alone once the worktree is gone."
    for f in ("task_id", "run_id", "task_name", "task_path", "branch", "pr"):
        if w[f].get("value") is None:
            w[f] = {"value": None, "reason": "not observed in this test"}
    r = d["reporter"]
    r["role"], r["role_source"] = "worker", "test harness"
    for f in ("model", "runtime", "harness", "reasoning_effort", "session_id"):
        if r[f].get("value") is None:
            r[f] = {"value": None, "reason": "not observed in this test"}
    d["usage"]["completeness"] = "unknown"
    d["usage"]["completeness_reason"] = "synthetic test report"
    rt = d["retrospective"]
    rt["outcome"] = {"intended": "exercise the bridge", "achieved": "exercised the bridge"}
    for k in ("domain", "tooling"):
        rt["difficulty"][k] = {"level": "low", "reason": "synthetic"}
    rt["suggestions"] = {"status": "none", "author": "reporting_model", "items": []}
    return d


def write_report(script, store, home, project, **over):
    """skeleton -> fill -> write, returning the write result and the draft."""
    args = ["skeleton", "close", "--caller", "close", "--project-dir", str(project)]
    for k, v in over.items():
        args += ["--" + k.replace("_", "-"), str(v)]
    sk = run(script, *args, store=store, home=home, cwd=project)
    assert sk.returncode == 0, sk.stderr
    draft = fill(sk.stdout)
    p = Path(tempfile.mkstemp(suffix=".json", dir=str(home))[1])
    p.write_text(json.dumps(draft))
    res = run(script, "write", str(p), "--project-dir", str(project),
              store=store, home=home, cwd=project)
    return res, draft


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    # ---------------------------------------------------------------- detection
    home = new_store(tmp, "home-absent")
    absent = make_tree(tmp / "t-absent", insights=None)
    r = run(absent, "probe", home=home)
    d = kv(r.stdout)
    check("probe exits 0 when insights is absent", r.returncode == 0)
    check("absent insights reports status=absent", d.get("status") == "absent")
    check("absent insights reports available=no", d.get("available") == "no")

    broken = make_tree(tmp / "t-broken", insights="broken")
    r = run(broken, "probe", home=home)
    d = kv(r.stdout)
    check("a failing helper is NOT reported as absent", d.get("status") == "unusable")
    check("a failing helper reports available=no", d.get("available") == "no")
    check("a failing helper names the reason", "store unavailable" in (d.get("reason") or ""))

    real = make_tree(tmp / "t-real", insights="real")
    store = new_store(tmp, "store")
    proj = new_project(tmp)
    r = run(real, "probe", store=store, home=home, cwd=proj)
    d = kv(r.stdout)
    check("a working helper probes ok", d.get("status") == "ok" and d.get("available") == "yes")
    check("probe names the helper it found", d.get("helper", "").endswith("insights.py"))
    # Callers read the contract path from here rather than rebuilding it: the
    # obvious `${CLAUDE_PLUGIN_ROOT}/../insights/...` is wrong in the marketplace
    # cache, where plugins sit at <root>/<plugin>/<version>/.
    check("probe points at the report contract",
          d.get("contract", "").endswith("insights/docs/REPORT-CONTRACT.md")
          and Path(d["contract"]).is_file())

    # The exit codes callers branch on: absent is a normal outcome (3), unusable
    # must be surfaced (4). Collapsing them would silently drop reports.
    check("reported exits 3 when insights is absent",
          run(absent, "reported", "t", home=home).returncode == 3)
    check("reported exits 4 when insights is unusable",
          run(broken, "reported", "t", home=home).returncode == 4)
    check("skeleton exits 3 when insights is absent",
          run(absent, "skeleton", "close", "--caller", "close", home=home).returncode == 3)

    # ------------------------------------------------------------- idempotency
    r = run(real, "reported", "some-task", "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    d = kv(r.stdout)
    check("an unreported task lists no reports", r.returncode == 0 and d.get("reports") == "0")
    check("an empty store reports no malformed files", d.get("malformed") == "0")

    res, draft = write_report(real, store, home, proj, task="some-task",
                              branch="task/some-task", status="completed")
    check("a filled skeleton stores", res.returncode == 0 and "status=stored" in res.stdout)
    stored_id = kv(res.stdout)["report_id"]

    r = run(real, "reported", "some-task", "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    d = kv(r.stdout)
    check("a close retry sees the existing report", d.get("reports") == "1")
    check("the existing report carries its trigger", "trigger=close" in r.stdout)

    r = run(real, "reported", "some-task", "--trigger", "handoff",
            "--project-dir", str(proj), store=store, home=home, cwd=proj)
    check("the trigger filter separates handoff from close",
          kv(r.stdout).get("reports") == "0")

    r = run(real, "reported", "other-task", "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    check("a different task is not considered reported", kv(r.stdout).get("reports") == "0")

    # A same-named task in ANOTHER checkout must not make this one look reported.
    other = new_project(tmp, "other-proj")
    r = run(real, "reported", "some-task", "--project-dir", str(other),
            store=store, home=home, cwd=other)
    check("the lookup is scoped to this project", kv(r.stdout).get("reports") == "0")

    # --------------------------------------------------------- fact provenance
    sk = run(real, "skeleton", "handoff", "--caller", "continue",
             "--task", "lane-a", "--branch", "task/lane-a", "--pr", "42",
             "--status", "blocked", "--related", stored_id,
             "--project-dir", str(proj), store=store, home=home, cwd=proj)
    d = json.loads(sk.stdout)
    check("the trigger reaches the draft", d["report_trigger"] == "handoff")
    check("task_status is set independently of the trigger", d["task_status"] == "blocked")
    check("the observed task name is carried over", d["work"]["task_name"]["value"] == "lane-a")
    check("a carried fact names where it came from",
          "work-system:continue" in d["work"]["task_name"]["source"])
    check("a carried fact is never a value+reason mix", "reason" not in d["work"]["branch"])
    check("the PR source is the command that observed it",
          d["work"]["pr"]["source"].startswith("gh pr view"))
    check("an earlier report is linked, not merged",
          d["work"]["related_reports"] == [stored_id])
    check("an unobserved fact stays unknown", d["work"]["run_id"]["value"] is None)

    # The overlay must not turn a skeleton into something storable on its own:
    # the fields the reporting model owns still fail validation by name.
    p = Path(tempfile.mkstemp(suffix=".json", dir=str(home))[1])
    p.write_text(sk.stdout)
    r = run(real, "write", str(p), "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    check("an unfilled overlaid skeleton is still rejected", r.returncode == 1)
    check("a rejected draft names its problems", "summary" in r.stderr)

    check("skeleton without --caller is a usage error",
          run(real, "skeleton", "close", store=store, home=home, cwd=proj).returncode == 2)
    check("an unknown trigger is a usage error",
          run(real, "skeleton", "nonsense", "--caller", "close",
              store=store, home=home, cwd=proj).returncode == 2)
    check("writing a missing draft is a usage error",
          run(real, "write", str(tmp / "nope.json"), store=store, home=home).returncode == 2)

    # ------------------------------------------------------- write passthrough
    # Retry safety: the same ID with identical content is a no-op success, and
    # different content under that ID must never overwrite the stored report.
    same = json.loads(json.dumps(draft))
    same["report_id"] = stored_id
    same["recorded_at"] = json.loads((store / f"{stored_id}.json").read_text())["recorded_at"]
    p = Path(tempfile.mkstemp(suffix=".json", dir=str(home))[1])
    p.write_text(json.dumps(same))
    r = run(real, "write", str(p), "--project-dir", str(proj), store=store, home=home, cwd=proj)
    check("re-writing an identical report is a no-op success",
          r.returncode == 0 and "status=unchanged" in r.stdout)

    same["work"]["summary"] = "Different content under the same report ID."
    p.write_text(json.dumps(same))
    r = run(real, "write", str(p), "--project-dir", str(proj), store=store, home=home, cwd=proj)
    check("a colliding ID never overwrites (exit 3)", r.returncode == 3)
    check("the stored report survived the collision",
          "stands alone" in (store / f"{stored_id}.json").read_text())

    # A storage failure must be exit 4 — never a quiet success a caller could
    # report as a saved report.
    bad = new_store(tmp, "bad-store", mode=0o777)
    res, _ = write_report(real, bad, home, proj, task="fails")
    check("a refused store fails with exit 4", res.returncode == 4)
    check("a refused store stores nothing", not list(bad.glob("*.json")))

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("insights-handoff.sh: all tests passed")
