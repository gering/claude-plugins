#!/usr/bin/env python3
"""Drive Kimi Code through ACP and return schema-validated findings JSON.

Kimi's `-p` mode only accepts the prompt as an argv value. Swarm's transport
contract forbids that because Linux limits one argv item to 128 KiB while real
review prompts exceed it. ACP v1 carries the complete prompt as NDJSON over
stdio instead, preserving the adapter's out-of-band transport.

The ACP session stays in manual-approval mode and this client rejects every
approval request. Read/search/fetch/think tools remain available; any OTHER
tool kind (an allowlist, not a denylist) that the agent runs — in progress,
completed, or failed without having been rejected here — kills the session on
first sight and fails the review. That is defense-in-depth only: the outer
agents.sh jail is the hard secret-read and REPOSITORY-write boundary (the host
HOME stays writable and the network open — documented residuals, not
boundaries).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_BACKEND = 10
EXIT_RESPONSE = 11
EXIT_PROTOCOL = 12
EXIT_POLICY_RESPONSE = 13
ACP_VERSION = 1
# Upper bound on the assistant text this client will buffer: the findings
# schema caps a valid answer well under this; a peer streaming past it can only
# be malfunctioning or hostile, and the outer timeout is the wrong tool for it.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# ALLOWLIST of tool kinds a review may run. Everything else — edit, delete,
# move, execute, switch_mode, other, an unknown or missing kind — is unsafe.
# An allowlist because the denylist version was fail-open: a kind this file
# had never heard of (or a kind rewritten to "read" on a later update) walked
# straight through.
SAFE_TOOL_KINDS = frozenset({"read", "search", "fetch", "think"})

# --- read-only shell policy --------------------------------------------------
# A reviewer needs `git log/show/blame` and grep pipelines, and Kimi has no
# native git tool — those come only through its Shell tool (ACP kind
# `execute`). So `execute` is neither on the safe list nor flatly unsafe: its
# COMMAND is checked against this policy. It is a positive allowlist of
# read-only programs (git restricted to read subcommands, no `-c` config
# injection, no `--output`), pipes between them are fine, and the whole string
# must be free of chaining, redirection, substitution and escapes. Anything
# else — an unknown program, `find -exec`, `rg --pre`, `tail -f`, a `>` even
# inside quotes — is rejected; a rejected command that Kimi nevertheless ran
# aborts the session, exactly like any other unsafe kind. Detection, not
# prevention, for auto-approved commands (Kimi runs what it deems safe without
# asking); the OS jail (repo/Git immutable, secrets denied) is the boundary.
# When Kimi DOES ask (`session/request_permission`), an allowlisted command
# is approved once — everything else stays rejected.
SHELL_META = re.compile(r"[;&<>`$\\\r\n]")
MAX_COMMAND_CHARS = 2000
READ_ONLY_PROGRAMS = frozenset({
    "git", "grep", "egrep", "fgrep", "rg", "find", "ls", "cat", "head", "tail",
    "wc", "sort", "uniq", "cut", "tr", "diff", "cmp", "comm", "paste", "stat",
    "file", "tree", "pwd", "echo", "printf", "basename", "dirname", "realpath",
    "readlink", "which", "du", "nl", "tac", "strings", "column", "jq", "fold",
    "md5sum", "sha256sum", "shasum", "true", "date",
})
GIT_READ_SUBCOMMANDS = frozenset({
    "log", "show", "blame", "diff", "status", "ls-files", "ls-tree", "grep",
    "rev-parse", "rev-list", "describe", "shortlog", "cat-file", "name-rev",
    "merge-base", "reflog", "show-ref", "for-each-ref", "count-objects",
    "diff-tree", "whatchanged", "check-ignore", "check-attr", "log-tree",
})
# Subcommands that read only with a listing flag and WRITE otherwise
# (`git branch foo` creates one, `git tag v1` too, `git stash` pushes).
GIT_LISTING_ONLY = {
    "branch": {"--list", "-l", "-a", "-r", "--all", "--remotes", "-v", "-vv",
               "--verbose", "--show-current", "--contains", "--merged",
               "--no-merged", "--points-at", "--sort", "--format"},
    "tag": {"--list", "-l", "-n", "--contains", "--merged", "--points-at",
            "--sort", "--format"},
    "remote": {"-v", "--verbose", "show", "get-url"},
    "stash": {"list", "show"},
    "worktree": {"list"},
    "config": {"--get", "--get-all", "--get-regexp", "--list", "-l"},
}
GIT_REJECTED_OPTIONS = ("-c", "--config-env", "--exec-path", "--output", "-o")
PROGRAM_REJECTED_OPTIONS = {
    "find": ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint",
             "-fprint0", "-fprintf", "-fls"),
    "rg": ("--pre",),
    "tail": ("-f", "-F", "--follow"),
    "sort": ("-o", "--output"),
}


def _git_segment_ok(args: list[str]) -> tuple[bool, str]:
    sub = None
    idx = 0
    while idx < len(args):
        tok = args[idx]
        if any(tok == opt or tok.startswith(opt + "=") for opt in GIT_REJECTED_OPTIONS):
            return False, f"git option {tok!r} is not read-only"
        if tok == "-C" or tok == "--git-dir" or tok == "--work-tree":
            idx += 2
            continue
        if tok.startswith("-"):
            idx += 1
            continue
        sub = tok
        break
    if sub is None:
        return False, "git without a subcommand"
    rest = args[idx + 1:]
    if any(tok == opt or tok.startswith(opt + "=") for tok in rest for opt in GIT_REJECTED_OPTIONS):
        return False, f"git {sub} carries a non-read-only option"
    if sub in GIT_READ_SUBCOMMANDS:
        return True, ""
    listing = GIT_LISTING_ONLY.get(sub)
    if listing is not None:
        if any(tok in listing or tok.split("=", 1)[0] in listing for tok in rest):
            return True, ""
        return False, f"git {sub} without a listing flag can write"
    return False, f"git subcommand {sub!r} is not read-only"


def _read_only_command(command: Any) -> tuple[bool, str]:
    """(allowed, reason). Reason is empty when allowed and never echoes more
    than the offending token when not — it reaches stderr and the report."""
    if not isinstance(command, str) or not command.strip():
        return False, "no command string"
    if len(command) > MAX_COMMAND_CHARS:
        return False, "command too long"
    if SHELL_META.search(command):
        return False, "shell chaining/redirection/substitution is not allowed"
    if "||" in command:
        return False, "shell chaining is not allowed"
    for segment in command.split("|"):
        try:
            args = shlex.split(segment, posix=True)
        except ValueError:
            return False, "unparseable shell segment"
        if not args:
            return False, "empty pipeline segment"
        prog = args[0]
        if "/" in prog or "=" in prog:
            return False, f"program {prog!r} must be a bare PATH name"
        if prog not in READ_ONLY_PROGRAMS:
            return False, f"program {prog!r} is not on the read-only allowlist"
        if prog == "git":
            ok, why = _git_segment_ok(args[1:])
            if not ok:
                return False, why
        for opt in PROGRAM_REJECTED_OPTIONS.get(prog, ()):
            if any(tok == opt or tok.startswith(opt + "=") for tok in args[1:]):
                return False, f"{prog} option {opt!r} is not read-only"
    return True, ""


def _command_of(raw_input: Any) -> Any:
    # Kimi's Shell tool sends {"command": "..."}; accept the common spellings
    # and an argv list, and hand anything else to the policy as "no command".
    if isinstance(raw_input, dict):
        for key in ("command", "cmd", "commandLine", "command_line"):
            value = raw_input.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                return shlex.join(value)
    return None
# Statuses under which an unsafe-kind tool has NOT run: still awaiting the
# approval this client will reject, or rejected. Anything else means it ran.
UNSTARTED_STATUSES = frozenset({"pending", None})
REJECTED_STATUSES = frozenset({"failed", "cancelled", "pending", None})


class BackendError(RuntimeError):
    """The Kimi process failed or closed its ACP stream."""


class ResponseError(RuntimeError):
    """Kimi completed but its answer did not satisfy the findings contract."""


class ProtocolError(RuntimeError):
    """The ACP peer violated the transport or read-only policy contract."""


class SchemaError(ValueError):
    """The configured JSON schema uses an unsupported or invalid construct."""


def _safe_error(message: str) -> None:
    sys.stderr.write(f"kimi ACP: {message}\n")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


_SCHEMA_KEYS = {
    "$schema",
    "$comment",
    "title",
    "description",
    "type",
    "additionalProperties",
    "required",
    "properties",
    "items",
    "maxItems",
    "maxLength",
    "minimum",
    "enum",
}
_ANNOTATION_KEYS = {"$schema", "$comment", "title", "description"}


def _validate_schema_node(schema: Any, path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: schema node must be an object")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise SchemaError(f"{path}: unsupported schema keyword(s): {names}")

    expected = schema.get("type")
    if expected is not None and expected not in {"object", "array", "string", "integer"}:
        raise SchemaError(f"{path}: unsupported type {expected!r}")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise SchemaError(f"{path}: enum must be an array")

    if expected == "object":
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
            raise SchemaError(f"{path}: required must be an array of strings")
        if not isinstance(properties, dict):
            raise SchemaError(f"{path}: properties must be an object")
        if not isinstance(additional, bool):
            raise SchemaError(f"{path}: only boolean additionalProperties is supported")
        for name, child in properties.items():
            _validate_schema_node(child, f"{path}.{name}")
    elif expected == "array":
        if "maxItems" in schema and (
            isinstance(schema["maxItems"], bool)
            or not isinstance(schema["maxItems"], int)
            or schema["maxItems"] < 0
        ):
            raise SchemaError(f"{path}: maxItems must be a non-negative integer")
        if "items" not in schema:
            raise SchemaError(f"{path}: array schema requires items")
        _validate_schema_node(schema["items"], f"{path}[]")
    elif expected == "string":
        if "maxLength" in schema and (
            isinstance(schema["maxLength"], bool)
            or not isinstance(schema["maxLength"], int)
            or schema["maxLength"] < 0
        ):
            raise SchemaError(f"{path}: maxLength must be a non-negative integer")
    elif expected == "integer":
        if "minimum" in schema and (
            isinstance(schema["minimum"], bool) or not isinstance(schema["minimum"], int)
        ):
            raise SchemaError(f"{path}: minimum must be an integer")

    # Annotation-only schemas would accept anything, which is not a useful output
    # contract for this adapter. Require at least a type or enum at every leaf.
    if set(schema) <= _ANNOTATION_KEYS:
        raise SchemaError(f"{path}: schema node has no validation keyword")


def _validate_instance(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    if expected == "object" and not isinstance(value, dict):
        raise ResponseError(f"{path}: expected object, got {_json_type(value)}")
    if expected == "array" and not isinstance(value, list):
        raise ResponseError(f"{path}: expected array, got {_json_type(value)}")
    if expected == "string" and not isinstance(value, str):
        raise ResponseError(f"{path}: expected string, got {_json_type(value)}")
    if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        raise ResponseError(f"{path}: expected integer, got {_json_type(value)}")

    if "enum" in schema and value not in schema["enum"]:
        raise ResponseError(f"{path}: value is outside the allowed enum")

    if expected == "object":
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ResponseError(f"{path}: missing required field(s): {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties", True) is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ResponseError(f"{path}: unexpected field(s): {', '.join(extra)}")
        for name, child in properties.items():
            if name in value:
                _validate_instance(value[name], child, f"{path}.{name}")
    elif expected == "array":
        maximum = schema.get("maxItems")
        if maximum is not None and len(value) > maximum:
            raise ResponseError(f"{path}: too many items ({len(value)} > {maximum})")
        for index, item in enumerate(value):
            _validate_instance(item, schema["items"], f"{path}[{index}]")
    elif expected == "string":
        maximum = schema.get("maxLength")
        if maximum is not None and len(value) > maximum:
            raise ResponseError(f"{path}: string exceeds maxLength {maximum}")
    elif expected == "integer":
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise ResponseError(f"{path}: integer is below minimum {minimum}")


class AcpClient:
    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.process: subprocess.Popen[str] | None = None
        self.next_id = 1
        self.collect_output = False
        self.output_chunks: list[str] = []
        self.unexpected_client_methods: list[str] = []
        self.protocol_violations: list[str] = []
        # kind per tool id; an unsafe kind is STICKY (a later update cannot
        # downgrade it to a safe one), a missing kind stays missing (= unsafe).
        self.tool_kinds: dict[str, str | None] = {}
        # Shell commands by tool id (first sight wins) and the ids whose
        # command passed the read-only policy — approved on request, or
        # observed running and found allowlisted.
        self.tool_inputs: dict[str, Any] = {}
        self.allowed_exec_ids: set[str] = set()
        # kimi-code 0.41 streams a tool's ARGUMENT JSON as text content while
        # the model is still composing it (`tool_call` pending with "" and
        # `tool_call_update` in_progress with "{", "\"command\"", …), and
        # sends no rawInput. The pieces are accumulated here until they parse.
        self.tool_arg_text: dict[str, str] = {}
        # Ids whose frame carried rawInput — kimi-code's "Running: …" frame,
        # i.e. the command is (about to be) executed, no longer composed.
        self.exec_started_ids: set[str] = set()
        # tool ids whose approval request this client rejected: their failed /
        # cancelled updates are the expected outcome, not evidence of a run.
        self.rejected_tool_ids: set[str] = set()
        # Realpath prefixes no tool may touch, of any kind: the ephemeral HOME
        # (linked host credentials, projected config) and the host store. A
        # `read` there is the own-token exfiltration the egress guard can only
        # ask the model not to do — abort on first sight instead.
        self.deny_paths: list[str] = []
        self.cwd: str = os.getcwd()       # the ACP session cwd; relative tool paths resolve here
        self.output_bytes = 0

    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                [self.executable, "acp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            raise BackendError(f"could not start Kimi: {exc}") from exc

    def close(self, force: bool = False) -> None:
        # force=True: SIGKILL the whole kimi process group at once. The adapter's
        # timeout wrapper SIGTERMs this helper and SIGKILLs it TIMEOUT_KILL_GRACE
        # (3 s) later — the same 3 s a graceful TERM→wait→KILL here would spend,
        # so a kimi that sits on SIGTERM left the helper dead and the session
        # alive (kimi runs in its own session precisely so killpg can reach it
        # without hitting this helper). The signal handler and the unsafe-tool
        # abort take this path; a normal end of turn keeps the graceful one.
        process = self.process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            graceful = False
            if not force:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                    graceful = True
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
            if not graceful:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        self.process = None

    def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise BackendError("ACP process is not running")
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise BackendError("Kimi closed the ACP input stream") from exc

    def _read(self) -> dict[str, Any]:
        process = self.process
        if process is None or process.stdout is None:
            raise BackendError("ACP process is not running")
        try:
            line = process.stdout.readline()
        except UnicodeDecodeError as exc:
            raise ProtocolError("ACP frame is not valid UTF-8") from exc
        if not line:
            rc = process.poll()
            raise BackendError(f"Kimi closed the ACP output stream (rc={rc})")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid NDJSON frame ({len(line.encode('utf-8'))} bytes)") from exc
        if not isinstance(message, dict):
            raise ProtocolError("ACP frame is not a JSON object")
        return message

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._read()
            if "method" in message:
                if "id" in message:
                    self._handle_server_request(message)
                else:
                    self._handle_notification(message)
                continue
            if message.get("id") != request_id:
                raise ProtocolError(f"unexpected response id {message.get('id')!r}")
            if "error" in message:
                error = message.get("error")
                code = error.get("code") if isinstance(error, dict) else "?"
                # The peer's message names the cause (quota exhausted, auth,
                # model) — pass it up, bounded; the adapter scrubs stderr.
                msg = error.get("message") if isinstance(error, dict) else None
                detail = f": {str(msg)[:300]}" if isinstance(msg, str) and msg else ""
                raise BackendError(f"{method} failed with JSON-RPC error {code}{detail}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ProtocolError(f"{method} returned a non-object result")
            return result

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "session/request_permission":
            self._trace(message)
            params = message.get("params")
            options = params.get("options", []) if isinstance(params, dict) else []
            tool_call = params.get("toolCall") if isinstance(params, dict) else None
            tool_id = tool_call.get("toolCallId") if isinstance(tool_call, dict) else None
            if isinstance(tool_call, dict) and isinstance(tool_id, str) and tool_id:
                # Approve ONCE a shell command the read-only policy accepts —
                # `git log` Kimi chose to ask about must not be lost to a blanket
                # rejection. Every other kind, and any other command, is rejected.
                kind = tool_call.get("kind", self.tool_kinds.get(tool_id))
                self._absorb_tool_args(tool_id, tool_call)
                command = self._command_for(tool_id)
                if kind == "execute" and not self._denied_tokens(command):
                    ok, _why = _read_only_command(command)
                    allow = next(
                        (
                            option
                            for option in options
                            if isinstance(option, dict)
                            and option.get("kind") == "allow_once"
                            and isinstance(option.get("optionId"), str)
                        ),
                        None,
                    ) if ok else None
                    if allow is not None:
                        self.tool_kinds.setdefault(tool_id, "execute")
                        self.allowed_exec_ids.add(tool_id)
                        self._send({"jsonrpc": "2.0", "id": request_id, "result": {
                            "outcome": {"outcome": "selected", "optionId": allow["optionId"]}}})
                        return
            if isinstance(tool_id, str) and tool_id:
                self.rejected_tool_ids.add(tool_id)
            reject = next(
                (
                    option
                    for option in options
                    if isinstance(option, dict)
                    and option.get("kind") in {"reject_once", "reject_always"}
                    and isinstance(option.get("optionId"), str)
                ),
                None,
            )
            if reject is None:
                result = {"outcome": {"outcome": "cancelled"}}
            else:
                result = {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": reject["optionId"],
                    }
                }
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
            return

        # The client advertises no fs or terminal capabilities. Any such request
        # is a protocol/policy violation; answer it so the peer cannot hang, then
        # reject the completed review even if the model later emits valid JSON.
        self.unexpected_client_methods.append(str(method))
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "client method not supported"},
            }
        )

    def _handle_notification(self, message: dict[str, Any]) -> None:
        # Diagnostics only: KIMI_ACP_TRACE=<file> appends every tool frame
        # verbatim. Off by default — the frames carry model-chosen commands.
        self._trace(message)
        if message.get("method") != "session/update":
            return
        params = message.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        if not isinstance(update, dict):
            return
        update_type = update.get("sessionUpdate")
        if self.collect_output and update_type == "agent_message_chunk":
            content = update.get("content")
            if isinstance(content, dict) and content.get("type") == "text":
                text = content.get("text")
                if isinstance(text, str):
                    self.output_bytes += len(text.encode("utf-8", "replace"))
                    if self.output_bytes > MAX_RESPONSE_BYTES:
                        self.close(force=True)
                        raise ProtocolError(
                            f"assistant text exceeded {MAX_RESPONSE_BYTES} bytes; session aborted"
                        )
                    self.output_chunks.append(text)

        if update_type in {"tool_call", "tool_call_update"}:
            tool_id = update.get("toolCallId")
            kind = update.get("kind")
            status = update.get("status")
            if not isinstance(tool_id, str) or not tool_id:
                self.protocol_violations.append("tool update has no string toolCallId")
                return
            if tool_id not in self.tool_kinds:
                # First sight wins — a MISSING kind stays missing (= unsafe); a
                # later update cannot reclassify it.
                self.tool_kinds[tool_id] = kind if isinstance(kind, str) else None
            else:
                known = self.tool_kinds[tool_id]
                if known in SAFE_TOOL_KINDS and isinstance(kind, str) and kind not in SAFE_TOOL_KINDS:
                    self.tool_kinds[tool_id] = kind  # may only escalate to unsafe
            effective_kind = self.tool_kinds[tool_id]
            self._check_locations(update, tool_id)
            if effective_kind is None and status not in UNSTARTED_STATUSES:
                # Keep the orphan wording: an update for a tool this client never
                # saw announced is malformed ACP, not merely an unsafe kind.
                self.protocol_violations.append(
                    f"{status} tool update {tool_id!r} has no known kind"
                )
                return
            if effective_kind in SAFE_TOOL_KINDS or effective_kind is None:
                return
            if effective_kind == "execute":
                self._absorb_tool_args(tool_id, update)
                if tool_id in self.allowed_exec_ids:
                    return
                if tool_id in self.rejected_tool_ids and status in REJECTED_STATUSES:
                    return
                command = self._command_for(tool_id)
                if command is not None:
                    # Known command: vet it as soon as it is readable. With
                    # streamed arguments that is usually while the model is
                    # still composing the call — before anything ran.
                    denied = self._denied_tokens(command)
                    ok, why = (False, "") if denied else _read_only_command(command)
                    if ok:
                        self.allowed_exec_ids.add(tool_id)
                        return
                    composing = status in UNSTARTED_STATUSES or (
                        status == "in_progress" and tool_id not in self.exec_started_ids
                    )
                    if composing:
                        # Proposed (still streaming, or pending), not run: in
                        # `default` mode Kimi asks first and the permission
                        # handler rejects it — the model then sees a failed
                        # tool, not a dead session. The rawInput frame marks
                        # execution; a disallowed command reaching it aborts.
                        return
                    self.close(force=True)
                    if denied:
                        raise ProtocolError(
                            f"tool {tool_id!r} touched a denied path under the runtime/auth store"
                        )
                    raise ProtocolError(
                        f"shell command outside the read-only allowlist ({why}); status={status}"
                    )
                if status in UNSTARTED_STATUSES or status == "in_progress":
                    # Still composing (or not started): nothing to vet yet.
                    # The end-of-turn sweep catches an execute whose command
                    # never became visible.
                    return
                # Terminal status with no command this client could read —
                # fall through to the generic abort: it ran, unvetted.
            # Unsafe kind. The ONLY acceptable histories: still pending (the
            # approval request has not arrived yet — this client will reject
            # it), or rejected by this client and then failed/cancelled. An
            # in-progress / completed status, or a terminal status without a
            # rejection on record, means the agent RAN it (an auto-approved
            # in-repo write, a shell whose command exited non-zero). Abort NOW —
            # kill the session rather than let it keep running while the model
            # finishes composing a clean-looking answer.
            if status in UNSTARTED_STATUSES:
                return
            if tool_id in self.rejected_tool_ids and status in REJECTED_STATUSES:
                return
            self.close(force=True)
            raw = self.tool_inputs.get(tool_id, update.get("rawInput"))
            shape = sorted(raw.keys())[:8] if isinstance(raw, dict) else type(raw).__name__
            title = update.get("title")
            title = title[:120] if isinstance(title, str) else ""
            raise ProtocolError(
                f"unsafe tool ran despite approval guard: kind={effective_kind} status={status}"
                f" rawInput={shape} title={title!r}"
            )  # "ran", not "completed": a failed shell command ran too

    def _denied(self, candidate: str) -> bool:
        # A path (realpath'd, ~ expanded) under a deny prefix — or any string
        # that merely CONTAINS one (a file:// URL, a shell command, an argument
        # list): the auth store must not appear in a tool call at all.
        if not candidate:
            return False
        for prefix in self.deny_paths:
            if prefix in candidate:
                return True
        expanded = os.path.expanduser(candidate)
        # A RELATIVE path resolves against the session cwd — `../.kimi-code/…`
        # from the repo root, or a repo symlink pointing into the store, reach
        # the same file the absolute form would.
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.cwd, expanded)
        real = os.path.realpath(expanded)
        for p in self.deny_paths:
            try:
                common = os.path.commonpath([real, p])
            except ValueError:
                continue
            if common == p:
                return True
            # An ANCESTOR of the store is just as bad as the store: a search
            # or grep rooted there walks into the linked credentials. `/`,
            # the user's HOME and the scratch parent all land here.
            if common == real:
                return True
        return False

    @staticmethod
    def _trace(message: dict[str, Any]) -> None:
        # Diagnostics only: KIMI_ACP_TRACE=<file> appends every tool frame and
        # permission request verbatim. Off by default — the frames carry
        # model-chosen commands.
        trace = os.environ.get("KIMI_ACP_TRACE")
        if not trace:
            return
        try:
            upd = (message.get("params") or {}).get("update") or {}
            is_tool = isinstance(upd, dict) and upd.get("sessionUpdate") in {"tool_call", "tool_call_update"}
            if is_tool or message.get("method") == "session/request_permission":
                with open(trace, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(message, ensure_ascii=True) + "\n")
        except (OSError, TypeError, ValueError):
            pass

    def _absorb_tool_args(self, tool_id: str, update: dict[str, Any]) -> None:
        raw = update.get("rawInput")
        if raw is not None:
            self.tool_inputs.setdefault(tool_id, raw)
            if update.get("sessionUpdate") in {"tool_call", "tool_call_update"}:
                self.exec_started_ids.add(tool_id)
        if tool_id in self.tool_inputs:
            return
        content = update.get("content")
        if not isinstance(content, list):
            return
        buf = self.tool_arg_text.get(tool_id, "")
        for block in content:
            inner = block.get("content") if isinstance(block, dict) else None
            if isinstance(inner, dict) and inner.get("type") == "text":
                text = inner.get("text")
                if isinstance(text, str):
                    # kimi-code 0.41 sends cumulative SNAPSHOTS of the argument
                    # JSON ("{", '{"command": "', '{"command": "git', …), so
                    # a chunk that opens the object replaces the buffer; a
                    # delta-streaming agent's pieces are appended instead.
                    buf = text if text.startswith("{") else buf + text
        # Bound the buffer: arguments are a few hundred bytes; anything past
        # this is tool OUTPUT streamed on the same channel, which never parses
        # as the argument object and only wastes memory.
        self.tool_arg_text[tool_id] = buf[:65536]

    def _command_for(self, tool_id: str) -> Any:
        if tool_id in self.tool_inputs:
            return _command_of(self.tool_inputs[tool_id])
        buf = self.tool_arg_text.get(tool_id, "")
        if not buf.startswith("{"):
            return None
        try:
            parsed = json.loads(buf)
        except ValueError:
            return None   # still streaming
        if isinstance(parsed, dict):
            self.tool_inputs[tool_id] = parsed
        return _command_of(parsed)

    def _denied_tokens(self, command: Any) -> bool:
        # A shell command is one string to `_denied`, so realpath would see
        # `grep -r x /private/tmp` as one bogus path; check its tokens too.
        if not isinstance(command, str):
            return False
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return True   # unparseable: treat as touching the store (fail closed)
        return any(self._denied(tok) for tok in tokens)

    def _check_locations(self, update: dict[str, Any], tool_id: str) -> None:
        # Any tool kind, any status: the announcement alone means the agent is
        # about to touch the path (or already did). `locations` is optional
        # UI follow-along data in ACP — so the raw tool input is scanned too,
        # every string in it, recursively; a check that trusts the peer to
        # volunteer `locations` is fail-open.
        if not self.deny_paths:
            return
        candidates: list[str] = []
        locations = update.get("locations")
        if isinstance(locations, list):
            for loc in locations:
                path = loc.get("path") if isinstance(loc, dict) else None
                if isinstance(path, str):
                    candidates.append(path)
        stack: list[Any] = [update.get("rawInput"), update.get("title")]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        for candidate in candidates:
            if self._denied(candidate) or (" " in candidate and self._denied_tokens(candidate)):
                self.close(force=True)
                raise ProtocolError(
                    f"tool {tool_id!r} touched a denied path under the runtime/auth store"
                )

    def unsettled_unsafe_tools(self) -> list[str]:
        # End-of-turn sweep: an unsafe-kind tool that was announced but never
        # reached a status this client can vouch for (no rejection on record).
        return sorted(
            f"{kind or 'unknown'}:{tool_id}"
            for tool_id, kind in self.tool_kinds.items()
            if (kind is None or kind not in SAFE_TOOL_KINDS)
            and tool_id not in self.rejected_tool_ids
            and tool_id not in self.allowed_exec_ids
        )


def _select_option(config_options: Any, config_id: str) -> dict[str, Any]:
    if not isinstance(config_options, list):
        raise ProtocolError("session did not advertise configuration options")
    option = next(
        (
            item
            for item in config_options
            if isinstance(item, dict) and item.get("id") == config_id
        ),
        None,
    )
    if option is None or option.get("type") != "select":
        raise ProtocolError(f"session has no selectable {config_id!r} option")
    return option


def _set_option(
    client: AcpClient,
    session_id: str,
    config_options: Any,
    config_id: str,
    value: str,
) -> list[dict[str, Any]]:
    option = _select_option(config_options, config_id)
    choices = option.get("options")
    if not isinstance(choices, list):
        raise ProtocolError(f"session option {config_id!r} has no value list")
    offered = {item.get("value") for item in choices if isinstance(item, dict)}
    if value not in offered:
        raise ProtocolError(f"session does not offer {config_id} value {value!r}")
    if option.get("currentValue") == value:
        return config_options   # already in effect — no round trip
    result = client.request(
        "session/set_config_option",
        {"sessionId": session_id, "configId": config_id, "value": value},
    )
    updated = result.get("configOptions")
    current = _select_option(updated, config_id).get("currentValue")
    if current != value:
        raise ProtocolError(
            f"session did not apply {config_id} value {value!r} (got {current!r})"
        )
    return updated


def _load_json(path: Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"could not read {label}: {exc}") from exc


def _extract_json_object(text: str) -> Any:
    """The single JSON object in the assistant text — bare, inside a ``` fence,
    or wrapped in prose. Kimi has no CLI schema-enforcement flag (codex/grok
    do), so the output contract is an instruction it may decorate; the strict
    schema validation that follows is what actually gates the answer. Content
    is never echoed on failure."""
    size = len(text.encode("utf-8"))
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    if fence:
        candidates.append(fence.group(1))
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first:last + 1])
    for cand in candidates:
        try:
            value = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ResponseError(
        f"assistant text is not a JSON object ({size} bytes; content withheld)"
    )


def _load_prompt(path: Path) -> str:
    # errors="replace": the prompt carries an untrusted diff that may embed
    # Latin-1 (or worse) bytes; a decode error here surfaced as rc 2 "adapter
    # rejected its configuration", pointing the operator at the schema instead
    # of the diff. A replacement character in a hunk is a review nuisance, not
    # a reason to lose the voice.
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SchemaError(f"could not read prompt file: {exc}") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True, choices=("low", "high", "max"))
    # The adapter resolves the executable ONCE ($KIMI_BIN, probed for readiness)
    # and hands it over explicitly — no second env lookup with its own default
    # here, so `ready` and `run` can never start two different binaries.
    parser.add_argument("--kimi-bin", required=True)
    parser.add_argument("--deny-path", action="append", default=[],
                        help="realpath prefix no tool call may touch (repeatable)")
    args = parser.parse_args(argv)
    if not args.kimi_bin.strip():
        parser.error("--kimi-bin cannot be empty")
    if not args.prompt_file.is_file():
        parser.error(f"prompt file not found: {args.prompt_file}")
    if not args.schema.is_file():
        parser.error(f"schema not found: {args.schema}")
    if not args.cwd.is_absolute() or not args.cwd.is_dir():
        parser.error(f"cwd must be an existing absolute directory: {args.cwd}")
    if not args.model.strip():
        parser.error("model cannot be empty")
    return args


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        schema = _load_json(args.schema, "schema")
        _validate_schema_node(schema)
        prompt = _load_prompt(args.prompt_file)
    except SchemaError as exc:
        _safe_error(str(exc))
        return 2

    client = AcpClient(args.kimi_bin)
    client.deny_paths = [os.path.realpath(p) for p in args.deny_path if p]
    client.cwd = os.path.realpath(str(args.cwd))
    prompt_started = False
    previous_handlers: dict[int, Any] = {}

    def stop_child(signum: int, _frame: Any) -> None:
        # The wrapper's SIGKILL follows in 3 s; a graceful close would lose
        # that race and orphan the kimi session — kill the group outright.
        client.close(force=True)
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, stop_child)

    try:
        client.start()
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": ACP_VERSION,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "swarm", "version": "1"},
            },
        )
        if initialized.get("protocolVersion") != ACP_VERSION:
            raise ProtocolError(
                f"protocol negotiation returned {initialized.get('protocolVersion')!r}"
            )

        session = client.request(
            "session/new",
            {"cwd": str(args.cwd), "mcpServers": []},
        )
        session_id = session.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ProtocolError("session/new returned no sessionId")
        options = session.get("configOptions")
        options = _set_option(client, session_id, options, "model", args.model)
        options = _set_option(client, session_id, options, "thinking", args.effort)
        # `default` = manual approvals: Kimi runs the shell commands it deems
        # safe (`git log`, grep) without asking — the read-only policy above
        # vets those after the fact — and asks for everything else, which this
        # client approves only for an allowlisted command and rejects
        # otherwise. (`plan` mode would remove the shell entirely, and with it
        # git history and grep pipelines a reviewer needs; `auto`/`yolo`
        # auto-approve writes.)
        _set_option(client, session_id, options, "mode", "default")

        client.collect_output = True
        prompt_started = True
        result = client.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": prompt}],
            },
        )
        client.collect_output = False
        if result.get("stopReason") != "end_turn":
            raise ResponseError(f"turn stopped with {result.get('stopReason')!r}")
        if client.unexpected_client_methods:
            methods = ", ".join(sorted(set(client.unexpected_client_methods)))
            raise ProtocolError(f"server requested unsupported client method(s): {methods}")
        if client.protocol_violations:
            violations = "; ".join(sorted(set(client.protocol_violations)))
            raise ProtocolError(f"malformed ACP tool update(s): {violations}")
        unsettled = client.unsettled_unsafe_tools()
        if unsettled:
            raise ProtocolError(
                "unsafe tool(s) announced and never rejected: " + ", ".join(unsettled)
            )

        response_text = "".join(client.output_chunks)
        if not response_text.strip():
            raise ResponseError("Kimi produced no assistant text")
        response = _extract_json_object(response_text)
        _validate_instance(response, schema)
        json.dump(response, sys.stdout, separators=(",", ":"), ensure_ascii=True)
        sys.stdout.write("\n")
        return 0
    except BackendError as exc:
        _safe_error(str(exc))
        return EXIT_BACKEND
    except ResponseError as exc:
        _safe_error(f"response rejected: {exc}")
        return EXIT_RESPONSE
    except ProtocolError as exc:
        _safe_error(f"protocol/policy failure: {exc}")
        return EXIT_POLICY_RESPONSE if prompt_started else EXIT_PROTOCOL
    finally:
        client.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
