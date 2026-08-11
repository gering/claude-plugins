#!/usr/bin/env python3
"""Render the per-call telemetry an external review run wrote.

`agents.sh run --telemetry <file> --unit <name>` appends one JSON line per
external call. This turns those lines into the two facts an operator needs and
cannot get from `backendErrors`:

  1. Which backend x cluster is approaching the wall. A voice that DIED at 600s
     is already reported; a voice that SURVIVED at 550s looks exactly like one
     that finished in 20s, so a cluster drifting toward the ceiling stays
     invisible until the run it finally crosses it.
  2. Whether a timeout is systemic or noise. "grok timed out" reads as bad luck;
     "grok x breakage, 3 runs, always at the wall" is a different bug.

Deterministic shell-level rendering on purpose (same contract as the rest of the
pipeline: assembly is never an LLM step) — the presenter prints what this emits.

Usage: telemetry-report.py <telemetry.jsonl> [--timeout-seconds N]
Each record carries the wall it actually ran under (SWARM_TIMEOUT is
overridable); --timeout-seconds is only the fallback for records without one.
Exit 0 always when the file is readable or absent: telemetry is diagnostics, and
must never turn a completed review into a failed one. Exit 2 on usage error.
"""
import json
import sys

# Fraction of the wall above which a SURVIVING call is called out. 0.6 is chosen
# from measurement, not taste: a real grok x breakage call landed at 374s/600s
# (62%) on a 42 KB diff while the same cluster at a lower effort took 161s (27%)
# — so the band above ~60% is where a normal run already sits close enough that
# ordinary variance reaches the wall.
WARN_FRACTION = 0.6


def load(path):
    """Return (records, unreadable_reason). A malformed line is skipped, not
    fatal: a partially-written file (the run died mid-call) still carries the
    completed calls, which is exactly when the numbers matter most."""
    records, skipped = [], 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
                else:
                    skipped += 1
    except FileNotFoundError:
        return [], "no telemetry file (the run predates it, or no external voice ran)"
    except OSError as exc:
        return [], f"telemetry unreadable: {exc}"
    return records, (f"{skipped} malformed line(s) skipped" if skipped else None)


def wall(rec, fallback):
    """The limit THIS call ran under. Per-record, not global: SWARM_TIMEOUT is
    overridable, so a fixed assumption would report a percentage of a wall that
    was never in force."""
    try:
        secs = int(rec.get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        secs = 0
    return secs if secs > 0 else fallback


def label(rec):
    unit = rec.get("unit") or "-"
    return f"{rec.get('backend', '?')}:{unit}"


def render(records, timeout_seconds):
    """Longest call first — the interesting end of the distribution is the top."""
    lines = []
    ordered = sorted(records, key=lambda r: _secs(r), reverse=True)
    for rec in ordered:
        secs = _secs(rec)
        limit = wall(rec, timeout_seconds)
        pct = (secs / limit * 100) if limit else 0
        if rec.get("timed_out"):
            mark = f"  ✗ TIMED OUT at the {limit}s wall"
        elif rec.get("backend_rc") not in (0, None):
            mark = f"  ✗ failed (rc={rec.get('backend_rc')})"
        elif limit and secs >= limit * WARN_FRACTION:
            mark = f"  ⚠️  {pct:.0f}% of the {limit}s wall"
        else:
            mark = ""
        effort = rec.get("effort") or "?"
        kib = (rec.get("prompt_bytes") or 0) / 1024
        lines.append(f"  {label(rec):<28} {secs:>4}s  {effort:<6} {kib:>6.1f} KiB{mark}")
    return lines


def _secs(rec):
    try:
        return int(rec.get("seconds") or 0)
    except (TypeError, ValueError):
        return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write(__doc__)
        return 2
    path = argv[0]
    timeout_seconds = 600
    rest = argv[1:]
    while rest:
        if rest[0] == "--timeout-seconds" and len(rest) > 1:
            try:
                timeout_seconds = int(rest[1])
            except ValueError:
                sys.stderr.write(f"invalid --timeout-seconds: {rest[1]}\n")
                return 2
            rest = rest[2:]
        else:
            sys.stderr.write(f"unknown argument: {rest[0]}\n")
            return 2

    records, note = load(path)
    if not records:
        # Say nothing renderable rather than printing an empty header: the
        # presenter drops the whole section when there is no output.
        if note:
            sys.stderr.write(note + "\n")
        return 0

    print("Voices:")
    for line in render(records, timeout_seconds):
        print(line)

    timed_out = [r for r in records if r.get("timed_out")]
    if timed_out:
        # Name the LENSES, not just the backend: the point of the per-cluster
        # topology is that a dead call costs specific coverage.
        print()
        for rec in timed_out:
            print(f"  ⚠️  {label(rec)} hit the {wall(rec, timeout_seconds)}s wall — that cluster "
                  f"reviewed without {rec.get('backend', '?')}.")
    if note:
        print(f"  ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
