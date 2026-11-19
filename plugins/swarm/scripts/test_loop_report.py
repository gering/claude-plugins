#!/usr/bin/env python3
"""Tests for the `--loop --pr` loop report in pr-post.py (loop-build /
loop-target / loop-post).

The post path runs against a fake `gh` on PATH and a throwaway git repo, so the
contract the task names is checked end to end: exactly one comment, the right
head SHA in it, and a re-run that updates that comment instead of adding a
second one.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "pr-post.py"
_spec = importlib.util.spec_from_file_location("pr_post", SCRIPT)
pr_post = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_post)

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


HEAD = "a" * 40
TREE = "c" * 40
BASE = {
    "pr_num": 112,
    "head_oid": HEAD,
    "tree": TREE,
    "reviewed_tree": TREE,
    "fixes_total": 4,
    "end": "0-findings",
    "cap": 10,
    "open_trajectory": [4, 1, 0],
    "fixed": {"critical": 1, "warning": 2, "design": 1},
    "open_rows": [],
    "agents": ["claude", "codex"],
}

# --- render: marker, counts, clean state ------------------------------------ #
body = pr_post.render_loop_body(BASE)
first = body.splitlines()[0]
check("marker is the first line", first.startswith("<!-- swarm-loop-report:v1 ") and first.endswith(" -->"))
check("marker pins the full head", f"head={HEAD}" in first)
check("marker fields", all(t in first for t in
      ("pr=112", "rounds=3", "end=0-findings", "clean=yes", "rereviewed=yes", "open=0", "fixed=4")))
check("visible head", f"`{HEAD}`" in body and "head aaaaaaa" in body)
check("trajectory", "4 → 1 → 0" in body)
check("fixed row", "| Fixed | 1 | 2 | 0 | 1 |" in body)
check("open row", "| Open | 0 | 0 | 0 | 0 |" in body)
check("no open table when clean", "### Open items" not in body)
check("deterministic", body == pr_post.render_loop_body(json.loads(json.dumps(BASE))))

# --- cap: not clean, not re-reviewed, open items listed + sanitized --------- #
CAP = {**BASE, "end": "cap", "cap": 2, "open_trajectory": [5, 3], "reviewed_tree": "e" * 40,
       "open_rows": [
           {"num": "2", "sev": "🟡", "ort": "a.sh:3", "befund": "ping @x see /Users/alice/repo/a.sh",
            "status": "❌ disagree", "kind": "defect"},
           {"num": "7", "sev": "🟡", "ort": "b.py:9", "befund": "reuse helper", "status": "❓ needs-decision",
            "kind": "design", "lens": "reuse"},
           {"num": "4", "sev": "🔴", "ort": "c.py:1", "befund": "token ghp_" + "x" * 36,
            "status": "⏭️ skipped-stale", "kind": "defect"},
       ]}
cbody = pr_post.render_loop_body(CAP)
cfirst = cbody.splitlines()[0]
check("cap: not clean", "clean=no" in cfirst and "end=cap" in cfirst and "rereviewed=no" in cfirst)
check("cap: open count", "open=3" in cfirst and "| Open | 1 | 1 | 0 | 1 |" in cbody)
check("cap: unreviewed note", "not re-reviewed" in cbody)
check("open table", "| # | Sev | Location | Finding | Status |" in cbody)
rows = [ln for ln in cbody.splitlines() if ln.startswith("| ") and ln.split("|")[1].strip().isdigit()]
check("design row last", len(rows) == 3 and "reuse helper" in rows[2] and "&#91;reuse&#93;" in rows[2])
check("mention neutralized", "@x" not in cbody and "&#64;x" in cbody)
check("local path redacted", "/Users/alice" not in cbody and "redacted:local-path" in cbody)
check("token redacted", "ghp_" not in cbody and "redacted:github-token" in cbody)
check("redaction count", "2 value(s) redacted" in cbody)
check("quoted secret with spaces redacted",
      "horse" not in pr_post.redact('password="correct horse battery"')[0])
check("WSL / data-volume user paths redacted",
      all("alice" not in pr_post.redact(t)[0] for t in ("/mnt/c/Users/alice/r/a.py", "/System/Volumes/Data/Users/alice/a")))
check("Windows user with a space redacted", "Doe" not in pr_post.redact("C:\\Users\\John Doe\\repo")[0])
check("dash-encoded dotted user redacted", "smith" not in pr_post.redact("-Users-john-smith-Projekte-r")[0])
dbody = pr_post.render_loop_body({**BASE, "coverage_notes": ["grok timed out on breakage"]})
check("lost coverage marked + shown", "degraded=yes" in dbody.splitlines()[0] and "grok timed out" in dbody)
check("full coverage not degraded", "degraded=no" in body.splitlines()[0])
check("secret split by a pipe in ort still redacted",
      "ghp_" not in pr_post.render_loop_body({**BASE, "end": "no-change", "open_trajectory": [1], "open_rows": [
          {"num": "1", "sev": "⚪", "ort": "ghp_" + "x" * 18 + "|" + "x" * 18, "befund": "b"}]}))
check("footer redactions counted", "1 value(s) redacted" in pr_post.render_loop_body({**BASE, "agents": ["/Users/bob/x"]}))
check("PEM body redacted, not just its header",
      "MIIabc" not in pr_post.render_loop_body({**CAP, "open_trajectory": [5, 1], "open_rows": [
          {"num": "1", "sev": "🔴", "ort": "k.py:1", "status": "x",
           "befund": "key -----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"}]}))
check("env assignment redacted", pr_post.redact("export GITHUB_TOKEN=abcdef123")[1] == 1)
check("repo-relative home/ kept", pr_post.redact("src/home/x.py:3") == ("src/home/x.py:3", 0))
check("dash-encoded user dir redacted", "alice" not in pr_post.redact("-Users-alice-Projekte-repo")[0])
fbody = pr_post.render_loop_body({**BASE, "agents": ["claude", "@team", "/Users/bob/x"]})
check("footer agents sanitized + redacted", "@team" not in fbody and "&#64;team" in fbody and "bob" not in fbody)
check("design-only also unreviewed",
      "rereviewed=no" in pr_post.render_loop_body({**BASE, "end": "design-only"}).splitlines()[0])
check("reasons come from loop-closeout", set(pr_post.loop_reasons()) ==
      {"0-findings", "nothing-agreed", "no-change", "design-only", "cap"})

# --- validation: misuse fails loudly ---------------------------------------- #
def invalid(patch):
    try:
        pr_post.validate_loop({**BASE, **patch})
    except ValueError:
        return True
    return False


check("short sha refused", invalid({"head_oid": "abc1234"}))
check("injected sha refused", invalid({"head_oid": HEAD + "\n**x**"}))
check("unknown reason refused", invalid({"end": "done"}))
check("bad pr refused", invalid({"pr_num": "1; rm"}))
check("empty trajectory refused", invalid({"open_trajectory": []}))
check("bool in trajectory refused", invalid({"open_trajectory": [True]}))
check("open-count mismatch refused", invalid({"open_trajectory": [2, 1]}))
check("unknown fixed bucket refused", invalid({"fixed": {"blocker": 1}}))
check("unknown sev refused", invalid({"open_trajectory": [1], "open_rows": [{"num": "1", "sev": "!!"}]}))
check("cap below rounds refused", invalid({"cap": 2}))
check("non-string end refused", invalid({"end": ["cap"]}))
check("fixes_total mismatch refused", invalid({"fixes_total": 5}))
check("missing tree refused", invalid({"tree": None}))
check("clean end with a changed tree refused", invalid({"reviewed_tree": "e" * 40}))
check("end=cap needs cap == rounds", invalid({"end": "cap", "cap": 10}))
check("oversized report refused", invalid({"open_trajectory": [400], "open_rows": [
    {"num": str(i), "sev": "⚪", "ort": "a:1", "befund": "x" * 200} for i in range(400)]}))
check("0-findings with open rows refused",
      invalid({"open_trajectory": [1], "open_rows": [{"num": "1", "sev": "🔴"}]}))
check("falsey malformed fixed refused", invalid({"fixed": []}))
r = subprocess.run([sys.executable, str(SCRIPT), "loop-build", "--input", "-"],
                   input=json.dumps({**BASE, "end": "nope"}), capture_output=True, text=True)
check("loop-build misuse -> exit 2", r.returncode == 2 and "Traceback" not in r.stderr)
r = subprocess.run([sys.executable, str(SCRIPT), "loop-build", "--input", "-"],
                   input=json.dumps(BASE), capture_output=True, text=True)
check("loop-build stdout = render", r.returncode == 0 and r.stdout == body)

# --- sync: migrate's patterns + the prep block's lane-file regex ------------ #
_ts = HERE.parent.parent / "work-system" / "scripts" / "task-store.py"
if _ts.is_file():   # same repo in development; an installed swarm ships alone
    _tspec = importlib.util.spec_from_file_location("task_store", _ts)
    task_store = importlib.util.module_from_spec(_tspec)
    _tspec.loader.exec_module(task_store)
    ours = dict(pr_post.REDACT_PATTERNS)
    for kind, pat in task_store.SECRET_PATTERNS + task_store.PUBLIC_PATTERNS:
        check(f"redaction covers migrate's {kind}", kind in ours)
        if kind not in ("private-key", "local-path"):   # deliberately stronger here
            check(f"redaction pattern {kind} matches migrate's", ours.get(kind) == pat)
SKILL_TEXT = (HERE.parent / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")
import re as _re
_prep_re = _re.search(r"grep -Ev '([^']+)'", SKILL_TEXT)
check("lane-file regex is exactly the prep block's",
      _prep_re is not None and _prep_re.group(1) == pr_post._LANE_UNTRACKED_RE.pattern)

# --- post: fake gh + real git ----------------------------------------------- #
FAKE_GH = textwrap.dedent(f"""\
    #!{sys.executable}
    import json, os, sys
    st = os.environ["FAKE_GH_STATE"]
    def load():
        with open(st) as f: return json.load(f)
    def save(s):
        with open(st, "w") as f: json.dump(s, f)
    a = sys.argv[1:]
    s = load()
    s["calls"].append(a); save(s)
    if a[:2] == ["pr", "view"]:
        if "headRefOid" == a[a.index("--json") + 1]:
            print(s["pr_head"]); sys.exit(0)
        print(json.dumps({{"number": 112, "headRefOid": s["pr_head"], "state": "OPEN", "baseRefName": s.get("base", "main")}})); sys.exit(0)
    if a[:2] == ["api", "user"]:
        print(s["me"]); sys.exit(0)
    if a[0] == "api" and "--paginate" in a:
        if s.get("list_fails"): sys.exit(1)
        for c in s["comments"]:
            print(json.dumps([c["id"], c["login"], c["body"].startswith("<!-- swarm-loop-report:v1 ")]))
        sys.exit(0)
    if a[0] == "api" and "-X" in a:
        method = a[a.index("-X") + 1]
        with open(a[a.index("--input") + 1]) as f: body = json.load(f)["body"]
        if method == "POST":
            cid = 100 + len(s["comments"])
            s["comments"].append({{"id": cid, "login": s["me"], "body": body}})
        else:
            cid = int([x for x in a if "/issues/comments/" in x][0].rsplit("/", 1)[1])
            for c in s["comments"]:
                if c["id"] == cid: c["body"] = body
        save(s); print(f"https://example.test/pr/112#issuecomment-{{cid}}"); sys.exit(0)
    sys.exit(3)
    """)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    bindir = tmp / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    repo = tmp / "repo"
    repo.mkdir()
    genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q"], cwd=repo, env=genv, check=True)
    (repo / "f.txt").write_text("1\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, env=genv, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=repo, env=genv, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=genv,
                          capture_output=True, text=True).stdout.strip()
    state = tmp / "gh.json"
    # someone else already posted a marker comment: never ours to update
    state.write_text(json.dumps({"me": "me", "pr_head": head, "calls": [], "comments": [
        {"id": 1, "login": "intruder", "body": "<!-- swarm-loop-report:v1 head=x -->"}]}))
    env = {**genv, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(state)}
    inp = tmp / "loop.json"

    def run(*args, data=None):
        if data is not None:
            inp.write_text(json.dumps(data))
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=repo, env=env,
                              capture_output=True, text=True, timeout=60)

    def mine():
        return [c for c in json.loads(state.read_text())["comments"] if c["login"] == "me"]

    t = run("loop-target", "--pr", "112")
    check("target resolves", f"LOOP_PR_HEAD_OID={head}" in t.stdout and "LOOP_PR_NUM=112" in t.stdout)
    t = run("loop-target", "--pr", "1;x")
    check("target refuses non-numeric", t.stdout.startswith("SWARM_PR_ERR="))

    lt = run("loop-tree")
    tree = lt.stdout.strip().partition("LOOP_TREE=")[2]
    check("loop-tree prints the tree", tree == subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, env=genv, capture_output=True, text=True).stdout.strip())
    POSTED = {**BASE, "head_oid": head, "tree": tree, "reviewed_tree": tree}
    p = run("loop-post", "--input", str(inp), data={**BASE, "head_oid": head, "tree": "d" * 40, "reviewed_tree": "d" * 40})
    check("tree mismatch -> not posted", p.stdout.startswith("SWARM_PR_TREE_MISMATCH=") and not mine())
    p = run("loop-post", "--input", str(inp), data=POSTED)
    check("first post creates", p.stdout.startswith("SWARM_PR_POSTED="))
    check("exactly one own comment", len(mine()) == 1)
    check("comment pins the head", f"head={head}" in mine()[0]["body"].splitlines()[0])
    check("posted = built", mine()[0]["body"] == pr_post.render_loop_body(POSTED))

    p = run("loop-post", "--input", str(inp), data={**CAP, "head_oid": head, "tree": tree, "reviewed_tree": tree})
    check("re-run updates", p.stdout.startswith("SWARM_PR_UPDATED=") and p.stdout.strip().endswith(f"-{mine()[0]['id']}"))
    check("still one own comment", len(mine()) == 1 and "end=cap" in mine()[0]["body"])
    check("foreign marker untouched",
          json.loads(state.read_text())["comments"][0]["body"] == "<!-- swarm-loop-report:v1 head=x -->")

    # uncommitted fixes: the report would pin a head that lacks them
    (repo / "f.txt").write_text("2\n")
    p = run("loop-post", "--input", str(inp), data=POSTED)
    check("dirty tree -> not posted", p.stdout.startswith("SWARM_PR_LOCAL_MISMATCH="))
    (repo / "TASK.md").write_text("x")
    subprocess.run(["git", "checkout", "-q", "f.txt"], cwd=repo, env=genv, check=True)
    p = run("loop-post", "--input", str(inp), data=POSTED)
    check("lane files are not dirt", p.stdout.startswith("SWARM_PR_UPDATED="))

    # PR advanced past the local head
    s = json.loads(state.read_text())
    s["pr_head"] = "b" * 40
    state.write_text(json.dumps(s))
    p = run("loop-post", "--input", str(inp), data=POSTED)
    check("stale PR -> not posted", p.stdout.startswith("SWARM_PR_STALE="))
    t = run("loop-target")
    check("target refuses a foreign head", t.stdout.startswith("SWARM_PR_ERR=local HEAD"))
    s = json.loads(state.read_text())
    s.update(pr_head=head, base="release")
    state.write_text(json.dumps(s))
    t = run("loop-target")
    check("target refuses a stacked PR", "not the default branch" in t.stdout)
    s["base"] = "main"
    state.write_text(json.dumps(s))

    # comment lookup fails: never blind-create a duplicate
    s = json.loads(state.read_text())
    s.update(pr_head=head, list_fails=True)
    state.write_text(json.dumps(s))
    n_before = len(mine())
    p = run("loop-post", "--input", str(inp), data=POSTED)
    check("lookup failure -> not posted", p.stdout.startswith("SWARM_PR_POST_ERR=") and len(mine()) == n_before)

# --- loop-tree in a real lane shape: gitignored lane files, tracked TASK.md -- #
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    g = lambda *a: subprocess.run(["git", *a], cwd=repo, env=genv, capture_output=True, text=True)  # noqa: E731
    g("init", "-q")
    (repo / ".gitignore").write_text("/TASK.md\n/MANDATE.md\n")
    (repo / "a").write_text("1\n")
    g("add", ".gitignore", "a")
    g("commit", "-qm", "c")
    (repo / "TASK.md").write_text("t")        # gitignored lane files present
    (repo / "MANDATE.md").write_text("m")
    (repo / ".swarm-workflow.x").mkdir()
    (repo / ".swarm-workflow.x" / "round 0.json").write_text("{}")
    (repo / "a").write_text("2\n")
    (repo / "new file").write_text("n")
    (repo / "T*").write_text("glob")           # a pathspec-magic name must stay literal
    (repo / "gen.txt").write_text("g")
    (repo / ".gitignore").write_text("/TASK.md\n/MANDATE.md\n/gen.txt\n")
    g("add", "-f", "gen.txt")                  # force-added ignored file, staged
    g("add", ".gitignore")
    r = subprocess.run([sys.executable, str(SCRIPT), "loop-tree"], cwd=repo, env=genv,
                       capture_output=True, text=True)
    lane_tree = r.stdout.strip().partition("LOOP_TREE=")[2]
    check("loop-tree works with gitignored lane files", len(lane_tree) == 40)
    g("add", "a", "new file", "T*", ".gitignore")
    g("commit", "-qm", "fixes")
    check("loop-tree = the commit of the loop's fixes", g("rev-parse", "HEAD^{tree}").stdout.strip() == lane_tree)
    head, htree, dirty = (lambda: (subprocess.run([sys.executable, "-c",
        "import importlib.util as u,sys;s=u.spec_from_file_location('p',sys.argv[1]);m=u.module_from_spec(s);"
        "s.loader.exec_module(m);print(*m.local_state())", str(SCRIPT)], cwd=repo, env=genv,
        capture_output=True, text=True).stdout.split()))()
    check("quoted lane paths are not dirt", dirty == "False")
    # a project that TRACKS a root TASK.md: the loop's edit to it is part of the tree
    (repo / ".gitignore").write_text("/MANDATE.md\n")
    g("add", ".gitignore", "TASK.md")
    g("commit", "-qm", "track")
    (repo / "TASK.md").write_text("edited")
    r = subprocess.run([sys.executable, str(SCRIPT), "loop-tree"], cwd=repo, env=genv,
                       capture_output=True, text=True)
    g("add", "TASK.md")
    g("commit", "-qm", "edit")
    check("tracked TASK.md edits are in the tree",
          r.stdout.strip().partition("LOOP_TREE=")[2] == g("rev-parse", "HEAD^{tree}").stdout.strip())

if FAILS:
    print("loop-report tests FAILED:", file=sys.stderr)
    for f in FAILS:
        print("  -", f, file=sys.stderr)
    sys.exit(1)
print("loop-report: all tests passed")
