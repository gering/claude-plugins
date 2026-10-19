#!/usr/bin/env python3
"""task-store.py — the one path from a work-system skill to the task backlog.

Usage:
  task-store.py backend <dir>
  task-store.py list    <dir>
  task-store.py get     <dir> <name|#N> [--write <path> | --body]   (an issue ref needs the #)
  task-store.py create  <dir> <name> --body-file <file> [--title <text>]
  task-store.py close   <dir> <name|#N> --branch <task-branch> [--pr <n>] [--sha <sha>]

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

Exit: 0 ok · 1 a gh/git call failed · 2 usage · 3 no such task · 4 backend
unavailable · 5 conflict (name taken, or ambiguous).
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LABEL = "work-system:task"
MARKER_RE = re.compile(r"<!--\s*work-system task:\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*-->")
# A local task name may keep an adopted branch's inner slashes (feature/foo/bar →
# foo/bar); the file flattens them (archive-task.sh does the same). Issue names
# and new tasks are flat.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
LOCAL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
GH_URL_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                       r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
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
        raise Fail(2, f"ws-settings.py get {key} failed: {oneline(r.stderr if r else '')}")
    kv = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    if kv.get("note"):  # stderr: stdout may be a task body (get --body)
        print(f"note={kv['note']}", file=sys.stderr)
    return kv.get("value", "")


def task_files(main, rel, plain=True):
    """Task files in <rel>. plain=False is every one of them — what keeps `auto`
    on local; plain=True only those whose name is a plain task name, the ones a
    skill may paste into a shell command (a file can be named `$(…).md`)."""
    d = os.path.join(main, rel)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return [n for n in names if n.endswith(".md") and n not in NOT_TASKS
            and (not plain or NAME_RE.match(n[:-3])) and os.path.isfile(os.path.join(d, n))]


def github_repo(main):
    r = run(["git", "-C", main, "remote", "get-url", "origin"])
    if r is None or r.returncode != 0:
        return None
    m = GH_URL_RE.match(r.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gh_ready():
    """None when gh answers for github.com, else the reason it does not."""
    r = run(["gh", "auth", "status", "--hostname", "github.com"])
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
    files = task_files(main, rel, plain=False)
    if files:
        return "local", main, {
            "source": "auto", "tasks_dir": rel,
            "reason": f"{len(files)} task file(s) in {rel}/",
            "hint": "these local task files keep the local backend; to use GitHub Issues, move "
                    "them to issues and archive the files, or set tasks.backend = \"github\" "
                    "in .work-system.toml"}
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


def issue_name(issue):
    # The LAST marker: create appends the real one after the body, so a
    # marker-shaped string quoted in the body cannot rename the task.
    found = MARKER_RE.findall(issue.get("body") or "")
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
    open_hits = [i for i in gh_issues(repo, "open") if issue_name(i) == name]
    if len(open_hits) > 1:
        nums = ", ".join(f"#{i['number']}" for i in open_hits)
        raise Fail(5, f"task {name} is ambiguous: open issues {nums} — pass #N")
    if open_hits:
        return open_hits[0]
    hits = [i for i in gh_issues(repo, "closed", f'"work-system task: {name}" in:body')
            if issue_name(i) == name]
    if hits:
        return max(hits, key=lambda i: i["number"])
    raise Fail(3, f"no task {name} in {repo}")


def strip_marker(body):
    return MARKER_RE.sub("", body or "").strip("\n")


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
        raise Fail(2, f"cannot write {path}: {e.strerror}")
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
        # `gh repo view` takes the repo as an argument and has no -R, so not gh().
        r = run(["gh", "repo", "view", info["repo"], "--json", "visibility"])
        if r is None or r.returncode != 0:
            raise Fail(1, f"gh repo view failed: {oneline(r.stderr if r else '')[:300]}")
        print(f"visibility={json.loads(r.stdout).get('visibility', '').lower()}")


def cmd_list(d):
    backend, main, info = resolve(d)
    print(f"backend={backend}")
    if info.get("hint"):
        print(f"hint={info['hint']}")
    rows = []
    if backend == "github":
        for i in sorted(gh_issues(info["repo"], "open"), key=lambda i: i["number"]):
            rows.append((issue_name(i), f"#{i['number']}", oneline(i["title"])))
    else:
        rel = info["tasks_dir"]
        skipped = len(task_files(main, rel, plain=False)) - len(task_files(main, rel))
        if skipped:
            print(f"note={skipped} file(s) in {rel}/ not listed: the name is not a plain task "
                  "name (letters, digits, . _ -) — rename them", file=sys.stderr)
        for f in task_files(main, rel):
            rows.append((f[:-3], f"{rel}/{f}", first_title(os.path.join(main, rel, f))))
    for name, ref, title in rows:
        if "\t" in name or "\n" in name:
            continue
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
    if any(issue_name(i) == name for i in gh_issues(repo, "open")):
        raise Fail(5, f"an open task named {name} already exists in {repo}")
    gh(["label", "create", LABEL, "--color", "5319e7", "--force",
        "--description", "A work-system task (managed by task-store.py)"], repo)
    body = f"{MARKER_RE.sub('', body).strip()}\n\n<!-- work-system task: {name} -->\n"
    r = run(["gh", "issue", "create", "--title", title, "--label", LABEL, "--body-file", "-",
             "-R", repo], input=body)
    if r is None or r.returncode != 0:
        raise Fail(1, f"gh issue create failed: {oneline(r.stderr if r else '')[:300]}")
    url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    m = re.search(r"/issues/([0-9]+)$", url)
    print(f"task={name}\nissue={m.group(1) if m else ''}\nurl={url}")


def cmd_close(d, ref, branch, pr, sha):
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


def usage():
    raise Fail(2, "usage: task-store.py backend|list <dir> · get <dir> <name|#N> [--write <path>|--body]"
                  " · create <dir> <name> --body-file <file> [--title <t>]"
                  " · close <dir> <name|#N> --branch <b> [--pr <n>] [--sha <s>]")


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
    elif cmd == "close" and rest:
        o = opts(rest[1:], {"--branch", "--pr", "--sha"})
        if "--branch" not in o or not re.fullmatch(r"[0-9]*", o.get("--pr", "")):
            usage()
        cmd_close(d, rest[0], o["--branch"], o.get("--pr", ""), o.get("--sha", ""))
    else:
        usage()


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Fail as e:
        print(f"task-store.py: {e}", file=sys.stderr)
        sys.exit(e.code)
