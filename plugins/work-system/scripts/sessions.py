#!/usr/bin/env python3
"""sessions.py — which running herdr sessions still run old plugin code, and
bringing the idle ones up to date with `/reload-plugins`. Run it through
sessions.sh, which hands over the shared composer draft check
($HERDR_COMPOSER_PY from herdr-agent.sh); without it nothing is ever typed.

Usage:
  sessions.sh inventory [--json]
  sessions.sh refresh [--workspace <id> | --all [--yes --only <pane@pid,...>]] [--wait-ms <n>] [--json]

inventory (read-only) judges every herdr agent against the effective install
of each plugin of the marketplace (SESSIONS_MARKETPLACE, default
gering-plugins) for its cwd. The text output is a summary line, then one row
per session (verdict, workspace/tab, pane, agent, age, action) plus its
non-current plugins; --json adds cwd, pid, start, reload time and every
plugin's effective install. Verdicts:
  current  the session started (or was reloaded by this tool) after the
           install's lastUpdated, and the install is the marketplace's newest
  stale    reload: the install changed after the session started
           install: the effective install is older than the marketplace —
                    a reload does not help until it is updated
  unknown  anything that cannot be decided (no start time, no lastUpdated, a
           version that does not parse, competing records): never `current`
  n/a      not a claude agent (codex, grok, kimi): never refreshed
An unreadable installed_plugins.json makes every claude session `unknown`.
It also prints the install audit: installs of any scope lagging the
marketplace, and project/local records whose projectPath is gone, each with
the `claude plugin` command that would fix it (every field shell-quoted).
Nothing is run.

Effective install for a cwd: local > project > user > managed, the order
`claude plugin update` documents for picking a scope. A project/local record
matches when its projectPath is the cwd or, for a git worktree, the main
checkout's root (worktrees share the main checkout's project installs). Two
matching records of one scope that disagree, or a record that only matches an
ancestor of the cwd, make that plugin `unknown`. A row with any install-stale
plugin gets the action `update install` even if another plugin only needs a
reload: the update comes first.

refresh types the fixed string `/reload-plugins` into each claude session of
the target that is `stale` for a reload reason. Default target: this herdr
workspace; --all is every workspace and, without --yes, only prints the
targets (confirm=required) so the caller can ask first; the confirmed run is
--all --yes --only <the pane@pid tokens it printed>, so neither a session that
turned stale after the question nor a new claude in a confirmed pane is typed
into. With no targets it prints targets=none. Per session: wait
until idle/done/blocked (bounded by --wait-ms, default 30000), re-check the
status and that the pane still runs the inventoried claude pid, require a clear composer
(no user draft) right before sending, send, then confirm from the pane that
the output after the pre-send transcript ends in /reload-plugins with a
"Reloaded:" summary after it (a reply asking for --force is reported as
`partial` and not recorded). Busy, blocked, drafted, non-claude
and own-pane ($HERDR_PANE_ID) sessions are skipped, never interrupted. A
confirmed reload is recorded per pane and claude pid in SESSIONS_STATE_FILE
(default ${XDG_STATE_HOME:-~/.local/state}/work-system/session-reloads.json),
so the next inventory counts it.

Exit: 0 done (results are in the output), 2 usage, 3 refresh outside herdr or
herdr unreachable. Test seams (env): SESSIONS_INSTALLED_FILE,
SESSIONS_MARKETPLACE_FILE, SESSIONS_STATE_FILE, SESSIONS_VERIFY_SECS,
SESSIONS_POLL_MS, SESSIONS_READ_LINES; herdr and ps come from PATH.
"""
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
HERDR_AGENT = os.path.join(HERE, "herdr-agent.sh")
RELOAD = "/reload-plugins"
SUMMARY_RE = re.compile(r"^\s*⎿\s+Reloaded:")
SCOPE_ORDER = ("local", "project", "user", "managed")
CALL_TIMEOUT = 15
# Claude Code writes a worktree's `local` install record during its own startup
# (observed ~1s after the process starts), so a just-created record this close
# after the session start is that write, not an update the session missed. Kept
# short: a real install inside the window would read `current`.
STARTUP_GRACE = 10
_VERIFY_ENV = os.environ.get("SESSIONS_VERIFY_SECS", "")
VERIFY_SECS = int(_VERIFY_ENV) if _VERIFY_ENV.isdigit() else 20
# The pause between transcript looks while a reload is verified. A test seam: a
# stub pane renders at once, and a 1 s poll per pane made the test minutes long.
# It can only shorten the pause, so it never stretches the VERIFY_SECS bound, and
# it never drops under 50 ms, so a leaked value cannot spin herdr in a busy loop.
_POLL_ENV = os.environ.get("SESSIONS_POLL_MS", "")
POLL_SECS = (max(50, min(int(_POLL_ENV), 1000)) / 1000
             if _POLL_ENV.isascii() and _POLL_ENV.isdigit() and len(_POLL_ENV) <= 4 else 1)


def usage(msg=""):
    if msg:
        sys.stderr.write("sessions: %s\n" % msg)
    sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
    sys.exit(2)


def clean(s):
    # herdr fields (labels, titles, cwd, pane text) are settable by any process in
    # a pane: every control/format char (Unicode category C*) and line/paragraph
    # separator (Zl/Zp) becomes a space.
    def bad(c):
        cat = unicodedata.category(c)
        return cat[0] == "C" or cat in ("Zl", "Zp")
    return "".join(" " if bad(c) else c for c in str(s or ""))


def run(argv, stdin=None, timeout=CALL_TIMEOUT):
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, errors="replace",
                           timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def parse_ver(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d+(\.\d+)*", v):
        return None
    return tuple(int(x) for x in v.split("."))


def ver_lt(a, b):
    # 1.25 == 1.25.0: pad to equal length before comparing.
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) < b + (0,) * (n - len(b))


def parse_iso(s):
    if not isinstance(s, str):
        return None
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    return d.timestamp()


def iso(ts):
    if ts is None:
        return ""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_etime(s):
    # ps etime: [[dd-]hh:]mm:ss
    m = re.fullmatch(r"\s*(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)\s*", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


# ---- inputs -------------------------------------------------------------------

def marketplace_name():
    return os.environ.get("SESSIONS_MARKETPLACE") or "gering-plugins"


def installed_records(mkt):
    path = os.environ.get("SESSIONS_INSTALLED_FILE") or os.path.expanduser(
        "~/.claude/plugins/installed_plugins.json")
    d = load_json(path)
    plugins = d.get("plugins") if isinstance(d, dict) else None
    out = {}
    if not isinstance(plugins, dict):
        return None
    for pid, recs in plugins.items():
        if not pid.endswith("@" + mkt) or not isinstance(recs, list):
            continue
        out[pid] = [r for r in recs if isinstance(r, dict)]
    return out


def marketplace_versions(mkt):
    path = os.environ.get("SESSIONS_MARKETPLACE_FILE")
    if not path:
        known = load_json(os.path.expanduser("~/.claude/plugins/known_marketplaces.json")) or {}
        entry = known.get(mkt) if isinstance(known, dict) else None
        loc = entry.get("installLocation") if isinstance(entry, dict) else None
        loc = loc if isinstance(loc, str) else None
        path = os.path.join(loc, ".claude-plugin", "marketplace.json") if loc else ""
    d = load_json(path) if path else None
    out = {}
    plugins = d.get("plugins") if isinstance(d, dict) else None
    for p in plugins if isinstance(plugins, list) else []:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            out[p["name"] + "@" + mkt] = p.get("version")
    return out


def state_path():
    p = os.environ.get("SESSIONS_STATE_FILE")
    if p:
        return p
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "work-system", "session-reloads.json")


def load_state():
    d = load_json(state_path())
    return d if isinstance(d, dict) else {}


def record_reload(pane, pid, ts):
    """Empty string on success, else the error: a failed write must not end the refresh."""
    path = state_path()
    st = load_state()
    st[pane] = {"pid": pid, "reloaded_at": iso(ts)}
    tmp = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".session-reloads.")
        with os.fdopen(fd, "w") as f:
            json.dump(st, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return "%s: %s" % (type(e).__name__, e.strerror or e)
    return ""


def herdr_json(argv):
    rc, out = run(argv)
    if rc != 0:
        return None
    try:
        r = json.loads(out).get("result")
    except (ValueError, AttributeError):
        return None
    return r if isinstance(r, dict) else None


def agents_list():
    rc, out = run(["bash", HERDR_AGENT, "list"])
    if rc != 0:
        return None
    try:
        a = json.loads(out)["result"]["agents"]
    except (ValueError, KeyError, TypeError):
        return None
    return [x for x in a if isinstance(x, dict)]


def labels():
    tabs, wss = {}, {}
    r = herdr_json(["herdr", "tab", "list"]) or {}
    for t in r.get("tabs") or []:
        if isinstance(t, dict):
            tabs[t.get("tab_id")] = t.get("label")
    r = herdr_json(["herdr", "workspace", "list"]) or {}
    for w in r.get("workspaces") or []:
        if isinstance(w, dict):
            wss[w.get("workspace_id")] = w.get("label")
    return tabs, wss


def claude_process(pane):
    """(pid, start_ts, cwd) of the pane's foreground claude, or Nones."""
    r = herdr_json(["herdr", "pane", "process-info", "--pane", pane]) or {}
    info = r.get("process_info") if isinstance(r.get("process_info"), dict) else {}
    pgid = info.get("foreground_process_group_id")
    if not isinstance(pgid, int):
        return None, None, None
    procs = info.get("foreground_processes")
    for p in procs if isinstance(procs, list) else []:
        if not isinstance(p, dict) or p.get("pid") != pgid:
            continue
        argv = p.get("argv") if isinstance(p.get("argv"), list) else []
        names = {os.path.basename(str(p.get("argv0") or ""))}
        if argv:
            names.add(os.path.basename(str(argv[0])))
        if "claude" not in names:
            return None, None, None
        rc, out = run(["ps", "-o", "etime=", "-p", str(pgid)])
        secs = parse_etime(out) if rc == 0 else None
        start = time.time() - secs if secs is not None else None
        return pgid, start, p.get("cwd") if isinstance(p.get("cwd"), str) else None
    return None, None, None


def main_root(cwd):
    """The main checkout's root when cwd IS a checkout's top level (the main one
    or a linked worktree), else None: a session in a subdir inherits nothing."""
    rc, out = run(["git", "-C", cwd, "rev-parse", "--path-format=absolute",
                   "--show-toplevel", "--git-common-dir"], timeout=5)
    parts = out.strip().split("\n")
    if rc != 0 or len(parts) != 2 or os.path.basename(parts[1]) != ".git":
        return None
    if os.path.realpath(parts[0]) != os.path.realpath(cwd):
        return None
    return os.path.realpath(os.path.dirname(parts[1]))


# ---- the verdict --------------------------------------------------------------

def rp(p):
    return os.path.realpath(p) if isinstance(p, str) and p else None


def newest_of(recs):
    # Records naming the same install may still differ in lastUpdated: the
    # latest one is the update a session has to have seen.
    return max(recs, key=lambda r: parse_iso(r.get("lastUpdated")) or 0)


def effective(recs, cwd, root):
    """(record, None) or (None, reason). reason 'not-installed' = no record at all."""
    for scope in SCOPE_ORDER:
        of = [r for r in recs if r.get("scope") == scope]
        if not of:
            continue
        if scope in ("user", "managed"):
            if len({(r.get("installPath"), r.get("version")) for r in of}) > 1:
                return None, "competing %s records" % scope
            return newest_of(of), None
        exact = [r for r in of if cwd and rp(r.get("projectPath")) == cwd]
        via_main = [r for r in of if root and root != cwd and rp(r.get("projectPath")) == root]
        hit = exact + via_main
        if hit:
            if len({(r.get("installPath"), r.get("version")) for r in hit}) > 1:
                return None, "competing %s records" % scope
            return newest_of(hit), None
        if cwd and any(rp(r.get("projectPath")) and cwd.startswith(rp(r.get("projectPath")).rstrip("/") + "/")
                       for r in of):
            return None, "%s record only for a parent dir" % scope
    return None, "not-installed"


def plugin_verdict(pid, rec, why, newest, since, grace=False):
    """grace: allow STARTUP_GRACE — only for the session's own `local` record,
    judged against the process start (never against a recorded reload)."""
    row = {"plugin": pid, "scope": "", "version": "", "newest": newest or "",
           "last_updated": "", "verdict": "unknown", "reason": why or ""}
    if rec is None:
        return row
    row.update(scope=rec.get("scope") or "", version=rec.get("version") or "",
               last_updated=rec.get("lastUpdated") or "")
    v, nv = parse_ver(rec.get("version")), parse_ver(newest)
    if v and nv and ver_lt(v, nv):
        row.update(verdict="stale", reason="install")
        return row
    lu = parse_iso(rec.get("lastUpdated"))
    if since is None:
        row["reason"] = "session start unknown"
    elif lu is None:
        row["reason"] = "no lastUpdated"
    elif since + (STARTUP_GRACE if grace else 0) < lu:
        row.update(verdict="stale", reason="reload")
    elif not (v and nv):
        row["reason"] = "version not comparable"
    else:
        row.update(verdict="current", reason="")
    return row


def session_rows(agents, records, newest, state, proc=claude_process):
    tabs, wss = labels()
    me = os.environ.get("HERDR_PANE_ID") or ""
    rows = []
    for a in agents:
        pane = str(a.get("pane_id") or "")
        kind = str(a.get("agent") or "")
        row = {"workspace": a.get("workspace_id") or "", "workspace_label": wss.get(a.get("workspace_id")) or "",
               "tab": a.get("tab_id") or "", "tab_label": tabs.get(a.get("tab_id")) or "",
               "pane": pane, "agent": kind, "status": a.get("agent_status") or "",
               "cwd": a.get("cwd") or "", "pid": None, "started_at": "", "reloaded_at": "",
               "self": bool(me) and pane == me, "plugins": [], "verdict": "n/a", "action": "none",
               "reason": ""}
        rows.append(row)
        if kind != "claude":
            continue
        pid, start, pcwd = proc(pane) if pane else (None, None, None)
        cwd = os.path.realpath(pcwd or row["cwd"]) if (pcwd or row["cwd"]) else ""
        row.update(pid=pid, started_at=iso(start), cwd=cwd or row["cwd"])
        since = start
        rec = state.get(pane) if isinstance(state.get(pane), dict) else None
        if rec and pid is not None and rec.get("pid") == pid:
            r_ts = parse_iso(rec.get("reloaded_at"))
            if r_ts is not None and since is not None:
                since = max(since, r_ts)
                row["reloaded_at"] = rec.get("reloaded_at")
        if records is None:
            # No readable registry: nothing can be decided, so nothing is current.
            row["verdict"], row["action"], row["reason"] = "unknown", "check", "install registry unreadable"
            continue
        root = main_root(cwd) if cwd else None
        for plugin in sorted(records):
            erec, why = effective(records[plugin], cwd, root)
            if why == "not-installed":
                continue
            # Only a record Claude Code just created (installedAt == lastUpdated):
            # an update written in the same minute changes lastUpdated alone.
            grace = (since == start and erec is not None and erec.get("scope") == "local"
                     and rp(erec.get("projectPath")) == cwd
                     and erec.get("installedAt") == erec.get("lastUpdated"))
            row["plugins"].append(plugin_verdict(plugin, erec, why, newest.get(plugin), since, grace))
        vs = [p["verdict"] for p in row["plugins"]]
        if "stale" in vs:
            row["verdict"] = "stale"
            # A stale install comes first: reloading would load the same old code
            # for that plugin, and the skill only offers the update on this action.
            if any(p["reason"] == "install" for p in row["plugins"]):
                row["action"] = "update install (reload does not help: install is stale)"
            elif row["self"]:
                row["action"] = "self: run /reload-plugins here"
            else:
                row["action"] = "reload"
        elif "unknown" in vs:
            row["verdict"], row["action"] = "unknown", "check"
        else:
            row["verdict"] = "current"
    return rows


def audit(records, newest):
    out = []
    for plugin in sorted(records):
        nv = parse_ver(newest.get(plugin))
        for r in records[plugin]:
            scope, path = r.get("scope"), r.get("projectPath")
            if scope not in SCOPE_ORDER:
                continue
            v = parse_ver(r.get("version"))

            if plugin.startswith("-"):
                # An id a CLI would read as an option flag: report, suggest nothing.
                out.append({"plugin": plugin, "scope": scope, "project": path or "",
                            "version": r.get("version") or "", "newest": newest.get(plugin) or "",
                            "kind": "unsafe", "command": "",
                            "note": "plugin id starts with '-': fix installed_plugins.json by hand"})
                continue

            def base(verb):
                return " ".join(["claude", "plugin", verb, shlex.quote(plugin), "--scope", scope])
            entry = {"plugin": plugin, "scope": scope, "project": path or "",
                     "version": r.get("version") or "", "newest": newest.get(plugin) or ""}
            if scope in ("project", "local") and isinstance(path, str) and not os.path.isdir(path):
                entry.update(kind="orphan", command="cd -- %s && %s" % (shlex.quote(path), base("uninstall")),
                             note="projectPath is gone; the command must run from it")
            elif v and nv and ver_lt(v, nv):
                cmd = base("update")
                if scope in ("project", "local") and isinstance(path, str):
                    cmd = "cd -- %s && %s" % (shlex.quote(path), cmd)
                entry.update(kind="lagging", command=cmd, note="")
            else:
                continue
            out.append(entry)
    return out


def gather():
    mkt = marketplace_name()
    records = installed_records(mkt)
    newest = marketplace_versions(mkt)
    herdr = os.environ.get("HERDR_ENV") == "1"
    agents = agents_list() if herdr else None
    return mkt, records, newest, herdr, agents


# ---- output ------------------------------------------------------------------

def age(started):
    ts = parse_iso(started)
    if ts is None:
        return "?"
    m = int(max(0, time.time() - ts) // 60)
    return "%dd%02dh" % (m // 1440, m // 60 % 24) if m >= 1440 else "%dh%02dm" % (m // 60, m % 60)


def summarize(rows, records, mkt):
    n = {"sessions": len(rows), "stale_reload": 0, "stale_install": 0, "self_stale": 0,
         "unknown": 0, "current": 0, "n/a": 0, "registry_ok": records is not None,
         "marketplace": mkt, "marketplace_installs": bool(records)}
    for r in rows:
        if r["verdict"] != "stale":
            n[r["verdict"]] = n.get(r["verdict"], 0) + 1
        elif r["action"].startswith("update install"):
            n["stale_install"] += 1
        elif r["self"]:
            n["self_stale"] += 1
        else:
            n["stale_reload"] += 1
    return n


def print_inventory(rows, aud, herdr, agents, summary):
    if not summary["registry_ok"]:
        print("sessions: installed_plugins.json unreadable — every claude session is unknown")
    elif not summary["marketplace_installs"]:
        print("sessions: no installs from marketplace %s — nothing to judge (set SESSIONS_MARKETPLACE?)"
              % clean(summary["marketplace"]))
    if not herdr:
        print("sessions: not inside herdr — no session list, install audit only")
    elif agents is None:
        print("sessions: unverified — herdr agent list unreachable")
    else:
        print("summary: %(sessions)d sessions · stale: %(stale_reload)d reload, %(stale_install)d install, "
              "%(self_stale)d own pane · unknown %(unknown)d · current %(current)d · n/a %(n/a)d" % summary)
    for r in rows:
        where = "%s/%s" % (clean(r["workspace_label"] or r["workspace"]), clean(r["tab_label"] or r["tab"]))
        print("%-8s %-44s %-8s %-7s %-6s %s" % (r["verdict"], where[:44], clean(r["pane"]), clean(r["agent"]),
                                              age(r["started_at"]) if r["agent"] == "claude" else "",
                                              r["action"] if r["action"] != "none" else ""))
        if r["reason"]:
            print("         %s" % r["reason"])
        for p in r["plugins"]:
            if p["verdict"] != "current":
                print("         %s %s@%s (newest %s): %s %s" % (clean(p["plugin"].split("@")[0]), clean(p["scope"]) or "?",
                                                               clean(p["version"]) or "?", clean(p["newest"]) or "?",
                                                               p["verdict"], clean(p["reason"])))
    if aud:
        print("\ninstall audit (nothing run):")
        # Orphans are grouped: their command has to run from a path that is gone,
        # so one line per plugin+scope says enough; --json lists every path.
        orphans = {}
        for a in aud:
            if a["kind"] == "orphan":
                orphans[(a["plugin"], a["scope"])] = orphans.get((a["plugin"], a["scope"]), 0) + 1
        for (plugin, scope), n in sorted(orphans.items()):
            print("  orphan  %s %s: %d record(s) whose projectPath is gone (never loaded; --json lists them)"
                  % (clean(plugin.split("@")[0]), scope, n))
        for a in aud:
            if a["kind"] == "orphan":
                continue
            print("  %-7s %s %s %s (newest %s)%s" % (a["kind"], clean(a["plugin"].split("@")[0]), a["scope"],
                                                     clean(a["version"]), clean(a["newest"]) or "?",
                                                     "  " + clean(a["project"]) if a["project"] else ""))
            print("          %s%s" % (clean(a["command"]), "   # " + a["note"] if a["note"] else ""))


def cmd_inventory(args):
    as_json = False
    for a in args:
        if a == "--json":
            as_json = True
        else:
            usage("unknown argument: %s" % a)
    mkt, records, newest, herdr, agents = gather()
    rows = session_rows(agents or [], records, newest, load_state()) if agents else []
    aud = audit(records or {}, newest)
    summary = summarize(rows, records, mkt)
    if as_json:
        print(json.dumps({"marketplace": mkt, "herdr": herdr, "agents_ok": agents is not None,
                          "summary": summary, "sessions": rows, "audit": aud}, ensure_ascii=True))
    else:
        print_inventory(rows, aud, herdr, agents, summary)
    return 0


# ---- refresh -----------------------------------------------------------------

_READ_ENV = os.environ.get("SESSIONS_READ_LINES", "")
READ_LINES = int(_READ_ENV) if _READ_ENV.isdigit() and int(_READ_ENV) > 0 else 1000


def transcript(pane):
    """(lines, saturated): the pane's recent non-blank lines above the composer,
    and whether the read hit its line cap (older output may have scrolled out).
    None if unreadable."""
    rc, out = run(["bash", HERDR_AGENT, "read", pane, "--source", "recent", "--lines", str(READ_LINES)])
    if rc != 0:
        return None
    lines = [l.replace("\xa0", " ").rstrip() for l in out.splitlines()]
    saturated = len(lines) >= READ_LINES
    rules = [i for i, l in enumerate(lines) if l.strip().startswith("\u2500" * 10)]
    if len(rules) >= 2:
        lines = lines[:rules[-2]]
    return [l for l in lines if l.strip()], saturated


# Stable lines matched again after the send. The search takes the rightmost
# match, so a too-short anchor can only miss new lines (unconfirmed), never
# confirm a reload that did not happen.
ANCHOR_LINES = 3


def stable(line):
    """A transcript entry that stays put once drawn: an input echo or the start
    of a reply. Claude Code also draws transient panels above the composer
    (`✦ Heads up`, `※ recap`, hint menus) that change from turn to turn; an
    anchor built on those is never found again."""
    # Column 0 only: Claude Code draws real echoes and reply starts flush left;
    # an indented marker is displayed content (model output, a menu cursor).
    return line.startswith(("\u276f", "\u23fa"))


def new_since(before, after):
    """The lines `after` (a transcript() result) gained since `before`, or None
    when that cannot be told. Both sides are compared on their stable() lines
    only. While nothing scrolled out, the stable lines of `before` are a prefix
    of those of `after`, and the new lines are everything after the last of
    them — identical repeated reload pairs cannot confuse that. Once the window
    is full, fall back to finding the last ANCHOR_LINES stable lines of `before`
    again (content, not a count: a count reads "nothing new" when the send
    pushes an old pair out)."""
    (before, _), (after, saturated) = before, after
    old = [l for l in before if stable(l)]
    idx = [i for i, l in enumerate(after) if stable(l)]
    cur = [after[i] for i in idx]
    if not saturated and cur[:len(old)] == old:
        return after[idx[len(old) - 1] + 1:] if old else after
    tail = old[-ANCHOR_LINES:]
    if not tail:
        return after
    for j in range(len(cur) - len(tail), -1, -1):
        if cur[j:j + len(tail)] == tail:
            return after[idx[j + len(tail) - 1] + 1:]
    return None


def reload_result(new):
    """(summary, reply, force_line) from the lines a send produced: summary when
    the last input echo among them is /reload-plugins and a Reloaded: line
    follows it; reply = the first line after that echo; force_line = a line
    asking for `/reload-plugins --force` (part of the update was held back)."""
    echoes = [i for i, l in enumerate(new) if l.startswith("\u276f")]
    if not echoes or new[echoes[-1]].rstrip() != "\u276f " + RELOAD:
        return "", "", False
    rest = new[echoes[-1] + 1:]
    hit = [l.strip() for l in rest if SUMMARY_RE.match(l)]
    force = [l.strip() for l in rest if RELOAD + " --force" in l]
    return (hit[0] if hit else ""), (rest[0].strip() if rest else ""), (force[0] if force else "")


def composer_state(pane):
    prog = os.environ.get("HERDR_COMPOSER_PY")
    if not prog:
        return "unknown"
    rc, out = run(["bash", HERDR_AGENT, "read", pane, "--source", "visible", "--format", "ansi"])
    if rc != 0:
        return "unknown"
    # -I: no cwd/script-dir on sys.path, so a planted re.py cannot steer the check.
    rc, res = run(["python3", "-I", "-c", prog], stdin=out)
    res = res.strip()
    return res if rc == 0 and res in ("clear", "draft") else "unknown"


def reload_one(row, wait_ms):
    pane = row["pane"]
    # blocked ends the wait too, so a blocked pane is reported as such at once.
    rc, _ = run(["bash", HERDR_AGENT, "wait", pane, "--until", "idle", "--until", "done",
                 "--until", "blocked", "--timeout", str(wait_ms)], timeout=wait_ms / 1000 + 10)
    if rc in (2, 3):   # ha_wait's own codes: usage, tools absent
        return "skipped", "herdr unavailable (wait rc=%d)" % rc
    if rc in (124, 142):   # the wrapper's wall bound fired: herdr itself hung
        return "skipped", "herdr wait hung (rc=%d)" % rc
    if rc != 0:
        return "skipped", "busy (not idle within %dms, wait rc=%d)" % (wait_ms, rc)
    rc, out = run(["bash", HERDR_AGENT, "get", pane])
    try:
        st = json.loads(out)["result"]["agent"]["agent_status"] if rc == 0 else ""
    except (ValueError, KeyError, TypeError):
        st = ""
    if st not in ("idle", "done"):
        return "skipped", "status %s" % (clean(st) or "unverified")
    if row["pid"] is None or claude_process(pane)[0] != row["pid"]:
        return "skipped", "pane no longer holds the inventoried claude session"
    before = transcript(pane)
    if before is None:
        return "skipped", "pane unreadable"
    # The draft check goes last, right before the send: the window in which a
    # user can start typing is then just this read and the prompt call.
    comp = composer_state(pane)
    if comp != "clear":
        return "skipped", "composer %s" % comp
    # The reload loads what is installed when it runs, so its record is the send
    # time: an update landing while the summary is awaited stays visible.
    sent_at = time.time()
    rc, _ = run(["herdr", "agent", "prompt", pane, RELOAD])
    if rc != 0:
        return "failed", "herdr agent prompt rc=%d" % rc
    def look():
        after = transcript(pane)
        new = new_since(before, after) if after is not None else None
        return reload_result(new) if new is not None else None

    def done(summary, force_line):
        if force_line:
            return "partial", "%s; pane asks for --force (the user's call): %s" % (clean(summary), clean(force_line)[:160])
        err = record_reload(pane, row["pid"], sent_at)
        return "reloaded", clean(summary) + ("; not recorded: " + clean(err) if err else "")

    # Poll until the summary shows, then decide after exactly ONE more look (a
    # --force notice may render a frame later). Every path is bounded by the
    # deadline plus that single look.
    deadline = time.time() + VERIFY_SECS
    reply, seen = "", ""
    while time.time() < deadline:
        time.sleep(POLL_SECS)
        res = look()
        if res is None:
            continue
        summary, reply, force_line = res
        if summary and force_line:
            return done(summary, force_line)
        if summary:
            seen = summary
            break
    if seen:
        # Not POLL_SECS: this is the grace a late --force line gets to render,
        # and a shorter test poll must not let a partial reload read as done.
        time.sleep(1)
        res = look()
        # A --force line counts whether or not the summary is still on screen.
        return done(seen, res[2] if res else "")
    hint = "; pane: " + clean(reply)[:160] if reply else ""
    return "unconfirmed", "sent, but no Reloaded: summary within %ds%s" % (VERIFY_SECS, hint)


def cmd_refresh(args):
    ws, all_ws, yes, as_json, wait_ms, only = None, False, False, False, 30000, None
    it = iter(args)
    for a in it:
        if a == "--workspace":
            ws = next(it, None) or usage("--workspace needs an id")
        elif a == "--all":
            all_ws = True
        elif a == "--yes":
            yes = True
        elif a == "--only":
            v = next(it, "")
            if not v:
                usage("--only needs a comma-separated pane list")
            only = set(v.split(","))   # pane@pid tokens, as confirm=required printed them
        elif a == "--json":
            as_json = True
        elif a == "--wait-ms":
            v = next(it, "")
            if not v.isdigit():
                usage("--wait-ms needs a number")
            wait_ms = int(v)
        else:
            usage("unknown argument: %s" % a)
    if ws and all_ws:
        usage("--workspace and --all exclude each other")
    if all_ws and yes and only is None:
        usage("--all --yes needs --only <the pane@pid tokens confirm=required listed>")
    if only is not None and not (all_ws and yes):
        usage("--only belongs to --all --yes")
    if os.environ.get("HERDR_ENV") != "1":
        sys.stderr.write("sessions: refresh needs herdr (HERDR_ENV=1)\n")
        return 3
    ws = ws or (None if all_ws else os.environ.get("HERDR_WORKSPACE_ID"))
    if not all_ws and not ws:
        usage("no herdr workspace: pass --workspace <id> or --all")
    mkt, records, newest, _, agents = gather()
    if agents is None:
        sys.stderr.write("sessions: herdr agent list unreachable\n")
        return 3
    agents = [a for a in agents if all_ws or a.get("workspace_id") == ws]
    rows = session_rows(agents, records, newest, load_state())
    targets = [r for r in rows if r["action"] == "reload"]
    results = []
    if all_ws and not yes:
        if not targets:
            print(json.dumps({"confirm": "none", "targets": []}) if as_json else "targets=none")
            return 0
        results = [{"pane": r["pane"], "token": "%s@%s" % (r["pane"], r["pid"]),
                    "where": "%s/%s" % (r["workspace_label"], r["tab_label"]),
                    "result": "pending", "detail": ""} for r in targets]
        if as_json:
            print(json.dumps({"confirm": "required", "targets": results}, ensure_ascii=True))
        else:
            print("confirm=required")
            for t in results:
                print("target\t%s\t%s" % (clean(t["token"]), clean(t["where"])))
        return 0
    for r in rows:
        where = "%s/%s" % (r["workspace_label"] or r["workspace"], r["tab_label"] or r["tab"])
        # The confirmation names a session (pane@pid), not a pane: a new claude
        # in a confirmed pane was never confirmed.
        if r["action"] == "reload" and only is not None and "%s@%s" % (r["pane"], r["pid"]) not in only:
            res, detail = "skipped", "not in the confirmed target list"
        elif r["action"] == "reload":
            res, detail = reload_one(r, wait_ms)
        elif r["agent"] != "claude":
            res, detail = "skipped", "n/a (%s worker)" % (r["agent"] or "unknown")
        elif r["self"] and r["action"].startswith("self"):
            res, detail = "skipped", "own pane: run /reload-plugins here"
        elif r["self"] and r["verdict"] != "stale":
            res, detail = "skipped", "own pane"
        elif r["verdict"] == "stale":
            res, detail = "skipped", "reload does not help: install is stale"
        else:
            res, detail = "skipped", r["verdict"] + (" (%s)" % r["reason"] if r["reason"] else "")
        results.append({"pane": r["pane"], "where": where, "result": res, "detail": detail})
    if as_json:
        print(json.dumps({"results": results}, ensure_ascii=True))
    else:
        for t in results:
            print("%-11s %-8s %s  %s" % (t["result"], clean(t["pane"]), clean(t["where"]), clean(t["detail"])))
    return 0


def main(argv):
    if not argv:
        usage()
    cmd, rest = argv[0], argv[1:]
    if cmd == "inventory":
        return cmd_inventory(rest)
    if cmd == "refresh":
        return cmd_refresh(rest)
    usage("unknown command: %s" % cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
