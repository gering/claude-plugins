#!/usr/bin/env python3
"""task-store.py — the one path from a work-system skill to the task backlog.

Usage:
  task-store.py backend <dir>
  task-store.py list    <dir>
  task-store.py get     <dir> <name|#N> [--write <path> | --body]   (an issue ref needs the #)
  task-store.py create  <dir> <name> --body-file <file> [--title <text>]
  task-store.py close   <dir> <name|#N> --branch <task-branch> [--pr <n>] [--sha <sha>]
                        [--name <task>]   (with #N: refuse an issue of another task)
  task-store.py ref     <dir> <name> [<TASK.md>]
  task-store.py migrate <dir> [--apply <token>] [--include <name>]... [--open-only]
                        [--pace <seconds>]

<dir> is any path inside the repo; the backlog belongs to the MAIN checkout, so a
lane resolves the same backlog as the main session.

Backends (setting `tasks.backend` in .work-system.toml, read through
ws-settings.py at the main checkout):
  local   Markdown files in tasks/ on the main checkout. `close` hands over to
          archive-task.sh unchanged and passes its output through.
          (paths.tasks_dir is not read: archive-task.sh and the statusline
          hardcode tasks/, and one backlog must have one directory. Wiring
          the setting is the follow-up task wire-work-system-paths-to-settings.)
  github  Issues labelled `work-system:task` in origin's repository. The task
          name lives in a body marker `<!-- work-system task: <name> -->`; an
          issue carrying the label but no marker answers to the slug of its
          title. The branch stays task/<name>.
  auto    (default) Deterministic: task files in tasks/ → local, with a
          hint on leaving it; else an origin on github.com plus an installed gh →
          github; else local. An explicit value always wins.

A github backend whose gh is missing, unauthenticated or unreachable fails with
exit 4 and a message — never a silent switch to local, which would split the
backlog in two. Under `auto`, only an absent gh resolves to local; an installed
gh that fails is the same exit 4.

Output is key=value lines on stdout; `list` prints one `task=<name>\\t<ref>\\t<title>`
line per task (ref = #N or the file path relative to the main checkout).

`migrate` moves a local backlog (tasks/*.md and tasks/archive/*.md) into
origin's GitHub issues: see cmd_migrate. Without --apply it is a dry run that
prints the plan and a `confirm=<token>`; --apply <token> runs exactly that plan
and refuses one that changed since.

Exit: 0 ok · 1 a call failed (gh, git, settings, writing a file) · 2 usage ·
3 no such task · 4 backend unavailable · 5 conflict (name taken, ambiguous, or a
pinned issue that is not this task) · 6 migrate stopped by a GitHub rate limit
(re-run the dry run later; it resumes).
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
LABEL = "work-system:task"
LABEL_COLOR, LABEL_DESC = "5319e7", "A work-system task (managed by task-store.py)"
# One name alphabet for names, markers and refs: a marker that cannot parse a
# name create accepted would lose the task.
NAME = r"[A-Za-z0-9_][A-Za-z0-9._-]*"
MARKER_RE = re.compile(rf"<!--\s*work-system task:\s*({NAME})\s*-->")
# A local task name may keep an adopted branch's inner slashes (feature/foo/bar →
# foo/bar); the file flattens them (archive-task.sh does the same). Issue names
# and new tasks are flat.
NAME_RE = re.compile(rf"^{NAME}$")
LOCAL_NAME_RE = re.compile(rf"^{NAME}(/{NAME})*$")
# SSH forms; https is parsed by urlsplit, so userinfo is allowed but the host
# must really be github.com. SSH host aliases stay unknown.
GH_SSH_RE = re.compile(r"^(?:git@github\.com:|ssh://git@github\.com/)(.+)$")
GH_PATH_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
NOT_TASKS = {"_index.md", "README.md", "ROADMAP.md"}
TASKS_DIR = "tasks"
# gh issue list fetches at most this many; a result this long may be cut off,
# so it is an error rather than a backlog silently missing its oldest tasks.
LIST_LIMIT = 1000


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        return None


def oneline(s):
    return " ".join(str(s).split())


def main_checkout(d):
    r = run(["git", "-C", d, "worktree", "list", "--porcelain"])
    if r is None or r.returncode != 0:
        raise Fail(2, f"not a git repository: {d}")
    first = r.stdout.split("\n", 1)[0]
    if not first.startswith("worktree "):
        raise Fail(2, f"cannot resolve the main checkout of {d}")
    return first[len("worktree "):]


def setting(main, key):
    r = run([sys.executable, "-I", os.path.join(HERE, "ws-settings.py"), "get", main, key])
    if r is None or r.returncode != 0:
        raise Fail(1, f"ws-settings.py get {key} failed: {oneline(r.stderr if r else '')}")
    kv = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    if kv.get("note"):  # stderr: stdout may be a task body (get --body)
        print(f"note={kv['note']}", file=sys.stderr)
    return kv.get("value", "")


def task_files(main, rel):
    """Every task file in <rel> — what keeps `auto` on local. `list` hands a
    skill only the plain names (a file can be named `$(…).md`)."""
    d = os.path.join(main, rel)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return [n for n in names if n.endswith(".md") and n not in NOT_TASKS
            and os.path.isfile(os.path.join(d, n))]


def github_repo(main):
    r = run(["git", "-C", main, "remote", "get-url", "origin"])
    if r is None or r.returncode != 0:
        return None
    url = r.stdout.strip()
    m = GH_SSH_RE.match(url)
    if m:
        path = m.group(1)
    else:
        try:
            u = urlsplit(url)
            ok = u.scheme == "https" and u.hostname == "github.com" and not u.query \
                and not u.fragment and u.port is None
        except ValueError:
            ok = False
        if not ok:
            return None
        path = u.path.lstrip("/")
    m = GH_PATH_RE.match(path)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gh_ready():
    """None when gh answers for github.com, else the reason it does not."""
    # --active: without it gh exits 1 when ANY account on the host has a bad
    # token, even though the active one (the one every call uses) works.
    r = run(["gh", "auth", "status", "--active", "--hostname", "github.com"])
    if r is not None and r.returncode != 0 and "unknown flag" in r.stderr:
        r = run(["gh", "auth", "status", "--hostname", "github.com"])  # gh < 2.40
    if r is None:
        return "gh is not installed"
    if r.returncode != 0:
        return "gh is not authenticated for github.com or cannot reach it (gh auth status failed)"
    return None


def resolve(d):
    """(backend, main, info dict) — prints the resolution lines as it goes."""
    main = main_checkout(d)
    rel = TASKS_DIR
    want = setting(main, "tasks.backend") or "auto"
    if want == "local":
        return "local", main, {"source": "setting", "tasks_dir": rel}
    if want == "github":
        repo = github_repo(main)
        if not repo:
            raise Fail(4, "tasks.backend = \"github\" but origin is not a github.com repository")
        why = gh_ready()
        if why:
            raise Fail(4, f"tasks.backend = \"github\" but {why} — fix gh (gh auth login); "
                          "work-system does not fall back to local tasks")
        return "github", main, {"source": "setting", "repo": repo}
    files = task_files(main, rel)
    if files:
        return "local", main, {
            "source": "auto", "tasks_dir": rel,
            "reason": f"{len(files)} task file(s) in {rel}/",
            "hint": "these local task files keep the local backend; to use GitHub Issues, run "
                    "/work-system:migrate (moves them to issues), or set tasks.backend = "
                    "\"github\" in .work-system.toml"}
    repo = github_repo(main)
    if not repo:
        return "local", main, {"source": "auto", "tasks_dir": rel,
                               "reason": "origin is not a github.com repository"}
    why = gh_ready()
    if why == "gh is not installed":
        return "local", main, {"source": "auto", "tasks_dir": rel, "reason": why}
    if why:
        raise Fail(4, f"origin is on GitHub, so tasks live in its issues, but {why} — fix gh "
                      "(gh auth login), or set tasks.backend = \"local\" in .work-system.toml")
    return "github", main, {"source": "auto", "repo": repo,
                            "reason": "origin is a github.com repository and gh is ready"}


def gh(args, repo):
    r = run(["gh", *args, "-R", repo])
    if r is None:
        raise Fail(4, "gh is not installed")
    if r.returncode != 0:
        raise Fail(1, f"gh {args[0]} {args[1]} failed: {oneline(r.stderr)[:300]}")
    return r.stdout


def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60].rstrip("-") or "task"


def markers(issue):
    return MARKER_RE.findall(issue.get("body") or "")


def live(issues):
    """Open task issues minus history: an issue carrying work-system:archived is
    an archived task (a migrate run stopped before closing it must neither
    shadow nor block the live task of that name)."""
    return [i for i in issues if not has_label(i, ARCHIVED_LABEL)]


def issue_name(issue):
    # The LAST marker: create appends the real one after the body, so a
    # marker-shaped string quoted in the body cannot rename the task.
    found = markers(issue)
    return found[-1] if found else slugify(issue.get("title") or "")


def check_name(name, local=False):
    if not (LOCAL_NAME_RE if local else NAME_RE).match(name):
        raise Fail(2, f"not a plain task name: {name!r}")
    return name


def issue_ref(ref):
    # Only `#N`: a bare number is a task name (a task may be called 2024-q3).
    m = re.fullmatch(r"#([0-9]+)", ref)
    return int(m.group(1)) if m else None


# ---- github ----------------------------------------------------------------

FIELDS = "number,title,body,state,url,labels"


def gh_issues(repo, state, search=None):
    args = ["issue", "list", "--label", LABEL, "--state", state, "--limit", str(LIST_LIMIT),
            "--json", FIELDS] + (["--search", search] if search else [])
    issues = json.loads(gh(args, repo) or "[]")
    if len(issues) >= LIST_LIMIT:
        raise Fail(1, f"{LIST_LIMIT} or more {state} task issues in {repo} — the list may be "
                      "cut off; refer to the task as #N")
    return issues


def gh_find(repo, ref):
    n = issue_ref(ref)
    if n is not None:
        r = run(["gh", "issue", "view", str(n), "--json", FIELDS, "-R", repo])
        if r is None:
            raise Fail(4, "gh is not installed")
        if r.returncode != 0:
            if "could not resolve to an issue" in r.stderr.lower():
                raise Fail(3, f"no issue #{n} in {repo}")
            raise Fail(1, f"gh issue view {n} failed: {oneline(r.stderr)[:300]}")
        issue = json.loads(r.stdout)
        if LABEL not in [l.get("name") for l in issue.get("labels") or []]:
            raise Fail(3, f"#{n} is not a work-system task (no {LABEL} label)")
        return issue
    name = check_name(ref)
    # Open tasks are the working set and are listed whole. Closed ones only
    # matter for an idempotent re-close, and their history grows without bound,
    # so they are narrowed by a search on the marker and then matched exactly.
    # A work-system:archived issue is history (migrate closes it; one a stopped
    # run left open must not shadow or duplicate the live task).
    open_hits = [i for i in live(gh_issues(repo, "open")) if issue_name(i) == name]
    if len(open_hits) > 1:
        nums = ", ".join(f"#{i['number']}" for i in open_hits)
        raise Fail(5, f"task {name} is ambiguous: open issues {nums} — pass #N")
    if open_hits:
        return open_hits[0]
    hits = [i for i in gh_issues(repo, "closed", f'"work-system task: {name}" in:body')
            if issue_name(i) == name]
    if hits:
        return max(hits, key=lambda i: (not has_label(i, ARCHIVED_LABEL), i["number"]))
    raise Fail(3, f"no task {name} in {repo}")


def strip_marker(body):
    return MARKER_RE.sub("", body or "").strip("\n")


def with_marker(body, name):
    """The issue body a task is stored as: its text, then the one marker (the
    LAST marker is the name, so any quoted in the text are dropped first)."""
    return f"{MARKER_RE.sub('', body).strip()}\n\n<!-- work-system task: {name} -->\n"


def ensure_label(repo, label, color, desc):
    # Create once; never --force, which would reset a maintainer's colour or
    # description on every call.
    r = run(["gh", "label", "create", label, "--color", color, "--description", desc, "-R", repo])
    if r is None or (r.returncode != 0 and "already exists" not in r.stderr.lower()):
        raise Fail(1, f"gh label create failed: {oneline(r.stderr if r else '')[:300]}")


def issue_doc(issue):
    return (f"# {oneline(issue['title'])}\n\nissue: #{issue['number']}\n\n"
            f"{strip_marker(issue.get('body'))}\n")


# ---- local -----------------------------------------------------------------

def local_path(main, rel, name):
    return os.path.join(main, rel, name.replace("/", "-") + ".md")


def first_title(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    return oneline(line.lstrip("#"))
    except OSError:
        pass
    return ""


def write_out(path, text):
    try:  # O_NOFOLLOW in the open itself: no window between a check and the write
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    except OSError as e:
        raise Fail(1, f"cannot write {path}: {e.strerror}")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"written={path}")


# ---- subcommands -------------------------------------------------------------

def cmd_backend(d):
    backend, main, info = resolve(d)
    print(f"backend={backend}")
    for k in ("source", "repo", "tasks_dir", "reason", "hint"):
        if info.get(k):
            print(f"{k}={info[k]}")
    if backend == "github":
        print(f"visibility={repo_visibility(info['repo'])}")


def repo_visibility(repo):
    # `gh repo view` takes the repo as an argument and has no -R, so not gh().
    r = run(["gh", "repo", "view", repo, "--json", "visibility"])
    if r is None or r.returncode != 0:
        raise Fail(1, f"gh repo view failed: {oneline(r.stderr if r else '')[:300]}")
    return json.loads(r.stdout).get("visibility", "").lower()


def cmd_list(d):
    backend, main, info = resolve(d)
    print(f"backend={backend}")
    if info.get("hint"):
        print(f"hint={info['hint']}")
    rows = []
    if backend == "github":
        for i in sorted(live(gh_issues(info["repo"], "open")), key=lambda i: i["number"]):
            rows.append((issue_name(i), f"#{i['number']}", oneline(i["title"])))
    else:
        rel = info["tasks_dir"]
        every = task_files(main, rel)
        plain = [f for f in every if NAME_RE.match(f[:-3])]
        if len(every) > len(plain):
            print(f"note={len(every) - len(plain)} file(s) in {rel}/ not listed: the name is not "
                  "a plain task name (letters, digits, . _ -) — rename them", file=sys.stderr)
        for f in plain:
            rows.append((f[:-3], f"{rel}/{f}", first_title(os.path.join(main, rel, f))))
    for name, ref, title in rows:  # names are NAME_RE-clean: no tab or newline
        print(f"task={name}\t{ref}\t{title}")
    print(f"count={len(rows)}")


def cmd_get(d, ref, write, body):
    backend, main, info = resolve(d)
    if backend == "github":
        issue = gh_find(info["repo"], ref)
        doc = issue_doc(issue)
        meta = [("task", issue_name(issue)), ("issue", issue["number"]),
                ("url", issue.get("url", "")), ("state", issue.get("state", "").lower()),
                ("title", oneline(issue["title"]))]
    else:
        name = check_name(ref, local=True)
        path = local_path(main, info["tasks_dir"], name)
        if not os.path.isfile(path):
            raise Fail(3, f"no task file {os.path.relpath(path, main)}")
        with open(path, encoding="utf-8") as f:
            doc = f.read()
        meta = [("task", name), ("path", os.path.relpath(path, main)), ("state", "open"),
                ("title", first_title(path))]
    if body:
        sys.stdout.write(doc)
        return
    print(f"backend={backend}")
    for k, v in meta:
        print(f"{k}={v}")
    if write:
        write_out(write, doc)


def split_title(text, title):
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        head = oneline(lines.pop(0)[2:])
        title = title or head
    return title, "\n".join(lines).strip("\n")


def cmd_create(d, name, body_file, title):
    check_name(name)
    if f"{name}.md" in NOT_TASKS:
        raise Fail(2, f"{name} is a reserved file name in tasks/, not a task name")
    try:
        with open(body_file, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise Fail(2, f"cannot read --body-file: {e.strerror}")
    backend, main, info = resolve(d)
    print(f"backend={backend}")
    if backend == "local":
        path = local_path(main, info["tasks_dir"], name)
        if os.path.exists(path):
            raise Fail(5, f"task file already exists: {os.path.relpath(path, main)}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if title and not text.lstrip().startswith("# "):
            text = f"# {title}\n\n{text}"
        with open(path, "x", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        print(f"task={name}\npath={os.path.relpath(path, main)}")
        return
    repo = info["repo"]
    title, body = split_title(text, oneline(title or ""))
    if not title:
        raise Fail(2, "no title: pass --title or start the body with a '# ' heading")
    if any(issue_name(i) == name for i in live(gh_issues(repo, "open"))):
        raise Fail(5, f"an open task named {name} already exists in {repo}")
    ensure_label(repo, LABEL, LABEL_COLOR, LABEL_DESC)
    body = with_marker(body, name)
    r = run(["gh", "issue", "create", "--title", title, "--label", LABEL, "--body-file", "-",
             "-R", repo], input=body)
    if r is None or r.returncode != 0:
        raise Fail(1, f"gh issue create failed: {oneline(r.stderr if r else '')[:300]}")
    url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    m = re.search(r"/issues/([0-9]+)$", url)
    print(f"task={name}\nissue={m.group(1) if m else ''}\nurl={url}")


def cmd_close(d, ref, branch, pr, sha, name=None):
    backend, main, info = resolve(d)
    if backend == "local":
        args = ["bash", os.path.join(HERE, "archive-task.sh"), "archive", main,
                check_name(ref, local=True), branch]
        if pr:
            args += ["--pr", pr] + (["--sha", sha] if sha else [])
        print(f"backend={backend}", flush=True)
        os.execvp("bash", args)
    print(f"backend={backend}")
    repo = info["repo"]
    issue = gh_find(repo, ref)
    if name and issue_name(issue) != name:
        raise Fail(5, f"#{issue['number']} is task {issue_name(issue)}, not {name} — "
                      "check which issue this lane shipped")
    print(f"task={issue_name(issue)}\nissue={issue['number']}")
    if issue.get("state", "").upper() != "OPEN":
        print("result=already-closed")
        return
    if pr:
        note = f"Closed by work-system /close: merged in #{pr}" + (f" ({sha[:12]})" if sha else "") + "."
    else:
        note = "Closed manually by work-system /close (no merged PR)."
    gh(["issue", "close", str(issue["number"]), "--comment", note], repo)
    print("result=closed")


def cmd_ref(d, name, task_md):
    """The ref /close should close: `#N` from TASK.md's `issue:` line on the github
    backend (pinned=yes), else the name. On github a name is pinned=no: it may
    match a newer open issue that reuses it, so the caller confirms the match.
    The pin needs no gh: a backend that fails with exit 4 is a GitHub one, so a
    pin read from TASK.md still answers (the close itself will need gh)."""
    try:
        backend, main, info = resolve(d)
    except Fail as e:
        if e.code != 4:
            raise
        backend = None
        print(f"note={e}", file=sys.stderr)
    if backend == "local":
        print(f"backend=local\nref={check_name(name, local=True)}\npinned=yes")
        return
    print("backend=github")
    n = None
    if task_md:
        try:
            fd = os.open(task_md, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
                head = [next(f, "") for _ in range(20)]
            for line in head:
                m = re.fullmatch(r"issue: #([0-9]+)\n?", line)
                if m:
                    n = m.group(1)
                    break
        except OSError:
            pass  # a gone worktree: fall back to the name
    if n:
        print(f"ref=#{n}\npinned=yes")
    elif backend is None:
        raise Fail(4, "no issue pin in TASK.md and the GitHub backend is unavailable")
    else:
        # A local name may keep an adopted branch's slashes; its file — and so
        # the migrated issue's marker — flattened them.
        print(f"ref={check_name(check_name(name, local=True).replace('/', '-'))}\npinned=no")


# ---- migrate (local files → GitHub issues) ----------------------------------
#
# One item per task file: tasks/<name>.md is an `open` task, tasks/archive/<name>.md
# an `archived` one. Each gets one action:
#   create  a new issue (archived: then closed as completed)
#   adopt   the file names an existing issue (`Tracking issue: [owner/repo]#N`):
#           that issue gets the label, the marker and the file as its body
#   resume  an issue with this task's marker already exists (an earlier run, or
#           this one stopped half-way): finish it (close, move the file)
#   skip    left alone, with a reason
# Identity is (marker, archived label): an open and an archived task may share a
# name. Every step is safe to repeat, so a stopped run resumes on the next one —
# the issue is written before the file moves, and a written issue is found by
# its marker. Files move to tasks/migrated/ (never deleted); the map
# tasks/migrated/issues.tsv records task ↔ issue.

ARCHIVED_LABEL = "work-system:archived"
MIGRATED_DIR = "migrated"
MAPPING = "issues.tsv"
BODY_MAX = 65536  # GitHub's limit on an issue body
TRACKING_RE = re.compile(
    r"^[>\s*_]*Tracking issue[*_]*:?[*_]*\s*"
    r"(?:https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/"
    r"|([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#)([0-9]+)\b.*$", re.I | re.M)
# Names a hit, never its value: the dry run output lands in a transcript.
SECRET_PATTERNS = [
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    ("aws-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("api-key", r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|\bAIza[0-9A-Za-z_-]{35}\b"
                r"|\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|\bglpat-[A-Za-z0-9_-]{20,}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}"),
    ("url-credentials", r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
    ("env-assignment", r"(?m)^[ \t]*(?:export[ \t]+)?[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|"
                       r"API_?KEY|PRIVATE_KEY|CREDENTIALS?)[A-Z0-9_]*\s*=\s*['\"]?[^\s'\"]{6,}"),
    ("secret-assignment", r"(?i)\b[a-z0-9_]*(?:password|passwd|secret|api[_-]?key|"
                          r"access[_-]?token|client[_-]?secret)[a-z0-9_]*['\"]?\s*[:=]\s*"
                          r"['\"]?[^\s'\"`]{8,}"),
    ("private-host", r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|internal|lan|corp|intranet|"
                     r"home\.arpa)\b(?!\.?[\w-])"),
    ("private-ip", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
]
# Fine in a private repo's issues, not in a public one's.
PUBLIC_PATTERNS = [("local-path", r"(?:/Users/|/home/)[A-Za-z0-9._-]+|\b[A-Z]:\\Users\\")]
RATE_LIMIT_RE = re.compile(r"rate limit|secondary rate|abuse detection|submitted too quickly|"
                           r"HTTP 429", re.I)


def scan(text, public):
    hits = []
    for kind, pat in SECRET_PATTERNS + (PUBLIC_PATTERNS if public else []):
        for m in re.finditer(pat, text):
            hits.append((text.count("\n", 0, m.start()) + 1, kind))
    return sorted(set(hits))


def task_doc(text):
    """(title, body): the first `# ` heading is the title and leaves the body;
    everything else (an archive stamp above it included) stays."""
    lines = text.splitlines()
    title = ""
    for k, line in enumerate(lines):
        if line.startswith("# "):
            title = oneline(line[2:])
            del lines[k]
            break
    return title, "\n".join(lines).strip("\n")


def has_label(issue, label):
    return label in [l.get("name") for l in issue.get("labels") or []]



def gh_write(args, repo, body=None):
    """A content-creating gh call: a rate-limit answer stops the run (exit 6)."""
    r = run(["gh", *args, "-R", repo], input=body)
    if r is None:
        raise Fail(4, "gh is not installed")
    if r.returncode != 0:
        if RATE_LIMIT_RE.search(r.stderr):
            raise Fail(6, f"GitHub rate limit on gh {args[0]} {args[1]} — nothing is lost: wait a "
                          "few minutes, run the dry run again and apply its new plan to resume")
        raise Fail(1, f"gh {args[0]} {args[1]} failed: {oneline(r.stderr)[:300]} — "
                      "run the dry run again to resume")
    return r.stdout


def migrate_index(repo):
    """Every issue with the task label, open and closed, through the REST issues
    endpoint: `gh issue list --label` goes through the search API, which stops
    at 1000 results and lags behind a create — and this index is what keeps a
    re-run from creating a duplicate."""
    out = gh_list(["api", "--paginate", f"repos/{repo}/issues?labels={LABEL}&state=all&per_page=100",
                   "--jq", ".[]"])
    issues = []
    for line in out.splitlines():
        if line.strip():
            i = json.loads(line)
            if "pull_request" not in i:
                i["state"] = (i.get("state") or "").upper()
                issues.append(i)
    return issues


def gh_list(args):
    r = run(["gh", *args])
    if r is None:
        raise Fail(4, "gh is not installed")
    if r.returncode != 0:
        raise Fail(1, f"gh {args[0]} failed: {oneline(r.stderr)[:300]}")
    return r.stdout


def read_task_file(path):
    """The file's text, or None for a symlink or anything not a regular file:
    a link to ../.env must never become an issue."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        return f.read()


def digest(text):
    return hashlib.sha256((text or "").encode()).hexdigest()


def migrate_plan(main, repo, public, includes, open_only):
    items, unplain = [], 0
    dirs = [("open", TASKS_DIR)] + ([] if open_only else [("archived", f"{TASKS_DIR}/archive")])
    for kind, rel in dirs:
        if os.path.islink(os.path.join(main, rel)):
            raise Fail(1, f"{rel}/ is a symlink — migrate reads only the repo's own task files")
        for f in task_files(main, rel):
            if not NAME_RE.match(f[:-3]):
                unplain += 1
                continue
            path = os.path.join(main, rel, f)
            text = read_task_file(path)
            items.append({"kind": kind, "name": f[:-3], "path": path, "rel": f"{rel}/{f}",
                          "text": text or "", "link": text is None})
    ids = {f"{i['kind']}/{i['name']}" for i in items}
    for inc in includes:
        n = sum(1 for i in items if i["name"] == inc)
        if n > 1:
            raise Fail(2, f"--include {inc}: an open and an archived task share this name — "
                          f"name one: open/{inc} or archived/{inc}")
        if inc not in ids and not n:
            raise Fail(2, f"--include {inc}: no such task file (use <name> or open|archived/<name>)")

    by_key, unmarked_open = {}, {}
    for i in (migrate_index(repo) if items else []):
        found = markers(i)
        if found:
            by_key.setdefault((found[-1], has_label(i, ARCHIVED_LABEL)), []).append(i)
        elif i.get("state", "").upper() == "OPEN":
            unmarked_open.setdefault(issue_name(i), i)

    for it in items:
        archived = it["kind"] == "archived"
        name = it["name"]
        it["title"], it["body"] = task_doc(it["text"])
        it["title"] = it["title"] or name
        it["hits"] = scan(it["text"], public)
        it.update(issue=None, state="", reason="", seen="", differs=False)
        if it["link"]:
            it.update(action="skip", reason="a symlink or not a regular file — never published")
            continue
        # The text the issue must hold for this file: with or without a
        # tracking line (an adopt drops it, a create keeps one for another repo).
        cores = {strip_marker(it["body"]).strip(),
                 strip_marker(TRACKING_RE.sub("", it["body"], count=1)).strip()}
        existing = by_key.get((name, archived), [])
        if len(existing) > 1:
            it.update(action="skip", reason="several issues carry this task's marker: "
                      + ", ".join(f"#{i['number']}" for i in existing))
            continue
        if existing:
            iss = existing[0]
            it.update(issue=iss["number"], state=iss.get("state", "").upper(),
                      seen=digest(iss.get("body")))
            # Text that differs is shown, not refused: the issue is the task
            # people kept editing, and the file is moved, not deleted.
            it["differs"] = not any(c in strip_marker(iss.get("body")) for c in cores)
            if not archived and it["state"] != "OPEN":
                it.update(action="skip", reason=f"issue #{iss['number']} for this task is "
                          "closed — reopen it, or remove the file")
            else:
                it["action"] = "resume"
            continue
        it["action"] = "create"
        m = TRACKING_RE.search(it["text"])
        if m and (m.group(1) or m.group(2) or repo).lower() == repo.lower():
            n = int(m.group(3))
            r = run(["gh", "issue", "view", str(n), "--json", FIELDS, "-R", repo])
            if r is None or r.returncode != 0:
                if r is not None and "could not resolve to an issue" in r.stderr.lower():
                    it.update(action="skip", reason=f"its tracking issue #{n} does not exist")
                    continue
                raise Fail(1, f"gh issue view {n} failed: {oneline(r.stderr if r else '')[:300]}")
            iss = json.loads(r.stdout)
            it.update(issue=n, state=iss.get("state", "").upper(), seen=digest(iss.get("body")))
            other = [x for x in markers(iss) if x != name]
            if other:
                it.update(action="skip", reason=f"its tracking issue #{n} is task {other[-1]}")
                continue
            if "/pull/" in (iss.get("url") or ""):
                it.update(action="skip", reason=f"its tracking line names pull request #{n}, "
                                                "not an issue — drop the line to get a new issue")
                continue
            if has_label(iss, ARCHIVED_LABEL) != archived:
                it.update(action="skip", reason=f"its tracking issue #{n} is the "
                          f"{'open' if archived else 'archived'} task {name}, not this one")
                continue
            if not archived and it["state"] != "OPEN":
                it.update(action="skip", reason=f"its tracking issue #{n} is closed — reopen it "
                                                f"or drop the line to get a new issue")
                continue
            # Drop the self-reference; keep what the issue said that the file does not.
            body = TRACKING_RE.sub("", it["body"], count=1).strip("\n")
            old = strip_marker(iss.get("body")).strip()
            if old and old not in body:
                body += ("\n\n<details><summary>Issue text before the migration</summary>\n\n"
                         f"{old}\n\n</details>")
            it.update(action="adopt", body=body)
        elif not archived and name in unmarked_open:
            it.update(action="skip", reason=f"open issue #{unmarked_open[name]['number']} already "
                                            f"answers to {name} (a task issue without a marker)")
            continue
        if it["hits"] and name not in includes and f"{it['kind']}/{name}" not in includes:
            it.update(action="skip", reason="possible secret or private detail (see hit= lines); "
                                            f"--include {it['kind']}/{name} publishes it anyway")
            continue
        it["body"] = with_marker(it["body"], name)
        if len(it["body"]) > BODY_MAX:
            it.update(action="skip", reason=f"longer than GitHub's {BODY_MAX}-character issue body")

    # One issue can hold one task: two files naming the same tracking issue
    # would overwrite each other's body (and an archived one would close it).
    targets = {}
    for it in items:
        if it["action"] in ("adopt", "resume"):
            targets.setdefault(it["issue"], []).append(it)
    for n, group in targets.items():
        if len(group) > 1:
            names = ", ".join(f"{i['kind']}/{i['name']}" for i in group)
            for it in group:
                it.update(action="skip", reason=f"issue #{n} is claimed by several "
                                                f"files ({names}) — keep the tracking line in one")
    return items, unplain


def plan_token(repo, visibility, items):
    # The issue text an adopt or resume decided on (`seen`) is part of the plan:
    # an edit after the dry run would otherwise be published unseen.
    rows = [[i["kind"], i["name"], i["action"], i["issue"], i["state"], i["seen"],
             digest(i["text"])] for i in items]
    blob = json.dumps([repo, visibility, rows], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def check_dest(main):
    """tasks/migrated/ (and its archive/ and map) must be the repo's own: a
    symlink there would carry task text or map rows out of the repo."""
    base = os.path.join(main, TASKS_DIR, MIGRATED_DIR)
    for p in (base, os.path.join(base, "archive"), os.path.join(base, MAPPING)):
        if os.path.islink(p):
            raise Fail(1, f"{os.path.relpath(p, main)} is a symlink — remove it and run again")


def append_mapping(main, it, n):
    path = os.path.join(main, TASKS_DIR, MIGRATED_DIR, MAPPING)
    line = f"{it['kind']}\t{it['name']}\t#{n}\t{it['rel']}\n"
    try:
        with open(path, encoding="utf-8") as f:
            if line in f.read():
                return
    except FileNotFoundError:
        with open(path, "w", encoding="utf-8") as f:
            f.write("kind\ttask\tissue\tmigrated_from\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def move_file(main, it):
    dest_dir = os.path.join(main, TASKS_DIR, MIGRATED_DIR,
                            *(["archive"] if it["kind"] == "archived" else []))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(it["path"]))
    if os.path.lexists(dest):
        raise Fail(1, f"{os.path.relpath(dest, main)} already exists — move "
                      f"{it['rel']} by hand, then run the dry run again")
    os.rename(it["path"], dest)
    return os.path.relpath(dest, main)


def backend_config(root):
    """(path, new text | None, problem | None) for tasks.backend = "github" in
    .work-system.toml: every other line (and comment) kept, the result re-parsed
    and required to differ from the old config in that one key only. new None and
    no problem = already set. Checked in the dry run, written at the end of apply.
    Not settings.py set: that plugin is optional, cannot locate an installed
    work-system's schema yet, and rewrites the file without its comments."""
    path = os.path.join(root, ".work-system.toml")
    manual = f"set tasks.backend = \"github\" in {path} by hand"
    try:
        import tomllib
    except ImportError:
        return path, None, f"python {sys.version_info[0]}.{sys.version_info[1]} has no tomllib — {manual}"
    try:
        old = read_task_file(path) if os.path.lexists(path) else ""
    except OSError as e:
        return path, None, f"cannot read it ({e.strerror}) — {manual}"
    if old is None:
        return path, None, f"it is a symlink or not a regular file — {manual}"
    try:
        before = tomllib.loads(old)
    except tomllib.TOMLDecodeError as e:
        return path, None, f"it is not valid TOML ({e}) — {manual}"
    if (before.get("tasks") or {}).get("backend") == "github":
        return path, None, None
    lines = old.splitlines(keepends=True)
    head = next((k for k, l in enumerate(lines) if re.match(r"\s*\[tasks\]\s*(#.*)?$", l)), None)
    if head is None:
        new = old + ("" if not old or old.endswith("\n") else "\n") + \
            ("\n" if old.strip() else "") + '[tasks]\nbackend = "github"\n'
    else:
        k = head + 1
        while k < len(lines) and not re.match(r"\s*\[", lines[k]):
            if re.match(r"\s*backend\s*=", lines[k]):
                lines[k] = 'backend = "github"\n'
                break
            k += 1
        else:
            lines.insert(head + 1, 'backend = "github"\n')
        new = "".join(lines)
    try:
        after = tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        after = None
    ok = after is not None and (after.get("tasks") or {}).get("backend") == "github"
    if ok:
        for cfg in (before, after):
            (cfg.get("tasks") or {}).pop("backend", None)
            if cfg.get("tasks") == {}:
                cfg.pop("tasks")
        ok = before == after
    if not ok:
        return path, None, f"the edit would not be clean (dotted keys or an inline table?) — {manual}"
    return path, new, None


def write_config(path, new):
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except FileNotFoundError:
        mode = 0o644
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    except OSError as e:
        raise Fail(1, f"cannot write {tmp}: {e.strerror} — set tasks.backend = \"github\" by hand")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    os.chmod(tmp, mode)  # the umask may have narrowed the create mode
    os.replace(tmp, path)


def git_tracked(main, *paths):
    r = run(["git", "-C", main, "ls-files", "--", *paths])
    return bool(r and r.returncode == 0 and r.stdout.strip())


def cmd_migrate(d, token, includes, open_only, pace):
    main = main_checkout(d)
    repo = github_repo(main)
    if not repo:
        raise Fail(4, "origin is not a github.com repository — nothing to migrate to")
    why = gh_ready()
    if why:
        raise Fail(4, f"cannot migrate: {why} — fix gh (gh auth login)")
    visibility = repo_visibility(repo)
    items, unplain = migrate_plan(main, repo, visibility == "public", includes, open_only)
    tok = plan_token(repo, visibility, items)
    check_dest(main)
    tracked = git_tracked(main, *[i["rel"] for i in items]) if items else False
    # The main checkout's own config: the backlog it switches is that checkout's.
    cfg_path, cfg_new, cfg_problem = backend_config(main)
    print(f"repo={repo}\nvisibility={visibility}")
    print(f"tasks_tracked={'yes' if tracked else 'no'}")
    if cfg_problem:
        print(f"config_edit=manual\t{cfg_problem}")
    for it in items:
        target = f"#{it['issue']}" if it["issue"] else "new"
        # A held-back file's heading may be where the hit is: never print it.
        shown = it["name"] in includes or f"{it['kind']}/{it['name']}" in includes
        title = "(held back)" if it["hits"] and not shown else it["title"]
        print(f"item={it['action']}\t{it['kind']}/{it['name']}\t{target}\t{title}")
        if it["action"] == "skip":
            print(f"skip={it['kind']}/{it['name']}\t{it['reason']}")
        elif it["differs"]:
            print(f"differs={it['kind']}/{it['name']}\t#{it['issue']}")
        for line, kind in it["hits"]:
            print(f"hit={it['kind']}/{it['name']}\t{line}\t{kind}")
    if unplain:
        print(f"note={unplain} task file(s) not migrated: the name is not a plain task name "
              "(letters, digits, . _ -) — rename them and run again", file=sys.stderr)
    counts = {a: sum(1 for i in items if i["action"] == a)
              for a in ("create", "adopt", "resume", "skip")}
    for a, c in counts.items():
        print(f"count_{a}={c}")
    print(f"count_differs={sum(1 for i in items if i['action'] == 'resume' and i['differs'])}")
    print(f"publish={counts['create'] + counts['adopt']}\nunplain={unplain}")
    if token is None:
        print(f"confirm={tok}")
        return
    if token != tok:
        raise Fail(5, "the plan changed since the dry run, or the token is not its confirm= — "
                      "run the dry run again and confirm that plan")

    work = [i for i in items if i["action"] != "skip"]
    if any(i["action"] in ("create", "adopt") for i in work):
        ensure_label(repo, LABEL, LABEL_COLOR, LABEL_DESC)
        if any(i["kind"] == "archived" for i in work):
            ensure_label(repo, ARCHIVED_LABEL, "c5def5", "A work-system task migrated from the archive")
    writes = 0

    def paced():
        nonlocal writes
        if writes and pace > 0:
            time.sleep(pace)
        writes += 1

    os.makedirs(os.path.join(main, TASKS_DIR, MIGRATED_DIR), exist_ok=True)
    for it in work:
        archived = it["kind"] == "archived"
        labels = [LABEL] + ([ARCHIVED_LABEL] if archived else [])
        if it["action"] == "create":
            paced()
            out = gh_write(["issue", "create", "--title", it["title"],
                            *[x for l in labels for x in ("--label", l)], "--body-file", "-"],
                           repo, it["body"])
            m = re.search(r"/issues/([0-9]+)\s*$", out.strip())
            if not m:
                raise Fail(1, f"gh issue create gave no issue URL for {it['rel']} — run the dry "
                              "run again (an issue it did create is found by its marker)")
            it.update(issue=int(m.group(1)), state="OPEN")
        elif it["action"] == "adopt":
            paced()
            gh_write(["issue", "edit", str(it["issue"]), "--add-label", ",".join(labels),
                      "--body-file", "-"], repo, it["body"])
        if archived and it["state"] == "OPEN":
            paced()
            gh_write(["issue", "close", str(it["issue"]), "--reason", "completed"], repo)
        append_mapping(main, it, it["issue"])
        if read_task_file(it["path"]) != it["text"]:
            raise Fail(1, f"{it['rel']} changed during the run; issue #{it['issue']} holds the "
                          "version from the dry run — run the dry run again (it resumes)")
        dest = move_file(main, it)
        print(f"done={it['action']}\t{it['kind']}/{it['name']}\t#{it['issue']}\t{dest}")

    left = task_files(main, TASKS_DIR)
    odd = [f for f in left if not NAME_RE.match(f[:-3])]
    if odd:
        print(f"backend_switched=no\nreason={len(odd)} task file(s) in {TASKS_DIR}/ have names "
              "migrate cannot use — rename them to plain task names and run again")
    elif left:
        print(f"backend_switched=no\nreason={len(left)} task file(s) still in {TASKS_DIR}/ — "
              "the backlog stays local, so the tasks already migrated are not listed until "
              "the rest is migrated (--include) or moved out of tasks/")
    elif cfg_problem:
        print(f"backend_switched=no\nreason={cfg_problem}")
    else:
        if cfg_new is not None:
            write_config(cfg_path, cfg_new)
        print("backend_switched=yes")
        print(f"config_changed={'.work-system.toml' if cfg_new is not None else 'no'}")
    paths = ([TASKS_DIR] if tracked else []) + \
        ([".work-system.toml"] if git_tracked(main, ".work-system.toml") else [])
    if paths:
        print("commit_needed=" + " ".join(paths))
    print("result=migrated")


def usage():
    raise Fail(2, "usage: task-store.py backend|list <dir> · get <dir> <name|#N> [--write <path>|--body]"
                  " · create <dir> <name> --body-file <file> [--title <t>]"
                  " · close <dir> <name|#N> --branch <b> [--pr <n>] [--sha <s>] [--name <task>]"
                  " · ref <dir> <name> [<TASK.md>]"
                  " · migrate <dir> [--apply <token>] [--include <name>]... [--open-only]"
                  " [--pace <s>]")


def opts(rest, allowed):
    out, i = {}, 0
    while i < len(rest):
        k = rest[i]
        if k == "--body" and "--body" in allowed:
            out[k] = True
            i += 1
            continue
        if k not in allowed or i + 1 >= len(rest):
            usage()
        out[k] = rest[i + 1]
        i += 2
    return out


def main(argv):
    if len(argv) < 3:
        usage()
    cmd, d, rest = argv[1], argv[2], argv[3:]
    if cmd in ("backend", "list"):
        if rest:
            usage()
        (cmd_backend if cmd == "backend" else cmd_list)(d)
    elif cmd == "get" and rest:
        o = opts(rest[1:], {"--write", "--body"})
        if "--write" in o and "--body" in o:
            usage()
        cmd_get(d, rest[0], o.get("--write"), o.get("--body", False))
    elif cmd == "create" and rest:
        o = opts(rest[1:], {"--body-file", "--title"})
        if "--body-file" not in o:
            usage()
        cmd_create(d, rest[0], o["--body-file"], o.get("--title"))
    elif cmd == "ref" and rest and len(rest) <= 2:
        cmd_ref(d, rest[0], rest[1] if len(rest) == 2 else None)
    elif cmd == "close" and rest:
        o = opts(rest[1:], {"--branch", "--pr", "--sha", "--name"})
        if "--branch" not in o or not re.fullmatch(r"[0-9]*", o.get("--pr", "")):
            usage()
        cmd_close(d, rest[0], o["--branch"], o.get("--pr", ""), o.get("--sha", ""),
                  o.get("--name"))
    elif cmd == "migrate":
        token, includes, open_only, pace, i = None, [], False, 2.0, 0
        while i < len(rest):
            k = rest[i]
            if k == "--open-only":
                open_only, i = True, i + 1
                continue
            if i + 1 >= len(rest):
                usage()
            v = rest[i + 1]
            if k == "--apply" and re.fullmatch(r"[0-9a-f]{12}", v):
                token = v
            elif k == "--include":
                includes.append(v)
            elif k == "--pace" and re.fullmatch(r"[0-9]+(\.[0-9]+)?", v):
                pace = float(v)
            else:
                usage()
            i += 2
        cmd_migrate(d, token, includes, open_only, pace)
    else:
        usage()


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Fail as e:
        print(f"task-store.py: {e}", file=sys.stderr)
        sys.exit(e.code)
