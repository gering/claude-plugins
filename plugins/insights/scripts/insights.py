#!/usr/bin/env python3
"""insights.py — the one validation + persistence path for insights reports.

Every producer (the manual `/insights:report` skill today, lifecycle handoffs
later) goes through this script, so the schema, the privacy rules and the
no-overwrite store live in exactly one place. The contract is documented in
`docs/REPORT-CONTRACT.md`; this file is its enforcement.

Subcommands:
  context [--project-dir DIR]      Observable facts for a draft (project identity,
                                   git branch, task hints, runtime env, store path).
  skeleton [--project-dir DIR] [--trigger T]  A complete draft: observed values
                                   prefilled, everything else empty (fails validation until filled).
  new-id                           Print a fresh report ID (for retry-safe producers).
  write FILE|- [--project-dir DIR] Fill report_id/recorded_at/project if absent,
        [--json]                   sanitize URLs, redact credentials, validate, publish atomically.
  validate FILE|- [--project-dir DIR]  Same fill + validation, never writes.
  read REPORT_ID                   Print one stored report (validated on read).
  list [--here|--project P] [--task T] [--trigger X] [--status S] [--limit N] [--json]
  store                            Print the resolved store directory and its source.

Every storing subcommand also accepts `--store DIR` (absolute) to override the
location; `INSIGHTS_STORE_DIR` does the same from the environment (tests use it).

Exit codes: 0 ok · 1 invalid report / malformed stored data · 2 usage ·
3 report ID collision (different content already stored under that ID) ·
4 storage failure (nothing saved) · 5 report not found.

Python 3.8+ stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA_ID = "insights.report/v1"
ENV_STORE = "INSIGHTS_STORE_DIR"
# $HOME/.gering-plugins/insights/reports — `.gering-plugins` is shared by the
# marketplace's plugins; everything from `insights` down is owned by this plugin
# and kept private (0700 dirs, 0600 files). Deliberately outside ~/.claude so
# reports survive plugin uninstalls and are reachable by non-Claude workers.
STORE_SUBPATH = (".gering-plugins", "insights", "reports")

EXIT_OK, EXIT_INVALID, EXIT_USAGE, EXIT_COLLISION, EXIT_STORAGE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4, 5

MAX_REPORT_BYTES = 64 * 1024
MAX_TEXT = 2000       # narrative fields — a report is a summary, not a transcript
MAX_FEEDBACK = 8000   # verbatim user feedback may be longer than agent prose
MAX_SHORT = 300       # identifiers, labels, evidence references
MAX_ITEMS = 50

# Matched with fullmatch: `$` would also accept a trailing newline, which then
# ends up inside a file name and splits the key=value output.
ID_RE = re.compile(r"ins-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
# Whole seconds only, so recorded_at sorts correctly as a string.
TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
# Control characters other than \n and \t, plus bidi overrides/isolates and
# Unicode line/paragraph separators — rejected so stored text can never carry
# terminal escapes or visually reordered lines into a later `read`/`list`.
# ZWJ/ZWNJ stay allowed (emoji sequences, some scripts).
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
REDACTED = "[REDACTED]"  # no ":" or "=": a redacted value must not match a pattern again
# High-confidence credential shapes, replaced by REDACTED before validation.
# Redacting instead of rejecting keeps verbatim user feedback storable without
# the producer editing it. Not a DLP scanner — a guard against the obvious paste
# of a token or key into a report.
SECRET_SUBS = [
    # a whole key block; a header without its END marker (prose mentioning the
    # format, or a truncated paste) loses only the header line itself
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), REDACTED),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[^\n]*"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"), REDACTED),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"), REDACTED),
    # credentials in a URL: scheme://user:pass@host
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@?#]+:[^/\s@?#]+@"), r"\1" + REDACTED + "@"),
    # token-like query parameters anywhere in text
    (re.compile(r"([?&](?:access_token|refresh_token|id_token|token|api_key|apikey|key|sig|signature|"
                r"secret|client_secret|password|passwd|auth|code)=)(?!\[REDACTED\])[^&#\s]*[^&#\s),.;:'\"]", re.I), r"\1" + REDACTED),
]

TRIGGERS = ("manual", "handoff", "close")
TASK_STATUSES = ("in_progress", "blocked", "completed", "aborted", "unknown")
ROLES = ("worker", "manager", "advisor", "user", "unknown")
BASES = ("user_feedback", "model_assessment", "run_evidence", "second_hand")
LEVELS = ("low", "medium", "high", "unknown")
CONFIDENCE = ("low", "medium", "high")
RESOLUTIONS = ("resolved", "workaround", "unresolved", "unknown")
COMPLETENESS = ("complete", "partial", "unknown")
REF_SOURCES = ("git-main-worktree", "explicit-dir", "cwd")
INTERVENTIONS = ("extra_attempt", "manual_intervention", "user_question", "restart")
QUESTION_CLASSES = ("new_approval", "avoidable_repeat", "clarification", "unknown")
YES_NO_UNKNOWN = ("yes", "no", "unknown")
KNOWLEDGE_FOUND = ("yes", "no", "partial", "unknown")


class UsageError(Exception):
    pass


class StorageError(Exception):
    pass


# --------------------------------------------------------------------------- utils


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def new_report_id() -> str:
    return f"ins-{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(6)}"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


URL_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/?#\s]*)([^?#\s]*)(?:[?#]\S*)?")


def sanitize_url(value: str) -> str:
    """Drop userinfo, query and fragment from a string that is exactly one URL.

    Credentials and tracking/session parameters live there; the host + path is
    the part a later reader needs. Anything else (`git@host:org/repo`, prose) is
    returned unchanged. Parsed with an anchored pattern rather than urlsplit:
    `.port` raises on a malformed port, `.hostname` drops IPv6 brackets, and a
    redacted `[REDACTED]@host` netloc makes urlsplit reject the URL outright.
    """
    if not isinstance(value, str):
        return value
    m = URL_RE.fullmatch(value)
    if not m or not m.group(2):
        return value
    return m.group(1) + m.group(2).rpartition("@")[2] + m.group(3)


def git_env() -> dict:
    # A GIT_DIR/GIT_WORK_TREE leaked from a hook would make identity describe
    # some other repository than the directory we were asked about.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run_git(cwd: Path, *args: str):
    try:
        res = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, env=git_env(), stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


# ----------------------------------------------------------------- project identity


def project_identity(project_dir=None) -> dict:
    """Readable name + unambiguous local reference for the project.

    In git the reference is the canonical MAIN checkout (first `git worktree
    list` entry, realpath'd), so every linked worktree of one checkout groups
    together while two unrelated repos that happen to share a directory name
    stay distinct. Outside git the canonical (realpath'd) directory is the
    fallback, labelled with where it came from. Nothing is written into the
    user's repository.
    """
    explicit = project_dir is not None
    base = Path(project_dir) if explicit else Path.cwd()
    try:
        base = base.resolve(strict=True)
    except OSError as e:
        raise UsageError(f"project directory not accessible: {base} ({e})")
    if not base.is_dir():
        raise UsageError(f"project directory is not a directory: {base}")

    main = None
    porcelain = run_git(base, "worktree", "list", "--porcelain")
    if porcelain and porcelain.startswith("worktree "):
        first = porcelain.split("\n", 1)[0][len("worktree "):]
        try:
            main = Path(first).resolve(strict=True)
        except OSError:
            main = None

    if main is not None:
        ref, source, name = f"git:{main}", "git-main-worktree", main.name
        remote = run_git(main, "config", "--get", "remote.origin.url")
        remote = sanitize_url(remote) if remote else None
    else:
        ref, source, name = f"dir:{base}", ("explicit-dir" if explicit else "cwd"), base.name
        remote = None
    return {
        "name": name or str(base),
        "ref": ref,
        "ref_source": source,
        "key": hashlib.sha256(ref.encode("utf-8")).hexdigest()[:16],
        "remote": remote,
    }


# -------------------------------------------------------------------------- store


def resolve_store(cli_store=None):
    """Return (reports_dir, source, private_from).

    `private_from` is the first directory this plugin owns and keeps at 0700.
    """
    override = cli_store or os.environ.get(ENV_STORE) or None
    if override:
        label = "--store" if cli_store else f"env:{ENV_STORE}"
        if not os.path.isabs(override):
            raise UsageError(f"{label} must be an absolute path, got {override!r}")
        reports = Path(override)
        return reports, label, reports

    home = os.environ.get("HOME", "")
    if not home or not os.path.isabs(home):
        raise StorageError("cannot resolve the store directory: HOME is unset or not absolute")
    base = Path(home)
    reports = base.joinpath(*STORE_SUBPATH)
    return reports, "default:$HOME/.gering-plugins", base / STORE_SUBPATH[0] / STORE_SUBPATH[1]


def ensure_private_dir(path: Path, private_from: Path) -> None:
    """Create the store directory; directories from `private_from` down must be private.

    Missing directories are created 0700. Existing ones are never chmod'ed — an
    override may point at a directory the user uses for something else — so a
    symlink, a directory owned by someone else, or one that group/others can
    access is refused instead of silently tightened.
    """
    try:
        private_parts = path.relative_to(private_from).parts
    except ValueError:
        private_parts = ()
    shared_root = private_from.parent
    try:
        shared_root.mkdir(parents=True, exist_ok=True)
        cur = shared_root
        for part in (private_from.name,) + tuple(private_parts):
            cur = cur / part
            try:
                os.mkdir(cur, 0o700)
            except FileExistsError:
                pass
            st = os.lstat(cur)
            if stat.S_ISLNK(st.st_mode):
                raise StorageError(f"store directory is a symlink (use the real path): {cur}")
            if not stat.S_ISDIR(st.st_mode):
                raise StorageError(f"store path component is not a directory: {cur}")
            if st.st_uid != os.getuid():
                raise StorageError(f"store directory not owned by the current user: {cur}")
            if st.st_mode & 0o077:
                raise StorageError(
                    f"store directory is accessible to group/others (mode "
                    f"{stat.S_IMODE(st.st_mode):o}); reports must stay private — "
                    f"`chmod 700` it or choose another directory: {cur}")
    except OSError as e:
        raise StorageError(f"cannot prepare store directory {path}: {e}")


def publish(reports: Path, report: dict) -> str:
    """Atomically publish `report`; never replace an existing file.

    The JSON is written to a private temp file in the same directory, fsynced,
    then hard-linked to its final name. `link` fails if the name exists, so two
    concurrent writers can never overwrite each other and a reader never sees
    partial JSON. Returns "stored" or "unchanged" (identical content already
    stored under this ID); raises on collision or I/O failure.
    """
    final = reports / f"{report['report_id']}.json"
    payload = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{report['report_id']}.", suffix=".tmp", dir=str(reports))
        with os.fdopen(fd, "wb") as fh:  # mkstemp creates the file 0600
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, final)
        except FileExistsError:
            return compare_existing(final, report)
        try:
            dfd = os.open(str(reports), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass  # directory fsync is best-effort (unsupported on some filesystems)
        return "stored"
    except OSError as e:
        raise StorageError(f"cannot write report to {reports}: {e}")
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class CollisionError(Exception):
    pass


def read_bounded(path: Path, limit: int) -> str:
    """Read a regular file of at most `limit` bytes.

    One descriptor for the type check, the size check and the read: no symlink
    following, no blocking on a FIFO, no file that grows between stat and read.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(str(path), flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_size > limit:
            raise ValueError(f"file is {st.st_size} bytes (max {limit}); not loaded")
        chunks, remaining = [], limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise ValueError(f"file grew beyond {limit} bytes while reading; not loaded")
        return data.decode("utf-8")
    finally:
        os.close(fd)


def compare_existing(final: Path, report: dict) -> str:
    try:
        existing = json.loads(read_bounded(final, MAX_STORED_FILE_BYTES))
    except (OSError, ValueError) as e:
        raise CollisionError(f"{final.name} already exists and is unreadable ({e}); not replaced")
    if canonical(existing) == canonical(report):
        return "unchanged"
    raise CollisionError(
        f"report_id {report['report_id']} is already stored with different content; "
        "stored reports are never replaced — write a new report that links the old ID "
        "in work.related_reports"
    )


# ---------------------------------------------------------------------- validation


class Validator:
    def __init__(self):
        self.errors: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    # -- primitives
    def obj(self, path, val, required, optional=()) -> bool:
        if not isinstance(val, dict):
            self.err(path, "must be an object")
            return False
        for k in required:
            if k not in val:
                self.err(f"{path}.{k}", "is required")
        allowed = set(required) | set(optional)
        for k in val:
            if k not in allowed:
                self.err(f"{path}.{k}", "unknown field")
        return True

    def text(self, path, val, max_len=MAX_TEXT, nullable=False) -> None:
        if val is None:
            if not nullable:
                self.err(path, "must be a non-empty string")
            return
        if not isinstance(val, str) or not val.strip():
            self.err(path, "must be a non-empty string" + (" or null" if nullable else ""))
        elif len(val) > max_len:
            self.err(path, f"is {len(val)} characters (max {max_len}) — summarize, do not paste")

    def enum(self, path, val, choices) -> None:
        if val not in choices:
            self.err(path, f"must be one of {', '.join(choices)}")

    def count(self, path, val) -> None:
        if val is None:
            return
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            self.err(path, "must be a non-negative integer or null")

    def items(self, path, val, fn, max_items=MAX_ITEMS) -> None:
        if not isinstance(val, list):
            self.err(path, "must be a list")
            return
        if len(val) > max_items:
            self.err(path, f"has {len(val)} items (max {max_items})")
        for i, item in enumerate(val):
            fn(f"{path}[{i}]", item)

    def short_list(self, path, val) -> None:
        self.items(path, val, lambda p, v: self.text(p, v, MAX_SHORT))

    def report_ids(self, path, val) -> None:
        def one(p, v):
            if not isinstance(v, str) or not ID_RE.fullmatch(v):
                self.err(p, "must be a report ID (ins-YYYYMMDDTHHMMSSZ-<12 hex>)")
        self.items(path, val, one)

    def fact(self, path, val, value_fn=None) -> None:
        """{"value": X, "source": "..."} or {"value": null, "reason": "..."}."""
        if not self.obj(path, val, ("value",), ("source", "reason")):
            return
        # The two variants are exclusive: an unknown value has no source, a
        # known one needs no excuse. Mixing them makes provenance ambiguous.
        if val.get("value") is None:
            self.text(f"{path}.reason", val.get("reason"), MAX_SHORT)
            if "source" in val:
                self.err(f"{path}.source", "must be absent when value is null (use reason)")
        else:
            self.text(f"{path}.source", val.get("source"), MAX_SHORT)
            if "reason" in val:
                self.err(f"{path}.reason", "must be absent when value is known (use source)")
            (value_fn or (lambda p, v: self.text(p, v, MAX_SHORT)))(f"{path}.value", val["value"])

    # -- sections
    def report(self, r) -> None:
        required = (
            "schema", "report_id", "recorded_at", "report_trigger", "task_status",
            "project", "work", "reporter", "participants", "usage", "user_feedback",
            "retrospective",
        )
        if not self.obj("report", r, required, ("plugin_details",)):
            return
        if r.get("schema") != SCHEMA_ID:
            self.err("schema", f"must be {SCHEMA_ID!r}")
        if "report_id" in r and not (isinstance(r["report_id"], str) and ID_RE.fullmatch(r["report_id"])):
            self.err("report_id", "must match ins-YYYYMMDDTHHMMSSZ-<12 hex> (use `insights.py new-id`)")
        if "recorded_at" in r:
            self.timestamp("recorded_at", r["recorded_at"])
        if "report_trigger" in r:
            self.enum("report_trigger", r["report_trigger"], TRIGGERS)
        if "task_status" in r:
            self.enum("task_status", r["task_status"], TASK_STATUSES)
        for key, fn in (
            ("project", self.project), ("work", self.work), ("reporter", self.reporter),
            ("usage", self.usage), ("retrospective", self.retrospective),
            ("plugin_details", self.plugin_details),
        ):
            if key in r:
                fn(key, r[key])
        if "participants" in r:
            self.items("participants", r["participants"], self.participant)
        if "user_feedback" in r:
            self.items("user_feedback", r["user_feedback"], self.feedback)
        self.walk_strings("report", r)
        try:
            size = len(canonical(r).encode("utf-8"))
        except (TypeError, ValueError):
            size = 0
        if size > MAX_REPORT_BYTES:
            self.err("report", f"is {size} bytes (max {MAX_REPORT_BYTES})")

    def timestamp(self, path, val) -> None:
        if not isinstance(val, str) or not TS_RE.fullmatch(val):
            self.err(path, "must be a UTC timestamp like 2026-09-16T15:30:00Z")
            return
        try:
            _dt.datetime.strptime(val[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            self.err(path, "is not a valid date/time")

    def project(self, path, p) -> None:
        if not self.obj(path, p, ("name", "ref", "ref_source", "key", "remote")):
            return
        self.text(f"{path}.name", p.get("name"), MAX_SHORT)
        self.text(f"{path}.ref", p.get("ref"), 1024)
        self.enum(f"{path}.ref_source", p.get("ref_source"), REF_SOURCES)
        ref = p.get("ref")
        if isinstance(ref, str):
            prefix = "git:" if p.get("ref_source") == "git-main-worktree" else "dir:"
            if not ref.startswith(prefix) or not os.path.isabs(ref[len(prefix):]):
                self.err(f"{path}.ref", f"must be '{prefix}<absolute path>' for ref_source {p.get('ref_source')!r}")
            elif p.get("key") != hashlib.sha256(ref.encode("utf-8")).hexdigest()[:16]:
                self.err(f"{path}.key", "does not match ref (derive project via `insights.py context`)")
        self.text(f"{path}.remote", p.get("remote"), 1024, nullable=True)

    def work(self, path, w) -> None:
        facts = ("task_id", "run_id", "task_name", "task_path", "branch", "pr")
        if not self.obj(path, w, ("summary",) + facts + ("instruction_ids", "related_reports")):
            return
        self.text(f"{path}.summary", w.get("summary"))
        for k in facts:
            if k in w:
                self.fact(f"{path}.{k}", w[k])
        if "instruction_ids" in w:
            self.short_list(f"{path}.instruction_ids", w["instruction_ids"])
        if "related_reports" in w:
            self.report_ids(f"{path}.related_reports", w["related_reports"])

    def identity(self, path, d, extra_required=(), extra_optional=()) -> bool:
        keys = ("role", "model", "runtime", "harness", "reasoning_effort")
        if not self.obj(path, d, keys + tuple(extra_required), extra_optional):
            return False
        if "role" in d:
            self.enum(f"{path}.role", d["role"], ROLES)
        for k in keys[1:]:
            if k in d:
                self.fact(f"{path}.{k}", d[k])
        return True

    def reporter(self, path, r) -> None:
        if not self.identity(path, r, ("role_source", "session_id")):
            return
        if r.get("role") == "unknown":
            self.text(f"{path}.role_source", r.get("role_source"), MAX_SHORT, nullable=True)
        else:
            self.text(f"{path}.role_source", r.get("role_source"), MAX_SHORT)
        if "session_id" in r:
            self.fact(f"{path}.session_id", r["session_id"])

    def participant(self, path, p) -> None:
        if not self.identity(path, p, ("label", "scope", "basis")):
            return
        self.text(f"{path}.label", p.get("label"), MAX_SHORT, nullable=True)
        self.text(f"{path}.scope", p.get("scope"), MAX_SHORT, nullable=True)
        self.enum(f"{path}.basis", p.get("basis"), BASES)

    def usage(self, path, u) -> None:
        if not self.obj(path, u, ("completeness", "completeness_reason", "skills")):
            return
        self.enum(f"{path}.completeness", u.get("completeness"), COMPLETENESS)
        self.text(f"{path}.completeness_reason", u.get("completeness_reason"), MAX_SHORT)

        def skill(p, s):
            if not self.obj(p, s, ("skill", "plugin", "plugin_version", "model", "note")):
                return
            self.text(f"{p}.skill", s.get("skill"), MAX_SHORT)
            self.text(f"{p}.plugin", s.get("plugin"), MAX_SHORT, nullable=True)
            for k in ("plugin_version", "model"):
                if k in s:
                    self.fact(f"{p}.{k}", s[k])
            self.text(f"{p}.note", s.get("note"), MAX_SHORT, nullable=True)

        if "skills" in u:
            self.items(f"{path}.skills", u["skills"], skill)

    def feedback(self, path, f) -> None:
        if not self.obj(path, f, ("text", "attribution", "captured_via")):
            return
        self.text(f"{path}.text", f.get("text"), MAX_FEEDBACK)
        self.text(f"{path}.attribution", f.get("attribution"), MAX_SHORT)
        self.text(f"{path}.captured_via", f.get("captured_via"), MAX_SHORT)

    def retrospective(self, path, r) -> None:
        keys = ("outcome", "difficulty", "worked_well", "friction", "interventions", "suggestions")
        if not self.obj(path, r, keys):
            return
        if "outcome" in r and self.obj(f"{path}.outcome", r["outcome"], ("intended", "achieved")):
            self.text(f"{path}.outcome.intended", r["outcome"].get("intended"))
            self.text(f"{path}.outcome.achieved", r["outcome"].get("achieved"))
        if "difficulty" in r and self.obj(f"{path}.difficulty", r["difficulty"], ("domain", "tooling")):
            for k in ("domain", "tooling"):
                d = r["difficulty"].get(k)
                if k in r["difficulty"] and self.obj(f"{path}.difficulty.{k}", d, ("level", "reason")):
                    self.enum(f"{path}.difficulty.{k}.level", d.get("level"), LEVELS)
                    self.text(f"{path}.difficulty.{k}.reason", d.get("reason"))
        if "worked_well" in r:
            self.items(f"{path}.worked_well", r["worked_well"], self.worked_well)
        if "friction" in r:
            self.items(f"{path}.friction", r["friction"], self.friction)
        if "interventions" in r:
            self.items(f"{path}.interventions", r["interventions"], self.intervention)
        if "suggestions" in r:
            self.suggestions(f"{path}.suggestions", r["suggestions"])

    def worked_well(self, path, w) -> None:
        if not self.obj(path, w, ("observation", "basis", "evidence")):
            return
        self.text(f"{path}.observation", w.get("observation"))
        self.enum(f"{path}.basis", w.get("basis"), BASES)
        if "evidence" in w:
            self.short_list(f"{path}.evidence", w["evidence"])

    def friction(self, path, f) -> None:
        keys = ("plugin", "skill", "expected", "observed", "impact", "resolution",
                "suspected_cause", "basis", "evidence", "related_reports")
        if not self.obj(path, f, keys):
            return
        self.text(f"{path}.plugin", f.get("plugin"), MAX_SHORT, nullable=True)
        self.text(f"{path}.skill", f.get("skill"), MAX_SHORT, nullable=True)
        for k in ("expected", "observed", "impact"):
            self.text(f"{path}.{k}", f.get(k))
        res = f.get("resolution")
        if "resolution" in f and self.obj(f"{path}.resolution", res, ("status", "detail")):
            self.enum(f"{path}.resolution.status", res.get("status"), RESOLUTIONS)
            self.text(f"{path}.resolution.detail", res.get("detail"), nullable=True)
        cause = f.get("suspected_cause")
        if cause is not None and self.obj(f"{path}.suspected_cause", cause, ("text", "confidence")):
            self.text(f"{path}.suspected_cause.text", cause.get("text"))
            self.enum(f"{path}.suspected_cause.confidence", cause.get("confidence"), CONFIDENCE)
        self.enum(f"{path}.basis", f.get("basis"), BASES)
        if "evidence" in f:
            self.short_list(f"{path}.evidence", f["evidence"])
        if "related_reports" in f:
            self.report_ids(f"{path}.related_reports", f["related_reports"])

    def intervention(self, path, i) -> None:
        if not self.obj(path, i, ("kind", "description", "reason", "basis")):
            return
        self.enum(f"{path}.kind", i.get("kind"), INTERVENTIONS)
        self.text(f"{path}.description", i.get("description"))
        self.text(f"{path}.reason", i.get("reason"), nullable=True)
        self.enum(f"{path}.basis", i.get("basis"), BASES)

    def suggestions(self, path, s) -> None:
        if not self.obj(path, s, ("status", "author", "items")):
            return
        self.enum(f"{path}.status", s.get("status"), ("provided", "none"))
        if s.get("author") != "reporting_model":
            self.err(f"{path}.author", "must be 'reporting_model' — suggestions are the reporting model's assessment")
        items = s.get("items")
        if s.get("status") == "none" and items:
            self.err(f"{path}.items", "must be empty when status is 'none'")
        if s.get("status") == "provided" and isinstance(items, list) and not items:
            self.err(f"{path}.items", "must not be empty when status is 'provided' (use status 'none')")

        def item(p, it):
            if not self.obj(p, it, ("change", "observation", "expected_benefit", "uncertainty")):
                return
            for k in ("change", "observation", "expected_benefit"):
                self.text(f"{p}.{k}", it.get(k))
            unc = it.get("uncertainty")
            if "uncertainty" in it and self.obj(f"{p}.uncertainty", unc, ("level", "note")):
                self.enum(f"{p}.uncertainty.level", unc.get("level"), CONFIDENCE)
                self.text(f"{p}.uncertainty.note", unc.get("note"), nullable=True)

        if "items" in s:
            self.items(f"{path}.items", items, item)

    # -- plugin-specific retrospective details (only for plugins actually used)
    def plugin_details(self, path, d) -> None:
        handlers = {
            "swarm": self.swarm,
            "work-system": self.work_system,
            "pr-flow": self.pr_flow,
            "knowledge-system": self.knowledge_system,
        }
        if not self.obj(path, d, (), tuple(handlers)):
            return
        for k, fn in handlers.items():
            if k in d:
                fn(f"{path}.{k}", d[k])

    def swarm(self, path, s) -> None:
        if not self.obj(path, s, ("runs",)):
            return

        def run(p, r):
            keys = ("profile", "voices", "failures", "restarts", "findings", "handoff",
                    "benefit_vs_effort", "basis")
            if not self.obj(p, r, keys):
                return
            if "profile" in r:
                self.fact(f"{p}.profile", r["profile"])
            v = r.get("voices")
            vkeys = ("planned", "started", "accepted", "missing_results", "empty_results")
            if "voices" in r and self.obj(f"{p}.voices", v, vkeys):
                for k in vkeys:
                    self.count(f"{p}.voices.{k}", v.get(k))
                ints = {k: v.get(k) for k in vkeys if type(v.get(k)) is int}
                if "started" in ints and "planned" in ints and ints["started"] > ints["planned"]:
                    self.err(f"{p}.voices.started", "exceeds planned")
                if "accepted" in ints and "started" in ints and ints["accepted"] > ints["started"]:
                    self.err(f"{p}.voices.accepted", "exceeds started")

            def failure(fp, f):
                if self.obj(fp, f, ("voice", "reason")):
                    self.text(f"{fp}.voice", f.get("voice"), MAX_SHORT)
                    self.text(f"{fp}.reason", f.get("reason"), MAX_SHORT, nullable=True)

            if "failures" in r:
                self.items(f"{p}.failures", r["failures"], failure)
            self.count(f"{p}.restarts", r.get("restarts"))
            fnd = r.get("findings")
            if "findings" in r and self.obj(f"{p}.findings", fnd, ("useful", "rejected", "rejection_reasons")):
                self.count(f"{p}.findings.useful", fnd.get("useful"))
                self.count(f"{p}.findings.rejected", fnd.get("rejected"))
                if "rejection_reasons" in fnd:
                    self.short_list(f"{p}.findings.rejection_reasons", fnd["rejection_reasons"])
            h = r.get("handoff")
            if "handoff" in r and self.obj(f"{p}.handoff", h, ("fix", "pr")):
                self.text(f"{p}.handoff.fix", h.get("fix"), MAX_SHORT, nullable=True)
                self.text(f"{p}.handoff.pr", h.get("pr"), MAX_SHORT, nullable=True)
            self.text(f"{p}.benefit_vs_effort", r.get("benefit_vs_effort"), nullable=True)
            self.enum(f"{p}.basis", r.get("basis"), BASES)

        if "runs" in s:
            self.items(f"{path}.runs", s["runs"], run)

    def work_system(self, path, w) -> None:
        if not self.obj(path, w, ("questions", "handoff_gaps", "ambiguous_states")):
            return

        def question(p, q):
            keys = ("question", "reason", "classification", "mandate_source", "mandate_scope",
                    "answer_already_available", "basis")
            if not self.obj(p, q, keys):
                return
            self.text(f"{p}.question", q.get("question"))
            self.text(f"{p}.reason", q.get("reason"), nullable=True)
            self.enum(f"{p}.classification", q.get("classification"), QUESTION_CLASSES)
            self.text(f"{p}.mandate_source", q.get("mandate_source"), MAX_SHORT, nullable=True)
            self.text(f"{p}.mandate_scope", q.get("mandate_scope"), nullable=True)
            self.enum(f"{p}.answer_already_available", q.get("answer_already_available"), YES_NO_UNKNOWN)
            if q.get("classification") == "avoidable_repeat" and q.get("answer_already_available") != "yes":
                self.err(f"{p}.classification", "'avoidable_repeat' requires answer_already_available 'yes'")
            self.enum(f"{p}.basis", q.get("basis"), BASES)

        def ambiguous(p, a):
            if not self.obj(p, a, ("kind", "observed", "resolution", "status", "basis")):
                return
            self.enum(f"{p}.kind", a.get("kind"), ("start", "delivery"))
            self.text(f"{p}.observed", a.get("observed"))
            self.text(f"{p}.resolution", a.get("resolution"), nullable=True)
            self.enum(f"{p}.status", a.get("status"), ("resolved", "unresolved"))
            if a.get("status") == "resolved" and not a.get("resolution"):
                self.err(f"{p}.resolution", "is required when status is 'resolved'")
            self.enum(f"{p}.basis", a.get("basis"), BASES)

        if "questions" in w:
            self.items(f"{path}.questions", w["questions"], question)
        if "handoff_gaps" in w:
            self.items(f"{path}.handoff_gaps", w["handoff_gaps"], lambda p, v: self.text(p, v))
        if "ambiguous_states" in w:
            self.items(f"{path}.ambiguous_states", w["ambiguous_states"], ambiguous)

    def pr_flow(self, path, p) -> None:
        if not self.obj(path, p, ("review_rounds", "rework_transitions", "notes")):
            return
        self.count(f"{path}.review_rounds", p.get("review_rounds"))
        self.text(f"{path}.rework_transitions", p.get("rework_transitions"), nullable=True)
        self.text(f"{path}.notes", p.get("notes"), nullable=True)

    def knowledge_system(self, path, k) -> None:
        if not self.obj(path, k, ("useful_knowledge_found", "stale_or_missing", "notes")):
            return
        self.enum(f"{path}.useful_knowledge_found", k.get("useful_knowledge_found"), KNOWLEDGE_FOUND)
        self.text(f"{path}.stale_or_missing", k.get("stale_or_missing"), nullable=True)
        self.text(f"{path}.notes", k.get("notes"), nullable=True)

    # -- whole-document string rules
    def walk_strings(self, path, val) -> None:
        if isinstance(val, dict):
            for k, v in val.items():
                self.walk_strings(f"{path}.{k}", v)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                self.walk_strings(f"{path}[{i}]", v)
        elif isinstance(val, str):
            if CTRL_RE.search(val):
                self.err(path, "contains control or bidi characters")
            # Writes redact before validating, so this only fires for a stored
            # file that bypassed the helper.
            segments = set(re.split(r"[.\[\]]", path))
            if not REDACTION_EXEMPT & segments and any(rx.search(val) for rx, _ in SECRET_SUBS):
                self.err(path, "contains an unredacted credential")


def validate_report(report) -> list:
    v = Validator()
    v.report(report)
    return v.errors


def fact_gaps(report) -> list:
    """Every unknown metadata value, as `path: reason` — what the confirmation lists."""
    gaps = []

    def walk(path, val):
        if isinstance(val, dict):
            if set(val) <= {"value", "source", "reason"} and "value" in val:
                if val["value"] is None:
                    gaps.append(f"{path}: {val.get('reason')}")
                return
            for k, v in val.items():
                walk(f"{path}.{k}" if path else k, v)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                walk(f"{path}[{i}]", v)

    walk("", report)
    usage = report.get("usage") if isinstance(report, dict) else None
    if isinstance(usage, dict) and usage.get("completeness") != "complete":
        gaps.append(f"usage.completeness={usage.get('completeness')}: {usage.get('completeness_reason')}")
    return gaps


# ----------------------------------------------------------------------- preparing


# Helper-derived identifiers are not free text: redacting a repo path such as
# `/code/sk-learn-experiments` would break project.key and report grouping.
REDACTION_EXEMPT = {"schema", "report_id", "recorded_at", "project", "branch", "task_name"}
MAX_REDACTION_PASSES = 5


def scrub_text(text):
    """Redact credential shapes in one string. Returns (text, replacements).

    Repeats until nothing changes: one substitution can remove a character that
    was blocking another pattern, and the validator re-checks with the same set.

    The single implementation. `redact` used to carry its own copy of this loop,
    which meant the two could drift in algorithm while both claimed to apply
    "the same substitutions" — only the patterns were actually shared.
    """
    count = 0
    for _ in range(MAX_REDACTION_PASSES):
        changed = 0
        for rx, repl in SECRET_SUBS:
            text, n = rx.subn(repl, text)
            changed += n
        count += changed
        if not changed:
            break
    return text, count


def redact_secrets(report) -> int:
    """Replace credential shapes in free-text strings, in place; return the count."""
    count = 0

    def scrub(text):
        nonlocal count
        text, n = scrub_text(text)
        count += n
        return text

    def walk(val, key=None):
        if key in REDACTION_EXEMPT:
            return val
        if isinstance(val, str):
            return scrub(val)
        if isinstance(val, dict):
            return {k: walk(v, k) for k, v in val.items()}
        if isinstance(val, list):
            return [walk(v) for v in val]
        return val

    for key in list(report):
        report[key] = walk(report[key], key)
    return count


def sanitize_report(report: dict) -> None:
    """Strip userinfo/query/fragment from the URL-bearing reference fields in place."""
    proj = report.get("project")
    if isinstance(proj, dict) and isinstance(proj.get("remote"), str):
        proj["remote"] = sanitize_url(proj["remote"])
    work = report.get("work")
    if isinstance(work, dict) and isinstance(work.get("pr"), dict):
        if isinstance(work["pr"].get("value"), str):
            work["pr"]["value"] = sanitize_url(work["pr"]["value"])

    def walk(val):
        if isinstance(val, dict):
            for k, v in val.items():
                if k == "evidence" and isinstance(v, list):
                    val[k] = [sanitize_url(e) for e in v]
                else:
                    walk(v)
        elif isinstance(val, list):
            for v in val:
                walk(v)

    walk(report)


def load_input(source: str) -> dict:
    try:
        if source == "-":
            raw = sys.stdin.buffer.read(MAX_REPORT_BYTES * 2 + 1)
        else:
            with open(source, "rb") as fh:
                raw = fh.read(MAX_REPORT_BYTES * 2 + 1)
    except OSError as e:
        raise UsageError(f"cannot read report input {source}: {e}")
    if len(raw) > MAX_REPORT_BYTES * 2:
        raise UsageError(f"report input exceeds {MAX_REPORT_BYTES * 2} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise InvalidReport([f"input: not valid UTF-8 JSON ({e})"])
    if not isinstance(data, dict):
        raise InvalidReport(["input: must be a JSON object"])
    return data


class InvalidReport(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def prepare(report: dict, project_dir=None):
    """Fill defaults, sanitize, redact, validate. Returns (report, redactions)."""
    report.setdefault("report_id", new_report_id())
    report.setdefault("recorded_at", utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    if "project" not in report:
        report["project"] = project_identity(project_dir)
    redactions = redact_secrets(report)  # before URL sanitizing, so stripped credentials count
    sanitize_report(report)
    errors = validate_report(report)
    if errors:
        raise InvalidReport(errors)
    return report, redactions


# ------------------------------------------------------------------------- context


def read_frontmatter_value(path: Path, key: str):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(rf"^{re.escape(key)}:\s*(.+)$", line)
        if m:
            return m.group(1).strip()
    return None


def plugin_version():
    manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version"), str(manifest)
    except (OSError, ValueError):
        return None, str(manifest)


def gather_context(project_dir=None, cli_store=None) -> dict:
    project = project_identity(project_dir)
    here = Path(project_dir).resolve() if project_dir else Path.cwd()
    top = run_git(here, "rev-parse", "--show-toplevel")
    git = {
        "inside": top is not None,
        "worktree_root": top,
        "linked_worktree": None,
        "branch": run_git(here, "symbolic-ref", "--short", "-q", "HEAD") if top else None,
        "head": run_git(here, "rev-parse", "--short", "HEAD") if top else None,
    }
    if top:
        common = run_git(here, "rev-parse", "--path-format=absolute", "--git-common-dir")
        gitdir = run_git(here, "rev-parse", "--path-format=absolute", "--git-dir")
        if common and gitdir:
            git["linked_worktree"] = os.path.realpath(common) != os.path.realpath(gitdir)

    root = Path(top) if top else here
    task_md = root / "TASK.md"
    mandate_md = root / "MANDATE.md"
    title = None
    if task_md.is_file():
        try:
            for line in task_md.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except (OSError, UnicodeDecodeError):
            pass
    hints = {
        "task_md": str(task_md) if task_md.is_file() else None,
        "task_title": title,
        "mandate_md": str(mandate_md) if mandate_md.is_file() else None,
        "mandate_task": read_frontmatter_value(mandate_md, "task") if mandate_md.is_file() else None,
    }
    if hints["mandate_task"] and project["ref_source"] == "git-main-worktree":
        candidate = Path(project["ref"][4:]) / "tasks" / f"{hints['mandate_task']}.md"
        hints["main_task_file"] = str(candidate) if candidate.is_file() else None

    # Whitelisted environment evidence only — never dump the environment (it
    # carries tokens). Each value states the variable it came from.
    env = os.environ
    runtime = {}
    if env.get("CLAUDECODE") == "1":
        execpath = env.get("CLAUDE_CODE_EXECPATH", "")
        m = re.search(r"(\d+\.\d+\.\d+)$", execpath)
        runtime["claude_code"] = {
            "version": m.group(1) if m else None,
            "version_source": "env:CLAUDE_CODE_EXECPATH" if m else None,
            "entrypoint": env.get("CLAUDE_CODE_ENTRYPOINT"),
        }
    for var, key in (("CLAUDE_EFFORT", "reasoning_effort"), ("CLAUDE_CODE_SESSION_ID", "session_id")):
        if env.get(var):
            runtime[key] = {"value": env[var], "source": f"env:{var}"}
    if env.get("HERDR_ENV") == "1":
        runtime["herdr"] = {"workspace": env.get("HERDR_WORKSPACE_ID"), "pane": env.get("HERDR_PANE_ID")}

    version, manifest = plugin_version()
    try:
        store, store_source, _ = resolve_store(cli_store)
        store_info = {"dir": str(store), "source": store_source}
    except (UsageError, StorageError) as e:
        store_info = {"dir": None, "error": str(e)}
    return {
        "project": project,
        "git": git,
        "task_hints": hints,
        "runtime": runtime,
        "insights_plugin": {"version": version, "source": manifest},
        "store": store_info,
    }


# ----------------------------------------------------------------------- read/list


# Stored files are pretty-printed, so allow indentation overhead above the
# canonical-size cap; anything bigger is refused before it is read into memory.
MAX_STORED_FILE_BYTES = MAX_REPORT_BYTES * 4


def load_stored(path: Path, expected_id: str):
    """Load and re-validate one stored report. Returns (report|None, errors)."""
    try:
        data = json.loads(read_bounded(path, MAX_STORED_FILE_BYTES))
    except OSError as e:
        return None, [f"unreadable ({e})"]
    except ValueError as e:  # includes UnicodeDecodeError and JSONDecodeError
        return None, [f"unreadable ({e})"]
    errors = validate_report(data)
    if not errors and data.get("report_id") != expected_id:
        errors = [f"report_id {data.get('report_id')!r} does not match file name {path.name}"]
    return (None, errors) if errors else (data, [])


def scan_store(reports: Path):
    """Yield (path, report|None, error|None) for every report file."""
    if not reports.is_dir():
        return
    for path in sorted(reports.glob("*.json")):
        if path.name.startswith("."):
            continue
        data, errors = load_stored(path, path.name[: -len(".json")])
        if errors:
            more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            yield path, None, errors[0] + more
        else:
            yield path, data, None


def safe_line(text) -> str:
    return CTRL_RE.sub("?", str(text)).replace("\n", " ").replace("\t", " ")


def matches(report: dict, args, here_ref) -> bool:
    proj = report["project"]
    if here_ref and proj["ref"] != here_ref:
        return False
    if args.project and args.project not in (proj["ref"], proj["key"], proj["name"]):
        return False
    if args.task:
        work = report["work"]
        if args.task not in (work["task_name"].get("value"), work["task_id"].get("value")):
            return False
    if args.trigger and report["report_trigger"] != args.trigger:
        return False
    if args.status and report["task_status"] != args.status:
        return False
    return True


# ---------------------------------------------------------------------------- CLI


def emit_kv(pairs) -> None:
    for k, v in pairs:
        print(f"{k}={v}")


def cmd_context(args) -> int:
    print(json.dumps(gather_context(args.project_dir, args.store), indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_new_id(args) -> int:
    print(new_report_id())
    return EXIT_OK


def cmd_store(args) -> int:
    reports, source, _ = resolve_store(args.store)
    emit_kv([("dir", reports), ("source", source), ("exists", "yes" if reports.is_dir() else "no")])
    return EXIT_OK


def unknown(reason=""):
    return {"value": None, "reason": reason}


def build_skeleton(ctx: dict, trigger: str) -> dict:
    """A complete draft with every required field present.

    Values the helper can observe are prefilled with their source. Everything
    the producer must decide is an empty string (or an unknown with an empty
    reason), which fails validation by name until it is filled in — so an
    untouched skeleton can never be stored as if it were a report.
    """
    hints, runtime, git = ctx["task_hints"], ctx["runtime"], ctx["git"]

    def observed(value, source):
        return {"value": value, "source": source} if value else unknown()

    claude = runtime.get("claude_code") or {}
    branch = git.get("branch") or ""
    if hints.get("mandate_task"):
        task_name = observed(hints["mandate_task"], "MANDATE.md task")
    elif branch.startswith("task/") and len(branch) > len("task/"):
        task_name = observed(branch[len("task/"):], "work-system task branch name")
    else:
        task_name = unknown()
    work = {
        "summary": "",
        "task_id": unknown(),
        "run_id": unknown(),
        "task_name": task_name,
        "task_path": observed(hints.get("main_task_file") or hints.get("task_md"), "insights.py context"),
        "branch": observed(git.get("branch"), "git"),
        "pr": unknown(),
        "instruction_ids": [],
        "related_reports": [],
    }
    reporter = {
        "role": "",
        "role_source": "",
        "model": unknown(),
        "runtime": observed(claude.get("version") and f"claude-code {claude['version']}",
                            claude.get("version_source")),
        "harness": observed("herdr" if "herdr" in runtime else None, "env:HERDR_ENV"),
        "reasoning_effort": runtime.get("reasoning_effort") or unknown(),
        "session_id": runtime.get("session_id") or unknown(),
    }
    level = {"level": "", "reason": ""}
    return {
        "schema": SCHEMA_ID,
        "report_trigger": trigger,
        "task_status": "",
        "project": ctx["project"],  # resolved now, so `write` can't re-derive it from another cwd
        "work": work,
        "reporter": reporter,
        "participants": [],
        "usage": {"completeness": "", "completeness_reason": "", "skills": []},
        "user_feedback": [],
        "retrospective": {
            "outcome": {"intended": "", "achieved": ""},
            "difficulty": {"domain": dict(level), "tooling": dict(level)},
            "worked_well": [],
            "friction": [],
            "interventions": [],
            "suggestions": {"status": "", "author": "reporting_model", "items": []},
        },
    }


def cmd_skeleton(args) -> int:
    ctx = gather_context(args.project_dir, args.store)
    print(json.dumps(build_skeleton(ctx, args.trigger), indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_validate(args) -> int:
    report, redactions = prepare(load_input(args.input), args.project_dir)
    gaps = fact_gaps(report)
    emit_kv([("status", "valid"), ("report_id", report["report_id"]),
             ("redactions", redactions), ("gaps", len(gaps))])
    for g in gaps:
        print(f"gap={safe_line(g)}")
    return EXIT_OK


def cmd_write(args) -> int:
    report, redactions = prepare(load_input(args.input), args.project_dir)
    reports, source, private_from = resolve_store(args.store)
    ensure_private_dir(reports, private_from)
    status = publish(reports, report)
    path = reports / f"{report['report_id']}.json"
    gaps = fact_gaps(report)
    if args.json:
        print(json.dumps({"status": status, "report_id": report["report_id"], "path": str(path),
                          "store_source": source, "redactions": redactions, "gaps": gaps},
                         indent=2, ensure_ascii=False))
    else:
        emit_kv([("status", status), ("report_id", report["report_id"]), ("path", path),
                 ("store_source", source), ("redactions", redactions), ("gaps", len(gaps))])
        for g in gaps:
            print(f"gap={safe_line(g)}")
    return EXIT_OK


def cmd_redact(args) -> int:
    """Redact credential shapes in a plain text file.

    A report is redacted by `write`. This exposes the SAME substitutions for the
    one case that has no report to write: work-system's `/close` preserves a
    compact summary in the archived task file when a report could not be stored,
    and that archive may be committed and pushed. Without this, the only options
    were copying SECRET_SUBS into another plugin (a second set to drift) or
    trusting prose not to paste a secret. Text-only — no schema, no storage — so
    it never becomes a second way to make a report.
    """
    try:
        if args.input == "-":
            raw = sys.stdin.buffer.read(MAX_REPORT_BYTES + 1)
        else:
            with open(args.input, "rb") as fh:
                raw = fh.read(MAX_REPORT_BYTES + 1)
    except OSError as e:
        raise UsageError(f"cannot read {args.input}: {e}")
    if len(raw) > MAX_REPORT_BYTES:
        raise UsageError(f"input exceeds {MAX_REPORT_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"{args.input}: not valid UTF-8 ({e})", file=sys.stderr)
        return EXIT_INVALID
    # STRIP control characters and bidi overrides rather than refusing the file.
    # A report is rejected because a bad report should not be stored; this text
    # is a note whose only alternative is being used UNREDACTED, so a single CR
    # or bidi override would have disabled redaction exactly when the input is
    # least trustworthy. Stripping keeps the output usable and safe.
    text, stripped = CTRL_RE.subn("", text)
    text, count = scrub_text(text)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    print(f"redactions={count} stripped={stripped}", file=sys.stderr)
    return EXIT_OK


def cmd_read(args) -> int:
    if not ID_RE.fullmatch(args.report_id):
        raise UsageError("report ID must match ins-YYYYMMDDTHHMMSSZ-<12 hex>")
    reports, _, _ = resolve_store(args.store)
    path = reports / f"{args.report_id}.json"
    if not path.is_file():
        print(f"error: no report {args.report_id} in {reports}", file=sys.stderr)
        return EXIT_NOT_FOUND
    data, errors = load_stored(path, args.report_id)
    if errors:
        print(f"error: {path} is malformed:", file=sys.stderr)
        for e in errors:
            print(f"  {safe_line(e)}", file=sys.stderr)
        return EXIT_INVALID
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_list(args) -> int:
    reports, source, _ = resolve_store(args.store)
    here_ref = project_identity(args.project_dir)["ref"] if args.here else None
    rows, malformed = [], []
    for path, report, error in scan_store(reports):
        if error:
            malformed.append({"path": str(path), "error": error})
        elif matches(report, args, here_ref):
            rows.append(report)
    rows.sort(key=lambda r: (r["recorded_at"], r["report_id"]))
    if args.limit is not None:
        rows = rows[-args.limit:]
    if args.json:
        out = [{
            "report_id": r["report_id"], "recorded_at": r["recorded_at"],
            "project": r["project"]["name"], "project_ref": r["project"]["ref"],
            "report_trigger": r["report_trigger"], "task_status": r["task_status"],
            "task": r["work"]["task_name"].get("value"), "summary": r["work"]["summary"],
        } for r in rows]
        print(json.dumps({"store": str(reports), "reports": out, "malformed": malformed},
                         indent=2, ensure_ascii=False))
    else:
        print(f"store={reports} ({source})")
        for r in rows:
            task = r["work"]["task_name"].get("value") or "-"
            summary = r["work"]["summary"]
            summary = summary if len(summary) <= 70 else summary[:67] + "..."
            print(safe_line(f"{r['recorded_at']}  {r['report_id']}  {r['project']['name']}  "
                            f"{r['report_trigger']}/{r['task_status']}  {task}  {summary}"))
        for m in malformed:
            print(safe_line(f"MALFORMED {m['path']}: {m['error']}"))
        print(f"reports={len(rows)} malformed={len(malformed)}")
    return EXIT_OK


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="insights.py", description="Insights report store.")
    sub = ap.add_subparsers(dest="cmd")

    def with_store(p):
        p.add_argument("--store", help="absolute store directory (overrides the default and INSIGHTS_STORE_DIR)")
        return p

    p = with_store(sub.add_parser("context"))
    p.add_argument("--project-dir")
    p.set_defaults(fn=cmd_context)
    p = with_store(sub.add_parser("skeleton"))
    p.add_argument("--project-dir")
    p.add_argument("--trigger", choices=TRIGGERS, default="manual")
    p.set_defaults(fn=cmd_skeleton)
    sub.add_parser("new-id").set_defaults(fn=cmd_new_id)
    with_store(sub.add_parser("store")).set_defaults(fn=cmd_store)
    for name, fn in (("write", cmd_write), ("validate", cmd_validate)):
        p = with_store(sub.add_parser(name))
        p.add_argument("input", help="report JSON file, or - for stdin")
        p.add_argument("--project-dir", help="derive project identity from DIR instead of the cwd")
        if name == "write":
            p.add_argument("--json", action="store_true")
        p.set_defaults(fn=fn)
    p = sub.add_parser("redact")
    p.add_argument("input", help="text file, or - for stdin")
    p.set_defaults(fn=cmd_redact)

    p = with_store(sub.add_parser("read"))
    p.add_argument("report_id")
    p.set_defaults(fn=cmd_read)
    p = with_store(sub.add_parser("list"))
    p.add_argument("--here", action="store_true", help="only reports for the current project")
    p.add_argument("--project-dir", help="with --here: resolve the project from DIR")
    p.add_argument("--project", help="project ref, key or name")
    p.add_argument("--task", help="task name or task ID")
    p.add_argument("--trigger", choices=TRIGGERS)
    p.add_argument("--status", choices=TASK_STATUSES)
    p.add_argument("--limit", type=positive_int, default=None, help="only the N most recent")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        return args.fn(args)
    except InvalidReport as e:
        print("error: report is invalid — NOT saved:", file=sys.stderr)
        for msg in e.errors:
            print(f"  {safe_line(msg)}", file=sys.stderr)
        return EXIT_INVALID
    except CollisionError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_COLLISION
    except StorageError as e:
        print(f"error: report NOT saved — {e}", file=sys.stderr)
        return EXIT_STORAGE
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
