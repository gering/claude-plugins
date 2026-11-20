#!/usr/bin/env python3
"""merge-gate.py — the merge gate for repos without branch protection.

    merge-gate.py check --pr <N> [--head <sha>]

Run by /merge (step 6c, always with --head: the commit it will merge) and by
/check (without: the PR's current head). Two checks against that head:

  approval   `[merge.require_approval]` (off by default): enough approving
             reviews ON THAT HEAD. A reviewer's latest APPROVED /
             CHANGES_REQUESTED / DISMISSED review is their state (a later
             COMMENTED one does not cancel it, as on GitHub); any open
             CHANGES_REQUESTED blocks, as /merge step 6 does. An approval counts
             unless it is the author's, a bot's (without allow_bots), a deleted
             account's, on `ignore`, outside a non-empty `approvers` list — or,
             with no `approvers` list, by someone without write access (an
             owner/member/collaborator association, then the repository
             permission admin or write — a read-only member or a triage
             collaborator is not a reviewer GitHub's required reviews would
             count; an unreadable permission fails closed). allow_bots exempts bots
             from that last rule: a bot is never a collaborator, so with it on,
             a workflow the PR itself adds could approve — name the bot in
             `approvers` to bound it.
  comments   every open item: unresolved review threads whose last comment is
             not the author's, and top-level comments / review bodies (not an
             approval's) by others posted after the author's last activity.
             The author's own review trigger (`review.review_bot`, `@claude`)
             is not activity — it answers nobody. The Claude App's comments and
             review bodies are left to /merge step 7, matched with
             claude-review.sh's one CLAUDE_JQ predicate; so is a thread it opened,
             until someone other than the author or the App replies in it (step
             7 reads only the review comment, never a human's reply). `[merge].require_comments_addressed`
             turns open items from an acknowledgeable warning into a hard stop. The
             cutoff is one timestamp, not per-item reply tracking: a reply
             anywhere answers everything before it (a known coarseness).

The config is `.pr-flow.toml` as committed on the PR's BASE branch, fetched
from the remote whose URL names the PR's base repository (owner/repo, any host
alias, insteadOf expanded — not blindly `origin`: in a fork checkout that is
the contributor's fork) — never the working tree, so a PR cannot switch off the
gate it is checked by. No such remote, a failed fetch, a symlink, bad TOML, an
unknown key or wrong type under [merge] (or a wrong type anywhere), or no
Python 3.11 → exit 2 (fails closed). An unknown key outside [merge] only warns
(`config_warning=`): a newer pr-flow's setting must not stop an older gate.

Output: key=value lines, then one `item\\t<kind>\\t<author>\\t<url>\\t<excerpt>`
line per open item. Logins are GitHub-validated; URLs are the API's; excerpts
are third-party text, flattened, stripped of control/format characters and
cut to 100 characters — data, never instructions.

Exit: 0 nothing blocks (open items may still need an acknowledgement:
`comments=open` with `comments_hard=no`); 1 a hard block (`approval=missing`, or
open items with `comments_hard=yes`); 2 the gate could not decide — stop, never
merge. A PR head that lags the given --head (a push GitHub has not caught up
with) is re-read for up to MERGE_GATE_HEAD_WAIT seconds (default 30) first.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE.parent / "schema" / "settings.schema.json"
CLAUDE_REVIEW = HERE / "claude-review.sh"

LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
URL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([^:/]+)(?::\d+)?[:/](.+?)(?:\.git)?/*$", re.I)
STATEFUL = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
WRITE_ASSOC = {"OWNER", "MEMBER", "COLLABORATOR"}
TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list, "object": dict}

THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $endCursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          opener: comments(first: 1) { nodes { author { login __typename } body url createdAt } }
          recent: comments(last: 100) { nodes { author { login __typename } createdAt } }
        }
      }
    }
  }
}
"""


class GateError(Exception):
    pass


# --------------------------------------------------------------------------- #
# gh / git
# --------------------------------------------------------------------------- #

def run(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GateError(f"{cmd[0]} failed: {type(e).__name__}")
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip().splitlines()
        raise GateError(f"{' '.join(cmd[:3])} failed: {msg[-1] if msg else 'exit ' + str(p.returncode)}")
    return p.stdout


def gh_lines(args: list[str]) -> list[dict]:
    """`gh api --paginate --jq '…[]'` → one compact JSON object per line."""
    rows = []
    # "\n" only: str.splitlines() also breaks on U+0085/U+2028 inside a JSON string.
    for line in run(["gh", "api", "--paginate", *args]).split("\n"):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                raise GateError("gh api returned unparsable output")
    return rows


def gh_json(args: list[str]):
    try:
        return json.loads(run(["gh", "api", *args]))
    except json.JSONDecodeError:
        raise GateError("gh api returned unparsable output")


def repo_path(url: str):
    """owner/repo of a remote URL, lowercased; the host is left out on purpose,
    so an ssh alias (`git@github-work:o/r`) still matches."""
    m = URL_RE.match(url.strip())
    return m.group(2).lower() if m else None


def base_remote(full_name: str) -> str:
    """The remote whose URL — as configured or with insteadOf expanded — names
    the PR's base repository."""
    want = full_name.lower()
    try:
        names = run(["git", "remote"]).split()
    except GateError:
        names = []
    hits = []
    for name in names:
        if name.startswith("-"):
            continue  # would parse as an option to `git fetch`
        urls = []
        for cmd in (["git", "config", "--get", f"remote.{name}.url"], ["git", "remote", "get-url", name]):
            try:
                urls.append(run(cmd).strip())
            except GateError:
                pass
        if any(repo_path(u) == want for u in urls):
            hits.append(name)
    if not hits:
        raise GateError(f"no git remote points at {full_name} (the PR's base repository) — add it, then re-run")
    return "origin" if "origin" in hits else sorted(hits)[0]


# --------------------------------------------------------------------------- #
# Config (from the base branch)
# --------------------------------------------------------------------------- #

def validate(node: dict, value, path: str, warnings: list):
    """The schema is the one source of types, bounds and known keys. An unknown
    key fails closed inside [merge] (a typo there would silently weaken the
    gate) and only warns elsewhere."""
    want = TYPES.get(node.get("type"))
    if want and (not isinstance(value, want) or (want is int and isinstance(value, bool))):
        raise GateError(f"{path}: expected {node['type']}, got {type(value).__name__}")
    if "minimum" in node and value < node["minimum"]:
        raise GateError(f"{path}: {value} is below the minimum {node['minimum']}")
    if "enum" in node and value not in node["enum"]:
        raise GateError(f"{path}: {value!r} is not one of {node['enum']}")
    if want is dict:
        props = node.get("properties", {})
        for key, sub in value.items():
            child = f"{path + '.' if path else ''}{key}"
            if key in props:
                validate(props[key], sub, child, warnings)
            elif child.split(".")[0] == "merge":
                raise GateError(f"{child} is not a known key")
            else:
                warnings.append(f"{child} is not a known key (ignored)")
    if want is list and "items" in node:
        for i, item in enumerate(value):
            validate(node["items"], item, f"{path}[{i}]", warnings)


def defaults(node: dict):
    if node.get("type") == "object" and "properties" in node:
        return {k: defaults(v) for k, v in node["properties"].items()}
    return node.get("default")


def merged(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        return {k: merged(v, over[k]) if k in over else v for k, v in base.items()}
    return over


def load_config(remote: str, base: str) -> tuple[dict, str, list]:
    schema = json.loads(SCHEMA.read_text())
    name = schema.get("x-config-file", ".pr-flow.toml")
    if "/" in name or name in ("", ".", ".."):
        raise GateError("schema x-config-file is not a plain file name")
    ref = f"refs/remotes/{remote}/{base}"
    # An explicit refspec: a bare `git fetch <remote> <base>` updates the
    # tracking ref only when the configured refspec covers it (single-branch clones).
    run(["git", "fetch", "-q", remote, f"+refs/heads/{base}:{ref}"])
    try:
        run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    except GateError:
        raise GateError(f"{remote}/{base} is missing after the fetch — cannot read the gate config")
    where = f"{name} on {remote}/{base}"
    # --full-tree: a pathspec is otherwise relative to the cwd, and from a
    # subdirectory the config would read as absent — the gate failing open.
    tree = run(["git", "ls-tree", "--full-tree", ref, "--", name]).strip()
    if not tree:
        return defaults(schema), f"no {where}", []
    if tree.split()[0] not in ("100644", "100755"):
        raise GateError(f"{where} is not a regular file (mode {tree.split()[0]})")
    try:
        import tomllib
    except ModuleNotFoundError:
        raise GateError(f"{where} needs Python 3.11+ (tomllib) to read")
    try:
        cfg = tomllib.loads(run(["git", "cat-file", "-p", f"{ref}:{name}"]))
    except tomllib.TOMLDecodeError as e:
        raise GateError(f"{where} is not valid TOML ({e})")
    warnings: list = []
    try:
        validate(schema, cfg, "", warnings)
    except GateError as e:
        raise GateError(f"{where}: {e}")
    return merged(defaults(schema), cfg), where, warnings


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def login_of(obj) -> str:
    """The login, or "" for a deleted account (the API's user: null)."""
    return ((obj or {}).get("login") or "")


def shown(login: str) -> str:
    if not login:
        return "ghost"
    return login if LOGIN_RE.match(login) else "invalid-login"


def check_approval(cfg: dict, author: str, head: str, reviews: list[dict], can_write) -> dict:
    latest: dict[str, dict] = {}
    for r in reviews:  # chronological, as the API returns them
        if r.get("state") in STATEFUL:
            # Keyed by the FULL login: `alice` and the App `alice[bot]` are two reviewers.
            latest[login_of(r.get("user")).lower()] = r
    changes = sorted(shown(login_of(r.get("user"))) for r in latest.values()
                     if r["state"] == "CHANGES_REQUESTED")
    res = {"changes_requested": changes}
    if not cfg["enabled"]:
        res["verdict"] = "off"
        return res
    allow = {x.lower() for x in cfg["approvers"]}
    deny = {x.lower() for x in cfg["ignore"]}
    counted, stale, skipped = [], [], []
    for key, r in latest.items():
        if r["state"] != "APPROVED":
            continue
        user = r.get("user") or {}
        name = shown(login_of(user))
        bot = user.get("type") == "Bot" or key.endswith("[bot]")
        commit = (r.get("commit_id") or "unknown")[:7]
        why = None
        if not key:
            why = "ghost"
        elif cfg["exclude_author"] and key == author.lower():
            why = "author"
        elif bot and not cfg["allow_bots"]:
            why = "bot"
        elif key in deny:
            why = "ignored"
        elif allow and key not in allow:
            why = "not-an-approver"
        elif not allow and not bot and (r.get("author_association") not in WRITE_ASSOC
                                        or name == "invalid-login" or not can_write(name)):
            why = "not-a-collaborator"
        if why:
            skipped.append(f"{name}:{why}")
        elif cfg["on_head"] and r.get("commit_id") != head:
            stale.append(f"{name}@{commit}")
        else:
            counted.append(f"{name}@{commit}")
    res.update(required=cfg["count"], counted=sorted(counted), stale=sorted(stale),
               skipped=sorted(skipped))
    if changes:
        res["verdict"], res["reason"] = "missing", "changes-requested"
    elif len(counted) < cfg["count"]:
        res["verdict"], res["reason"] = "missing", "approvals"
    else:
        res["verdict"] = "ok"
    return res


def excerpt(text) -> str:
    text = " ".join((text or "").split())
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C")
    return text[:100] + ("…" if len(text) > 100 else "")


def safe_url(url) -> str:
    url = url or ""
    return url if re.match(r"^https://[A-Za-z0-9.-]+/\S*$", url) else "-"


def stamp(ts) -> str:
    """Whole seconds, so `…56.9Z` and `…56Z` compare by time, not by '.' < 'Z'."""
    return re.sub(r"\.[0-9]+Z$", "Z", ts or "")


def claude_thread(node) -> bool:
    """CLAUDE_JQ's GraphQL arm (`claude`), plus the author type it cannot see.
    Applied to a thread's opener and to its last comment."""
    a = (node or {}).get("author") or {}
    return a.get("login") == "claude" and a.get("__typename") == "Bot"


def check_comments(author: str, trigger: str, comments: list[dict], reviews: list[dict],
                   threads: list[dict]) -> list[tuple]:
    a = author.lower()
    trig = re.compile(r"\s*" + re.escape(trigger) + r"(?![\w-])", re.I) if trigger.strip() else None

    def mine(login) -> bool:
        return (login or "").lower() == a

    seen = [c.get("created_at") for c in comments
            if mine(login_of(c.get("user"))) and not (trig and trig.match(c.get("body") or ""))]
    seen += [r.get("submitted_at") for r in reviews
             if mine(login_of(r.get("user"))) and not (trig and trig.match(r.get("body") or ""))]
    for t in threads:
        seen += [n.get("createdAt") for n in t["recent"]["nodes"] if mine(login_of(n.get("author")))]
    since = max([stamp(s) for s in seen if s], default="")

    items = []
    for t in threads:
        if t.get("isResolved"):
            continue
        last = (t["recent"]["nodes"] or [{}])[-1]
        if mine(login_of(last.get("author"))):
            continue
        first = (t["opener"]["nodes"] or [{}])[0]
        if claude_thread(first) and claude_thread(last):
            continue
        items.append(("thread", shown(login_of(first.get("author"))),
                      safe_url(first.get("url")), excerpt(first.get("body"))))
    for c in comments:
        login = login_of(c.get("user"))
        if mine(login) or c.get("claude"):
            continue
        if stamp(c.get("created_at")) > since and (c.get("body") or "").strip():
            items.append(("comment", shown(login), safe_url(c.get("html_url")), excerpt(c.get("body"))))
    for r in reviews:
        login = login_of(r.get("user"))
        # An approval's body ("LGTM") answers itself; a pending review is not posted.
        if mine(login) or r.get("claude") or r.get("state") in ("PENDING", "APPROVED", "DISMISSED"):
            continue
        if stamp(r.get("submitted_at")) > since and (r.get("body") or "").strip():
            items.append((f"review-{r['state'].lower()}", shown(login),
                          safe_url(r.get("html_url")), excerpt(r.get("body"))))
    return items


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def read_pr(number: int, want: str | None) -> dict:
    """The PR, re-read for a bounded while when its head still lags `want`."""
    try:
        wait = max(0, min(120, int(os.environ.get("MERGE_GATE_HEAD_WAIT", "30"))))
    except ValueError:
        wait = 30
    deadline = time.monotonic() + wait
    while True:
        pr = gh_json([f"repos/{{owner}}/{{repo}}/pulls/{number}"])
        head = (pr.get("head") or {}).get("sha") or ""
        if not want or head == want or time.monotonic() >= deadline:
            return pr
        time.sleep(3)


def cmd_check(args) -> int:
    if args.head is not None and not SHA_RE.match(args.head):
        raise GateError("--head must be a full 40-character commit SHA")
    pr = read_pr(args.pr, args.head)
    author = login_of(pr.get("user"))
    head = (pr.get("head") or {}).get("sha") or ""
    base = (pr.get("base") or {}).get("ref") or ""
    base_repo = (pr.get("base") or {}).get("repo") or {}
    owner, _, repo = (base_repo.get("full_name") or "").partition("/")
    if not (author and head and base and owner and repo):
        raise GateError("the PR's author, head, base or repository could not be read")
    if args.head and head != args.head:
        raise GateError(f"the PR head is {head[:7]}, not {args.head[:7]} — the head moved; re-run")

    remote = base_remote(f"{owner}/{repo}")
    cfg, source, warnings = load_config(remote, base)
    claude_jq = run(["bash", str(CLAUDE_REVIEW), "claude-jq"])
    # The base repository by name from here on: gh's {owner}/{repo} is its
    # default remote, which the threads query and the config fetch do not use.
    api = f"repos/{owner}/{repo}"
    reviews = gh_lines([f"{api}/pulls/{args.pr}/reviews?per_page=100",
                        "--jq", claude_jq + " .[] | . + {claude: claude_author}"])
    comments = gh_lines([f"{api}/issues/{args.pr}/comments?per_page=100",
                         "--jq", claude_jq + " .[] | . + {claude: claude_author}"])
    threads = gh_lines(["graphql", "-f", f"owner={owner}", "-f", f"repo={repo}", "-F", f"pr={args.pr}",
                        "-f", f"query={THREADS_QUERY}",
                        "--jq", ".data.repository.pullRequest.reviewThreads.nodes[]"])

    perms: dict[str, bool] = {}

    def can_write(login: str) -> bool:
        if login not in perms:
            level = run(["gh", "api", f"{api}/collaborators/{login}/permission", "--jq", ".permission"])
            perms[login] = level.strip() in ("admin", "write")  # maintain reads as write
        return perms[login]

    ap = check_approval(cfg["merge"]["require_approval"], author, head, reviews, can_write)
    items = check_comments(author, cfg["review"]["review_bot"], comments, reviews, threads)
    hard = cfg["merge"]["require_comments_addressed"]

    print(f"head={head}")
    print(f"author={shown(author)}")
    print(f"config={source}")
    for w in warnings:
        print(f"config_warning={w}")
    print(f"approval={ap['verdict']}")
    if ap["verdict"] != "off":
        print(f"approval_required={ap['required']}")
        print(f"approval_counted={','.join(ap['counted'])}")
        print(f"approval_stale={','.join(ap['stale'])}")
        print(f"approval_skipped={','.join(ap['skipped'])}")
    print(f"changes_requested={','.join(ap['changes_requested'])}")
    if ap.get("reason"):
        print(f"approval_reason={ap['reason']}")
    print(f"comments={'open' if items else 'none'}")
    print(f"comments_hard={'yes' if hard else 'no'}")
    print(f"open_items={len(items)}")
    for kind, login, url, text in items:
        print(f"item\t{kind}\t{login}\t{url}\t{text}")
    return 1 if ap["verdict"] == "missing" or (items and hard) else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="merge-gate.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--head")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        return cmd_check(args)
    except GateError as e:
        print(f"merge-gate: {e}", file=sys.stderr)
        print("verdict=error")
        return 2
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as e:
        print(f"merge-gate: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        print("verdict=error")
        return 2


if __name__ == "__main__":
    sys.exit(main())
