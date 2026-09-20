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
project-scoped idempotency lookup a close retry depends on, `prepare`'s
derivation of the lane's identity (and that a shell-metacharacter branch name
stays DATA), the exit-code mapping that keeps an unsaved report from reading as
"nothing to do", and the /close step ordering.
"""
import json
import os
import re
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
    for name in (SCRIPT.name, "lib-bounded.sh", "task-status.sh", "main-repo-path.sh"):
        shutil.copy(HERE / name, ws / name)
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


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def new_project(root, name="proj", branch=None):
    """A real git repo: the store groups reports by the main checkout."""
    d = root / name
    d.mkdir(parents=True)
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "T")
    (d / "README.md").write_text("x\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    if branch:
        git(d, "checkout", "-q", "-b", branch)
        # A real lane has commits on its branch, and the namesake check needs
        # them: the lane's start is its first commit off the default branch. A
        # branch with nothing on it has no resolvable start, which correctly
        # falls back to "cannot rule out a namesake, so still skip".
        (d / "work.txt").write_text("lane work\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "lane work")
    return d


def draft_file(home, data):
    """A draft on disk, with its descriptor closed (mkstemp hands one back)."""
    fd, name = tempfile.mkstemp(suffix=".json", dir=str(home))
    os.close(fd)
    p = Path(name)
    p.write_text(json.dumps(data))
    return p


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


def prepare_and_write(script, store, home, lane, trigger="close", caller="close",
                      role=None, patch=None, **over):
    """prepare -> fill the draft it left -> write. Returns (result, prepare kv)."""
    args = ["prepare", trigger, "--caller", caller, "--lane", str(lane),
            "--project-dir", str(lane)]
    for k, v in over.items():
        args += ["--" + k.replace("_", "-"), str(v)]
    pre = run(script, *args, store=store, home=home, cwd=lane)
    assert pre.returncode == 0, pre.stderr
    info = kv(pre.stdout)
    if info.get("action") != "draft":
        return None, info
    d = fill(Path(info["draft"]).read_text())
    if role:
        d["reporter"]["role"] = role
        d["reporter"]["role_source"] = "test harness"
    if patch:
        patch(d)
    Path(info["draft"]).write_text(json.dumps(d))
    res = run(script, "write", info["draft"], "--project-dir", str(lane),
              store=store, home=home, cwd=lane)
    os.unlink(info["draft"])
    return res, info


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
    proj = new_project(tmp, branch="task/some-task")
    r = run(real, "probe", store=store, home=home, cwd=proj)
    d = kv(r.stdout)
    check("a working helper probes ok", d.get("status") == "ok" and d.get("available") == "yes")
    check("probe names the helper it found", d.get("helper", "").endswith("insights.py"))
    # Callers read the contract path from here rather than rebuilding it: the
    # obvious `<plugin-root>/../insights/...` is wrong in the marketplace cache,
    # where plugins sit at <root>/<plugin>/<version>/.
    check("probe points at the report contract",
          d.get("contract", "").endswith("insights/docs/REPORT-CONTRACT.md")
          and Path(d["contract"]).is_file())

    # The exit codes callers branch on: absent is a normal outcome (3), unusable
    # must be surfaced (4). Collapsing them would silently drop reports.
    check("reported exits 3 when insights is absent",
          run(absent, "reported", "t", home=home).returncode == 3)
    check("reported exits 4 when insights is unusable",
          run(broken, "reported", "t", home=home).returncode == 4)
    r = run(absent, "prepare", "close", "--caller", "close", "--lane", str(proj), home=home)
    check("prepare exits 3 when insights is absent", r.returncode == 3)
    # stdout is the payload channel: a caller parsing it must get facts or
    # nothing, never a status line.
    check("a failed prepare writes nothing to stdout", r.stdout == "")
    check("a failed prepare explains itself on stderr", "status=absent" in r.stderr)

    # ------------------------------------------------- deriving the lane identity
    # The whole point of --lane: work-system derives the task name and branch
    # itself, so nothing repo-authored is ever pasted into a command line.
    r = run(real, "prepare", "handoff", "--caller", "continue", "--lane", str(proj),
            "--project-dir", str(proj), "--status", "in_progress",
            store=store, home=home, cwd=tmp)
    d = kv(r.stdout)
    check("prepare derives the task name from the lane", d.get("task") == "some-task")
    check("prepare derives the branch from the lane", d.get("branch") == "task/some-task")
    check("prepare leaves a draft", d.get("action") == "draft" and Path(d["draft"]).is_file())
    check("the draft is private", (Path(d["draft"]).stat().st_mode & 0o077) == 0)
    drafted = json.loads(Path(d["draft"]).read_text())
    check("the derived task name reaches the draft",
          drafted["work"]["task_name"]["value"] == "some-task")
    check("a carried fact names where it came from",
          "work-system:continue" in drafted["work"]["task_name"]["source"])
    check("a carried fact is never a value+reason mix", "reason" not in drafted["work"]["branch"])
    check("the trigger reaches the draft", drafted["report_trigger"] == "handoff")
    check("task_status is set independently of the trigger",
          drafted["task_status"] == "in_progress")
    check("an unobserved fact stays unknown", drafted["work"]["run_id"]["value"] is None)
    os.unlink(d["draft"])

    # A branch name git accepts but a shell would EXECUTE. `$(...)` is a legal
    # refname as long as it has no space, so this is not hypothetical — it is the
    # reason prepare takes a directory instead of a name.
    hostile = "task/x$(touch$IFS%s)y" % (tmp / "PWNED")
    hproj = new_project(tmp, "hostile-proj")
    if git(hproj, "checkout", "-q", "-b", hostile).returncode == 0:
        r = run(real, "prepare", "handoff", "--caller", "continue", "--lane", str(hproj),
                "--project-dir", str(hproj), "--status", "in_progress",
                store=store, home=home, cwd=tmp)
        d = kv(r.stdout)
        check("a metacharacter branch name is not executed", not (tmp / "PWNED").exists())
        check("a metacharacter branch name survives as data",
              d.get("branch") == hostile)
        if d.get("action") == "draft":
            check("the hostile name reaches the draft verbatim",
                  json.loads(Path(d["draft"]).read_text())["work"]["branch"]["value"] == hostile)
            os.unlink(d["draft"])
    else:
        # Git's refname rules changed; the guarantee is still the code's, not git's.
        check("hostile-refname case could be constructed", True)

    # ------------------------------------------------------------- idempotency
    r = run(real, "reported", "some-task", "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    d = kv(r.stdout)
    check("an unreported task lists no reports", r.returncode == 0 and d.get("reports") == "0")
    # Named store-global on purpose: insights cannot attribute an unparsable file
    # to a task, so a caller must not read this as "this task has malformed reports".
    check("the malformed count is labelled store-global", "malformed_store" in d)

    res, info = prepare_and_write(real, store, home, proj, trigger="close", status="completed")
    check("a filled draft stores", res.returncode == 0 and "status=stored" in res.stdout)
    stored_id = kv(res.stdout)["report_id"]

    r = run(real, "reported", "some-task", "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    d = kv(r.stdout)
    check("a close retry sees the existing report", d.get("reports") == "1")
    check("the existing report carries its trigger", "trigger=close" in r.stdout)
    check("the existing report carries its timestamp — the only way to spot a "
          "stale namesake", "recorded_at=" in r.stdout)

    # The one automatic skip, and it is trigger-scoped.
    _, info = prepare_and_write(real, store, home, proj, trigger="close", status="completed")
    check("a second close is skipped, not duplicated", info.get("action") == "skip")
    check("the skip names the report that already covers it",
          info.get("report") == stored_id)
    r = run(real, "prepare", "handoff", "--caller", "continue", "--lane", str(proj),
            "--project-dir", str(proj), store=store, home=home, cwd=proj)
    d = kv(r.stdout)
    check("a handoff is never auto-skipped by a close report", d.get("action") == "draft")
    check("prepare offers the earlier report for linking",
          stored_id in (d.get("related") or ""))
    os.unlink(d["draft"])

    r = run(real, "reported", "some-task", "--trigger", "handoff",
            "--project-dir", str(proj), store=store, home=home, cwd=proj)
    check("the trigger filter separates handoff from close", kv(r.stdout).get("reports") == "0")
    r = run(real, "reported", "other-task", "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    check("a different task is not considered reported", kv(r.stdout).get("reports") == "0")

    # A same-named task in ANOTHER checkout must not make this one look reported.
    other = new_project(tmp, "other-proj")
    r = run(real, "reported", "some-task", "--project-dir", str(other),
            store=store, home=home, cwd=other)
    check("the lookup is scoped to this project", kv(r.stdout).get("reports") == "0")

    # ----------------------------------------------------------- usage errors
    check("prepare without --caller is a usage error",
          run(real, "prepare", "close", "--lane", str(proj),
              store=store, home=home, cwd=proj).returncode == 2)
    check("prepare without --lane is a usage error",
          run(real, "prepare", "close", "--caller", "close",
              store=store, home=home, cwd=proj).returncode == 2)
    check("an unknown trigger is a usage error",
          run(real, "prepare", "nonsense", "--caller", "close", "--lane", str(proj),
              store=store, home=home, cwd=proj).returncode == 2)
    # Validated here rather than downstream: argparse's rejection came back as
    # "insights is installed but unusable", which blames the wrong component.
    r = run(real, "reported", "t", "--trigger", "nonsense",
            store=store, home=home, cwd=proj)
    check("reported validates its trigger itself", r.returncode == 2)
    check("a bad trigger is not reported as a broken plugin",
          "unusable" not in r.stderr)
    check("a non-numeric --pr is a usage error",
          run(real, "prepare", "close", "--caller", "close", "--lane", str(proj),
              "--pr", "12; rm -rf /", store=store, home=home, cwd=proj).returncode == 2)
    check("writing a missing draft is a usage error",
          run(real, "write", str(tmp / "nope.json"), store=store, home=home).returncode == 2)
    link = tmp / "draft-link.json"
    link.symlink_to(tmp / "nope.json")
    check("a symlinked draft is refused, like --note-file",
          run(real, "write", str(link), store=store, home=home).returncode == 2)

    # ------------------------------------------------- the exit-code namespace
    # insights.py's 2 (usage) and 3 (collision) must NOT surface as this script's
    # 2 (bad argv) and 3 (absent) — a collision reading as "not installed" would
    # turn an unsaved report into "nothing to do".
    stored = json.loads((store / f"{stored_id}.json").read_text())
    same = draft_file(home, stored)
    r = run(real, "write", str(same), "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    check("re-writing an identical report is a no-op success",
          r.returncode == 0 and "status=unchanged" in r.stdout)
    clash = json.loads(json.dumps(stored))
    clash["work"]["summary"] = "Different content under the same report ID."
    same.write_text(json.dumps(clash))
    r = run(real, "write", str(same), "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    check("a colliding ID exits 5, not 3", r.returncode == 5)
    check("the stored report survived the collision",
          "stands alone" in (store / f"{stored_id}.json").read_text())
    bad = draft_file(home, {"schema": "insights.report/v1"})
    r = run(real, "write", str(bad), "--project-dir", str(proj),
            store=store, home=home, cwd=proj)
    check("an invalid draft exits 1", r.returncode == 1)
    os.unlink(same); os.unlink(bad)

    # A storage failure must be exit 4 — never a quiet success.
    badstore = new_store(tmp, "bad-store", mode=0o777)
    res, _ = prepare_and_write(real, badstore, home, proj, trigger="handoff", status="blocked")
    check("a refused store fails with exit 4", res.returncode == 4)
    check("a refused store stores nothing", not list(badstore.glob("*.json")))

    # ------------------------------------------------- a reused task name
    # The skip used to be permanent: any stored close report for the NAME won,
    # so a new task reusing an archived name could never get its own report.
    # A report older than this lane's first commit belongs to the namesake.
    reused = new_project(tmp, "reused-proj", branch="task/reissue")
    res, _ = prepare_and_write(real, store, home, reused, trigger="close", status="completed")
    check("the first close of a name stores", res.returncode == 0)
    old_id = kv(res.stdout)["report_id"]

    _, info = prepare_and_write(real, store, home, reused, trigger="close", status="completed")
    check("an immediate retry still skips", info.get("action") == "skip")
    check("the skip shows the timestamp it judged by", "report_recorded_at" in info)

    # Rewrite the stored report as if it came from an earlier task of the same
    # name, then re-run: the lane's commits are newer, so it must NOT skip.
    stale = json.loads((store / f"{old_id}.json").read_text())
    (store / f"{old_id}.json").unlink()
    stale["report_id"] = stale["report_id"].replace("ins-2026", "ins-2020")
    stale["recorded_at"] = "2020-01-01T00:00:00Z"
    (store / f"{stale['report_id']}.json").write_text(json.dumps(stale, indent=2))
    r = run(real, "prepare", "close", "--caller", "close", "--lane", str(reused),
            "--project-dir", str(reused), "--status", "completed",
            store=store, home=home, cwd=reused)
    d = kv(r.stdout)
    check("a report predating the lane does not block a new close report",
          d.get("action") == "draft")
    # It is reported — so a human can see it — but NOT linked: related_reports is
    # a claim of relation, and a different task that merely reused the name is
    # not this task's history.
    check("the namesake is surfaced", stale["report_id"] in r.stdout)
    check("the namesake is flagged as such", "namesake=yes" in r.stdout)
    check("the namesake is not linked into the new report",
          stale["report_id"] not in (d.get("related") or ""))
    if d.get("action") == "draft":
        drafted = json.loads(Path(d["draft"]).read_text())
        check("no namesake reaches related_reports in the draft",
              stale["report_id"] not in drafted["work"]["related_reports"])
        os.unlink(d["draft"])

    # ---------------------------------------------------------------- redact
    # The /close fallback note is not a report, but it lands in a file this repo
    # may commit and push — so it goes through insights' own patterns, not prose.
    note = tmp / "note.txt"
    note.write_text("write failed. token sk-ant-0123456789abcdefghijklmno stays out of git\n")
    r = run(real, "redact", str(note), store=store, home=home, cwd=proj)
    check("redact exits 0", r.returncode == 0)
    check("redact removes the credential shape", "sk-ant-0123" not in r.stdout)
    check("redact keeps the surrounding text", "write failed." in r.stdout)
    check("redact reports how much it replaced", "redactions=1" in r.stderr)
    check("redact is unavailable when insights is absent",
          run(absent, "redact", str(note), home=home).returncode == 3)

    # Refusing on a control character left the caller with UNREDACTED text — the
    # one input where redaction matters most. It strips instead.
    ctl = tmp / "note-ctl.txt"
    ctl.write_text("tok sk-ant-0123456789abcdefghijklmno\x01end\n")
    r = run(real, "redact", str(ctl), store=store, home=home, cwd=proj)
    check("a control character no longer disables redaction", r.returncode == 0)
    check("the credential is still removed", "sk-ant-0123" not in r.stdout)
    check("the control character is stripped", "\x01" not in r.stdout)

    # --in-place exists so the SKILL does not spell out a redirect-then-rename at
    # the one moment the note is the last copy of the observation.
    inplace = tmp / "note-inplace.txt"
    inplace.write_text("keep this. sk-ant-0123456789abcdefghijklmno\n")
    r = run(real, "redact", str(inplace), "--in-place", store=store, home=home, cwd=proj)
    check("--in-place exits 0", r.returncode == 0)
    check("--in-place rewrites the file", "sk-ant-0123" not in inplace.read_text())
    check("--in-place keeps the rest of the note", "keep this." in inplace.read_text())
    # A failed pass must leave the ORIGINAL intact, never a truncated one.
    untouched = tmp / "note-untouched.txt"
    untouched.write_text("original content\n")
    r = run(broken, "redact", str(untouched), "--in-place", home=home)
    check("a failed --in-place leaves the note unchanged",
          untouched.read_text() == "original content\n")
    check("a failed --in-place is not reported as success", r.returncode != 0)

    # `rc=$?` taken after a closed `if` always read 0, so the invalid-vs-unusable
    # mapping was dead and every failure came back as 4 ("installed but
    # unusable") — blaming the plugin for a bad input.
    badinput = tmp / "note-badutf8.txt"
    badinput.write_bytes(b"\xff\xfe not utf-8\n")
    r = run(real, "redact", str(badinput), "--in-place", store=store, home=home, cwd=proj)
    check("a rejected input maps to exit 1, not 'plugin unusable'", r.returncode == 1)
    check("a rejected --in-place leaves the file byte-identical",
          badinput.read_bytes() == b"\xff\xfe not utf-8\n")

    # The cleanup trap was dead code: `f="$(mktemp_tracked)"` appended inside a
    # subshell, so the parent's array was always empty. Assert no stray temp
    # files survive a run that creates several.
    before = set(Path(os.environ.get("TMPDIR", "/tmp")).glob("insights-handoff.*"))
    run(real, "reported", "some-task", "--project-dir", str(proj),
        store=store, home=home, cwd=proj)
    after = set(Path(os.environ.get("TMPDIR", "/tmp")).glob("insights-handoff.*"))
    check("the bridge leaves no temp files behind", after <= before)


# ======================================================= /close ordering contract
# The ordering in the task's acceptance criteria is prose, and prose drifts under
# later edits — which is exactly how a reporting step would migrate past the
# safety gate or turn into a cleanup blocker. Assert it against the skill.
CLOSE = (HERE.parent / "skills" / "close" / "SKILL.md").read_text()
CONTINUE = (HERE.parent / "skills" / "continue" / "SKILL.md").read_text()


def at(text, needle):
    i = text.find(needle)
    assert i >= 0, f"missing anchor: {needle!r}"
    return i


gate = at(CLOSE, "2. **Verify the task is merged**")
report = at(CLOSE, "6b. **Preserve an insights report**")
removal = at(CLOSE, "7. **Remove worktree**")
check("the merge gate still comes before reporting", gate < report)
check("reporting comes before the worktree is removed", report < removal)

step6b = CLOSE[report:removal]
check("an absent insights plugin is skipped silently", "silently" in step6b)
check("a failed report never blocks cleanup", "never a cleanup gate" in step6b)
check("an unsaved report is never described as recorded",
      "never describe it as recorded" in step6b)
check("a report grants no authority", "authorizes anything" in step6b)
check("the unsaved summary is redacted before it can be committed",
      "redact" in step6b)
check("the unsaved summary lands in the archived task file",
      "--note-file" in step6b and "--note-file" in CLOSE[removal:])
# The lane's identity is DERIVED, never pasted: a refname may legally contain
# `$(...)`. Scoped to the INSIGHTS calls, which is all this change controls — the
# pre-existing archive-task.sh invocation in step 10 still interpolates
# `<task-name>`/`<task-branch>` and predates this feature; claiming otherwise here
# would assert a guarantee the repo does not have.
for label, text in (("close", step6b), ("continue", CONTINUE)):
    check(f"{label} passes a lane to the bridge, not a task name", "--lane" in text)
    check(f"{label} never pastes a repo-derived name into a bridge call",
          '--task "<task-name>"' not in text and "--task '<task-name>'" not in text)
# Every flag the SKILLs prescribe must actually exist, or the model hits
# `unknown option` (exit 2) on a path neither skill documents a branch for.
# (This used to be a loop whose only statement was `continue` — zero assertions.)
SCRIPT_TEXT = SCRIPT.read_text()
FLAG_RE = re.compile(r"--[a-z][a-z-]+")
KNOWN_NON_BRIDGE = {"--pr", "--json", "--jq", "--ff-only", "--quiet", "--short",
                    "--sha", "--note-file", "--date", "--format", "--admin"}
for label, text in (("close", CLOSE), ("continue", CONTINUE)):
    # Join backslash-continued lines first: the SKILLs wrap their commands, and
    # scanning raw lines saw only each command's first fragment.
    for line in re.sub(r"\\\n\s*", " ", text).splitlines():
        if "insights-handoff.sh" not in line:
            continue
        for flag in FLAG_RE.findall(line):
            if flag in KNOWN_NON_BRIDGE:
                continue
            check(f"{label} prescribes {flag}, which the bridge accepts",
                  f"{flag})" in SCRIPT_TEXT)
check("no SKILL prescribes a --related flag",
      "--related" not in CLOSE and "--related" not in CONTINUE)
check("--related is not a bridge flag either", "--related)" not in SCRIPT_TEXT)
check("--in-place is what the SKILLs prescribe for redaction",
      "--in-place" in CLOSE and "--in-place)" in SCRIPT_TEXT)
check("a failed redaction never archives the note",
      "must **not** archive it" in CLOSE)

check("the handoff report is bounded to real handoffs",
      "Never at a tool call, a turn end, a commit, or an unchanged idle" in CONTINUE)
check("reaching the gate is not a completed task",
      "Reaching `reviewed-pr`" in CONTINUE and "is `in_progress`" in CONTINUE)
check("the handoff report consumes no review round", "consumes **no** review round" in CONTINUE)
check("the crash gap is documented, not advertised away", "**Known gap.**" in CONTINUE)

if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("insights-handoff.sh: all tests passed")
