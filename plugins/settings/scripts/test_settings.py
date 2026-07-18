#!/usr/bin/env python3
"""Self-contained tests for settings.py. Run by check-structure.py in CI.

Uses a throwaway plugins dir + project root via SETTINGS_PLUGINS_DIR /
SETTINGS_PROJECT_ROOT so nothing touches the real repo config.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings  # noqa: E402

SCHEMA = {
    "x-plugin": "demo",
    "x-config-file": ".demo.toml",
    "type": "object",
    "properties": {
        "paths": {
            "type": "object",
            "properties": {
                "tasks_dir": {"type": "string", "default": "tasks"},
                "worktrees_dir": {"type": "string", "default": ".claude/worktrees"},
            },
        },
        "branches": {
            "type": "object",
            "properties": {
                "task_prefix": {"type": "string", "default": "task/"},
                "backup_prefixes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["backup/", "old/"],
                },
            },
        },
        "behavior": {
            "type": "object",
            "properties": {
                "copy_task_to_worktree": {"type": "boolean", "default": True},
                "retries": {"type": "integer", "default": 3},
            },
        },
        "mode": {
            "type": "object",
            "properties": {
                "knowledge_mode": {
                    "type": "string",
                    "enum": ["claude", "neutral", "symlinked"],
                    "default": "claude",
                },
            },
        },
        "related_projects": {
            "type": "object",
            "x-semantic": "related-projects",
            "default": {},
        },
    },
}


def make_env(tmp: Path) -> tuple[Path, Path]:
    """Create a plugins dir with the demo schema + a project root; wire env."""
    plugins = tmp / "plugins"
    schema_dir = plugins / "demo" / "schema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "settings.schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    project = tmp / "project"
    project.mkdir()
    os.environ["SETTINGS_PLUGINS_DIR"] = str(plugins)
    os.environ["SETTINGS_PROJECT_ROOT"] = str(project)
    return plugins, project


def run(argv: list[str]) -> int:
    return settings.main(argv)


def test_defaults_and_resolve():
    schema = SCHEMA
    defaults = settings.schema_defaults(schema)
    assert defaults["paths"]["tasks_dir"] == "tasks"
    assert defaults["branches"]["backup_prefixes"] == ["backup/", "old/"]
    assert defaults["behavior"]["retries"] == 3
    assert defaults["related_projects"] == {}
    # user override replaces just the touched leaf; siblings survive
    merged = settings.resolve(schema, {"paths": {"tasks_dir": "todo"}})
    assert merged["paths"]["tasks_dir"] == "todo"
    assert merged["paths"]["worktrees_dir"] == ".claude/worktrees"
    print("ok: defaults + resolve")


def test_validation(project: Path):
    # type error, enum error, unknown key
    bad = {
        "behavior": {"retries": "three"},
        "mode": {"knowledge_mode": "bogus"},
        "paths": {"nope": "x"},
    }
    findings = settings.validate(SCHEMA, bad, project)
    levels = [lvl for lvl, _ in findings]
    msgs = " | ".join(m for _, m in findings)
    assert "error" in levels, msgs
    assert "expected integer" in msgs, msgs
    assert "not in allowed values" in msgs, msgs
    assert any(lvl == "warn" and "unknown key" in m for lvl, m in findings), msgs
    # a clean config validates with no findings
    assert settings.validate(SCHEMA, {"paths": {"tasks_dir": "t"}}, project) == []
    print("ok: validation (type/enum/unknown)")


def test_related_projects(project: Path):
    existing = project / "sibling"
    existing.mkdir()
    cfg = {
        "related_projects": {
            "backend": str(existing),  # string form, exists
            "frontend": {"path": str(project / "missing"), "role": "ui", "tags": ["web"]},
            "broken": {"role": "x"},  # table missing 'path'
        }
    }
    findings = settings.validate(SCHEMA, cfg, project)
    msgs = " | ".join(f"{l}:{m}" for l, m in findings)
    assert any(l == "warn" and "does not exist" in m and "frontend" in m for l, m in findings), msgs
    assert any(l == "error" and "missing 'path'" in m for l, m in findings), msgs
    assert not any("backend" in m for _, m in findings), msgs  # existing path: silent
    norm = settings.normalize_related_projects(cfg["related_projects"])
    assert norm["backend"] == {"path": str(existing)}
    assert norm["frontend"]["role"] == "ui"
    print("ok: related_projects semantics")


def test_toml_roundtrip():
    data = {
        "paths": {"tasks_dir": "tasks"},
        "branches": {"backup_prefixes": ["a/", "b/"]},
        "behavior": {"copy_task_to_worktree": False, "retries": 5},
        "related_projects": {"api": {"path": "/x", "tags": ["p"]}},
    }
    import tomllib

    reparsed = tomllib.loads(settings.dump_toml(data))
    assert reparsed == data, reparsed
    assert settings.dump_toml({}) == ""

    # control chars in a string value must escape so the file reparses (#1)
    tricky = {"paths": {"tasks_dir": "a\nb\tc\\d\"e"}}
    assert tomllib.loads(settings.dump_toml(tricky)) == tricky
    # non-bare table names / keys must be quoted, not emitted raw (#7)
    special = {"related_projects": {"web api": {"path": "/x"}, "a.b": {"path": "/y"}}}
    assert tomllib.loads(settings.dump_toml(special)) == special
    print("ok: toml dump round-trips (control chars + non-bare keys)")


def test_config_filename_sandbox():
    # x-config-file must be a plain basename — traversal / absolute / ~ rejected (#3)
    for bad in ["../escape.toml", "/tmp/x.toml", "a/b.toml", "~/x.toml", "..", "."]:
        try:
            settings.config_filename({"x-plugin": "demo", "x-config-file": bad})
            assert False, f"expected rejection for {bad!r}"
        except settings.SettingsError:
            pass
    assert settings.config_filename({"x-config-file": ".demo.toml"}) == ".demo.toml"
    print("ok: config filename sandbox")


def test_set_guards(project: Path):
    cfg_path = project / ".demo.toml"
    if cfg_path.exists():
        cfg_path.unlink()
    # #5: setting a section (object node) is rejected, nothing written
    assert run(["set", "demo.paths", "foo"]) == 1
    assert not cfg_path.exists()
    # #8: descending past a scalar leaf is rejected
    assert run(["set", "demo.paths.tasks_dir.extra", "x"]) == 1
    assert not cfg_path.exists()
    # #4: an array with wrong element types is refused before writing
    assert run(["set", "demo.branches.backup_prefixes", "[1,2,3]"]) == 1
    assert not cfg_path.exists()
    # valid array still works
    assert run(["set", "demo.branches.backup_prefixes", '["x/","y/"]']) == 0
    import tomllib

    with cfg_path.open("rb") as fh:
        assert tomllib.load(fh)["branches"]["backup_prefixes"] == ["x/", "y/"]
    # #6 guard: a mistyped dynamic related_projects field can't silently corrupt
    assert run(["set", "demo.related_projects.frontend.tags", '["web"]']) == 1
    # string-shorthand related_projects entry still works (path warns, not errors)
    assert run(["set", "demo.related_projects.backend", "/tmp"]) == 0
    with cfg_path.open("rb") as fh:
        assert tomllib.load(fh)["related_projects"]["backend"] == "/tmp"
    cfg_path.unlink()  # leave a clean slate for the next test
    print("ok: set guards (#4 #5 #6 #8)")


def test_coercion():
    assert settings.coerce_value("false", {"type": "boolean"}) is False
    assert settings.coerce_value("on", {"type": "boolean"}) is True
    assert settings.coerce_value("7", {"type": "integer"}) == 7
    assert settings.coerce_value("a, b ,c", {"type": "array"}) == ["a", "b", "c"]
    assert settings.coerce_value('["x","y"]', {"type": "array"}) == ["x", "y"]
    assert settings.coerce_value("plain", {"type": "string"}) == "plain"
    try:
        settings.coerce_value("nope", {"type": "integer"})
        assert False, "expected SettingsError"
    except settings.SettingsError:
        pass
    print("ok: coercion")


def test_cli_set_get_unset(project: Path):
    cfg_path = project / ".demo.toml"
    assert run(["set", "demo.paths.tasks_dir", "todo"]) == 0
    assert cfg_path.exists()
    import tomllib

    with cfg_path.open("rb") as fh:
        assert tomllib.load(fh)["paths"]["tasks_dir"] == "todo"
    # get returns the resolved value (override wins)
    assert run(["get", "demo.paths.tasks_dir"]) == 0
    # get a default (not overridden) still resolves
    assert run(["get", "demo.branches.task_prefix"]) == 0
    # enum-safe set rejects an invalid value and does not write it
    assert run(["set", "demo.mode.knowledge_mode", "bogus"]) == 1
    assert run(["set", "demo.mode.knowledge_mode", "neutral"]) == 0
    # boolean coercion through the CLI
    assert run(["set", "demo.behavior.copy_task_to_worktree", "false"]) == 0
    with cfg_path.open("rb") as fh:
        loaded = tomllib.load(fh)
    assert loaded["behavior"]["copy_task_to_worktree"] is False
    assert loaded["mode"]["knowledge_mode"] == "neutral"
    # unset prunes the emptied section
    assert run(["unset", "demo.behavior.copy_task_to_worktree"]) == 0
    with cfg_path.open("rb") as fh:
        assert "behavior" not in tomllib.load(fh)
    # unset a missing key errors
    assert run(["unset", "demo.paths.missing"]) == 1
    print("ok: cli set/get/unset")


def test_cli_list_show_validate():
    assert run(["list"]) == 0
    assert run(["show", "demo"]) == 0  # resolved by default
    assert run(["show", "demo", "--overrides"]) == 0
    assert run(["show", "--json"]) == 0
    assert run(["defaults", "demo", "--json"]) == 0
    assert run(["validate"]) == 0  # current config is valid
    assert run(["get", "demo.does.not.exist"]) == 1
    assert run(["show", "no-such-plugin"]) == 1
    print("ok: cli list/show/validate")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, project = make_env(tmp)
        test_defaults_and_resolve()
        test_validation(project)
        test_related_projects(project)
        test_toml_roundtrip()
        test_config_filename_sandbox()
        test_coercion()
        test_set_guards(project)
        test_cli_set_get_unset(project)
        test_cli_list_show_validate()
    print("\nall settings tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
