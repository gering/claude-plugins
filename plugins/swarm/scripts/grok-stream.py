#!/usr/bin/env python3
"""grok `--output-format streaming-json` capture, envelope extraction, metrics.

The adapter used to keep only grok's final `json` envelope, so turn count, tool
calls and stop reason had never been observed — "why is grok slow?" had no data
behind it. The stream (one ACP session update per line, verified on grok 1.0.46)
carries all of it, and its closing `end` event holds the SAME fields the json
envelope did (`structuredOutput`, `stopReason`, `num_turns`, `usage`, …), so the
review result stays byte-identical while the run becomes observable.

Subcommands (all exit 0 unless stated; this is diagnostics plus one extraction):

  stamp <stream-out> <times-out>
      Copy stdin to <stream-out> line by line and write each line's arrival
      time (seconds since start, monotonic) to <times-out>. The stream carries no
      timestamps of its own, so per-turn latency only exists if someone records
      when each line arrived. Flushes per line: a call killed at the wall keeps
      everything it produced up to the kill, which is the run a later recovery
      most needs.

  envelope <stream>
      Print the final envelope — the last `end` event, else the last `error`
      event — as one JSON object, for the adapter's unchanged validator. Exit 1
      (with the event count on stderr, never the content) when neither exists.

  metrics (--stream <file> [--times <file>] | --envelope -) [--no-latency]
      Print two lines: `<tool_calls|null> <complete:true|false>` for the
      adapter's existing scalar telemetry fields, then one compact ASCII JSON
      object for the record's `grok` field. --envelope reads the old json
      envelope on stdin (degraded path: no tools, no latency). --no-latency
      says the arrival times are not real (the adapter's watchdog buffers
      stdout and replays it at the end); reporting them would be fiction.

Every value that comes from grok's output is untrusted. Only counts, numbers and
names reduced to a fixed character set leave this script; no text, tool input
or tool output is ever printed.
"""
import json
import re
import statistics
import sys
import time

# Grok tool names the adapter grants (see GROK_TOOLS in agents.sh), plus the
# display names grok actually emits for them: web search reports
# `toolName: "Web search:"` with ACP kind `search` — the same kind grep may use,
# so the name decides first and the ACP kind is only the fallback.
READ_TOOLS = {"read_file", "list_dir", "grep"}
SHELL_TOOLS = {"run_terminal_command"}
WEB_TOOLS = {"web_search", "web_fetch"}
KIND_CLASS = {"read": "read", "search": "read", "execute": "shell", "fetch": "web"}
MAX_TOOL_NAMES = 16
SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _safe(value, limit=48):
    """Reduce an untrusted string to a fixed charset; '' for non-strings."""
    if not isinstance(value, str):
        return ""
    return SAFE.sub("_", value.strip().rstrip(":").strip())[:limit].strip("_")


def _tool_class(name, kind):
    low = name.lower().rstrip(":").strip()
    if low in READ_TOOLS:
        return "read"
    if low in SHELL_TOOLS:
        return "shell"
    if low in WEB_TOOLS or low.startswith("web"):
        return "web"
    return KIND_CLASS.get(kind, "other")


def _int(value):
    return value if type(value) is int and value >= 0 else None


def _num(value):
    if type(value) in (int, float) and value >= 0:
        return value
    return None


def stamp(stream_path, times_path):
    start = time.monotonic()
    src = sys.stdin.buffer
    with open(stream_path, "ab") as out, open(times_path, "a") as times:
        while True:
            line = src.readline()
            if not line:
                break
            out.write(line)
            out.flush()
            times.write("%.3f\n" % (time.monotonic() - start))
            times.flush()
    return 0


def _events(path):
    events, malformed = [], 0
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    events.append(None)
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    ev = None
                if not isinstance(ev, dict):
                    malformed += 1
                    ev = None
                events.append(ev)
    except OSError:
        return [], 0
    return events, malformed


def _last(events, kind):
    for ev in reversed(events):
        if ev is not None and ev.get("type") == kind:
            return ev
    return None


def envelope(stream_path):
    events, _ = _events(stream_path)
    final = _last(events, "end") or _last(events, "error")
    if final is None:
        n = sum(1 for ev in events if ev is not None)
        sys.stderr.write("grok stream has no end event (%d events; content withheld)\n" % n)
        return 1
    json.dump(final, sys.stdout)
    return 0


def _summary(final):
    """Fields shared by the stream's `end` event and the json envelope."""
    out = {"num_turns": None, "stop_reason": None, "structured_output": False,
           "findings": None, "usage": None, "cost_usd": None}
    if not isinstance(final, dict):
        return out
    if final.get("type") == "error":
        out["stop_reason"] = "error"
        return out
    out["num_turns"] = _int(final.get("num_turns"))
    out["stop_reason"] = _safe(final.get("stopReason"), 32) or None
    so = final.get("structuredOutput")
    out["structured_output"] = isinstance(so, dict)
    if isinstance(so, dict) and isinstance(so.get("findings"), list):
        out["findings"] = len(so["findings"])
    usage = final.get("usage")
    if isinstance(usage, dict):
        keys = ("input_tokens", "output_tokens", "reasoning_tokens",
                "cache_read_input_tokens", "total_tokens")
        picked = {k.replace("_input", ""): _int(usage.get(k)) for k in keys}
        if any(v is not None for v in picked.values()):
            out["usage"] = picked
    cost = _num(final.get("total_cost_usd"))
    out["cost_usd"] = round(cost, 6) if cost is not None else None
    return out


def _read_times(path):
    try:
        with open(path) as fh:
            return [float(x) for x in fh.read().split()]
    except (OSError, ValueError):
        return None


def _stream_metrics(stream_path, times_path, latency):
    events, malformed = _events(stream_path)
    final = _last(events, "end")
    err = _last(events, "error")
    m = _summary(final if final is not None else err)
    m = {"source": "stream", "complete": final is not None, **m}

    names, classes, failed, seen = {}, {"read": 0, "shell": 0, "web": 0, "other": 0}, set(), set()
    usage_idx = []
    for i, ev in enumerate(events):
        if ev is None:
            continue
        kind = ev.get("type")
        if kind == "usage":
            usage_idx.append(i)
        elif kind == "tool_call":
            tid = ev.get("toolCallId")
            key = tid if isinstance(tid, str) else ("#", i)
            if key in seen:
                continue
            seen.add(key)
            # Never the ACP `title`: it often is the command line or URL.
            name = _safe(ev.get("toolName")) or "unknown"
            if name not in names and len(names) >= MAX_TOOL_NAMES:
                name = "other"
            names[name] = names.get(name, 0) + 1
            classes[_tool_class(name, ev.get("kind"))] += 1
        elif kind == "tool_call_update" and ev.get("status") == "failed":
            tid = ev.get("toolCallId")
            if isinstance(tid, str):
                failed.add(tid)

    # One `usage` event closes each model call (2 calls → 2 usage events, and
    # `num_turns: 2` in the end event). A killed run has no end event, so the
    # usage count is the only turn count it leaves.
    if m["num_turns"] is None and usage_idx:
        m["num_turns"] = len(usage_idx)
    m["tool_calls"] = len(seen)
    m["tool_failed"] = len(failed)
    m["tools"] = names
    m["tool_kinds"] = classes

    # Per-turn wall time: from the previous turn's close (or the start of the
    # call, which includes CLI startup) to this turn's `usage` event. It covers
    # the model round AND the tools that turn ran — the number that adds up to
    # the voice's duration.
    m["turn_s"] = None
    m["turn_s_unavailable"] = None
    times = _read_times(times_path) if (latency and times_path) else None
    if not latency:
        m["turn_s_unavailable"] = "buffered"
    elif times is None or len(times) < len(events):
        m["turn_s_unavailable"] = "no-times"
    elif usage_idx:
        prev, spans = 0.0, []
        for i in usage_idx:
            spans.append(max(0.0, times[i] - prev))
            prev = times[i]
        m["turn_s"] = {"min": round(min(spans), 1),
                       "median": round(statistics.median(spans), 1),
                       "max": round(max(spans), 1)}
    m["events"] = sum(1 for ev in events if ev is not None)
    m["malformed"] = malformed
    return m


def _envelope_metrics(data):
    try:
        final = json.loads(data)
    except ValueError:
        final = None
    m = _summary(final)
    return {"source": "envelope", "complete": isinstance(final, dict) and final.get("type") != "error"
            and "stopReason" in final, **m}


def metrics(argv):
    stream = times = None
    use_envelope = False
    latency = True
    rest = list(argv)
    while rest:
        flag = rest.pop(0)
        if flag == "--stream" and rest:
            stream = rest.pop(0)
        elif flag == "--times" and rest:
            times = rest.pop(0)
        elif flag == "--envelope" and rest and rest[0] == "-":
            rest.pop(0)
            use_envelope = True
        elif flag == "--no-latency":
            latency = False
        else:
            sys.stderr.write("grok-stream.py metrics: bad argument %r\n" % flag)
            return 2
    if use_envelope == (stream is not None):
        sys.stderr.write("grok-stream.py metrics: need exactly one of --stream/--envelope -\n")
        return 2
    if use_envelope:
        m = _envelope_metrics(sys.stdin.read())
        calls = "null"
    else:
        m = _stream_metrics(stream, times, latency)
        calls = str(m["tool_calls"])
    print(calls, "true" if (m["complete"] and not use_envelope) else "false")
    print(json.dumps(m, separators=(",", ":"), ensure_ascii=True, sort_keys=True))
    return 0


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "stamp" and len(rest) == 2:
        return stamp(*rest)
    if cmd == "envelope" and len(rest) == 1:
        return envelope(rest[0])
    if cmd == "metrics":
        return metrics(rest)
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
