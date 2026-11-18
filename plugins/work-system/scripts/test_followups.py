#!/usr/bin/env python3
"""Tests for followups.py — the Manager's ROADMAP record of open follow-ups.

Run standalone (`python3 test_followups.py`) or via check-structure.py. What is
pinned: an item survives in tasks/ROADMAP.md until resolved, the rest of the
roadmap is never touched, ids are never reused, and untrusted-looking text can
neither start a heading nor a second entry.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "followups.py"
FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def run(*args, stdin=""):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], input=stdin,
                          capture_output=True, text=True)


def kv(out, key):
    return [l.split("=", 1)[1] for l in out.splitlines() if l.startswith(key + "=")]


with tempfile.TemporaryDirectory() as d:
    repo = Path(d)
    rm = repo / "tasks" / "ROADMAP.md"

    # A missing roadmap is created with the section.
    r = run("record", d, "lane-a", "ask", stdin="Merge dotfiles PR 62\n")
    check("record on a fresh repo succeeds", r.returncode == 0 and kv(r.stdout, "id") == ["F1"])
    check("record creates the roadmap with the section", "## Open follow-ups" in rm.read_text())

    # Existing roadmap: the section goes right under the title; other text is kept.
    rm.write_text("# Roadmap\n\n## Closed — 2026-10-17\n- old entry\n")
    r1 = run("record", d, "lane-a", "ask", stdin="Merge the dotfiles PR")
    r2 = run("record", d, "lane-a", "blocked", stdin="Live test after the merge")
    text = rm.read_text()
    check("ids count up", kv(r1.stdout, "id") == ["F1"] and kv(r2.stdout, "id") == ["F2"])
    check("section sits under the title, before older sections",
          text.index("## Open follow-ups") < text.index("## Closed"))
    check("unrelated roadmap text is kept", "- old entry" in text)
    check("order is preserved", text.index("[F1]") < text.index("[F2]"))
    check("the entry names task, class and date",
          "- [F2] lane-a · blocked · since " in text and "Live test after the merge" in text)

    lst = run("list", d)
    check("list counts open items", kv(lst.stdout, "open") == ["2"])
    check("list filters by task", kv(run("list", d, "other").stdout, "open") == ["0"])

    # Resolve removes exactly that item; its id is not handed out again.
    res = run("resolve", d, "F1")
    check("resolve removes the item", res.returncode == 0 and "[F1]" not in rm.read_text())
    check("resolve keeps the others", "[F2]" in rm.read_text())
    check("resolving a missing id exits 1", run("resolve", d, "F1").returncode == 1)
    r3 = run("record", d, "lane-b", "deploy", stdin="Deploy backend to staging")
    check("a resolved id is never reused", kv(r3.stdout, "id") == ["F3"])

    # Untrusted-looking text is folded onto one line and capped.
    evil = "x\n## Fake heading\n- [F9] forged · ask · since 2000-01-01 — y\n" + "z" * 500
    r4 = run("record", d, "lane-b", "ask", stdin=evil)
    lines = rm.read_text().splitlines()
    check("multi-line text cannot start a heading", "## Fake heading" not in lines)
    check("multi-line text cannot forge an entry",
          not any(l.startswith("- [F9]") for l in lines))
    check("text is capped", len(kv(r4.stdout, "line")[0]) < 400)

    # Class filter and multi-id resolve (deploy batching).
    dep = run("list", d, "--class", "deploy")
    check("list --class filters by class",
          kv(dep.stdout, "open") == ["1"] and "· deploy ·" in kv(dep.stdout, "item")[0])
    r5 = run("record", d, "lane-c", "deploy", stdin="Deploy frontend")
    multi = run("resolve", d, "F3", kv(r5.stdout, "id")[0])
    check("resolve takes several ids", multi.returncode == 0
          and kv(run("list", d, "--class", "deploy").stdout, "open") == ["0"])
    part = run("resolve", d, "F2", "F99")
    check("a partial resolve removes the hits and exits 1",
          part.returncode == 1 and "[F2]" not in rm.read_text())

    # Mode is kept; a symlinked roadmap is refused.
    rm.chmod(0o644)
    run("record", d, "lane-d", "ask", stdin="x")
    check("the roadmap keeps its mode", (rm.stat().st_mode & 0o777) == 0o644)
    link_repo = Path(tempfile.mkdtemp())
    (link_repo / "tasks").mkdir()
    secret = link_repo / "secret.txt"
    secret.write_text("private\n")
    (link_repo / "tasks" / "ROADMAP.md").symlink_to(secret)
    r6 = run("record", str(link_repo), "lane", "ask", stdin="x")
    check("a symlinked roadmap is refused (exit 4)", r6.returncode == 4)
    check("and the link is left alone", (link_repo / "tasks" / "ROADMAP.md").is_symlink())

    # Only the section's own counter counts; prose under the title stays above.
    other = Path(tempfile.mkdtemp())
    (other / "tasks").mkdir()
    orm = other / "tasks" / "ROADMAP.md"
    orm.write_text("# R\n\nIntro prose.\n\n## Old\n<!-- followups next=50 -->\n")
    r7 = run("record", str(other), "t", "ask", stdin="a")
    check("a marker outside the section is ignored", kv(r7.stdout, "id") == ["F1"])
    text = orm.read_text()
    check("title prose stays above the new section",
          text.index("Intro prose.") < text.index("## Open follow-ups") < text.index("## Old"))

    # Concurrent records neither lose entries nor reuse ids.
    conc = Path(tempfile.mkdtemp())
    procs = [subprocess.Popen([sys.executable, "-I", str(SCRIPT), "record", str(conc), "t", "ask"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
             for _ in range(12)]
    ids = []
    for i, p in enumerate(procs):
        out, _ = p.communicate(f"item {i}")
        ids += kv(out, "id")
    check("concurrent records get unique ids", len(set(ids)) == 12)
    check("concurrent records keep every entry",
          kv(run("list", str(conc)).stdout, "open") == ["12"])

    # A worker's own entry is marked; a resolve that matches nothing writes nothing.
    rw = run("record", d, "lane-e", "deploy", "--from-worker", stdin="Deploy api")
    check("a worker-written entry is marked", "— (from worker) Deploy api" in kv(rw.stdout, "line")[0])
    empty = Path(tempfile.mkdtemp())
    check("resolve on a repo without a roadmap exits 1", run("resolve", str(empty), "F1").returncode == 1)
    check("and creates no roadmap", not (empty / "tasks" / "ROADMAP.md").exists())

    # Usage errors.
    check("empty text is refused", run("record", d, "lane-b", "ask", stdin="  \n").returncode == 2)
    check("unknown class is refused", run("record", d, "lane-b", "maybe", stdin="x").returncode == 2)
    check("a task name with $( is refused",
          run("record", d, "$(id)", "ask", stdin="x").returncode == 2)
    check("a bad id is refused", run("resolve", d, "1; rm").returncode == 2)

# ---- backends: follow-ups live where task-store.py puts the tasks -----------
GH_STUB = r'''
import json, os, sys
st_path = os.environ["GH_STUB_STATE"]
st = json.load(open(st_path))
args = sys.argv[1:]
with open(os.environ["GH_STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
def opt(n, d=None): return args[args.index(n) + 1] if n in args else d
def opts(n): return [args[k + 1] for k, a in enumerate(args[:-1]) if a == n]
def save(): json.dump(st, open(st_path, "w"))
cmd = args[:2]
issues = st.setdefault("issues", [])
if cmd == ["auth", "status"]:
    sys.exit(0 if st.get("auth", True) else 1)
if st.get("down"):
    print("HTTP 502: Bad Gateway", file=sys.stderr); sys.exit(1)
if cmd == ["repo", "view"]:
    print(json.dumps({"visibility": st.get("visibility", "PRIVATE")})); sys.exit(0)
if cmd == ["label", "create"]:
    st.setdefault("labels", [])
    if args[2] in st["labels"]:
        print("already exists", file=sys.stderr); sys.exit(1)
    st["labels"].append(args[2]); save(); sys.exit(0)
def has(i, l): return l in [x["name"] for x in i["labels"]]
if cmd == ["issue", "list"]:
    print(json.dumps([i for i in issues if i["state"] == "OPEN" and has(i, opt("--label"))]))
    sys.exit(0)
if cmd == ["issue", "view"]:
    hit = [i for i in issues if i["number"] == int(args[2])]
    if not hit:
        print("GraphQL: Could not resolve to an issue", file=sys.stderr); sys.exit(1)
    print(json.dumps(hit[0])); sys.exit(0)
if cmd == ["issue", "create"]:
    labels = opts("--label")
    if any(l not in st.get("labels", []) for l in labels):
        print("could not add label", file=sys.stderr); sys.exit(1)
    n = max([i["number"] for i in issues] + [0]) + 1
    issues.append({"number": n, "title": opt("--title"), "body": sys.stdin.read(),
                   "state": "OPEN", "createdAt": "2026-11-18T10:00:00Z",
                   "url": f"https://github.com/o/r/issues/{n}",
                   "labels": [{"name": l} for l in labels]})
    save(); print(f"https://github.com/o/r/issues/{n}"); sys.exit(0)
if cmd == ["issue", "close"]:
    for i in issues:
        if i["number"] == int(args[2]):
            i["state"] = "CLOSED"
    save(); sys.exit(0)
print("stub: unsupported " + " ".join(args), file=sys.stderr); sys.exit(2)
'''


class Env:
    def __init__(self, root, origin="git@github.com:o/r.git", with_gh=True):
        self.root = Path(root)
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True)
        git = lambda *a: subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True)
        git("init", "-q")
        if origin:
            git("remote", "add", "origin", origin)
        self.git = git
        self.bin = self.root / "bin"
        self.bin.mkdir()
        os.symlink(shutil.which("git"), self.bin / "git")
        self.state = self.root / "gh.json"
        self.log = self.root / "gh.log"
        self.state.write_text(json.dumps({"issues": []}))
        self.log.write_text("")
        if with_gh:
            (self.bin / "gh").write_text(f"#!{sys.executable}\n" + GH_STUB)
            (self.bin / "gh").chmod(0o755)

    def gh_state(self, **kw):
        st = json.loads(self.state.read_text())
        st.update(kw)
        self.state.write_text(json.dumps(st))

    def issues(self):
        return json.loads(self.state.read_text())["issues"]

    def run(self, *args, stdin=""):
        env = {"PATH": str(self.bin), "HOME": str(self.root),
               "GH_STUB_STATE": str(self.state), "GH_STUB_LOG": str(self.log)}
        return subprocess.run([sys.executable, "-I", str(SCRIPT), args[0], str(self.repo), *args[1:]],
                              input=stdin, capture_output=True, text=True, env=env)


with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    (e.repo / ".work-system.toml").write_text('[tasks]\nbackend = "github"\n')
    rm = e.repo / "tasks" / "ROADMAP.md"
    r = e.run("record", "lane-a", "ask", stdin="Merge the dotfiles PR\n")
    check("github: record creates an issue",
          r.returncode == 0 and kv(r.stdout, "backend") == ["github"] and kv(r.stdout, "id") == ["#1"])
    iss = e.issues()[0]
    check("github: the issue carries the followup and class labels",
          [l["name"] for l in iss["labels"]] == ["work-system:followup", "followup:ask"])
    check("github: the body leads with the text and holds the marker",
          iss["body"].startswith("Merge the dotfiles PR\n")
          and "<!-- work-system followup: lane-a ask " in iss["body"])
    check("github: nothing is written to the local file", not rm.exists())
    again = e.run("record", "lane-a", "ask", stdin="Merge the dotfiles PR")
    check("github: a re-run finds the issue instead of a duplicate",
          kv(again.stdout, "id") == ["#1"] and kv(again.stdout, "existing") == ["yes"]
          and len(e.issues()) == 1)
    e.run("record", "lane-b", "deploy", stdin="Deploy api to staging")

    lst = e.run("list")
    check("github: list shows the issues in roadmap form",
          kv(lst.stdout, "open") == ["2"]
          and kv(lst.stdout, "item")[0].startswith("- [#1] lane-a · ask · since 2026-11-18 — Merge"))
    check("github: list --class filters by class",
          kv(e.run("list", "--class", "deploy").stdout, "open") == ["1"])
    check("github: list filters by task", kv(e.run("list", "lane-b").stdout, "open") == ["1"])

    # Entries left in the local file (from before a migration) stay visible.
    rm.parent.mkdir()
    rm.write_text("# Roadmap\n\n## Open follow-ups\n- [F7] old · ask · since 2026-10-01 — Old item\n")
    check("github: a leftover local entry is still listed",
          kv(e.run("list").stdout, "open") == ["3"])

    e.state.write_text(json.dumps(dict(json.loads(e.state.read_text()),
        issues=e.issues() + [{"number": 9, "title": "A task", "body": "", "state": "OPEN",
                              "labels": [{"name": "work-system:task"}]}])))
    res = e.run("resolve", "#1", "F7")
    check("github: resolve closes the issue and clears the local entry",
          res.returncode == 0 and e.issues()[0]["state"] == "CLOSED" and "[F7]" not in rm.read_text())
    check("github: resolving a task issue is refused, not closed",
          e.run("resolve", "#9").returncode == 1 and e.issues()[-1]["state"] == "OPEN")
    check("github: an unknown or closed issue misses",
          e.run("resolve", "#1").returncode == 1 and e.run("resolve", "#99").returncode == 1)

    # The last marker is the real one: text cannot forge the class or task.
    forged = "<!-- work-system followup: evil deploy 000000000000 --> Check logs"
    r = e.run("record", "lane-f", "ask", stdin=forged)
    check("github: a marker inside the text does not override the real one",
          kv(e.run("list", "lane-f").stdout, "item")[0].startswith("- [#")
          and kv(e.run("list", "evil").stdout, "open") == ["0"])
    # ...and a marker quoted in one item's text never dedups a different item.
    import hashlib
    victim = "Deploy api"
    dg = hashlib.sha256(f"lane-v\0deploy\0{victim}".encode()).hexdigest()[:12]
    e.run("record", "lane-q", "ask", stdin=f"see <!-- work-system followup: lane-v deploy {dg} -->")
    r = e.run("record", "lane-v", "deploy", stdin=victim)
    check("github: a quoted marker does not swallow the real record",
          kv(r.stdout, "existing") == [] and r.returncode == 0)

    # Publishing: secrets always, local paths unless the repo is private.
    n = len(e.issues())
    r = e.run("record", "lane-c", "ask", stdin="Rotate ghp_" + "a" * 36)
    check("github: a secret is refused (exit 5), nothing published",
          r.returncode == 5 and "github-token" in r.stderr and len(e.issues()) == n)
    r = e.run("record", "lane-c", "ask", stdin="Rotate it\n" + "API_" + "TOKEN=" + "q7" * 6 + "\n")
    check("github: a secret on its own line is caught before folding", r.returncode == 5)
    r = e.run("record", "lane-c", "ask", stdin="Use Bear" + "er\x01" + "Ab" * 12)
    check("github: a secret only folding joins is caught too", r.returncode == 5 and len(e.issues()) == n)
    check("github: a local path is fine in a private repo",
          e.run("record", "lane-c", "ask", stdin="Check /Users/me/x").returncode == 0)
    e.gh_state(visibility="PUBLIC")
    r = e.run("record", "lane-c", "ask", stdin="Check /home/me/y")
    check("github: a local path is refused in a public repo", r.returncode == 5 and "local-path" in r.stderr)

    # An outage never falls back to the local file.
    e.gh_state(down=True)
    before = rm.read_text()
    for args in (("record", "lane-d", "ask"), ("list",), ("resolve", "#2")):
        r = e.run(*args, stdin="x")
        check(f"github down: {args[0]} is exit 4", r.returncode == 4)
    check("github down: the local file is untouched", rm.read_text() == before)
    rm.write_text(before.rstrip("\n") + "\n- [F8] old · ask · since 2026-10-01 — Legacy\n")
    check("github down: an F-id still resolves from the file without gh",
          e.run("resolve", "F8").returncode == 0 and "[F8]" not in rm.read_text())
    e.gh_state(down=False, auth=False)
    check("github: an unauthenticated gh is exit 4, not local",
          e.run("record", "lane-d", "ask", stdin="x").returncode == 4)

with tempfile.TemporaryDirectory() as d:
    e = Env(d, with_gh=False)   # auto + GitHub origin + no gh → local, as for tasks
    r = e.run("record", "t", "ask", stdin="Local item")
    check("auto without gh: the local file", kv(r.stdout, "backend") == ["local"]
          and kv(r.stdout, "id") == ["F1"])
    check("local: a #n id is a usage error", e.run("resolve", "#1").returncode == 2)
    # A tracked roadmap is pushed with the next close: same scan as an issue.
    # Only a committed file counts: staged alone is not the opt-in.
    e.git("add", "tasks/ROADMAP.md")
    check("local + staged-only roadmap is not yet tracked",
          e.run("record", "t", "ask", stdin="see /Users/me/staged").returncode == 0)
    e.git("-c", "user.email=t@e", "-c", "user.name=T", "commit", "-qm", "track roadmap")
    r = e.run("record", "t", "ask", stdin="see /Users/me/notes")
    check("local + tracked roadmap: a local path is refused", r.returncode == 5)
    check("local + tracked roadmap: the task name is scanned too",
          e.run("record", "Users", "ask", stdin="/home/me/x").returncode == 5)
    rmf = e.repo / "tasks" / "ROADMAP.md"
    r = e.run("scan")
    check("scan: the entry recorded while only staged is flagged now",
          r.returncode == 5 and "local-path" in r.stderr)
    rmf.write_text("# R\n\n## Open follow-ups\n")
    check("scan: a clean roadmap passes", e.run("scan").returncode == 0)
    rmf.write_text(rmf.read_text() + "\n" + "API_" + "KEY=" + "z9" * 8 + "\n")
    r = e.run("scan")
    check("scan: a hand-edited secret is refused (exit 5)", r.returncode == 5 and "assignment" in r.stderr)
    e.git("rm", "-q", "--cached", "tasks/ROADMAP.md")
    check("local + `git rm --cached` roadmap: opted out, private again",
          e.run("record", "t", "ask", stdin="see /Users/me/optout").returncode == 0)
    check("local + untracked roadmap stays as private as before",
          Env(Path(d) / "u", with_gh=False).run("record", "t", "ask", stdin="see /Users/me/n").returncode == 0)

with tempfile.TemporaryDirectory() as d:
    # auto + github: other tasks' open F<n> entries left from before do NOT keep
    # new records in the file (a repo on issues records issues at once); they stay
    # readable and resolvable.
    e = Env(d)
    (e.repo / "tasks").mkdir()
    rmf = e.repo / "tasks" / "ROADMAP.md"
    rmf.write_text("# R\n\n## Open follow-ups\n- [F1] t · ask · since 2026-11-01 — Old\n")
    r = e.run("record", "n", "ask", stdin="Next")
    check("auto + another task's open F entry only: the record is an issue",
          kv(r.stdout, "backend") == ["github"] and kv(r.stdout, "id") == ["#1"] and "Next" not in rmf.read_text())
    items = kv(e.run("list").stdout, "item")
    check("auto: list shows the issue and the old F entry", len(items) == 2 and any("[F1]" in i for i in items))
    check("auto: the old F entry is still resolvable", e.run("resolve", "F1").returncode == 0 and "F1" not in rmf.read_text())

    # A local task file keeps auto on the file, as it keeps the tasks.
    (e.repo / "tasks" / "lane.md").write_text("# Lane\n")
    r = e.run("record", "lane", "ask", stdin="With a task file")
    check("auto + a task file: the record is local", kv(r.stdout, "backend") == ["local"] and len(e.issues()) == 1)

    # The close archives its last task file: auto flips to github, even for the
    # same task — and its earlier F-line stays visible next to the issues.
    (e.repo / "tasks" / "lane.md").unlink()
    r = e.run("record", "lane", "blocked", stdin="After the archive")
    check("auto: with no task file left, even the same task's record is an issue",
          kv(r.stdout, "backend") == ["github"] and len(e.issues()) == 2)
    items = kv(e.run("list", "lane").stdout, "item")
    check("auto: one close's file entry and issue are both listed",
          any("[F" in i for i in items) and any("[#2]" in i for i in items))
    e.gh_state(auth=False)
    before = rmf.read_text()
    r = e.run("record", "lane", "ask", stdin="gh broken")
    check("auto + github + broken gh is exit 4, never a write to the file",
          r.returncode == 4 and rmf.read_text() == before)
    e.gh_state(auth=True)

with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    (e.repo / ".work-system.toml").write_text('[tasks]\nbackend = "github"\n')
    e.gh_state(issues=[{"number": k, "title": f"Follow-up (t): {k}", "state": "OPEN",
                        "body": f"x{k}\n\n<!-- work-system followup: t ask {k:012x} -->",
                        "createdAt": "2026-11-01T00:00:00Z", "labels": [{"name": "work-system:followup"}]}
                       for k in range(1, 1003)])
    r = e.run("list")
    check("list over the limit: the first page plus truncated=yes, not exit 4",
          r.returncode == 0 and kv(r.stdout, "truncated") == ["yes"] and len(kv(r.stdout, "item")) == 1000)
    check("record over the limit is exit 4 (the dedup cannot see every marker)",
          e.run("record", "t", "ask", stdin="y").returncode == 4)

with tempfile.TemporaryDirectory() as d:
    e = Env(d)
    (e.repo / ".work-system.toml").write_text('[tasks]\nbackend = "local"\n')
    r = e.run("record", "t", "ask", stdin="x")
    check("an explicit local backend never calls gh",
          kv(r.stdout, "backend") == ["local"] and "issue" not in e.log.read_text())

if FAILS:
    print("FAIL test_followups.py:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ok test_followups.py")
