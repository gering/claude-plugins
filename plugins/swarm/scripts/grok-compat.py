#!/usr/bin/env python3
"""grok-compat.py — does this Grok model ENFORCE --json-schema? Probe once, cache.

The swarm adapter is built on schema JSON. A model that merely accepts
`--json-schema` and returns `structuredOutput: null` fails LATE, after a full
review was paid for. That used to be guarded by a hand-maintained allowlist,
which made every new Grok release a code edit. This replaces the list with a
measurement:

  * one tiny SYNTHETIC call — no repo data, no tools, no web, an empty temp cwd.
    The prompt asks for a plain-English sentence and never mentions JSON, so a
    schema-shaped answer can only come from enforcement, not from cooperation;
  * the verdict is cached per (model, grok CLI version, probe contract version),
    privately and atomically, and every cached record is re-validated on read;
  * a lock makes concurrent callers (one adapter process per review cluster)
    share ONE probe instead of each paying for their own.

Usage:
  grok-compat.py check  --model ID [--cli-version V]   cache only, never probes
  grok-compat.py ensure --model ID [--cli-version V]   cache, else probe once

Output is `key=value` lines on stdout (compat, source, reason, model,
cli_version, contract, actual_model, checked_at). Exit codes:
  0  compatible            — enforced structured output was observed
  1  incompatible          — the call completed and the output was NOT enforced
  3  unknown               — no verdict: not cached (check), or the probe could
                             not run/finish (timeout, launch failure, CLI error)
  2  usage / unsafe cache directory
A caller must treat 3 as "not established", never as success.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

# Bump when the prompt, schema, argv or acceptance rule changes: old verdicts
# answered a different question and must not be reused.
CONTRACT = "grok-schema-probe/v1"
RECORD_SCHEMA = "swarm.grok-compat/v1"

PROBE_TOKEN = "swarm-grok-schema-probe-v1"
PROBE_PROMPT = "What is 3 plus 4? Answer in one plain English sentence.\n"
PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "probe": {"type": "string", "enum": [PROBE_TOKEN]},
        "sum": {"type": "integer"},
    },
    "required": ["probe", "sum"],
    "additionalProperties": False,
}

# Any id the CLI could plausibly take as `-m` — explicit pins may be variants.
MODEL_RE = re.compile(r"^grok-[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
MAX_ID_LEN = 64
MAX_RECORD_BYTES = 4096
MAX_STDOUT_BYTES = 262144

# How long a verdict stands. A pass is re-measured occasionally because the
# weights behind an id can change server-side; a definite fail is retried daily
# (providers fix this); an inconclusive probe is held just long enough that the
# sibling cluster processes of ONE review do not each re-pay for it.
TTL = {"ok": 14 * 86400, "failed": 86400, "unknown": 600}
CLOCK_SKEW = 120

EXIT = {"ok": 0, "failed": 1, "unknown": 3}


class UnsafeCache(Exception):
    pass


def cache_dir():
    override = os.environ.get("SWARM_GROK_COMPAT_DIR")
    if override:
        return override
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "gering-swarm", "grok-compat")


def ensure_private_dir(path):
    """Create the store 0700, or REFUSE a pre-existing one that is not private.

    Refuse, never chmod: a directory someone else can write is already a place
    where a verdict may have been planted, and tightening the mode afterwards
    would launder whatever is in it.
    """
    try:
        os.makedirs(path, mode=0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafeCache(f"{path} is not a directory (or is a symlink)")
    if st.st_uid != os.getuid():
        raise UnsafeCache(f"{path} is not owned by the current user")
    if st.st_mode & 0o077:
        raise UnsafeCache(f"{path} is accessible to group/other (mode {st.st_mode & 0o777:o})")


def record_path(directory, model, cli_version):
    key = hashlib.sha256(f"{model}\0{cli_version}\0{CONTRACT}".encode()).hexdigest()[:16]
    return os.path.join(directory, f"{model}--{key}.json")


def read_record(path, model, cli_version, now):
    """Return (record, None) for a usable verdict, else (None, why-not)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None, "not cached"
        return None, f"cache record unreadable ({errno.errorcode.get(exc.errno, exc.errno)})"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "cache record is not a regular file"
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            return None, "cache record is not private to the current user"
        if st.st_size > MAX_RECORD_BYTES:
            return None, "cache record is oversized"
        raw = os.read(fd, MAX_RECORD_BYTES + 1)
    finally:
        os.close(fd)
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "cache record is not valid JSON"
    if not isinstance(rec, dict):
        return None, "cache record is not an object"
    want = {"schema": RECORD_SCHEMA, "contract": CONTRACT, "model": model, "cli_version": cli_version}
    for k, v in want.items():
        if rec.get(k) != v:
            return None, f"cache record does not match this {k}"
    if rec.get("compat") not in TTL:
        return None, "cache record has an unknown verdict"
    checked = rec.get("checked_at")
    if isinstance(checked, bool) or not isinstance(checked, int):
        return None, "cache record has no valid timestamp"
    if checked > now + CLOCK_SKEW:
        return None, "cache record is dated in the future"
    if now - checked > TTL[rec["compat"]]:
        return None, "cache record expired"
    for k in ("reason", "actual_model"):
        if not isinstance(rec.get(k, ""), str):
            return None, f"cache record has a malformed {k}"
    return rec, None


def write_record(path, rec):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)  # 0600, O_EXCL
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def one_line(text, limit=200):
    return re.sub(r"[^\x20-\x7e]+", " ", str(text)).strip()[:limit]


def run_bounded(argv, timeout, cwd=None):
    """Run argv in its own process group; kill the GROUP at the deadline.

    Returns (rc, stdout_bytes); rc None = the bound fired. stderr is discarded:
    it is untrusted CLI text and nothing here may echo it.
    """
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return exc, b""
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out[:MAX_STDOUT_BYTES]
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        return None, b""


def detect_cli_version(grok_bin):
    rc, out = run_bounded([grok_bin, "--version"], 10)
    if rc == 0:
        m = re.search(r"\b([0-9]+(?:\.[0-9]+){1,3})\b", out.decode("utf-8", "replace"))
        if m:
            return m.group(1)
    return "unknown"


def judge(rc, out):
    """(compat, reason, actual_model) from one finished probe call."""
    if isinstance(rc, OSError):
        return "unknown", f"grok could not be launched ({errno.errorcode.get(rc.errno, rc.errno)})", ""
    if rc is None:
        return "unknown", "probe timed out", ""
    if rc != 0:
        return "unknown", f"grok exited {rc} (auth, network, denied, or the model id was rejected)", ""
    try:
        doc = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "unknown", "grok returned no JSON envelope", ""
    if not isinstance(doc, dict):
        return "unknown", "grok returned a non-object envelope", ""
    if doc.get("type") == "error":
        return "unknown", "grok reported an error: " + one_line(doc.get("message", "unknown"), 120), ""
    usage = doc.get("modelUsage")
    actual = ",".join(sorted(one_line(k, MAX_ID_LEN) for k in usage)) if isinstance(usage, dict) else ""
    # From here the call COMPLETED, so a bad shape is a definite answer about the
    # model, not about the environment. A successful exit alone proves nothing.
    so = doc.get("structuredOutput")
    if so is None:
        return "failed", "call succeeded but structuredOutput is null — the schema was not enforced", actual
    if not isinstance(so, dict) or set(so) != {"probe", "sum"}:
        return "failed", "structuredOutput does not have the schema's exact keys", actual
    if so["probe"] != PROBE_TOKEN:
        return "failed", "structuredOutput ignored the schema's enum constraint", actual
    if isinstance(so["sum"], bool) or not isinstance(so["sum"], int):
        return "failed", "structuredOutput ignored the schema's integer constraint", actual
    return "ok", "enforced structured output observed", actual


def probe(model, grok_bin, timeout):
    work = tempfile.mkdtemp(prefix="swarm-grok-probe-")
    try:
        prompt = os.path.join(work, "prompt.txt")
        with open(prompt, "w", encoding="utf-8") as fh:
            fh.write(PROBE_PROMPT)
        argv = [grok_bin, "-m", model, "--effort", "low", "--tools", "", "--disable-web-search",
                "--max-turns", "1", "--cwd", work,
                "--json-schema", json.dumps(PROBE_SCHEMA, separators=(",", ":")),
                "--prompt-file", prompt]
        return judge(*run_bounded(argv, timeout, cwd=work))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def lock(directory, path, wait):
    """Exclusive per-record lock, bounded. Returns the fd, or None on timeout."""
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.2)


def emit(rec, source, extra_reason=None):
    fields = dict(rec)
    fields["source"] = source
    if extra_reason:
        fields["cache_note"] = extra_reason
    for k in ("compat", "source", "reason", "model", "cli_version", "contract",
              "actual_model", "checked_at", "cache_note"):
        if fields.get(k) not in (None, ""):
            print(f"{k}={one_line(fields[k])}")
    return EXIT[fields["compat"]]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="grok-compat.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("check", "ensure"))
    ap.add_argument("--model", required=True)
    ap.add_argument("--cli-version", default="")
    ap.add_argument("--grok-bin", default=os.environ.get("SWARM_GROK_BIN", "grok"))
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args(argv)

    if len(args.model) > MAX_ID_LEN or not MODEL_RE.fullmatch(args.model):
        print(f"grok-compat: refusing malformed model id {one_line(args.model, 80)!r}", file=sys.stderr)
        return 2
    if not 5 <= args.timeout <= 300:
        print("grok-compat: --timeout must be 5..300 seconds", file=sys.stderr)
        return 2
    cli_version = args.cli_version or detect_cli_version(args.grok_bin)
    if cli_version != "unknown" and not VERSION_RE.fullmatch(cli_version):
        print(f"grok-compat: refusing malformed CLI version {one_line(cli_version, 40)!r}", file=sys.stderr)
        return 2

    directory = cache_dir()
    try:
        ensure_private_dir(directory)
    except (UnsafeCache, OSError) as exc:
        print(f"grok-compat: unsafe cache directory — {exc}", file=sys.stderr)
        return 2
    path = record_path(directory, args.model, cli_version)
    base = {"schema": RECORD_SCHEMA, "contract": CONTRACT, "model": args.model,
            "cli_version": cli_version}

    rec, why = read_record(path, args.model, cli_version, int(time.time()))
    if rec:
        return emit(rec, "cache")
    if args.mode == "check":
        return emit(dict(base, compat="unknown", reason=why), "none")

    fd = lock(directory, path, args.timeout + 15)
    if fd is None:
        return emit(dict(base, compat="unknown",
                         reason="another probe for this model did not finish in time"), "none")
    try:
        # Someone else may have probed while we waited — that is the point.
        rec, _ = read_record(path, args.model, cli_version, int(time.time()))
        if rec:
            return emit(rec, "cache")
        compat, reason, actual = probe(args.model, args.grok_bin, args.timeout)
        rec = dict(base, compat=compat, reason=reason, actual_model=actual,
                   checked_at=int(time.time()))
        try:
            write_record(path, rec)
            note = None if why == "not cached" else f"replaced: {why}"
        except OSError as exc:
            note = f"verdict could not be cached ({errno.errorcode.get(exc.errno, exc.errno)})"
        return emit(rec, "probe", note)
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
