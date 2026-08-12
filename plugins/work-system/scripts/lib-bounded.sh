#!/usr/bin/env bash
# lib-bounded.sh — `run_bounded`, the one time-bounded command runner.
#
# SOURCE this file (`. "<dir>/lib-bounded.sh"`); it defines a function and runs
# nothing. Both agent-registry.sh (CLI availability probes) and herdr-launch.sh
# (the herdr capability probe + its polling reads) need the same primitive, and
# they shipped a copy each until the copies drifted — one merged the child's
# stderr into the captured output, the other didn't. A sibling-file dependency is
# already the norm between these scripts (herdr-launch.sh shells out to
# herdr-teardown.sh, agent-registry.sh), so one definition beats two.
#
# NOT yet migrated: herdr-agent.sh's `_ha_bounded` and ws-statusline.sh's own
# `run_bounded`. Both lack the SIGTERM->SIGKILL escalation and the 124
# normalization below, so they are worth moving here — but they sit on unrelated
# code paths (the lane registry and the status line), and this file was extracted
# during a launch-path fix. Named explicitly so the consolidation is a known
# follow-up rather than an oversight.

# run_bounded <seconds> <cmd...> — run cmd with a hard time bound, so an external
# probe or a wedged server can never hang the caller. Prints cmd's stdout; returns
# cmd's exit code, or 124 if the bound killed it.
#
# Uses timeout/gtimeout when present; otherwise self-bounds with a detached killer
# that escalates SIGTERM -> SIGKILL, so a process ignoring SIGTERM still can't
# block us. Two details that look incidental but are not:
#   * cmd's stdout goes to a TEMP FILE, not this function's stdout. Callers run
#     this inside a command substitution, and if the killer has to SIGKILL cmd, an
#     orphaned grandchild inherits cmd's fds — were that the $( ) pipe, the caller
#     would block until the orphan died. Only the final `cat` writes to stdout.
#   * cmd's STDERR is deliberately NOT merged into that file: it stays on the
#     function's stderr, which the CALL SITE controls (`run_bounded … 2>/dev/null`
#     to drop it, `2>&1` to capture it). Merging it here would silently fold error
#     text into probe output that callers then pattern-match.
# The killer's own fds go to /dev/null for the same command-substitution reason.
run_bounded() {
  local secs="$1"; shift
  local rc=0
  # `-k 1`: GNU timeout only SIGTERMs at the deadline then waits — a child that
  # ignores SIGTERM would run forever on the COMMON path (any host with timeout/
  # gtimeout). --kill-after escalates to SIGKILL, matching the self-watchdog below.
  if command -v timeout >/dev/null 2>&1; then
    timeout  -k 1 "$secs" "$@" || rc=$?
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout -k 1 "$secs" "$@" || rc=$?
  else
    local tmp; tmp="$(mktemp)"
    "$@" >"$tmp" &
    local pid=$!
    ( sleep "$secs"; kill "$pid" 2>/dev/null; sleep 2; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
    local killer=$!
    if wait "$pid" 2>/dev/null; then rc=0; else rc=$?; fi
    kill "$killer" 2>/dev/null || true
    cat "$tmp"; rm -f "$tmp"
  fi
  # Normalize a bounded kill to ONE "timed out" code (124): GNU timeout reports
  # 124 (SIGTERM) or 137 (needed SIGKILL); the watchdog's `wait` yields 137/143.
  # Callers use 124 to treat a slow probe as inconclusive, distinct from a real
  # non-zero exit (e.g. genuine auth failure).
  case "$rc" in 137|143) rc=124 ;; esac
  return "$rc"
}
