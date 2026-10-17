#!/usr/bin/env python3
"""followups.py — the Manager's durable record of a closed lane's open follow-ups.

A follow-up the Manager neither ran nor got an answer for used to live only in
the conversation: an unanswered "yes?" vanished with it. This keeps every such
item under one "## Open follow-ups" section of <main-repo>/tasks/ROADMAP.md
until it is resolved, so the next session (or the next summary) still sees it.

Usage:
  followups.py record  <main-repo> <task> <class> [--from-worker]   item text on stdin
  followups.py list    <main-repo> [<task>] [--class <class>]
  followups.py resolve <main-repo> <id>...

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
  record   id=F<n>  line=<the entry as written>
  list     open=<n>, then one item=<entry> per open item (oldest first)
  resolve  per id: resolved=F<n> line=<the entry removed>, or resolved=none id=F<n>

Exit 0 done, 1 resolve missed at least one id (the others are removed), 2
usage (bad args, empty text, bad task name), 4 the roadmap could not be read,
locked or written (a symlinked ROADMAP.md is refused). Record and resolve hold
an exclusive lock (tasks/.ROADMAP.lock) across read-modify-write. Writes are atomic (temp file +
rename); tasks/ is untracked by design, so nothing here commits.
"""
import contextlib
import datetime
import fcntl
import os
import re
import stat
import sys
import tempfile

SECTION = "## Open follow-ups"
CLASSES = ("ask", "deploy", "blocked")
TASK_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")
ENTRY_RE = re.compile(r"^- \[(F\d+)\] (\S+) · (\S+) · ")
MAX_TEXT = 300


def die(msg, code=2):
    print(f"followups.py: {msg}", file=sys.stderr)
    sys.exit(code)


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
    text = fold(sys.stdin.read())
    if text and from_worker:
        # A worker's self-close writes here with no Manager in between: mark it so
        # nobody later reads the line as the Manager's own vetted record.
        text = "(from worker) " + text
    if not text:
        die("empty item text (pass it on stdin)")
    path = roadmap(repo)
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
    print(f"id={fid}\nline={line}")


def cmd_list(repo, task=None, cls=None):
    lines = read_lines(roadmap(repo))
    items = []
    for _, l in entries(lines, section_span(lines)):
        m = ENTRY_RE.match(l)
        if (task is None or m.group(2) == task) and (cls is None or m.group(3) == cls):
            items.append(l)
    print(f"open={len(items)}")
    for l in items:
        print(f"item={l}")


def cmd_resolve(repo, fids):
    for fid in fids:
        if not re.match(r"^F\d+$", fid):
            die(f"invalid id: {fid!r}")
    path = roadmap(repo)
    missing = 0
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
                missing += 1
        if missing < len(fids):   # nothing matched → leave the file (or its absence) alone
            write_lines(path, lines)
    if missing:
        sys.exit(1)


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
    else:
        die("usage: followups.py {record <main-repo> <task> <class> [--from-worker] | list <main-repo> [<task>]"
            " [--class <class>] | resolve <main-repo> <id>...}")


if __name__ == "__main__":
    main(sys.argv)
