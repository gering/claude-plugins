#!/usr/bin/env bash
# herdr-shell.sh — drive screen-based programs in temporary herdr tabs, and hand
# user-only flows (logins, 2FA) over to the user.
#
# Line-based automation (y/n installers, REPLs, password prompts read with
# `read`/getpass) belongs to `expect`, not here: stream matching consumes its
# buffer, so it has no stale-match problem and no tab lifecycle. When to use
# which: docs/herdr-shell.md. Every rule below comes from a measured result
# (herdr 0.8.2, macOS, zsh) summarized in that doc.
#
#   herdr-shell.sh open <label> <abs-cwd> [--workspace W] [--timeout MS]
#       Create a tab labelled `tmp-<label>` (prefix: $HERDR_SHELL_PREFIX), record
#       it, and wait until its shell has held the foreground ~1 s CONTINUOUSLY
#       (one sample lies: rc files flip the foreground several times). A relative
#       cwd is refused — herdr resolves it against the SERVER's cwd. On a
#       readiness timeout the tab is closed again. Prints tab=, pane=, label=,
#       ready=yes|no, and on a timeout closed=yes|no (no: the record stays,
#       close or sweep it). Needs $HERDR_PANE_ID: the owner of the record.
#   herdr-shell.sh run  <tab> <command>          `pane run`: text + Enter, at the SHELL
#   herdr-shell.sh text <tab> <text>             `pane send-text`, no Enter
#   herdr-shell.sh keys <tab> <key> [key…]       `pane send-keys` (ctrl+c, Enter, …)
#       Inside a screen program use `text` + `keys Enter`, not `run`: `run`
#       wraps the text in bracketed paste, which e.g. `vim -u NONE` takes as
#       keystrokes (`:q!` via run left vim open; text + Enter quit it).
#   herdr-shell.sh read <tab> [--source S] [--lines N] [--format F]
#       `pane read`, default --source visible.
#   herdr-shell.sh prompt <tab> <regex> [timeout-ms] [quiet-ms]
#       Wait until the LAST NON-EMPTY visible line matches <regex> (Python re,
#       search) AND the screen stayed unchanged for quiet-ms (default 400). Never
#       `--lines N` (bottom rows, blank below the cursor) and never bare
#       `wait-output` (it matches the previous identical prompt). The line is
#       matched with trailing whitespace stripped (the screen pads every row), so
#       anchor without it: `\[y/N\]$`, `Password:$` — then a prompt already
#       answered no longer matches.
#   herdr-shell.sh state <tab>
#       fg=shell|<program>|unknown, echo=, icanon=, kill= from `stty -a` on the
#       pane's tty. A missing process-info field (setuid fg such as top) is
#       `unknown`, never a crash.
#   herdr-shell.sh handover <tab> [--run CMD] [--timeout MS] [--grace MS]
#       Optionally run CMD, focus the tab for the user, then wait on PROCESS
#       STATE: a program takes the foreground, then the shell gets it back (or
#       the tab closes). Never reads the pane. result=done|tab-gone|no-program|timeout.
#   herdr-shell.sh send-secret <tab> --prompt <regex> (--from-keychain SERVICE
#                  [--account A] | --from-file PATH) [--timeout MS]
#       Send an agent-held secret through the socket API only (the request goes
#       file/Keychain → this process → $HERDR_SOCKET_PATH; never in any argv).
#       Refuses (exit 6) unless the prompt matched AND the tty is echo=off ∧
#       icanon=on — the kernel line-discipline password prompts. One request
#       carries the tty's kill char (clears the line), the secret and Enter. Raw-mode prompts
#       (masked or not; also zle at the bare shell prompt) are refused: use
#       `handover`. There is no echo probe: unobservable at no-echo prompts, and
#       it types keybindings into raw-mode programs. Never prints the secret and
#       never reads the pane afterwards.
#   herdr-shell.sh close <tab>
#       A recorded tab (not one another live caller pane owns), or an
#       unrecorded tab whose label STARTS with the prefix (no state glyph: a
#       glyphed "● tmp-x" is a lane tab). Never the caller's own, never a tab
#       an agent runs in.
#   herdr-shell.sh sweep [--workspace W]
#       Close recorded tabs owned by this caller pane (or by a pane that no
#       longer exists), then UNRECORDED tabs in the workspace whose label
#       starts with the prefix — a `tmp-` tab someone made by hand included.
#       Records of another live caller, and records without an owner pane, are
#       left alone; a record whose id now names another tab is forgotten.
#   herdr-shell.sh list              this server's records (tab pane label owner)
#
# Every subcommand except open/close/sweep/list works on RECORDED tabs only, so a
# typo can never aim keystrokes at a tab this helper did not open.
#
# Callers clean up with a trap, so an error path does not leave the tab behind
# (a `kill -9` still can — `sweep` is for that):
#     out="$(bash herdr-shell.sh open rebase "$PWD")" || exit
#     tab="$(printf '%s\n' "$out" | sed -n 's/^tab=//p')"
#     trap 'bash herdr-shell.sh close "$tab" >/dev/null 2>&1' EXIT
#
# Records live in ${HERDR_SHELL_STATE_DIR:-${XDG_STATE_HOME:-~/.local/state}/
# work-system/herdr-shell}, one file per herdr socket; callers with a different
# state dir do not see each other's records. Other knobs: HERDR_SHELL_PREFIX
# (default tmp-), HERDR_SHELL_CALL_TIMEOUT_SECS (10), HERDR_SHELL_POLL_SECS (0.1).
#
# A tab opened AT a worktree or the main checkout shares its cwd with the lane's
# own tab, and herdr-teardown.sh finds tabs by cwd: close temp tabs before
# /close, and sweep after a killed caller.
#
# Exit codes (consistent with herdr-agent.sh 2–5):
#   0  success              1  timeout / not reached (prompt, readiness, handover)
#   2  usage                3  not inside herdr, or herdr/python3 absent (degrade)
#   4  herdr call failed    5  herdr output was not the expected JSON shape
#   6  refused by a guard (unrecorded tab, termios gate, unsafe secret source)

# `read -d ''` rather than "$(cat <<'PY' …)": bash scans a heredoc inside $(…)
# for parentheses and quotes, and Python source trips it.
IFS= read -r -d '' HS_PY <<'PY' || true
import fcntl, functools, hashlib, json, os, platform, re, socket, stat, subprocess, sys, tempfile, time

PREFIX = os.environ.get("HERDR_SHELL_PREFIX") or "tmp-"
# The prefix is what sweep/close trust on an UNRECORDED tab: keep it specific.
if not re.fullmatch(r"[a-z][a-z0-9]{2,}-", PREFIX):
    PREFIX = "tmp-"
def env_secs(name, default):
    try:
        v = float(os.environ.get(name) or default)
        return v if v > 0 else default
    except ValueError:
        return default


CALL_TIMEOUT = env_secs("HERDR_SHELL_CALL_TIMEOUT_SECS", 10)
POLL = env_secs("HERDR_SHELL_POLL_SECS", 0.1)
READY_SAMPLES = 5           # x READY_POLL = ~1 s of continuous shell foreground
READY_POLL = 0.2
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*:[A-Za-z0-9_]+$")
# herdr-tab-glyph.sh state glyphs; stacked ones are stripped like its strip_glyph.
GLYPH_RE = re.compile(r"^(?:[○●◇◆✓◉]\s+)+")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
FIELDS = ("tab", "pane", "label", "workspace", "owner", "created")


class Fail(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg)
        self.code, self.msg = code, msg


def out(**kv):
    for k, v in kv.items():
        print(f"{k}={v}")


def clean(s):
    # Display data from the pane/herdr: a newline or escape would forge output lines.
    return CTRL_RE.sub(" ", str(s))


# ---- herdr access ------------------------------------------------------------
def herdr_raw(*args, timeout=None):
    try:
        p = subprocess.run(["herdr", *args], capture_output=True, text=True, errors="replace",
                           timeout=timeout or CALL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(4, f"herdr {args[0]} {args[1]}: {e.__class__.__name__}")
    if p.returncode != 0:
        raise Fail(4, f"herdr {args[0]} {args[1]}: " + clean(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else f"exit {p.returncode}"))
    return p.stdout


def herdr_json(*args):
    raw = herdr_raw(*args)
    try:
        res = json.loads(raw)["result"]
        if not isinstance(res, dict):
            raise ValueError
        return res
    except Exception:
        raise Fail(5, f"herdr {args[0]} {args[1]}: unexpected output")


def socket_call(method, params):
    # One request per connection (herdr closes after answering). The request is
    # built in memory and written to the socket: nothing here enters an argv.
    path = os.environ.get("HERDR_SOCKET_PATH") or ""
    if not path:
        raise Fail(3, "HERDR_SOCKET_PATH is not set")
    # Only a socket this user owns: anything else could be another user's
    # listener waiting for what gets written to it.
    try:
        st = os.stat(path)
    except OSError:
        raise Fail(4, "socket: HERDR_SOCKET_PATH does not exist")
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
        raise Fail(6, "refused: HERDR_SOCKET_PATH is not a socket owned by you")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(CALL_TIMEOUT)
    sent = False
    try:
        s.connect(path)
        s.sendall((json.dumps({"id": "herdr-shell", "method": method, "params": params}) + "\n").encode())
        sent = True
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError as e:
        # After the request went out, a missing answer does not mean the input
        # was not delivered: say so, so nobody resends a secret into whatever
        # holds the tty now.
        tail = "; the request was sent and may have been delivered - do not resend" if sent else ""
        raise Fail(4, f"socket {method}: {e.__class__.__name__}{tail}")
    finally:
        s.close()
    try:
        resp = json.loads(buf)
        if not isinstance(resp, dict):
            raise ValueError
    except Exception:
        raise Fail(5, f"socket {method}: unexpected output; the request was sent - do not resend")
    if "error" in resp:
        # The code only: an error message could quote the request back.
        raise Fail(4, f"socket {method}: " + clean((resp.get("error") or {}).get("code", "error")))
    return resp.get("result")


def process_info(pane):
    info = herdr_json("pane", "process-info", "--pane", pane).get("process_info")
    return info if isinstance(info, dict) else {}


def fg_state(info):
    # -> ("shell"|"program"|"unknown", program name). Decided by WHICH processes
    # hold the foreground (only the shell -> shell), like herdr-launch.sh's
    # extract_shell_ready -- not by fg pgid == shell pid, which only holds with
    # job control (`set +m` keeps every child in the shell's group). A setuid
    # foreground (top) makes herdr OMIT the fg fields entirely: unknown, not a crash.
    shell, procs = info.get("shell_pid"), info.get("foreground_processes")
    if not isinstance(shell, int) or isinstance(shell, bool) or not isinstance(procs, list) or not procs:
        return "unknown", "unknown"
    if not all(isinstance(p, dict) and isinstance(p.get("pid"), int) and not isinstance(p.get("pid"), bool)
               for p in procs):
        return "unknown", "unknown"
    others = [p for p in procs if p["pid"] != shell]
    if not others:
        return "shell", "shell"
    pgid = info.get("foreground_process_group_id")
    lead = next((p for p in others if p["pid"] == pgid), others[0])
    name = os.path.basename(str(lead.get("argv0") or lead.get("name") or "unknown"))
    return "program", clean(name) or "unknown"


def parse_stty(text):
    # `stty -a` lflags: `-echo` / `echo`, `-icanon` / `icanon`; whole words only
    # (echoe, echok, echoctl are other flags). kill = the canonical VKILL char.
    res = {"echo": "unknown", "icanon": "unknown", "kill": "unknown", "erase": "unknown", "werase": "unknown"}
    for neg, flag in re.findall(r"(?<![\w-])(-?)(echo|icanon)(?![\w-])", text or ""):
        res[flag] = "off" if neg else "on"
    # Every special char the tty defines (eof, eol, intr, lnext, ...), not
    # just the line-editing ones: remapped to a printable char, any of them
    # would act on a secret that contains it.
    res["cchars"] = {}
    for name, val in re.findall(r"(?<![\w-])([a-z][a-z0-9]*)\s*=\s*([^;\s]+);", text or ""):
        if name in ("min", "time", "line"):   # numeric settings, not chars
            continue
        res["cchars"][name] = val
        if name in ("kill", "erase", "werase"):
            res[name] = val
    return res


def cc_char(cc):
    # stty's rendering of a control char -> the char: `^U` -> "\x15", `^?` ->
    # DEL, a plain `#` -> "#"; `<undef>` or anything unreadable -> None.
    if cc == "^?":
        return "\x7f"
    if len(cc) == 2 and cc[0] == "^" and "@" <= cc[1].upper() <= "_":
        return chr(ord(cc[1].upper()) & 0x1F)
    if len(cc) == 1:
        return cc
    return None


@functools.lru_cache(maxsize=None)
def tty_of(shell):
    # A shell keeps its tty for life: one `ps` per process, not one per check
    # (the last check sits in send-secret's race window).
    try:
        tty = subprocess.run(["ps", "-o", "tty=", "-p", str(shell)], capture_output=True,
                             text=True, timeout=CALL_TIMEOUT).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if not re.fullmatch(r"(tty|pts/)?[A-Za-z0-9]+", tty or "") or tty in ("?", "??"):
        return ""
    return tty


def termios(info):
    shell = info.get("shell_pid")
    tty = tty_of(shell) if isinstance(shell, int) else ""
    if not tty:
        return "", parse_stty("")
    flag = "-f" if platform.system() == "Darwin" else "-F"
    try:
        text = subprocess.run(["stty", "-a", flag, "/dev/" + tty], capture_output=True,
                              text=True, timeout=CALL_TIMEOUT).stdout
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    return tty, parse_stty(text)


def last_line(text):
    for line in reversed((text or "").splitlines()):
        if line.strip():
            return line.rstrip()
    return ""


# ---- records -----------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def state_file():
    base = os.environ.get("HERDR_SHELL_STATE_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "work-system", "herdr-shell")
    os.makedirs(base, mode=0o700, exist_ok=True)
    st = os.stat(base)
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise Fail(6, f"state dir {base} must be yours and not group/world-writable")
    key = hashlib.sha256((os.environ.get("HERDR_SOCKET_PATH") or "").encode()).hexdigest()[:12]
    return os.path.join(base, f"tabs-{key}.tsv")


def load():
    try:
        with open(state_file()) as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return []
    recs = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) == len(FIELDS) and ID_RE.match(parts[0]) and ID_RE.match(parts[1]):
            recs.append(dict(zip(FIELDS, parts)))
    return recs


class Locked:
    # One flock serializes add() and drop(): an unlocked read-filter-replace
    # would silently discard a record another caller appended meanwhile.
    def __enter__(self):
        self.fd = os.open(state_file() + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def add_unlocked(rec):
    # Caller holds Locked. Fields are re-checked here: a tab or newline in one
    # would forge a record for a tab this helper never opened.
    if any(CTRL_RE.search(rec[f]) for f in FIELDS):
        raise Fail(2, "record field contains a control character")
    fd = os.open(state_file(), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write("\t".join(rec[f] for f in FIELDS) + "\n")


def drop_unlocked(*tabs):
    # Caller holds Locked (flock does not nest across descriptors: taking it
    # again here would deadlock).
    path = state_file()
    keep = [r for r in load() if r["tab"] not in tabs]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tabs-")   # O_EXCL, 0600
    with os.fdopen(fd, "w") as fh:
        fh.writelines("\t".join(r[f] for f in FIELDS) + "\n" for r in keep)
    os.replace(tmp, path)


def drop(*tabs):
    with Locked():
        drop_unlocked(*tabs)


class Live:
    # One snapshot of herdr's tabs and panes, for the checks that must not
    # trust a record alone: is the tab still there, still a temp tab (label
    # prefixed, no agent in it), and whose is it.
    def __init__(self):
        self.tabs = tabs_by_id()
        panes = [p for p in herdr_json("pane", "list").get("panes") or [] if isinstance(p, dict)]
        self.panes = {p.get("pane_id"): p for p in panes}
        self.agent_tabs = {p.get("tab_id") for p in panes if p.get("agent")}
        me = os.environ.get("HERDR_PANE_ID") or ""
        # $HERDR_TAB_ID can be empty; the caller's pane names its tab too.
        self.own_tab = os.environ.get("HERDR_TAB_ID") or (self.panes.get(me) or {}).get("tab_id")

    def disposable(self, tab, recorded):
        # A temp tab by its looks: prefixed label and no agent running in it.
        # An UNRECORDED tab must carry the prefix bare: a lane tab is stamped
        # with a state glyph ("● tmp-cleanup") and must not become sweepable
        # just because its worker exited. A recorded one may have been glyphed.
        t = self.tabs.get(tab)
        if t is None or tab == self.own_tab or tab in self.agent_tabs:
            return False
        label = str(t.get("label") or "")
        return prefixed(label) if recorded else label.startswith(PREFIX)

    def owner_live(self, owner):
        return owner_live(owner, set(self.panes))

    def pane_matches(self, rec):
        return (self.panes.get(rec["pane"]) or {}).get("tab_id") == rec["tab"]


def lookup(tab):
    rec = next((r for r in load() if r["tab"] == tab), None)
    if rec is None:
        raise Fail(6, f"{clean(tab)} is not a tab this helper opened (see: list)")
    live = Live()
    if rec["tab"] not in live.tabs:
        drop(rec["tab"])
        raise Fail(6, f"{clean(tab)} is gone; record dropped")
    # A record whose ids now name another tab (herdr restarted and reissued
    # them) must never be driven: the live tab has to still look like ours.
    if not live.disposable(rec["tab"], True) or not live.pane_matches(rec):
        drop(rec["tab"])
        raise Fail(6, f"refused: {clean(tab)} no longer matches its record (label, agent or pane); record dropped")
    if live.owner_live(rec["owner"]):
        raise Fail(6, f"refused: {clean(tab)} belongs to another live caller ({clean(rec['owner'])})")
    return rec


def tabs_by_id():
    return {t.get("tab_id"): t for t in herdr_json("tab", "list").get("tabs") or [] if isinstance(t, dict)}


def prefixed(label):
    return GLYPH_RE.sub("", str(label or "")).startswith(PREFIX)


# ---- argument helpers ----------------------------------------------------------
def opts(argv, spec):
    # spec: {"--flag": takes_value}. Returns (options, positionals).
    o, pos, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a in spec:
            if spec[a]:
                if i + 1 >= len(argv):
                    raise Fail(2, f"{a} needs a value")
                o[a] = argv[i + 1]
                i += 2
                continue
            o[a] = True
        elif a.startswith("--"):
            raise Fail(2, f"unknown option {a}")
        else:
            pos.append(a)
        i += 1
    return o, pos


def ms(value, name):
    if not re.fullmatch(r"[0-9]+", str(value)):
        raise Fail(2, f"{name} must be milliseconds")
    return int(value) / 1000.0


def regex(value):
    try:
        return re.compile(value)
    except re.error as e:
        raise Fail(2, f"bad regex: {e}")


# ---- subcommands ---------------------------------------------------------------
def workspace(o):
    ws = o.get("--workspace") or os.environ.get("HERDR_WORKSPACE_ID") or ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_]*", ws):
        raise Fail(2, "no usable workspace (--workspace or $HERDR_WORKSPACE_ID)")
    return ws


def created_ids(res):
    # The shapes herdr-launch.sh's tab-create parser accepts: result.tab.tab_id
    # (herdr 0.8) or a bare result.tab string, a flat result.tab_id, or the tab
    # id echoed under the root pane; the pane under root_pane, pane, or flat.
    def obj(v):
        return v if isinstance(v, dict) else {}
    tab, root, pane = res.get("tab"), obj(res.get("root_pane")), obj(res.get("pane"))
    tid = (tab if isinstance(tab, str) else obj(tab).get("tab_id")) or res.get("tab_id") or root.get("tab_id")
    pid = root.get("pane_id") or pane.get("pane_id") or res.get("pane_id")
    return (tid if isinstance(tid, str) else ""), (pid if isinstance(pid, str) else "")


def close_unrecorded_label(ws, label):
    # Caller holds Locked. herdr may have created the tab although its answer
    # was lost (timeout, bad JSON). While the lock is held no other open can be
    # between create and record, so an unrecorded tab with exactly this label is
    # the one we just made.
    try:
        recorded = {r["tab"] for r in load()}
        for tid, t in tabs_by_id().items():
            if (t.get("label") == label and t.get("workspace_id") == ws
                    and tid not in recorded and ID_RE.match(str(tid or ""))):
                herdr_raw("tab", "close", tid)
    except Fail:
        pass


def cmd_open(argv):
    o, pos = opts(argv, {"--workspace": True, "--timeout": True})
    if len(pos) != 2:
        raise Fail(2, "open <label> <abs-cwd>")
    label, cwd = pos
    if not os.path.isabs(cwd):
        raise Fail(2, "cwd must be absolute (herdr resolves a relative one against its server)")
    if not os.path.isdir(cwd):
        raise Fail(2, f"no such directory: {cwd}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", label):
        raise Fail(2, "label: letters, digits, . _ - (max 40)")
    label = label if label.startswith(PREFIX) else PREFIX + label
    ws = workspace(o)
    # The owner pane is what keeps a live caller's tab from other callers and
    # lets sweep reap it once the caller is gone: no pane, no tab.
    owner = os.environ.get("HERDR_PANE_ID") or ""
    if not ID_RE.match(owner):
        raise Fail(2, "no usable $HERDR_PANE_ID (run from inside a herdr pane)")
    deadline = time.monotonic() + ms(o.get("--timeout", "15000"), "--timeout")
    # Create and record under ONE lock, which sweep also takes for its snapshot:
    # otherwise a concurrent sweep sees a fresh prefixed tab with no record yet
    # and closes it as an orphan. Recorded BEFORE the readiness wait, so a caller
    # killed mid-wait still leaves a record that sweep can find.
    with Locked():
        try:
            res = herdr_json("tab", "create", "--workspace", ws, "--cwd", cwd, "--label", label, "--no-focus")
            tab, pane = created_ids(res)
            if not ID_RE.match(tab):
                raise Fail(5, "tab create returned no usable tab id")
        except Fail as f:
            close_unrecorded_label(ws, label)
            raise f
        if not ID_RE.match(pane):
            try:
                herdr_raw("tab", "close", tab)
            except Fail:
                pass
            raise Fail(5, "tab create returned no usable pane id; tab closed")
        # One row per tab id: a row left by a caller from before a herdr
        # restart that reissued this id would otherwise shadow ours.
        drop_unlocked(tab)
        add_unlocked({"tab": tab, "pane": pane, "label": label, "workspace": ws,
                      "owner": owner, "created": str(int(time.time()))})
    streak = 0
    while time.monotonic() < deadline:
        try:
            st, _ = fg_state(process_info(pane))
        except Fail:
            st = "unknown"
        streak = streak + 1 if st == "shell" else 0
        if streak >= READY_SAMPLES:
            out(tab=tab, pane=pane, label=label, ready="yes")
            return 0
        time.sleep(READY_POLL)
    closed = "no"   # record kept, so close/sweep can still reach the tab
    try:
        # Same guard as close: never a tab someone started an agent in meanwhile.
        if Live().disposable(tab, True):
            close_tab(tab)
            closed = "yes"
    except Fail:
        pass
    out(tab=tab, pane=pane, label=label, ready="no", closed=closed)
    print("open: the shell never held the foreground for 1 s", file=sys.stderr)
    return 1


def cmd_run(argv):
    if len(argv) != 2:
        raise Fail(2, "run <tab> <command>")
    herdr_raw("pane", "run", lookup(argv[0])["pane"], argv[1])
    return 0


def cmd_text(argv):
    if len(argv) != 2:
        raise Fail(2, "text <tab> <text>")
    herdr_raw("pane", "send-text", lookup(argv[0])["pane"], argv[1])
    return 0


def cmd_keys(argv):
    if len(argv) < 2:
        raise Fail(2, "keys <tab> <key> [key...]")
    if any(k.startswith("-") for k in argv[1:]):
        raise Fail(2, "a key may not start with -")
    herdr_raw("pane", "send-keys", lookup(argv[0])["pane"], *argv[1:])
    return 0


def cmd_read(argv):
    o, pos = opts(argv, {"--source": True, "--lines": True, "--format": True})
    if len(pos) != 1:
        raise Fail(2, "read <tab> [--source S] [--lines N] [--format F]")
    args = ["pane", "read", lookup(pos[0])["pane"], "--source", o.get("--source", "visible")]
    for f in ("--lines", "--format"):
        if f in o:
            args += [f, o[f]]
    sys.stdout.write(herdr_raw(*args))
    return 0


def wait_prompt(pane, rx, timeout, quiet):
    # Last non-empty visible line matches AND the screen is unchanged for `quiet`.
    deadline = time.monotonic() + timeout
    prev, changed, line = None, time.monotonic(), ""
    while True:
        text = herdr_raw("pane", "read", pane, "--source", "visible")
        now = time.monotonic()
        if text != prev:
            prev, changed = text, now
        line = last_line(text)
        if rx.search(line) and now - changed >= quiet:
            return True, line
        if now >= deadline:
            return False, line
        time.sleep(POLL)


def cmd_prompt(argv):
    if not 2 <= len(argv) <= 4:
        raise Fail(2, "prompt <tab> <regex> [timeout-ms] [quiet-ms]")
    pane = lookup(argv[0])["pane"]
    ok, line = wait_prompt(pane, regex(argv[1]),
                           ms(argv[2] if len(argv) > 2 else "30000", "timeout"),
                           ms(argv[3] if len(argv) > 3 else "400", "quiet"))
    out(matched="yes" if ok else "no", line=clean(line))
    return 0 if ok else 1


def gate(pane):
    info = process_info(pane)
    fg, name = fg_state(info)
    tty, t = termios(info)
    return info, fg, name, tty, t


def cmd_state(argv):
    if len(argv) != 1:
        raise Fail(2, "state <tab>")
    info, fg, name, tty, t = gate(lookup(argv[0])["pane"])
    out(fg=name if fg == "program" else fg, fg_pgid=info.get("foreground_process_group_id", "unknown"),
        shell_pid=info.get("shell_pid", "unknown"), tty=tty or "unknown",
        echo=t["echo"], icanon=t["icanon"], kill=t["kill"])
    return 0


def tab_gone(tab):
    # Only a SUCCESSFUL list that lacks the tab proves it gone; an unreachable
    # herdr is not evidence.
    try:
        return tab not in tabs_by_id()
    except Fail:
        return False


def cmd_handover(argv):
    o, pos = opts(argv, {"--run": True, "--timeout": True, "--grace": True})
    if len(pos) != 1:
        raise Fail(2, "handover <tab> [--run CMD] [--timeout MS] [--grace MS]")
    rec = lookup(pos[0])
    tab, pane = rec["tab"], rec["pane"]
    timeout = ms(o.get("--timeout", "600000"), "--timeout")
    grace = ms(o.get("--grace", "5000"), "--grace")
    if "--run" in o:
        herdr_raw("pane", "run", pane, o["--run"])
    focused = "no"
    # HERDR_SHELL_NO_FOCUS: test seam only (the live smoke test must not steal
    # the user's focus); a real handover always focuses.
    if os.environ.get("HERDR_SHELL_NO_FOCUS") != "1":
        try:
            herdr_raw("tab", "focus", tab)
            focused = "yes"
        except Fail:
            pass
    start = time.monotonic()
    seen_program, shell_streak, result = False, 0, "timeout"
    # Process state only — the pane is never read here, a credential field may be up.
    # A program that ends within one poll is never seen: result=no-program.
    while time.monotonic() - start < timeout:
        setuid_fg = False
        try:
            info = process_info(pane)
            fg, _ = fg_state(info)
            # A setuid program (top, sudo) makes herdr omit the fg fields while
            # shell_pid stays readable; an empty or malformed answer is neither.
            setuid_fg = isinstance(info.get("shell_pid"), int) and "foreground_processes" not in info
        except Fail:
            if tab_gone(tab):
                result = "tab-gone"
                break
            fg = None
        if fg == "program" or (fg == "unknown" and setuid_fg):
            seen_program, shell_streak = True, 0
        elif fg == "shell":
            shell_streak += 1
            if seen_program and shell_streak >= 2:
                result = "done"
                break
            if not seen_program and time.monotonic() - start >= grace:
                result = "no-program"
                break
        # Fast while waiting for the program to appear (a short one must not slip
        # between samples), slow once it runs: a login can take minutes.
        time.sleep(POLL if not seen_program else max(POLL, 0.5))
    if result == "tab-gone":
        drop(tab)
    out(focused=focused, result=result)
    return 0 if result in ("done", "tab-gone") else 1


def read_secret(o):
    if "--from-file" in o:
        # Checked on the OPENED fd (fstat), not the path: a path swapped between
        # a check and the open would send a file the check never saw. O_NOFOLLOW
        # refuses a symlink as the final component.
        try:
            fd = os.open(o["--from-file"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            raise Fail(6, "secret file not found, or a symlink")
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise Fail(6, "secret file must be a regular file you own, mode 600 or stricter")
            raw = fh.read()
    else:
        cmd = ["security", "find-generic-password", "-s", o["--from-keychain"]]
        if "--account" in o:
            cmd += ["-a", o["--account"]]
        try:
            p = subprocess.run(cmd + ["-w"], capture_output=True, timeout=CALL_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            raise Fail(6, "keychain lookup failed")
        if p.returncode != 0:
            raise Fail(6, "keychain item not found")
        raw = p.stdout
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Fail(6, "secret is not UTF-8")
    if secret.endswith("\r\n"):
        secret = secret[:-2]
    elif secret.endswith("\n"):
        secret = secret[:-1]
    # Control chars are line-discipline commands in canonical mode (^U kills the
    # line, ^C interrupts, a newline submits early): never send them as text.
    if not secret or CTRL_RE.search(secret):
        raise Fail(6, "secret is empty or contains control characters")
    return secret


def cmd_send_secret(argv):
    o, pos = opts(argv, {"--prompt": True, "--from-keychain": True, "--from-file": True,
                         "--account": True, "--timeout": True})
    if len(pos) != 1 or "--prompt" not in o:
        raise Fail(2, "send-secret <tab> --prompt <regex> (--from-keychain S [--account A] | --from-file P)")
    if ("--from-keychain" in o) == ("--from-file" in o):
        raise Fail(2, "exactly one of --from-keychain SERVICE / --from-file PATH")
    pane = lookup(pos[0])["pane"]
    rx = regex(o["--prompt"])
    # The secret is fetched FIRST: a Keychain lookup can block on an approval
    # dialog, and the prompt it is meant for must be checked after that, not before.
    secret = read_secret(o)

    def check():
        info, fg, _, _, t = gate(pane)
        if t["echo"] == "off" and t["icanon"] == "off":
            raise Fail(6, "refused: raw-mode prompt (echo off, icanon off; also the bare shell prompt) - use handover")
        if t["echo"] == "on":
            raise Fail(6, "refused: echo is on - the secret would be displayed")
        if not (t["echo"] == "off" and t["icanon"] == "on"):
            raise Fail(6, "refused: tty modes unknown")
        # A setuid reader (sudo) hides the foreground from herdr: the reader
        # cannot be identified, so nothing is sent to it.
        if fg == "unknown":
            raise Fail(6, "refused: the foreground program cannot be identified")
        procs = info.get("foreground_processes") or []
        # The shell itself in the foreground is fine only for a line-editing
        # shell's builtin `read -s` (its idle prompt is raw mode, refused above).
        # sh/dash read their command line canonically: an idle prompt there
        # would pass the termios check and run the secret as a command.
        if fg == "shell":
            name = os.path.basename(str(procs[0].get("argv0") or procs[0].get("name") or "")).lstrip("-")
            if name not in ("zsh", "bash"):
                raise Fail(6, f"refused: {clean(name) or 'the shell'} itself reads - only zsh/bash builtins are trusted")
        kill = cc_char(t["kill"])
        if kill is None:
            raise Fail(6, "refused: the tty has no kill char, so the line cannot be cleared first")
        # In canonical mode the tty's special chars are commands, not text.
        if any(c and c in secret for c in map(cc_char, t["cchars"].values())):
            raise Fail(6, "refused: the secret contains one of the tty's special chars")
        return [p.get("pid") for p in procs], kill

    ok, _ = wait_prompt(pane, rx, ms(o.get("--timeout", "30000"), "--timeout"), 0.4)
    if not ok:
        raise Fail(6, "refused: the prompt did not match")
    fg, _ = check()
    # Final gate: the prompt still matches, THEN the whole check again (same
    # reader, canonical, no-echo, special chars re-validated) -- the termios
    # sample is the last thing before the write, and its kill char is the one
    # sent. ONE request carries the line clear, the secret and Enter (always:
    # an unsubmitted secret would be typed input for whatever reads next).
    # What stays open is the round trip between this check and herdr writing.
    if not rx.search(last_line(herdr_raw("pane", "read", pane, "--source", "visible"))):
        raise Fail(6, "refused: the prompt changed before sending")
    fg2, kill = check()
    if fg2 != fg:
        raise Fail(6, "refused: the foreground changed before sending")
    params = {"pane_id": pane, "text": kill + secret, "keys": ["Enter"]}
    socket_call("pane.send_input", params)
    del secret, params
    out(sent="yes")
    return 0


def close_tab(tab):
    herdr_raw("tab", "close", tab)
    drop(tab)


def owner_live(owner, live):
    # True when the record belongs to ANOTHER caller whose pane still exists.
    # "-" (a record from before open required $HERDR_PANE_ID) cannot be
    # checked: close accepts it, sweep never reaps it.
    me = os.environ.get("HERDR_PANE_ID") or "-"
    if owner in ("-", me):
        return False
    return owner in live


def cmd_close(argv):
    if len(argv) != 1 or not ID_RE.match(argv[0]):
        raise Fail(2, "close <tab>")
    tab = argv[0]
    live = Live()
    if tab == live.own_tab:
        raise Fail(6, "refused: that is the caller's own tab")
    rec = next((r for r in load() if r["tab"] == tab), None)
    if rec is not None and tab not in live.tabs:
        drop(tab)
        out(closed="already")
        return 0
    if tab not in live.tabs:
        raise Fail(6, f"{tab} is neither recorded nor open")
    # Recorded or not, the live tab must still look like a temp tab: a stale
    # record may name a reissued id, and an unrecorded tab is trusted by label.
    if not live.disposable(tab, rec is not None) or (rec is not None and not live.pane_matches(rec)):
        if rec is not None:
            drop(tab)
        raise Fail(6, f"refused: {tab} is not a temp tab (label lacks {PREFIX}, or an agent runs in it)")
    if rec is not None and live.owner_live(rec["owner"]):
        raise Fail(6, f"refused: {tab} belongs to another live caller ({clean(rec['owner'])})")
    herdr_raw("tab", "close", tab)
    if rec is not None:
        drop(tab)
    out(closed="yes")
    return 0


def cmd_sweep(argv):
    o, pos = opts(argv, {"--workspace": True})
    if pos:
        raise Fail(2, "sweep [--workspace W]")
    ws = workspace(o)   # never all workspaces: the prefix pass trusts labels alone
    # Tabs and records are read under the lock open() holds from create to
    # record, so a tab that is being opened is either in both or in neither.
    with Locked():
        live = Live()
        recs = load()
    closed = stale = kept = failed = 0
    gone = []

    def try_close(tid):
        # One failure (a tab its owner's trap closed meanwhile) must not
        # abort the sweep and leave the rest open.
        nonlocal closed, failed
        try:
            herdr_raw("tab", "close", tid)
            closed += 1
            return True
        except Fail:
            failed += 1
            return False

    for r in recs:
        if r["tab"] not in live.tabs:
            gone.append(r["tab"])
            stale += 1
        elif not live.pane_matches(r) or not live.disposable(r["tab"], True):
            # The id now names something else (herdr reissued it): forget the
            # row, never close what it points at.
            gone.append(r["tab"])
            stale += 1
        elif r["owner"] != "-" and not live.owner_live(r["owner"]):
            if try_close(r["tab"]):
                gone.append(r["tab"])
        else:
            kept += 1
    recorded = {r["tab"] for r in recs}
    for tid, t in live.tabs.items():
        if (tid not in recorded and ID_RE.match(str(tid or ""))
                and t.get("workspace_id") == ws and live.disposable(tid, False)):
            try_close(tid)
    if gone:
        drop(*gone)
    out(closed=closed, stale=stale, kept=kept, failed=failed)
    return 0 if not failed else 4


def cmd_list(argv):
    if argv:
        raise Fail(2, "list")
    for r in load():
        print("\t".join((r["tab"], r["pane"], r["label"], r["owner"])))
    return 0


CMDS = {"open": cmd_open, "run": cmd_run, "text": cmd_text, "keys": cmd_keys, "read": cmd_read,
        "prompt": cmd_prompt, "state": cmd_state, "handover": cmd_handover,
        "send-secret": cmd_send_secret, "close": cmd_close, "sweep": cmd_sweep,
        "list": cmd_list}


def main(argv):
    if not argv or argv[0] not in CMDS:
        print("usage: herdr-shell.sh {open|run|text|keys|read|prompt|state|handover|send-secret|close|sweep|list} ...", file=sys.stderr)
        return 2
    try:
        return CMDS[argv[0]](argv[1:])
    except Fail as f:
        if f.msg:
            print(f"herdr-shell: {f.msg}", file=sys.stderr)
        return f.code


sys.exit(main(sys.argv[1:]))
PY

case "${1:-}" in
  '')
    echo "usage: ${0##*/} {open|run|text|keys|read|prompt|state|handover|send-secret|close|sweep|list|help} ..." >&2
    exit 2
    ;;
  -h|--help|help)
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

# Degrade, never block, like herdr-agent.sh: outside herdr (HERDR_ENV=1 is
# required here, since the helper creates tabs) there is nothing to drive.
if [ "${HERDR_ENV:-}" != "1" ] || ! command -v herdr >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "herdr-shell: not inside herdr (HERDR_ENV=1, herdr and python3 required)" >&2
  exit 3
fi

# -I (isolated): with -c, Python puts the cwd first on sys.path, so a json.py or
# socket.py in the caller's repo would shadow the stdlib and run inside the
# helper -- in send-secret, while it holds the secret.
exec python3 -I -c "$HS_PY" "$@"
