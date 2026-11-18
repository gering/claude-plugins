#!/usr/bin/env python3
"""followups.py — the Manager's durable record of a closed lane's open follow-ups.

A follow-up the Manager neither ran nor got an answer for used to live only in
the conversation: an unanswered "yes?" vanished with it. This keeps every such
item until it is resolved, so the next session (or the next summary) still
sees it — and, where the backlog allows, in a place with history:

  github backend  one issue per item, labelled `work-system:followup` plus
                  `followup:<class>`; resolve closes it. The id is `#<n>`.
  local backend   one "## Open follow-ups" section of <main-repo>/tasks/ROADMAP.md,
                  ids `F<n>`. Committed by /close (archive-task.sh) only when the
                  file is already tracked: committing it once is the opt-in, so a
                  private roadmap is never published unasked.

The backend is task-store.py's own resolution (tasks.backend: auto|github|local),
imported, not copied, so follow-ups always live next to the tasks. A github
backend whose gh fails is exit 4, never a silent write to the local file, which
would split the follow-ups in two. A directory that is not a git repository is
local. On github, entries left in a local ROADMAP.md (from before a migration)
are still listed and resolvable by their F<n> id, but they never keep new
records in the file: under auto only local task files do (as they keep the
tasks), so a repo already on issues records issues at once.

An issue body is published: on github the text is scanned with migrate's
patterns (secrets always, local paths unless the repo is private) and a hit is
refused (exit 5), never redacted, so the Manager rewrites it in its own words.
A tracked ROADMAP.md gets the same scan, local paths included.

Usage:
  followups.py record  <main-repo> <task> <class> [--from-worker]   item text on stdin
  followups.py list    <main-repo> [<task>] [--class <class>]
  followups.py resolve <main-repo> <id>...   (quote "#<n>": a bare # starts a shell comment)
  followups.py scan    <main-repo>           (exit 5 when ROADMAP.md holds a secret/local path)

<class> says why the item is open:
  ask      awaits the user's yes (merge/push/release, another repo, destructive, unclear)
  deploy   a deploy awaiting the user's yes (deploy.confirm = ask)
  blocked  its predecessor awaits the user, so it was not run early

--from-worker marks an entry a worker wrote on its own self-close (no Manager
vetted it): the text is prefixed "(from worker)".

The text is the Manager's OWN description of the item — never a command or URL
copied from a worker's output (that text is untrusted). It goes in on stdin so
no quoting can break and no `$(...)` can run; it is folded to one line and
capped, so it can never start a heading or a second entry.

Output (key=value):
  record   backend=<github|local>  id=<#n|F<n>>  line=<the entry as written>
           (github: also url=, and existing=yes when an open issue already holds
           the same task, class and text — a re-run creates no duplicate)
  list     backend=, open=<n>, then one item=<entry> per open item (oldest first;
           on github the issues, then any F<n> left in the local file)
  resolve  per id: resolved=<id> line=<the entry removed>, or resolved=none id=<id>
           (an issue without the followup label, or one already closed, is none)

Exit 0 done, 1 resolve missed at least one id (the others are resolved), 2
usage (bad args, empty text, bad task name, a #n on the local backend), 4 the
backend could not be read or written (a symlinked ROADMAP.md is refused; gh
missing, unauthenticated or failing on github), 5 the text would publish a
secret or local identifier (nothing written). Local record and resolve hold an
exclusive lock (tasks/.ROADMAP.lock) across read-modify-write; writes are
atomic (temp file + rename). Nothing here commits.
"""
import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

SECTION = "## Open follow-ups"
CLASSES = ("ask", "deploy", "blocked")
TASK_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")
ENTRY_RE = re.compile(r"^- \[(F\d+)\] (\S+) · (\S+) · ")
ID_RE = re.compile(r"^(F\d+|#\d+)$")
MAX_TEXT = 300
LIST_LIMIT = 1000
MARKER_RE = re.compile(r"<!-- work-system followup: (\S+) (\S+) ([0-9a-f]{12}) -->")
HERE = os.path.dirname(os.path.abspath(__file__))


def die(msg, code=2):
    print(f"followups.py: {msg}", file=sys.stderr)
    sys.exit(code)


def task_store():
    spec = importlib.util.spec_from_file_location("task_store", os.path.join(HERE, "task-store.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TS = task_store()
LABEL = TS.FOLLOWUP_LABEL


def backend(repo):
    """('github', 'owner/name') or ('local', None) — task-store's resolution, so
    follow-ups go where the tasks go: under auto, local task files keep the
    file, and nothing else does. Leftover F<n> entries pin nothing (a repo on
    issues records issues at once); on github they are still listed and
    resolvable, so a close whose archive of the last task file flips auto
    between two of its records splits nothing out of sight."""
    if not os.path.isdir(repo):
        die(f"not a directory: {repo}")
    r = TS.run(["git", "-C", repo, "rev-parse", "--git-dir"])
    if r is None or r.returncode != 0:
        return "local", None   # no repository, so no origin to publish to
    try:
        b, _, info = TS.resolve(repo)
    except TS.Fail as e:
        die(f"{e} — nothing recorded", 4)
    return b, info.get("repo")


def leak_kinds(text, public):
    """migrate's patterns, one predicate for record time and commit time."""
    return sorted({kind for _, kind in TS.scan(text, public)})


def refuse_leaks(text, public):
    # Scan what was given AND what is published: line-anchored patterns (an
    # `API_TOKEN=...` line) no longer match once folding joined the lines, and
    # folding can join a split secret (`KEY=\nvalue`) into one that matches.
    hits = sorted(set(leak_kinds(text, public)) | set(leak_kinds(fold(text), public)))
    if hits:
        die(f"the text holds {', '.join(hits)} — it would be published; "
            "describe the item in your own words without it (nothing recorded)", 5)


def tracked(repo, path):
    """archive-task.sh's roadmap_tracked(): committed at HEAD (a file only staged,
    or `add -N`, is not), still in the index (`git rm --cached` opts out) and not
    a symlink."""
    rel = os.path.relpath(path, repo)
    if os.path.islink(path):
        return False
    r = TS.run(["git", "-C", repo, "cat-file", "-e", "HEAD:" + rel])
    i = TS.run(["git", "-C", repo, "ls-files", "--error-unmatch", "--", rel])
    return r is not None and r.returncode == 0 and i is not None and i.returncode == 0


def gh(args, repo, body=None):
    r = TS.run(["gh", *args, "-R", repo], input=body)
    if r is None:
        die("gh is not installed", 4)
    if r.returncode != 0:
        die(f"gh {args[0]} {args[1]} failed: {TS.oneline(r.stderr)[:300]}", 4)
    return r.stdout


def gh_followups(repo, strict=True):
    """strict (record's dedup): more than LIST_LIMIT is exit 4, since a truncated
    list could miss the marker. list passes strict=False: the first page plus a
    truncated=yes line, never nothing."""
    out = gh(["issue", "list", "--label", LABEL, "--state", "open", "--limit", str(LIST_LIMIT + 1),
              "--json", "number,title,body,labels,createdAt,url"], repo)
    try:
        issues = json.loads(out)
    except ValueError:
        die("gh issue list returned no JSON", 4)
    if len(issues) > LIST_LIMIT:
        if strict:
            die(f"more than {LIST_LIMIT} open {LABEL} issues — close some before recording more", 4)
        print("truncated=yes")
        issues = issues[:LIST_LIMIT]
    return sorted(issues, key=lambda i: i["number"])


def issue_entry(i):
    """The issue as a roadmap-shaped line, so callers read one format."""
    body = i.get("body") or ""
    found = MARKER_RE.findall(body)   # the last one is ours: create appends it
    m = found[-1] if found else None
    labels = [l["name"] for l in i.get("labels", [])]
    task = m[0] if m else "?"
    cls = m[1] if m else next((l.split(":", 1)[1] for l in labels
                                     if l.startswith("followup:")), "?")
    text = fold(body.split("\n", 1)[0]) or fold(i.get("title", ""))
    since = (i.get("createdAt") or "")[:10] or "?"
    return task, cls, f"- [#{i['number']}] {task} · {cls} · since {since} — {text}"


def roadmap(repo):
    if not os.path.isdir(repo):
        die(f"not a directory: {repo}")
    return os.path.join(repo, "tasks", "ROADMAP.md")


def read_lines(path):
    # A symlinked roadmap is refused, not followed: the rename below would
    # replace the link with a regular file holding whatever it pointed at.
    if os.path.islink(path):
        die(f"{path} is a symlink — refusing to read or rewrite it", 4)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as e:
        die(f"could not read {path}: {getattr(e, 'strerror', None) or e}", 4)


def write_lines(path, lines):
    d = os.path.dirname(path)
    tmp = None
    try:
        os.makedirs(d, exist_ok=True)
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            mode = 0o644
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".ROADMAP.")
        os.fchmod(fd, mode)   # mkstemp's 0600 would otherwise stick to the roadmap
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        die(f"could not write {path}: {e.strerror}", 4)


@contextlib.contextmanager
def locked(path):
    """Exclusive lock across read-modify-write: two closes recording at once
    would otherwise both read the same counter, hand out one id twice, and the
    later rename would drop the earlier entry."""
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
        fd = os.open(os.path.join(d, ".ROADMAP.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        die(f"could not lock {path}: {e.strerror}", 4)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def section_span(lines):
    """(header index, end) of the section; end is the next heading or EOF."""
    for i, line in enumerate(lines):
        if line.strip() == SECTION:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#"):
                j += 1
            return i, j
    return None


def entries(lines, span):
    if span is None:
        return []
    return [(k, lines[k]) for k in range(span[0] + 1, span[1]) if ENTRY_RE.match(lines[k])]


def ensure_section(lines):
    """Create the section if missing: before the first `## ` heading (so it is
    the first section a reader sees, and the title's own prose stays above it),
    else at the end. Returns its span."""
    span = section_span(lines)
    if span is not None:
        return span
    if not lines:
        lines.append("# Roadmap")
    at = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
    block = [SECTION, ""]
    if at > 0 and lines[at - 1].strip():
        block.insert(0, "")
    lines[at:at] = block
    return section_span(lines)


COUNTER_RE = re.compile(r"^<!-- followups next=(\d+) -->$")


def next_id(lines, span):
    """The section's own counter marker is the one source of truth; entry ids
    only floor it (a hand-edited marker can never make an id repeat). Ids of
    resolved items are never handed out again, so a stale resolve misses."""
    nxt = 1
    for k in range(span[0] + 1, span[1]):
        m = COUNTER_RE.match(lines[k])
        if m:
            nxt = max(nxt, int(m.group(1)))
    for _, l in entries(lines, span):
        nxt = max(nxt, int(ENTRY_RE.match(l).group(1)[1:]) + 1)
    return nxt


def set_counter(lines, nxt):
    span = section_span(lines)
    marker = f"<!-- followups next={nxt} -->"
    for k in range(span[0] + 1, span[1]):
        if COUNTER_RE.match(lines[k]):
            lines[k] = marker
            return
    lines.insert(span[0] + 1, marker)


def fold(text):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def cmd_record(repo, task, cls, from_worker=False):
    if not TASK_RE.match(task):
        die(f"invalid task name: {task!r}")
    if cls not in CLASSES:
        die(f"class must be one of {'|'.join(CLASSES)}")
    raw = sys.stdin.read()
    text = fold(raw)
    if text and from_worker:
        # A worker's self-close writes here with no Manager in between: mark it so
        # nobody later reads the line as the Manager's own vetted record.
        text = "(from worker) " + text
    if not text:
        die("empty item text (pass it on stdin)")
    b, gh_repo = backend(repo)
    if b == "github":
        return record_issue(gh_repo, task, cls, text, raw)
    path = roadmap(repo)
    if tracked(repo, path):
        refuse_leaks(f"{task}\n{raw}", True)   # a tracked roadmap is pushed with the next close
    with locked(path):
        lines = read_lines(path)
        span = ensure_section(lines)
        n = next_id(lines, span)
        fid = f"F{n}"
        line = f"- [{fid}] {task} · {cls} · since {datetime.date.today().isoformat()} — {text}"
        end = span[1]
        while end > span[0] + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = [line]
        set_counter(lines, n + 1)
        write_lines(path, lines)
    print(f"backend=local\nid={fid}\nline={line}")


def record_issue(repo, task, cls, text, raw):
    try:
        public = TS.repo_visibility(repo) != "private"
    except TS.Fail as e:
        die(f"{e} — nothing recorded", 4)
    refuse_leaks(f"{task}\n{raw}", public)   # the task name is published too
    # The marker is the identity: a close interrupted after the create (or a
    # create whose reply was lost) re-runs into the same issue, not a second one.
    digest = hashlib.sha256(f"{task}\0{cls}\0{text}".encode()).hexdigest()[:12]
    marker = f"<!-- work-system followup: {task} {cls} {digest} -->"
    for i in gh_followups(repo):
        # Compare the LAST marker (the one create appends), as issue_entry reads
        # it: a marker quoted inside another item's text must not match.
        found = MARKER_RE.findall(i.get("body") or "")
        if found and found[-1] == (task, cls, digest):
            print(f"backend=github\nid=#{i['number']}\nexisting=yes\nurl={i.get('url', '')}"
                  f"\nline={issue_entry(i)[2]}")
            return
    try:
        TS.ensure_label(repo, LABEL, "c5def5", "Open follow-up of a closed work-system task")
        TS.ensure_label(repo, f"followup:{cls}", "ededed", f"work-system follow-up: {cls}")
    except TS.Fail as e:
        die(f"{e} — nothing recorded", 4)
    title = f"Follow-up ({task}): " + (text if len(text) <= 80 else text[:79] + "…")
    body = (f"{text}\n\nOpen follow-up the work-system Manager recorded when task `{task}` "
            f"closed (class: {cls}). Close this issue once it is done.\n\n{marker}\n")
    out = gh(["issue", "create", "--title", title, "--label", LABEL, "--label", f"followup:{cls}",
              "--body-file", "-"], repo, body).strip()
    url = out.splitlines()[-1] if out else ""
    n = url.rstrip("/").rsplit("/", 1)[-1]
    if not n.isdigit():
        die("gh issue create printed no issue URL — run record again: the marker finds "
            "the issue if it was created", 4)
    line = f"- [#{n}] {task} · {cls} · since {datetime.date.today().isoformat()} — {text}"
    print(f"backend=github\nid=#{n}\nurl={url}\nline={line}")


def local_items(repo, task=None, cls=None):
    lines = read_lines(roadmap(repo))
    items = []
    for _, l in entries(lines, section_span(lines)):
        m = ENTRY_RE.match(l)
        if (task is None or m.group(2) == task) and (cls is None or m.group(3) == cls):
            items.append(l)
    return items


def cmd_list(repo, task=None, cls=None):
    b, gh_repo = backend(repo)
    items = []
    if b == "github":
        for i in gh_followups(gh_repo, strict=False):
            t, c, line = issue_entry(i)
            if (task is None or t == task) and (cls is None or c == cls):
                items.append(line)
    items += local_items(repo, task, cls)   # on github: entries from before a migration
    print(f"backend={b}\nopen={len(items)}")
    for l in items:
        print(f"item={l}")


def resolve_issue(repo, fid):
    """True when the open follow-up issue was closed; never closes a task issue."""
    r = TS.run(["gh", "issue", "view", fid[1:], "--json", "number,state,labels,body,title,createdAt",
                "-R", repo])
    if r is None:
        die("gh is not installed", 4)
    if r.returncode != 0:
        if "could not resolve to an issue" in r.stderr.lower():
            print(f"resolved=none id={fid}")
            return False
        die(f"gh issue view failed: {TS.oneline(r.stderr)[:300]}", 4)
    try:
        i = json.loads(r.stdout)
    except ValueError:
        die("gh issue view returned no JSON", 4)
    if LABEL not in [l["name"] for l in i.get("labels", [])] or i.get("state") != "OPEN":
        print(f"resolved=none id={fid}")
        return False
    gh(["issue", "close", fid[1:], "--reason", "completed",
        "--comment", "Resolved by the work-system Manager."], repo)
    print(f"resolved={fid}\nline={issue_entry(i)[2]}")
    return True


def cmd_resolve(repo, ids):
    for fid in ids:
        if not ID_RE.match(fid):
            die(f"invalid id: {fid!r}")
    issues = [i for i in ids if i.startswith("#")]
    fids = [i for i in ids if i.startswith("F")]
    # F<n> ids live in the local file on every backend: resolving only those
    # needs no gh, so an outage never blocks clearing the file.
    b, gh_repo = backend(repo) if issues else ("local", None)
    if issues and b != "github":
        die(f"{issues[0]} names an issue, but this repo's follow-ups are local (F<n> ids)")
    missing = sum(not resolve_issue(gh_repo, i) for i in issues)
    if fids:
        path = roadmap(repo)
        miss_local = 0
        with locked(path):
            lines = read_lines(path)
            for fid in fids:
                hit = [(k, l) for k, l in entries(lines, section_span(lines))
                       if ENTRY_RE.match(l).group(1) == fid]
                if hit:
                    del lines[hit[0][0]]
                    print(f"resolved={fid}\nline={hit[0][1]}")
                else:
                    print(f"resolved=none id={fid}")
                    miss_local += 1
            if miss_local < len(fids):   # nothing matched → leave the file (or its absence) alone
                write_lines(path, lines)
        missing += miss_local
    if missing:
        sys.exit(1)


def cmd_scan(repo):
    """Exit 5 when the roadmap file holds a secret or a local path: archive-task.sh
    runs this before it commits a tracked roadmap, hand edits included."""
    path = roadmap(repo)
    hits = leak_kinds("\n".join(read_lines(path)), True)
    if hits:
        die(f"{path} holds {', '.join(hits)} — not committed; remove it from the file", 5)
    print("scan=clean")


def main(argv):
    args = argv[1:]
    cls = None
    if args[:1] == ["list"] and "--class" in args:
        i = args.index("--class")
        if i + 1 >= len(args) or args[i + 1] not in CLASSES:
            die(f"--class needs one of {'|'.join(CLASSES)}")
        cls = args[i + 1]
        del args[i:i + 2]
    from_worker = args[:1] == ["record"] and "--from-worker" in args
    if from_worker:
        args.remove("--from-worker")
    if args[:1] == ["record"] and len(args) == 4:
        cmd_record(args[1], args[2], args[3], from_worker)
    elif args[:1] == ["list"] and len(args) in (2, 3):
        cmd_list(args[1], args[2] if len(args) == 3 else None, cls)
    elif args[:1] == ["resolve"] and len(args) >= 3:
        cmd_resolve(args[1], args[2:])
    elif args[:1] == ["scan"] and len(args) == 2:
        cmd_scan(args[1])
    else:
        die("usage: followups.py {record <main-repo> <task> <class> [--from-worker] | list <main-repo> [<task>]"
            " [--class <class>] | resolve <main-repo> <id>... | scan <main-repo>}")


if __name__ == "__main__":
    main(sys.argv)
