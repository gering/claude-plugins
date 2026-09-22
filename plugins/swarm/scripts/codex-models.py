#!/usr/bin/env python3
"""Read Codex's picker catalog without starting a thread or a generation turn.

Usage: python3 codex-models.py [--codex PATH] [--timeout SECONDS]
Run once under the adapter's _probe_or_bare, passing its resolved probe timeout.
The single wall covers startup, initialization and ALL pages (not each page).
POSIX only: the child has its own process group, killed/reaped on every exit,
including TERM from the parent watchdog. The parent must TERM before KILL;
SIGKILL cannot be caught. Child diagnostics are discarded, not credential-dumped.

Success: one JSON object, complete=true, authoritative=false, models=[objects].
Nonzero: unavailable/malformed/incomplete/timeout; stdout stays empty. A complete
picker catalog is NOT an exhaustive list of accepted -m IDs, or proof of access.
Never reject a missing model using this API. It can return bundled/stale models
when refresh fails; custom providers/aliases and the exec config can differ.
Codex may refresh its own auth/cache even for model/list; this is non-generative,
not a promise that the CLI is mutation-free. This helper never reads credentials.

Verified against upstream rust-v0.153.4 (local CLI --help: 0.153.4):
https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/app-server/README.md
https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/app-server-protocol/src/protocol/v2/model.rs
https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/app-server/src/models.rs
https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/models-manager/src/manager.rs
"""

import argparse
import json
import math
import os
import signal
import subprocess
import sys

MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 64


class ProbeError(Exception):
    pass


def _text(value):
    return isinstance(value, str) and bool(value) and not any(
        ord(char) < 32 or ord(char) == 127 for char in value
    )


def _model(row):
    if not isinstance(row, dict):
        raise ProbeError("invalid model entry")
    for key in ("id", "model", "displayName", "defaultReasoningEffort"):
        if not _text(row.get(key)):
            raise ProbeError("invalid model metadata")
    if type(row.get("hidden")) is not bool:
        raise ProbeError("invalid model visibility")
    efforts = row.get("supportedReasoningEfforts")
    if not isinstance(efforts, list) or not efforts:
        raise ProbeError("invalid reasoning efforts")
    seen = set()
    for effort in efforts:
        if not isinstance(effort, dict) or not _text(effort.get("reasoningEffort")):
            raise ProbeError("invalid reasoning effort")
        name = effort["reasoningEffort"]
        if name in seen or not isinstance(effort.get("description"), str):
            raise ProbeError("invalid reasoning effort metadata")
        seen.add(name)
    if row["defaultReasoningEffort"] not in seen:
        raise ProbeError("default effort absent from supported efforts")
    model = {key: row[key] for key in (
        "id", "model", "displayName", "hidden", "defaultReasoningEffort",
    )}
    model["supportedReasoningEfforts"] = [
        {"reasoningEffort": item["reasoningEffort"], "description": item["description"]}
        for item in efforts
    ]
    return model


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ProbeError("non-JSON numeric constant")


class Peer:
    def __init__(self, process):
        self.process = process
        self.remaining = MAX_BYTES
        self.request_id = 0

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def request(self, method, params):
        self.request_id += 1
        self.send({"id": self.request_id, "method": method, "params": params})
        while True:
            line = self.process.stdout.readline(self.remaining + 1)
            self.remaining -= len(line)
            if self.remaining < 0:
                raise ProbeError("catalog exceeded byte limit")
            if not line or not line.endswith(b"\n"):
                raise ProbeError("incomplete JSONL response")
            message = json.loads(line, object_pairs_hook=_unique_object,
                                 parse_constant=_invalid_constant)
            if not isinstance(message, dict):
                raise ProbeError("invalid RPC envelope")
            if "id" not in message:
                # Notifications may interleave with replies; server requests are
                # never approved or executed by this catalog-only client.
                if not _text(message.get("method")) or any(
                    key in message for key in ("result", "error")
                ):
                    raise ProbeError("invalid notification")
                continue
            if type(message["id"]) is not int or message["id"] != self.request_id:
                raise ProbeError("unexpected RPC response id")
            if "method" in message or "error" in message:
                raise ProbeError("RPC request failed or unexpected server request")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ProbeError("invalid RPC result")
            return result


def catalog(process):
    peer = Peer(process)
    initialized = peer.request("initialize", {
        "clientInfo": {"name": "swarm_model_probe", "version": "1.0.0"},
    })
    if not _text(initialized.get("userAgent")):
        raise ProbeError("invalid initialize response")
    peer.send({"method": "initialized"})
    models, ids, cursors = [], set(), set()
    cursor = None
    for _ in range(MAX_PAGES):
        page = peer.request("model/list", {
            "includeHidden": True, "limit": 100, "cursor": cursor,
        })
        if not isinstance(page.get("data"), list) or "nextCursor" not in page:
            raise ProbeError("invalid catalog page")
        for row in page["data"]:
            model = _model(row)
            if model["id"] in ids:
                raise ProbeError("duplicate model id across pages")
            ids.add(model["id"])
            models.append(model)
        cursor = page["nextCursor"]
        if cursor is None:
            return {
                "models": models, "complete": True, "authoritative": False,
                "source": "codex app-server model/list",
                "reason": "Catalog may be cached/bundled; custom aliases and exec config are not exhaustive.",
            }
        if not _text(cursor) or len(cursor) > 4096 or cursor in cursors or not page["data"]:
            raise ProbeError("invalid or non-progressing catalog cursor")
        cursors.add(cursor)
    raise ProbeError("catalog exceeded page limit")


def _timeout(value):
    try:
        timeout = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number") from None
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 20:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 20 seconds")
    return timeout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="Codex executable path")
    parser.add_argument("--timeout", type=_timeout, default=10.0)
    args = parser.parse_args()
    process = None
    result = None
    code = 1

    def interrupted(signum, _frame):
        raise ProbeError("timed out" if signum == signal.SIGALRM else "interrupted")

    for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    signal.setitimer(signal.ITIMER_REAL, args.timeout)
    try:
        process = subprocess.Popen(
            [args.codex, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        result = catalog(process)
        if process.poll() not in (None, 0):
            raise ProbeError("app-server exited unsuccessfully")
        code = 0
    except (ProbeError, OSError, ValueError, RecursionError) as error:
        # Never reflect peer-supplied messages, executable paths or raw stderr.
        reason = str(error) if isinstance(error, ProbeError) else "unavailable or malformed response"
        print("codex-models: " + reason, file=sys.stderr)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, signal.SIG_IGN)
        if process is not None:
            # Kill the group even when its leader already exited: descendants
            # may still own the pipes. Do not wait for cooperative shutdown.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                print("codex-models: child did not exit after KILL", file=sys.stderr)
                code = 1
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
            process.stdout.close()
    if code == 0:
        print(json.dumps(result, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
