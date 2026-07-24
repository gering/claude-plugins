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
#   jail                  Print jail=yes|no (working OS sandbox wrapper?)
#   run <backend> [opts]  Run a review prompt -> findings JSON on stdout
#       --prompt-file <f>   Read the lens prompt from a file (default: stdin)
#       --effort <level>    low|medium|high|xhigh|max (default: xhigh)
#       --model <name>      Backend model override
#       --schema <file>     JSON schema to enforce (default: bundled finding.schema.json)
#
# Backend notes (probed against codex 0.144.6 / grok 0.2.103, 2026-07):
#   claude — probe-only: reviews run in-session via the Agent tool, so
#            `run claude` is a usage error. available/ready/list include it.
#   codex  — `codex exec --output-schema` under `-s read-only` with
#            `-C <repo>` + `-c tools.web_search=true` (web works under read-only;
#            no sandbox loosen). Pure schema JSON via --output-last-message.
#            Auth: `codex login status`. Effort has no "max" tier -> max→xhigh.
#   grok   — headless `--single=` with inline --json-schema; the validated
#            object is `.structuredOutput` of a response envelope. Needs an
#            explicit model (-m): grok-4.5 is the sole schema-capable model and
#            accepts --effort (ladder is low|medium|high — no max tier, so the
#            adapter maps xhigh/max down to high, mirroring codex's missing
#            max). Read+web via STRICT `--tools` allowlist
#            (read_file,list_dir,grep,web_search,web_fetch) + `--cwd <repo>`;
#            no write/shell tools. Readiness is model-aware: auth (non-empty
#            ~/.grok/auth.json — there is no status command) AND grok-4.5 listed
#            by `grok models`. The CLI rejects an unlisted -m id at launch
#            ("unknown model id") and drops/renames models between releases
#            (0.2.101 removed grok-composer-2.5-fast), so an auth-only check
#            would advertise a model the CLI no longer offers. The probe
#            degrades to auth-only — with a warning, never silently — when it
#            cannot run: no coreutils timeout to bound it, or an empty/
#            unparseable list. Its bound is SWARM_PROBE_TIMEOUT (10s), not
#            SWARM_TIMEOUT (a review-length cap).
#
# Security floor (both external voices):
#   - OS secret-jail (sandbox-exec/bwrap) denies HOME secret stores +
#     repo-ROOT .env*/data/*.pem/id_*/*.key (nested via SWARM_DENY_PATHS).
#   - Egress guard is a prompt policy (model-cooperation-dependent) — the
#     jail is the hard boundary. scrub_secrets filters OUTPUT only.
#   - No write/shell/network-write tools; review is read-only.
#
# Exit codes: 0 ok · 1 unavailable / not ready / run failed · 2 usage error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SCHEMA="$SCRIPT_DIR/schema/finding.schema.json"
CODEX_DEFAULT_MODEL="gpt-5.6-terra"
GROK_DEFAULT_MODEL="grok-4.5"
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

# OS-level read-deny jail for external CLI calls. Both voices may now read
# project files (out-of-diff bugs), so the jail is the HARD boundary that bounds
# blast radius if an injection steers a read: common secret stores stay
# unreadable while the CLI's own config + non-secret project files remain
# readable (verified: ~/.aws blocked, ~/.codex readable). macOS: sandbox-exec;
# Linux: bwrap; else passthrough (scrub_secrets + backend flags remain).
# Extra deny paths via SWARM_DENY_PATHS (colon-separated absolute paths).
_sandbox_deny_paths() {
  # $1 = the calling backend (its OWN credential dir stays readable — it needs
  # it to authenticate; the OTHER backends' cred dirs are denied so an injected
  # read can't steal a sibling's token. ACCEPTED RESIDUAL: with web on, an
  # injected read of that own dir could exfiltrate the backend's OWN API token —
  # unjailable without breaking its auth; bounded to that one token and named
  # in [[swarm-backend-adapter]] § residual risk). A denylist is a backstop, not a full
  # allowlist: the node/bun-based CLIs load runtime from all over $HOME, so
  # deny-$HOME breaks them (documented in the blueprint). scrub_secrets + env
  # filtering + the prompt egress guard back it up.
  local own="${1:-}"
  printf '%s\n' \
    "$HOME/.aws" "$HOME/.ssh" "$HOME/.gnupg" "$HOME/.netrc" \
    "$HOME/.config/gcloud" "$HOME/.kube" "$HOME/.docker" \
    "$HOME/.git-credentials" "$HOME/.npmrc" "$HOME/.pypirc" \
    "$HOME/.config/gh" "$HOME/.cargo/credentials" "/etc/master.passwd" \
    "$HOME/.config/anthropic" "$HOME/.config/openai" "$HOME/.claude.json"
  if [[ "$own" != "codex" ]]; then printf '%s\n' "$HOME/.codex"; fi
  if [[ "$own" != "grok" ]]; then printf '%s\n' "$HOME/.grok"; fi
  # Repo-local secrets: .env*, data/, common key/cred files at repo root.
  # Best-effort (skip if not in a git work tree); only emit paths that exist so
  # the profile stays clean. The `[[ -e ]]` guard also filters an unmatched
  # pattern's literal fallback (`[[ -e ]]` does not glob its operand), so no
  # nullglob juggling is needed — test_sandbox_deny.py pins that behavior.
  # ROOT-LEVEL ONLY (not recursive): a nested apps/api/.env is not auto-denied —
  # deliberate (bwrap can't regex, a recursive glob bloats the profile on large
  # trees). HOME cred stores are covered at full depth; nested repo secrets go
  # via SWARM_DENY_PATHS. (documented in [[swarm-backend-adapter]] § Posture)
  local repo
  repo="$(_repo_root)"
  if [[ -n "$repo" ]]; then
    # When the reviewed root is a LINKED WORKTREE, also deny the MAIN checkout's
    # root globs: untracked .env/data/ never propagate into a worktree, so in
    # the standard /kickoff layout the real secrets sit in the main checkout —
    # a plain readable sibling path without this (0.6.0 self-review, round 2).
    local roots=("$repo") common main
    common="$(git rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "$common" ]]; then
      case "$common" in /*) ;; *) common="$repo/$common" ;; esac  # relative when cwd IS the main root
      main="$(dirname "$common")"
      if [[ "$main" != "$repo" && -d "$main" ]]; then roots+=("$main"); fi
    fi
    # Key globs are the SSH id names (id_rsa*/id_ed25519*/…), NOT a bare id_* —
    # that would jail legit files (id_utils.py), and under bwrap a denied path
    # reads as silently EMPTY (tmpfs / /dev/null bind), not EPERM, feeding the
    # reviewers false "file is empty" evidence. .git/config can embed a token in
    # a remote URL; .npmrc/.pypirc/credentials.json mirror the HOME store list.
    local r p
    for r in "${roots[@]}"; do
      for p in "$r"/.env* "$r"/data "$r"/*.pem "$r"/id_rsa* "$r"/id_ed25519* \
               "$r"/id_ecdsa* "$r"/id_dsa* "$r"/*.key "$r"/.npmrc "$r"/.pypirc \
               "$r"/credentials.json "$r"/.git/config; do
        if [[ -e "$p" ]]; then printf '%s\n' "$p"; fi
      done
    done
  fi
  local extra="${SWARM_DENY_PATHS:-}"
  # if-form, not `[[ … ]] && …`: the latter returns 1 when extra is empty, and
  # under set -e that aborts the `profile="$(…)"` assignment that calls this.
  if [[ -n "$extra" ]]; then printf '%s\n' "${extra//:/$'\n'}"; fi
  return 0
}

# Resolve the repo root for -C/--cwd scoping. Best-effort: empty when not in a
# git work tree (callers fall back to the ambient cwd).
_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || true
}

_jail_available() {
  # $1 = backend. Builds the jail (memoized) and reports whether an OS sandbox
  # wrapper exists. run_codex/run_grok consult this to FAIL CLOSED: the read+web
  # posture is only safe under the OS secret-jail (the hard boundary), so on a
  # host without sandbox-exec/bwrap the externals degrade to the 0.5.x
  # tool-less/no-web flags instead of running read+web bare — 0.5.x was safe
  # there precisely because the flags, not the jail, closed the channel.
  _init_sandbox "$1"
  (( ${#SANDBOX_CMD[@]} > 0 ))
}

SANDBOX_CMD=()
_sandbox_warned=""
# Sentinel no backend name can equal: the memo below compares against the
# backend, so an empty initial value would collide with an empty/unset argument
# and skip jail construction entirely — failing OPEN, the one direction a
# sandbox must never fail.
_sandbox_ready="<none>"
_init_sandbox() {
  # Lazy, per-backend (needs python3 for realpath). Memoized ON THE BACKEND, not
  # a bare "already built" flag: the profile encodes which cred dir stays
  # readable, so reusing another backend's jail would deny a backend its OWN
  # token and leave a sibling's readable — the exact cross-backend theft the
  # denylist prevents. Only sandboxed() (a `run` call) reaches here today, but
  # keying on the backend keeps it correct if a second entry point returns.
  local backend="${1:-}"
  [[ "$_sandbox_ready" == "$backend" ]] && return
  _sandbox_ready="$backend"
  SANDBOX_CMD=()
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
  # Probe that the wrapper actually WORKS, not merely exists on PATH: a
  # container can ship a bwrap that cannot create namespaces, and a broken
  # sandbox-exec would otherwise fail every review run with an opaque backend
  # error instead of taking the callers' fail-closed degrade path. On probe
  # failure treat the host as jail-less (audibly) — _jail_available then
  # reports false and run_codex/run_grok degrade.
  if ((${#SANDBOX_CMD[@]} > 0)); then
    if ! "${SANDBOX_CMD[@]}" true >/dev/null 2>&1; then
      echo "warning: ${SANDBOX_CMD[0]} is installed but not functional here — treating host as jail-less; externals fail closed" >&2
      SANDBOX_CMD=()
    fi
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
  # Wraps a review, which processes the untrusted diff — that is why it carries
  # the full jail; the readiness probe deliberately does NOT go through here
  # (it processes no diff, and _init_sandbox's python3 profile-build must not
  # become a dependency of the local `ready`/`list` paths — see grok_model_fetch).
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
  # DRIFT WARNING: these patterns hand-mirror the JS output gate (scrubField in
  # swarm-review.js); keep both lists in sync so they redact identically.
  python3 -c '
import re, sys
PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED-AWS-KEY]"),
    # PEM key: full BEGIN...END block (any interior — incl. encrypted Proc-Type/
    # DEK-Info metadata), OR a key truncated by a field cap (header + base64, no
    # END). Alternation: END-block first, else base64 run.
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----|[A-Za-z0-9+/=\r\n]*)"), "[REDACTED-PRIVATE-KEY]"),
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

# Wall-clock bound for the readiness probe. Deliberately NOT SWARM_TIMEOUT: that
# caps a full review (600s default) and may be disabled with 0, neither of which
# suits a probe that `list`/`ready` block on. Override with SWARM_PROBE_TIMEOUT;
# a malformed or 0 value falls back to 10 (never uncapped, never a `timeout`
# usage error that would read as "model missing").
PROBE_TIMEOUT="${SWARM_PROBE_TIMEOUT:-10}"
[[ "$PROBE_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || PROBE_TIMEOUT=10

# grok's model list, memoized for the process (`grok models` is a network call —
# fetch at most once).
_grok_models_done=""
_grok_models=""
_probe_degraded() {
  # The ONE exit for every "the model check did not happen" route (no timeout
  # binary, probe failed, probe timed out, unparseable list). Each ends in the
  # same trust-auth degrade, so each must be equally audible: the docs promise
  # the check falls back "never silently", and a promise that holds on only some
  # routes is the runtime-lie this branch removes.
  echo "warning: grok model probe unavailable ($1) — readiness falls back to auth alone; the ${GROK_DEFAULT_MODEL} check did not run" >&2
}
grok_model_fetch() {
  # Populates the cache globals. Call it DIRECTLY — never as `$(grok_model_fetch)`:
  # a command substitution runs it in a subshell, the assignments die with that
  # subshell, and every caller silently re-pays the network call.
  [[ -n "$_grok_models_done" ]] && return 0
  _grok_models_done=1
  local to=""
  if command -v timeout >/dev/null; then to="timeout"
  elif command -v gtimeout >/dev/null; then to="gtimeout"
  fi
  if [[ -z "$to" ]]; then
    # `ready`/`list` were purely local before this probe, so it must never be the
    # thing that hangs them: with no way to bound the call, skip it rather than
    # run it uncapped.
    _probe_degraded "no timeout/gtimeout on PATH — install coreutils to restore it"
    return 0
  fi
  # Run grok DIRECTLY, not through sandboxed(): this is a readiness check, not a
  # review — it passes no untrusted diff to grok, so it needs no read-deny jail,
  # exactly like the sibling `codex login status` check a few lines down. Going
  # through sandboxed() would also drag _init_sandbox's python3 profile-build
  # into the `ready`/`list` paths (a hard dep they never had), whose failure the
  # degrade branch would then misreport as "grok models failed".
  # `-k` (SIGKILL after a grace period) is what actually enforces the bound: a
  # grok that ignores SIGTERM, or forks a stdout-inheriting child, would keep the
  # `$(...)` substitution blocking past the timeout — the "must never hang" hole.
  local raw rc=0
  raw="$("$to" -k 3 "$PROBE_TIMEOUT" grok models </dev/null 2>/dev/null)" || rc=$?
  # Check rc BEFORE looking at the output, and discard whatever arrived: a probe
  # killed mid-stream (timeout) or erroring late can still have flushed a PARTIAL
  # list. Reading that as authoritative is worse than not probing — a truncated
  # list missing grok-4.5 reports "model gone" and tells the user to update an
  # already-current CLI. Only a clean exit produces an answer; everything else is
  # a degrade.
  if (( rc != 0 )); then
    # GNU timeout: 124 = SIGTERM ended the job at the deadline (a definite
    # timeout). 137 (128+9) = the job was SIGKILLed — almost always our own `-k`
    # firing on a SIGTERM-ignoring grok, but an OS OOM-kill or an external
    # SIGKILL yields the same code, so don't assert a timeout that may not have
    # happened; word it for both.
    if (( rc == 124 )); then _probe_degraded "\`grok models\` timed out after ${PROBE_TIMEOUT}s"
    elif (( rc == 137 )); then _probe_degraded "\`grok models\` was killed (SIGKILL — likely the ${PROBE_TIMEOUT}s \`-k\` bound)"
    else _probe_degraded "\`grok models\` failed (rc=$rc)"
    fi
    return 0
  fi
  # One model id PER BULLET LINE: the id is the FIRST grok-shaped token after the
  # `*` marker (documented form "  * grok-4.5 (default)"). Take only the first —
  # scanning the whole line would also pick up a grok-4.5 mentioned in trailing
  # PROSE on another model's line ("* grok-5 (successor to grok-4.5)"), reporting
  # a retired model as still offered. Match the id SUBSTRING, not the raw field,
  # so glued-on punctuation ("grok-4.5," / "grok-4.5." / backticks) doesn't ride
  # along and break the exact-match below; the pattern ends on alphanumerics, so
  # a trailing separator is never captured. No id-shaped token → empty → degrade.
  _grok_models="$(printf '%s\n' "$raw" | awk '
    /^[[:space:]]*\*/ {
      for (i = 1; i <= NF; i++)
        if (match($i, /grok-[A-Za-z0-9]+([._-][A-Za-z0-9]+)*/)) {
          print substr($i, RSTART, RLENGTH)
          break
        }
    }')"
  if [[ -z "$_grok_models" ]]; then
    _probe_degraded "\`grok models\` returned no model ids — output format may have changed"
  fi
}

grok_model_offered() {
  # Three-state, collapsed to an exit code: 0 = the CLI lists grok-4.5, 1 = it
  # lists models but NOT grok-4.5 (an honest "gone"), 0 = the list is empty /
  # unparseable / not probed (probe unusable — offline, no timeout binary, or a
  # future CLI renaming the subcommand). The empty case deliberately trusts auth
  # instead of failing closed: silently dropping grok from every fan-out is
  # worse than letting run_grok surface its explicit "unknown model id" error.
  grok_model_fetch
  local list="$_grok_models"
  # Substring match on newline-fenced text, NOT `grep -qxF`: an early-exiting
  # `grep -q` can SIGPIPE the writer, and pipefail would then report failure
  # even on a hit.
  case "$list" in
    "") return 0 ;;
    *) case $'\n'"$list"$'\n' in
         *$'\n'"$GROK_DEFAULT_MODEL"$'\n'*) return 0 ;;
         *) return 1 ;;
       esac ;;
  esac
}

ready_check() {
  local backend="$1"
  case "$backend" in
    claude) return 0 ;;  # in-session, no separate auth
    codex)  codex login status >/dev/null 2>&1 ;;
    # Model-aware: auth alone would advertise grok even when the CLI no longer
    # offers the one model the adapter can drive (grok drops/renames models
    # between releases — 0.2.101 removed grok-composer-2.5-fast).
    grok)   [[ -s "$GROK_AUTH_FILE" ]] && grok_model_offered ;;
  esac
}

ready_hint() {
  # claude needs no hint: it is always available + ready in-session.
  case "$1" in
    codex) echo "run: codex login" ;;
    grok)
      if [[ ! -s "$GROK_AUTH_FILE" ]]; then
        echo "run: grok login"
      else
        echo "this grok CLI does not offer $GROK_DEFAULT_MODEL (see: grok models) — update the grok CLI"
      fi
      ;;
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

subcmd_jail() {
  # Machine-readable jail availability. The /swarm:review skill reads this to
  # brand its run-start notice and the externals' prompt capabilities honestly:
  # jail=no means the fail-closed degrade will apply (grok tool-less/no-web,
  # codex web hard-off) — the "audible warning" must reach the USER, and the
  # transport layer discards adapter stderr, so this is the visible channel.
  # Requires python3 on macOS (profile build) exactly like a run would.
  if _jail_available codex; then echo "jail=yes"; else echo "jail=no"; fi
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
      --effort)      effort="$2"; shift 2 ;;
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

  # Pin the swarm codex model (override with --model) so the ensemble is
  # deterministic regardless of the user's global ~/.codex/config.toml default.
  # Array (not unquoted ${model:+…}) so a model name with whitespace is one
  # argv word, matching the effort_args idiom in run_grok.
  local model_args=(-m "${model:-$CODEX_DEFAULT_MODEL}")

  # Scope the working root to the repo so exploration reads project files (not
  # an ambient cwd). `-C` is a working root — do NOT use `--add-dir` (writable).
  # Web research is enabled under read-only via tools.web_search (model-native;
  # verified under -s read-only + --strict-config; no sandbox loosen needed).
  # OS secret-jail + prompt egress guard bound the blast radius.
  local repo_args=()
  local repo
  repo="$(_repo_root)"
  if [[ -n "$repo" ]]; then
    repo_args=(-C "$repo")
  else
    echo "warning: codex could not resolve repo root (git rev-parse) — running without -C" >&2
  fi

  # FAIL CLOSED without the OS jail: web + FS-read with no read-deny boundary
  # would let an injected read reach ~/.aws etc. and exfiltrate via web_search.
  # Degrade closes the EGRESS half: web is HARD-disabled (=false, not merely
  # omitted — an omitted flag would inherit a future codex default or a user
  # config that turns web on). FS reads remain: -s read-only is codex's most
  # restrictive sandbox tier (there is no no-read tier), the same read surface
  # codex always had in 0.5.x — the degrade is per-voice, not "tool-less", and
  # the docs describe it that way (do not over-claim).
  local web_args=(-c tools.web_search=true)
  if ! _jail_available codex; then
    echo "warning: no working OS sandbox (sandbox-exec/bwrap) — codex web search HARD-disabled (fail closed); FS reads stay inside codex's own read-only sandbox (0.5.x read surface)" >&2
    web_args=(-c tools.web_search=false)
  fi

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
  sandboxed codex codex exec -s read-only \
      ${repo_args[@]+"${repo_args[@]}"} \
      --skip-git-repo-check \
      ${web_args[@]+"${web_args[@]}"} \
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

# grok tool allowlist: read/explore + verified web tools only. STRICT allowlist
# — mutating tools (write, search_replace, run_terminal_command, spawn_*, …)
# stay out. Web IDs probed 2026-07-20 on grok 0.2.103: web_search, web_fetch.
# Do NOT fall back to a denylist that could admit a mutating tool.
GROK_READ_TOOLS="read_file,list_dir,grep"
GROK_WEB_TOOLS="web_search,web_fetch"
GROK_TOOLS="${GROK_READ_TOOLS},${GROK_WEB_TOOLS}"

run_grok() {
  local prompt="$1" effort="$2" model="$3" schema="$4"
  # grok's effort ladder is low|medium|high (0.2.101 dropped max) — map the two
  # higher adapter tiers down so a stale caller degrades instead of erroring,
  # mirroring codex's max→xhigh mapping.
  case "$effort" in xhigh|max) effort="high" ;; esac
  local grok_model="${model:-$GROK_DEFAULT_MODEL}"

  # Preflight-reject any non-default model: only grok-4.5 enforces --json-schema
  # (and accepts --effort). Another model would silently return
  # structuredOutput:null and fail late with no schema output — so reject up
  # front with a usage error rather than burn a review on it.
  if [[ "$grok_model" != "$GROK_DEFAULT_MODEL" ]]; then
    echo "grok model '$grok_model' does not enforce --json-schema — the adapter requires schema output; use $GROK_DEFAULT_MODEL (the only supported grok model)" >&2
    exit 2
  fi

  # --single=<prompt> (not "-p <prompt>"): as a separate argv word a prompt
  # starting with "-" would be parsed as a flag.
  # Read+web posture (0.6.0): strict --tools allowlist grants file-read
  # (read_file,list_dir,grep) + web (web_search,web_fetch) so grok can find
  # out-of-diff bugs and research external knowledge. No write/shell tools.
  # --cwd pins the project root. The OS secret-jail (sandboxed) blocks
  # credential paths; the prompt egress guard (SKILL.md HDR, outside the diff
  # fence) is the model-cooperation web policy; scrub_secrets is the output
  # backstop. Do NOT re-add --disable-web-search or --tools "" unconditionally —
  # they are reserved for the no-jail fail-closed degrade below.
  local cwd_args=()
  local repo
  repo="$(_repo_root)"
  if [[ -n "$repo" ]]; then
    cwd_args=(--cwd "$repo")
  else
    echo "warning: grok could not resolve repo root (git rev-parse) — running without --cwd" >&2
  fi

  # FAIL CLOSED without the OS jail: grok's file+web tools with no read-deny
  # boundary would re-open the exfil channel 0.5.x closed by flags. Degrade to
  # the 0.5.x posture (tool-less, no web) and say so — the review still runs on
  # the inlined diff, just without exploration.
  local tool_args=(--tools "$GROK_TOOLS")
  if ! _jail_available grok; then
    echo "warning: no sandbox-exec/bwrap — grok degraded to tool-less/no-web (fail closed; read+web needs the OS secret-jail)" >&2
    tool_args=(--tools "" --disable-web-search)
  fi

  local raw rc=0
  raw="$(sandboxed grok grok -m "$grok_model" --effort "$effort" \
      ${tool_args[@]+"${tool_args[@]}"} \
      ${cwd_args[@]+"${cwd_args[@]}"} \
      --json-schema "$(cat "$schema")" \
      --single="$prompt" </dev/null 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    # stderr is deliberately discarded (injection guard), so name the likely
    # cause: an older CLI that predates the pinned model reports Ready (auth
    # heuristic) yet rejects the model id at runtime.
    (( rc == 124 )) && echo "grok timed out after ${ADAPTER_TIMEOUT}s" >&2 \
      || echo "grok failed — check that the installed grok CLI knows model '$grok_model' ($GROK_DEFAULT_MODEL needs grok >= 0.2.101)" >&2
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
    jail)          subcmd_jail ;;
    run)           subcmd_run "$@" ;;
    -h|--help)     print_usage; exit 0 ;;
    "")            usage ;;
    *)             echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
