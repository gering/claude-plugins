#!/usr/bin/env bash
# agents.sh — swarm backend adapter layer
#
# Uniform interface over the review backends (claude, codex, grok) so swarm
# skills never talk to an external CLI directly.
#
# Subcommands:
#   list [--json]         Probe all backends -> human table or JSON array
#   available <backend>   Exit 0 if the CLI is installed; prints its version
#   ready <backend>       Exit 0 if authenticated/usable; hint on stderr if not
#   run <backend> [opts]  Run a review prompt -> findings JSON on stdout
#       --prompt-file <f>   Read the lens prompt from a file (default: stdin)
#       --effort <level>    low|medium|high|xhigh|max (default: xhigh)
#       --model <name>      Backend model override
#       --schema <file>     JSON schema to enforce (default: bundled finding.schema.json)
#
# Backend notes (probed against codex 0.128 / grok 0.2.77, 2026-07):
#   claude — probe-only: reviews run in-session via the Agent tool, so
#            `run claude` is a usage error. available/ready/list include it.
#   codex  — `codex exec --output-schema` in a read-only sandbox; the pure
#            schema JSON arrives via --output-last-message (stdout carries the
#            agent transcript, which we discard). Auth: `codex login status`.
#            Reasoning effort has no "max" tier -> max maps to xhigh.
#   grok   — headless `-p` with inline --json-schema; the validated object is
#            the `.structuredOutput` field of a response envelope. Needs an
#            explicit model (-m): the default grok model rejects --effort.
#            Auth heuristic: non-empty ~/.grok/auth.json (no status command).
#
# Exit codes: 0 ok · 1 unavailable / not ready / run failed · 2 usage error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SCHEMA="$SCRIPT_DIR/schema/finding.schema.json"
GROK_DEFAULT_MODEL="grok-build"
GROK_AUTH_FILE="${GROK_AUTH_FILE:-$HOME/.grok/auth.json}"

# Temp file for codex's --output-last-message; must be a global (not a
# function-local) so the EXIT trap still sees it under `set -u`.
TMP_OUT=""
cleanup() { if [[ -n "${TMP_OUT:-}" ]]; then rm -f "$TMP_OUT"; fi; }
trap cleanup EXIT

usage() {
  sed -n '2,17p' "$0" | sed 's|^# \{0,1\}||'
  exit 2
}

validate_backend() {
  case "$1" in
    claude|codex|grok) ;;
    *) echo "Unknown backend: $1 (expected claude|codex|grok)" >&2; exit 2 ;;
  esac
}

# ---------- probes ----------

available_version() {
  # Prints the CLI's version line; exit 1 if not installed.
  local backend="$1"
  command -v "$backend" >/dev/null || return 1
  "$backend" --version 2>/dev/null | head -1
}

ready_check() {
  local backend="$1"
  case "$backend" in
    claude) return 0 ;;  # in-session, no separate auth
    codex)  codex login status >/dev/null 2>&1 ;;
    grok)   [[ -s "$GROK_AUTH_FILE" ]] ;;
  esac
}

ready_hint() {
  case "$1" in
    claude) echo "install Claude Code" ;;
    codex)  echo "run: codex login" ;;
    grok)   echo "run: grok login" ;;
  esac
}

# ---------- subcommands ----------

subcmd_available() {
  local backend="${1:-}"
  [[ -z "$backend" ]] && usage
  validate_backend "$backend"
  available_version "$backend"
}

subcmd_ready() {
  local backend="${1:-}"
  [[ -z "$backend" ]] && usage
  validate_backend "$backend"
  if ! available_version "$backend" >/dev/null; then
    echo "$backend: not installed" >&2
    exit 1
  fi
  if ready_check "$backend"; then
    echo "ready"
  else
    echo "$backend: not ready — $(ready_hint "$backend")" >&2
    exit 1
  fi
}

print_rows() {
  # One TSV row per backend: backend, available, version, ready, hint
  local b ver avail rdy hint
  for b in claude codex grok; do
    ver="" avail=no rdy=no hint=""
    if ver="$(available_version "$b")"; then
      avail=yes
      if ready_check "$b"; then rdy=yes; else hint="$(ready_hint "$b")"; fi
    else
      hint="not installed"
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$b" "$avail" "$ver" "$rdy" "$hint"
  done
}

subcmd_list() {
  if [[ "${1:-}" == "--json" ]]; then
    print_rows | python3 -c '
import json, sys
rows = []
for line in sys.stdin:
    b, avail, ver, rdy, hint = (line.rstrip("\n").split("\t") + [""] * 5)[:5]
    rows.append({"backend": b, "available": avail == "yes", "version": ver,
                 "ready": rdy == "yes", "hint": hint})
json.dump(rows, sys.stdout, indent=2)
print()
'
  else
    { printf 'BACKEND\tAVAILABLE\tVERSION\tREADY\tHINT\n'; print_rows; } \
      | column -t -s $'\t'
  fi
}

subcmd_run() {
  local backend="${1:-}"
  [[ -z "$backend" ]] && usage
  shift
  validate_backend "$backend"
  if [[ "$backend" == "claude" ]]; then
    echo "claude reviews run in-session via the Agent tool, not through this adapter" >&2
    exit 2
  fi

  local prompt_file="" effort="xhigh" model="" schema="$DEFAULT_SCHEMA"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prompt-file) prompt_file="$2"; shift 2 ;;
      --effort)      effort="$2";      shift 2 ;;
      --model)       model="$2";       shift 2 ;;
      --schema)      schema="$2";      shift 2 ;;
      *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
  done
  case "$effort" in
    low|medium|high|xhigh|max) ;;
    *) echo "Invalid effort: $effort (low|medium|high|xhigh|max)" >&2; exit 2 ;;
  esac
  [[ -f "$schema" ]] || { echo "Schema not found: $schema" >&2; exit 2; }

  local prompt
  if [[ -n "$prompt_file" ]]; then
    prompt="$(cat "$prompt_file")"
  else
    prompt="$(cat)"
  fi
  [[ -z "$prompt" ]] && { echo "Empty prompt (use --prompt-file or stdin)" >&2; exit 2; }

  if ! available_version "$backend" >/dev/null; then
    echo "$backend: not installed" >&2
    exit 1
  fi
  if ! ready_check "$backend"; then
    echo "$backend: not ready — $(ready_hint "$backend")" >&2
    exit 1
  fi

  case "$backend" in
    codex) run_codex "$prompt" "$effort" "$model" "$schema" ;;
    grok)  run_grok  "$prompt" "$effort" "$model" "$schema" ;;
  esac
}

run_codex() {
  local prompt="$1" effort="$2" model="$3" schema="$4"
  [[ "$effort" == "max" ]] && effort="xhigh"

  TMP_OUT="$(mktemp)"

  # The schema-validated JSON lands in $TMP_OUT; codex's stdout copy of the
  # final message is discarded (its transcript goes to stderr = debug info).
  # stdin must be closed: with an inherited open non-TTY stdin, codex waits
  # for "additional input from stdin" and hangs.
  if ! codex exec -s read-only --skip-git-repo-check \
      -c model_reasoning_effort="$effort" \
      ${model:+-m "$model"} \
      --output-schema "$schema" \
      --output-last-message "$TMP_OUT" \
      "$prompt" </dev/null >/dev/null; then
    echo "codex exec failed" >&2
    exit 1
  fi
  [[ -s "$TMP_OUT" ]] || { echo "codex produced no output" >&2; exit 1; }
  python3 -c 'import json,sys; json.load(sys.stdin)' <"$TMP_OUT" 2>/dev/null \
    || { echo "codex returned invalid JSON" >&2; exit 1; }
  cat "$TMP_OUT"
  echo
}

run_grok() {
  local prompt="$1" effort="$2" model="$3" schema="$4"
  local raw
  if ! raw="$(grok -m "${model:-$GROK_DEFAULT_MODEL}" --effort "$effort" \
      --json-schema "$(cat "$schema")" \
      -p "$prompt" </dev/null)"; then
    echo "grok failed" >&2
    exit 1
  fi
  printf '%s' "$raw" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if d.get("type") == "error":
    sys.stderr.write("grok error: %s\n" % d.get("message", "unknown"))
    sys.exit(1)
out = d.get("structuredOutput")
if out is None:
    sys.stderr.write("grok returned no structuredOutput\n")
    sys.exit(1)
json.dump(out, sys.stdout)
print()
'
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    list)          subcmd_list "$@" ;;
    available)     subcmd_available "$@" ;;
    ready)         subcmd_ready "$@" ;;
    run)           subcmd_run "$@" ;;
    ""|-h|--help)  usage ;;
    *)             echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
