#!/usr/bin/env python3
"""Drive Kimi Code through ACP and return schema-validated findings JSON.

Kimi's `-p` mode only accepts the prompt as an argv value. Swarm's transport
contract forbids that because Linux limits one argv item to 128 KiB while real
review prompts exceed it. ACP v1 carries the complete prompt as NDJSON over
stdio instead, preserving the adapter's out-of-band transport.

The ACP session stays in manual-approval mode and this client rejects every
approval request. Read/search/fetch tools remain available; write/edit/shell
calls cannot execute. The outer agents.sh sandbox still provides the hard
secret-read boundary.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_BACKEND = 10
EXIT_RESPONSE = 11
EXIT_PROTOCOL = 12
ACP_VERSION = 1


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
        self.unsafe_completed_tools: list[str] = []
        self.tool_kinds: dict[str, str] = {}

    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                [self.executable, "acp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            raise BackendError(f"could not start Kimi: {exc}") from exc

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
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
        line = process.stdout.readline()
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
                raise BackendError(f"{method} failed with JSON-RPC error {code}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ProtocolError(f"{method} returned a non-object result")
            return result

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "session/request_permission":
            params = message.get("params")
            options = params.get("options", []) if isinstance(params, dict) else []
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
                    self.output_chunks.append(text)

        if update_type in {"tool_call", "tool_call_update"}:
            tool_id = update.get("toolCallId")
            kind = update.get("kind")
            if isinstance(tool_id, str) and isinstance(kind, str):
                self.tool_kinds[tool_id] = kind
            effective_kind = self.tool_kinds.get(tool_id, kind)
            if update.get("status") == "completed" and effective_kind in {
                "edit",
                "delete",
                "move",
                "execute",
                "switch_mode",
                "other",
            }:
                self.unsafe_completed_tools.append(str(effective_kind))


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


def _load_prompt(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SchemaError(f"could not read prompt file: {exc}") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True, choices=("low", "high", "max"))
    args = parser.parse_args(argv)
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

    executable = os.environ.get("SWARM_KIMI_BIN", "kimi")
    client = AcpClient(executable)
    previous_handlers: dict[int, Any] = {}

    def stop_child(signum: int, _frame: Any) -> None:
        client.close()
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
        _set_option(client, session_id, options, "mode", "default")

        client.collect_output = True
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
        if client.unsafe_completed_tools:
            kinds = ", ".join(sorted(set(client.unsafe_completed_tools)))
            raise ProtocolError(f"unsafe tool completed despite approval guard: {kinds}")

        response_text = "".join(client.output_chunks)
        if not response_text.strip():
            raise ResponseError("Kimi produced no assistant text")
        try:
            response = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ResponseError(
                f"assistant text is invalid JSON ({len(response_text.encode('utf-8'))} bytes; content withheld)"
            ) from exc
        _validate_instance(response, schema)
        json.dump(response, sys.stdout, separators=(",", ":"), ensure_ascii=False)
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
        return EXIT_PROTOCOL
    finally:
        client.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
