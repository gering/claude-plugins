#!/usr/bin/env python3
"""Tests for herdr-shell.sh — temporary herdr tabs for screen-based programs.

Hermetic: a fake `herdr` (stateful: per-call response sequences), fake `ps`,
`stty` and `security` on PATH, and a fake herdr socket that records requests.
Runs the REAL herdr-shell.sh end to end. Covers the degrade gate, open's
absolute-cwd rule, prefixing, record-before-ready and close-on-timeout, the
recorded-tab guard, prompt matching (last NON-EMPTY line, trailing padding,
quiet window), termios parsing (whole-word flags, missing fg fields), the
send-secret gate (prompt + echo=off ∧ icanon=on, raw/echo-on refused, the
secret never in any argv), close/sweep ownership rules, and that handover
never reads the pane. The live counterpart is smoke_herdr_shell.py.
"""
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "herdr-shell.sh"

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


# The fake herdr: responses per "argv1 argv2" from $STUB/config.json, each a
# list consumed in order (the last one repeats). "<key>@after" = {"other": K,
# "n": N, "seq": [...]} switches <key> to seq once K was called N times. Every
# call is logged as one JSON line to $STUB/argv.log.
HERDR_STUB = r'''#!/usr/bin/env python3
import json, os, sys
stub = os.environ["STUB"]
with open(os.path.join(stub, "argv.log"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
cfg = json.load(open(os.path.join(stub, "config.json")))
key = " ".join(sys.argv[1:3])
seq = cfg.get(key)
if seq is None:
    sys.stderr.write("unhandled stub call: " + key + "\n")
    sys.exit(9)
def count(k):
    c = os.path.join(stub, "count-" + k.replace(" ", "_"))
    return int(open(c).read()) if os.path.exists(c) else 0
sw = cfg.get(key + "@after")
if sw and count(sw["other"]) >= sw["n"]:
    seq = sw["seq"]
cpath = os.path.join(stub, "count-" + key.replace(" ", "_"))
n = count(key)
open(cpath, "w").write(str(n + 1))
r = seq[min(n, len(seq) - 1)]
out = r.get("out", "")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(r.get("err", ""))
sys.exit(r.get("rc", 0))
'''


def pinfo(shell=100, fg=100, name="zsh", omit=False):
    d = {"pane_id": "w1:p9", "shell_pid": shell}
    if not omit:
        d["foreground_process_group_id"] = fg
        d["foreground_processes"] = [{"pid": fg, "argv0": name, "name": name}]
    return {"out": {"result": {"process_info": d, "type": "pane_process_info"}}}


SHELL = pinfo()
PROGRAM = pinfo(fg=200, name="vim")
CREATED = {"out": {"result": {"tab": {"tab_id": "w1:t9", "label": "tmp-x"},
                              "root_pane": {"pane_id": "w1:p9"}}}}
OK = {"out": {"result": {"type": "ok"}}}
STTY_PW = "lflags: icanon isig iexten -echo echoe -echok echoke\ncchars: kill = ^U; lnext = ^V;\n"
STTY_RAW = "lflags: -icanon -isig -iexten -echo echoe -echok\ncchars: kill = ^U;\n"
STTY_ECHO = "lflags: icanon isig echo echoe echok\ncchars: kill = ^U;\n"
STTY_PW_KILLX = "lflags: icanon isig -echo echoe\ncchars: kill = ^X;\n"


def tablist(*tabs):
    return {"out": {"result": {"tabs": [
        {"tab_id": t, "label": l, "workspace_id": w} for t, l, w in tabs]}}}


def panelist(*panes):
    # "w1:p9" or ("w1:p9", "w1:t9") or ("w1:p9", "w1:t9", "claude")
    rows = []
    for p in panes:
        p = (p,) if isinstance(p, str) else p
        rows.append({"pane_id": p[0], "tab_id": p[1] if len(p) > 1 else None,
                     "agent": p[2] if len(p) > 2 else None})
    return {"out": {"result": {"panes": rows}}}


# What herdr shows by default: the recorded temp tab w1:t9 (pane w1:p9) and the
# caller's own tab w1:t1 (pane w1:p1, a claude agent).
LIVE_TABS = tablist(("w1:t9", "tmp-x", "w1"), ("w1:t1", "worker", "w1"))
LIVE_PANES = panelist(("w1:p9", "w1:t9"), ("w1:p1", "w1:t1", "claude"))


class FakeSocket:
    """A herdr socket double: one request per connection, answers ok."""

    def __init__(self, path):
        self.path, self.requests = path, []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(8)
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = c.recv(65536)
                if not chunk:
                    break
                buf += chunk
            req = json.loads(buf)
            self.requests.append(req)
            c.sendall((json.dumps({"id": req["id"], "result": {"type": "ok"}}) + "\n").encode())
            c.close()


_STUB_CACHE = tempfile.TemporaryDirectory()


class Env:
    def __init__(self, config, stty=STTY_PW, keychain=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stub = self.root / "stub"
        self.stub.mkdir()
        self.config = config
        config.setdefault("tab list", [LIVE_TABS])
        config.setdefault("pane list", [LIVE_PANES])
        (self.stub / "config.json").write_text(json.dumps(config))
        bindir = self.root / "bin"
        bindir.mkdir()
        self._exe(bindir / "herdr", HERDR_STUB)
        self._exe(bindir / "ps", "#!/bin/sh\necho ' ttys042'\n")
        (self.stub / "stty.txt").write_text(stty)
        # stty2.txt, when present, answers every call after the first.
        self._exe(bindir / "stty", """#!/bin/sh
d="$STUB"
n=$(cat "$d/stty.count" 2>/dev/null || echo 0); echo $((n+1)) > "$d/stty.count"
if [ "$n" -ge 1 ] && [ -f "$d/stty2.txt" ]; then cat "$d/stty2.txt"; else cat "$d/stty.txt"; fi
""")
        if keychain is not None:
            self._exe(bindir / "security", f"#!/bin/sh\nprintf '%s\\n' '{keychain}'\n")
        self.state = self.root / "state"
        self.sock = None
        # AF_UNIX paths are short (104 bytes on macOS): keep the socket in /tmp.
        self.sockdir = tempfile.TemporaryDirectory(dir="/tmp")
        self.env = dict(os.environ)
        self.env.update({
            "PATH": f"{bindir}:{self.env['PATH']}", "STUB": str(self.stub),
            "HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "w1", "HERDR_PANE_ID": "w1:p1",
            "HERDR_TAB_ID": "w1:t1", "HERDR_SHELL_STATE_DIR": str(self.state),
            "HERDR_SOCKET_PATH": os.path.join(self.sockdir.name, "s"),
            "HERDR_SHELL_POLL_SECS": "0.01", "HERDR_SHELL_NO_FOCUS": "",
        })

    @staticmethod
    def _exe(path, body):
        # macOS assesses every NEW executable on its first exec (~150 ms,
        # serialized across processes); a link to one already run is not new.
        # Each distinct body is written once and linked, never written through.
        src = Path(_STUB_CACHE.name) / hashlib.sha256(body.encode()).hexdigest()
        if not src.exists():
            src.write_text(body)
            src.chmod(0o755)
        path.unlink(missing_ok=True)
        path.symlink_to(src)

    def set(self, key, seq):
        self.config[key] = seq
        (self.stub / "config.json").write_text(json.dumps(self.config))
        c = self.stub / ("count-" + key.replace(" ", "_"))
        if c.exists():
            c.unlink()

    def socket(self):
        self.sock = FakeSocket(self.env["HERDR_SOCKET_PATH"])
        return self.sock

    def record(self, tab="w1:t9", pane="w1:p9", label="tmp-x", owner="w1:p1"):
        self.state.mkdir(exist_ok=True)
        r = subprocess.run(["bash", str(SCRIPT), "list"], env=self.env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        files = list(self.state.glob("tabs-*.tsv")) or [self.state / self._name()]
        with open(files[0], "a") as fh:
            fh.write("\t".join((tab, pane, label, "w1", owner, "0")) + "\n")

    def _name(self):
        import hashlib
        key = hashlib.sha256(self.env["HERDR_SOCKET_PATH"].encode()).hexdigest()[:12]
        return f"tabs-{key}.tsv"

    def records(self):
        f = self.state / self._name()
        return [l.split("\t") for l in f.read_text().splitlines()] if f.exists() else []

    def argv(self):
        f = self.stub / "argv.log"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def run(self, *args, env=None):
        e = dict(self.env)
        e.update(env or {})
        p = subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True, timeout=60)
        kv = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
        return p.returncode, kv, p.stdout, p.stderr


def visible(*lines):
    return {"out": "\n".join(lines) + "\n" + " " * 40 + "\n\n\n"}


def test_degrade():
    e = Env({})
    rc, *_ = e.run("list", env={"HERDR_ENV": ""})
    check("degrade: outside herdr → 3", rc == 3)
    rc, _, out, _ = e.run("help", env={"HERDR_ENV": ""})
    check("help works outside herdr", rc == 0 and "send-secret" in out)
    check("help lists the exit codes", "refused by a guard" in out)
    rc, *_ = e.run("frobnicate")
    check("unknown subcommand → 2", rc == 2)
    rc, *_ = e.run()
    check("no subcommand → 2", rc == 2)
    # -I: a stdlib-shadowing module in the caller's cwd must not be imported.
    (e.root / "json.py").write_text("raise SystemExit(42)\n")
    p = subprocess.run(["bash", str(SCRIPT), "list"], env=e.env, cwd=e.root, capture_output=True, text=True)
    check("cwd json.py does not shadow the stdlib", p.returncode == 0)


def test_open():
    e = Env({"tab create": [CREATED], "pane process-info": [SHELL], "tab close": [OK]})
    rc, *_ = e.run("open", "x", "relative/dir")
    check("open: relative cwd refused (2)", rc == 2)
    rc, *_ = e.run("open", "x", str(e.root / "missing"))
    check("open: missing dir refused (2)", rc == 2)
    rc, *_ = e.run("open", "-x", str(e.root))
    check("open: dash label refused (2)", rc == 2)
    rc, *_ = e.run("open", "x", str(e.root), env={"HERDR_PANE_ID": ""})
    check("open: no owner pane → 2", rc == 2)
    check("open: nothing created on usage errors", e.argv() == [])

    rc, kv, _, err = e.run("open", "x", str(e.root))
    check(f"open: ready → 0 ({err})", rc == 0 and kv.get("ready") == "yes")
    check("open: label prefixed", kv.get("label") == "tmp-x")
    create = next(a for a in e.argv() if a[:2] == ["tab", "create"])
    check("open: --no-focus, absolute --cwd",
          "--no-focus" in create and create[create.index("--cwd") + 1] == str(e.root))
    pis = sum(1 for a in e.argv() if a[:2] == ["pane", "process-info"])
    check("open: readiness is several samples, not one", pis >= 5)
    check("open: tab recorded with owner", e.records() == [["w1:t9", "w1:p9", "tmp-x", "w1", "w1:p1", e.records()[0][5]]])

    # The foreground flips back mid-gate: the streak restarts.
    e2 = Env({"tab create": [CREATED], "tab close": [OK],
              "pane process-info": [SHELL, SHELL, SHELL, PROGRAM, SHELL]})
    rc, *_ = e2.run("open", "tmp-x", str(e2.root))
    pis = sum(1 for a in e2.argv() if a[:2] == ["pane", "process-info"])
    check("open: a flip resets the 1 s streak", rc == 0 and pis >= 9)

    e4 = Env({"tab create": [{"out": {"result": {"tab": {"tab_id": "w1:t9"}, "root_pane": {}}}}],
              "tab close": [OK]})
    rc, *_ = e4.run("open", "x", str(e4.root))
    check("open: no pane id → 5 and the tab is closed again",
          rc == 5 and ["tab", "close", "w1:t9"] in e4.argv() and e4.records() == [])

    e3 = Env({"tab create": [CREATED], "pane process-info": [PROGRAM], "tab close": [OK]})
    rc, kv, *_ = e3.run("open", "x", str(e3.root), "--timeout", "500")
    check("open: readiness timeout → 1, ready=no", rc == 1 and kv.get("ready") == "no")
    check("open: timed-out tab closed", ["tab", "close", "w1:t9"] in e3.argv() and kv.get("closed") == "yes")
    check("open: timed-out record dropped", e3.records() == [])
    e3 = Env({"tab create": [CREATED], "pane process-info": [PROGRAM], "tab close": [{"rc": 1, "err": "x"}]})
    rc, kv, *_ = e3.run("open", "x", str(e3.root), "--timeout", "300")
    check("open: a failed close keeps the record", rc == 1 and kv.get("closed") == "no" and len(e3.records()) == 1)

    rc, *_ = e.run("open", "x", str(e.root))
    check("open: re-opening an id replaces its row", rc == 0 and len(e.records()) == 1)

    e6 = Env({"tab create": [{"rc": 1, "err": "timeout"}], "tab close": [OK],
              "tab list": [tablist(("w1:t9", "tmp-x", "w1"))]})
    rc, *_ = e6.run("open", "x", str(e6.root))
    check("open: a lost create answer closes the unrecorded tab it made",
          rc == 4 and ["tab", "close", "w1:t9"] in e6.argv())

    flat = {"out": {"result": {"tab_id": "w1:t9", "pane": {"pane_id": "w1:p9"}}}}
    e5 = Env({"tab create": [flat], "pane process-info": [SHELL]})
    rc, kv, *_ = e5.run("open", "x", str(e5.root))
    check("open: flat result.tab_id + pane.pane_id shape accepted", rc == 0 and kv.get("tab") == "w1:t9")
    e5 = Env({"tab create": [CREATED], "pane process-info": [SHELL]})
    rc, *_ = e5.run("open", "x", str(e5.root), env={"HERDR_WORKSPACE_ID": ""})
    check("open: no workspace → 2, nothing created", rc == 2 and e5.argv() == [])
    sd = e5.root / "shared"
    sd.mkdir(mode=0o777)
    sd.chmod(0o777)
    rc, *_ = e5.run("open", "x", str(e5.root), env={"HERDR_SHELL_STATE_DIR": str(sd)})
    check("open: world-writable state dir refused (6)", rc == 6 and e5.argv() == [])


def test_guard_unrecorded():
    e = Env({"pane run": [OK], "pane read": [visible("$")]})
    for args in (("run", "w1:t9", "ls"), ("read", "w1:t9"), ("text", "w1:t9", "x"),
                 ("keys", "w1:t9", "Enter"), ("state", "w1:t9"), ("prompt", "w1:t9", "x")):
        rc, *_ = e.run(*args)
        check(f"unrecorded tab refused (6): {args[0]}", rc == 6)
    check("unrecorded: herdr never called", e.argv() == [])

    # A record whose id herdr reissued to someone else's tab is never driven.
    e = Env({"pane run": [OK], "tab list": [tablist(("w1:t9", "● fix-login", "w1"))],
             "pane list": [panelist(("w1:p9", "w1:t9", "claude"))]})
    e.record()
    rc, *_ = e.run("run", "w1:t9", "ls")
    check("stale record (label/agent mismatch) refused and dropped",
          rc == 6 and e.records() == [] and not any(a[:2] == ["pane", "run"] for a in e.argv()))
    e = Env({"pane run": [OK], "pane list": [panelist(("w1:p9", "w1:t9"), ("w1:p2", "w1:t2"))]})
    e.record(owner="w1:p2")
    rc, *_ = e.run("run", "w1:t9", "ls")
    check("another live caller's tab is not driven (6)", rc == 6 and not any(a[:2] == ["pane", "run"] for a in e.argv()))


def test_read_and_keys():
    e = Env({"pane read": [visible("hello")], "pane send-keys": [OK]})
    e.record()
    rc, _, out, _ = e.run("read", "w1:t9")
    check("read: default --source visible",
          rc == 0 and "hello" in out and ["pane", "read", "w1:p9", "--source", "visible"] in e.argv())
    rc, *_ = e.run("keys", "w1:t9", "--foo")
    check("keys: dash key refused (2)", rc == 2)


def test_prompt():
    e = Env({"pane read": [visible("old output", "Password:   ")]})
    e.record()
    rc, kv, *_ = e.run("prompt", "w1:t9", "Password:$", "2000", "50")
    check("prompt: last NON-EMPTY line, padding stripped", rc == 0 and kv.get("matched") == "yes"
          and kv.get("line") == "Password:")
    rc, kv, *_ = e.run("prompt", "w1:t9", "^old", "300", "50")
    check("prompt: an earlier line never matches", rc == 1 and kv.get("matched") == "no")
    # A screen that keeps changing never satisfies the quiet window.
    e.set("pane read", [visible(f"tick {i}", "> ") for i in range(400)])
    rc, *_ = e.run("prompt", "w1:t9", ">$", "400", "200")
    check("prompt: changing screen → no match", rc == 1)
    rc, *_ = e.run("prompt", "w1:t9", "(", "100")
    check("prompt: bad regex → 2", rc == 2)


def test_state():
    e = Env({"pane process-info": [SHELL]}, stty=STTY_PW)
    e.record()
    rc, kv, *_ = e.run("state", "w1:t9")
    check("state: whole-word flags (-echo with echoe present)",
          rc == 0 and kv.get("echo") == "off" and kv.get("icanon") == "on" and kv.get("kill") == "^U")
    check("state: shell fg", kv.get("fg") == "shell" and kv.get("tty") == "ttys042")
    e.set("pane process-info", [pinfo(omit=True)])
    rc, kv, *_ = e.run("state", "w1:t9")
    check("state: missing fg fields → unknown, no crash", rc == 0 and kv.get("fg") == "unknown")
    e.set("pane process-info", [PROGRAM])
    rc, kv, *_ = e.run("state", "w1:t9")
    check("state: program name", kv.get("fg") == "vim")
    # Job control off: the child shares the shell's group, so pgid == shell pid.
    nojc = pinfo()
    nojc["out"]["result"]["process_info"]["foreground_processes"].append({"pid": 300, "argv0": "kimi"})
    e.set("pane process-info", [nojc])
    rc, kv, *_ = e.run("state", "w1:t9")
    check("state: a child in the shell's group is still a program", kv.get("fg") == "kimi")


def secret_env(stty, screen="Password:", keychain=None):
    e = Env({"pane read": [visible(screen)], "pane process-info": [PROGRAM]}, stty=stty, keychain=keychain)
    e.record()
    e.socket()
    sec = e.root / "sec"
    sec.write_text("hunter2-s3cret\n")
    sec.chmod(0o600)
    return e, str(sec)


def test_send_secret():
    e, sec = secret_env(STTY_PW)
    rc, kv, out, err = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check(f"send-secret: gate passes → sent ({err})", rc == 0 and kv.get("sent") == "yes")
    reqs = e.sock.requests
    check("send-secret: kill char + secret + Enter in ONE socket request",
          [r["method"] for r in reqs] == ["pane.send_input"]
          and reqs[0]["params"] == {"pane_id": "w1:p9", "text": "\x15hunter2-s3cret", "keys": ["Enter"]})
    check("send-secret: never in any herdr argv", not any("hunter2" in " ".join(a) for a in e.argv()))
    check("send-secret: never printed", "hunter2" not in out + err)
    check("send-secret: termios sampled last, pane not read after sending",
          [a[:2] for a in e.argv()[-2:]] == [["pane", "read"], ["pane", "process-info"]])

    e, sec = secret_env(STTY_RAW)
    rc, _, _, err = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: raw mode refused (6) → handover", rc == 6 and "handover" in err and not e.sock.requests)

    e, sec = secret_env(STTY_ECHO)
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: echo on refused (6)", rc == 6 and not e.sock.requests)

    e, sec = secret_env(STTY_PW, screen="$ ")
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec, "--timeout", "300")
    check("send-secret: no prompt match refused (6)", rc == 6 and not e.sock.requests)

    e, sec = secret_env(STTY_PW)
    os.chmod(sec, 0o644)
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: group/world-readable file refused (6)", rc == 6 and not e.sock.requests)
    link = e.root / "link"
    os.chmod(sec, 0o600)
    link.symlink_to(sec)
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", str(link))
    check("send-secret: symlink refused (6)", rc == 6 and not e.sock.requests)
    Path(sec).write_text("two\nlines\n")
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: control chars refused (6)", rc == 6 and not e.sock.requests)

    e, sec = secret_env(STTY_PW_KILLX)
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: clears with the tty's own kill char",
          rc == 0 and e.sock.requests[-1]["params"]["text"] == "\x18hunter2-s3cret")

    e, sec = secret_env("lflags: icanon -echo\ncchars: kill = <undef>;\n")
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: no kill char → refused (6)", rc == 6 and not e.sock.requests)
    e, sec = secret_env("lflags: icanon -echo\ncchars: erase = -; kill = ^U;\n")
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: secret holds the erase char → refused (6)", rc == 6 and not e.sock.requests)
    e, sec = secret_env(STTY_PW)
    e.set("pane process-info", [pinfo(omit=True)])
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: unidentifiable (setuid) reader → refused (6)", rc == 6 and not e.sock.requests)
    e, sec = secret_env(STTY_PW)
    e.set("pane read@after", {"other": "pane process-info", "n": 1, "seq": [visible("OTP code:")]})
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: prompt changed before the final send → refused (6)", rc == 6 and not e.sock.requests)

    e, sec = secret_env(STTY_PW)
    (e.stub / "stty2.txt").write_text("lflags: icanon -echo\ncchars: eol = s; kill = ^U;\n")
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: special chars re-validated in the final check (6)", rc == 6 and not e.sock.requests)
    e, sec = secret_env(STTY_PW)
    e.set("pane process-info", [pinfo(name="dash")])
    rc, _, _, err = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: a non-line-editing shell reading itself is refused (6)", rc == 6 and not e.sock.requests)
    e.set("pane process-info", [pinfo(name="-zsh")])
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: zsh builtin read -s accepted", rc == 0)

    e, _ = secret_env(STTY_PW, keychain="kc-s3cret")
    rc, kv, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-keychain", "svc")
    check("send-secret: keychain",
          rc == 0 and e.sock.requests[-1]["params"] == {"pane_id": "w1:p9", "text": "\x15kc-s3cret", "keys": ["Enter"]})
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-keychain", "svc", "--no-enter")
    check("send-secret: --no-enter is gone (2)", rc == 2)
    e, sec = secret_env("lflags: icanon -echo\ncchars: eol = s; kill = ^U; min = 1; time = 0;\n")
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: secret holds a remapped eol char → refused (6)", rc == 6 and not e.sock.requests)
    Path(sec).write_text("hunter210\n")
    e2, _ = secret_env("lflags: icanon -echo\ncchars: kill = ^U; min = 1; time = 0;\n")
    rc, *_ = e2.run("send-secret", "w1:t9", "--prompt", "Password:$", "--from-file", sec)
    check("send-secret: min/time digits are not special chars", rc == 0)
    rc, *_ = e.run("send-secret", "w1:t9", "--prompt", "x")
    check("send-secret: no source → 2", rc == 2)


def test_handover():
    e = Env({"pane run": [OK], "tab focus": [OK],
             "pane process-info": [SHELL, PROGRAM, pinfo(omit=True), PROGRAM, SHELL, SHELL],
             "tab list": [tablist(("w1:t9", "tmp-x", "w1"))]})
    e.record()
    rc, kv, *_ = e.run("handover", "w1:t9", "--run", "kimi login", "--timeout", "10000")
    check("handover: program then shell → done", rc == 0 and kv.get("result") == "done")
    check("handover: focuses the tab", ["tab", "focus", "w1:t9"] in e.argv())
    check("handover: never reads the pane", not any(a[:2] == ["pane", "read"] for a in e.argv()))

    e = Env({"tab focus": [OK], "pane process-info": [{"out": {"result": {"process_info": {}}}}, SHELL]})
    e.record()
    rc, kv, *_ = e.run("handover", "w1:t9", "--grace", "300")
    check("handover: an empty answer is not a program", rc == 1 and kv.get("result") == "no-program")
    e = Env({"tab focus": [OK], "pane process-info": [SHELL]})
    e.record()
    rc, kv, *_ = e.run("handover", "w1:t9", "--grace", "300")
    check("handover: nothing started → no-program (1)", rc == 1 and kv.get("result") == "no-program")

    e = Env({"tab focus": [OK], "pane process-info": [PROGRAM, {"rc": 1, "err": "gone"}],
             "tab list": [LIVE_TABS, tablist()]})
    e.record()
    rc, kv, *_ = e.run("handover", "w1:t9", "--timeout", "5000")
    check("handover: tab closed → tab-gone, record dropped",
          rc == 0 and kv.get("result") == "tab-gone" and e.records() == [])


def test_close():
    e = Env({"tab close": [OK], "pane list": [panelist(("w1:p1", "w1:t1", "claude"), ("w1:p2", "w1:t2"), ("w1:p8", "w1:t8"),
                                                        ("w1:p7", "w1:t7", "claude"), ("w1:p9", "w1:t9"))], "tab list": [tablist(
        ("w1:t7", "tmp-lane", "w1"), ("w1:t4", "tmp-bare", "w1"),
        ("w1:t9", "tmp-x", "w1"), ("w1:t5", "Manager", "w1"), ("w1:t6", "● ◇ tmp-y", "w1"),
        ("w1:t8", "tmp-z", "w1"))]})
    rc, *_ = e.run("close", "w1:t1")
    check("close: caller's own tab refused (6)", rc == 6)
    rc, *_ = e.run("close", "w1:t1", env={"HERDR_TAB_ID": ""})
    check("close: own tab found via the caller's pane when HERDR_TAB_ID is empty (6)", rc == 6)
    rc, *_ = e.run("close", "w1:t7")
    check("close: a tmp- tab with an agent in it is refused (6)", rc == 6 and ["tab", "close", "w1:t7"] not in e.argv())
    rc, *_ = e.run("close", "w1:t5")
    check("close: unrecorded, unprefixed refused (6)", rc == 6 and ["tab", "close", "w1:t5"] not in e.argv())
    rc, *_ = e.run("close", "w1:t6")
    check("close: an unrecorded glyphed tmp- tab (a lane tab) is refused",
          rc == 6 and ["tab", "close", "w1:t6"] not in e.argv())
    rc, *_ = e.run("close", "w1:t4")
    check("close: an unrecorded bare tmp- tab closes", rc == 0 and ["tab", "close", "w1:t4"] in e.argv())
    e.record("w1:t8", "w1:p8", "tmp-z", "w1:p2")
    rc, *_ = e.run("close", "w1:t8")
    check("close: another live caller's record refused (6)", rc == 6 and ["tab", "close", "w1:t8"] not in e.argv())
    e.record()
    rc, *_ = e.run("close", "w1:t9")
    check("close: recorded closes and drops", rc == 0 and [r[0] for r in e.records()] == ["w1:t8"])


def test_sweep():
    e = Env({"tab close": [OK],
             "tab list": [tablist(("w1:t9", "tmp-a", "w1"), ("w1:t8", "tmp-b", "w1"),
                                  ("w1:t7", "tmp-c", "w1"), ("w1:t6", "tmp-orphan", "w1"),
                                  ("w2:t6", "tmp-elsewhere", "w2"), ("w1:t5", "Manager", "w1"),
                                  ("w1:t2", "● tmp-cleanup", "w1"), ("w1:t12", "◇ tmp-done", "w1"),
                                  ("w1:t11", "tmp-reissued", "w1"))],
             "pane list": [panelist(("w1:p1", "w1:t1", "claude"), ("w1:p2", "w1:t2", "claude"),
                                    ("w1:p9", "w1:t9"), ("w1:p8", "w1:t8"), ("w1:p7", "w1:t7"),
                                    ("w1:p3", "w1:t3"), ("w1:p12", "w1:t12"), ("w1:p99", "w1:t11"))]})
    e.record("w1:t9", "w1:p9", "tmp-a", "w1:p1")      # mine
    e.record("w1:t8", "w1:p8", "tmp-b", "w1:p2")      # another live caller
    e.record("w1:t7", "w1:p7", "tmp-c", "w1:p77")     # owner pane gone
    e.record("w1:t11", "w1:p11", "tmp-old", "w1:p1")  # id reissued: pane no longer in it
    e.record("w1:t4", "w1:p4", "tmp-d", "w1:p1")      # tab already gone
    e.record("w1:t3", "w1:p3", "tmp-e", "-")          # no owner pane recorded
    e.config["tab list"][0]["out"]["result"]["tabs"].append({"tab_id": "w1:t3", "label": "tmp-e", "workspace_id": "w1"})
    e.set("tab list", e.config["tab list"])
    rc, kv, *_ = e.run("sweep")
    closed = {a[2] for a in e.argv() if a[:2] == ["tab", "close"]}
    check(f"sweep: mine + dead-owner + workspace orphan closed ({closed})",
          rc == 0 and closed == {"w1:t9", "w1:t7", "w1:t6"})
    check(f"sweep: counts ({kv})", kv == {"closed": "3", "stale": "2", "kept": "2", "failed": "0"})
    check("sweep: live caller's and owner-less records kept", sorted(r[0] for r in e.records()) == ["w1:t3", "w1:t8"])

    # One failing close does not abort the sweep.
    rc, *_ = e.run("sweep", env={"HERDR_WORKSPACE_ID": ""})
    check("sweep: no workspace → 2 (never all workspaces)", rc == 2)

    e = Env({"tab close": [{"rc": 1, "err": "gone"}, OK],
             "tab list": [tablist(("w1:t9", "tmp-a", "w1"), ("w1:t6", "tmp-orphan", "w1"))],
             "pane list": [panelist(("w1:p1", "w1:t1", "claude"), ("w1:p9", "w1:t9"))]})
    e.record("w1:t9", "w1:p9", "tmp-a", "w1:p1")
    rc, kv, *_ = e.run("sweep")
    check(f"sweep: continues past a failed close → 4 ({kv})",
          rc == 4 and ["tab", "close", "w1:t6"] in e.argv() and kv.get("failed") == "1" and kv.get("closed") == "1")


def test_sync():
    # The embedded Python escapes `bash -n`: compile it here so a syntax error
    # fails CI instead of the first real call.
    src = SCRIPT.read_text()
    body = src.split("<<'PY' || true\n", 1)[1].split("\nPY\n", 1)[0]
    try:
        compile(body, "herdr-shell.sh:HS_PY", "exec")
        check("embedded python compiles", True)
    except SyntaxError as e:
        check(f"embedded python compiles: {e}", False)
    # The state-glyph set must match herdr-tab-glyph.sh, or a glyph-prefixed
    # tmp- tab stops counting as prefixed.
    import re
    glyph_src = (HERE / "herdr-tab-glyph.sh").read_text()
    case_line = next(l for l in glyph_src.splitlines() if '"○ "*' in l)   # strip_glyph's case arm
    theirs = set(re.findall(r'"(.) "\*', case_line))
    ours = set(re.search(r"GLYPH_RE = re.compile\(r\"\^\(\?:\[([^\]]+)\]", src).group(1))
    check(f"glyph set in sync with strip_glyph (theirs {sorted(theirs)}, ours {sorted(ours)})", theirs and theirs == ours)


def main():
    for t in (test_degrade, test_open, test_guard_unrecorded, test_read_and_keys, test_prompt,
              test_state, test_send_secret, test_handover, test_close, test_sweep, test_sync):
        t()
    if FAILS:
        print("FAIL test_herdr_shell:")
        for f in FAILS:
            print(f"  - {f}")
        raise SystemExit(1)
    print("ok test_herdr_shell")


if __name__ == "__main__":
    main()
