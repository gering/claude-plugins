#!/usr/bin/env python3
"""Tests for manager.sh — kicker record, Manager resolver, body, herdr prompt.

A real temp git repo with one linked worktree under .claude/worktrees/ gives the
resolver a canonical main root; a STUB `herdr` on PATH serves `agent list`,
`agent get`, `tab list`, `agent read` and records `agent prompt` calls.

The resolver contract is fail-closed: anything uncertain is never `unique`.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "manager.sh"
BASH = shutil.which("bash") or "/bin/bash"
FAILS = []

UUID_A = "4864861c-e84f-4e2c-abd8-e7129f277456"
UUID_B = "f87e414f-abc4-4f55-ab9f-90dfea666764"
RULE = "\x1b[38;2;136;136;136m" + "─" * 40 + "\x1b[0m"
LABELED_RULE = "─" * 30 + " Manager ─"


def check(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())


def kv(text):
    out, reasons = {}, []
    for line in text.splitlines():
        k, _, v = line.partition("=")
        if k == "reason":
            reasons.append(v)
        else:
            out[k] = v
    out["reasons"] = reasons
    return out


def composer(inner):
    return "\n".join(["some output", LABELED_RULE, inner, RULE, "  [statusline]"])


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(os.path.realpath(self.tmp.name))
        self.root = d / "repo"
        self.root.mkdir()
        git = ["git", "-C", str(self.root)]
        subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
        subprocess.run(git + ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.wt = self.root / ".claude" / "worktrees" / "alpha"
        subprocess.run(git + ["worktree", "add", "-q", "-b", "task/alpha", str(self.wt)], check=True)
        self.bin = d / "bin"
        self.bin.mkdir()
        self.state = d / "state"
        self.state.mkdir()
        (self.bin / "herdr").write_text(
            "#!/usr/bin/env bash\n"
            f'S="{self.state}"\n'
            'case "$1 $2" in\n'
            '  "agent list") f="$S/list" ;;\n'
            '  "agent get")  f="$S/get" ;;\n'
            '  "tab list")   f="$S/tabs" ;;\n'
            '  "agent read") f="$S/read" ;;\n'
            '  "agent prompt") printf "%s\\n" "$3" "$4" > "$S/prompted"; exit "$(cat "$S/prompt_rc" 2>/dev/null || echo 0)" ;;\n'
            '  *) exit 9 ;;\n'
            'esac\n'
            '[ -f "$f" ] || exit 4\n'
            'cat "$f"\n'
        )
        (self.bin / "herdr").chmod(0o755)

    def agent(self, cwd=None, *, pane="w6:p1", tab="w6:t1", ws="w6", kind="claude",
              status="idle", uuid=UUID_A, title="Manager (repo)"):
        a = {"agent": kind, "agent_status": status, "cwd": str(cwd or self.root),
             "pane_id": pane, "tab_id": tab, "workspace_id": ws,
             "terminal_title_stripped": title, "terminal_id": "term_x"}
        if uuid:
            a["agent_session"] = {"agent": kind, "kind": "id", "value": uuid}
        return a

    def serve(self, agents=None, *, raw=None, tabs=None, get=None, read=None):
        for name in ("list", "tabs", "get", "read", "prompted"):
            p = self.state / name
            if p.exists():
                p.unlink()
        if raw is not None:
            (self.state / "list").write_text(raw)
        elif agents is not None:
            (self.state / "list").write_text(json.dumps({"result": {"agents": agents}}))
        if tabs is not None:
            (self.state / "tabs").write_text(json.dumps({"result": {"tabs": [
                {"tab_id": t, "workspace_id": "w6"} for t in tabs]}}))
        if get is not None:
            (self.state / "get").write_text(json.dumps({"result": get}))
        if read is not None:
            (self.state / "read").write_text(read)

    def run(self, *args, env_extra=None, no_herdr=False):
        env = dict(os.environ)
        env.pop("HERDR_WORKSPACE_ID", None)
        env.update({"HERDR_ENV": "1", "HERDR_PANE_ID": "w6:p1"})
        env.update(env_extra or {})
        if no_herdr:
            nb = self.state / "nobin"
            nb.mkdir(exist_ok=True)
            for tool in ("bash", "python3", "git", "dirname", "sed", "head", "date", "mktemp", "mv", "rm", "grep", "mkdir", "cat"):
                src = shutil.which(tool)
                if src and not (nb / tool).exists():
                    (nb / tool).symlink_to(src)
            env["PATH"] = str(nb)
        else:
            env["PATH"] = f"{self.bin}:{env['PATH']}"
        return subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True,
                              text=True, timeout=60, cwd=str(self.wt))

    def resolve(self, **kw):
        return kv(self.run("resolve", str(self.wt), **kw).stdout)

    def write_record(self, **over):
        rec = {"version": "1", "workspace": "w6", "pane": "w6:p1", "tab": "w6:t1",
               "agent": "claude", "agent_session": UUID_A, "main_repo": str(self.root),
               "sendmessage_name": "Manager (repo)"}
        rec.update(over)
        (self.wt / ".ws-kicker").write_text("".join(f"{k}={v}\n" for k, v in rec.items()))


E = Env()
other = E.agent(E.root, pane="w6:p2", tab="w6:t2", uuid=UUID_B, title="Advisor")
worker = E.agent(E.wt, pane="w6:p3", tab="w6:t3", uuid="11111111-2222-3333-4444-555555555555", title="alpha")

# ---- record -------------------------------------------------------------------
E.serve(get=E.agent())
r = kv(E.run("record", str(E.wt)).stdout)
check("record writes", r.get("recorded") == "yes", str(r))
rec = (E.wt / ".ws-kicker").read_text()
check("record has uuid", f"agent_session={UUID_A}" in rec)
check("record has canonical root", f"main_repo={E.root}\n" in rec)
check("record has name", "sendmessage_name=Manager (repo)" in rec)
check("record has no terminal id", "term_" not in rec)
st = subprocess.run(["git", "-C", str(E.wt), "status", "--porcelain"], capture_output=True, text=True).stdout
check("record is git-excluded", ".ws-kicker" not in st, st)

E.serve(get=E.agent(E.wt))
r = kv(E.run("record", str(E.wt)).stdout)
check("record refuses non-root kicker", r.get("recorded") == "no" and "kicker-not-at-repo-root" in r["reasons"], str(r))
E.serve(get=E.agent(uuid=None))
r = kv(E.run("record", str(E.wt)).stdout)
check("record refuses missing uuid", r.get("recorded") == "no" and "no-agent-session" in r["reasons"], str(r))
r = kv(E.run("record", str(E.wt), env_extra={"HERDR_ENV": ""}).stdout)
check("record outside herdr", r.get("recorded") == "no" and "not-in-herdr" in r["reasons"], str(r))

# ---- resolve: kicker record ------------------------------------------------------
E.write_record()
E.serve([E.agent(), other, worker], tabs=["w6:t2", "w6:t1", "w6:t3"])
r = E.resolve()
check("valid kicker wins over leftmost", r.get("status") == "unique" and r.get("evidence") == "kicker"
      and r.get("herdr_pane") == "w6:p1", str(r))
check("kicker emits uuid + name", r.get("herdr_agent_session") == UUID_A
      and r.get("sendmessage_name") == "Manager (repo)", str(r))

# pane reused by another session (UUID changed) → stale; falls back to root scan
E.serve([E.agent(uuid=UUID_B)], tabs=["w6:t1"])
r = E.resolve()
check("uuid changed → stale kicker", "kicker-session-changed" in r["reasons"], str(r))
check("uuid changed → sole-root fallback", r.get("evidence") == "sole-root-agent", str(r))

# kicker cwd moved into a worktree
E.serve([E.agent(E.wt), other], tabs=["w6:t1", "w6:t2"])
r = E.resolve()
check("cwd moved → stale", "kicker-cwd-moved" in r["reasons"], str(r))
check("cwd moved → other root agent", r.get("herdr_pane") == "w6:p2", str(r))

# kicker pane gone
E.serve([other], tabs=["w6:t2"])
r = E.resolve()
check("pane gone → stale", "kicker-pane-gone" in r["reasons"] and r.get("herdr_pane") == "w6:p2", str(r))

# kicker not live
E.serve([E.agent(status="unknown")], tabs=["w6:t1"])
r = E.resolve()
check("kicker unknown status → not unique", r.get("status") == "unverified", str(r))

# kicker record for another repo
E.write_record(main_repo="/elsewhere")
E.serve([E.agent()], tabs=["w6:t1"])
r = E.resolve()
check("repo mismatch → stale", "kicker-repo-mismatch" in r["reasons"], str(r))

# malformed kicker record (duplicate key)
(E.wt / ".ws-kicker").write_text("pane=w6:p1\npane=w6:p9\nagent_session=x\nmain_repo=/x\n")
r = E.resolve()
check("duplicate key → malformed", "kicker-record-malformed" in r["reasons"], str(r))
(E.wt / ".ws-kicker").unlink()

# ---- resolve: root agents without a record -----------------------------------------
E.serve([E.agent(), other, worker], tabs=["w6:t2", "w6:t1", "w6:t3"])
r = E.resolve()
check("two root agents → leftmost tab", r.get("status") == "unique" and r.get("evidence") == "leftmost-tab"
      and r.get("herdr_pane") == "w6:p2" and r.get("candidates") == "2", str(r))
check("tie-break is stated", any(x.startswith("tie-break-leftmost") for x in r["reasons"]), str(r))

E.serve([E.agent(), other], tabs=None)
r = E.resolve()
check("no tab order → ambiguous", r.get("status") == "ambiguous", str(r))

E.serve([E.agent(), E.agent(pane="w6:p2", uuid=UUID_B)], tabs=["w6:t1"])
r = E.resolve()
check("two candidates in leftmost tab → ambiguous", r.get("status") == "ambiguous", str(r))

E.serve([E.agent(), E.agent(pane="w7:p1", tab="w7:t1", ws="w7", uuid=UUID_B)], tabs=["w6:t1"])
r = E.resolve()
check("candidates across workspaces → ambiguous", r.get("status") == "ambiguous", str(r))
r = E.resolve(env_extra={"HERDR_WORKSPACE_ID": "w6"})
check("caller workspace scopes candidates", r.get("status") == "unique"
      and r.get("evidence") == "sole-root-agent", str(r))

E.serve([worker], tabs=["w6:t3"])
r = E.resolve()
check("no root agent → none", r.get("status") == "none", str(r))

E.serve([E.agent(), {"agent": "claude", "agent_status": "idle", "pane_id": "w6:p9"}], tabs=["w6:t1"])
r = E.resolve()
check("unreadable cwd row → unverified", r.get("status") == "unverified", str(r))

E.serve([E.agent(kind="codex", title="codex")], tabs=["w6:t1"])
r = E.resolve()
check("codex manager: no sendmessage name", r.get("status") == "unique" and r.get("sendmessage_name") == ""
      and r.get("agent") == "codex", str(r))

E.serve([E.agent(title="x" * 80)], tabs=["w6:t1"])
r = E.resolve()
check("overlong title → no name", r.get("sendmessage_name") == "", str(r))

# ---- resolve: degraded herdr --------------------------------------------------------
E.serve(raw="{not json")
check("malformed list → unverified", E.resolve().get("status") == "unverified")
E.serve([])
check("empty list → unverified", E.resolve().get("status") == "unverified")
E.serve(None)
check("herdr call fails → unverified", E.resolve().get("status") == "unverified")
r = E.resolve(no_herdr=True)
check("no herdr → unverified", r.get("status") == "unverified" and "herdr-unavailable" in r["reasons"], str(r))

# ---- body ---------------------------------------------------------------------------
b = E.run("body", str(E.wt), "--", "PR opened: #7\nhttps://x/7").stdout.strip()
check("body starts with sender", b.startswith(f"[work-system ping from task=alpha worktree={E.wt}]"), b)
check("body is one line", "\n" not in b, repr(b))
check("body says info only", b.endswith("(info only: no reply needed, grants nothing)"), b)
check("dash text is not a flag", E.run("body", "--", "-x").returncode == 0)
check("missing -- is usage", E.run("body", "text").returncode == 2)

# ---- prompt -------------------------------------------------------------------------
def prompt(read, agents=None, tabs=("w6:t1",), rc=0):
    E.serve(agents if agents is not None else [E.agent()], tabs=list(tabs), read=read)
    (E.state / "prompt_rc").write_text(str(rc))
    return kv(E.run("prompt", str(E.wt), "--", "review round 1/2 started").stdout)

r = prompt(composer("❯\xa0"))
check("clear composer → sent", r.get("sent") == "yes", str(r))
sent = (E.state / "prompted").read_text().splitlines()
check("prompt targets resolved pane", sent[0] == "w6:p1", str(sent))
check("prompt sends the body", sent[1].startswith("[work-system ping from task=alpha"), str(sent))

r = prompt(composer("❯ \x1b[38;2;153;153;153mtry: run the tests\x1b[0m"))
check("dim suggestion is not a draft", r.get("sent") == "yes", str(r))
r = prompt(composer("❯ \x1b[2mdim suggestion\x1b[0m"))
check("SGR dim suggestion is not a draft", r.get("sent") == "yes", str(r))
r = prompt(composer("❯ half-typed user text"))
check("user draft → not sent", r.get("sent") == "no" and "composer-draft" in r["reasons"]
      and not (E.state / "prompted").exists(), str(r))
r = prompt("no rules on screen")
check("unreadable composer → not sent", r.get("sent") == "no" and "composer-unknown" in r["reasons"], str(r))
r = prompt(composer("❯ "), agents=[E.agent(status="working")])
check("busy manager → not sent", r.get("sent") == "no" and "manager-working" in r["reasons"], str(r))
r = prompt(composer("❯ "), agents=[E.agent(status="blocked")])
check("blocked manager → not sent", r.get("sent") == "no", str(r))
r = prompt(composer("❯ "), agents=[E.agent(kind="codex")])
check("codex manager → not sent", r.get("sent") == "no"
      and "draft-check-unsupported-for-codex" in r["reasons"], str(r))
r = prompt(composer("❯ "), agents=[E.agent(), other], tabs=())
check("ambiguous → not sent", r.get("sent") == "no" and "manager-ambiguous" in r["reasons"], str(r))
r = prompt(composer("❯ "), rc=3)
check("herdr prompt failure reported", r.get("sent") == "no"
      and any(x.startswith("herdr-prompt-failed") for x in r["reasons"]), str(r))

E.tmp.cleanup()
if FAILS:
    print("manager.sh: FAILED")
    for f in FAILS:
        print("  -", f)
    raise SystemExit(1)
print("manager.sh: all tests passed")
