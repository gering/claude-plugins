#!/usr/bin/env python3
"""Hermetic tests for insights.py. Run by scripts/check-structure.py in CI.

HOME, XDG_DATA_HOME and INSIGHTS_STORE_DIR are pointed at a throwaway directory
before anything runs, so neither the in-process calls nor the CLI subprocesses
can reach the real global store.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
SANDBOX = Path(tempfile.mkdtemp(prefix="insights-test-")).resolve()
os.environ["HOME"] = str(SANDBOX / "home")
os.environ["XDG_DATA_HOME"] = str(SANDBOX / "xdg")
os.environ.pop("INSIGHTS_STORE_DIR", None)
for var in [k for k in os.environ if k.startswith("GIT_")]:
    os.environ.pop(var)

sys.path.insert(0, str(HERE))
import insights  # noqa: E402

SCRIPT = HERE / "insights.py"
FIXTURES = HERE / "fixtures"
GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
       "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"]


# ------------------------------------------------------------------------ helpers


def fresh_store(name: str) -> Path:
    path = SANDBOX / "stores" / name
    shutil.rmtree(path, ignore_errors=True)
    return path


def cli(*args, stdin=None, cwd=None, env=None):
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, text=True,
        cwd=str(cwd or SANDBOX), env=full_env, timeout=60,
    )


def kv(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(k, v)
    return out


def unk(reason="not observable at report time"):
    return {"value": None, "reason": reason}


def draft(summary="Implemented the store; tests pending.", **overrides) -> dict:
    """A minimal valid manual report without report_id/recorded_at/project."""
    doc = {
        "schema": "insights.report/v1",
        "report_trigger": "manual",
        "task_status": "in_progress",
        "work": {
            "summary": summary,
            "task_id": unk("work-system tasks carry no task ID"),
            "run_id": unk("no run registry"),
            "task_name": {"value": "demo-task", "source": "MANDATE.md task"},
            "task_path": unk(),
            "branch": {"value": "task/demo-task", "source": "git"},
            "pr": unk("no PR yet"),
            "instruction_ids": [],
            "related_reports": [],
        },
        "reporter": {
            "role": "worker", "role_source": "task lane session",
            "model": {"value": "claude-opus-5", "source": "system prompt"},
            "runtime": {"value": "claude-code 2.1.273", "source": "env:CLAUDE_CODE_EXECPATH"},
            "harness": unk("no harness detected"),
            "reasoning_effort": {"value": "high", "source": "env:CLAUDE_EFFORT"},
            "session_id": unk(),
        },
        "participants": [],
        "usage": {
            "completeness": "partial",
            "completeness_reason": "context covers this session only",
            "skills": [],
        },
        "user_feedback": [],
        "retrospective": {
            "outcome": {"intended": "Ship the store.", "achieved": "Store works, docs pending."},
            "difficulty": {
                "domain": {"level": "medium", "reason": "concurrency semantics"},
                "tooling": {"level": "low", "reason": "no tool friction"},
            },
            "worked_well": [],
            "friction": [],
            "interventions": [],
            "suggestions": {"status": "none", "author": "reporting_model", "items": []},
        },
    }
    doc.update(overrides)
    return doc


def prepared(**overrides) -> dict:
    return insights.prepare(draft(**overrides), str(SANDBOX))[0]


def errors_for(doc) -> list:
    return insights.validate_report(doc)


def assert_error(doc, needle: str):
    errs = errors_for(doc)
    assert any(needle in e for e in errs), f"expected an error containing {needle!r}, got {errs}"


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(GIT + ["init", "-q", str(path)], check=True)
    subprocess.run(GIT + ["-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return path


# -------------------------------------------------------------------------- tests


def test_fixtures_are_valid_historical_examples():
    names = sorted(p.name for p in FIXTURES.glob("*.json"))
    assert names == [
        "dispatch-timeout-later-running.json",
        "repeat-question-vs-new-approval.json",
        "swarm-partial-voice-loss.json",
    ], names
    for path in FIXTURES.glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert errors_for(doc) == [], (path.name, errors_for(doc))
        assert doc["work"]["summary"].startswith("HISTORICAL FIXTURE"), path.name

    swarm = json.loads((FIXTURES / "swarm-partial-voice-loss.json").read_text())
    run = swarm["plugin_details"]["swarm"]["runs"][0]
    # Missing results are recorded separately from empty (successful) results.
    assert run["voices"]["missing_results"] == 6 and run["voices"]["empty_results"] is None
    assert len(run["failures"]) == 6

    repeat = json.loads((FIXTURES / "repeat-question-vs-new-approval.json").read_text())
    classes = [q["classification"] for q in repeat["plugin_details"]["work-system"]["questions"]]
    assert classes == ["avoidable_repeat", "new_approval"]

    timeout = json.loads((FIXTURES / "dispatch-timeout-later-running.json").read_text())
    state = timeout["plugin_details"]["work-system"]["ambiguous_states"][0]
    assert state["status"] == "resolved" and "no retry" in state["resolution"]


def test_schema_rejects_structural_errors():
    good = prepared()
    assert errors_for(good) == []

    missing = copy.deepcopy(good)
    del missing["usage"]
    assert_error(missing, "usage: is required")

    extra = copy.deepcopy(good)
    extra["transcript"] = "raw conversation"
    assert_error(extra, "report.transcript: unknown field")

    bad_enum = copy.deepcopy(good)
    bad_enum["report_trigger"] = "automatic"
    assert_error(bad_enum, "report_trigger: must be one of")

    status_is_not_trigger = copy.deepcopy(good)
    status_is_not_trigger["task_status"] = "manual"
    assert_error(status_is_not_trigger, "task_status: must be one of")

    bad_id = copy.deepcopy(good)
    bad_id["report_id"] = "../../etc/passwd"
    assert_error(bad_id, "report_id: must match")

    bad_ts = copy.deepcopy(good)
    bad_ts["recorded_at"] = "yesterday"
    assert_error(bad_ts, "recorded_at: must be a UTC timestamp")

    newline_id = copy.deepcopy(good)
    newline_id["report_id"] = insights.new_report_id() + "\n"
    assert_error(newline_id, "report_id: must match")

    fractional = copy.deepcopy(good)
    fractional["recorded_at"] = "2026-09-16T10:00:00.9Z"  # would mis-sort as a string
    assert_error(fractional, "recorded_at: must be a UTC timestamp")

    related = copy.deepcopy(good)
    related["work"]["related_reports"] = [insights.new_report_id() + "\n"]
    assert_error(related, "work.related_reports[0]: must be a report ID")

    wrong_schema = copy.deepcopy(good)
    wrong_schema["schema"] = "insights.report/v2"
    assert_error(wrong_schema, "schema: must be")

    too_long = copy.deepcopy(good)
    too_long["work"]["summary"] = "x" * (insights.MAX_TEXT + 1)
    assert_error(too_long, "summarize, do not paste")

    empty_summary = copy.deepcopy(good)
    empty_summary["work"]["summary"] = "  "
    assert_error(empty_summary, "work.summary: must be a non-empty string")

    tampered_key = copy.deepcopy(good)
    tampered_key["project"]["key"] = "0" * 16
    assert_error(tampered_key, "project.key: does not match ref")


def test_unknown_values_need_reasons_and_known_values_need_sources():
    good = prepared()

    no_reason = copy.deepcopy(good)
    no_reason["work"]["run_id"] = {"value": None}
    assert_error(no_reason, "work.run_id.reason")

    no_source = copy.deepcopy(good)
    no_source["reporter"]["model"] = {"value": "claude-opus-5"}
    assert_error(no_source, "reporter.model.source")

    role_without_source = copy.deepcopy(good)
    role_without_source["reporter"]["role_source"] = None
    assert_error(role_without_source, "reporter.role_source")

    mixed_unknown = copy.deepcopy(good)
    mixed_unknown["work"]["run_id"] = {"value": None, "reason": "not observable", "source": "system prompt"}
    assert_error(mixed_unknown, "work.run_id.source: must be absent")

    mixed_known = copy.deepcopy(good)
    mixed_known["reporter"]["model"] = {"value": "claude-opus-5", "source": "system prompt", "reason": None}
    assert_error(mixed_known, "reporter.model.reason: must be absent")

    unknown_role = copy.deepcopy(good)
    unknown_role["reporter"]["role"] = "unknown"
    unknown_role["reporter"]["role_source"] = None
    assert errors_for(unknown_role) == []


def test_partial_metadata_is_reported_as_gaps():
    doc = prepared()
    gaps = insights.fact_gaps(doc)
    assert "work.task_id: work-system tasks carry no task ID" in gaps
    assert "reporter.harness: no harness detected" in gaps
    assert any(g.startswith("usage.completeness=partial") for g in gaps)
    assert not any(g.startswith("reporter.model") for g in gaps)  # known values are not gaps


def test_model_and_plugin_version_changes_are_separate_usages():
    doc = prepared(participants=[
        {"role": "worker", "label": "lane before /model switch",
         "model": {"value": "claude-sonnet-5", "source": "system prompt at session start"},
         "runtime": {"value": "claude-code", "source": "session"}, "harness": unk(),
         "reasoning_effort": unk(), "scope": "first half of implementation", "basis": "run_evidence"},
        {"role": "worker", "label": "lane after /model switch",
         "model": {"value": "claude-opus-5", "source": "/model command output"},
         "runtime": {"value": "claude-code", "source": "session"}, "harness": unk(),
         "reasoning_effort": unk(), "scope": "second half", "basis": "run_evidence"},
        {"role": "unknown", "label": "codex review voice", "model": unk("adapter did not report a model"),
         "runtime": {"value": "codex CLI", "source": "swarm adapter"}, "harness": unk(),
         "reasoning_effort": unk(), "scope": "review", "basis": "run_evidence"},
    ])
    doc["usage"]["skills"] = [
        {"skill": "work-system:continue", "plugin": "work-system",
         "plugin_version": {"value": "1.13.0", "source": "skill base directory at invocation"},
         "model": {"value": "claude-sonnet-5", "source": "system prompt"}, "note": None},
        {"skill": "work-system:continue", "plugin": "work-system",
         "plugin_version": {"value": "1.14.0", "source": "skill base directory after plugin update"},
         "model": {"value": "claude-opus-5", "source": "system prompt"}, "note": "after /reload-plugins"},
    ]
    assert errors_for(doc) == [], errors_for(doc)

    # An installed version without execution evidence must not pass as known.
    unevidenced = copy.deepcopy(doc)
    unevidenced["usage"]["skills"][0]["plugin_version"] = {"value": "1.14.0"}
    assert_error(unevidenced, "usage.skills[0].plugin_version.source")


def test_retrospective_contracts():
    good = prepared()

    none_with_items = copy.deepcopy(good)
    none_with_items["retrospective"]["suggestions"]["items"] = [
        {"change": "x", "observation": "y", "expected_benefit": "z", "uncertainty": {"level": "low", "note": None}}]
    assert_error(none_with_items, "must be empty when status is 'none'")

    provided_empty = copy.deepcopy(good)
    provided_empty["retrospective"]["suggestions"]["status"] = "provided"
    assert_error(provided_empty, "use status 'none'")

    wrong_author = copy.deepcopy(good)
    wrong_author["retrospective"]["suggestions"]["author"] = "user"
    assert_error(wrong_author, "suggestions.author")

    friction = copy.deepcopy(good)
    friction["retrospective"]["friction"] = [{
        "plugin": "pr-flow", "skill": "pr-flow:open", "expected": "a", "observed": "b", "impact": "c",
        "resolution": {"status": "unresolved", "detail": None},
        "suspected_cause": {"text": "maybe the poll interval", "confidence": "certain"},
        "basis": "model_assessment", "evidence": [], "related_reports": []}]
    assert_error(friction, "suspected_cause.confidence")

    repeat = copy.deepcopy(good)
    repeat["plugin_details"] = {"work-system": {"questions": [{
        "question": "Proceed?", "reason": None, "classification": "avoidable_repeat",
        "mandate_source": None, "mandate_scope": None, "answer_already_available": "unknown",
        "basis": "model_assessment"}], "handoff_gaps": [], "ambiguous_states": []}}
    assert_error(repeat, "'avoidable_repeat' requires answer_already_available 'yes'")

    unresolved_timeout = copy.deepcopy(good)
    unresolved_timeout["plugin_details"] = {"work-system": {"questions": [], "handoff_gaps": [],
        "ambiguous_states": [{"kind": "start", "observed": "launch timed out", "resolution": None,
                              "status": "resolved", "basis": "run_evidence"}]}}
    assert_error(unresolved_timeout, "resolution: is required when status is 'resolved'")

    swarm = copy.deepcopy(good)
    swarm["plugin_details"] = {"swarm": {"runs": [{
        "profile": unk(), "voices": {"planned": 4, "started": 4, "accepted": 5, "missing_results": 0,
                                     "empty_results": 1},
        "failures": [], "restarts": None,
        "findings": {"useful": 1, "rejected": 0, "rejection_reasons": []},
        "handoff": {"fix": None, "pr": None}, "benefit_vs_effort": None, "basis": "run_evidence"}]}}
    assert_error(swarm, "voices.accepted: exceeds started")

    unknown_plugin = copy.deepcopy(good)
    unknown_plugin["plugin_details"] = {"some-other-plugin": {}}
    assert_error(unknown_plugin, "plugin_details.some-other-plugin: unknown field")


def test_privacy_guards():
    good = prepared()

    escape = copy.deepcopy(good)
    escape["work"]["summary"] = "colored \x1b[31mtext"
    assert_error(escape, "contains control or bidi characters")
    bidi = copy.deepcopy(good)
    bidi["work"]["summary"] = "looks fine \u202etxt.exe"
    assert_error(bidi, "contains control or bidi characters")
    separator = copy.deepcopy(good)
    separator["work"]["summary"] = "line\u2028break"
    assert_error(separator, "contains control or bidi characters")
    emoji = copy.deepcopy(good)
    emoji["work"]["summary"] = "family \U0001F468\u200d\U0001F469 ok"  # ZWJ stays legal
    assert errors_for(emoji) == []

    # A stored file that bypassed the helper's redaction is malformed on read.
    leaked = copy.deepcopy(good)
    leaked["work"]["summary"] = "used token ghp_" + "a" * 36
    assert_error(leaked, "contains an unredacted credential")

    doc = draft()
    doc["work"]["pr"] = {"value": "https://user:tok@github.com/o/r/pull/7?token=abc#frag", "source": "gh"}
    doc["retrospective"]["worked_well"] = [{"observation": "ok", "basis": "run_evidence",
                                            "evidence": ["https://ci.example.com/run/1?sig=secret"]}]
    doc["project"] = insights.project_identity(str(SANDBOX))
    doc["project"]["remote"] = "https://me:pw@github.com/o/r.git"
    out, _ = insights.prepare(doc)
    assert out["work"]["pr"]["value"] == "https://github.com/o/r/pull/7"
    assert out["retrospective"]["worked_well"][0]["evidence"] == ["https://ci.example.com/run/1"]
    assert out["project"]["remote"] == "https://github.com/o/r.git"
    assert insights.sanitize_url("git@github.com:o/r.git") == "git@github.com:o/r.git"
    # Malformed ports and IPv6 literals must neither crash nor corrupt the host.
    assert insights.sanitize_url("http://localhost:99999/api?x=1") == "http://localhost:99999/api"
    assert insights.sanitize_url("https://example.test:not-a-port/repo") == "https://example.test:not-a-port/repo"
    assert insights.sanitize_url("https://[::1]:8443/run/1?token=x") == "https://[::1]:8443/run/1"


def test_credentials_are_redacted_not_rejected():
    store = fresh_store("redaction")
    token = "ghp_" + "b" * 36
    feedback = f"The {token} token leaked into the README — see https://example.com/cb?access_token=SECRET123&page=2"
    doc = draft(summary="Callback https://svc.example/cb?code=abc123 and key sk-ant-" + "c" * 24)
    doc["user_feedback"] = [{"text": feedback, "attribution": "user", "captured_via": "/insights:report argument"}]
    doc["work"]["pr"] = {"value": "http://[::1]:99999/pull/1", "source": "gh"}  # malformed port: no crash

    res = cli("write", "-", "--store", str(store), stdin=json.dumps(doc, ensure_ascii=False))
    assert res.returncode == 0, res.stderr
    out = kv(res.stdout)
    assert out["status"] == "stored" and out["redactions"] == "4", res.stdout
    saved = json.loads(cli("read", out["report_id"], "--store", str(store)).stdout)
    text = saved["user_feedback"][0]["text"]
    assert token not in text and "SECRET123" not in text
    assert text == ("The [REDACTED] token leaked into the README — see "
                    "https://example.com/cb?access_token=[REDACTED]&page=2")
    assert saved["work"]["summary"] == "Callback https://svc.example/cb?code=[REDACTED] and key [REDACTED]"

    # Redaction is idempotent: re-sending the stored report is an unchanged retry.
    again = cli("write", "-", "--store", str(store), stdin=json.dumps(saved, ensure_ascii=False))
    assert again.returncode == 0 and kv(again.stdout)["status"] == "unchanged", again.stderr
    assert kv(again.stdout)["redactions"] == "0"


def test_skeleton_is_complete_but_never_storable_untouched():
    root = SANDBOX / "skeleton-repo"
    shutil.rmtree(root, ignore_errors=True)
    repo = git_repo(root / "svc")
    (repo / "MANDATE.md").write_text("---\ntask: add-thing\n---\n")
    res = cli("skeleton", cwd=repo)
    assert res.returncode == 0, res.stderr
    skel = json.loads(res.stdout)
    assert skel["work"]["task_name"] == {"value": "add-thing", "source": "MANDATE.md task"}
    assert skel["work"]["branch"]["value"] == "main"

    store = fresh_store("skeleton")
    untouched = cli("write", "-", "--store", str(store), cwd=repo, stdin=res.stdout)
    assert untouched.returncode == insights.EXIT_INVALID, untouched.stdout
    for field in ("work.summary", "reporter.role", "reporter.model.reason", "usage.completeness"):
        assert field in untouched.stderr, field
    assert not store.exists() or not list(store.glob("*.json"))

    filled = copy.deepcopy(skel)
    filled.update(task_status="in_progress")
    filled["work"]["summary"] = "Adding the thing."
    for fact in filled["work"].values():
        if isinstance(fact, dict) and fact.get("value") is None:
            fact["reason"] = "not available"
    filled["reporter"].update(role="worker", role_source="lane session",
                              model={"value": "claude-opus-5", "source": "system prompt"})
    for k in ("runtime", "harness", "reasoning_effort", "session_id"):
        if filled["reporter"][k]["value"] is None:
            filled["reporter"][k]["reason"] = "not observed"
    filled["usage"].update(completeness="partial", completeness_reason="session only")
    retro = filled["retrospective"]
    retro["outcome"] = {"intended": "Add it.", "achieved": "Half done."}
    retro["difficulty"] = {"domain": {"level": "low", "reason": "small"},
                           "tooling": {"level": "low", "reason": "smooth"}}
    retro["suggestions"]["status"] = "none"
    ok = cli("write", "-", "--store", str(store), cwd=repo, stdin=json.dumps(filled))
    assert ok.returncode == 0, ok.stderr


def test_list_rejects_non_positive_limit():
    store = fresh_store("limit")
    for bad in ("-2", "0", "x"):
        res = cli("list", "--store", str(store), "--limit", bad)
        assert res.returncode == insights.EXIT_USAGE, (bad, res.returncode)


def test_worktrees_group_and_same_named_repos_stay_distinct():
    root = SANDBOX / "repos"
    shutil.rmtree(root, ignore_errors=True)
    main = git_repo(root / "a" / "proj")
    other = git_repo(root / "b" / "proj")  # unrelated repo, same directory name
    wt = root / "a" / "proj-wt"
    subprocess.run(GIT + ["-C", str(main), "worktree", "add", "-q", "-b", "task/x", str(wt)], check=True)
    (main / "sub").mkdir()

    id_main = insights.project_identity(str(main))
    id_sub = insights.project_identity(str(main / "sub"))
    id_wt = insights.project_identity(str(wt))
    id_other = insights.project_identity(str(other))

    assert id_main["ref_source"] == "git-main-worktree"
    assert id_main["ref"] == id_wt["ref"] == id_sub["ref"], (id_main, id_wt, id_sub)
    assert id_main["key"] == id_wt["key"]
    assert id_main["name"] == id_other["name"] == "proj"
    assert id_main["ref"] != id_other["ref"] and id_main["key"] != id_other["key"]

    ctx = insights.gather_context(str(wt))
    assert ctx["git"]["linked_worktree"] is True and ctx["git"]["branch"] == "task/x"
    assert insights.gather_context(str(main))["git"]["linked_worktree"] is False

    # A GIT_DIR leaked from a hook must not change which project is identified.
    res = cli("context", "--project-dir", str(other), env={"GIT_DIR": str(main / ".git")})
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["project"]["ref"] == id_other["ref"]


def test_non_git_fallback_is_labelled():
    plain = SANDBOX / "plain" / "notes"
    plain.mkdir(parents=True, exist_ok=True)
    explicit = insights.project_identity(str(plain))
    assert explicit["ref"] == f"dir:{plain}" and explicit["ref_source"] == "explicit-dir"
    assert explicit["remote"] is None

    res = cli("context", cwd=plain)
    assert res.returncode == 0, res.stderr
    ctx = json.loads(res.stdout)
    assert ctx["project"]["ref_source"] == "cwd" and ctx["project"]["ref"] == f"dir:{plain}"
    assert ctx["git"]["inside"] is False and ctx["task_hints"]["task_md"] is None


def test_store_location_resolution():
    env_store = os.environ.pop("INSIGHTS_STORE_DIR", None)
    try:
        path, source, _ = insights.resolve_store()
        assert path == SANDBOX / "xdg" / "gering-plugins" / "insights" / "v1" / "reports"
        assert source == "xdg:XDG_DATA_HOME"

        os.environ["XDG_DATA_HOME"] = "relative/data"
        path, source, private_from = insights.resolve_store()
        assert path == SANDBOX / "home" / ".local" / "share" / "gering-plugins" / "insights" / "v1" / "reports"
        assert "ignored non-absolute XDG_DATA_HOME" in source
        assert private_from == SANDBOX / "home" / ".local" / "share" / "gering-plugins" / "insights"

        os.environ["XDG_DATA_HOME"] = ""
        assert insights.resolve_store()[1] == "default:$HOME/.local/share"

        os.environ["INSIGHTS_STORE_DIR"] = "not/absolute"
        try:
            insights.resolve_store()
            raise AssertionError("relative INSIGHTS_STORE_DIR accepted")
        except insights.UsageError:
            pass
    finally:
        os.environ["XDG_DATA_HOME"] = str(SANDBOX / "xdg")
        os.environ.pop("INSIGHTS_STORE_DIR", None)
        if env_store:
            os.environ["INSIGHTS_STORE_DIR"] = env_store


def test_default_store_is_private():
    report = prepared()
    reports, _, private_from = insights.resolve_store()
    insights.ensure_private_dir(reports, private_from)
    assert insights.publish(reports, report) == "stored"
    for d in (private_from, private_from / "v1", reports):
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700, d
    stored = reports / f"{report['report_id']}.json"
    assert stat.S_IMODE(os.stat(stored).st_mode) == 0o600
    assert not list(reports.glob(".*.tmp")), "temp file left behind"
    assert str(reports).startswith(str(SANDBOX)), "test escaped the sandbox"

    # An existing override directory is never chmod'ed: open or symlinked ones are refused.
    shared = SANDBOX / "shared-dir"
    shared.mkdir(exist_ok=True)
    os.chmod(shared, 0o755)
    res = cli("write", "-", "--store", str(shared), stdin=json.dumps(draft()))
    assert res.returncode == insights.EXIT_STORAGE and "group/others" in res.stderr, res.stderr
    assert stat.S_IMODE(os.stat(shared).st_mode) == 0o755, "override dir was chmod'ed"
    assert not list(shared.glob("*.json"))

    private = SANDBOX / "private-target"
    private.mkdir(mode=0o700, exist_ok=True)
    link = SANDBOX / "store-link"
    if not link.exists():
        link.symlink_to(private)
    res = cli("write", "-", "--store", str(link), stdin=json.dumps(draft()))
    assert res.returncode == insights.EXIT_STORAGE and "symlink" in res.stderr, res.stderr
    assert not list(private.glob("*.json"))


def test_retry_is_idempotent_and_collisions_fail():
    store = fresh_store("retry")
    doc = draft()
    doc["report_id"] = insights.new_report_id()
    doc["recorded_at"] = "2026-09-16T10:00:00Z"
    body = json.dumps(doc)

    first = cli("write", "-", "--store", str(store), stdin=body)
    assert first.returncode == 0 and kv(first.stdout)["status"] == "stored", first.stderr
    path = Path(kv(first.stdout)["path"])
    original = path.read_bytes()

    again = cli("write", "-", "--store", str(store), stdin=body)
    assert again.returncode == 0 and kv(again.stdout)["status"] == "unchanged", again.stderr

    changed = dict(doc, task_status="blocked")
    clash = cli("write", "-", "--store", str(store), stdin=json.dumps(changed))
    assert clash.returncode == insights.EXIT_COLLISION, (clash.returncode, clash.stderr)
    assert "status=" not in clash.stdout
    assert "never replaced" in clash.stderr
    assert path.read_bytes() == original, "existing report was modified"


def test_concurrent_writes_never_overwrite():
    store = fresh_store("concurrent")
    shared_id = insights.new_report_id()
    procs = []
    for i in range(8):
        doc = draft(summary=f"writer {i}")
        doc["report_id"] = shared_id
        doc["recorded_at"] = "2026-09-16T10:00:00Z"
        procs.append(subprocess.Popen(
            [sys.executable, str(SCRIPT), "write", "-", "--store", str(store), "--project-dir", str(SANDBOX)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        procs[-1].stdin.write(json.dumps(doc))
        procs[-1].stdin.close()
    results = []
    for p in procs:
        out = p.stdout.read()
        p.wait(timeout=60)
        p.stdout.close()
        p.stderr.close()
        results.append((p.returncode, out))
    winners = [out for code, out in results if code == 0]
    losers = [code for code, _ in results if code != 0]
    assert len(winners) == 1, results
    assert set(losers) == {insights.EXIT_COLLISION}, results
    stored = json.loads((store / f"{shared_id}.json").read_text())
    assert errors_for(stored) == [] and stored["work"]["summary"].startswith("writer ")
    assert kv(winners[0])["status"] == "stored"

    # Distinct IDs written concurrently all land, with no partial or temp files.
    procs = []
    for i in range(8):
        procs.append(subprocess.Popen(
            [sys.executable, str(SCRIPT), "write", "-", "--store", str(store), "--project-dir", str(SANDBOX)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        procs[-1].stdin.write(json.dumps(draft(summary=f"parallel {i}")))
        procs[-1].stdin.close()
    for p in procs:
        p.wait(timeout=60)
        p.stdout.close()
        p.stderr.close()
        assert p.returncode == 0
    files = list(store.glob("*.json"))
    assert len(files) == 9, len(files)
    assert not list(store.glob(".*")), "temp files left behind"
    for f in files:
        assert errors_for(json.loads(f.read_text())) == []


def test_failed_write_never_claims_success():
    # Invalid report: explicit failure, nothing written.
    store = fresh_store("failed")
    bad = draft()
    bad["task_status"] = "done"
    res = cli("write", "-", "--store", str(store), stdin=json.dumps(bad))
    assert res.returncode == insights.EXIT_INVALID
    assert "NOT saved" in res.stderr and "status=" not in res.stdout
    assert not store.exists() or not list(store.glob("*.json"))

    # Unparseable input.
    res = cli("write", "-", "--store", str(store), stdin="{not json")
    assert res.returncode == insights.EXIT_INVALID and "status=" not in res.stdout

    # Unwritable store directory.
    if os.geteuid() != 0:  # root ignores directory permissions
        store.mkdir(parents=True, exist_ok=True)
        os.chmod(store, 0o500)  # private but unwritable
        try:
            res = cli("write", "-", "--store", str(store), stdin=json.dumps(draft()))
            assert res.returncode == insights.EXIT_STORAGE, (res.returncode, res.stderr)
            assert "report NOT saved" in res.stderr and "status=" not in res.stdout
        finally:
            os.chmod(store, 0o700)
        assert not list(store.glob("*.json"))

    # The store path is a file, not a directory.
    blocker = SANDBOX / "stores" / "is-a-file"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("x")
    res = cli("write", "-", "--store", str(blocker), stdin=json.dumps(draft()))
    assert res.returncode == insights.EXIT_STORAGE and "status=" not in res.stdout, res.stderr

    # link() failing mid-publish surfaces as a storage error, not success.
    store = fresh_store("link-fails")
    store.mkdir(parents=True, mode=0o700)
    original_link = os.link

    def broken_link(*_a, **_k):
        raise OSError(28, "No space left on device")

    os.link = broken_link
    try:
        try:
            insights.publish(store, prepared())
            raise AssertionError("publish reported success despite link failure")
        except insights.StorageError as e:
            assert "No space left" in str(e)
    finally:
        os.link = original_link
    assert not list(store.iterdir()), "partial files left behind"


def test_corrupt_store_is_reported_honestly():
    store = fresh_store("corrupt")
    good = cli("write", "-", "--store", str(store), stdin=json.dumps(draft()))
    assert good.returncode == 0, good.stderr
    good_id = kv(good.stdout)["report_id"]

    broken_id = insights.new_report_id()
    (store / f"{broken_id}.json").write_text('{"schema": "insights.report/v1", "trunc')
    invalid_id = insights.new_report_id()
    doc = prepared()
    doc["report_id"] = invalid_id
    doc["task_status"] = "finished"
    (store / f"{invalid_id}.json").write_text(json.dumps(doc))
    renamed_id = insights.new_report_id()
    (store / f"{renamed_id}.json").write_text((store / f"{good_id}.json").read_text())
    (store / ".tmp-in-flight.tmp").write_text("{")  # temp files are not reports
    huge_id = insights.new_report_id()
    (store / f"{huge_id}.json").write_text(" " * (insights.MAX_STORED_FILE_BYTES + 1))

    res = cli("list", "--store", str(store))
    assert res.returncode == 0, res.stderr
    assert "reports=1 malformed=4" in res.stdout, res.stdout
    assert res.stdout.count("MALFORMED") == 4

    listed = json.loads(cli("list", "--store", str(store), "--json").stdout)
    assert [r["report_id"] for r in listed["reports"]] == [good_id]
    assert len(listed["malformed"]) == 4

    res = cli("read", huge_id, "--store", str(store))
    assert res.returncode == insights.EXIT_INVALID and "not loaded" in res.stderr, res.stderr

    res = cli("read", broken_id, "--store", str(store))
    assert res.returncode == insights.EXIT_INVALID and "malformed" in res.stderr and not res.stdout
    res = cli("read", invalid_id, "--store", str(store))
    assert res.returncode == insights.EXIT_INVALID and "task_status" in res.stderr
    res = cli("read", renamed_id, "--store", str(store))
    assert res.returncode == insights.EXIT_INVALID
    res = cli("read", insights.new_report_id(), "--store", str(store))
    assert res.returncode == insights.EXIT_NOT_FOUND
    res = cli("read", "../../secret", "--store", str(store))
    assert res.returncode == insights.EXIT_USAGE


def test_list_filters_by_project_and_task():
    store = fresh_store("filters")
    root = SANDBOX / "filter-repos"
    shutil.rmtree(root, ignore_errors=True)
    repo = git_repo(root / "x" / "app")
    same_name = git_repo(root / "y" / "app")

    def write(project_dir, **kw):
        res = cli("write", "-", "--store", str(store), "--project-dir", str(project_dir),
                  stdin=json.dumps(draft(**kw)))
        assert res.returncode == 0, res.stderr
        return kv(res.stdout)["report_id"]

    a = write(repo)
    no_task = draft(task_status="unknown")
    no_task["work"]["task_name"] = unk("no work-system task")
    res = cli("write", "-", "--store", str(store), "--project-dir", str(repo), stdin=json.dumps(no_task))
    b = kv(res.stdout)["report_id"]
    c = write(same_name)

    def ids(*args, cwd=None):
        res = cli("list", "--store", str(store), "--json", *args, cwd=cwd)
        assert res.returncode == 0, res.stderr
        return sorted(r["report_id"] for r in json.loads(res.stdout)["reports"])

    assert ids("--here", cwd=repo) == sorted([a, b])
    assert ids("--project", insights.project_identity(str(same_name))["key"]) == [c]
    assert ids("--project", "app") == sorted([a, b, c])  # a bare name is ambiguous by design
    assert ids("--task", "demo-task") == sorted([a, c])
    assert ids("--status", "unknown") == [b]
    assert ids("--trigger", "handoff") == []


def test_demo_manual_mid_task_report_roundtrip():
    """Documented skill path: context → draft → write via stdin → read back."""
    store = fresh_store("demo")
    root = SANDBOX / "demo-repo"
    shutil.rmtree(root, ignore_errors=True)
    repo = git_repo(root / "shop")
    wt = root / "shop-wt"
    subprocess.run(GIT + ["-C", str(repo), "worktree", "add", "-q", "-b", "task/fix-cart", str(wt)], check=True)
    (wt / "TASK.md").write_text("# Fix the cart total\n")

    ctx = json.loads(cli("context", "--store", str(store), cwd=wt).stdout)
    assert ctx["task_hints"]["task_title"] == "Fix the cart total"
    assert ctx["store"]["dir"] == str(store)

    user_text = "Der /cycle-Poll hat 3× gewartet, obwohl das Review längst da war — bitte prüfen."
    doc = draft(summary="Mid-task: cart total fix implemented, review round 1 in progress.")
    doc["work"]["task_name"] = {"value": "fix-cart", "source": "branch name"}
    doc["work"]["branch"] = {"value": ctx["git"]["branch"], "source": "insights.py context"}
    doc["user_feedback"] = [{"text": user_text, "attribution": "user", "captured_via": "/insights:report argument"}]
    doc["retrospective"]["friction"] = [{
        "plugin": "pr-flow", "skill": "pr-flow:cycle",
        "expected": "Polling stops once the review is posted.",
        "observed": "Three further poll waits after the review appeared (per user feedback).",
        "impact": "Several minutes of idle waiting.",
        "resolution": {"status": "unresolved", "detail": None},
        "suspected_cause": None, "basis": "user_feedback", "evidence": [], "related_reports": []}]
    res = cli("write", "-", "--store", str(store), cwd=wt, stdin=json.dumps(doc, ensure_ascii=False))
    assert res.returncode == 0, res.stderr
    out = kv(res.stdout)
    assert out["status"] == "stored" and int(out["gaps"]) > 0

    back = cli("read", out["report_id"], "--store", str(store))
    assert back.returncode == 0, back.stderr
    saved = json.loads(back.stdout)
    assert saved["user_feedback"][0]["text"] == user_text  # verbatim, umlauts intact
    assert saved["project"]["ref"] == insights.project_identity(str(repo))["ref"]  # main, not worktree
    assert saved["report_trigger"] == "manual" and saved["task_status"] == "in_progress"


def test_demo_no_task_project_report_outside_git():
    store = fresh_store("demo-no-task")
    folder = SANDBOX / "research-notes"
    folder.mkdir(exist_ok=True)
    doc = draft(summary="Agent snapshot: evaluated two note-taking layouts; no task file involved.",
                task_status="unknown")
    for k in ("task_name", "branch"):
        doc["work"][k] = unk("no work-system task / not a git repository")
    res = cli("write", "-", "--store", str(store), cwd=folder, stdin=json.dumps(doc))
    assert res.returncode == 0, res.stderr
    saved = json.loads(cli("read", kv(res.stdout)["report_id"], "--store", str(store)).stdout)
    assert saved["project"]["ref_source"] == "cwd"
    assert saved["project"]["ref"] == f"dir:{folder}"
    assert saved["work"]["task_name"]["value"] is None


# --------------------------------------------------------------------------- main


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"ok   {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
        real_default = Path.home()  # HOME is the sandbox; nothing outside it was used
        assert str(real_default).startswith(str(SANDBOX))
    finally:
        for p in SANDBOX.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    os.chmod(p, 0o700)
            except OSError:
                pass
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
