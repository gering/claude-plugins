#!/usr/bin/env python3
"""Tests for merge-gate.py — run standalone or via scripts/check-structure.py.

A stub `gh` serves fixtures per endpoint and runs the script's REAL `--jq`
filters (claude-review.sh's CLAUDE_JQ included), and the base config is
fetched from a real bare repository, so what is under test is the gate's
decision, not a re-implementation of it:

  * approval: write access (association, then the repository permission;
    unreadable → exit 2); old head → missing; own approval → missing; approval on the
    current head → ok; an open CHANGES_REQUESTED → missing; a later COMMENTED
    does not cancel an approval; bots count only with allow_bots; outsiders,
    deleted accounts and `alice[bot]` standing in for `alice` do not count;
    the approvers / ignore lists;
  * the config comes from the BASE branch of the remote that points at the
    PR's base repository, never the working tree or a fork's `origin`, and an
    unreadable one — bad TOML, an unknown key under [merge], a wrong type
    anywhere — fails closed (exit 2); an unknown key elsewhere only warns;
  * comments: unresolved threads the author has not answered, comments and
    review bodies after the author's last activity (a review trigger is not
    activity; an approval's body is not an item), the Claude App left out
    (unless a third party replied in its thread) but a human named `claude`
    kept, and third-party text flattened.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "merge-gate.py"
FAILS = []

HEAD = "a" * 40
OLD = "b" * 40
BASE_URL = "https://github.com/o/r"


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)
        if detail:
            print("     " + detail.replace("\n", "\n     "))


TMP = Path(tempfile.mkdtemp())
atexit.register(shutil.rmtree, TMP, True)
STUB = TMP / "bin"
STUB.mkdir()
(STUB / "gh").write_text(r"""#!/usr/bin/env bash
[ "$1" = auth ] && exit 0
key="" expr="."
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) expr="$2"; shift ;;
    graphql) key=graphql ;;
    repos/*/collaborators/*/permission) l="${1#*/collaborators/}"; key="perm-${l%/permission}" ;;
    repos/*/reviews*) key=reviews ;;
    repos/*/comments*) key=comments ;;
    repos/*/pulls/*) key=pull ;;
  esac
  shift
done
f="$GH_FIXTURES/$key.json"
case "$key" in perm-*) [ -f "$f" ] || f="$GH_FIXTURES/perm-default.json" ;; esac
[ "$(cat "$f")" = FAIL ] && { echo "HTTP 403" >&2; exit 1; }
exec jq -rc "$expr" "$f"
""")
(STUB / "gh").chmod(0o755)
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_NOSYSTEM="1")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=GIT_ENV)


def make_repo(base_toml=None, worktree_toml=None, symlink=False, remote_url=BASE_URL + ".git",
              push=True):
    """A checkout whose remote `origin` (configured as remote_url) is a local
    bare repo holding `main` with the given base config."""
    root = Path(tempfile.mkdtemp(dir=TMP))
    bare, src, repo = root / "bare.git", root / "src", root / "repo"
    git(root, "init", "-q", "--bare", str(bare))
    git(root, "init", "-q", "-b", "main", str(src))
    if symlink:
        (src / ".pr-flow.toml").symlink_to("/etc/hosts")
    elif base_toml is not None:
        (src / ".pr-flow.toml").write_text(base_toml)
    (src / "x").write_text("x")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "base")
    if push:
        git(src, "push", "-q", str(bare), "main")
    git(root, "init", "-q", "-b", "task", str(repo))
    git(repo, "remote", "add", "origin", remote_url)
    git(repo, "config", f"url.file://{bare}.insteadOf", remote_url)
    if worktree_toml is not None:
        (repo / ".pr-flow.toml").write_text(worktree_toml)
    return repo


def user(login, kind="User"):
    return None if login is None else {"login": login, "type": kind}


def review(login, state, commit=HEAD, when="2026-01-02T00:00:00Z", body="", kind="User",
           assoc="MEMBER"):
    return {"user": user(login, kind), "state": state, "commit_id": commit, "author_association": assoc,
            "submitted_at": when, "body": body, "html_url": "https://github.com/o/r/pull/7#r"}


def comment(login, body, when="2026-01-02T00:00:00Z", kind="User"):
    return {"user": user(login, kind), "body": body, "created_at": when,
            "html_url": "https://github.com/o/r/pull/7#c"}


def thread(opener, *later, resolved=False, body="please fix", when="2026-01-02T00:00:00Z", kind="User"):
    nodes = [{"author": {"login": opener, "__typename": kind}, "createdAt": when}]
    nodes += [{"author": {"login": who, "__typename": "Bot" if who == "claude" else "User"}, "createdAt": at}
              for who, at in later]
    return {"isResolved": resolved,
            "opener": {"nodes": [{"author": {"login": opener, "__typename": kind}, "body": body,
                                  "url": "https://github.com/o/r/pull/7#t", "createdAt": when}]},
            "recent": {"nodes": nodes}}


def gate(repo, reviews=(), comments=(), threads=(), head=HEAD, pr_head=HEAD, base_url=BASE_URL, cwd=None,
         perms=None):
    fx = Path(tempfile.mkdtemp(dir=TMP))
    (fx / "perm-default.json").write_text(json.dumps({"permission": "write"}))
    for login, level in (perms or {}).items():
        (fx / f"perm-{login}.json").write_text("FAIL" if level is None else json.dumps({"permission": level}))
    (fx / "pull.json").write_text(json.dumps({
        "user": user("dev"), "head": {"sha": pr_head},
        "base": {"ref": "main", "repo": {"full_name": "o/r", "html_url": base_url}}}))
    (fx / "reviews.json").write_text(json.dumps(list(reviews)))
    (fx / "comments.json").write_text(json.dumps(list(comments)))
    (fx / "graphql.json").write_text(json.dumps(
        {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": list(threads)}}}}}))
    env = dict(GIT_ENV, PATH=f"{STUB}{os.pathsep}{os.environ['PATH']}", GH_FIXTURES=str(fx),
               MERGE_GATE_HEAD_WAIT="0")
    argv = [sys.executable, str(SCRIPT), "check", "--pr", "7"] + (["--head", head] if head else [])
    p = subprocess.run(argv, cwd=cwd or repo, env=env, capture_output=True, text=True, timeout=60)
    kv, items = {}, []
    for line in p.stdout.split("\n"):
        if line.startswith("item\t"):
            items.append(line.split("\t")[1:])
        elif "=" in line:
            k, _, v = line.partition("=")
            kv[k] = v
    return p.returncode, kv, items, p.stderr


ON = "[merge.require_approval]\nenabled = true\n"

# --- approval ------------------------------------------------------------- #
r = make_repo()
rc, kv, items, err = gate(r)
check("no config: gate off, nothing open → exit 0", rc == 0 and kv.get("approval") == "off", err)
check("and says where the config was looked for", kv.get("config") == "no .pr-flow.toml on origin/main", str(kv))

r = make_repo(ON)
rc, kv, _, err = gate(r, [review("rev", "APPROVED", commit=OLD)])
check("approval on an old head → missing", rc == 1 and kv.get("approval") == "missing"
      and kv.get("approval_reason") == "approvals", str(kv) + err)
check("and names the stale approval", kv.get("approval_stale") == "rev@bbbbbbb", str(kv))

rc, kv, _, _ = gate(r, [review("Dev", "APPROVED"), review("Bob", "CHANGES_REQUESTED")])
check("logins keep their case in every field", kv.get("approval_skipped") == "Dev:author"
      and kv.get("changes_requested") == "Bob", str(kv))

rc, kv, _, _ = gate(r, [review("dev", "APPROVED")])
check("own approval → missing", rc == 1 and kv.get("approval_skipped") == "dev:author", str(kv))

rc, kv, _, _ = gate(r, [review("rev", "APPROVED")])
check("approval on the current head → ok", rc == 0 and kv.get("approval") == "ok"
      and kv.get("approval_counted") == "rev@aaaaaaa", str(kv))

rc, kv, _, _ = gate(r, [review("rev", "APPROVED", when="2026-01-01T00:00:00Z"),
                        review("rev", "CHANGES_REQUESTED", when="2026-01-03T00:00:00Z")])
check("later CHANGES_REQUESTED by the same reviewer → missing",
      rc == 1 and kv.get("approval_reason") == "changes-requested"
      and kv.get("changes_requested") == "rev", str(kv))

rc, kv, _, _ = gate(r, [review("rev", "APPROVED"), review("other", "CHANGES_REQUESTED")])
check("an open CHANGES_REQUESTED by anyone → missing", rc == 1
      and kv.get("approval_reason") == "changes-requested", str(kv))

rc, kv, _, _ = gate(r, [review("rev", "CHANGES_REQUESTED", when="2026-01-01T00:00:00Z"),
                        review("rev", "APPROVED", when="2026-01-03T00:00:00Z")])
check("re-approval after CHANGES_REQUESTED → ok", rc == 0 and kv.get("approval") == "ok", str(kv))

rc, kv, _, _ = gate(r, [review("rev", "APPROVED", when="2026-01-01T00:00:00Z"),
                        review("rev", "COMMENTED", when="2026-01-01T01:00:00Z")])
check("a later COMMENTED review keeps the approval", rc == 0 and kv.get("approval") == "ok", str(kv))

rc, kv, _, _ = gate(r, [review("rev", "APPROVED", when="2026-01-01T00:00:00Z"),
                        review("rev", "DISMISSED", when="2026-01-01T01:00:00Z")])
check("a dismissed approval does not count", rc == 1, str(kv))

rc, kv, _, _ = gate(r, [review("alice", "CHANGES_REQUESTED", when="2026-01-01T00:00:00Z"),
                        review("alice[bot]", "APPROVED", when="2026-01-03T00:00:00Z", kind="Bot")])
check("an App `alice[bot]` does not overwrite `alice`'s change request", rc == 1
      and kv.get("changes_requested") == "alice", str(kv))

rc, kv, _, err = gate(r, [review(None, "CHANGES_REQUESTED")])
check("a deleted account's change request is reported, not a crash", rc == 1
      and kv.get("changes_requested") == "ghost", str(kv) + err)
rc, kv, _, _ = gate(r, [review(None, "APPROVED")])
check("a deleted account's approval does not count", rc == 1
      and kv.get("approval_skipped") == "ghost:ghost", str(kv))

rc, kv, _, _ = gate(r, [review("drive-by", "APPROVED", assoc="NONE")])
check("an outsider's approval does not count", rc == 1
      and kv.get("approval_skipped") == "drive-by:not-a-collaborator", str(kv))

rc, kv, _, _ = gate(r, [review("reader", "APPROVED")], perms={"reader": "read"})
check("a member with read access only does not count", rc == 1
      and kv.get("approval_skipped") == "reader:not-a-collaborator", str(kv))
rc, kv, _, err = gate(r, [review("rev", "APPROVED")], perms={"rev": None})
check("an unreadable permission fails closed", rc == 2 and kv.get("verdict") == "error", str(kv) + err)
rc, kv, _, _ = gate(r, [review("github-actions[bot]", "APPROVED", kind="Bot", assoc="NONE")])
check("a GitHub Actions approval does not count by default", rc == 1
      and kv.get("approval_skipped") == "github-actions[bot]:bot", str(kv))
rb = make_repo(ON + "allow_bots = true\n")
rc, kv, _, _ = gate(rb, [review("github-actions[bot]", "APPROVED", kind="Bot", assoc="NONE")])
check("… and counts with allow_bots", rc == 0, str(kv))

r2 = make_repo(ON + "count = 2\n")
rc, kv, _, _ = gate(r2, [review("a1", "APPROVED"), review("a2", "APPROVED", commit=OLD)])
check("count = 2 with one approval on head → missing", rc == 1
      and kv.get("approval_required") == "2", str(kv))

ra = make_repo(ON + 'approvers = ["Lead"]\n')
rc, kv, _, _ = gate(ra, [review("rev", "APPROVED")])
check("approvers list: others do not count", rc == 1
      and kv.get("approval_skipped") == "rev:not-an-approver", str(kv))
rc, kv, _, _ = gate(ra, [review("lead", "APPROVED", assoc="NONE")])
check("approvers list matches case-insensitively and replaces the collaborator rule", rc == 0, str(kv))
ri = make_repo(ON + 'ignore = ["rev"]\n')
rc, kv, _, _ = gate(ri, [review("rev", "APPROVED")])
check("ignore list", rc == 1 and kv.get("approval_skipped") == "rev:ignored", str(kv))

ro = make_repo(ON + "on_head = false\n")
rc, kv, _, _ = gate(ro, [review("rev", "APPROVED", commit=OLD)])
check("on_head = false counts an older approval, and shows which commit",
      rc == 0 and kv.get("approval_counted") == "rev@bbbbbbb", str(kv))

# --- config source -------------------------------------------------------- #
rw = make_repo(ON, worktree_toml="[merge.require_approval]\nenabled = false\n")
rc, kv, _, _ = gate(rw)
check("the working tree cannot switch the gate off", rc == 1 and kv.get("approval") == "missing", str(kv))
rw = make_repo(None, worktree_toml=ON)
rc, kv, _, _ = gate(rw)
check("… nor is an uncommitted config read", rc == 0 and kv.get("approval") == "off", str(kv))

rc, kv, _, err = gate(make_repo(ON, remote_url="https://github.com/fork/r.git"))
check("a checkout with no remote for the base repo (a fork) fails closed",
      rc == 2 and "no git remote points at o/r" in err, err)
rc, kv, _, err = gate(make_repo(ON, remote_url="git@github.com:O/r.git"))
check("an ssh remote URL for the base repo is recognized", rc == 1 and kv.get("approval") == "missing", err)

for name, kw in [("bad TOML", dict(base_toml="[merge\n")),
                 ("an unknown key in [merge.require_approval]", dict(base_toml=ON + "requred = 2\n")),
                 ("an unknown key in [merge]", dict(base_toml="[merge]\nrequire_comment_addressed = true\n")),
                 ("a misspelled subtable", dict(base_toml="[merge.require_approvl]\nenabled = true\n")),
                 ("a wrong type", dict(base_toml=ON + 'count = "1"\n')),
                 ("a bool where an integer belongs", dict(base_toml=ON + "count = true\n")),
                 ("count = 0", dict(base_toml=ON + "count = 0\n")),
                 ("a non-string in approvers", dict(base_toml=ON + "approvers = [1]\n")),
                 ("a symlink", dict(symlink=True)),
                 ("a base branch the remote lacks", dict(base_toml=ON, push=False))]:
    rc, kv, _, err = gate(make_repo(**kw))
    check(f"{name} on the base fails closed (exit 2)", rc == 2 and kv.get("verdict") == "error", err)

rc, kv, _, err = gate(make_repo("[mrege.require_approval]\nenabled = true\n"))
check("an unknown key outside [merge] only warns, and says so",
      rc == 0 and kv.get("config_warning") == "mrege is not a known key (ignored)", str(kv) + err)
sub_dir = make_repo(ON)
(sub_dir / "plugins").mkdir()
rc, kv, _, err = gate(sub_dir, cwd=sub_dir / "plugins")
check("run from a subdirectory, the base config is still found",
      rc == 1 and kv.get("approval") == "missing", str(kv) + err)
rc, kv, _, err = gate(make_repo(ON, remote_url="git@github-work:o/r.git"))
check("an ssh host alias for the base repo is recognized", rc == 1 and kv.get("approval") == "missing", err)
single = make_repo(ON)
git(single, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
(single / "y").write_text("y")
git(single, "add", "y")
git(single, "commit", "-q", "-m", "stale")
git(single, "update-ref", "refs/remotes/origin/main", "HEAD")
rc, kv, _, err = gate(single)
check("a single-branch clone's stale tracking ref is refreshed, not read",
      rc == 1 and kv.get("approval") == "missing", str(kv) + err)
rc, kv, _, err = gate(make_repo(ON), head=None, pr_head=OLD)
check("without --head the PR's current head is checked", kv.get("head") == OLD and rc == 1, str(kv) + err)

rc, kv, _, err = gate(make_repo(ON), pr_head=OLD)
check("a PR head other than --head → exit 2", rc == 2 and "head moved" in err, err)
rc, kv, _, err = gate(make_repo(ON), head="abc")
check("a short --head → exit 2", rc == 2, err)

# --- comments ------------------------------------------------------------- #
r = make_repo()
rc, kv, items, _ = gate(r, threads=[thread("rev")])
check("an unresolved thread nobody answered → open (soft: exit 0)", rc == 0 and kv.get("comments") == "open"
      and items and items[0][:2] == ["thread", "rev"], str(items))
rc, kv, items, _ = gate(r, threads=[thread("rev", ("dev", "2026-01-03T00:00:00Z"))])
check("a thread the author answered last → not open", rc == 0 and kv.get("comments") == "none" and kv.get("open_items") == "0", str(items))
rc, kv, items, _ = gate(r, threads=[thread("rev", resolved=True)])
check("a resolved thread → not open", kv.get("comments") == "none", str(items))

rc, kv, items, _ = gate(r, comments=[comment("dev", "done", "2026-01-01T00:00:00Z"),
                                     comment("rev", "one more thing", "2026-01-02T00:00:00Z")])
check("a comment after the author's last reply → open", kv.get("comments") == "open" and len(items) == 1
      and items[0][1] == "rev", str(items))
rc, kv, items, _ = gate(r, comments=[comment("rev", "q?", "2026-01-01T00:00:00Z"),
                                     comment("dev", "answered", "2026-01-02T00:00:00Z")])
check("a comment the author answered after → not open", kv.get("comments") == "none", str(items))
rc, kv, items, _ = gate(r, comments=[comment("rev", "don't merge yet", "2026-01-01T00:00:00Z"),
                                     comment("dev", "@claude review", "2026-01-02T00:00:00Z")])
check("the author's review trigger is not an answer", kv.get("comments") == "open" and items[0][1] == "rev", str(items))
rc, kv, items, _ = gate(r, comments=[comment("rev", "q?", "2026-01-01T10:05:00Z")],
                        threads=[thread("rev", ("dev", "2026-01-01T10:10:00Z"), ("rev", "2026-01-01T10:15:00Z"),
                                        resolved=True, when="2026-01-01T10:00:00Z")])
check("an author reply in the middle of a thread counts as activity", kv.get("comments") == "none", str(items))
rc, kv, items, _ = gate(r, comments=[comment("claude[bot]", "## Review", kind="Bot")])
check("the Claude App's review comment is left to step 7", kv.get("comments") == "none", str(items))
rc, kv, items, _ = gate(r, comments=[comment("claude", "blocking: leaks the token")])
check("a human account named `claude` is not the Claude App", kv.get("comments") == "open" and items[0][1] == "claude", str(items))
rc, kv, items, _ = gate(r, comments=[comment("ci-bot[bot]", "coverage dropped", kind="Bot")])
check("another bot's comment is an open item", kv.get("comments") == "open" and items[0][1] == "ci-bot[bot]", str(items))
rc, kv, items, _ = gate(r, reviews=[review("rev", "COMMENTED", body="Should-fix: rename x")])
check("a review body after the author's last activity → open", kv.get("comments") == "open"
      and items[0][0] == "review-commented", str(items))
rc, kv, items, _ = gate(r, reviews=[review("rev", "COMMENTED", body="")])
check("an empty review body is not an item", kv.get("comments") == "none", str(items))
rc, kv, items, _ = gate(r, reviews=[review("rev", "APPROVED", body="LGTM, nice work")])
check("an approval's body is not an item", kv.get("comments") == "none", str(items))

rc, kv, items, _ = gate(r, threads=[thread("claude", kind="Bot")],
                        reviews=[review("claude[bot]", "COMMENTED", body="## Review", kind="Bot")])
check("the Claude App's inline threads and review bodies are left to step 7",
      kv.get("comments") == "none", str(items))
rc, kv, items, _ = gate(r, threads=[thread("claude", kind="User")])
check("… but not a human `claude`'s thread", kv.get("comments") == "open", str(items))
rc, kv, items, _ = gate(r, threads=[thread("claude", ("carol", "2026-01-03T00:00:00Z"), kind="Bot")])
check("a third party's reply in a Claude App thread is an item (step 7 never reads it)",
      kv.get("comments") == "open" and items[0][0] == "thread", str(items))
rc, kv, items, _ = gate(r, reviews=[review("rev", "COMMENTED", body="why?", when="2026-01-01T00:00:00Z"),
                                    review("dev", "COMMENTED", body="@claude", when="2026-01-02T00:00:00Z")])
check("a review that only triggers the bot is not an answer either",
      kv.get("comments") == "open" and items[0][1] == "rev", str(items))
rc, kv, items, _ = gate(r, comments=[comment("rev", "q?", "2026-01-01T00:00:56Z"),
                                     comment("dev", "answered", "2026-01-01T00:00:56.900Z")])
check("timestamps compare by time, whatever their precision", kv.get("comments") == "none", str(items))

evil = "line1\n\tline2 ‮evil​  " + "x" * 200
rc, kv, items, err = gate(r, comments=[comment("rev", evil)])
text = items[0][3] if items else ""
check("excerpts are flattened, stripped of format characters and cut",
      "\n" not in text and "‮" not in text and "​" not in text and " " not in text
      and text.startswith("line1 line2 evil") and text.endswith("…") and len(text) <= 101, repr(text) + err)

rh = make_repo("[merge]\nrequire_comments_addressed = true\n")
rc, kv, _, _ = gate(rh, threads=[thread("rev")])
check("require_comments_addressed → comments_hard=yes and exit 1", rc == 1 and kv.get("comments_hard") == "yes", str(kv))
rc, kv, _, _ = gate(r, threads=[thread("rev")])
check("default → comments_hard=no", kv.get("comments_hard") == "no", str(kv))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
