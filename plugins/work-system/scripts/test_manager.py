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
            '  "tab list")   echo x >> "$S/tab_calls"; f="$S/tabs" ;;\n'
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
        for name in ("list", "tabs", "get", "read", "prompted", "tab_calls"):
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
check("kicker → delegate unasked", r.get("delegate_unasked") == "yes", str(r))

# pane reused by another session (UUID changed) → stale; falls back to root scan
E.serve([E.agent(uuid=UUID_B)], tabs=["w6:t1"])
r = E.resolve()
check("uuid changed → stale kicker", "kicker-session-changed" in r["reasons"], str(r))
check("uuid changed → sole-root fallback", r.get("evidence") == "sole-root-agent", str(r))
check("failed record → sole root is not unasked", r.get("delegate_unasked") == "no", str(r))

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
check("leftmost-tab → asked", r.get("delegate_unasked") == "no", str(r))

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
check("sole root without a record → unasked", r.get("delegate_unasked") == "yes", str(r))

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

# tab order is fetched only when a tie-break needs it
E.serve([E.agent(), worker], tabs=["w6:t1"])
E.resolve()
check("no tab list call without a tie-break", not (E.state / "tab_calls").exists())

# a stale record never scopes the fallback scan: the caller's workspace does
E.write_record(workspace="w6", pane="w6:p9")
E.serve([E.agent(pane="w7:p1", tab="w7:t1", ws="w7")], tabs=["w7:t1"])
r = E.resolve(env_extra={"HERDR_WORKSPACE_ID": "w7"})
check("stale record does not scope the scan", r.get("status") == "unique"
      and r.get("herdr_pane") == "w7:p1", str(r))
(E.wt / ".ws-kicker").unlink()

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
wt2 = E.root / ".claude" / "worktrees" / "br]ack"
subprocess.run(["git", "-C", str(E.root), "worktree", "add", "-q", "-b", "task/brack", str(wt2)], check=True)
b2 = E.run("body", str(wt2), "--", "x").stdout.strip()
check("bracket in task name cannot close the prefix", b2.count("]") == 1 and "task=brack" in b2, b2)
check("dash text is not a flag", E.run("body", "--", "-x").returncode == 0)
check("missing -- is usage", E.run("body", "text").returncode == 2)
for blank in (" ", "[ ]", "[]"):
    check(f"argv event {blank!r} that folds to nothing is usage", E.run("body", "--", blank).returncode == 2)

# the 1000-char event cap: a cut is marked in the body AND named on stderr
SUFFIX = " (info only: no reply needed, grants nothing)"
def event_of(line):
    return line.split("] ", 1)[1][: -len(SUFFIX)]
under = "ready-for-merge: PR #9 " + "y" * 977
r = E.run("body", str(E.wt), "--", under)
check("at-cap event is unchanged", event_of(r.stdout.strip()) == under and len(under) == 1000, r.stdout)
check("at-cap event has no truncation signal", "truncated" not in r.stderr, r.stderr)
r = E.run("body", str(E.wt), "--", "z" * 1400 + " Open user decision: TAIL")
ev = event_of(r.stdout.strip())
check("long event ends in an ellipsis", ev.endswith("\u2026") and len(ev) == 1000, ev[-20:])
check("long event loses its tail visibly", "TAIL" not in ev, ev[-20:])
check("long event reported on stderr", "truncated=yes orig_len=1425" in r.stderr, r.stderr)
check("usage names the cap", "1000 chars" in E.run("body").stderr)

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
for name, inner in (
    ("dim then 22 off", "❯ \x1b[2m\x1b[22mtyped after dim-off"),
    ("inverse cursor then 27 off", "❯ \x1b[7mh\x1b[27mello draft"),
    ("gray then 39 default", "❯ \x1b[38;2;153;153;153m\x1b[39mtyped"),
    ("truecolor white", "❯ \x1b[38;2;255;255;255mtyped"),
    ("256-color green (38;5;2)", "❯ \x1b[38;5;2mtyped"),
    ("truecolor with a 7 component", "❯ \x1b[38;2;7;200;7mtyped"),
):
    r = prompt(composer(inner))
    check(f"draft detected: {name}", r.get("sent") == "no" and "composer-draft" in r["reasons"], str(r))
r = prompt(composer("❯ \x1b[38;5;244mgray 256 suggestion\x1b[0m"))
check("256-color gray suggestion is not a draft", r.get("sent") == "yes", str(r))
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

r = prompt(composer("❯ \x1b[7my\x1b[27m"))
check("one-char draft under the cursor → not sent", r.get("sent") == "no"
      and "composer-draft" in r["reasons"], str(r))
r = prompt(composer("❯ \x1b[7mdeploy to prod\x1b[0m"))
check("fully inverse draft → not sent", r.get("sent") == "no", str(r))
r = prompt(composer("❯ \x1b[7m \x1b[27m"))
check("inverse-space cursor alone → sent", r.get("sent") == "yes", str(r))
for name, inner in (
    ("light theme black", "❯ \x1b[38;2;0;0;0mdeploy to prod"),
    ("light theme dark gray", "❯ \x1b[38;2;40;40;40mdeploy"),
    ("256-color near-black", "❯ \x1b[38;5;233mdeploy"),
):
    r = prompt(composer(inner))
    check(f"draft detected: {name}", r.get("sent") == "no" and "composer-draft" in r["reasons"], str(r))
r = prompt(composer("❯ \x1b[7mt\x1b[27m\x1b[2mry: run the tests\x1b[0m"))
check("cursor on a suggestion's first char → sent", r.get("sent") == "yes", str(r))
r = prompt(composer("❯ \x1b[7mh\x1b[27mello"))
check("cursor over typed text → not sent", r.get("sent") == "no", str(r))
for name, inner in (
    ("256-color 241 hint", "❯ \x1b[38;5;241mtry: dark-theme hint\x1b[0m"),
    ("truecolor 90 hint", "❯ \x1b[38;2;90;90;90mtry: dark-theme hint\x1b[0m"),
):
    r = prompt(composer(inner))
    check(f"not a draft: {name}", r.get("sent") == "yes", str(r))
for name, inner in (
    ("256-color 240", "❯ \x1b[38;5;240mdeploy"),
    ("truecolor 85", "❯ \x1b[38;2;85;85;85mdeploy to prod"),
    ("inverse char, plain space, same-line dim hint", "❯ \x1b[7mx\x1b[27m \x1b[2mhint\x1b[0m"),
    ("inverse char, dim padding, dim hint", "❯ \x1b[7mx\x1b[27;2m ? for shortcuts\x1b[0m"),
    ("light gray truecolor typed text", "❯ \x1b[38;2;197;200;198mdeploy to prod\x1b[0m"),
    ("256-color 250", "❯ \x1b[38;5;250mdeploy"),
    ("dim paste placeholder", "❯ \x1b[2m[Pasted text #1 +40 lines]\x1b[22m\x1b[7m \x1b[27m"),
    ("gray image placeholder", "❯ \x1b[38;2;153;153;153m[Image #2]\x1b[0m"),
    ("256-color near-black 237", "❯ \x1b[38;5;237mdeploy"),
    ("256-color light gray 252", "❯ \x1b[38;5;252mdeploy"),
    ("fully inverse draft + dim line elsewhere", "❯ \x1b[7mdeploy to prod\x1b[27m\n  \x1b[2mhint\x1b[0m"),
    ("one inverse char + dim line elsewhere", "❯ \x1b[7mh\x1b[27m\n  \x1b[2mhint\x1b[0m"),
    ("inverse char at line end before a dim line", "❯ \x1b[2mhint\x1b[0m \x1b[7mx\x1b[27m"),
):
    r = prompt(composer(inner))
    check(f"draft detected: {name}", r.get("sent") == "no" and "composer-draft" in r["reasons"], str(r))
r = prompt(composer("❯ \x1b[2mfirst half of a long\n  wrapped suggestion\x1b[0m"))
check("wrapped dim suggestion is not a draft", r.get("sent") == "yes", str(r))

# event text from stdin: quotes and $(...) are data, never shell
E.serve([E.agent()], tabs=["w6:t1"], read=composer("❯ "))
(E.state / "prompt_rc").write_text("0")
env = dict(os.environ, HERDR_ENV="1", HERDR_PANE_ID="w6:p1", PATH=f"{E.bin}:{os.environ['PATH']}")
env.pop("HERDR_WORKSPACE_ID", None)
r = subprocess.run([BASH, str(SCRIPT), "prompt", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="needs-decision: can't merge $(touch /tmp/x)\n")
sent = (E.state / "prompted").read_text() if (E.state / "prompted").exists() else ""
check("stdin event sent verbatim", kv(r.stdout).get("sent") == "yes"
      and "can't merge $(touch /tmp/x)" in sent, r.stdout + sent)
r = subprocess.run([BASH, str(SCRIPT), "prompt", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="needs-decision: " + "q" * 1200 + "\n")
sent = (E.state / "prompted").read_text() if (E.state / "prompted").exists() else ""
check("prompt route marks a cut too", kv(r.stdout).get("sent") == "yes" and "q\u2026 (info only" in sent
      and "truncated=yes orig_len=1216" in r.stderr, r.stdout + r.stderr)
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="x" * 3_000_000)
check("huge stdin is bounded, not E2BIG", r.returncode == 0 and len(r.stdout) < 1500
      and "truncated=yes" in r.stderr and "stdin_clipped=yes" in r.stderr, r.stderr[:200])
# a cut past the stdin read is flagged even when the kept window folds under the cap
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input=" " * 16384 + "needs-decision: approve merge?")
check("clipped stdin is never silent", r.returncode == 0 and "stdin_clipped=yes" in r.stderr
      and "\u2026 (info only" in r.stdout, r.stdout[-80:] + r.stderr)
for name, data in (("byte 16385 is a newline", " " * 16384 + "\nneeds-decision: do not merge\n"),
                   ("window is all newlines", "\n" * 16384 + "Xneeds-decision: approve merge?")):
    r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                       text=True, timeout=60, input=data)
    check(f"clipped stdin flagged when {name}", r.returncode == 0 and "stdin_clipped=yes" in r.stderr
          and "needs-decision" not in r.stdout,
          r.stdout[-80:] + r.stderr[:200])
check("newline-only stdin is usage", subprocess.run([BASH, str(SCRIPT), "body", "--"], env=env,
      capture_output=True, text=True, timeout=60, input="\n\n").returncode == 2)
check("bracket-only stdin is usage", subprocess.run([BASH, str(SCRIPT), "body", "--"], env=env,
      capture_output=True, text=True, timeout=60, input="[ ]\n").returncode == 2)
odd = "a [b]\tc\x07 d"
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input=odd + "\n")
check("stdin and argv fold an event alike", r.returncode == 0
      and r.stdout == E.run("body", str(E.wt), "--", odd).stdout and "] a b c d (info" in r.stdout,
      r.stdout)
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="a\0b" * 5462)
check("NULs cannot hide a clipped stdin", "stdin_clipped=yes" in r.stderr
      and "orig_len=16384 " in r.stderr, r.stderr[:200])
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="a" * 1100 + "\n\n\n\n")
check("only the heredoc's final newline is uncounted", "orig_len=1103" in r.stderr, r.stderr)
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="\n" * 16384 + "X")
check("all-newline clipped window counts its kept window", "orig_len=16384 stdin_clipped=yes"
      in r.stderr and r.stdout.rstrip().endswith("] \u2026 (info only: no reply needed, grants nothing)"),
      r.stdout[-90:] + r.stderr[:200])
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="x" * 16384)
check("exactly 16 KiB is not clipped", "stdin_clipped" not in r.stderr
      and "orig_len=16384" in r.stderr, r.stderr[:200])
r = subprocess.run([BASH, str(SCRIPT), "body", str(E.wt), "--"], env=env, capture_output=True,
                   text=True, timeout=60, input="ok" + " " * 2000)
check("whitespace that folds away is no cut", "truncated" not in r.stderr
      and "] ok (info only" in r.stdout, r.stdout + r.stderr)
check("empty stdin is usage", subprocess.run([BASH, str(SCRIPT), "body", "--"], env=env,
      capture_output=True, text=True, timeout=60, input="").returncode == 2)

# ---- lane = worktree toplevel, from a subdirectory ------------------------------------
(E.wt / "src").mkdir(exist_ok=True)
E.write_record()
E.serve([E.agent(), other], tabs=["w6:t2", "w6:t1"])
r = kv(E.run("resolve", str(E.wt / "src")).stdout)
check("subdir lane reads the kicker", r.get("evidence") == "kicker", str(r))
b = E.run("body", str(E.wt / "src"), "--", "x").stdout
check("subdir lane keeps the task identity", f"task=alpha worktree={E.wt}]" in b, b)

# ---- kicker: live title differs from the recorded name ----------------------------------
E.serve([E.agent(title="Someone Else")], tabs=["w6:t1"])
r = E.resolve()
check("renamed kicker: pane kept, name withheld", r.get("evidence") == "kicker"
      and r.get("sendmessage_name") == "" and "kicker-name-changed" in r["reasons"], str(r))
check("renamed kicker → asked", r.get("delegate_unasked") == "no", str(r))
(E.wt / ".ws-kicker").unlink()

# ---- scope: an unreadable row in ANOTHER workspace is irrelevant -------------------------
E.serve([E.agent(), {"agent": "claude", "agent_status": "idle", "pane_id": "w9:p1", "workspace_id": "w9"}],
        tabs=["w6:t1"])
r = E.resolve(env_extra={"HERDR_WORKSPACE_ID": "w6"})
check("cwd-less row elsewhere does not veto", r.get("status") == "unique", str(r))
E.serve([E.agent(), {"agent": "claude", "agent_status": "idle", "pane_id": "w6:p9", "workspace_id": "w6"}],
        tabs=["w6:t1"])
r = E.resolve(env_extra={"HERDR_WORKSPACE_ID": "w6"})
check("cwd-less row in scope still → unverified", r.get("status") == "unverified", str(r))

# ---- record refuses a tracked .ws-kicker -------------------------------------------------
(E.wt / ".ws-kicker").write_text("x\n")
subprocess.run(["git", "-C", str(E.wt), "add", "-f", ".ws-kicker"], check=True)
E.serve(get=E.agent())
r = kv(E.run("record", str(E.wt)).stdout)
check("tracked record is refused", r.get("recorded") == "no" and "kicker-file-tracked" in r["reasons"], str(r))
subprocess.run(["git", "-C", str(E.wt), "rm", "-q", "-f", "--cached", ".ws-kicker"], check=True)
(E.wt / ".ws-kicker").unlink()

# ---- backfill: the Manager records itself for lanes without a valid record ---------------
beta = E.root / ".claude" / "worktrees" / "beta"
subprocess.run(["git", "-C", str(E.root), "worktree", "add", "-q", "-b", "task/beta", str(beta)], check=True)
KA, KB = E.wt / ".ws-kicker", beta / ".ws-kicker"


def backfill(**kw):
    out = E.run("backfill", str(E.root), **kw).stdout
    return out, kv(out)


def lines(out, key):
    return [ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith(key + "=")]


TABS = ["w6:t1", "w6:t2", "w6:t3"]
E.serve([E.agent(), other, worker], tabs=TABS, get=E.agent(E.wt))
out, r = backfill(env_extra={"HERDR_PANE_ID": "w6:p3"})
check("backfill: refused from a worktree session", r.get("backfilled") == "0"
      and "caller-not-resolved-manager" in r["reasons"] and not KA.exists() and not KB.exists(), out)
E.serve([E.agent(), other, worker], tabs=TABS, get=other)
out, r = backfill(env_extra={"HERDR_PANE_ID": "w6:p2"})
check("backfill: a second root session never stamps itself", r.get("backfilled") == "0"
      and "caller-not-resolved-manager" in r["reasons"] and not KA.exists(), out)
out, r = backfill(env_extra={"HERDR_ENV": ""})
check("backfill: refused outside herdr", "not-in-herdr" in r["reasons"] and not KA.exists(), out)

E.serve([], get=E.agent())
out, r = backfill()
check("backfill: empty agent list proves nothing", r.get("backfilled") == "0"
      and "alpha unverified" in lines(out, "kept") and not KA.exists(), out)
E.serve([E.agent(), {}], tabs=TABS, get=E.agent())
out, r = backfill()
check("backfill: an unreadable row proves nothing", r.get("backfilled") == "0"
      and "alpha unverified" in lines(out, "kept") and not KA.exists(), out)

# An unregistered directory under .claude/worktrees is no lane.
scratch = E.root / ".claude" / "worktrees" / "scratch"
scratch.mkdir()
(scratch / ".git").write_text("gitdir: /nowhere\n")

E.serve([E.agent(), other, worker], tabs=TABS, get=E.agent())
out, r = backfill()
check("backfill: absent records written by the resolved Manager",
      {"alpha kicker-record-absent", "beta kicker-record-absent"} <= set(lines(out, "recorded"))
      and r.get("backfilled") == str(len(lines(out, "recorded"))), out)
check("backfill: unregistered dir skipped", "scratch" not in out and not (scratch / ".ws-kicker").exists(), out)
check("backfill: record names the caller", f"agent_session={UUID_A}" in KA.read_text()
      and f"main_repo={E.root}\n" in KB.read_text(), KA.read_text())
st = subprocess.run(["git", "-C", str(beta), "status", "--porcelain"], capture_output=True, text=True).stdout
check("backfill: record is git-excluded", ".ws-kicker" not in st, st)
r = E.resolve()
check("backfilled record resolves as kicker", r.get("evidence") == "kicker"
      and r.get("delegate_unasked") == "yes", str(r))
out, r = backfill()
check("backfill: nothing to do stays cheap", r.get("backfilled") == "0" and not r["reasons"], out)

# Another live session's record is never overwritten; a gone pane's is.
E.write_record(pane="w6:p2", tab="w6:t2", agent_session=UUID_B, sendmessage_name="Advisor")
other_rec = KA.read_text()
KB.write_text(KB.read_text().replace("pane=w6:p1", "pane=w6:p8"))
out, r = backfill()
check("backfill: live record of another session kept", KA.read_text() == other_rec
      and "alpha valid" in lines(out, "kept"), out)
check("backfill: gone pane rewritten", "beta kicker-pane-gone" in lines(out, "recorded")
      and "pane=w6:p1\n" in KB.read_text(), out)

# A pane in a workspace this server does not show may live on another server.
KB.write_text(f"version=1\nworkspace=w9\npane=w9:p1\nagent_session={UUID_B}\nmain_repo={E.root}\n")
out, r = backfill()
check("backfill: unseen workspace kept", "beta kicker-workspace-unseen" in lines(out, "kept")
      and "pane=w9:p1" in KB.read_text(), out)

# The recorded pane now hosts a different session → stale, rewritten.
E.write_record(pane="w6:p2", tab="w6:t2", agent_session="99999999-0000-0000-0000-000000000000")
out, r = backfill()
check("backfill: changed session rewritten", "alpha kicker-session-changed" in lines(out, "recorded")
      and f"agent_session={UUID_A}" in KA.read_text(), out)

# Records resolve rejects for other reasons stay for a human to look at.
E.write_record(pane="w6:p3", tab="w6:t3", agent_session=worker["agent_session"]["value"])
out, r = backfill()
check("backfill: moved kicker kept", "alpha kicker-cwd-moved" in lines(out, "kept"), out)
KA.write_text("pane=w6:p8\npane=w6:p9\nagent_session=x\nmain_repo=/x\n")
out, r = backfill()
check("backfill: malformed kept", "alpha kicker-record-malformed" in lines(out, "kept")
      and "pane=w6:p9" in KA.read_text(), out)
E.write_record(main_repo="/elsewhere", pane="w6:p8")
out, r = backfill()
check("backfill: another repo's record kept", "alpha kicker-repo-mismatch" in lines(out, "kept")
      and "main_repo=/elsewhere" in KA.read_text(), out)

# A symlink planted where the temp file goes is never written through.
target = E.root / "outside.txt"
target.write_text("keep\n")
KA.unlink()
for i in range(3):
    (E.wt / f".ws-kicker.planted{i}").symlink_to(target)
out, r = backfill()
check("backfill: planted symlinks untouched", target.read_text() == "keep\n"
      and "alpha kicker-record-absent" in lines(out, "recorded"), out)
for i in range(3):
    (E.wt / f".ws-kicker.planted{i}").unlink()
shutil.rmtree(scratch)
KA.unlink()
KB.unlink()
subprocess.run(["git", "-C", str(E.root), "worktree", "remove", "--force", str(beta)], check=True)

# ---- SendMessage name sanitizer ($HERDR_NAME_PRELUDE) ------------------------------------
def name_of(**over):
    a = E.agent()
    a.pop("terminal_title_stripped")
    a.update(over)
    E.serve([a], tabs=["w6:t1"])
    return E.resolve().get("sendmessage_name")

check("spinner glyph stripped", name_of(terminal_title_stripped="◐ Manager") == "Manager")
check("glyph strip keeps inner spaces", name_of(terminal_title_stripped="✳ My Repo") == "My Repo")
check("punctuation-led name kept", name_of(terminal_title_stripped="/habemus") == "/habemus")
check("falls back to terminal_title", name_of(terminal_title="✳ From Title") == "From Title")
check("herdr agent name is no address", name_of(name="from-agent-name") == "")
check("newline scrubbed", name_of(terminal_title_stripped="Manager\nnone") == "Manager none")
check("ANSI stripped", "\x1b" not in (name_of(terminal_title_stripped="\x1b[31mM\x1b[0m") or ""))
check("bidi override stripped", "‮" not in (name_of(terminal_title_stripped="Man‮ager") or ""))
check("64 chars pass", name_of(terminal_title_stripped="x" * 64) == "x" * 64)
check("65 chars → no name", name_of(terminal_title_stripped="x" * 65) == "")

E.tmp.cleanup()
if FAILS:
    print("manager.sh: FAILED")
    for f in FAILS:
        print("  -", f)
    raise SystemExit(1)
print("manager.sh: all tests passed")
