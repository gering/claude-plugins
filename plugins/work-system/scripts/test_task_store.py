#!/usr/bin/env python3
"""Tests for task-store.py — the one path from a skill to the task backlog.

Run standalone (`python3 test_task_store.py`) or via check-structure.py. What is
pinned: the backend resolution matrix (explicit setting / auto with local task
files / auto with a GitHub origin / no GitHub origin / gh absent or failing),
the GitHub backend end to end through a stubbed `gh` on PATH (label, body
marker, lookup by name and #N, TASK.md copy, idempotent close), and that the
local backend keeps the task files and archive-task.sh.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "task-store.py"
FAILS = []

# A fake gh: state lives in $GH_STUB_STATE (issues, auth, visibility), every
# call is appended to $GH_STUB_LOG. Only the subcommands task-store.py uses.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
st_path = os.environ["GH_STUB_STATE"]
st = json.load(open(st_path))
args = sys.argv[1:]
with open(os.environ["GH_STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default
def save():
    json.dump(st, open(st_path, "w"))
cmd = args[:2]
if cmd == ["auth", "status"]:
    if st.get("old_gh") and "--active" in args:
        print("unknown flag: --active", file=sys.stderr); sys.exit(1)
    sys.exit(0 if st.get("auth", True) else 1)
if cmd == ["repo", "view"]:
    if "-R" in args:  # the real gh: unknown shorthand flag
        print("unknown shorthand flag: 'R' in -R", file=sys.stderr); sys.exit(1)
    print(json.dumps({"visibility": st.get("visibility", "PRIVATE")})); sys.exit(0)
if cmd == ["label", "create"]:
    if "--force" in args:
        print("stub: --force would overwrite the label", file=sys.stderr); sys.exit(2)
    st.setdefault("labels", [])
    if args[2] in st["labels"]:
        print(f"label with name \"{args[2]}\" already exists", file=sys.stderr); sys.exit(1)
    st["labels"].append(args[2]); save(); sys.exit(0)
issues = st.setdefault("issues", [])
if args[:1] == ["api"]:
    # REST issues listing (migrate's consistent index): one JSON object per line,
    # as --jq '.[]' prints it; states are lower case there.
    import urllib.parse
    label = urllib.parse.parse_qs(urllib.parse.urlsplit(args[2]).query).get("labels", [None])[0]
    for i in issues:
        if label is None or label in [x["name"] for x in i["labels"]]:
            print(json.dumps(dict(i, state=i["state"].lower())))
    sys.exit(0)
def has_label(i, l): return l in [x["name"] for x in i["labels"]]
if cmd == ["issue", "list"]:
    state = opt("--state", "open").upper(); label = opt("--label")
    out = [i for i in issues if (state == "ALL" or i["state"] == state)
           and (label is None or has_label(i, label))]
    print(json.dumps(out)); sys.exit(0)
if cmd == ["issue", "view"]:
    if st.get("view_error"):
        print(st["view_error"], file=sys.stderr); sys.exit(1)
    n = int(args[2]); hit = [i for i in issues if i["number"] == n]
    if not hit:
        print("GraphQL: Could not resolve to an issue", file=sys.stderr); sys.exit(1)
    print(json.dumps(hit[0])); sys.exit(0)
def opts(name):
    return [args[k + 1] for k, a in enumerate(args[:-1]) if a == name]
def write_call():
    # rate_limit_after = how many content-creating calls succeed before a 403
    left = st.get("rate_limit_after")
    if left is not None:
        if left <= 0:
            print("HTTP 403: You have exceeded a secondary rate limit", file=sys.stderr); sys.exit(1)
        st["rate_limit_after"] = left - 1
if cmd == ["issue", "create"]:
    labels = opts("--label")
    if any(l not in st.get("labels", []) for l in labels):
        print("could not add label", file=sys.stderr); sys.exit(1)
    write_call()
    n = max([i["number"] for i in issues] + [0]) + 1
    body = sys.stdin.read() if opt("--body-file") == "-" else ""
    issues.append({"number": n, "title": opt("--title"), "body": body, "state": "OPEN",
                   "url": f"https://github.com/o/r/issues/{n}",
                   "labels": [{"name": l} for l in labels]})
    save()
    if st.get("create_lost_reply"):  # created, but the answer never arrived
        print("stub: connection reset", file=sys.stderr); sys.exit(1)
    print(f"https://github.com/o/r/issues/{n}"); sys.exit(0)
if cmd == ["issue", "edit"]:
    write_call()
    n = int(args[2])
    for i in issues:
        if i["number"] == n:
            for l in ",".join(opts("--add-label")).split(","):
                if l and l not in [x["name"] for x in i["labels"]]:
                    i["labels"].append({"name": l})
            if opt("--body-file") == "-":
                i["body"] = sys.stdin.read()
    save(); sys.exit(0)
if cmd == ["issue", "close"]:
    write_call()
    n = int(args[2])
    for i in issues:
        if i["number"] == n:
            i["state"] = "CLOSED"; i["comment"] = opt("--comment"); i["reason"] = opt("--reason")
    save(); sys.exit(0)
print("stub: unsupported " + " ".join(args), file=sys.stderr); sys.exit(2)
'''


def check(name, cond):
    if not cond:
        FAILS.append(name)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def kv(out, key):
    return [l.split("=", 1)[1] for l in out.splitlines() if l.startswith(key + "=")]


def issue_name_of(i):
    import re
    m = re.findall(r"<!-- work-system task: (\S+) -->", i.get("body") or "")
    return m[-1] if m else ""


def issue(n, title, body, state="OPEN", label="work-system:task", labels=None, url=None):
    return {"number": n, "title": title, "body": body, "state": state,
            "url": url or f"https://github.com/o/r/issues/{n}",
            "labels": [{"name": l} for l in (labels if labels is not None else [label])]}


class Env:
    """A temp repo, a bin dir that holds git/bash (and gh unless told not to)."""

    def __init__(self, root, origin="git@github.com:o/r.git", with_gh=True):
        self.root = Path(root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        if origin:
            git(self.repo, "remote", "add", "origin", origin)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for tool in ("git", "bash", "sed", "date", "mkdir", "mv", "cat", "dirname", "basename",
                     "mktemp", "rm", "printf", "tr", "head", "grep"):
            p = shutil.which(tool)
            if p:
                os.symlink(p, self.bin / tool)
        self.state = self.root / "gh-state.json"
        self.log = self.root / "gh-log"
        self.state.write_text(json.dumps({"issues": []}))
        self.log.write_text("")
        if with_gh:
            g = self.bin / "gh"
            g.write_text(f"#!{sys.executable}\n" + GH_STUB)
            g.chmod(0o755)

    def setting(self, text):
        (self.repo / ".work-system.toml").write_text(text)

    def gh_state(self, **kw):
        st = json.loads(self.state.read_text())
        st.update(kw)
        self.state.write_text(json.dumps(st))

    def issues(self):
        return json.loads(self.state.read_text())["issues"]

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def run(self, *args, cwd=None):
        env = {"PATH": str(self.bin), "HOME": str(self.root),
               "GH_STUB_STATE": str(self.state), "GH_STUB_LOG": str(self.log)}
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,
                              text=True, env=env, cwd=cwd or str(self.root))


# ---- backend resolution matrix ----------------------------------------------

with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    r = e.run("backend", str(e.repo))
    check("auto + GitHub origin + gh ready → github",
          r.returncode == 0 and kv(r.stdout, "backend") == ["github"]
          and kv(r.stdout, "source") == ["auto"] and kv(r.stdout, "repo") == ["o/r"])
    check("backend reports the repo's visibility", kv(r.stdout, "visibility") == ["private"])

    (e.repo / "tasks").mkdir()
    (e.repo / "tasks" / "ROADMAP.md").write_text("# Roadmap\n")
    r = e.run("backend", str(e.repo))
    check("ROADMAP.md alone is not a task file", kv(r.stdout, "backend") == ["github"])

    (e.repo / "tasks" / "old-task.md").write_text("# Old task\n")
    r = e.run("backend", str(e.repo))
    check("auto + local task files → local with a hint on leaving it",
          kv(r.stdout, "backend") == ["local"] and "tasks.backend" in "".join(kv(r.stdout, "hint")))

    check("list relays the hint", kv(e.run("list", str(e.repo)).stdout, "hint") != [])
    (e.repo / "tasks" / "old-task.md").rename(e.repo / "tasks" / "Fix DST bug.md")
    r = e.run("backend", str(e.repo))
    check("a task file with a non-plain name still keeps auto on local",
          kv(r.stdout, "backend") == ["local"])
    r = e.run("list", str(e.repo))
    check("... and is reported, not listed",
          kv(r.stdout, "count") == ["0"] and "not listed" in r.stderr)
    (e.repo / "tasks" / "Fix DST bug.md").rename(e.repo / "tasks" / "old-task.md")
    e.setting('[tasks]\nbackend = "github"\n')
    r = e.run("backend", str(e.repo))
    check("explicit github wins over local task files",
          kv(r.stdout, "backend") == ["github"] and kv(r.stdout, "source") == ["setting"])

    e.gh_state(auth=False)
    r = e.run("backend", str(e.repo))
    check("explicit github + failing gh → exit 4, no fallback",
          r.returncode == 4 and "backend=local" not in r.stdout and "gh auth login" in r.stderr)
    r = e.run("list", str(e.repo))
    check("list on a broken github backend fails too", r.returncode == 4)

    e.setting('[tasks]\nbackend = "local"\n')
    (e.repo / "tasks" / "old-task.md").unlink()
    r = e.run("backend", str(e.repo))
    check("explicit local wins over a GitHub origin",
          kv(r.stdout, "backend") == ["local"] and kv(r.stdout, "source") == ["setting"])

    (e.repo / ".work-system.toml").unlink()
    r = e.run("backend", str(e.repo))
    check("auto + GitHub origin + installed gh that fails → exit 4", r.returncode == 4)

with tempfile.TemporaryDirectory() as d:
    e = Env(d, with_gh=False)
    r = e.run("backend", str(e.repo))
    check("auto + GitHub origin + no gh → local",
          kv(r.stdout, "backend") == ["local"] and kv(r.stdout, "reason") == ["gh is not installed"])
    e.setting('[tasks]\nbackend = "github"\n')
    check("explicit github + no gh → exit 4", e.run("backend", str(e.repo)).returncode == 4)

with tempfile.TemporaryDirectory() as d:
    e = Env(d, origin="https://gitlab.com/o/r.git")
    r = e.run("backend", str(e.repo))
    check("auto + non-GitHub origin → local", kv(r.stdout, "backend") == ["local"])
    e.setting('[tasks]\nbackend = "github"\n')
    check("explicit github without a GitHub origin → exit 4",
          e.run("backend", str(e.repo)).returncode == 4)

with tempfile.TemporaryDirectory() as d:
    e = Env(d, origin=None)
    r = e.run("backend", str(e.repo))
    check("auto + no remote → local", kv(r.stdout, "backend") == ["local"])
    check("no gh call without a GitHub origin", e.calls() == [])

with tempfile.TemporaryDirectory() as d:
    e = Env(d, origin="https://tracker.example?x@github.com/o/r")
    check("a userinfo-looking origin on another host is not GitHub",
          kv(e.run("backend", str(e.repo)).stdout, "backend") == ["local"])

for url in ("https://u@github.com/o/r.git", "https://github.com/o/r", "https://github.com/o/r.git", "ssh://git@github.com/o/r.git"):
    with tempfile.TemporaryDirectory() as d:
        e = Env(d, origin=url)
        check(f"origin form {url} is GitHub", kv(e.run("backend", str(e.repo)).stdout, "repo") == ["o/r"])

# ---- GitHub backend ----------------------------------------------------------

with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    body = e.root / "body.md"
    body.write_text("# Add dark mode\n\n## Goal\nA toggle.\n")
    r = e.run("create", str(e.repo), "add-dark-mode", "--body-file", str(body))
    iss = e.issues()
    check("create makes one labelled issue",
          r.returncode == 0 and len(iss) == 1 and iss[0]["labels"] == [{"name": "work-system:task"}])
    check("create takes the title from the heading", iss and iss[0]["title"] == "Add dark mode")
    check("create puts the name marker in the body",
          iss and "<!-- work-system task: add-dark-mode -->" in iss[0]["body"]
          and "# Add dark mode" not in iss[0]["body"])
    check("create reports the issue", kv(r.stdout, "issue") == ["1"])
    check("create ensures the label first",
          [c[:2] for c in e.calls()].index(["label", "create"])
          < [c[:2] for c in e.calls()].index(["issue", "create"]))
    r = e.run("create", str(e.repo), "add-dark-mode", "--body-file", str(body))
    check("create refuses a name an open task holds", r.returncode == 5 and len(e.issues()) == 1)
    body2 = e.root / "body2.md"
    body2.write_text("# Quote\n\nSee <!-- work-system task: add-dark-mode --> for the format.\n")
    r = e.run("create", str(e.repo), "quote-marker", "--body-file", str(body2))
    check("a marker quoted in the body does not rename the task",
          r.returncode == 0 and kv(e.run("get", str(e.repo), "quote-marker").stdout, "issue") == ["2"])
    e.gh_state(issues=[i for i in e.issues() if i["number"] != 2])
    r = e.run("create", str(e.repo), "_spike", "--body-file", str(body2))
    check("a name with a leading _ round-trips through its marker",
          r.returncode == 0 and kv(e.run("get", str(e.repo), "_spike").stdout, "task") == ["_spike"])
    e.gh_state(issues=[i for i in e.issues() if issue_name_of(i) != "_spike"])
    check("create refuses a reserved file name",
          e.run("create", str(e.repo), "ROADMAP", "--body-file", str(body)).returncode == 2)
    check("create refuses a non-plain name",
          e.run("create", str(e.repo), "x$(id)", "--body-file", str(body)).returncode == 2)

    # Ordinary issues and unmarked task issues.
    e.gh_state(issues=e.issues() + [issue(2, "Crash on start", "trace", label="bug"),
                                    issue(3, "Fix Login Flow!", "no marker")])
    r = e.run("list", str(e.repo))
    rows = kv(r.stdout, "task")
    check("list shows only labelled issues",
          rows == ["add-dark-mode\t#1\tAdd dark mode", "fix-login-flow\t#3\tFix Login Flow!"])
    check("list counts them", kv(r.stdout, "count") == ["2"])

    r = e.run("get", str(e.repo), "add-dark-mode")
    check("get by name", kv(r.stdout, "issue") == ["1"] and kv(r.stdout, "state") == ["open"])
    check("get by #N", kv(e.run("get", str(e.repo), "#3").stdout, "task") == ["fix-login-flow"])
    check("an unmarked task answers to its title slug",
          kv(e.run("get", str(e.repo), "fix-login-flow").stdout, "issue") == ["3"])
    check("a non-task issue is not a task", e.run("get", str(e.repo), "#2").returncode == 3)
    check("an unknown name exits 3", e.run("get", str(e.repo), "nope").returncode == 3)
    check("a bare number is a name, not an issue ref",
          e.run("get", str(e.repo), "3").returncode == 3)
    e.gh_state(view_error="HTTP 502: Bad Gateway")
    check("a failing gh issue view is exit 1, not 'no such task'",
          e.run("get", str(e.repo), "#3").returncode == 1)
    e.gh_state(view_error="")

    wt = e.root / "TASK.md"
    r = e.run("get", str(e.repo), "add-dark-mode", "--write", str(wt))
    text = wt.read_text()
    check("--write copies title, issue line and body",
          text.startswith("# Add dark mode\n\nissue: #1\n\n## Goal\nA toggle.")
          and "work-system task:" not in text)
    check("--body prints the same document",
          e.run("get", str(e.repo), "add-dark-mode", "--body").stdout == text)
    os.symlink(e.root / "elsewhere", e.root / "LINK.md")
    check("--write refuses a symlink",
          e.run("get", str(e.repo), "#1", "--write", str(e.root / "LINK.md")).returncode == 1)

    # A second open issue with the same marker makes the name ambiguous.
    e.gh_state(issues=e.issues() + [issue(4, "Dup", "<!-- work-system task: add-dark-mode -->")])
    check("two open issues with one name → exit 5",
          e.run("get", str(e.repo), "add-dark-mode").returncode == 5)
    e.gh_state(issues=[i for i in e.issues() if i["number"] != 4])

    r = e.run("close", str(e.repo), "add-dark-mode", "--branch", "task/add-dark-mode",
              "--pr", "12", "--sha", "abcdef1234567890")
    closed = [i for i in e.issues() if i["number"] == 1][0]
    check("close closes the issue", r.returncode == 0 and kv(r.stdout, "result") == ["closed"]
          and closed["state"] == "CLOSED")
    check("close comments the merged PR", "#12" in closed["comment"] and "abcdef123456" in closed["comment"])
    r = e.run("close", str(e.repo), "add-dark-mode", "--branch", "task/add-dark-mode", "--pr", "12")
    check("close is idempotent", r.returncode == 0 and kv(r.stdout, "result") == ["already-closed"])
    check("a closed task is still found by name",
          kv(e.run("get", str(e.repo), "add-dark-mode").stdout, "state") == ["closed"])
    r = e.run("close", str(e.repo), "#3", "--branch", "task/fix-login-flow")
    check("manual close says so", "manually" in [i for i in e.issues() if i["number"] == 3][0]["comment"])
    check("close needs --branch", e.run("close", str(e.repo), "#3").returncode == 2)

    tm = e.root / "LANE-TASK.md"
    tm.write_text("# Add dark mode\n\nissue: #1\n\nbody\n")
    r = e.run("ref", str(e.repo), "add-dark-mode", str(tm))
    check("ref pins the issue TASK.md names", kv(r.stdout, "ref") == ["#1"] and kv(r.stdout, "pinned") == ["yes"])
    r = e.run("ref", str(e.repo), "add-dark-mode", str(e.root / "gone" / "TASK.md"))
    check("ref without TASK.md is an unpinned name",
          kv(r.stdout, "ref") == ["add-dark-mode"] and kv(r.stdout, "pinned") == ["no"])
    e.gh_state(auth=False)
    r = e.run("ref", str(e.repo), "add-dark-mode", str(tm))
    check("ref reads the pin without a working gh", r.returncode == 0 and kv(r.stdout, "ref") == ["#1"])
    check("... but an unpinned name without gh is exit 4",
          e.run("ref", str(e.repo), "add-dark-mode").returncode == 4)
    e.gh_state(auth=True, old_gh=True)
    check("a gh without --active still counts as ready",
          kv(e.run("backend", str(e.repo)).stdout, "backend") == ["github"])
    e.gh_state(old_gh=False)
    r = e.run("close", str(e.repo), "#3", "--branch", "task/x", "--name", "add-dark-mode")
    check("close --name refuses a pinned issue of another task", r.returncode == 5)

    # A lane resolves the main checkout's backlog.
    git(e.repo, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-q", "--allow-empty", "-m", "i")
    git(e.repo, "worktree", "add", "-q", str(e.root / "lane"), "-b", "task/x")
    check("a lane lists the same backlog",
          kv(e.run("list", str(e.root / "lane")).stdout, "count") == ["0"]
          and kv(e.run("backend", str(e.root / "lane")).stdout, "repo") == ["o/r"])

# ---- local backend -----------------------------------------------------------

with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    e.setting('[tasks]\nbackend = "local"\n')
    git(e.repo, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-q", "--allow-empty", "-m", "i")
    body = e.root / "body.md"
    body.write_text("## Goal\nSomething.\n")
    r = e.run("create", str(e.repo), "local-one", "--body-file", str(body), "--title", "Local one")
    f = e.repo / "tasks" / "local-one.md"
    check("local create writes the file", r.returncode == 0 and f.read_text().startswith("# Local one\n"))
    check("local create refuses an existing file",
          e.run("create", str(e.repo), "local-one", "--body-file", str(body)).returncode == 5)
    check("local list", kv(e.run("list", str(e.repo)).stdout, "task")
          == ["local-one\ttasks/local-one.md\tLocal one"])
    wt = e.root / "TASK.md"
    e.run("get", str(e.repo), "local-one", "--write", str(wt))
    check("local --write copies the file", wt.read_text() == f.read_text())
    wt.write_text("# Local one\n\nissue: #12\n")
    r = e.run("ref", str(e.repo), "local-one", str(wt))
    check("on the local backend an issue: line is not a ref",
          kv(r.stdout, "ref") == ["local-one"] and kv(r.stdout, "pinned") == ["yes"])
    r = e.run("close", str(e.repo), "local-one", "--branch", "task/local-one", "--pr", "5")
    check("local close hands over to archive-task.sh",
          r.returncode == 0 and kv(r.stdout, "backend") == ["local"]
          and kv(r.stdout, "archived_path") == ["tasks/archive/local-one.md"] and not f.exists())
    check("local close of a missing file exits 3",
          e.run("close", str(e.repo), "local-one", "--branch", "task/local-one").returncode == 3)
    check("the local backend never calls gh",
          not any(c[:1] == ["issue"] for c in e.calls()))

    (e.repo / "tasks" / "kept.md").write_text("# Kept\n")
    e.setting('[tasks]\nbackend = "bogus"\n')
    r = e.run("get", str(e.repo), "kept", "--body")
    check("a settings note never reaches a task body",
          r.stdout == "# Kept\n" and "note=" not in r.stdout and "note=" in r.stderr)
    (e.repo / "tasks" / "$(touch pwned).md").write_text("# Evil\n")
    check("a file name that is not a plain task name is not listed",
          kv(e.run("list", str(e.repo)).stdout, "task") == ["kept\ttasks/kept.md\tKept"])

with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    e.gh_state(issues=[issue(n, f"T{n}", f"<!-- work-system task: t{n} -->") for n in range(1, 1001)])
    r = e.run("get", str(e.repo), "t5")
    check("a backlog list at the cap fails instead of missing tasks",
          r.returncode == 1 and "#N" in r.stderr)

# ---- migrate: local files → issues --------------------------------------------

def migrate_env(d):
    e = Env(d)
    git(e.repo, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-q", "--allow-empty", "-m", "i")
    t = e.repo / "tasks"
    (t / "archive").mkdir(parents=True)
    (t / "ROADMAP.md").write_text("# Roadmap\n")
    (t / "add-search.md").write_text("# Add search\n\n## Goal\nFind things.\n")
    (t / "fix-crash.md").write_text("# Fix crash\n\n## Goal\nNo crash.\n")
    (t / "leaky.md").write_text("# Leaky\n\nexport API_TOKEN=xxxxxxxxxxxx\n")
    (t / "archive" / "_index.md").write_text("# Archived tasks\n")
    (t / "archive" / "old-thing.md").write_text(
        "> Archived 2026-08-11 · PR #52 (merged @ 5cee34a) · task/old-thing\n\n# Old thing\n\nDone.\n")
    # An archived task may share its name with an open one.
    (t / "archive" / "add-search.md").write_text(
        "> Archived 2026-07-01 · closed manually (no merged PR) · task/add-search\n\n# Add search v1\n")
    return e


def migrate(e, *args):
    return e.run("migrate", str(e.repo), "--pace", "0", *args)


with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    r = migrate(e)
    items = kv(r.stdout, "item")
    check("migrate dry run lists every task file with its action",
          r.returncode == 0 and sorted(items) == sorted([
              "create\topen/add-search\tnew\tAdd search", "create\topen/fix-crash\tnew\tFix crash",
              "skip\topen/leaky\tnew\t(held back)", "create\tarchived/old-thing\tnew\tOld thing",
              "create\tarchived/add-search\tnew\tAdd search v1"]))
    check("dry run names the repo and its visibility",
          kv(r.stdout, "repo") == ["o/r"] and kv(r.stdout, "visibility") == ["private"])
    check("dry run counts what it would publish", kv(r.stdout, "publish") == ["4"])
    check("the secret scan names the hit, never the value",
          kv(r.stdout, "hit") == ["open/leaky\t3\tenv-assignment"] and "xxxxxxxxxxxx" not in r.stdout)
    check("ROADMAP.md and the archive index are not tasks",
          not any("ROADMAP" in i or "_index" in i for i in items))
    check("a dry run writes nothing",
          e.issues() == [] and not any(c[:2] in (["issue", "create"], ["label", "create"])
                                       for c in e.calls()))
    token = kv(r.stdout, "confirm")[0]

    check("apply refuses a token that is not the plan's",
          migrate(e, "--apply", "000000000000").returncode == 5 and e.issues() == [])
    (e.repo / "tasks" / "fix-crash.md").write_text("# Fix crash\n\nChanged after the dry run.\n")
    check("apply refuses a plan that changed since the dry run",
          migrate(e, "--apply", token).returncode == 5 and e.issues() == [])
    check("--include of an unknown task is a usage error",
          migrate(e, "--include", "nope").returncode == 2)

    r = migrate(e, "--include", "open/leaky")
    check("--include publishes a file the scan held back",
          "create\topen/leaky\tnew\tLeaky" in kv(r.stdout, "item"))
    r = migrate(e)
    r = migrate(e, "--apply", kv(r.stdout, "confirm")[0])
    iss = {issue_name_of(i) + ("@archived" if any(l["name"] == "work-system:archived"
                                                   for l in i["labels"]) else ""): i
           for i in e.issues()}
    check("apply creates one issue per publishable file",
          r.returncode == 0 and sorted(iss) == ["add-search", "add-search@archived", "fix-crash",
                                                "old-thing@archived"])
    check("an open task becomes an open labelled issue titled by its H1",
          iss["fix-crash"]["state"] == "OPEN" and iss["fix-crash"]["title"] == "Fix crash"
          and [l["name"] for l in iss["fix-crash"]["labels"]] == ["work-system:task"]
          and "# Fix crash" not in iss["fix-crash"]["body"])
    old = iss["old-thing@archived"]
    check("an archived task becomes a closed issue (completed) that keeps its stamp",
          old["state"] == "CLOSED" and old.get("reason") == "completed"
          and old["body"].startswith("> Archived 2026-08-11")
          and [l["name"] for l in old["labels"]] == ["work-system:task", "work-system:archived"])
    m = e.repo / "tasks" / "migrated"
    check("files move to tasks/migrated/, not deleted",
          (m / "fix-crash.md").exists() and (m / "archive" / "old-thing.md").exists()
          and (m / "archive" / "add-search.md").exists() and (m / "add-search.md").exists()
          and not (e.repo / "tasks" / "fix-crash.md").exists())
    check("the held-back file stays and ROADMAP.md is untouched",
          (e.repo / "tasks" / "leaky.md").exists() and (e.repo / "tasks" / "ROADMAP.md").exists())
    mapping = (m / "issues.tsv").read_text()
    check("the map records task ↔ issue",
          f"open\tfix-crash\t#{iss['fix-crash']['number']}\ttasks/fix-crash.md\n" in mapping
          and "archived\told-thing\t#" in mapping)
    check("with a file left the backend is not switched",
          kv(r.stdout, "backend_switched") == ["no"] and not (e.repo / ".work-system.toml").exists())

    # Re-run: nothing is duplicated; including the held-back file finishes the job.
    (e.repo / ".work-system.toml").write_text('# mine\n[paths]\ntasks_dir = "tasks"\n')
    r = migrate(e, "--include", "leaky")
    check("a re-run plans only what is left",
          kv(r.stdout, "item") == ["create\topen/leaky\tnew\tLeaky"])
    r = migrate(e, "--include", "leaky", "--apply", kv(r.stdout, "confirm")[0])
    check("re-run creates no duplicates", r.returncode == 0 and len(e.issues()) == 5)
    check("an empty tasks/ switches the backend, keeping the config's other lines",
          kv(r.stdout, "backend_switched") == ["yes"]
          and kv(r.stdout, "config_changed") == [".work-system.toml"]
          and (e.repo / ".work-system.toml").read_text()
          == '# mine\n[paths]\ntasks_dir = "tasks"\n\n[tasks]\nbackend = "github"\n')
    check("the store now reads the backlog from issues",
          kv(e.run("backend", str(e.repo)).stdout, "backend") == ["github"]
          and kv(e.run("list", str(e.repo)).stdout, "count") == ["3"])
    check("an untracked tasks/ needs no commit", kv(r.stdout, "commit_needed") == [])
    check("the open name resolves to the open issue, not the archived one",
          kv(e.run("get", str(e.repo), "add-search").stdout, "issue")
          == [str(iss["add-search"]["number"])])
    r = migrate(e)
    check("a finished migration plans nothing", kv(r.stdout, "item") == [] and r.returncode == 0)

    # An in-flight lane (local-format TASK.md, no issue: line) still closes.
    tm = e.root / "TASK.md"
    tm.write_text("# Fix crash\n\n## Goal\nNo crash.\n")
    r = e.run("ref", str(e.repo), "fix-crash", str(tm))
    check("an in-flight lane resolves its task by name after migration",
          kv(r.stdout, "ref") == ["fix-crash"] and kv(r.stdout, "pinned") == ["no"])
    r = e.run("close", str(e.repo), "fix-crash", "--branch", "task/fix-crash", "--pr", "9")
    check("... and closes the migrated issue", kv(r.stdout, "result") == ["closed"]
          and kv(r.stdout, "issue") == [str(iss["fix-crash"]["number"])])

# Resume after a stop: a rate limit mid-run, and a create whose reply was lost.
with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    (e.repo / "tasks" / "leaky.md").unlink()
    # Three creates succeed; closing the archived add-search hits the limit.
    e.gh_state(rate_limit_after=3)
    r = migrate(e)
    r = migrate(e, "--apply", kv(r.stdout, "confirm")[0])
    check("a rate limit stops the run cleanly with exit 6 and a resume hint",
          r.returncode == 6 and "resume" in r.stderr and len(kv(r.stdout, "done")) == 2)
    check("... after three issues were written", len(e.issues()) == 3)
    e.gh_state(rate_limit_after=None)
    r = migrate(e)
    check("the next dry run resumes the half-done item",
          kv(r.stdout, "item") == ["resume\tarchived/add-search\t#3\tAdd search v1",
                                   "create\tarchived/old-thing\tnew\tOld thing"]
          and kv(r.stdout, "publish") == ["1"])
    e.gh_state(create_lost_reply=True)
    r = migrate(e, "--apply", kv(r.stdout, "confirm")[0])
    check("a create whose reply is lost fails the run", r.returncode == 1)
    e.gh_state(create_lost_reply=False)
    r = migrate(e)
    r = migrate(e, "--apply", kv(r.stdout, "confirm")[0])
    names = sorted(issue_name_of(i) for i in e.issues())
    check("resuming never duplicates an issue",
          r.returncode == 0 and names == ["add-search", "add-search", "fix-crash", "old-thing"])
    check("every archived issue ends closed",
          all(i["state"] == "CLOSED" for i in e.issues()
              if any(l["name"] == "work-system:archived" for l in i["labels"])))
    check("resume finishes with the backend switched", kv(r.stdout, "backend_switched") == ["yes"])

# Adopting an existing tracking issue; --open-only; tracked tasks/; public repo.
with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    (e.repo / "tasks" / "leaky.md").unlink()
    (e.repo / "tasks" / "fix-crash.md").write_text(
        "# Fix crash\n\n> Tracking issue: o/r#108\n\n## Goal\nNo crash.\n")
    (e.repo / "tasks" / "add-search.md").write_text(
        "# Add search\n\n> Tracking issue: #109\n\nGoal.\n")
    (e.repo / "tasks" / "other-repo.md").write_text(
        "# Other repo\n\n> Tracking issue: someone/else#7\n")
    e.gh_state(issues=[issue(108, "Crash on launch", "Seen on Android 14.", label="bug"),
                       issue(109, "Search", "", state="CLOSED", label="enhancement")])
    git(e.repo, "add", "tasks")
    git(e.repo, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-q", "-m", "tasks")
    r = migrate(e, "--open-only")
    items = sorted(kv(r.stdout, "item"))
    check("a tracking issue is adopted, shown as its own case",
          "adopt\topen/fix-crash\t#108\tFix crash" in items)
    check("an adopt whose file says more than the issue is flagged as drift",
          "open/fix-crash\t#108" in kv(r.stdout, "differs") and kv(r.stdout, "count_differs") == ["1"])
    check("a closed tracking issue is not adopted for an open task",
          "skip\topen/add-search\t#109\tAdd search" in items
          and any(s.startswith("open/add-search\t") and "closed" in s for s in kv(r.stdout, "skip")))
    check("a tracking line for another repo creates a new issue",
          "create\topen/other-repo\tnew\tOther repo" in items)
    check("--open-only leaves the archive out", not any("archived/" in i for i in items))
    check("tracked task files are reported", kv(r.stdout, "tasks_tracked") == ["yes"])
    r = migrate(e, "--open-only", "--apply", kv(r.stdout, "confirm")[0])
    i108 = [i for i in e.issues() if i["number"] == 108][0]
    check("adopt adds the label and the marker without a new issue",
          r.returncode == 0 and {l["name"] for l in i108["labels"]} == {"bug", "work-system:task"}
          and issue_name_of(i108) == "fix-crash" and len(e.issues()) == 3)
    check("adopt keeps the issue's own text and adds only the marker",
          i108["body"] == "Seen on Android 14.\n\n<!-- work-system task: fix-crash -->\n"
          and i108["title"] == "Crash on launch")
    check("with tracked task files the moves need a commit (never made by the tool)",
          kv(r.stdout, "commit_needed") == ["tasks"]
          and git(e.repo, "log", "--oneline").stdout.count("\n") == 2)
    r = migrate(e, "--open-only")
    check("an adopted issue is not adopted twice",
          not any(i.startswith("adopt\t") for i in kv(r.stdout, "item")))

with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    (e.repo / "tasks" / "paths.md").write_text("# Paths\n\nSee /Users/someone/notes and app.internal.\n"
                                               "Config: .claude/settings.local.json\n")
    r = migrate(e)
    private_token = kv(r.stdout, "confirm")
    check("a private repo allows local paths but not private hosts",
          [h for h in kv(r.stdout, "hit") if h.startswith("open/paths")] == ["open/paths\t3\tprivate-host"])
    e.gh_state(visibility="PUBLIC")
    r = migrate(e)
    check("a public repo also holds back local paths",
          [h for h in kv(r.stdout, "hit") if h.startswith("open/paths")]
          == ["open/paths\t3\tlocal-path", "open/paths\t3\tprivate-host"])
    check("visibility changes the token",
          kv(r.stdout, "visibility") == ["public"] and kv(r.stdout, "confirm") != private_token)

# What a dry run must refuse to plan.
with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    t = e.repo / "tasks"
    (t / "leaky.md").unlink()
    secret = e.root / "dotenv"
    secret.write_text("# Not a task\nDB=hunter2\n")
    os.symlink(secret, t / "linked.md")
    (t / "a.md").write_text("# A\n\n> Tracking issue: #12\n")
    (t / "b.md").write_text("# B\n\nTracking issue: o/r#12\n")
    e.gh_state(issues=[issue(12, "Shared", "", label="bug"),
                       issue(20, "Fix crash", "Other text.\n\n<!-- work-system task: fix-crash -->"),
                       issue(21, "Add search", "## Goal\nFind things.\n\n"
                             "<!-- work-system task: add-search -->", state="CLOSED")])
    e.setting('tasks.backend = "local"\n')
    r = migrate(e)
    skips = dict(x.split("\t", 1) for x in kv(r.stdout, "skip"))
    check("a symlinked task file is never published", "symlink" in skips.get("open/linked", ""))
    check("two files naming one tracking issue adopt neither",
          "several" in skips.get("open/a", "") and "several" in skips.get("open/b", ""))
    check("an issue with the marker but other text is resumed and flagged",
          "resume\topen/fix-crash\t#20\tFix crash" in kv(r.stdout, "item")
          and kv(r.stdout, "differs") == ["open/fix-crash\t#20"])
    check("an open task whose issue is closed is not resumed",
          "closed" in skips.get("open/add-search", ""))
    check("a config the line edit cannot handle is reported in the dry run",
          any(x.startswith("manual\t") for x in kv(r.stdout, "config_edit")))
    e.gh_state(issues=[i for i in e.issues() if i["number"] != 12]
               + [issue(12, "Shared", "edited later", label="bug")])
    r2 = migrate(e)
    check("an adopted issue's text is part of the plan token",
          kv(r2.stdout, "confirm") != kv(r.stdout, "confirm"))

# Round-2 guards: archived identity, archived leftovers, consistent index, symlinks.
with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    t = e.repo / "tasks"
    for f in ("leaky.md", "fix-crash.md"):
        (t / f).unlink()
    (t / "archive" / "add-search.md").write_text("# Add search v1\n\nTracking issue: #12\n")
    e.gh_state(issues=[issue(12, "Add search", "## Goal\nFind things.\n\n"
                             "<!-- work-system task: add-search -->")])
    r = migrate(e)
    skips = dict(x.split("\t", 1) for x in kv(r.stdout, "skip"))
    check("an archived file never adopts its open twin's issue",
          "open task add-search" in skips.get("archived/add-search", "")
          and "resume\topen/add-search\t#12\tAdd search" in kv(r.stdout, "item"))
    check("the migrate index reads the REST issues endpoint, not the search API",
          any(c[:1] == ["api"] for c in e.calls())
          and not any(c[:2] == ["issue", "list"] and "all" in c for c in e.calls()))
    (t / "notes.md").write_text('# Notes\n\n{"password": "correcthorse99"}\nglpat-' + "x" * 20 + "\n")
    hits = [h for h in kv(migrate(e).stdout, "hit") if h.startswith("open/notes")]
    check("quoted JSON passwords and GitLab tokens are scan hits",
          hits == ["open/notes\t3\tsecret-assignment", "open/notes\t4\tapi-key"])
    (t / "migrated").mkdir()
    os.symlink(e.root, t / "migrated" / "archive")
    check("a symlinked tasks/migrated/archive is refused (exit 1, not the re-plan code 5)",
          migrate(e).returncode == 1)

with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    e.gh_state(issues=[issue(1, "Add search", "<!-- work-system task: add-search -->"),
                       dict(issue(2, "Add search v1", "<!-- work-system task: add-search -->"),
                            labels=[{"name": "work-system:task"}, {"name": "work-system:archived"}])])
    check("an archived issue a stopped run left open is not a live task",
          kv(e.run("list", str(e.repo)).stdout, "count") == ["1"]
          and kv(e.run("get", str(e.repo), "add-search").stdout, "issue") == ["1"])
    e.gh_state(issues=[i for i in e.issues() if i["number"] == 2], labels=["work-system:task"])
    body = e.root / "b.md"
    body.write_text("# Add search\n")
    check("... nor does it block creating that name",
          e.run("create", str(e.repo), "add-search", "--body-file", str(body)).returncode == 0)
    r = e.run("ref", str(e.repo), "feature/add-search")
    check("a slash name from an adopted branch resolves to its flattened issue name",
          kv(r.stdout, "ref") == ["feature-add-search"])

# Round-3 guards: pull requests, stale archived labels, wider scan, --include.
with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    t = e.repo / "tasks"
    (t / "leaky.md").unlink()
    (t / "fix-crash.md").write_text("# Fix crash\n\nTracking issue: #30\n")
    (t / "add-search.md").write_text("# Add search\n\nTracking issue: #31\n")
    (t / "creds.md").write_text("# Creds\n\ndb_password = correcthorse9\n")
    e.gh_state(issues=[dict(issue(30, "A PR", "", label="bug"), url="https://github.com/o/r/pull/30"),
                       issue(31, "Old", "", label="work-system:archived")])
    r = migrate(e)
    skips = dict(x.split("\t", 1) for x in kv(r.stdout, "skip"))
    check("a tracking line naming a pull request is not adopted",
          "pull request" in skips.get("open/fix-crash", ""))
    check("an open task never adopts an issue labelled archived",
          "archived task" in skips.get("open/add-search", ""))
    check("snake_case secret names are scan hits",
          "open/creds\t3\tsecret-assignment" in kv(r.stdout, "hit"))
    check("a bare --include naming an open and an archived task is refused",
          migrate(e, "--include", "add-search").returncode == 2)

# ---- issue selection: tasks.select / include / exclude / gate labels ---------

def labelled(n, title, body, *labels, state="OPEN", url=None):
    return issue(n, title, body, state=state, labels=list(labels), url=url)


with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    git(e.repo, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-q", "--allow-empty", "-m", "i")
    trello = "DEFINITION ONLY\n\nhttps://trello.com/c/abc\n"
    e.gh_state(issues=[
        labelled(1, "Marked task", "<!-- work-system task: marked -->", "work-system:task"),
        labelled(2, "Müll-Tour planen", trello, "Definition-Only"),
        labelled(3, "Bump lodash", "bot", "dependencies"),
        labelled(4, "Fix login", "steps", "bug", "work-system:task", "dependencies"),
        labelled(5, "Old", "x", "work-system:task", "work-system:archived"),
        labelled(6, "A PR", "x", "bug", url="https://github.com/o/r/pull/6"),
        labelled(7, "Closed one", "x", "bug", state="CLOSED")])
    rows = lambda r: [x.split("\t")[1] for x in kv(r.stdout, "task")]

    r = e.run("list", str(e.repo))
    check("default select is the label, as before",
          kv(r.stdout, "select") == ["label"] and rows(r) == ["#1", "#4"])
    check("default: an unlabelled issue is not a task", e.run("get", str(e.repo), "#2").returncode == 3)

    e.setting('[tasks]\nselect = "all-open"\n')
    r = e.run("list", str(e.repo))
    check("all-open lists every open issue but archived ones",
          kv(r.stdout, "select") == ["all-open"] and rows(r) == ["#1", "#2", "#3", "#4"])
    check("all-open lists without a gh label filter",
          not any(c[:2] == ["issue", "list"] and "--label" in c for c in e.calls()[-2:]))
    check("a marker-less title slugs umlauts readably",
          "muell-tour-planen\t#2\tMüll-Tour planen" in kv(r.stdout, "task"))
    check("list marks a gated task", kv(r.stdout, "gated") == ["muell-tour-planen\t#2\tdefinition-only"])
    check("a pull request is never a task", e.run("get", str(e.repo), "#6").returncode == 3)

    e.setting('[tasks]\nselect = "all-open"\nexclude_labels = ["dependencies"]\n')
    r = e.run("list", str(e.repo))
    check("exclude_labels drops an issue, even one with the task label", rows(r) == ["#1", "#2"])
    check("... and get by #N agrees", e.run("get", str(e.repo), "#4").returncode == 3)

    e.setting('[tasks]\nselect = "all-open"\ninclude_labels = ["bug", "definition-only"]\n')
    check("include_labels keeps only issues with one of them, a body marker does not bypass it",
          rows(e.run("list", str(e.repo))) == ["#2", "#4"])
    e.gh_state(labels=["work-system:task", "bug"])
    body = e.root / "new.md"
    body.write_text("# New thing\n")
    r = e.run("create", str(e.repo), "new-thing", "--body-file", str(body))
    check("create adds the first include label, so its task is listed",
          r.returncode == 0 and "new-thing" in [x.split("\t")[0] for x in kv(e.run("list", str(e.repo)).stdout, "task")]
          and any(c[:2] == ["issue", "create"] and "bug" in c for c in e.calls()))
    e.gh_state(issues=[i for i in e.issues() if issue_name_of(i) != "new-thing"])
    e.setting('[tasks]\nselect = "all-open"\nexclude_labels = ["dependencies"]\n')
    r = e.run("create", str(e.repo), "bump-lodash", "--body-file", str(body))
    check("create counts an excluded issue's name as taken, and names it",
          r.returncode == 5 and "#3" in r.stderr)
    e.setting('[tasks]\nexclude_labels = ["work-system:task"]\n')
    r = e.run("create", str(e.repo), "new-thing", "--body-file", str(body))
    check("create refuses a task the filter would hide (a config error, exit 2)",
          r.returncode == 2 and "[tasks]" in r.stderr
          and not any(issue_name_of(i) == "new-thing" for i in e.issues()))

    e.setting('[tasks]\nselect = "everything"\n')
    r = e.run("list", str(e.repo))
    check("an invalid select falls back to the label default with a note",
          rows(r) == ["#1", "#4"] and "tasks.select" in r.stderr)

    # A marker-less, gated issue through kickoff, close and the idempotent re-close.
    e.setting('[tasks]\nselect = "all-open"\n')
    r = e.run("get", str(e.repo), "muell-tour-planen")
    check("get names the gate", r.returncode == 0 and kv(r.stdout, "gate") == ["definition-only"]
          and kv(r.stdout, "issue") == ["2"])
    wt = e.root / "lane-TASK.md"
    r = e.run("get", str(e.repo), "#2", "--write", str(wt))
    check("the kickoff copy of a gated issue is refused (exit 7)",
          r.returncode == 7 and not wt.exists() and "definition-only" in r.stderr)
    check("--allow-gated needs --write", e.run("get", str(e.repo), "#2", "--allow-gated").returncode == 2)
    r = e.run("get", str(e.repo), "#2", "--write", str(wt), "--allow-gated")
    check("--allow-gated writes the copy with the issue pin",
          r.returncode == 0 and wt.read_text() == "# Müll-Tour planen\n\nissue: #2\n\n" + trello)
    r = e.run("ref", str(e.repo), "muell-tour-planen", str(wt))
    check("the marker-less task is pinned by its issue", kv(r.stdout, "ref") == ["#2"])
    e.setting('[tasks]\nselect = "all-open"\nexclude_labels = ["definition-only"]\n')
    r = e.run("close", str(e.repo), "#2", "--branch", "task/muell-tour-planen", "--pr", "3",
              "--name", "muell-tour-planen")
    i2 = [i for i in e.issues() if i["number"] == 2][0]
    check("close of the pinned issue works even after the filter changed",
          r.returncode == 0 and kv(r.stdout, "result") == ["closed"] and i2["state"] == "CLOSED")
    check("nobody's issue body was rewritten",
          i2["body"] == trello and not any(c[:2] == ["issue", "edit"] for c in e.calls()))
    r = e.run("close", str(e.repo), "#2", "--branch", "task/muell-tour-planen", "--pr", "3",
              "--name", "muell-tour-planen")
    check("re-close of the marker-less task is idempotent", kv(r.stdout, "result") == ["already-closed"])
    i4 = [i for i in e.issues() if i["number"] == 4][0]
    e.gh_state(issues=[i for i in e.issues() if i["number"] != 4] + [dict(i4, title="Login repair")])
    r = e.run("close", str(e.repo), "#4", "--branch", "task/fix-login", "--name", "fix-login")
    check("a retitled marker-less pin is a conflict to confirm (5), never 'nothing to close'",
          r.returncode == 5 and "retitled" in r.stderr)
    e.setting('[tasks]\nselect = "all-open"\nexclude_labels = ["dependencies"]\n')
    r = e.run("close", str(e.repo), "#4", "--branch", "task/fix-login")
    check("the user-confirmed #N closes even when the filter now drops it",
          r.returncode == 0 and kv(r.stdout, "result") == ["closed"])
    check("close by #N still refuses a pull request",
          e.run("close", str(e.repo), "#6", "--branch", "task/x").returncode == 3)
    e.setting('[tasks]\nexclude_labels = ["dependencies"]\n')
    check("in label mode close by #N refuses an issue without the task label",
          e.run("close", str(e.repo), "#3", "--branch", "task/x").returncode == 3)
    i1 = [i for i in e.issues() if i["number"] == 1][0]
    e.gh_state(issues=[i for i in e.issues() if i["number"] != 1]
               + [dict(i1, labels=i1["labels"] + [{"name": "dependencies"}])])
    r = e.run("close", str(e.repo), "marked", "--branch", "task/marked")
    check("an unpinned close by name ignores an exclude label added mid-lane",
          r.returncode == 0 and kv(r.stdout, "issue") == ["1"])

with tempfile.TemporaryDirectory() as d:
    e = Env(d, origin="git@github.com:acme/pull.git")
    e.gh_state(issues=[issue(1, "One", "x", url="https://github.com/acme/pull/issues/1")])
    check("a repo named pull still has issues, not pull requests",
          kv(e.run("list", str(e.repo)).stdout, "count") == ["1"])

# migrate under all-open: an unlabelled open issue already answers to the name.
with tempfile.TemporaryDirectory() as d:
    e = migrate_env(d)
    (e.repo / "tasks" / "leaky.md").unlink()
    e.setting('[tasks]\nbackend = "local"\nselect = "all-open"\n')
    e.gh_state(issues=[issue(3, "Fix crash", "reported by a user", label="bug")])
    r = migrate(e, "--open-only")
    skips = dict(x.split("\t", 1) for x in kv(r.stdout, "skip"))
    check("migrate under all-open does not duplicate an unlabelled issue's name",
          "already answers to fix-crash" in skips.get("open/fix-crash", ""))
    (e.repo / "tasks" / "add-search.md").write_text("# Add search\n\nTracking issue: #4\n")
    e.gh_state(issues=e.issues() + [issue(4, "Search", "the reporter's text", label="enhancement")])
    r = migrate(e, "--open-only")
    e.setting('[tasks]\nbackend = "local"\nselect = "all-open"\nexclude_labels = ["enhancement"]\n')
    r2 = migrate(e, "--open-only")
    check("migrate never adopts an issue the filter would hide",
          "exclude_labels" in dict(x.split("\t", 1) for x in kv(r2.stdout, "skip")).get("open/add-search", ""))
    check("an adopt whose file holds only its title and tracking line is not drift",
          "adopt\topen/add-search\t#4\tAdd search" in kv(r.stdout, "item")
          and not any(x.startswith("open/add-search") for x in kv(r.stdout, "differs")))

if FAILS:
    print("FAIL test_task_store.py:")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("ok test_task_store.py")
