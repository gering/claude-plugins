#!/usr/bin/env python3
"""Tests for workers.sh / workers.py — the status part of /work-system:manager.

A real temp git repo with linked worktrees under .claude/worktrees/ gives the
shared classify_cwd a main root; a fake `herdr` on PATH serves canned JSON/text
per pane and logs every argv; a fake `gh` serves `pr list` / `pr view`; a fake
manager.sh (WS_MANAGER_SH) logs pings and answers sent=yes|no. mandate.sh is the
real one, so ready-for-merge runs against a really recorded review.

Covers list (roles, scope, flags question/asks/verify/draft, self), screen
(--lines clamp, recap join), question (options, selection, live-dialog run,
read-only), events (dedupe per pane + session, re-fire, /exit successor, no
UUID, ping text, on-blocked only on an undelivered ping or after the deadline,
user-scope hook file and its refusals, repo settings, lock, herdr failure keeps
the state, ready-for-merge per lane + head). Every run is checked through the
argv log: nothing ever reaches `herdr agent send-keys|prompt|send-text` or
`pane send-*`.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "workers.sh"
MANDATE = HERE / "mandate.sh"
BASH = shutil.which("bash") or "/bin/bash"
FAILS = []
CHECKS = [0]
UUID = "11111111-2222-3333-4444-555555555555"
UUID2 = "66666666-7777-8888-9999-000000000000"
RULE = "─" * 40
COMPOSER_CLEAR = "\n".join([RULE, "❯ ", RULE, " status"])
COMPOSER_DRAFT = "\n".join([RULE, "❯ half typed text", RULE, " status"])
DIALOG = "\n".join(["old 1. scrollback", "Bash command", "  rm -rf build",
                    "Do you want to proceed?", "❯ 1. Yes", "  2. Yes, and don't ask again",
                    "  7. stray", "  3. No, and tell Claude what to do differently (esc)"])
SENT_PREFIXES = ("agent send-keys", "agent prompt", "agent send-text", "pane send")
ALL_SENT = []


def check(name, cond, detail=""):
    CHECKS[0] += 1
    if not cond:
        FAILS.append(("%s %s" % (name, detail)).strip())


def git(*args, cwd):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(os.path.realpath(self.tmp.name))
        self.d = d
        self.repo = d / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.repo)
        self.wt = {}
        for t in ("fix-x", "fix-y"):
            p = self.repo / ".claude" / "worktrees" / t
            git("worktree", "add", "-q", "-b", "task/" + t, str(p), cwd=self.repo)
            self.wt[t] = p
        (d / "elsewhere").mkdir()
        self.s = d / "state"
        self.s.mkdir()
        self.bin = d / "bin"
        self.bin.mkdir()
        self.log = d / "argv.log"
        (self.bin / "herdr").write_text(r'''#!/usr/bin/env bash
S="%s"
echo "$*" >> "$S/../argv.log"
pane_file() { printf '%%s' "$1" | tr ':' '_'; }
case "$1 $2" in
  "agent list") [ -f "$S/list_rc" ] && exit "$(cat "$S/list_rc")"; cat "$S/list.json"; exit 0 ;;
  "agent get") f="$S/get-$(pane_file "$3").json"; [ -f "$f" ] || exit 1; cat "$f"; exit 0 ;;
  "workspace list") printf '%%s' '{"result":{"workspaces":[{"workspace_id":"w2","label":"app"}]}}'; exit 0 ;;
  "agent read")
    p="$(pane_file "$3")"; src=recent
    case "$*" in *"--source visible"*) src=visible ;; esac
    for f in "$S/read-$p-$src" "$S/read-$p"; do [ -f "$f" ] && { cat "$f"; exit 0; }; done
    exit 0 ;;
  *) exit 9 ;;
esac
''' % self.s)
        (self.bin / "gh").write_text(r'''#!/usr/bin/env bash
S="%s"
echo "gh $*" >> "$S/../argv.log"
case "$1 $2" in
  "pr list") [ -f "$S/gh_list" ] && cat "$S/gh_list"; exit 0 ;;
  "pr view") [ -f "$S/gh_view" ] || exit 1; cat "$S/gh_view"; exit 0 ;;
esac
exit 1
''' % self.s)
        self.mlog = d / "manager.log"
        (d / "manager.sh").write_text(
            '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "%s"\n'
            'printf "%%s\\n" "${HERDR_WORKSPACE_ID:-}" >> "%s/manager_ws"\ncat "%s/manager_reply"\n'
            % (self.mlog, self.s, self.s))
        (self.s / "manager_reply").write_text("sent=yes\n")
        self.hook_out = d / "hook.json"
        hk = d / "hook.sh"
        hk.write_text('#!/usr/bin/env bash\ncat >> "%s"\necho >> "%s"\n' % (self.hook_out, self.hook_out))
        for f in ("herdr", "gh"):
            (self.bin / f).chmod(0o755)
        hk.chmod(0o755)
        self.hook = str(hk)
        self.state = str(d / "st" / "events.json")
        self.xdg = d / "xdg"
        self.env = dict(os.environ, PATH="%s:%s" % (self.bin, os.environ["PATH"]),
                        WS_MANAGER_SH=str(d / "manager.sh"), XDG_CONFIG_HOME=str(self.xdg))
        for k in ("HERDR_PANE_ID", "HERDR_WORKSPACE_ID", "HERDR_ENV", "SETTINGS_PROJECT_ROOT"):
            self.env.pop(k, None)

    def agent(self, pane, where, status="idle", kind="claude", uuid=UUID, title="✳ Worker"):
        cwd = {"repo": self.repo, "elsewhere": self.d / "elsewhere"}.get(where) or self.wt[where]
        a = {"agent": kind, "agent_status": status, "cwd": str(cwd), "pane_id": pane,
             "tab_id": pane.split(":")[0] + ":t" + pane.split("p")[-1],
             "workspace_id": pane.split(":")[0], "terminal_title": title,
             "terminal_title_stripped": title.lstrip("✳ ")}
        if uuid:
            a["agent_session"] = {"kind": "id", "value": uuid}
        return a

    def serve(self, agents, screens=None):
        (self.s / "list.json").write_text(json.dumps({"result": {"agents": agents}}))
        for f in self.s.glob("get-*.json"):
            f.unlink()
        for f in self.s.glob("read-*"):
            f.unlink()
        for a in agents:
            p = a["pane_id"].replace(":", "_")
            (self.s / ("get-%s.json" % p)).write_text(json.dumps({"result": {"agent": a}}))
        for key, text in (screens or {}).items():
            (self.s / ("read-" + key.replace(":", "_"))).write_text(text)

    def run(self, *args, **env):
        e = dict(self.env, **env)
        r = subprocess.run([BASH, str(SCRIPT), *args], env=e, capture_output=True, text=True,
                           timeout=120, cwd=str(self.d))
        ALL_SENT.extend(self.sent())
        return r

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def sent(self):
        return [c for c in self.calls() if c.startswith(SENT_PREFIXES)]

    def pings(self):
        return self.mlog.read_text().splitlines() if self.mlog.exists() else []

    def hooks(self):
        if not self.hook_out.exists():
            return []
        return [json.loads(l) for l in self.hook_out.read_text().splitlines() if l.strip()]

    def close(self):
        self.tmp.cleanup()


def rows_of(r):
    return json.loads(r.stdout.split("summary:")[0])["agents"]


# --- list: roles, task, scope -------------------------------------------------- #
E = Env()
E.serve([E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x"), E.agent("w2:p3", "elsewhere")],
        {"w2:p1": "done\n" + COMPOSER_CLEAR, "w2:p2": "done\n" + COMPOSER_CLEAR,
         "w2:p3": "done\n" + COMPOSER_CLEAR})
r = E.run("list", "--all")
check("list: exit 0", r.returncode == 0, r.stderr)
check("list: manager row", "| manager | - |" in r.stdout, r.stdout)
check("list: worker row with task", "| worker | fix-x |" in r.stdout, r.stdout)
check("list: other row", "| other | - |" in r.stdout, r.stdout)
check("list: workspace label", "| w2 app |" in r.stdout, r.stdout)
check("list: summary", "summary: agents=3 workers=1 working=0 blocked=0 idle_or_done=3" in r.stdout, r.stdout)
check("list: quiet idle has no flags", "verify=0 asks=0 draft=0" in r.stdout, r.stdout)
r = E.run("list", "--json", HERDR_WORKSPACE_ID="w9")
check("list: env scope filters", rows_of(r) == [], r.stdout)
E.log.write_text("")
r = E.run("list", "--json", HERDR_WORKSPACE_ID="w2", HERDR_PANE_ID="w2:p1")
rows = rows_of(r)
check("list: own pane is self", [x["role"] for x in rows if x["pane"] == "w2:p1"] == ["self"], str(rows))
check("list: own pane is not read", not any(c.startswith("agent read w2:p1") for c in E.calls()))
check("list: json has cwd + full session", rows[1]["cwd"].endswith("fix-x") and rows[1]["session"] == UUID)
check("list: --workspace validated", E.run("list", "--workspace", "x;y").returncode == 2)
check("list: --all excludes --workspace", E.run("list", "--all", "--workspace", "w2").returncode == 2)
check("list: unknown flag", E.run("list", "--bogus").returncode == 2)
(E.s / "list_rc").write_text("1")
r = E.run("list", "--all")
check("list: herdr unreachable → exit 4, no rows", r.returncode == 4 and "|" not in r.stdout, r.stdout)
(E.s / "list_rc").unlink()
(E.s / "list.json").write_text("{not json")
check("list: malformed → exit 5", E.run("list", "--all").returncode == 5)
r = E.run("list", "--all", PATH="/usr/bin:/bin")
check("list: herdr missing → exit 3", r.returncode == 3, r.stderr)
r = subprocess.run(["python3", "-I", str(HERE / "workers.py"), "list"], capture_output=True, text=True)
check("list: direct call without the wrapper → exit 3", r.returncode == 3, r.stderr)

# --- flags ---------------------------------------------------------------------- #
for name, screen, flag in (
        ("verify: spinner", "✶ Pondering… (12s · esc to interrupt)\n" + COMPOSER_CLEAR, "verify=1"),
        ("verify: background tasks", "ok\n  ⏵⏵ auto mode on · 2 background tasks\n" + COMPOSER_CLEAR, "verify=1"),
        ("asks: trailing question", "● Shall I open the PR now?\n\n" + COMPOSER_CLEAR, "asks=1"),
        ("draft: composer text", "● Done.\n" + COMPOSER_DRAFT, "draft=1")):
    E.serve([E.agent("w2:p2", "fix-x")], {"w2:p2": screen})
    check(name, flag in E.run("list", "--all").stdout)
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
r = E.run("list", "--all")
check("question flag", "| question |" in r.stdout and "blocked=1" in r.stdout, r.stdout)
E.serve([E.agent("w2:p2", "fix-x", status="weird")], {"w2:p2": "x"})
check("unknown status counted", "unknown=1" in E.run("list", "--all").stdout)
E.serve([E.agent("w2:p2", "fix-x", title="| evil\nname")], {"w2:p2": "x"})
r = E.run("list", "--all")
check("name cell cannot forge a row", r.stdout.count("\n| ") == 2, r.stdout)

# --- screen ----------------------------------------------------------------------- #
E.serve([E.agent("w2:p2", "fix-x")], {"w2:p2": "※ recap: first half\n  second half\n\n❯ \x1b[31mred\x1b[0m"})
r = E.run("screen", "w2:p2", "--lines", "5000")
check("screen: clamp 200", "lines=200" in r.stdout and any("--lines 200" in c for c in E.calls()), r.stdout)
check("screen: recap joined", "recap=first half second half" in r.stdout, r.stdout)
check("screen: ansi stripped", "\x1b" not in r.stdout and "red" in r.stdout)
E.serve([E.agent("w2:p2", "fix-x")], {"w2:p2": "a\u009b2J b \x1b]52;c;QUJD\n❯ "})
r = E.run("screen", "w2:p2")
check("screen: C1 and unterminated escapes blanked", "\u009b" not in r.stdout and "\x1b" not in r.stdout
      and "52;c;QUJD" in r.stdout, repr(r.stdout))
check("list: a bad HA_CALL_TIMEOUT_SECS falls back, no traceback",
      E.run("list", "--all", HA_CALL_TIMEOUT_SECS="10s").returncode == 0)
check("screen: clamp 20", "lines=20" in E.run("screen", "w2:p2", "--lines", "1").stdout)
check("screen: default 80", "lines=80" in E.run("screen", "w2:p2").stdout)
check("screen: bad target", E.run("screen", "-x").returncode == 2)
check("screen: bad --lines", E.run("screen", "w2:p2", "--lines", "x").returncode == 2)
check("screen: gone pane → 4", E.run("screen", "w2:p9").returncode == 4)

# --- question --------------------------------------------------------------------- #
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
r = E.run("question", "w2:p2")
check("question: blocked=yes", "blocked=yes" in r.stdout, r.stdout)
check("question: live options only", r.stdout.count("option=") == 3 and "option=1\tYes" in r.stdout
      and "option=3\tNo" in r.stdout and "stray" not in r.stdout.split("--- dialog ---")[0], r.stdout)
check("question: selected", "selected=1" in r.stdout)
check("question: dialog verbatim", "--- dialog ---" in r.stdout and "rm -rf build" in r.stdout)
check("question: session", "session=" + UUID in r.stdout)
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG.replace("rm -rf build", "rm \u202e-rf build")})
r = E.run("question", "w2:p2")
check("question: bidi controls blanked in the dialog", "\u202e" not in r.stdout and "rm  -rf build" in r.stdout,
      repr(r.stdout))
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": "Pick one\nno numbered lines"})
r = E.run("question", "w2:p2")
check("question: no options → no guess", "option=" not in r.stdout and "selected=\n" in r.stdout
      and "Pick one" in r.stdout, r.stdout)
E.serve([E.agent("w2:p2", "fix-x")], {"w2:p2": "● Merge now?\n" + COMPOSER_CLEAR})
r = E.run("question", "w2:p2")
check("question: idle → blocked=no + asks", "blocked=no" in r.stdout and "asks=yes" in r.stdout
      and "composer=clear" in r.stdout, r.stdout)
stale = "\n".join(["Plan:", "1. Install dependencies", "2. Run tests", "", "done.",
                   "more output", "even more", "and more", "and more again", "Allow execution? [y/n]"])
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": stale})
r = E.run("question", "w2:p2")
check("question: an older list is not the dialog", "option=" not in r.stdout
      and "Allow execution" in r.stdout, r.stdout)
E.serve([E.agent("w2:p2", "fix-x", status="blocked")],
        {"w2:p2": "\n".join(["1. a", "2. b", "Do you want X?", "\u276f 1. Yes", "  2. No", "", "Esc to cancel"])})
r = E.run("question", "w2:p2")
check("question: interrupted run → the later dialog", r.stdout.count("option=") == 2
      and "option=1\tYes" in r.stdout and "selected=1" in r.stdout, r.stdout)
ask = "\n".join(["How should the branch land?", "\u276f 1. Rebase", "     Replay commits on main",
                 "  2. Merge", "     Create a merge commit", "  3. Type something.", "",
                 "Enter to select \u00b7 Esc to cancel"])
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": ask})
r = E.run("question", "w2:p2")
check("question: description lines keep every option", r.stdout.count("option=") == 3
      and "option=2\tMerge" in r.stdout and "selected=1" in r.stdout, r.stdout)
near = "\n".join(["1. Install dependencies", "2. Run tests", "", "Allow execution? [y/n]"])
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": near})
r = E.run("question", "w2:p2")
check("question: a prompt below a list → no options", "option=" not in r.stdout, r.stdout)
# read failure: herdr read fails → verify, screen exit 4
(E.bin / "herdr").write_text((E.bin / "herdr").read_text().replace(
    '  "agent read")\n', '  "agent read")\n    [ -f "$S/read_fail" ] && exit 1\n'))
E.serve([E.agent("w2:p2", "fix-x")], {"w2:p2": "x"})
(E.s / "read_fail").write_text("")
check("list: unreadable idle screen → verify", "verify=1" in E.run("list", "--all").stdout)
check("screen: read failure → 4", E.run("screen", "w2:p2").returncode == 4)
r = E.run("question", "w2:p2")
check("question: read failure → asks=unknown", "asks=unknown" in r.stdout, r.stdout)
(E.s / "read_fail").unlink()
check("answer subcommand does not exist", E.run("answer", "w2:p2").returncode == 2)
check("list/screen/question never write", E.sent() == [], str(E.sent()))
E.close()

# --- events: dedupe, ping, hook ------------------------------------------------- #
E = Env()
blocked = [E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="blocked")]
idle = [E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="idle")]
scr = {"w2:p2": DIALOG}
E.serve(blocked, scr)
check("events: --state required", E.run("events", "--all").returncode == 2)
check("events: --on-blocked needs a value", E.run("events", "--all", "--state", E.state, "--on-blocked").returncode == 2)
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: fired once", "summary: events=1" in r.stdout and "ping=sent" in r.stdout, r.stdout + r.stderr)
p = E.pings()
check("events: ping text names task and pane",
      len(p) == 1 and "fix-x" in p[0] and "w2:p2" in p[0] and p[0].startswith("prompt ")
      and p[0].split()[1] == str(E.wt["fix-x"]) and " -- worker in pane w2:p2 is blocked" in p[0], str(p))
check("events: no hook when ping delivered (escalate 0)", E.hooks() == [])
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: dedupe while still blocked", "summary: events=0" in r.stdout and len(E.pings()) == 1, r.stdout)
E.serve(idle, scr)
E.run("events", "--all", "--state", E.state, "--ping")
E.serve(blocked, scr)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: re-fires after leaving blocked", "summary: events=1" in r.stdout and len(E.pings()) == 2, r.stdout)
check("events: manager never pinged about itself", all("w2:p1" not in x for x in E.pings()))
E.serve([E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="blocked", uuid=UUID2)], scr)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: /exit successor (new UUID) starts fresh", "summary: events=1" in r.stdout, r.stdout)
st = json.loads(Path(E.state).read_text())
check("events: old session forgotten", not any(UUID in k for k in st) and any(UUID2 in k for k in st), str(st))
E.serve([E.agent("w2:p2", "fix-x", status="blocked", uuid="")], scr)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: no UUID → no blocked event", "summary: events=0" in r.stdout, r.stdout)
check("events: a row without a UUID is no observation (record kept)",
      any(UUID2 in k for k in json.loads(Path(E.state).read_text())))
E.serve([E.agent("w2:p1", "repo")], scr)
E.run("events", "--all", "--state", E.state, "--ping")
check("events: vanished pane forgotten", not any(k.startswith("blocked|") for k in json.loads(Path(E.state).read_text())))
E.serve(blocked, scr)
(E.s / "list_rc").write_text("1")
before = Path(E.state).read_text()
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: herdr unreachable → exit 4, state kept", r.returncode == 4 and Path(E.state).read_text() == before)
(E.s / "list_rc").unlink()
Path(E.state).write_text("{broken")
n0 = len(E.pings())
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: unreadable state → exit 2 before any ping", r.returncode == 2 and len(E.pings()) == n0, r.stderr)
os.remove(E.state)
E.serve([E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="blocked", uuid=UUID2)], scr)
E.log.write_text("")
r = E.run("events", "--all", "--state", E.state)
check("events: a new event without outlets is still printed", "event=blocked" in r.stdout
      and "outlets=none" in r.stdout and "summary: events=1" in r.stdout, r.stdout)
check("events: reads no manager screen and no composer",
      not any(c.startswith("agent read w2:p1") or "--format ansi" in c for c in E.calls()), str(E.calls()))
lock = open(E.state + ".lock", "a")
import fcntl
fcntl.flock(lock, fcntl.LOCK_EX)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: concurrent run skips", "summary: skipped=locked" in r.stdout and r.returncode == 0, r.stdout)
lock.close()
E.close()

E = Env()
blocked = [E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="blocked")]
E.serve(blocked, {"w2:p2": DIALOG})
(E.s / "manager_reply").write_text("sent=no\nreason=composer-draft\n")
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: failed ping reported", "ping=failed:composer-draft" in r.stdout, r.stdout)
hs = E.hooks()
check("events: hook gets JSON payload", len(hs) == 1 and hs[0]["event"] == "blocked"
      and hs[0]["pane"] == "w2:p2" and hs[0]["task"] == "fix-x" and hs[0]["manager_ping"] == "failed"
      and hs[0]["session"] == UUID and "last_line" in hs[0] and hs[0]["workspace"] == "w2", str(hs))
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: ping retried, hook not repeated", len(E.pings()) == 2 and len(E.hooks()) == 1)
check("events: a retry is a retry= line, not an event", "retry=blocked" in r.stdout
      and "event=" not in r.stdout and "summary: events=0" in r.stdout, r.stdout)
(E.s / "manager_reply").write_text("sent=yes\n")
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: retried ping lands once", "ping=sent" in r.stdout and len(E.pings()) == 3, r.stdout)
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: delivered ping not resent", "summary: events=0" in r.stdout and len(E.pings()) == 3, r.stdout)
E.close()

E = Env()
E.serve([E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
E.run("events", "--all", "--state", E.state, "--on-blocked", E.hook + " --flag 'two words'")
check("events: hook without ping", len(E.hooks()) == 1 and E.pings() == []
      and E.hooks()[0]["manager_ping"] == "off", str(E.hooks()))
st = json.loads(Path(E.state).read_text())
check("events: state keyed by pane+session", ("blocked|w2:p2|" + UUID) in st, str(st))
E.close()

E = Env()
E.serve([E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook, "--escalate-after", "300")
check("events: no hook before the deadline", E.hooks() == [])
st = json.loads(Path(E.state).read_text())
st["blocked|w2:p2|" + UUID]["ping_at"] -= 600
Path(E.state).write_text(json.dumps(st))
E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook, "--escalate-after", "300")
check("events: escalates after the deadline", len(E.hooks()) == 1 and len(E.pings()) == 1
      and E.hooks()[0]["manager_ping"] == "sent", str(E.hooks()))
E.close()

E = Env()
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
r = E.run("events", "--all", "--state", E.state, "--ping", HERDR_PANE_ID="w2:p2", HERDR_WORKSPACE_ID="w2")
check("events: own pane never an event", E.pings() == [] and "summary: events=0" in r.stdout, r.stdout)
E.close()

# --- events: user-scope hook file and repo settings ----------------------------- #
E = Env()
E.serve([E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
cfg = E.xdg / "work-system" / "events.toml"
cfg.parent.mkdir(parents=True)
cfg.write_text('on_blocked = ["%s"]\n' % E.hook)
cfg.chmod(0o644)
E.run("events", "--all", "--state", E.state)
check("events: hook from user-scope file", len(E.hooks()) == 1, str(E.hooks()))
os.remove(E.state)
cfg.chmod(0o666)
r = E.run("events", "--all", "--state", E.state)
check("events: world-writable user file refused", len(E.hooks()) == 1 and "refused" in r.stderr, r.stderr)
cfg.unlink()
os.symlink(str(E.d / "manager.sh"), str(cfg))
os.remove(E.state)
r = E.run("events", "--all", "--state", E.state)
check("events: symlinked user file refused", len(E.hooks()) == 1 and "refused" in r.stderr, r.stderr)
cfg.unlink()
(E.wt["fix-x"] / ".work-system.toml").write_text('[events]\non_blocked = ["%s"]\n' % E.hook)
os.remove(E.state)
E.run("events", "--all", "--state", E.state)
check("events: a repo cannot set the command", len(E.hooks()) == 1)
(E.wt["fix-x"] / ".work-system.toml").write_text("[events]\nenabled = false\n")
os.remove(E.state)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: a lane cannot silence itself", "ping=sent" in r.stdout and len(E.pings()) == 1, r.stdout)
(E.wt["fix-x"] / ".work-system.toml").unlink()
(E.repo / ".work-system.toml").write_text("[events]\nenabled = false\n")
os.remove(E.state)
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: repo events.enabled=false sends nothing", len(E.pings()) == 1 and len(E.hooks()) == 1
      and "outlets=disabled" in r.stdout, r.stdout)
(E.repo / ".work-system.toml").write_text("[events]\nescalate_after = 300\n")
os.remove(E.state)
E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
st = json.loads(Path(E.state).read_text())
st["blocked|w2:p2|" + UUID]["ping_at"] -= 600
Path(E.state).write_text(json.dumps(st))
E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("events: repo escalate_after applies", len(E.hooks()) == 2, str(E.hooks()))
E.close()

# --- events: ready-for-merge ------------------------------------------------------ #
E = Env()
wt = E.wt["fix-x"]
(wt / "f").write_text("a\n")
git("add", "f", cwd=wt)
git("commit", "-qm", "f", cwd=wt)
subprocess.run([BASH, str(MANDATE), "init", str(wt), "allow=commit,local-review",
                "authorized_by=user", "task=fix-x"], check=True, capture_output=True)
subprocess.run([BASH, str(MANDATE), "reviewed", str(wt)], check=True, capture_output=True)
head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
(E.s / "gh_list").write_text("7|OPEN|https://example.invalid/pr/7\n")


def view(head_oid, checks, draft=False, **over):
    v = {"headRefOid": head_oid, "isDraft": draft, "isCrossRepository": False,
         "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "",
         "statusCheckRollup": checks}
    v.update(over)
    (E.s / "gh_view").write_text(json.dumps(v))


GREEN_RUN = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
             {"__typename": "StatusContext", "state": "SUCCESS"}]
idle = [E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x")]
E.serve(idle, {"w2:p2": "● PR is up.\n" + COMPOSER_CLEAR})
view(head, [{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None}])
r = E.run("events", "--all", "--state", E.state, "--ping")
check("ready: pending check → no event", "summary: events=0" in r.stdout, r.stdout + r.stderr)
view("0" * 40, GREEN_RUN)
check("ready: unpushed head → no event", "events=0" in E.run("events", "--all", "--state", E.state, "--ping").stdout)
view(head, GREEN_RUN, draft=True)
check("ready: draft PR → no event", "events=0" in E.run("events", "--all", "--state", E.state, "--ping").stdout)
for name, kw, reason in (("no checks yet", {"checks": []}, "checks-none"),
                         ("conflicting", {"mergeable": "CONFLICTING"}, "conflicting"),
                         ("changes requested", {"reviewDecision": "CHANGES_REQUESTED"}, "changes-requested"),
                         ("fork PR", {"isCrossRepository": True}, "fork-pr"),
                         ("required check missing", {"mergeStateStatus": "BLOCKED"}, "merge-state-blocked"),
                         ("behind base", {"mergeStateStatus": "BEHIND"}, "merge-state-behind"),
                         ("merge state not computed", {"mergeStateStatus": "UNKNOWN"}, "merge-state-unknown"),
                         ("mergeable not computed", {"mergeable": "UNKNOWN"}, "mergeable-unknown")):
    checks = kw.pop("checks", GREEN_RUN)
    view(head, checks, **kw)
    r = E.run("ready", str(wt))
    check("ready: %s → %s" % (name, reason), "reason=%s" % reason in r.stdout
          and ("ready=unknown" if reason.endswith("-unknown") else "ready=no") in r.stdout, r.stdout)
    check("ready: %s → no event" % name, "events=0" in E.run("events", "--all", "--state", E.state, "--ping").stdout)
check("ready: usage", E.run("ready").returncode == 2 and E.run("ready", "-x").returncode == 2)
view(head, GREEN_RUN)
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("ready: fires", "event=ready-for-merge" in r.stdout and "pr=7" in r.stdout and "summary: events=1" in r.stdout,
      r.stdout + r.stderr)
check("ready: ping says merge is the human's",
      len(E.pings()) == 1 and "PR #7 is ready for merge" in E.pings()[0] and "human" in E.pings()[0], str(E.pings()))
r = E.run("events", "--all", "--state", E.state, "--ping")
check("ready: dedupe per head", "summary: events=0" in r.stdout and len(E.pings()) == 1, r.stdout)
check("ready: subcommand agrees", "ready=yes" in E.run("ready", str(wt)).stdout)
(E.d / "w2:p2").mkdir()
check("ready: a worker pane resolves to its lane (even beside a dir of that name)",
      "ready=yes" in E.run("ready", "w2:p2").stdout)
check("ready: a manager pane is refused", E.run("ready", "w2:p1").returncode == 2)
check("ready: a gone pane → 4", E.run("ready", "w2:p9").returncode == 4)
(E.s / "gh_view").unlink()
r = E.run("events", "--all", "--state", E.state, "--ping")
check("ready: gh blip → unknown, record kept", "ready=unknown" in E.run("ready", str(wt)).stdout
      and "summary: events=0" in r.stdout, r.stdout)
view(head, GREEN_RUN)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("ready: no re-fire after the blip", "summary: events=0" in r.stdout and len(E.pings()) == 1, r.stdout)
E.serve([E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x", status="working")], {"w2:p2": "x"})
E.log.unlink()
E.run("events", "--all", "--state", E.state, "--ping")
check("events: a working worker's screen is not read", not any(c.startswith("agent read") for c in E.calls()),
      str(E.calls()))
E.serve(idle, {"w2:p2": "● PR is up.\n" + COMPOSER_CLEAR})
r = E.run("events", "--all", "--state", E.state, "--ping")
check("ready: a busy spell does not re-arm", "summary: events=0" in r.stdout, r.stdout)
(wt / "f").write_text("b\n")
git("commit", "-qam", "g", cwd=wt)
head2 = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
view(head2, GREEN_RUN)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("ready: new head without review → no event", "summary: events=0" in r.stdout, r.stdout)
subprocess.run([BASH, str(MANDATE), "reviewed", str(wt)], check=True, capture_output=True)
r = E.run("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
check("ready: reviewed new head fires again", "summary: events=1" in r.stdout and len(E.pings()) == 2, r.stdout)
check("ready: hook only when the ping failed", E.hooks() == [])
check("ready: no merge command ever", not any(c.startswith(("gh pr merge", "gh pr review")) for c in E.calls()))
E.close()

# --- events: ready across a red re-run, shared lanes --------------------------- #
E = Env()
wt = E.wt["fix-x"]
subprocess.run([BASH, str(MANDATE), "init", str(wt), "allow=commit,local-review",
                "authorized_by=user", "task=fix-x"], check=True, capture_output=True)
subprocess.run([BASH, str(MANDATE), "reviewed", str(wt)], check=True, capture_output=True)
head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
(E.s / "gh_list").write_text("7|OPEN|https://example.invalid/pr/7\n")
idle = [E.agent("w2:p1", "repo"), E.agent("w2:p2", "fix-x")]
E.serve(idle, {"w2:p2": "● PR is up.\n" + COMPOSER_CLEAR})
(E.s / "manager_reply").write_text("sent=no\nreason=manager-working\n")
view(head, GREEN_RUN)
args = ("events", "--all", "--state", E.state, "--ping", "--on-blocked", E.hook)
E.run(*args)
view(head, [{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None}])
E.run(*args)
view(head, GREEN_RUN)
E.run(*args)
check("ready: red-then-green on the same head is no second event", len(E.hooks()) == 1, str(E.hooks()))
E.close()

E = Env()
wt = E.wt["fix-x"]
subprocess.run([BASH, str(MANDATE), "init", str(wt), "allow=commit,local-review",
                "authorized_by=user", "task=fix-x"], check=True, capture_output=True)
subprocess.run([BASH, str(MANDATE), "reviewed", str(wt)], check=True, capture_output=True)
head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
(E.s / "gh_list").write_text("7|OPEN|https://example.invalid/pr/7\n")
view(head, GREEN_RUN)
scr = {"w2:p2": "● ok\n" + COMPOSER_CLEAR, "w2:p3": "● ok\n" + COMPOSER_CLEAR}
for order in ((E.agent("w2:p2", "fix-x", status="working"), E.agent("w2:p3", "fix-x")),
              (E.agent("w2:p3", "fix-x"), E.agent("w2:p2", "fix-x", status="working"))):
    E.serve(list(order), scr)
    r = E.run("events", "--all", "--state", E.state, "--ping")
    check("ready: a shared lane with one busy pane waits (order %s)" % order[0]["pane_id"],
          "summary: events=0" in r.stdout, r.stdout)
E.serve([E.agent("w2:p2", "fix-x"), E.agent("w2:p3", "fix-x")], scr)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("ready: a shared lane fires once when all panes are idle",
      r.stdout.count("event=ready-for-merge") == 1, r.stdout)
E.serve([E.agent("w2:p2", "fix-x", status="working"), E.agent("w2:p3", "fix-x")], scr)
r = E.run("ready", "w2:p3")
check("ready: the subcommand waits for a busy sibling pane too",
      "ready=no" in r.stdout and "reason=lane-busy" in r.stdout, r.stdout)
E.serve([E.agent("w2:p2", "fix-x"), E.agent("w3:p3", "fix-x", status="working")], scr)
r = E.run("events", "--workspace", "w2", "--state", E.state + ".ws", "--ping")
check("ready: a busy pane of the lane in another workspace holds a scoped run",
      "summary: events=0" in r.stdout, r.stdout)
sub = E.agent("w2:p4", "fix-x", status="working", kind="codex")
(wt / "src").mkdir(exist_ok=True)
sub["cwd"] = str(wt / "src")
E.serve([E.agent("w2:p2", "fix-x"), sub], scr)
r = E.run("ready", "w2:p2")
check("ready: an agent working in a sub-directory of the lane counts", "reason=lane-busy" in r.stdout, r.stdout)
sub["agent_status"] = "idle"
E.serve([E.agent("w2:p2", "fix-x"), sub],
        dict(scr, **{"w2:p4": "✶ Pondering… (12s · esc to interrupt)\n" + COMPOSER_CLEAR}))
r = E.run("ready", "w2:p2")
check("ready: an idle sub-directory agent whose screen works counts", "reason=lane-busy" in r.stdout, r.stdout)
E.serve([sub], {"w2:p4": "● ok\n" + COMPOSER_CLEAR})
E.run("events", "--all", "--state", E.state, "--ping")
E.serve([E.agent("w2:p2", "fix-x")], scr)
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: an agent in a lane sub-directory keeps the ready record",
      "event=ready-for-merge" not in r.stdout and "summary: events=0" in r.stdout, r.stdout)
E.serve([E.agent("w2:p2", "fix-x")], scr)
os.symlink(str(E.d / "victim"), E.state + ".sym.lock")
r = E.run("events", "--all", "--state", E.state + ".sym", "--ping")
check("events: a symlinked lock file is refused, not followed",
      r.returncode == 2 and not (E.d / "victim").exists(), r.stdout + r.stderr)
E.close()

# --- events: unknown flicker, scopes, hook cap, ping workspace, raw cwd -------- #
E = Env()
blk = lambda st="blocked", pane="w2:p2", where="fix-x": E.agent(pane, where, status=st)
E.serve([E.agent("w2:p1", "repo"), blk()], {"w2:p2": DIALOG})
E.run("events", "--all", "--state", E.state, "--ping")
E.serve([E.agent("w2:p1", "repo"), blk("weird")], {"w2:p2": DIALOG})
E.run("events", "--all", "--state", E.state, "--ping")
E.serve([E.agent("w2:p1", "repo"), blk()], {"w2:p2": DIALOG})
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: unknown status flicker does not re-fire", "summary: events=0" in r.stdout
      and len(E.pings()) == 1, r.stdout)
check("events: ping resolves in the worker's workspace",
      (E.s / "manager_ws").read_text().split() == ["w2"], (E.s / "manager_ws").read_text())
E.serve([E.agent("w2:p1", "repo"), blk(), E.agent("w3:p5", "fix-y", status="blocked")], {"w2:p2": DIALOG})
E.run("events", "--all", "--state", E.state, "--ping")
check("events: w3 worker fired", len(E.pings()) == 2)
E.run("events", "--workspace", "w2", "--state", E.state, "--ping")
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: a narrower scope keeps other workspaces' records", "summary: events=0" in r.stdout
      and len(E.pings()) == 2, r.stdout)
E.close()

E = Env()
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
E.run("events", "--all", "--state", E.state, "--ping")
before = set(json.loads(Path(E.state).read_text()))
E.serve([])
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: an empty agent list forgets nothing",
      before and set(json.loads(Path(E.state).read_text())) == before, r.stdout)
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: no re-fire after an empty list", "summary: events=0" in r.stdout and len(E.pings()) == 1, r.stdout)
E.serve([E.agent("w2:p2", "fix-x", status="blocked", uuid=None)], {"w2:p2": DIALOG})
E.run("events", "--all", "--state", E.state, "--ping")
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
r = E.run("events", "--all", "--state", E.state, "--ping")
check("events: one poll without a UUID forgets nothing", "summary: events=0" in r.stdout
      and len(E.pings()) == 1, r.stdout)
E.close()

E = Env()
E.serve([E.agent("w2:p2", "fix-x", status="blocked"), E.agent("w3:p3", "fix-y")], {"w2:p2": DIALOG})
E.run("events", "--workspace", "w2", "--state", E.state)
E.serve([E.agent("w3:p3", "fix-y")])
E.run("events", "--workspace", "w2", "--state", E.state)
check("events: a scope with no agents left forgets its vanished panes",
      not any(k.startswith("blocked|w2:p2") for k in json.loads(Path(E.state).read_text())))
E.close()

E = Env()
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
(E.s / "manager_reply").write_text("sent=no\nreason=manager-is-self\n")
r1 = E.run("events", "--all", "--state", E.state, "--ping")
r2 = E.run("events", "--all", "--state", E.state, "--ping")
check("events: a ping that cannot land is not retried", "ping=final:manager-is-self" in r1.stdout
      and len(E.pings()) == 1 and "summary: events=0" in r2.stdout, r1.stdout + r2.stdout)
bg = E.d / "bg-hook.sh"
bg.write_text("#!/usr/bin/env bash\n( sleep 20 ) &\nexit 0\n")
bg.chmod(0o755)
E.serve([E.agent("w2:p2", "fix-x", status="blocked", uuid=UUID2)], {"w2:p2": DIALOG})
t0 = time.time()
E.run("events", "--all", "--state", E.state, "--on-blocked", str(bg))
st = json.loads(Path(E.state).read_text())
check("events: a hook's background child does not hold the run",
      time.time() - t0 < 15 and st.get("blocked|w2:p2|" + UUID2, {}).get("hooked") is True, str(st))
E.close()

E = Env()
E.serve([E.agent("w2:p2", "fix-x", status="blocked")], {"w2:p2": DIALOG})
bad = E.d / "bad-hook.sh"
bad.write_text("#!/usr/bin/env bash\necho x >> %s\nexit 1\n" % (E.d / "bad.log"))
bad.chmod(0o755)
for _ in range(5):
    E.run("events", "--all", "--state", E.state, "--on-blocked", str(bad))
check("events: failing hook retried at most 3 times",
      len((E.d / "bad.log").read_text().splitlines()) == 3)
ro = E.d / "ro"
ro.mkdir()
ro.chmod(0o500)
r = E.run("events", "--all", "--state", str(ro / "sub" / "s.json"))
ro.chmod(0o700)
check("events: unusable state path → exit 2, no traceback", r.returncode == 2
      and "Traceback" not in r.stderr and "state file" in r.stderr, r.stderr)
E.close()

E = Env()
odd = E.repo / ".claude" / "worktrees" / "a  b"
git("worktree", "add", "-q", "-b", "task/odd", str(odd), cwd=E.repo)
E.wt["odd"] = odd
E.serve([E.agent("w2:p2", "odd", status="blocked", title="\u202eevil")], {"w2:p2": DIALOG})
r = E.run("events", "--all", "--state", E.state, "--ping")
p = E.pings()
check("events: raw cwd reaches the ping (no whitespace fold)", len(p) == 1 and str(odd) in p[0], str(p))
check("events: the ping text carries no worktree name", len(p) == 1
      and p[0].split(" -- ", 1)[1].startswith("worker in pane w2:p2 is blocked"), str(p))
r = E.run("list", "--all")
check("list: bidi override blanked", "\u202e" not in r.stdout, r.stdout)
E.close()

check("nothing ever wrote to a pane", ALL_SENT == [], str(ALL_SENT))

if FAILS:
    print("FAIL: %d of %d" % (len(FAILS), CHECKS[0]))
    for f in FAILS:
        print("  - " + f)
    raise SystemExit(1)
print("workers.sh: all tests passed (%d checks)" % CHECKS[0])
