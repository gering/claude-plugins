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
    sys.exit(0 if st.get("auth", True) else 1)
if cmd == ["repo", "view"]:
    if "-R" in args:  # the real gh: unknown shorthand flag
        print("unknown shorthand flag: 'R' in -R", file=sys.stderr); sys.exit(1)
    print(json.dumps({"visibility": st.get("visibility", "PRIVATE")})); sys.exit(0)
if cmd == ["label", "create"]:
    st.setdefault("labels", []); st["labels"].append(args[2]); save(); sys.exit(0)
issues = st.setdefault("issues", [])
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
if cmd == ["issue", "create"]:
    if opt("--label") not in st.get("labels", []):
        print("could not add label", file=sys.stderr); sys.exit(1)
    n = max([i["number"] for i in issues] + [0]) + 1
    body = sys.stdin.read() if opt("--body-file") == "-" else ""
    issues.append({"number": n, "title": opt("--title"), "body": body, "state": "OPEN",
                   "url": f"https://github.com/o/r/issues/{n}",
                   "labels": [{"name": opt("--label")}]})
    save(); print(f"https://github.com/o/r/issues/{n}"); sys.exit(0)
if cmd == ["issue", "close"]:
    n = int(args[2])
    for i in issues:
        if i["number"] == n:
            i["state"] = "CLOSED"; i["comment"] = opt("--comment")
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


def issue(n, title, body, state="OPEN", label="work-system:task"):
    return {"number": n, "title": title, "body": body, "state": state,
            "url": f"https://github.com/o/r/issues/{n}", "labels": [{"name": label}]}


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

for url in ("https://github.com/o/r", "https://github.com/o/r.git", "ssh://git@github.com/o/r.git"):
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
          e.run("get", str(e.repo), "#1", "--write", str(e.root / "LINK.md")).returncode == 2)

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

if FAILS:
    print("FAIL test_task_store.py:")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("ok test_task_store.py")
