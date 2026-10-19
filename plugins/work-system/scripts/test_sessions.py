#!/usr/bin/env python3
"""Tests for sessions.sh / sessions.py — session inventory, install audit and
the idle-only /reload-plugins refresh.

Two layers:
  * the pure logic (effective install, staleness matrix, audit) imported from
    sessions.py;
  * the REAL sessions.sh end to end, with a fixture installed_plugins.json and
    marketplace.json, and stub `herdr` + `ps` binaries first on PATH. The herdr
    stub logs every call, so the refresh tests can assert which panes were
    typed into (only the stale, idle, clear-composer, non-self claude ones).
"""
import datetime
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import sessions  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())


NOW = datetime.datetime.now(datetime.timezone.utc)


def ago(**kw):
    return (NOW - datetime.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ts_ago(**kw):
    return (NOW - datetime.timedelta(**kw)).timestamp()


MKT = "gering-plugins"

# ---- pure logic ----------------------------------------------------------------

check("etime mm:ss", sessions.parse_etime("05:07") == 307)
check("etime hh:mm:ss", sessions.parse_etime(" 01:00:00") == 3600)
check("etime dd-hh:mm:ss", sessions.parse_etime("2-00:00:01") == 172801)
check("etime junk", sessions.parse_etime("abc") is None)
check("version order", sessions.parse_ver("1.10.0") > sessions.parse_ver("1.9.3"))
check("version junk", sessions.parse_ver("1.2.x") is None)

tmp = tempfile.TemporaryDirectory()
T = Path(tmp.name)
repo = T / "repo"
wt = repo / ".claude" / "worktrees" / "lane"
other = T / "other"
other.mkdir()
sub = repo / "sub"
subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                "commit", "-q", "--allow-empty", "-m", "init"], check=True)
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "task/lane", str(wt)], check=True)
sub.mkdir()
REPO, WT, OTHER, SUB = (os.path.realpath(p) for p in (repo, wt, other, sub))
GONE = str(T / "gone")


def rec(scope, version, updated, path=None, ip=None):
    r = {"scope": scope, "version": version, "lastUpdated": updated,
         "installPath": ip or f"/cache/{version}"}
    if path:
        r["projectPath"] = path
    return r


user = rec("user", "2.0.0", ago(hours=1))
proj_main = rec("project", "1.0.0", ago(days=3), REPO)
local_wt = rec("local", "2.0.0", ago(days=3), WT)

check("main_root: a worktree maps to its main checkout", sessions.main_root(WT) == REPO)
check("main_root: the main checkout itself", sessions.main_root(REPO) == REPO)
check("main_root: a subdir is no worktree", sessions.main_root(SUB) is None)
check("main_root: no repo", sessions.main_root(OTHER) is None)

r, why = sessions.effective([user], OTHER, None)
check("user install applies anywhere", r is user and why is None)
r, why = sessions.effective([user, proj_main], WT, REPO)
check("worktree inherits the main checkout's project install", r is proj_main, str((r, why)))
r, why = sessions.effective([user, proj_main, local_wt], WT, REPO)
check("local beats project", r is local_wt, str((r, why)))
r, why = sessions.effective([proj_main], OTHER, None)
check("no matching record → not-installed", r is None and why == "not-installed")
r, why = sessions.effective([user, rec("local", "1.0.0", ago(days=1), WT),
                             rec("local", "2.0.0", ago(days=1), REPO)], WT, REPO)
check("competing local records → unknown", r is None and "competing" in why, str(why))
r, why = sessions.effective([user, proj_main], SUB, sessions.main_root(SUB))
check("parent-dir-only project record → unknown", r is None and "parent" in why, str(why))

V = sessions.plugin_verdict
start = ts_ago(hours=2)
check("updated after start → stale reload",
      V("a", rec("user", "1.0.0", ago(hours=1)), None, "1.0.0", start)["reason"] == "reload")
check("updated before start → current",
      V("a", rec("user", "1.0.0", ago(hours=3)), None, "1.0.0", start)["verdict"] == "current")
check("older than marketplace → stale install",
      V("a", rec("user", "1.0.0", ago(hours=3)), None, "1.1.0", start)["reason"] == "install")
check("1.25 equals 1.25.0 (padded compare)",
      V("a", rec("user", "1.25", ago(hours=3)), None, "1.25.0", start)["verdict"] == "current")
check("startup write within the grace → current",
      V("a", rec("local", "1.0.0", ago(hours=2, seconds=-2)), None, "1.0.0", start, grace=True)["verdict"] == "current")
check("no grace without the flag (a real update right after start)",
      V("a", rec("user", "1.0.0", ago(hours=2, seconds=-2)), None, "1.0.0", start)["reason"] == "reload")
check("unknown start → unknown",
      V("a", rec("user", "1.0.0", ago(hours=3)), None, "1.0.0", None)["verdict"] == "unknown")
check("no lastUpdated → unknown",
      V("a", rec("user", "1.0.0", None), None, "1.0.0", start)["verdict"] == "unknown")
check("plugin missing from marketplace → unknown, never current",
      V("a", rec("user", "1.0.0", ago(hours=3)), None, None, start)["verdict"] == "unknown")
check("unresolvable install → unknown",
      V("a", None, "competing local records", "1.0.0", start)["verdict"] == "unknown")
r, why = sessions.effective([rec("user", "1.0.0", ago(days=2)), rec("user", "1.0.0", ago(hours=1))], OTHER, None)
check("identical records → the newest lastUpdated wins", r is not None and r["lastUpdated"] == ago(hours=1), str(r))

aud = sessions.audit({"p@" + MKT: [rec("user", "1.0.0", ago(days=1)),
                                    rec("project", "0.9.0", ago(days=1), REPO),
                                    rec("local", "1.0.0", ago(days=1), GONE),
                                    rec("project", "1.0.0", ago(days=1), REPO)]},
                     {"p@" + MKT: "1.0.0"})
kinds = sorted((a["kind"], a["scope"]) for a in aud)
check("audit: lagging project + orphan local only", kinds == [("lagging", "project"), ("orphan", "local")], str(kinds))
lag = [a for a in aud if a["kind"] == "lagging"][0]
check("audit command runs the update from the project",
      lag["command"] == f"cd -- {REPO} && claude plugin update p@{MKT} --scope project", lag["command"])
aud = sessions.audit({"we ird%s@" + MKT: [rec("user", "0.1.0", ago(days=1)),
                                          rec("project; touch /tmp/x", "0.1.0", ago(days=1), REPO)]},
                     {"we ird%s@" + MKT: "1.0.0"})
dash = sessions.audit({"-x@" + MKT: [rec("user", "0.1.0", ago(days=1))]}, {"-x@" + MKT: "1.0.0"})
check("dash-led plugin id → reported as unsafe, no command",
      [(a["kind"], a["command"]) for a in dash] == [("unsafe", "")], str(dash))
check("audit quotes the plugin id and drops an unknown scope",
      [a["command"] for a in aud] == [f"claude plugin update 'we ird%s@{MKT}' --scope user"], str(aud))
check("clean blanks C1 and bidi controls",
      sessions.clean("a\x85b\u202ec\x1b") == "a b c ", repr(sessions.clean("a\x85b\u202ec\x1b")))

# ---- end to end: stub herdr + ps -------------------------------------------------

BIN = T / "bin"
BIN.mkdir()
STUB = T / "stub"
STUB.mkdir()

HERDR_STUB = r'''#!/usr/bin/env python3
import json, os, sys
D = os.environ["STUB_DIR"]
cfg = json.load(open(os.path.join(D, "cfg.json")))
a = sys.argv[1:]
with open(os.path.join(D, "calls.log"), "a") as f:
    f.write(" ".join(a) + "\n")
def out(result):
    print(json.dumps({"id": "stub", "result": result}))
def summaries(p):
    try:
        return int(open(os.path.join(D, "sum-" + p.replace(":", "_"))).read())
    except OSError:
        return 0
def bump(name):
    f = os.path.join(D, name.replace(":", "_"))
    n = int(open(f).read()) + 1 if os.path.exists(f) else 1
    open(f, "w").write(str(n))
    return n
RULE = "─" * 20
if a[:2] == ["agent", "list"]:
    out({"agents": cfg["agents"]})
elif a[:2] == ["agent", "get"]:
    ag = [x for x in cfg["agents"] if x["pane_id"] == a[2]][0]
    ag = dict(ag, agent_status=cfg.get("after_wait", {}).get(a[2], ag["agent_status"]))
    out({"agent": ag})
elif a[:2] == ["agent", "wait"]:
    sys.exit(1 if a[2] in cfg.get("busy", []) else 0)
elif a[:2] == ["agent", "read"]:
    p = a[2]
    if "visible" in a:
        inner = cfg.get("composer", {}).get(p, "❯ ")
        print("─" * 20 + "\n" + inner + "\n" + "─" * 20)
    else:
        lines = list(cfg.get("history", {}).get(p, ["❯ hello", "⏺ hi"]))
        pair = ["❯ /reload-plugins", "  ⎿  Reloaded: 1 plugins"]
        if p in cfg.get("force", []):
            pair.append("  MCP server changes need /reload-plugins --force")
        lines += pair * summaries(p)
        if p in cfg.get("vanish", []) and summaries(p) and bump("reads-" + p) > 1:
            # The summary was on screen once; a redraw now shows the echo without it.
            lines[-1] = "  \u23bf  (redrawn)"
        if p in cfg.get("vanish_force", []) and summaries(p) and bump("reads-" + p) > 1:
            # The redraw drops the summary and shows a --force request instead.
            lines[-1] = "  MCP server changes need /reload-plugins --force"
        if p in cfg.get("transient", []):
            # A panel above the composer that Claude Code redraws every turn,
            # with an indented menu cursor that must not count as an echo.
            lines += ["✦ Heads up · note %d" % bump("panel-" + p), "  ❯ 1: Learn more   0: Dismiss"]
        lines += [RULE, "❯ ", RULE]
        w = cfg.get("window", {}).get(p)
        if "--lines" in a:
            n = int(a[a.index("--lines") + 1])
            w = min(w or n, n)
        print("\n".join(lines[-w:] if w else lines))
elif a[:2] == ["agent", "prompt"]:
    p = a[2]
    if p not in cfg.get("noconfirm", []):
        n = summaries(p) + 1
        open(os.path.join(D, "sum-" + p.replace(":", "_")), "w").write(str(n))
    out({"type": "agent_prompted"})
elif a[:2] == ["pane", "process-info"]:
    p = a[3]
    pr = cfg["procs"].get(p)
    if pr and p in cfg.get("newpid", []) and bump("pi-" + p) > 1:
        pr = dict(pr, pid=pr["pid"] + 1)
    if not pr:
        out({"process_info": {"foreground_processes": []}})
    else:
        out({"process_info": {"foreground_process_group_id": pr["pid"], "foreground_processes": [
            {"pid": pr["pid"], "argv0": pr.get("argv0", "claude"), "argv": [pr.get("argv0", "claude")],
             "cwd": pr["cwd"]}]}})
elif a[:2] == ["tab", "list"]:
    out({"tabs": [{"tab_id": x["tab_id"], "label": "tab-\u009b\u202e" + x["pane_id"]} for x in cfg["agents"]]})
elif a[:2] == ["workspace", "list"]:
    out({"workspaces": [{"workspace_id": "w1", "label": "one"}, {"workspace_id": "w2", "label": "two"}]})
else:
    sys.exit(9)
'''
PS_STUB = r'''#!/usr/bin/env python3
import json, os, sys
cfg = json.load(open(os.path.join(os.environ["STUB_DIR"], "cfg.json")))
e = cfg.get("etime", {}).get(sys.argv[-1])
if e is None:
    sys.exit(1)
print(e)
'''
for name, body in (("herdr", HERDR_STUB), ("ps", PS_STUB)):
    p = BIN / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)

wt2 = repo / ".claude" / "worktrees" / "fresh-lane"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "task/fresh", str(wt2)], check=True)
WT2 = os.path.realpath(wt2)
INSTALLED = T / "installed.json"
INSTALLED.write_text(json.dumps({"version": 2, "plugins": {
    "alpha@" + MKT: [rec("user", "1.0.0", ago(hours=1))],
    "beta@" + MKT: [rec("user", "2.0.0", ago(days=5)), rec("project", "1.0.0", ago(days=5), REPO)],
    "gamma@" + MKT: [dict(rec("local", "1.0.0", ago(hours=2, seconds=-5), WT2), installedAt=ago(hours=2, seconds=-5))],
    # created at startup, then really updated 30s later: the grace must not hide it
    "delta@" + MKT: [dict(rec("local", "1.0.0", ago(hours=2, seconds=-30), WT2), installedAt=ago(hours=2, seconds=-1))],
    "foreign@other": [rec("user", "0.1.0", ago(minutes=1))],
}}))
MARKET = T / "marketplace.json"
MARKET.write_text(json.dumps({"plugins": [{"name": "alpha", "version": "1.0.0"},
                                          {"name": "beta", "version": "2.0.0"},
                                          {"name": "gamma", "version": "1.0.0"},
                                          {"name": "delta", "version": "1.0.0"}]}))
STATE = T / "state.json"


def agent(pane, kind="claude", status="idle", ws="w1", cwd=OTHER):
    return {"pane_id": pane, "tab_id": "t-" + pane, "workspace_id": ws, "agent": kind,
            "agent_status": status, "cwd": cwd}


AGENTS = [
    agent("w1:stale"),                       # started 2h ago, alpha updated 1h ago
    agent("w1:fresh"),                       # started 10m ago
    agent("w1:codex", kind="codex"),
    agent("w1:self"),                        # $HERDR_PANE_ID
    agent("w1:wt", cwd=WT),                  # beta project install lags
    agent("w1:nostart"),                     # ps knows no etime
    agent("w1:busy"),
    agent("w1:draft"),
    agent("w1:working"),                     # wait ok, but get says working
    agent("w1:noconfirm"),                   # prompt lands, no summary appears
    agent("w1:reloaded"),                    # recorded reload after the update
    agent("w1:oldpid"),                      # recorded reload, but a different pid
    agent("w2:stale", ws="w2"),              # other workspace
    agent("w1:window"),                      # old summary scrolls out of the read window
    agent("w1:twice"),                       # pane already ended in a reload; window saturated
    agent("w1:same"),                        # tail is identical pairs, the new one matches them
    agent("w1:force"),                       # the reload asks for --force
    agent("w1:vanish"),                      # summary seen once, then redrawn away
    agent("w1:lateforce"),                   # redraw replaces the summary with a --force request
    agent("w1:panel"),                       # a transient Heads-up panel changes every read
    agent("w1:panelwin"),                    # the same, with a full read window
    agent("w1:newpid"),                      # a new session took the pane after inventory
    agent("w1:wtfresh", cwd=WT2),            # own local record written 5s after start
    agent("w1:mixed", cwd=WT),               # alpha needs a reload, beta's install lags
]
STALE_START = "02:00:00"
PROCS, ETIME = {}, {}
for i, a in enumerate(AGENTS):
    if a["agent"] != "claude":
        continue
    pid = 1000 + i
    PROCS[a["pane_id"]] = {"pid": pid, "cwd": a["cwd"]}
    if a["pane_id"] != "w1:nostart":
        ETIME[str(pid)] = "10:00" if a["pane_id"] in ("w1:fresh", "w1:wt") else STALE_START
CFG = {"agents": AGENTS, "procs": PROCS, "etime": ETIME, "busy": ["w1:busy"],
       "after_wait": {"w1:working": "working"}, "noconfirm": ["w1:noconfirm"],
       "composer": {"w1:draft": "❯ half-typed user text"},
       # Each window holds exactly the pre-send screen, so the send pushes the
       # oldest pair out: a pair or line count would read "nothing new".
       "history": {"w1:window": ["❯ /reload-plugins", "  ⎿  Reloaded: old", "❯ a", "⏺ b", "❯ hi", "⏺ ok"],
                   "w1:same": ["❯ /reload-plugins", "  ⎿  Reloaded: 1 plugins"] * 2,
                   "w1:panelwin": ["❯ /reload-plugins", "  ⎿  Reloaded: old", "❯ a", "⏺ b",
                                   "❯ hi", "⏺ ok"],
                   "w1:twice": ["❯ /reload-plugins", "  ⎿  Reloaded: older", "❯ z", "⏺ w",
                                "❯ /reload-plugins", "  ⎿  Reloaded: old"]},
       "window": {"w1:window": 9, "w1:twice": 9, "w1:panelwin": 11}, "newpid": ["w1:newpid"], "force": ["w1:force"], "vanish": ["w1:vanish"],
       "vanish_force": ["w1:lateforce"], "transient": ["w1:panel", "w1:panelwin"]}


def reset():
    (STUB / "cfg.json").write_text(json.dumps(CFG))
    for f in STUB.iterdir():
        if f.name != "cfg.json":
            f.unlink()
    STATE.write_text(json.dumps({
        "w1:reloaded": {"pid": PROCS["w1:reloaded"]["pid"], "reloaded_at": ago(minutes=30)[:19] + "Z"},
        "w1:oldpid": {"pid": 1, "reloaded_at": ago(minutes=30)[:19] + "Z"},
    }))


def sh(*args, herdr=True, mkt=MKT, extra=None):
    env = dict(os.environ, **(extra or {}))
    env.update(PATH=f"{BIN}:{os.environ['PATH']}", STUB_DIR=str(STUB),
               SESSIONS_INSTALLED_FILE=str(INSTALLED), SESSIONS_MARKETPLACE_FILE=str(MARKET),
               SESSIONS_STATE_FILE=str(STATE), SESSIONS_MARKETPLACE=mkt,
               HERDR_PANE_ID="w1:self", HERDR_WORKSPACE_ID="w1", SESSIONS_VERIFY_SECS="2")
    env.pop("HERDR_ENV", None)
    if herdr:
        env["HERDR_ENV"] = "1"
    return subprocess.run(["bash", str(HERE / "sessions.sh"), *args], env=env,
                          capture_output=True, text=True, timeout=120)


def prompted():
    log = STUB / "calls.log"
    lines = log.read_text().splitlines() if log.exists() else []
    return sorted(l.split()[2] for l in lines if l.startswith("agent prompt"))


reset()
p = sh("inventory", "--json")
check("inventory exits 0", p.returncode == 0, p.stderr)
inv = json.loads(p.stdout)
rows = {r["pane"]: r for r in inv["sessions"]}
expect = {
    "w1:stale": ("stale", "reload"), "w1:fresh": ("current", "none"),
    "w1:codex": ("n/a", "none"), "w1:self": ("stale", "self: run /reload-plugins here"),
    "w1:nostart": ("unknown", "check"), "w1:reloaded": ("current", "none"),
    "w1:oldpid": ("stale", "reload"), "w2:stale": ("stale", "reload"),
}
for pane, (verdict, action) in expect.items():
    got = (rows[pane]["verdict"], rows[pane]["action"])
    check(f"inventory {pane}", got == (verdict, action), str(got))
wtrow = rows["w1:wt"]
check("worktree row: install stale, reload does not help",
      wtrow["verdict"] == "stale" and wtrow["action"].startswith("update install"), str(wtrow["action"]))
check("mixed row: install update comes before the reload",
      rows["w1:mixed"]["action"].startswith("update install"), rows["w1:mixed"]["action"])
check("own worktree local record within the grace → current",
      [x["verdict"] for x in rows["w1:wtfresh"]["plugins"] if x["plugin"].startswith("gamma")] == ["current"],
      str(rows["w1:wtfresh"]["plugins"]))
check("an update right after the startup write is not hidden by the grace",
      [x["reason"] for x in rows["w1:wtfresh"]["plugins"] if x["plugin"].startswith("delta")] == ["reload"],
      str(rows["w1:wtfresh"]["plugins"]))
check("grace never covers another scope's update",
      [x["reason"] for x in rows["w1:wtfresh"]["plugins"] if x["plugin"].startswith("alpha")] == ["reload"])
check("json output is ASCII-only (pane-set controls arrive escaped)", p.stdout.isascii())
check("summary counts", inv["summary"]["stale_install"] == 3 and inv["summary"]["n/a"] == 1
      and inv["summary"]["registry_ok"] is True, str(inv["summary"]))
check("worktree row: beta resolved to the main checkout's project record",
      [(x["scope"], x["reason"]) for x in wtrow["plugins"] if x["plugin"].startswith("beta")] == [("project", "install")])
check("foreign marketplace ignored",
      not any(x["plugin"].startswith("foreign") for r in inv["sessions"] for x in r["plugins"]))
check("inventory types nothing", prompted() == [])
check("audit carries the lagging project install",
      [(a["kind"], a["plugin"]) for a in inv["audit"]] == [("lagging", "beta@" + MKT)], str(inv["audit"]))

p = sh("inventory")
check("text inventory renders", p.returncode == 0 and "install audit" in p.stdout, p.stderr)
p = sh("inventory", "--json", herdr=False)
out = json.loads(p.stdout)
check("outside herdr: audit only", p.returncode == 0 and out["sessions"] == [] and out["audit"], p.stdout)

reset()
p = sh("refresh", "--json", "--wait-ms", "1000")
check("refresh exits 0", p.returncode == 0, p.stderr)
res = {r["pane"]: r for r in json.loads(p.stdout)["results"]}
check("refresh types only into the stale, idle, clear panes of this workspace",
      prompted() == ["w1:force", "w1:lateforce", "w1:noconfirm", "w1:oldpid", "w1:panel", "w1:panelwin",
                     "w1:same", "w1:stale", "w1:twice", "w1:vanish", "w1:window"],
      str(prompted()))
check("summary seen once, then redrawn away → reloaded, and the loop ends",
      res["w1:vanish"]["result"] == "reloaded", str(res["w1:vanish"]))
check("--force request in the settle frame without the summary → partial",
      res["w1:lateforce"]["result"] == "partial", str(res["w1:lateforce"]))
check("transient panel redrawn between reads → still confirmed",
      res["w1:panel"]["result"] == "reloaded", str(res["w1:panel"]))
check("transient panel with a full read window → still confirmed",
      res["w1:panelwin"]["result"] == "reloaded", str(res["w1:panelwin"]))
check("identical reload pairs in the tail → the new one still confirms",
      res["w1:same"]["result"] == "reloaded", str(res["w1:same"]))
check("reply asking for --force → partial, not recorded",
      res["w1:force"]["result"] == "partial" and "MCP server changes" in res["w1:force"]["detail"],
      str(res["w1:force"]))
check("a second reload after a reload, window saturated → confirmed",
      res["w1:twice"]["result"] == "reloaded", str(res["w1:twice"]))
check("old summary scrolling out still confirms the new one", res["w1:window"]["result"] == "reloaded",
      str(res["w1:window"]))
check("new session in the pane → skipped", "no longer holds" in res["w1:newpid"]["detail"],
      str(res["w1:newpid"]))
check("stale → reloaded", res["w1:stale"]["result"] == "reloaded", str(res["w1:stale"]))
check("busy → skipped", res["w1:busy"]["result"] == "skipped" and "busy" in res["w1:busy"]["detail"])
check("draft → skipped", res["w1:draft"]["detail"] == "composer draft", str(res["w1:draft"]))
check("working after wait → skipped", res["w1:working"]["detail"] == "status working", str(res["w1:working"]))
check("no summary → unconfirmed", res["w1:noconfirm"]["result"] == "unconfirmed")
check("codex → n/a", res["w1:codex"]["detail"].startswith("n/a"))
check("own pane → told to reload itself", "run /reload-plugins here" in res["w1:self"]["detail"])
check("install-stale → reload does not help", "install is stale" in res["w1:wt"]["detail"])
check("other workspace untouched", "w2:stale" not in res)
state = json.loads(STATE.read_text())
check("confirmed reload recorded with its pid",
      state.get("w1:stale", {}).get("pid") == PROCS["w1:stale"]["pid"], str(state))
check("unconfirmed reload not recorded", "w1:noconfirm" not in state and "w1:force" not in state)
p = sh("inventory", "--json")
rows = {r["pane"]: r for r in json.loads(p.stdout)["sessions"]}
check("recorded reload → next inventory says current", rows["w1:stale"]["verdict"] == "current")

reset()
p = sh("refresh", "--all")
check("--all without --yes only lists targets",
      p.returncode == 0 and p.stdout.startswith("confirm=required") and prompted() == [], p.stdout)
check("--all lists the other workspace too", "w2:stale" in p.stdout)
tokens = dict(l.split("\t")[1].split("@") for l in p.stdout.splitlines() if l.startswith("target\t"))
check("confirm lists pane@pid tokens", tokens.get("w1:stale") == str(PROCS["w1:stale"]["pid"]), str(tokens))
check("--all --yes without --only → usage", sh("refresh", "--all", "--yes").returncode == 2)
check("--only without --all --yes → usage", sh("refresh", "--only", "w1:stale@1").returncode == 2)
only = ",".join(f"{pn}@{PROCS[pn]['pid']}" for pn in ("w2:stale", "w1:stale")) + ",w1:window@1"
p = sh("refresh", "--all", "--yes", "--only", only, "--json", "--wait-ms", "1000")
res = {r["pane"]: r for r in json.loads(p.stdout)["results"]}
check("--all --yes --only types into exactly the confirmed sessions (pane and pid)",
      prompted() == ["w1:stale", "w2:stale"] and "confirmed target" in res["w1:window"]["detail"], str(prompted()))

reset()
STATE.unlink()
STATE.mkdir()          # the state path is a directory: the write fails
p = sh("refresh", "--json", "--wait-ms", "1000")
res = {r["pane"]: r for r in json.loads(p.stdout)["results"]} if p.returncode == 0 else {}
check("failed record write → reloaded, not recorded, refresh goes on",
      res.get("w1:stale", {}).get("result") == "reloaded" and "not recorded" in res["w1:stale"]["detail"]
      and res.get("w1:window", {}).get("result") == "reloaded", p.stderr[-300:] + str(res.get("w1:stale")))
STATE.rmdir()

good = INSTALLED.read_text()
INSTALLED.write_text("{ half written")
reset()
p = sh("inventory", "--json")
inv = json.loads(p.stdout)
check("unreadable registry → every claude row unknown, never current",
      {r["verdict"] for r in inv["sessions"] if r["agent"] == "claude"} == {"unknown"}
      and inv["summary"]["registry_ok"] is False, str({r["pane"]: r["verdict"] for r in inv["sessions"]}))
p = sh("refresh", "--json", "--wait-ms", "1000")
check("unreadable registry → refresh types nothing", p.returncode == 0 and prompted() == [], p.stdout[-300:])
INSTALLED.write_text(good)

nothing = dict(CFG, agents=[agent("w1:fresh")])
(STUB / "cfg.json").write_text(json.dumps(nothing))
p = sh("refresh", "--all")
check("--all with nothing stale → targets=none, no confirmation", p.stdout.strip() == "targets=none", p.stdout)
reset()

p = sh("inventory", mkt="no-such-marketplace")
check("no installs from the marketplace → said so, not silently current",
      "no installs from marketplace no-such-marketplace" in p.stdout, p.stdout[:300])

# A read that fills SESSIONS_READ_LINES: the stable-line anchor path, with a
# panel that changes between reads (a raw-line anchor fails exactly here).
sat = dict(CFG, agents=[agent("w1:panelsat")],
           history={"w1:panelsat": ["❯ a", "⏺ b", "❯ c", "⏺ d", "❯ e", "⏺ f", "❯ g", "⏺ h"]},
           transient=["w1:panelsat"])
PROCS["w1:panelsat"] = {"pid": 4242, "cwd": OTHER}
sat["procs"] = dict(PROCS); sat["etime"] = dict(ETIME, **{"4242": STALE_START})
(STUB / "cfg.json").write_text(json.dumps(sat))
p = sh("refresh", "--json", "--wait-ms", "1000", extra={"SESSIONS_READ_LINES": "13"})
res = {r["pane"]: r for r in json.loads(p.stdout)["results"]} if p.returncode == 0 else {}
check("full read window + changing panel → confirmed via the stable anchor",
      res.get("w1:panelsat", {}).get("result") == "reloaded", p.stderr[-300:] + str(res.get("w1:panelsat")))
reset()

check("refresh outside herdr → exit 3", sh("refresh", herdr=False).returncode == 3)
check("--workspace with --all → usage", sh("refresh", "--all", "--workspace", "w1").returncode == 2)
check("unknown command → usage", sh("bogus").returncode == 2)

if FAILS:
    print("sessions.sh: FAILED")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("sessions.sh: all tests passed")
