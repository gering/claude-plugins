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
#            explicit model (-m): the default model (grok-composer-2.5-fast)
#            rejects --effort AND ignores --json-schema (structuredOutput
#            stays null) — grok-build is the only schema-capable choice.
#            Auth heuristic: non-empty ~/.grok/auth.json (no status command).
#
# Exit codes: 0 ok · 1 unavailable / not ready / run failed · 2 usage error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SCHEMA="$SCRIPT_DIR/schema/finding.schema.json"
GROK_DEFAULT_MODEL="grok-build"
# Default HOME so `$HOME` expansions below (auth file, sandbox deny paths) don't
# abort the whole script under `set -u` when HOME is unset.
HOME="${HOME:-$(cd ~ 2>/dev/null && pwd || echo /nonexistent)}"
GROK_AUTH_FILE="${GROK_AUTH_FILE:-$HOME/.grok/auth.json}"

# Temp file for codex's --output-last-message; must be a global (not a
# function-local) so the EXIT trap still sees it under `set -u`.
TMP_OUT=""
cleanup() { if [[ -n "${TMP_OUT:-}" ]]; then rm -f "$TMP_OUT"; fi; }
trap cleanup EXIT

print_usage() {
  # Usage block = header comment up to (not including) "# Backend notes";
  # bounded by pattern, not line numbers, so header edits can't truncate it.
  awk 'NR < 2 {next} /^# Backend notes/ {exit} {sub(/^# ?/, ""); print}' "$0"
}

usage() {
  print_usage >&2
  exit 2
}

require_python3() {
  command -v python3 >/dev/null \
    || { echo "python3 not found on PATH — required by the swarm adapter" >&2; exit 1; }
}

column_or_cat() {
  # Align TSV into columns when util-linux `column` is present; otherwise pass
  # the raw TSV through so `list` degrades instead of dying (exit 127) under
  # set -euo pipefail on a minimal host.
  if command -v column >/dev/null; then
    column -t -s $'\t'
  else
    cat
  fi
}

# Wall-clock cap for external CLI calls so a hung backend fails fast instead of
# blocking a fan-out forever. Uses coreutils timeout/gtimeout when available;
# passes through unchanged if neither exists (best-effort, never a hard dep).
# Override seconds via SWARM_TIMEOUT; 0 disables.
ADAPTER_TIMEOUT="${SWARM_TIMEOUT:-600}"
_timeout_warned=""
with_timeout() {
  if [[ "$ADAPTER_TIMEOUT" == "0" ]]; then "$@"; return; fi
  if command -v timeout >/dev/null; then timeout "$ADAPTER_TIMEOUT" "$@"
  elif command -v gtimeout >/dev/null; then gtimeout "$ADAPTER_TIMEOUT" "$@"
  else
    # No coreutils timeout: run bare, but say so once — otherwise the documented
    # cap silently never applies (e.g. stock macOS) and a hung backend blocks.
    if [[ -z "$_timeout_warned" ]]; then
      echo "warning: no timeout/gtimeout on PATH — external calls run WITHOUT the ${ADAPTER_TIMEOUT}s cap (install coreutils, or set SWARM_TIMEOUT=0 to silence)" >&2
      _timeout_warned=1
    fi
    "$@"
  fi
}

require_valid_timeout() {
  # A malformed SWARM_TIMEOUT would reach `timeout` and exit 125 — which the
  # rc==124 checks don't recognize, so every external run would misreport as a
  # backend failure. Reject up front. Only the literal integer disables (0).
  [[ "$ADAPTER_TIMEOUT" =~ ^[0-9]+$ ]] \
    || { echo "Invalid SWARM_TIMEOUT='$ADAPTER_TIMEOUT' — must be a non-negative integer (seconds; 0 disables)" >&2; exit 2; }
}

# OS-level read-deny jail for external CLI calls (the root-cause fix for
# "-s read-only still permits file reads"). The diff is untrusted, so an
# injected payload could steer a backend to read local secrets. This denies
# reads of common secret stores while leaving the CLI's own config + the repo
# readable (verified: ~/.aws blocked, ~/.codex readable). macOS: sandbox-exec;
# Linux: bwrap; else passthrough (scrub_secrets + backend flags remain).
# Extra deny paths via SWARM_DENY_PATHS (colon-separated).
_sandbox_deny_paths() {
  # $1 = the calling backend (its OWN credential dir stays readable — it needs
  # it to authenticate; the OTHER backends' cred dirs are denied so an injected
  # read can't steal a sibling's token). A denylist is a backstop, not the
  # primary defense: grok runs tool-less, the diff is inlined so backends need
  # no file reads at all, and scrub_secrets + env filtering back it up. A full
  # allowlist jail is impractical here — the node/bun-based CLIs load runtime
  # from all over $HOME, so deny-$HOME breaks them (documented in the blueprint).
  local own="${1:-}"
  printf '%s\n' \
    "$HOME/.aws" "$HOME/.ssh" "$HOME/.gnupg" "$HOME/.netrc" \
    "$HOME/.config/gcloud" "$HOME/.kube" "$HOME/.docker" \
    "$HOME/.git-credentials" "$HOME/.npmrc" "$HOME/.pypirc" \
    "$HOME/.config/gh" "$HOME/.cargo/credentials" "/etc/master.passwd" \
    "$HOME/.config/anthropic" "$HOME/.config/openai" "$HOME/.claude.json"
  if [[ "$own" != "codex" ]]; then printf '%s\n' "$HOME/.codex"; fi
  if [[ "$own" != "grok" ]]; then printf '%s\n' "$HOME/.grok"; fi
  local extra="${SWARM_DENY_PATHS:-}"
  # if-form, not `[[ … ]] && …`: the latter returns 1 when extra is empty, and
  # under set -e that aborts the `profile="$(…)"` assignment that calls this.
  if [[ -n "$extra" ]]; then printf '%s\n' "${extra//:/$'\n'}"; fi
  return 0
}

SANDBOX_CMD=()
_sandbox_warned=""
_sandbox_ready=""
_init_sandbox() {
  # Lazy, per-backend (needs python3 for realpath). One backend per process, so
  # building once is fine.
  [[ -n "$_sandbox_ready" ]] && return
  _sandbox_ready=1
  local backend="${1:-}"
  if command -v sandbox-exec >/dev/null; then
    # Build the deny profile via python: realpath each path (defeats symlinks
    # like /tmp→/private/tmp, /etc→/private/etc — sandbox-exec matches the
    # resolved path) and deny it as BOTH a subpath (dirs + contents) and a
    # literal (single files like ~/.netrc).
    local profile
    profile="$(_sandbox_deny_paths "$backend" | python3 -c '
import os, sys
rules = []
for line in sys.stdin:
    p = line.strip()
    if not p:
        continue
    rp = os.path.realpath(p)
    esc = rp.replace("\\", "\\\\").replace("\"", "\\\"")
    rules.append("(subpath \"%s\")" % esc)
    rules.append("(literal \"%s\")" % esc)
sys.stdout.write("(version 1)(allow default)(deny file-read* %s)" % " ".join(rules))
')"
    SANDBOX_CMD=(sandbox-exec -p "$profile")
  elif command -v bwrap >/dev/null; then
    # --tmpfs masks a directory; a regular file (e.g. ~/.netrc) needs a bind of
    # an empty source instead — --tmpfs over a file dies with ENOTDIR.
    local args=(--dev-bind / /) p
    while IFS= read -r p; do
      if [[ -d "$p" ]]; then args+=(--tmpfs "$p")
      elif [[ -f "$p" ]]; then args+=(--ro-bind /dev/null "$p")
      fi
    done < <(_sandbox_deny_paths "$backend")
    SANDBOX_CMD=(bwrap "${args[@]}")
  fi
}

_env_filter_args() {
  # Emit `-u NAME` pairs for secret-shaped env vars: the jail blocks file reads
  # but backends inherit the environment, so a secret in AWS_SECRET_ACCESS_KEY /
  # *_TOKEN / *_API_KEY would otherwise pass straight through. Backend auth comes
  # from its config dir (not env), so stripping these is safe.
  local name
  while IFS='=' read -r name _; do
    case "$name" in
      AWS_*|*_TOKEN|*_SECRET|*_PASSWORD|*PASSWD*|*_API_KEY|*APIKEY*|*_CREDENTIALS|GH_TOKEN|GITHUB_TOKEN|NPM_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|XAI_API_KEY|GROK_API_KEY)
        printf '%s\n' "-u" "$name" ;;
    esac
  done < <(env)
}

sandboxed() {
  # OS jail + env filter around an external call. $1 = backend (its own cred dir
  # stays readable; siblings' are denied). Warn once if no jail is available.
  local backend="$1"; shift
  _init_sandbox "$backend"
  if ((${#SANDBOX_CMD[@]} == 0)) && [[ -z "$_sandbox_warned" ]]; then
    echo "warning: no sandbox-exec/bwrap — external calls run without an OS read-deny jail (secret scrub + env filter still apply)" >&2
    _sandbox_warned=1
  fi
  local env_args=() _e
  while IFS= read -r _e; do env_args+=("$_e"); done < <(_env_filter_args)
  # order: timeout → env (strip secrets) → sandbox-exec (jail) → backend
  with_timeout env ${env_args[@]+"${env_args[@]}"} ${SANDBOX_CMD[@]+"${SANDBOX_CMD[@]}"} "$@"
}

scrub_secrets() {
  # Last-line-of-defense secret filter on the findings JSON before it leaves the
  # adapter. The diff under review is untrusted and a prompt-injected backend
  # could try to route a credential into a findings string field; redact
  # secret-shaped content here so it can never reach the merged report, even if
  # a backend sandbox is bypassed. Redacts (not blocks) so real findings survive.
  python3 -c '
import re, sys
PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED-AWS-KEY]"),
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), "[REDACTED-PRIVATE-KEY]"),
    (re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+]{20,}"), "aws_secret_access_key=[REDACTED]"),
    (re.compile(r"(?i)\b(secret|token|password|passwd|api[_-]?key)\b\s*[=:]\s*[A-Za-z0-9/+._-]{16,}"), r"\1=[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED-GH-TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "[REDACTED-API-KEY]"),
]
data = sys.stdin.read()
hit = False
for pat, repl in PATTERNS:
    data, n = pat.subn(repl, data)
    if n: hit = True
if hit:
    sys.stderr.write("swarm: redacted secret-shaped content from findings before output\n")
sys.stdout.write(data)
'
}

validate_backend() {
  case "$1" in
    claude|codex|grok) ;;
    *) echo "Unknown backend: $1 (expected claude|codex|grok)" >&2; exit 2 ;;
  esac
}

# ---------- probes ----------

available_version() {
  # Prints the backend's version line; exit 1 if not installed.
  local backend="$1"
  if [[ "$backend" == "claude" ]]; then
    # claude reviews run in-session via the Agent tool, so inside a Claude
    # Code session the backend exists by definition — the PATH lookup only
    # provides a nicer version string, never gates availability.
    # Capture separately (not `… || echo in-session`): a SIGPIPE from head()
    # under pipefail would otherwise run BOTH the real version and the
    # fallback, printing two lines.
    local cver
    cver="$(claude --version 2>/dev/null | head -1 || true)"
    echo "${cver:-in-session}"
    return 0
  fi
  command -v "$backend" >/dev/null || return 1
  # Best-effort version string. `|| true` + explicit `return 0`: once
  # `command -v` confirmed the CLI, a non-zero `--version` exit or a SIGPIPE
  # from head() (under pipefail) must NOT flip an installed backend to
  # "unavailable".
  "$backend" --version 2>/dev/null | head -1 || true
  return 0
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
  # claude needs no hint: it is always available + ready in-session.
  case "$1" in
    codex) echo "run: codex login" ;;
    grok)  echo "run: grok login" ;;
  esac
}

# ---------- subcommands ----------

subcmd_available() {
  local backend="${1:-}"
  [[ -z "$backend" ]] && usage
  validate_backend "$backend"
  available_version "$backend"
}

require_usable() {
  # Shared installed+ready gate for `ready` and `run`.
  local backend="$1"
  if ! available_version "$backend" >/dev/null; then
    echo "$backend: not installed" >&2
    exit 1
  fi
  if ! ready_check "$backend"; then
    echo "$backend: not ready — $(ready_hint "$backend")" >&2
    exit 1
  fi
}

subcmd_ready() {
  local backend="${1:-}"
  [[ -z "$backend" ]] && usage
  validate_backend "$backend"
  require_usable "$backend"
  echo "ready"
}

print_rows() {
  # One TSV row per backend: backend, available, version, ready, hint.
  # $1 fills empty fields — the human table needs a placeholder because BSD
  # column collapses adjacent tabs, shifting later columns left.
  local placeholder="${1:-}"
  local b ver avail rdy hint
  for b in claude codex grok; do
    ver="" avail=no rdy=no hint=""
    if ver="$(available_version "$b")"; then
      avail=yes
      if ready_check "$b"; then rdy=yes; else hint="$(ready_hint "$b")"; fi
    else
      hint="not installed"
    fi
    ver="${ver//$'\t'/ }"  # a tab inside a version string would shift the TSV columns
    printf '%s\t%s\t%s\t%s\t%s\n' "$b" "$avail" "${ver:-$placeholder}" "$rdy" "${hint:-$placeholder}"
  done
}

subcmd_list() {
  case "${1:-}" in
    --json)
      require_python3
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
      ;;
    "")
      { printf 'BACKEND\tAVAILABLE\tVERSION\tREADY\tHINT\n'; print_rows "-"; } \
        | column_or_cat
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
  esac
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
    [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
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

  # The prompt travels as ONE argv word, so the binding limit is the per-argument
  # cap, not total ARG_MAX: Linux MAX_ARG_STRLEN is 128 KiB (macOS has no
  # per-arg cap but a ~1 MiB total). Cap at 120 KiB to stay under the Linux
  # per-arg limit with headroom for the schema arg + environment. Measure BYTES
  # (a multibyte prompt would slip a `${#prompt}` char-count yet overflow exec),
  # and for a file check its size BEFORE reading it (a 500 MiB file would
  # otherwise be slurped into a shell variable first).
  local max_bytes=122880 nbytes
  local prompt
  if [[ -n "$prompt_file" ]]; then
    [[ -f "$prompt_file" ]] || { echo "Prompt file not found: $prompt_file" >&2; exit 2; }
    nbytes=$(wc -c < "$prompt_file")
    (( nbytes > max_bytes )) && { echo "Prompt file too large ($(( nbytes / 1024 )) KiB > 120 KiB) — inline less of the diff, or have the agent read it itself" >&2; exit 2; }
    prompt="$(cat "$prompt_file")"
  else
    # Guard against blocking forever on an interactive/absent stdin: with no
    # --prompt-file and a TTY on fd 0, `cat` would hang waiting for input.
    [[ -t 0 ]] && { echo "No prompt: pass --prompt-file <f> or pipe the prompt on stdin" >&2; exit 2; }
    prompt="$(cat)"
    nbytes=$(printf '%s' "$prompt" | wc -c)
    (( nbytes > max_bytes )) && { echo "Prompt too large ($(( nbytes / 1024 )) KiB > 120 KiB) — inline less of the diff, or have the agent read it itself" >&2; exit 2; }
  fi
  [[ -z "$prompt" ]] && { echo "Empty prompt (use --prompt-file or stdin)" >&2; exit 2; }

  require_usable "$backend"
  require_python3
  require_valid_timeout

  case "$backend" in
    codex) run_codex "$prompt" "$effort" "$model" "$schema" ;;
    grok)  run_grok  "$prompt" "$effort" "$model" "$schema" ;;
  esac
}

run_codex() {
  local prompt="$1" effort="$2" model="$3" schema="$4"
  [[ "$effort" == "max" ]] && effort="xhigh"

  TMP_OUT="$(mktemp)"

  # Array (not unquoted ${model:+…}) so a model name with whitespace is one
  # argv word, matching the effort_args idiom in run_grok.
  local model_args=()
  [[ -n "$model" ]] && model_args=(-m "$model")

  # The schema-validated JSON lands in $TMP_OUT; codex's stdout copy of the
  # final message is discarded (its transcript goes to stderr = debug info).
  # stdin must be closed: with an inherited open non-TTY stdin, codex waits
  # for "additional input from stdin" and hangs.
  # `--` ends flag parsing: a prompt starting with "-" (e.g. a markdown
  # bullet) would otherwise be rejected as an unknown flag.
  # 2>/dev/null discards codex's reasoning transcript (goes to stderr): under
  # injection it could echo a secret it read, and it never passes scrub_secrets.
  # The exit code (incl. 124 timeout) still drives error handling.
  local rc=0
  sandboxed codex codex exec -s read-only --skip-git-repo-check \
      -c model_reasoning_effort="$effort" \
      ${model_args[@]+"${model_args[@]}"} \
      --output-schema "$schema" \
      --output-last-message "$TMP_OUT" \
      -- "$prompt" </dev/null >/dev/null 2>/dev/null || rc=$?
  if (( rc != 0 )); then
    (( rc == 124 )) && echo "codex exec timed out after ${ADAPTER_TIMEOUT}s" >&2 || echo "codex exec failed" >&2
    exit 1
  fi
  [[ -s "$TMP_OUT" ]] || { echo "codex produced no output" >&2; exit 1; }
  # Validate SHAPE, not just JSON syntax: a valid-but-wrong object (no findings
  # array) would otherwise pass through and crash the merge step downstream.
  python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.stderr.write("codex returned invalid JSON\n"); sys.exit(1)
if not (isinstance(d, dict) and isinstance(d.get("findings"), list)):
    sys.stderr.write("codex output is not a {findings:[...]} object\n"); sys.exit(1)
' <"$TMP_OUT" || exit 1
  scrub_secrets <"$TMP_OUT"
  echo
}

run_grok() {
  local prompt="$1" effort="$2" model="$3" schema="$4"
  local grok_model="${model:-$GROK_DEFAULT_MODEL}"

  # Preflight-reject non-default models: only grok-build enforces --json-schema
  # (and accepts --effort). Any other model would silently return
  # structuredOutput:null and the run would fail late with no schema output —
  # so reject up front with a usage error rather than burn a review on it.
  # (Schema-less models like grok-composer-2.5-fast belong on the caller's own
  # defensive-parse path, not this schema-enforcing adapter.)
  if [[ "$grok_model" != "$GROK_DEFAULT_MODEL" ]]; then
    echo "grok model '$grok_model' does not enforce --json-schema — the adapter requires schema output; use $GROK_DEFAULT_MODEL (default)" >&2
    exit 2
  fi

  # --single=<prompt> (not "-p <prompt>"): as a separate argv word a prompt
  # starting with "-" would be parsed as a flag.
  # Sandbox: the diff is untrusted and could try to steer grok into reading
  # local secrets or fetching a URL to exfiltrate. grok reviews the diff INLINE
  # in the prompt, so it needs NO tools — `--tools ""` (empty allowlist)
  # removes file/shell access (verified: grok then can't read files), and
  # `--disable-web-search` closes the network channel. scrub_secrets on the
  # output is the belt-and-braces backstop.
  local raw rc=0
  raw="$(sandboxed grok grok -m "$grok_model" --effort "$effort" \
      --tools "" --disable-web-search \
      --json-schema "$(cat "$schema")" \
      --single="$prompt" </dev/null 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    (( rc == 124 )) && echo "grok timed out after ${ADAPTER_TIMEOUT}s" >&2 || echo "grok failed" >&2
    exit 1
  fi
  printf '%s' "$raw" | python3 -c '
import json, sys
data = sys.stdin.read()
try:
    d = json.loads(data)
except Exception:
    # Do NOT echo the raw bytes: on the error path they never pass scrub_secrets
    # and could carry injected/secret content. Report size only.
    sys.stderr.write("grok returned invalid JSON (%d bytes; content withheld)\n" % len(data))
    sys.exit(1)
if not isinstance(d, dict):
    sys.stderr.write("grok returned non-object JSON (%s)\n" % type(d).__name__)
    sys.exit(1)
if d.get("type") == "error":
    sys.stderr.write("grok error: %s\n" % d.get("message", "unknown"))
    sys.exit(1)
out = d.get("structuredOutput")
if out is None:
    sys.stderr.write("grok returned no structuredOutput\n")
    sys.exit(1)
if not (isinstance(out, dict) and isinstance(out.get("findings"), list)):
    sys.stderr.write("grok structuredOutput is not a {findings:[...]} object\n")
    sys.exit(1)
json.dump(out, sys.stdout)
print()
' | scrub_secrets
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    list)          subcmd_list "$@" ;;
    available)     subcmd_available "$@" ;;
    ready)         subcmd_ready "$@" ;;
    run)           subcmd_run "$@" ;;
    -h|--help)     print_usage; exit 0 ;;
    "")            usage ;;
    *)             echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
