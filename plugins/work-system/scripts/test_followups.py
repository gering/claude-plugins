#!/usr/bin/env python3
"""Tests for followups.py — the Manager's ROADMAP record of open follow-ups.

Run standalone (`python3 test_followups.py`) or via check-structure.py. What is
pinned: an item survives in tasks/ROADMAP.md until resolved, the rest of the
roadmap is never touched, ids are never reused, and untrusted-looking text can
neither start a heading nor a second entry.
"""
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

if FAILS:
    print("FAIL test_followups.py:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ok test_followups.py")
