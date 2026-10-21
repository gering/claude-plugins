#!/usr/bin/env python3
"""Live smoke test for herdr-shell.sh — run by hand inside herdr.

Gated on HERDR_ENV=1 (skips otherwise). Named smoke_*, not test_*, so
check-structure.py and CI never open tabs in a user's session. Opens one temporary
`tmp-smoke-secret` tab (never focused), drives a `read -s` prompt with a dummy
secret from a 0600 file, checks the termios gate refuses zle and vim, runs a
handover, and closes its own tab by id (never a workspace-wide sweep, which
would close other callers' unrecorded tmp- tabs). The dummy is the only secret.

    python3 plugins/work-system/scripts/smoke_herdr_shell.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parent / "herdr-shell.sh"
FAILS = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def hs(*args, env):
    p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120)
    kv = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
    return p.returncode, kv, p.stdout


def main():
    if os.environ.get("HERDR_ENV") != "1":
        print("skip smoke_herdr_shell: not inside herdr")
        return
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, HERDR_SHELL_STATE_DIR=os.path.join(tmp, "state"), HERDR_SHELL_NO_FOCUS="1")
        secret = Path(tmp) / "dummy"
        secret.write_text("smoke-dummy-42\n")
        secret.chmod(0o600)
        rc, kv, _ = hs("open", "smoke-secret", tmp, env=env)
        check("open → ready", rc == 0 and kv.get("ready") == "yes")
        tab = kv.get("tab", "")
        try:
            rc, *_ = hs("send-secret", tab, "--prompt", ".", "--from-file", str(secret), "--timeout", "3000", env=env)
            check("zle prompt refused (raw mode)", rc == 6)
            hs("run", tab, 'read -s "x?Secret:"; echo; echo "len=${#x}"', env=env)
            rc, *_ = hs("send-secret", tab, "--prompt", "Secret:$", "--from-file", str(secret), env=env)
            check("read -s prompt → sent", rc == 0)
            rc, _, screen = hs("read", tab, "--source", "recent", env=env)
            check("program received 14 chars", "len=14" in screen)
            check("secret not on screen", "smoke-dummy" not in screen)
            hs("run", tab, "vim -u NONE", env=env)
            rc, kv, _ = hs("prompt", tab, "~$", "5000", env=env)
            rc, kv, _ = hs("state", tab, env=env)
            check("vim: echo=off icanon=off", kv.get("fg") == "vim" and kv.get("icanon") == "off")
            rc, *_ = hs("send-secret", tab, "--prompt", "~$", "--from-file", str(secret), "--timeout", "2000", env=env)
            check("vim refused (raw mode)", rc == 6)
            hs("text", tab, ":q!", env=env)
            hs("keys", tab, "Enter", env=env)
            rc, kv, _ = hs("handover", tab, "--run", "sleep 1", "--timeout", "15000", env=env)
            check("handover: sleep ran and ended → done", rc == 0 and kv.get("result") == "done")
        finally:
            rc, kv, _ = hs("close", tab, env=env)
            check("smoke tab closed", rc == 0 and kv.get("closed") == "yes")
    if FAILS:
        sys.exit(1)


if __name__ == "__main__":
    main()
