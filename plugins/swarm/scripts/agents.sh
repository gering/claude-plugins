#!/usr/bin/env bash
# agents.sh — swarm backend adapter layer
#
# Uniform interface over the review backends (claude, codex, grok, kimi) so
# swarm skills never talk to an external CLI directly.
#
# Subcommands:
#   list [--json]         Probe all backends -> human table or JSON array
#   available <backend>   Exit 0 if the CLI is installed; prints its version
#   ready <backend>       Exit 0 if authenticated/usable; hint on stderr if not
#   jail                  Print jail=yes|no (working OS sandbox wrapper?)
#   config                Print the RESOLVED numeric config (max_prompt_bytes,
#                         cap_headroom, oversize_threshold, timeout_seconds,
#                         probe_timeout_seconds, probe_budget_seconds). Callers
#                         read this instead of parsing SWARM_* themselves — one
#                         parser, one verdict. probe_budget_seconds is what the
#                         workflow sizes its timeout margin from.
#   run <backend> [opts]  Run a review prompt -> findings JSON on stdout
#       --prompt-file <f>   Read the lens prompt from a file (default: stdin)
#       --lens-instr <s>    Per-cluster lens instruction, prepended VERBATIM
#                           before the prompt body (the workflow passes the
#                           gated cluster's briefs here). Rejected if empty.
#       --lens-instr-sum <hex>  FNV-1a/32 checksum of --lens-instr (8 hex).
#                           REQUIRED whenever --lens-instr is given; a mismatch
#                           means it was altered in transport -> hard error.
#       --effort <level>    low|medium|high|xhigh|max (default: xhigh)
#       --model <name>      Backend model override
#       --schema <file>     JSON schema to enforce (default: bundled finding.schema.json)
#       --telemetry <file>  Append one JSON line per call (backend, unit, effort,
#                           model, prompt_bytes, seconds, timeout_seconds,
#                           backend_rc, adapter_rc, timed_out). Written on EVERY
#                           exit path, so a timeout is recorded too.
#       --unit <name>       Cluster/lens label recorded in the telemetry line
#
# The prompt reaches the backend OUT-OF-BAND (codex: stdin · grok:
# --prompt-file · kimi: ACP v1 NDJSON over stdio), never on argv — so the diff
# is bounded by model context, not by exec's MAX_ARG_STRLEN.
# SWARM_MAX_PROMPT_BYTES (default 512 KiB) is that sanity cap.
#
# Backend notes (probed against codex 0.147.0 / grok 1.0.13 / kimi-code 0.32.0,
# 2026-07..08):

#   claude — probe-only: reviews run in-session via the Agent tool, so
#            `run claude` is a usage error. available/ready/list include it.
#   codex  — `codex exec --output-schema` under `-s read-only` with
#            `-C <repo>` + `-c tools.web_search=true` (web works under read-only;
#            no sandbox loosen). Pure schema JSON via --output-last-message.
#            Prompt via `-- -` = read instructions from stdin.
#            Auth: `codex login status`. Effort has no "max" tier -> max→xhigh.
#   grok   — headless `--prompt-file` with inline --json-schema; the validated
#            object is `.structuredOutput` of a response envelope. Needs an
#            explicit model (-m). The model is DISCOVERED, not hard-pinned: the
#            newest canonical id the CLI lists (bare version ids, major >= 4)
#            whose --json-schema enforcement is verified in
#            GROK_SCHEMA_VERIFIED; a newer unverified model is reported, never
#            silently chosen. GROK_DEFAULT_MODEL is only the fallback floor.
#            Effort ladder is low|medium|high (no max tier, so the adapter maps
#            xhigh/max down to high, mirroring codex's missing max). Read+web via STRICT `--tools` allowlist
#            (read_file,list_dir,grep,web_search,web_fetch) + `--cwd <repo>`;
#            no write/shell tools. Readiness is model-aware: auth (non-empty
#            ~/.grok/auth.json — there is no status command) AND at least one
#            SCHEMA-VERIFIED canonical model listed by `grok models` (not one
#            fixed id — the model is discovered); an unprobeable list degrades to
#            trusting auth rather than dropping the backend. The CLI rejects an unlisted -m id at launch
#            ("unknown model id") and drops/renames models between releases
#            (0.2.101 removed grok-composer-2.5-fast), so an auth-only check
#            would advertise a model the CLI no longer offers. The probe
#            degrades to auth-only — with a warning, never silently — when the
#            list comes back empty or unparseable. It always RUNS: the adapter
#            bounds it with its own watchdog where coreutils is missing. Its
#            bound is SWARM_PROBE_TIMEOUT (10s, ceiling 20s), not SWARM_TIMEOUT
#            (a review-length cap).
#   kimi   — ACP v1 headless session over NDJSON stdio. `-p` is deliberately
#            NOT used: it only accepts the full prompt on argv and would restore
#            Linux MAX_ARG_STRLEN failures. The local ACP client rejects every
#            approval-gated tool call, so read/search/fetch remain available but
#            write/edit/shell cannot execute. Kimi has no CLI schema flag; the
#            client validates the final assistant JSON strictly against the
#            configured schema and fails closed without retry. ACP exposes the
#            selected model and thinking ladder (low|high|max), so adapter effort
#            maps down to those verified tiers instead of pretending it is ignored.
#            No working jail/repo root means Kimi does not run at all: unlike the
#            other CLIs it has no safe inline-prompt/tool-less fallback.
#
# Security floor (all external voices):
#   - OS secret-jail (sandbox-exec/bwrap) denies HOME secret stores +
#     repo-ROOT .env*/data/*.pem/id_rsa*|id_ed25519*|…/*.key/.npmrc/.pypirc/
#     credentials.json (nested via SWARM_DENY_PATHS; main checkout too in a
#     linked worktree). No working jail -> fail closed per voice.
#   - Egress guard is a prompt policy (model-cooperation-dependent) — the
#     jail is the hard boundary. scrub_secrets filters OUTPUT only.
#   - No write/shell/network-write tools; review is read-only.
#
# Exit codes: 0 ok · 1 unavailable / not ready / run failed · 2 usage error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SCHEMA="$SCRIPT_DIR/schema/finding.schema.json"
KIMI_ACP_CLIENT="$SCRIPT_DIR/kimi-acp.py"
CODEX_DEFAULT_MODEL="gpt-5.6-terra"
KIMI_DEFAULT_MODEL="kimi-code/k3-256k"
KIMI_BIN="${SWARM_KIMI_BIN:-kimi}"
# The FALLBACK used wherever discovery cannot run — no model list, offline, an
# unparseable listing. Deliberately the OLDEST still-verified id, not the newest:
# this value is only ever reached when we could not read what the CLI offers, and
# guessing high there is the expensive direction. An older CLI that ships
# grok-4.5 but not grok-4.6 would be handed an unknown model id, reject every
# call, and lose the whole grok family for the run — the exact silent-family-loss
# this plugin keeps fighting. Guessing low costs at most a slightly older model
# on a host we could not probe. Raise it only when the low end of
# GROK_SCHEMA_VERIFIED is retired.
GROK_DEFAULT_MODEL="grok-4.5"

# --- canonical grok model discovery -------------------------------------------
#
# Ported from the cc-harness-agents helper in ~/dotfiles (which tracks the same
# provider), with ONE substituted gate: that helper withholds an upgrade until a
# model's context window is known, because its proxy catalog carries no
# context_length. The adapter does not care about the window — it cares that the
# model ENFORCES `--json-schema`, because the whole ensemble is built on schema
# JSON. A model that merely accepts the flag and returns `structuredOutput: null`
# fails LATE, after burning a full review.
#
# GROK_CANONICAL_RE — anchored, accepts ONLY bare version ids. A provider catalog
# mixes canonical releases with variants that are not drop-in substitutes for a
# review: dated snapshots, reasoning/non-reasoning splits, multi-agent, build,
# composer and image/video ids. Against the live xAI catalog this accepts
# grok-4.3/4.5/4.6 and rejects grok-3-mini, grok-4.20-0309-reasoning,
# grok-4.20-multi-agent-0309, grok-build-0.1, grok-composer-2.5-fast and the
# grok-imagine-* family. Major >= 4 is deliberate: grok-3* is a generation this
# adapter never used, so a catalog that regresses to it cannot pull us backwards.
GROK_CANONICAL_RE='^grok-([4-9]|[1-9][0-9]+)(\.[0-9]+)?$'

# GROK_SCHEMA_VERIFIED — the hard gate. A discovered model is only SELECTED when
# its schema enforcement has been confirmed by hand against the real CLI. The
# model list says nothing about it, and guessing is what this table exists to
# prevent: an unverified newer model is REPORTED (stderr), never silently chosen,
# so adopting it is a one-line edit here after a check, not an accident.
# Verified 2026-08-16 against grok CLI 1.0.3 — both return an envelope whose
# `.structuredOutput` carries the schema's `findings` array:
GROK_SCHEMA_VERIFIED="grok-4.5
grok-4.6"
# Default HOME so `$HOME` expansions below (auth file, sandbox deny paths) don't
# abort the whole script under `set -u` when HOME is unset.
HOME="${HOME:-$(cd ~ 2>/dev/null && pwd || echo /nonexistent)}"
GROK_AUTH_FILE="${GROK_AUTH_FILE:-$HOME/.grok/auth.json}"
# Kimi's live OAuth token is here. ~/.kimi-code/oauth/kimi-code can exist as a
# zero-byte compatibility path even while authenticated, so it is not an auth
# signal (verified in work-system's Kimi worker integration).
KIMI_CREDENTIALS_FILE="${KIMI_CREDENTIALS_FILE:-$HOME/.kimi-code/credentials/kimi-code.json}"

# Temp files: codex's --output-last-message, and the assembled prompt the
# backends read out-of-band (see the transport note in `run`). Both must be
# globals (not function-locals) so the EXIT trap still sees them under `set -u`.
# TMP_PROMPT holds the untrusted diff, so it is removed on EVERY exit path,
# including the error ones.
TMP_OUT=""
TMP_PROMPT=""
# Scratch file for the wrapper-free bound (_bounded_bg). Registered here for the
# same reason as the other two: the whole point of that path is surviving a kill,
# and a file removed only on the normal return paths is orphaned by exactly the
# signal it was created to bound.
TMP_BOUNDED=""

# Per-call telemetry (opt-in via --telemetry). WHY it exists: an external voice
# that dies at the wall is reported, but a voice that *survived* at 550s looks
# identical to one that finished in 20s — so a cluster drifting toward the
# ceiling is invisible until it crosses it, and "grok timed out" cannot be told
# apart from "grok × breakage times out every single run". Duration per
# backend×unit is the missing number; the failure attribution (backend, unit,
# lenses) already exists in backendErrors since 0.7.0.
TELEMETRY_FILE=""
TELEMETRY_UNIT=""
TELEMETRY_START=""
TELEMETRY_BACKEND=""
TELEMETRY_EFFORT=""
TELEMETRY_MODEL=""
TELEMETRY_BYTES=""
# The backend CLI's own rc, captured before run_codex/run_grok/run_kimi translate it into
# the adapter's exit code — otherwise a timeout (124) and a plain failure both
# reach the trap as exit 1 and the one distinction worth logging is lost.
TELEMETRY_RC=""

_json_escape() {
  # Minimal JSON string escaping: backslash first (or it would double-escape the
  # quotes we add next), then quotes, then the control characters that would
  # break the one-line record.
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//$'\n'/\\n}"
  v="${v//$'\r'/\\r}"
  v="${v//$'\t'/\\t}"
  # JSON forbids EVERY unescaped control character below 0x20, not just the three
  # with short forms. Emitting one produces a record the reader discards as
  # malformed — and a silently dropped record reads as "that voice never ran",
  # the exact misreading this telemetry exists to prevent. Anything still
  # remaining in that range becomes \u00XX.
  # Fast path: the overwhelming majority of fields (cluster names, model ids)
  # contain no control characters at all, and the loop below is per-character.
  # [[:cntrl:]] is a POSIX class, not a collation range, so it does not carry the
  # locale hazard the loop guards against.
  if [[ ! "$v" =~ [[:cntrl:]] ]]; then
    printf '%s' "$v"
    return 0
  fi
  local out="" ch i code
  for (( i = 0; i < ${#v}; i++ )); do
    ch="${v:i:1}"
    # Numeric, not `[[ "$ch" < $'\x20' ]]`: `<` compares by the current locale's
    # COLLATION, where a control character can sort after a space and the test
    # quietly stops firing — emitting a raw control byte, i.e. a record the reader
    # discards as malformed, which reads as "that voice never ran".
    # `code >= 0` is load-bearing: for a multi-byte character printf yields the
    # code point (>= 128) or a NEGATIVE first byte depending on locale, and
    # treating that as "below 0x20" would mangle every non-ASCII name into
    # \uffffffffffffffc3 garbage. Control characters are 0..31, always positive.
    # `printf -v` is the BUILTIN form: `code=$(printf …)` forked a subshell for
    # every character of every escaped field.
    printf -v code '%d' "'$ch" 2>/dev/null || code=32
    if (( code >= 0 && code < 32 )); then
      printf -v ch '\\u%04x' "'$ch"
    fi
    out+="$ch"
  done
  printf '%s' "$out"
}

_write_telemetry() {
  # $1 = the adapter's exit code. Best-effort: telemetry must never turn a
  # successful review into a failure, so every step tolerates failure and the
  # function always returns 0.
  local adapter_rc="${1:-}"
  [[ -n "$TELEMETRY_FILE" && -n "$TELEMETRY_START" ]] || return 0
  local end secs
  end=$(date +%s 2>/dev/null) || return 0
  secs=$(( end - TELEMETRY_START ))
  # ONE printf of a single line: concurrent per-cluster voices append to the
  # same file, and a lone write under the pipe-buffer size is atomic with
  # O_APPEND, so lines interleave but never tear. Do not split this into
  # multiple writes.
  # Record the wall this call actually ran under, and read it from
  # `_enforced_wall` — NOT from `_adapter_timeout`, which says what was
  # CONFIGURED, not whether anything enforced it. A reader that takes the
  # configured value computes "% of the wall" against a limit that may never have
  # applied. `_enforced_wall` is set by `_set_enforced_wall`, in the main shell,
  # before the call (see the note on that function for why it cannot live inside
  # with_timeout or any command substitution). Unset writes 0, which the reader
  # already understands as "no cap".
  # Never call adapter_timeout() from here: this runs in the EXIT trap, where
  # _resolve_int`s `exit 2` would replace the real status and lose the record.
  # Escape the string fields. They are adapter-controlled today (cluster names,
  # ids filtered by GROK_CANONICAL_RE), but a `"` or `\` in any of them would
  # emit a line the reader silently SKIPS as malformed — telemetry that quietly
  # loses records is worse than none, since it reads as "that voice never ran".
  # DELIBERATELY not `python3 -c json.dumps`, even though the `run` path already
  # requires python3: this runs in the EXIT trap, including on paths that failed
  # BEFORE require_python3, and a fork that can fail is the wrong dependency for
  # the code whose job is to leave a record when everything else went wrong.
  # The locale traps it carries (backslash ordering, multibyte printf codes) are
  # pinned by test_telemetry_report.py across C/en_US/de_DE.
  local _b _u _e _m
  _b="$(_json_escape "$TELEMETRY_BACKEND")"; _u="$(_json_escape "$TELEMETRY_UNIT")"
  _e="$(_json_escape "$TELEMETRY_EFFORT")";  _m="$(_json_escape "$TELEMETRY_MODEL")"
  printf '{"backend":"%s","unit":"%s","effort":"%s","model":"%s","prompt_bytes":%s,"seconds":%s,"timeout_seconds":%s,"backend_rc":%s,"adapter_rc":%s,"timed_out":%s}\n' \
    "$_b" "$_u" "$_e" "$_m" \
    "$(( ${TELEMETRY_BYTES:-0} + 0 ))" "$secs" "$(( ${_enforced_wall:-0} + 0 ))" "${TELEMETRY_RC:-null}" "${adapter_rc:-null}" \
    "$( _is_timeout_rc "${TELEMETRY_RC:-0}" && echo true || echo false )" \
    >> "$TELEMETRY_FILE" 2>/dev/null || true
  return 0
}

cleanup() {
  # FIRST statement: $? here is the script's exit status, and any command below
  # would overwrite it.
  local rc=$?
  if [[ -n "${TMP_OUT:-}" ]]; then rm -f "$TMP_OUT"; fi
  if [[ -n "${TMP_PROMPT:-}" ]]; then rm -f "$TMP_PROMPT"; fi
  if [[ -n "${TMP_BOUNDED:-}" ]]; then rm -f "$TMP_BOUNDED"; fi
  _write_telemetry "$rc"
}
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
# --- numeric configuration: ONE parse, ONE set of rules -----------------------
#
# Three env knobs are read by BOTH this adapter and the skill's prep block, and
# every one of them has now caused the same bug class: the two sides parsed the
# same string differently, or one side validated what the other did not. Three
# separate review rounds found three separate instances (cap decimal-forced on
# one side only; timeout decimal-forced on one side only; a post-conversion
# positivity check present in one place). Patching the reported instance each
# time never ended it, so the parse lives HERE and callers ask for the result
# (`agents.sh config`) instead of re-deriving it.
#
# _resolve_int <name> <raw> <default> <min> [max]
#   Prints the resolved value; exits 2 with a usage error otherwise. Rules, all
#   of which exist because their absence was a real defect:
#     - digits only (a sign or unit suffix must not reach arithmetic);
#     - `10#` DECIMAL forcing, or "0100000" is octal here and decimal there;
#     - the range check runs AFTER conversion, or "00" passes a `!= 0` test and
#       then behaves as 0;
#     - an explicit upper bound, or a huge value wraps in 64-bit arithmetic to a
#       small positive number that still satisfies `> 0`.
_resolve_int() {
  local name="$1" raw="$2" def="$3" min="$4" max="${5:-}"
  [[ -n "$raw" ]] || raw="$def"
  [[ "$raw" =~ ^[0-9]+$ ]] \
    || { echo "Invalid $name='$raw' — must be an integer" >&2; exit 2; }
  # Reject before arithmetic: bash silently truncates on overflow, so a 25-digit
  # value would wrap rather than error.
  (( ${#raw} <= 18 )) \
    || { echo "Invalid $name='$raw' — too large" >&2; exit 2; }
  local v=$((10#$raw))
  (( v >= min )) \
    || { echo "Invalid $name='$raw' — must be >= $min" >&2; exit 2; }
  if [[ -n "$max" ]]; then
    (( v <= max )) \
      || { echo "Invalid $name='$raw' — must be <= $max" >&2; exit 2; }
  fi
  printf '%s' "$v"
}

# Upper bounds are sanity rails, not policy: 1 GiB of prompt and a day of wall
# clock are both far past anything a review can use, and both are small enough
# that the arithmetic below can never wrap.
SWARM_MAX_PROMPT_BYTES_MAX=1073741824
SWARM_TIMEOUT_MAX=86400
# The headroom the skill subtracts for the per-cluster lens instruction plus
# Kimi's appended schema-output contract. A cap at or below it would make the
# oversize threshold zero or negative — i.e. EVERY
# prompt "too large" and every external voice dropped, silently. Defined here so
# the skill can read it rather than hard-code a second copy.
SWARM_CAP_HEADROOM=4096
# Grace between SIGTERM and SIGKILL for every bounded call.
TIMEOUT_KILL_GRACE=3
# How far past its nominal bound a bounded call may return, before the grace.
# `timeout` is precise; the wrapper-free watchdog measures with `SECONDS`, whose
# 1s granularity means it fires in (secs, secs+1] — deliberately never early.
# Budgeted rather than ignored: the difference between the advertised budget and
# the real worst case is exactly what lets the outer window win the race the
# margin exists to lose.
TIMEOUT_EXPIRY_SLOP=1

# Did this rc come from hitting the wall? `timeout` reports 124 when SIGTERM
# expired the command — but with `-k` a backend that IGNORES SIGTERM is SIGKILLed
# instead and the shell reports 137 (128+9). Both mean "hit the wall", and grok
# is documented right here as a CLI that ignores SIGTERM, so 137 is the EXPECTED
# code for exactly the voice this branch keeps timing out. Keying only on 124
# made that path print "check that the CLI offers model X" and record
# timed_out:false — sending the operator to inspect a model list over a wall hit,
# with the telemetry denying the timeout happened.
_is_timeout_rc() {
  # A wall must have been IN FORCE for either code to mean "we killed it", and
  # `_enforced_wall` is the single value that says so (set by _set_enforced_wall
  # in the main shell: a wrapper exists AND a non-zero cap is configured).
  # Re-deriving that predicate from _timeout_bin + _adapter_timeout here made a
  # second copy of it — and the 124 branch carried NO copy at all. A backend that
  # exits 124 on its own (any wrapper that calls `timeout` internally propagates
  # it) was then reported as `timed out after 0s` with timeout_seconds:0, which
  # telemetry-report renders as the self-contradictory "no adapter cap — the
  # outer window killed it" for a call whose EXIT trap demonstrably ran.
  # 137 is 128+SIGKILL — OUR `-k` escalation, but also an OOM kill or any outside
  # `kill -9`; the wall-in-force test is what keeps those from being called a
  # timeout, and it is now the same test for both codes.
  # ACCEPTED RESIDUAL: with a wall in force, a backend that exits 124 on its own
  # is still read as a timeout. It is not distinguishable — `timeout` reports the
  # same 124 for an expiry, and the watchdog is built to match it — so the choice
  # is which way to be wrong. Claiming the timeout is the safer direction: the
  # duration in the same telemetry record shows at a glance whether the call
  # actually reached the wall, whereas a real timeout reported as a plain failure
  # sends the operator to inspect a model list.
  [[ "${_enforced_wall:-0}" != "0" ]] || return 1
  (( $1 == 124 || $1 == 137 ))
}

# Resolved LAZILY, on first use. Resolving at top level meant a bad value in a
# shell profile (SWARM_TIMEOUT=600s) exited 2 from EVERY subcommand — `--help`,
# `jail`, `list --json`, `available` — none of which use the timeout at all. The
# old code validated on the `run` path only, so this was a silent behaviour
# regression: `/swarm:agents` printed nothing and the review blamed a variable
# the user never set. Verbs that DO use the value (`run`, `config`) still fail
# loudly, which is the part worth keeping.
_adapter_timeout=""
adapter_timeout() {
  [[ -n "$_adapter_timeout" ]] && return 0
  _adapter_timeout="$(_resolve_int SWARM_TIMEOUT "${SWARM_TIMEOUT:-}" 600 0 "$SWARM_TIMEOUT_MAX")" \
    || exit $?
}
_timeout_warned=""
# The wall actually ENFORCED on the backend call (0 = none). Distinct from the
# configured _adapter_timeout, which says nothing about whether a wrapper existed
# to apply it.
#
# SET BY THE CALLER, in the main shell — never inside with_timeout. Both backends
# invoke it as `raw="$(sandboxed …)"`, so an assignment there dies with the
# substitution and the EXIT trap (which runs in the PARENT) wrote
# timeout_seconds:0 for every grok call: telemetry-report then said "no adapter
# cap — the outer window killed it" while the adapter's own stderr said "timed out
# after Ns", and the near-wall warning could never fire for the one backend this
# branch exists to diagnose. Fourth instance of this class; see the note on
# _set_enforced_wall.
_enforced_wall=0
_set_enforced_wall() {
  # Decide it where the answer is known and durable. A wall is enforced iff a
  # non-zero cap is configured — the wrapper is no longer part of the question,
  # because with_timeout falls back to the polling watchdog instead of running
  # bare, so rc 124/137 are ours on every host. While it WAS part of the
  # question, a coreutils-less host recorded timeout_seconds:0 for calls that
  # were in fact uncapped, and the two facts were indistinguishable afterwards.
  if [[ -n "${_adapter_timeout:-}" && "${_adapter_timeout:-0}" != "0" ]]; then
    _enforced_wall="$_adapter_timeout"
  else
    _enforced_wall=0
  fi
}
with_timeout() {
  adapter_timeout
  local ADAPTER_TIMEOUT="$_adapter_timeout"
  if [[ "$ADAPTER_TIMEOUT" == "0" ]]; then "$@"; return; fi
  # `-k` is what actually ENFORCES the bound. Plain `timeout` only sends SIGTERM;
  # a backend that ignores it — grok is documented as doing exactly that a few
  # hundred lines down — or that forks a stdout-inheriting child keeps the
  # command substitution blocking past the deadline. The outer Bash window then
  # kills the whole adapter instead: rc is never 124, the EXIT trap never runs,
  # so there is no "timed out after Ns" line and no telemetry record. That lost
  # diagnosis is the thing this branch exists to prevent, and the probe path
  # already used -k for the same reason.
  # Same discovery the probes use — open-coding it here meant two `command -v`
  # sweeps per process and two places to keep in step.
  timeout_bin
  if [[ -z "$_timeout_bin" ]]; then
    # No coreutils timeout: bound it OURSELVES rather than running bare. The bare
    # branch made the documented cap silently inapplicable on stock macOS — the
    # common host, not an exotic one — so `_enforced_wall` stayed 0, rc 124/137
    # could never fire, cleanup()/_write_telemetry never ran, and the voice was
    # missing from the telemetry file entirely: indistinguishable from a voice
    # that never ran, which is the one reading telemetry-report must never make.
    # Meanwhile `config` still advertised timeout_seconds and the workflow shrank
    # its inner cap for a margin that bought nothing.
    # Same bound as the probes, one flavour up: stdin and stderr pass through
    # (codex reads its prompt on stdin; both stream diagnostics on stderr).
    if [[ -z "$_timeout_warned" ]]; then
      echo "warning: no timeout/gtimeout on PATH — external calls are bounded by the adapter's own polling watchdog instead (install coreutils for the cheaper wrapper; SWARM_TIMEOUT=0 disables the cap)" >&2
      _timeout_warned=1
    fi
  fi
  # Same dispatch as every probe — see _bounded_call.
  _bounded_call "$ADAPTER_TIMEOUT" "$@"
}

# OS-level read-deny jail for external CLI calls. All external voices may read
# project files (out-of-diff bugs), so the jail is the HARD boundary that bounds
# blast radius if an injection steers a read: common secret stores stay
# unreadable while the CLI's own config + non-secret project files remain
# readable (verified: ~/.aws blocked, ~/.codex readable). macOS: sandbox-exec;
# Linux: bwrap; else passthrough (scrub_secrets + backend flags remain).
# NOTE: the prompt-containment check does its canonicalizing compare in ONE
# python3 pass over the whole deny list (forking an interpreter per entry cost
# ~25 startups per grok call, inside the pre-timer budget). A per-path helper was
# what that replaced, so it is gone rather than kept as an unused second answer
# to the same question — the next reader would have had to work out which of the
# two the jail actually uses.

_assert_prompt_readable_in_jail() {
  # grok reads the prompt from a PATH, inside the jail. A deny entry covering that
  # path does not error under bwrap — it masks the file, which reads as EMPTY. The
  # backend then reviews nothing, returns schema-valid `{"findings":[]}` in
  # seconds, and the pipeline counts it as a family that reviewed and found
  # nothing. Prose ("never deny TMPDIR") cannot stop that; a check can.
  #
  # ONLY when a jail is actually in force: with neither sandbox-exec nor bwrap the
  # deny list is never applied to anything, so refusing there would drop the whole
  # grok family for masking that cannot occur — the same silent-family-loss this
  # guard exists to prevent, in the opposite direction.
  #
  # The caller always passes the backend explicitly; the fallback keeps a direct
  # call in a test from being silently backend-less.
  local backend="${2:-grok}"
  _jail_available "$backend" || return 0

  # ONE python3 for the whole comparison. Canonicalization matters (macOS $TMPDIR
  # is a symlink, and both jail builders realpath their entries, so a raw string
  # compare asks a different question than the jail answers) — but doing it per
  # entry forked an interpreter for each of ~25 deny paths on every grok call,
  # startup cost landing squarely in the pre-timer budget this branch spent three
  # rounds bounding. No associative array either: bash 3.2 (stock macOS) has none.
  local hit=""
  # No python3-less arm. `run` calls require_python3 before it dispatches, so this
  # function is only ever reached with python3 present — and the fallback that
  # used to sit here answered a DIFFERENT question (a raw prefix compare, no
  # realpath), so on the symlinked $TMPDIR that motivated the canonicalizing
  # compare it returned the opposite verdict. An unreachable second answer to a
  # safety question is worse than none: the next reader has to work out which one
  # applies. Guard loudly instead, so a future caller that skips require_python3
  # fails here rather than silently skipping the check.
  command -v python3 >/dev/null 2>&1 \
    || { echo "refusing to run: python3 is required to verify that the prompt file is readable inside the jail" >&2; exit 2; }
  hit="$(_sandbox_deny_paths "$backend" | python3 -c '
import os, sys
prompt = os.path.realpath(sys.argv[1])
for line in sys.stdin:
    d = line.rstrip("\n")
    if not d:
        continue
    rd = os.path.realpath(d)
    if prompt == rd or prompt.startswith(rd.rstrip("/") + "/"):
        print(d)
        break
' "$1" 2>/dev/null)" || {
    # FAIL CLOSED. `|| true` here turned any failure of the deny-list build or of
    # python into an EMPTY result, which the test below reads as "not denied" —
    # a safety check that answers "safe" when it could not answer at all. The
    # thing it guards against is invisible by construction (a masked prompt reads
    # as empty and the backend returns a schema-valid "no findings"), so the one
    # unacceptable outcome is passing without having checked.
    echo "refusing to run: could not verify that the prompt file is readable inside the jail (the deny-path check failed) — rerun with SWARM_DENY_PATHS unset to narrow it down" >&2
    exit 2
  }

  if [[ -n "$hit" ]]; then
    echo "refusing to run: the prompt file ($1) is inside a denied path ($hit) — inside the jail it would read as EMPTY and the backend would return 'no findings' from a call that never saw the diff. Move TMPDIR outside SWARM_DENY_PATHS." >&2
    exit 2
  fi
}

# The deny list is deterministic within a process (the repo root is memoized,
# the glob sweep only stats), so building it twice per `run grok` — once inside
# _init_sandbox, once for the prompt-containment check — is pure duplicate work:
# two extra `git` forks plus ~50 stats per external call, one call per gated
# cluster.
#
# Split into a VOID SETTER plus a PURE PRINTER rather than memoizing in place.
# Every caller invokes the list as `$( )` or in a process substitution, so an
# assignment inside the printer would die with that subshell and re-pay the work
# anyway — the print-and-cache class test_lens_sync.py now forbids outright.
# Fail-safe by construction: if the setter never ran in the main shell, the
# printer just rebuilds. The optimization can be lost; the answer cannot.
_DENY_PATHS_FOR=""
_DENY_PATHS_MEMO=""
_deny_paths_ensure() {
  local backend="${1:-}"
  [[ "$_DENY_PATHS_FOR" == "backend:$backend" ]] && return 0
  _DENY_PATHS_MEMO="$(_sandbox_deny_paths_build "$backend")"
  _DENY_PATHS_FOR="backend:$backend"
}
_sandbox_deny_paths() {
  local backend="${1:-}"
  if [[ "$_DENY_PATHS_FOR" == "backend:$backend" ]]; then
    printf '%s\n' "$_DENY_PATHS_MEMO"
    return 0
  fi
  _sandbox_deny_paths_build "$backend"
}

_sandbox_deny_paths_build() {
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
    "$HOME/.config/gh" "$HOME/.cargo/credentials" "$HOME/.cargo/credentials.toml" \
    "$HOME/.gitconfig" "$HOME/.config/git" "/etc/master.passwd" \
    "$HOME/.config/anthropic" "$HOME/.config/openai" "$HOME/.claude.json"
  if [[ "$own" != "codex" ]]; then printf '%s\n' "$HOME/.codex"; fi
  if [[ "$own" != "grok" ]]; then printf '%s\n' "$HOME/.grok"; fi
  if [[ "$own" != "kimi" ]]; then printf '%s\n' "$HOME/.kimi-code"; fi
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
  _repo_root_ensure; repo="$_REPO_ROOT_MEMO"
  if [[ -n "$repo" ]]; then
    # When the reviewed root is a LINKED WORKTREE, also deny the MAIN checkout's
    # root globs: untracked .env/data/ never propagate into a worktree, so in
    # the standard /kickoff layout the real secrets sit in the main checkout —
    # a plain readable sibling path without this (0.6.0 self-review, round 2).
    local roots=("$repo") common main
    # --git-common-dir can print a path RELATIVE to CWD, so run it with -C "$repo"
    # (→ relative to $repo, which we control) and anchor any relative result there.
    # Bash-only resolution (cd+pwd -P), NOT `--path-format=absolute` (git >= 2.31)
    # or a python realpath: this runs on the bwrap path too, which must not gain a
    # git-version floor or a python3 dep. `cd … && pwd -P` canonicalizes symlinks.
    common="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "$common" ]]; then
      [[ "$common" = /* ]] || common="$repo/$common"
      main="$(cd "$(dirname -- "$common")" 2>/dev/null && pwd -P || true)"
      if [[ -n "$main" && "$main" != "$repo" && -d "$main" ]]; then roots+=("$main"); fi
    fi
    # Key globs are the SSH id names (id_rsa*/id_ed25519*/…), NOT a bare id_* —
    # that would jail legit files (id_utils.py), and under bwrap a denied path
    # reads as silently EMPTY (tmpfs / /dev/null bind), not EPERM, feeding the
    # reviewers false "file is empty" evidence. .npmrc/.pypirc/credentials.json
    # mirror the HOME store list. NOTE: the repo's own .git/config is NOT denied
    # — git treats an EPERM on it as fatal (sandbox-exec), which would break the
    # externals' git-based exploration entirely; a repo-config-embedded token is
    # an accepted residual ([[swarm-backend-adapter]] § residual risk).
    local r p
    for r in "${roots[@]}"; do
      for p in "$r"/.env* "$r"/data "$r"/*.pem "$r"/id_rsa* "$r"/id_ed25519* \
               "$r"/id_ecdsa* "$r"/id_dsa* "$r"/*.key "$r"/.npmrc "$r"/.pypirc \
               "$r"/credentials.json; do
        [[ -e "$p" ]] || continue
        # Skip conventionally NON-secret .env templates: they are committed for
        # docs, and jailing them makes bwrap serve them as empty (no EPERM) —
        # feeding reviewers a false "config is empty" finding for a file that is
        # legitimately readable. Real secrets never use these names.
        case "${p##*/}" in
          .env.example|.env.sample|.env.template|.env.dist|.env.defaults) continue ;;
        esac
        printf '%s\n' "$p"
      done
    done
  fi
  local extra="${SWARM_DENY_PATHS:-}"
  # if-form, not `[[ … ]] && …`: the latter returns 1 when extra is empty, and
  # under set -e that aborts the `profile="$(…)"` assignment that calls this.
  # Trim each user-supplied entry HERE, once, so every consumer downstream can
  # take the bytes verbatim. They did not agree before: the sandbox-exec profile
  # builder and the containment preflight both `.strip()`, while the bwrap
  # builder reads with `IFS= read -r` and masks the untrimmed string. A deny
  # entry with a trailing space was therefore MASKED by bwrap but invisible to
  # the preflight — the prompt file would read as empty inside the jail and grok
  # would return schema-valid "no findings" from a call that saw no diff, which
  # is the exact silence that check exists to prevent.
  if [[ -n "$extra" ]]; then
    local e
    while IFS= read -r e; do
      e="${e#"${e%%[![:space:]]*}"}"   # leading whitespace
      e="${e%"${e##*[![:space:]]}"}"   # trailing whitespace
      [[ -n "$e" ]] && printf '%s\n' "$e"
    done <<< "${extra//:/$'\n'}"
  fi
  return 0
}

# Resolve the repo root for -C/--cwd scoping. Best-effort: empty when not in a
# git work tree (callers fall back to the ambient cwd).
_REPO_ROOT_DONE=""
_REPO_ROOT_MEMO=""
_repo_root_ensure() {
  # VOID setter — fills $_REPO_ROOT_MEMO, prints nothing. Callers read the
  # variable directly.
  #
  # It used to print its result too, and every caller invoked it as `$(_repo_root)`
  # — so the memo was written in a subshell that exited immediately and the `git
  # rev-parse` fork ran once per caller anyway. A function cannot both print and
  # cache; test_lens_sync.py now enforces that, and this is the case it found.
  [[ -n "$_REPO_ROOT_DONE" ]] && return 0
  _REPO_ROOT_DONE=1
  _REPO_ROOT_MEMO="$(git rev-parse --show-toplevel 2>/dev/null || true)"
}

_read_web_safe() {
  # $1 = backend. read+web is safe ONLY when BOTH hold: a working OS jail (the
  # hard boundary) AND a resolvable repo root. Without the root,
  # _sandbox_deny_paths emits NO repo-local denies and _scope_args drops
  # -C/--cwd — so the reviewed repo's own .env*/data/keys would be readable and,
  # with web on, exfiltratable. The HOME denylist alone keeps _jail_available
  # true, so that check is not enough on its own — hence the second condition.
  _jail_available "$1" || return 1
  _repo_root_ensure
  [[ -n "$_REPO_ROOT_MEMO" ]]
}

_scope_args() {
  # Shared "scope the working root to the repo, or warn" block for run_codex
  # (-C) and run_grok (--cwd) — one source of truth so the resolution + warning
  # can't drift between the two. $1 = the backend's working-root flag, $2 = a
  # label for the warning. Prints the two argv words ("<flag>\n<repo>") on
  # success; on failure warns to stderr and prints nothing (caller runs
  # unscoped, falling back to the ambient cwd).
  local flag="$1" who="$2" repo
  _repo_root_ensure; repo="$_REPO_ROOT_MEMO"
  if [[ -n "$repo" ]]; then
    printf '%s\n%s\n' "$flag" "$repo"
  else
    echo "warning: $who could not resolve repo root (git rev-parse) — running without $flag" >&2
  fi
}

_jail_available() {
  # $1 = backend. Builds the jail (memoized) and reports whether an OS sandbox
  # wrapper exists. All external run paths consult this to FAIL CLOSED: the
  # read+web posture is only safe under the OS secret-jail (the hard boundary).
  # Without one, codex/grok degrade to their 0.5.x restricted flags instead of
  # running read+web bare; Kimi refuses because ACP has no equivalent safe
  # no-read tier.
  _init_sandbox "${1:-}"
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
  # Built ONCE, in the main shell, so the containment check that follows reuses it
  # (see _deny_paths_ensure) — but only INSIDE the wrapper branches: on a host
  # with neither sandbox-exec nor bwrap the list is never applied to anything, and
  # building it there cost two git forks plus a ~50-path stat sweep per adapter
  # process for a value nothing reads.
  if command -v sandbox-exec >/dev/null; then
    _deny_paths_ensure "$backend"
    # Build the deny profile via python: realpath each path (defeats symlinks
    # like /tmp→/private/tmp, /etc→/private/etc — sandbox-exec matches the
    # resolved path) and deny it as BOTH a subpath (dirs + contents) and a
    # literal (single files like ~/.netrc).
    local profile
    profile="$(_sandbox_deny_paths "$backend" | python3 -c '
import os, sys
rules = []
for line in sys.stdin:
    p = line.rstrip("\n")
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
    _deny_paths_ensure "$backend"
    # --tmpfs masks a directory; a regular file (e.g. ~/.netrc) needs a bind of
    # an empty source instead — --tmpfs over a file dies with ENOTDIR.
    # REALPATH each path (readlink -f, resolving the final symlink component too):
    # bwrap masks the exact path given, so a symlinked secret (~/.gitconfig →
    # dotfiles/.gitconfig) would stay readable via its real path if only the link
    # name were masked. sandbox-exec realpaths in its profile builder; the bwrap
    # path must match, or Linux under-denies. Mask BOTH the link name and the
    # resolved target so neither is a bypass.
    local args=(--dev-bind / /) p rp q targets
    while IFS= read -r p; do
      rp="$(readlink -f -- "$p" 2>/dev/null || printf '%s' "$p")"
      # Mask the link name, plus its resolved target when it differs (a symlink).
      # Array (not unquoted $(…)) so a path with spaces stays one word.
      targets=("$p"); [[ "$rp" != "$p" ]] && targets+=("$rp")
      for q in "${targets[@]}"; do
        if [[ -d "$q" ]]; then args+=(--tmpfs "$q")
        elif [[ -e "$q" ]]; then args+=(--ro-bind /dev/null "$q")
        fi
      done
    done < <(_sandbox_deny_paths "$backend")
    SANDBOX_CMD=(bwrap "${args[@]}")
  fi
  # Probe that the wrapper actually WORKS, not merely exists on PATH: a
  # container can ship a bwrap that cannot create namespaces, and a broken
  # sandbox-exec would otherwise fail every review run with an opaque backend
  # error instead of taking the callers' fail-closed degrade path. On probe
  # failure treat the host as jail-less (audibly) — _jail_available then
  # reports false and the external run paths fail closed.
  if ((${#SANDBOX_CMD[@]} > 0)); then
    # BOUNDED: this is pre-timer work the timeout margin does not measure, and a
    # bwrap that stalls creating a user namespace blocks here for as long as the
    # kernel takes — wall clock the workflow already believes it has spent. The
    # 14s slack the workflow reserves for jail construction covers a bounded
    # probe; it cannot cover an unbounded one.
    _probe_setup_lenient
    if ! _probe_or_bare "${SANDBOX_CMD[@]}" true >/dev/null 2>&1; then
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
  # GIT_CONFIG_GLOBAL/SYSTEM=/dev/null: the denylist now jails ~/.gitconfig /
  # ~/.config/git (a PAT can live there via url.insteadOf / http.extraHeader),
  # but git treats an EPERM on a config it reads as FATAL — so point git at
  # /dev/null for global+system config and it never opens the denied paths,
  # keeping the externals' git-based exploration working while the files stay
  # unreadable to a direct read_file. (repo .git/config is left readable — git
  # needs it; see _sandbox_deny_paths.)
  # order: timeout → env (strip secrets, redirect git config) → sandbox-exec (jail) → backend.
  # env options (-u …) MUST precede the NAME=VALUE assignments, or env treats the
  # first `-u` after an assignment as the command.
  with_timeout env ${env_args[@]+"${env_args[@]}"} \
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
    ${SANDBOX_CMD[@]+"${SANDBOX_CMD[@]}"} "$@"
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
    claude|codex|grok|kimi) ;;
    *) echo "Unknown backend: $1 (expected claude|codex|grok|kimi)" >&2; exit 2 ;;
  esac
}

# ---------- probes ----------

backend_installed() {
  local backend="$1" executable="$1"
  [[ "$backend" == "claude" ]] && return 0
  [[ "$backend" == "kimi" ]] && executable="$KIMI_BIN"
  command -v "$executable" >/dev/null
}

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
    # BOUNDED like every other probe. It is only a nicer version string — the
    # backend exists by definition in-session — but `list --json` calls this
    # FIRST, from the review skill-s own prep block, so a `claude` wedged on a
    # hung mount or a blocked IPC socket stalled the whole review before any
    # voice started: the unbounded-probe hang this branch exists to remove,
    # surviving in the one branch that returned early before reaching the bound.
    local cver
    _probe_setup_lenient
    cver="$(_probe_or_bare claude --version | head -1 || true)"
    echo "${cver:-in-session}"
    return 0
  fi
  local executable="$backend"
  [[ "$backend" == "kimi" ]] && executable="$KIMI_BIN"
  backend_installed "$backend" || return 1
  # BOUNDED like every other pre-timer call. `<backend> --version` looks trivial
  # but runs before TELEMETRY_START on the `run` path, and a CLI wedged on a stale
  # leader socket or a hung PATH entry blocks here forever — wall clock the
  # workflow's margin believes it has already accounted for. Unbounded, it lets
  # the OUTER Bash window kill the adapter before the inner cap fires: no rc=124,
  # no EXIT trap, no telemetry record.
  # Best-effort output: once `command -v` confirmed the CLI, a non-zero exit, a
  # timeout, or a SIGPIPE from head() must NOT flip an installed backend to
  # "unavailable".
  # Bounded WHEN POSSIBLE, unbounded otherwise — the same contract with_timeout
  # documents a few hundred lines up ("run bare, but say so once"). Denying the
  # version instead would blank the VERSION column on any host without coreutils
  # (stock macOS), and the bounded call cannot make that choice itself: it runs
  # inside $(...) where it can neither warn once nor set a flag.
  _probe_setup_lenient
  _probe_or_bare "$executable" --version 2>/dev/null | head -1 || true
  return 0
}

# Wall-clock bound for the readiness probe. Deliberately NOT SWARM_TIMEOUT: that
# caps a full review (600s default) and may be disabled with 0, neither of which
# suits a probe that `list`/`ready` block on. Override with SWARM_PROBE_TIMEOUT. A
# malformed value, 0, or anything above the ceiling is REFUSED (exit 2) rather
# than quietly normalized: the old silent fallback meant an operator who set 300
# got 10 and never learned the knob had no effect. `run` and `config` fail loudly
# on a bad value, and since the skill runs `config` first that surfaces as one
# clear SWARM_CFG_ERR instead of N per-call failures.
# Bounded at 20s deliberately. The workflow sizes its timeout margin from the
# assumption that pre-timer probe work fits inside it; an unbounded value here
# (SWARM_PROBE_TIMEOUT=300) would push two probes past that margin and let the
# OUTER window kill the call before the inner cap fires — losing rc=124 and the
# telemetry record, which is the diagnosis this whole branch exists to keep.
# The workflow no longer mirrors this by hand: `config` reports
# probe_budget_seconds and swarm-review.js derives its margin from that. Raising
# this ceiling still widens the budget, so raise it deliberately.
SWARM_PROBE_TIMEOUT_MAX=20
# Lazy for the same reason as adapter_timeout(): `--help` and `list` must not die
# on a knob they never read.
_probe_timeout=""
probe_timeout() {
  [[ -n "$_probe_timeout" ]] && return 0
  _probe_timeout="$(_resolve_int SWARM_PROBE_TIMEOUT "${SWARM_PROBE_TIMEOUT:-}" 10 1 "$SWARM_PROBE_TIMEOUT_MAX")" \
    || exit $?
}
# How many bounded probes a single `run` may perform before the timed backend
# call starts. Every one is memoized per process, so this is the worst case for
# the heaviest backend (grok): `grok models`, `grok --help`, and the sandbox
# smoke test (`<wrapper> true`, bounded since 0.10.13). codex needs two
# (`login status`, the same sandbox test).
# `--version` is NOT among them: the `run` gate asks `command -v`, and the probe
# that prints a version string only runs where that string is shown.
# CHANGE THIS WHENEVER A PRE-TIMER PROBE IS ADDED. The workflow reads the derived
# budget instead of re-deriving it, so this constant is the single place the
# arithmetic lives — but it only stays honest if a new probe is counted here. An
# uncounted probe is invisible to the margin, which is exactly how 0.10.0 overran
# it.
SWARM_MAX_PROBES_PER_RUN=3

# grok's model list, memoized for the process (`grok models` is a network call —
# fetch at most once).
_grok_models_done=""
_grok_models=""
_codex_probe_rc=0
_probe_degraded() {
  # The ONE exit for every "the model check did not happen" route (no timeout
  # binary, probe failed, probe timed out, unparseable list). Each ends in the
  # same trust-auth degrade, so each must be equally audible: the docs promise
  # the check falls back "never silently", and a promise that holds on only some
  # routes is the runtime-lie this branch removes.
  echo "warning: grok model probe unavailable ($1) — readiness falls back to auth alone; the schema-verified-model check did not run" >&2
}
# Which timeout wrapper exists, resolved once. Availability is a property of the
# HOST, so asking it through a probe's exit code was always a category error: 126
# is also what `timeout` returns for "command found but cannot be invoked", so a
# grok wrapper that execs a non-executable helper looked exactly like a missing
# coreutils — and the adapter then trusted auth and skipped the model check. 127
# (the previous sentinel) collides with "command not found" the same way. No exit
# code is free, so the question moves out of band.
# A probe timeout for the DISPLAY paths (`list`, `available`, `ready`), which must
# never die on a knob they only pass through. `run` and `config` still refuse a bad
# value — they apply it — but a malformed SWARM_PROBE_TIMEOUT in a shell profile
# must not make `list --json` report an installed, authenticated CLI as "not
# installed", which is exactly what the strict resolver did from inside
# available_version's command substitution (the exit died there and was read as
# "absent"). Degrade loudly, once.
_probe_timeout_warned=""
probe_timeout_lenient() {
  [[ -n "$_probe_timeout" ]] && return 0
  local v
  if v="$(_resolve_int SWARM_PROBE_TIMEOUT "${SWARM_PROBE_TIMEOUT:-}" 10 1 "$SWARM_PROBE_TIMEOUT_MAX" 2>/dev/null)"; then
    _probe_timeout="$v"
    return 0
  fi
  _probe_timeout=10
  if [[ -z "$_probe_timeout_warned" ]]; then
    _probe_timeout_warned=1
    echo "warning: ignoring invalid SWARM_PROBE_TIMEOUT='${SWARM_PROBE_TIMEOUT:-}' for this listing — probing with ${_probe_timeout}s (\`run\` and \`config\` still refuse it)" >&2
  fi
}

# The two preludes every _probe_or_bare caller must run FIRST, in the main shell.
# Named because the pair was duplicated at four call sites and enforced only by a
# comment — and a missed call silently disables bounding (the memo and any exit
# die inside the $(...) that invokes the probe).
_probe_setup_strict()  { timeout_bin; probe_timeout; frac_sleep_probe; }
_probe_setup_lenient() { timeout_bin; probe_timeout_lenient; frac_sleep_probe; }

# Does `sleep` accept a fraction? POSIX only guarantees integers, so it has to be
# tried — and trying it costs the 100ms it sleeps. A HOST property, resolved once
# here in the main shell like $_timeout_bin: _bounded_bg runs inside `$( )` on the
# grok path, so a memo written there dies with the subshell and every probe re-paid
# the 100ms out of the pre-timer budget it exists to keep honest.
# Unset (a direct call that skipped the prelude) reads as 0 — whole-second polling,
# correct but coarse. Never wrong, only slower.
_frac_sleep_done=""
_frac_sleep=0
frac_sleep_probe() {
  [[ -n "$_frac_sleep_done" ]] && return 0
  _frac_sleep_done=1
  sleep 0.1 2>/dev/null && _frac_sleep=1
  return 0
}

_timeout_bin_done=""
_timeout_bin=""
timeout_bin() {
  [[ -n "$_timeout_bin_done" ]] && return 0
  _timeout_bin_done=1
  if command -v timeout >/dev/null; then _timeout_bin="timeout"
  elif command -v gtimeout >/dev/null; then _timeout_bin="gtimeout"
  else _timeout_bin=""
  fi
}
# Bound a command on a host with NO coreutils wrapper (stock macOS is the common
# case, not the exotic one). Runs it in the background, polls, and escalates
# SIGTERM -> SIGKILL, reporting timeout(1)-s own codes: 124 when the deadline
# expired, 137 when the escalation had to kill it, 126 when it could not be
# bounded at all.
#
# It exists because "run bare when no wrapper exists" was not a bound: on that
# host a wedged CLI (captive portal, stale leader socket, blocked FS) ran without
# any deadline while `config` advertised a budget the run could not enforce — the
# workflow shrank its inner cap for margin nothing could spend, and the outer
# Bash window, not the adapter, did the killing: no rc, no message, no telemetry
# record. That lost diagnosis is what this whole branch exists to prevent.
#
# stdout is captured and replayed; stdin and stderr are whatever the CALLER
# redirects — probes close stdin and discard stderr at their own call site
# (_probe_or_bare), while the backend call inherits both (it streams the CLI-s
# error output, and codex reads its prompt on stdin).
_bounded_bg() {
  local secs="$1"; shift
  local out rc=0 pid t0 monitor=""
  out="$(mktemp)" || out=""
  if [[ -z "$out" ]]; then
    # FAIL CLOSED. The first version fell through to an unbounded run here, which
    # voided the bound on exactly the hosts this function exists for: a full or
    # read-only $TMPDIR turned "bounded everywhere" back into "hangs forever",
    # and the caller could not tell. 126 is the adapter-s established
    # "could not bound this" sentinel (127 collides with command-not-found), and
    # run_codex/run_grok name it apart from a backend failure.
    echo "warning: could not create a scratch file to bound \`$1\` — refusing to run it unbounded" >&2
    return 126
  fi
  # Registered with the EXIT trap so a kill mid-flight does not orphan it. Honest
  # about the limit: the grok path runs this inside `raw="$(sandboxed …)"`, and a
  # command substitution gets its own subshell with default traps — so there the
  # assignment protects nothing and the inline `rm -f` on every return path is
  # the whole cleanup. The codex path runs in the main shell, where it works.
  TMP_BOUNDED="$out"
  # Monitor mode for the launch ONLY, so the child becomes its own process-group
  # leader and the expiry below can signal the GROUP. Without it we signal the
  # direct child alone, while `timeout` (which this path claims to be
  # indistinguishable from) signals the group: codex and grok are node CLIs that
  # spawn helpers, so a wedged call left its children reparented to init, still
  # holding sockets and burning API budget after the adapter already reported a
  # timeout — and across a `--loop` run those orphans accumulate.
  case "$-" in *m*) monitor=1 ;; esac
  set -m
  # `<&0` is NOT redundant. POSIX assigns an asynchronous command-s stdin to
  # /dev/null before any explicit redirection, so a bare `"$@" &` silently starves
  # the child of stdin — and codex reads its whole prompt there (`-- - <file`).
  # That would have sent every codex voice an EMPTY prompt on any host without
  # coreutils: a schema-valid "no findings" answer from a call that saw no diff,
  # counted as a family that reviewed. Duplicating fd 0 explicitly overrides the
  # implicit /dev/null; the probe flavour points fd 0 at /dev/null itself, so it
  # still gets exactly what it asks for.
  "$@" >"$out" <&0 &
  pid=$!
  [[ -n "$monitor" ]] || set +m
  # Measure the WALL, do not count ticks. Accumulating a fixed 100ms per iteration
  # ignored the fork/exec of `sleep` plus the loop-s own work, so every iteration
  # cost MORE than it counted and the enforced cap drifted systematically long —
  # on a 547s wall a few percent of drift is tens of seconds, i.e. straight past
  # the 600s outer window the whole margin exists to stay inside.
  # `SECONDS` is the clock that costs no fork (`date` at 10 Hz for nine minutes
  # would not be free) and exists on bash 3.2. It has 1s granularity, so compare
  # STRICTLY GREATER: the first tick can arrive milliseconds after the launch, and
  # `>=` would expire a 1s probe almost immediately. This fires between secs and
  # secs+1 — never early, at most a second late.
  # Resolved once per process by frac_sleep_probe (see it for why not here), and
  # only USED for a short bound: fine granularity exists for the ~40ms readiness
  # probes, while a bound of minutes is watched by a test with 1s granularity
  # anyway.
  local frac=0
  if (( secs <= 20 )); then frac="${_frac_sleep:-0}"; fi
  t0=$SECONDS
  local elapsed=0
  while kill -0 "$pid" 2>/dev/null; do
    elapsed=$(( SECONDS - t0 ))
    if (( elapsed > secs )); then
      # Signal the GROUP (negated pgid), falling back to the child if the group
      # does not exist — then escalate, exactly like `timeout -k`.
      # Wrapped in a stderr-silenced block: with monitor mode on for the launch,
      # bash prints its own "Terminated: 15" job notice when it reaps the job,
      # which would land in the adapter-s stderr and be reported as backend output.
      { kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        # POLL the grace, do not sleep it out. An unconditional
        # `sleep $TIMEOUT_KILL_GRACE` charged every timeout the full grace even
        # when the backend died on the first SIGTERM — the common case — so the
        # measured wall of a bounded call ran systematically over its own budget
        # (SWARM_TIMEOUT=3 returned after 7s, and telemetry-report then printed
        # percentages above 100).
        local ticks=0 max_ticks="$TIMEOUT_KILL_GRACE"
        (( frac )) && max_ticks=$(( TIMEOUT_KILL_GRACE * 10 ))
        while (( ticks < max_ticks )) && kill -0 "$pid" 2>/dev/null; do
          if (( frac )); then sleep 0.1; else sleep 1; fi
          ticks=$(( ticks + 1 ))
        done
        if kill -0 "$pid" 2>/dev/null; then
          kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
          wait "$pid" 2>/dev/null || true
          rc=137
        else
          wait "$pid" 2>/dev/null || true
          rc=124
        fi
      } 2>/dev/null
      cat "$out"; rm -f "$out"; TMP_BOUNDED=""
      return "$rc"
    fi
    # Adaptive: 100ms for the first few seconds, then whole seconds. The fine
    # step exists for the ~40ms readiness probes (rounding those up to a second
    # is paid per probe, per backend, per gated cluster); the backend call runs
    # for minutes, and polling it at 10 Hz forked /bin/sleep ~5,500 times per
    # call — bash 3.2 has no sleep builtin — to watch a deadline whose own test
    # has 1-second granularity anyway.
    if (( elapsed < 3 && frac )); then sleep 0.1; else sleep 1; fi
  done
  # `|| rc=$?` and not a bare `wait`: under `set -e` a non-zero rc would exit the
  # whole adapter here instead of reaching the caller that has to interpret it.
  wait "$pid" || rc=$?
  cat "$out"
  rm -f "$out"; TMP_BOUNDED=""
  return "$rc"
}

# THE dispatch: wrapper if one exists, polling watchdog otherwise. One place, so
# the grace, the flags and the rc semantics cannot drift between "how a probe is
# bounded" and "how the backend call is bounded" — the two used to open the same
# if/else independently, and every "these two must agree" split on this branch
# has cost a round. The CALLER supplies redirections; this only decides HOW.
_bounded_call() {
  local secs="$1"; shift
  if [[ -n "$_timeout_bin" ]]; then
    "$_timeout_bin" -k "$TIMEOUT_KILL_GRACE" "$secs" "$@"
  else
    _bounded_bg "$secs" "$@"
  fi
}

# The PROBE entry point — call this, never _bounded_call directly. Bounded either
# way (wrapper or watchdog), stdin closed and stderr discarded: a probe that reads
# stdin would block forever on an interactive shell (a probe that must never hang,
# hanging), and probe noise must not reach the adapter-s own stderr.
# Callers MUST run _probe_setup_strict or _probe_setup_lenient in the main shell
# first: this is invoked as $(...), so anything resolved here dies with the
# subshell and any exit is swallowed by the substitution.
_probe_or_bare() {
  _bounded_call "${_probe_timeout:-10}" "$@" </dev/null 2>/dev/null
}


# The parser as its OWN function, reading the raw listing on stdin. It used to be
# inlined in the assignment below, which forced both test files to SCRAPE it back
# out of this file with a regex; the two anchors then differed in strictness, so
# a reformat could silently disable one whole test file (20+ format regressions)
# while the other stayed green. Its own function is drivable directly — see
# test_grok_models.py, which sources this file and pipes fixtures through it.
grok_parse_models() {
  awk '
    # The id must be the FIRST token after the bullet, matched whole, on a short
    # line. Scanning every field meant a prose bullet — " - grok-4.6 reaches end
    # of life on 2026-12-01" — yielded grok-4.6 as an offered model; discovery
    # then selects it (it is schema-verified) and every call dies at launch with
    # "unknown model id", losing the grok family. The actual rule is below: the
    # id alone, or the id followed by a BRACKETED annotation. (It was NF<=3 once;
    # saying so here while the code checks something else is how a reader ends up
    # debugging the comment.)
    /^[[:space:]]*[*-][[:space:]]/ {
      # Accept: bullet, then the id as the FIRST token, then EITHER nothing or a
      # BRACKETED annotation — "(default)", "[stable]", "{beta}". Surrounding
      # backticks/quotes and trailing punctuation are stripped off the id.
      #
      # This is a deliberate trade-off between two ways to lose the grok family,
      # and it is not symmetric:
      #   - harvesting PROSE as a model ("- grok-4.6 reaches end of life on …")
      #     makes discovery select an id the CLI does not offer; every call then
      #     dies at launch and nothing says why. SILENT.
      #   - rejecting an unfamiliar annotation style ("- grok-4.5 Fast reasoning
      #     model") empties the list, which lands in the documented trust-auth
      #     degrade: _probe_degraded WARNS on stderr and the pinned fallback is
      #     used. LOUD, and recoverable by adding the style here.
      # So when the two cannot be told apart syntactically — and a bare word after
      # the id cannot be — prefer the loud failure. Bracketed annotations are the
      # convention every observed listing uses, which is why they are admitted.
      # The `-` bullet itself must stay accepted: grok 1.0.3 marks only the
      # default with `*`.
      # An annotation can also state the model is NOT usable — "[retired]",
      # "(deprecated)", "- grok-4.6 [coming soon]". The bracket rule alone
      # admits those, discovery then SELECTS the id (it is canonical and
      # schema-verified, and the membership guard runs only for an explicit
      # --model override), and every call dies at launch with "unknown model id":
      # the silent family loss this parser exists to prevent, caused by the
      # parser. Reading the annotation is the only way to tell an offered model
      # from a listed-but-withdrawn one, so a withdrawal vocabulary is checked
      # here. An unknown wording still falls through to the loud degrade.
      low = tolower($0)
      if (low ~ /(retired|deprecated|unavailable|not available|sunset|end of life|end-of-life|coming soon|disabled|removed)/) next
      if (NF >= 2 && (NF == 2 || $3 ~ /^[(\[{]/)) {
        tok = $2
        # NOTE: no apostrophe may appear anywhere in this awk program (comments
        # included) — the whole program is wrapped in awk with single quotes, so
        # one would end the shell quoting and silently corrupt the parser. It did:
        # three fixtures went red and the live probe reported "output format may
        # have changed".
        gsub(/[`"]/, "", tok)
        sub(/[.,;:]+$/, "", tok)
        if (tok ~ /^grok-[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$/) print tok
      }
    }
'
}

# Kimi readiness has two independently bounded capability checks: ACP support
# (the only argv-safe prompt transport) and the offered model catalog. Both are
# memoized because `list` asks ready_check and ready_hint in the same process.
_kimi_acp_done=""
_kimi_acp_supported=""
_kimi_models_done=""
_kimi_models_known=""
_kimi_models=""
_kimi_probe_degraded() {
  echo "warning: kimi probe unavailable ($1) — readiness falls back to authenticated install; the ACP/model check did not run" >&2
}
_kimi_has_acp() {
  if [[ -n "$_kimi_acp_done" ]]; then
    [[ "$_kimi_acp_supported" == "yes" ]]
    return
  fi
  _kimi_acp_done=1
  local help="" rc=0
  help="$(_probe_or_bare "$KIMI_BIN" acp --help)" || rc=$?
  if (( rc == 0 )); then
    _kimi_acp_supported=yes
    if [[ "$help" != *"Agent Client Protocol"* || "$help" != *"stdio"* ]]; then
      _kimi_probe_degraded "\`kimi acp --help\` returned unrecognized help text"
    fi
  elif (( rc == 2 || rc == 127 )); then
    # A normal CLI usage/not-found result is a clean negative: the installed
    # binary cannot enter ACP. Timeouts and infrastructure failures remain
    # inconclusive and degrade to trusting the authenticated install.
    _kimi_acp_supported=no
  else
    _kimi_probe_degraded "\`kimi acp --help\` did not complete (rc=$rc)"
    _kimi_acp_supported=yes
  fi
  [[ "$_kimi_acp_supported" == "yes" ]]
}

kimi_model_fetch() {
  [[ -n "$_kimi_models_done" ]] && return 0
  _kimi_models_done=1
  local raw="" rc=0 parsed=""
  raw="$(_probe_or_bare "$KIMI_BIN" provider list --json)" || rc=$?
  if (( rc != 0 )); then
    _kimi_probe_degraded "\`kimi provider list --json\` did not complete (rc=$rc)"
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    _kimi_probe_degraded "python3 is unavailable to parse \`kimi provider list --json\`"
    return 0
  fi
  parsed="$(printf '%s' "$raw" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
models = data.get("models") if isinstance(data, dict) else None
if not isinstance(models, dict):
    raise SystemExit(1)
for model in models:
    if isinstance(model, str) and model:
        print(model)
')" || rc=$?
  if (( rc != 0 )); then
    _kimi_probe_degraded "\`kimi provider list --json\` returned an unrecognized document"
    return 0
  fi
  _kimi_models_known=yes
  _kimi_models="$parsed"
}

kimi_model_offered() {
  local model="$1"
  kimi_model_fetch
  # Unknown means probe-degraded, not definitely absent: trust auth and let the
  # ACP session report a precise model-catalog error if the run reaches it.
  [[ "$_kimi_models_known" != "yes" ]] && return 0
  case $'\n'"$_kimi_models"$'\n' in
    *$'\n'"$model"$'\n'*) return 0 ;;
    *) return 1 ;;
  esac
}

grok_model_fetch() {
  # Populates the cache globals. Call it DIRECTLY — never as `$(grok_model_fetch)`:
  # a command substitution runs it in a subshell, the assignments die with that
  # subshell, and every caller silently re-pays the network call.
  [[ -n "$_grok_models_done" ]] && return 0
  _grok_models_done=1

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
  # Resolve BOTH knobs in this shell, before the substitution below. probe_timeout
  # memoizes into a global and _resolve_int exits on a bad value — inside $(...)
  # the memoization dies with the subshell (the next caller re-resolves, and the
  # message below printed an empty "after s") and the exit never leaves it, so a
  # malformed SWARM_PROBE_TIMEOUT degraded both probes and still reported ready.
  _probe_setup_lenient
  # `_probe_or_bare`, and no coreutils special case: with
  # the polling watchdog the call is bounded on every host, so skipping it there
  # is no longer the cautious choice — it is the dangerous one. Skipping left the
  # model list EMPTY, which reads as the trust-auth degrade: readiness passes,
  # grok_select_model falls back to GROK_DEFAULT_MODEL, and if the CLI has
  # withdrawn that id every gated cluster dies at launch with "unknown model" —
  # the whole grok family gone, where one clean not-ready would have been the
  # honest answer. The reason the skip existed (`ready`/`list` must never hang)
  # is now satisfied by the bound itself.
  raw="$(_probe_or_bare grok models)" || rc=$?
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
    if (( rc == 124 )); then _probe_degraded "\`grok models\` timed out after ${_probe_timeout}s"
    elif (( rc == 137 )); then _probe_degraded "\`grok models\` was killed (SIGKILL — likely the ${_probe_timeout}s \`-k\` bound)"
    elif (( rc == 126 )); then _probe_degraded "\`grok models\` could not be bounded (no scratch file) — refused rather than run uncapped"
    else _probe_degraded "\`grok models\` failed (rc=$rc)"
    fi
    return 0
  fi
  # One model id PER BULLET LINE: the id is the FIRST grok-shaped token after the
  # bullet marker. Take only the first — scanning the whole line would also pick
  # up a grok-4.5 mentioned in trailing PROSE on another model's line
  # ("* grok-5 (successor to grok-4.5)"), reporting a retired model as still
  # offered. Match the id SUBSTRING, not the raw field, so glued-on punctuation
  # ("grok-4.5," / "grok-4.5." / backticks) doesn't ride along and break the
  # exact-match below; the pattern ends on alphanumerics, so a trailing separator
  # is never captured. No id-shaped token → empty → degrade.
  #
  # ACCEPT BOTH BULLET MARKERS. Up to grok 0.2.x every listed model carried `*`;
  # 1.0.3 marks only the DEFAULT with `*` and lists the rest with `-`:
  #     * grok-4.6 (default)
  #     - grok-4.5
  # A `*`-only matcher therefore saw a list that did not contain the pinned
  # grok-4.5 and reported "this CLI does not offer grok-4.5", dropping grok from
  # EVERY review — the third model family silently gone, which is precisely the
  # failure this plugin's timeout work exists to make impossible. Anchoring on
  # the marker at all is what makes this brittle; accepting both is the minimal
  # fix that keeps the anti-prose guard (a bullet line per model) intact.
  # The rules themselves live in grok_parse_models, not here.
  _grok_models="$(printf '%s\n' "$raw" | grok_parse_models)"
  if [[ -z "$_grok_models" ]]; then
    _probe_degraded "\`grok models\` returned no model ids — output format may have changed"
  fi
}

_line_in_list() {
  # Is line $1 present in the newline-separated list $2? Newline-fenced substring
  # match, not `grep -q`: an early-exiting grep can SIGPIPE the writer and
  # pipefail would then report failure even on a hit. Named once because the
  # idiom is subtle enough that a re-typed copy is where the next bug goes.
  case $'\n'"$2"$'\n' in
    *$'\n'"$1"$'\n'*) return 0 ;;
    *) return 1 ;;
  esac
}

_grok_schema_verified() { _line_in_list "$1" "$GROK_SCHEMA_VERIFIED"; }

_grok_version_newer() {
  # Is $1 strictly newer than $2? Both are canonical ids sharing the `grok-`
  # prefix, which is all GROK_CANONICAL_RE lets through.
  #
  # COMPONENT-WISE and numeric, so grok-4.20 is newer than grok-4.6 — read as a
  # decimal fraction it would be older, but the provider means "the 20th minor
  # release", and the live catalog already ships 4.20-derived ids.
  local a="${1##*-}" b="${2##*-}"
  local a_major="${a%%.*}" b_major="${b%%.*}"
  local a_minor="0" b_minor="0"
  case "$a" in *.*) a_minor="${a#*.}" ;; esac
  case "$b" in *.*) b_minor="${b#*.}" ;; esac
  # A non-numeric component would make `-gt` a hard `set -e` failure rather than
  # a false, so refuse the comparison — the caller reads that as "not newer" and
  # keeps what it had.
  case "$a_major$a_minor$b_major$b_minor" in *[!0-9]*) return 1 ;; esac
  # Digits alone are not enough: "08"/"09" are digit-only yet invalid octal, and
  # the comparisons below would abort the whole adapter under `set -e` ("value
  # too great for base") instead of answering "not newer". Force decimal.
  a_major=$((10#$a_major)); a_minor=$((10#$a_minor))
  b_major=$((10#$b_major)); b_minor=$((10#$b_minor))
  if [[ "$a_major" -ne "$b_major" ]]; then
    [[ "$a_major" -gt "$b_major" ]]
    return
  fi
  [[ "$a_minor" -gt "$b_minor" ]]
}

_grok_highest_canonical() {
  # Highest listed id accepted by GROK_CANONICAL_RE, or "" if none is.
  # $1 = "verified" restricts the scan to schema-verified models.
  #
  # NOT memoized, deliberately. Every call site invokes this as `$(...)`, so any
  # global it set would die with that subshell — the memo added in 0.10.6 never
  # reached a second caller and only looked like an optimization. A function that
  # PRINTS its result cannot also cache into a global; splitting it into a void
  # setter plus a getter would buy a few forks over an already-memoized in-memory
  # list, which is not worth a second calling convention in this file.
  local mode="${1:-any}" best="" id
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    [[ "$id" =~ $GROK_CANONICAL_RE ]] || continue
    if [[ "$mode" == "verified" ]] && ! _grok_schema_verified "$id"; then continue; fi
    if [[ -z "$best" ]] || _grok_version_newer "$id" "$best"; then best="$id"; fi
  done <<<"$_grok_models"
  printf '%s' "$best"
}

GROK_SELECTED_MODEL=""
GROK_SELECT_NOTE=""
grok_select_model() {
  # Resolve the model to run: an explicit --model wins, else the newest
  # schema-verified canonical id the CLI lists, else the pin. Sets
  # GROK_SELECTED_MODEL and, when the user should know something,
  # GROK_SELECT_NOTE. Memoized via GROK_SELECTED_MODEL — grok_model_fetch is a
  # network call.
  local override="${1:-}"
  [[ -n "$GROK_SELECTED_MODEL" ]] && return 0
  if [[ -n "$override" ]]; then
    # An override bypasses DISCOVERY but NOT the schema gate: running an
    # unverified model is the "fails late with structuredOutput: null after
    # burning a full review" case the gate exists to prevent.
    GROK_SELECTED_MODEL="$override"
    return 0
  fi
  grok_model_fetch
  if [[ -z "$_grok_models" ]]; then
    # No usable list (offline, no timeout binary, format changed). Keep the pin
    # rather than fail: grok_model_fetch already reported the degrade, and
    # dropping grok entirely is worse than running the known-good model.
    GROK_SELECTED_MODEL="$GROK_DEFAULT_MODEL"
    return 0
  fi
  local top verified
  top="$(_grok_highest_canonical)"
  verified="$(_grok_highest_canonical verified)"
  if [[ -z "$verified" ]]; then
    # The CLI lists canonical models but none we have verified. Keep the pin and
    # say so — run_grok's own preflight decides whether that is fatal.
    GROK_SELECTED_MODEL="$GROK_DEFAULT_MODEL"
    [[ -n "$top" ]] && GROK_SELECT_NOTE="grok lists $top but no schema-verified model — keeping $GROK_DEFAULT_MODEL"
    return 0
  fi
  GROK_SELECTED_MODEL="$verified"
  # A newer canonical model exists that we have NOT verified: report it, never
  # select it. This is the upgrade prompt — confirm schema enforcement by hand,
  # then add one line to GROK_SCHEMA_VERIFIED.
  if [[ -n "$top" && "$top" != "$verified" ]] && _grok_version_newer "$top" "$verified"; then
    GROK_SELECT_NOTE="grok offers a newer model ($top) that is not schema-verified — using $verified; verify --json-schema on $top, then add it to GROK_SCHEMA_VERIFIED"
  fi
  return 0
}

grok_model_offered() {
  # Three-state, collapsed to an exit code: 0 = the CLI offers a schema-verified
  # canonical model, 1 = it lists models but none we can use (an honest "gone"),
  # 0 = the list is empty / unparseable / not probed (probe unusable — offline,
  # no timeout binary, or a future CLI renaming the subcommand). The empty case
  # deliberately trusts auth instead of failing closed: silently dropping grok
  # from every fan-out is worse than letting run_grok surface its explicit
  # "unknown model id" error.
  #
  # Since discovery this asks "is ANY verified model on offer?", not "is THE
  # pinned id on offer?" — the pin is a floor, and readiness must agree with what
  # grok_select_model would actually run, or the probe rejects a CLI the review
  # would have used (exactly how the 1.0.3 marker change dropped grok entirely).
  # The --prompt-file capability is a property of the INSTALLED CLI, so it
  # belongs to readiness rather than to each run: probed here, a CLI without it
  # is reported not-ready once and never enters externalVoices. Probed only
  # inside run_grok (as it was), `list --json` advertised grok as live and every
  # gated cluster then failed identically with the same upgrade message.
  _grok_has_prompt_file || return 1
  grok_model_fetch
  [[ -z "$_grok_models" ]] && return 0
  [[ -n "$(_grok_highest_canonical verified)" ]]
}

ready_check() {
  local backend="$1" requested_model="${2:-}"
  case "$backend" in
    claude) return 0 ;;  # in-session, no separate auth
    # Bounded on every host (_probe_or_bare falls back to the polling watchdog),
    # because this is a NETWORK call on the pre-timer path: a captive portal or
    # dead proxy would otherwise stall past the timeout margin.
    #
    # A WALL HIT is a definite answer here, and it is "no". This looks like the
    # trade grok refuses one branch down, but the two probes ask different
    # questions: grok`s auth check is a local file test and the bounded probe
    # only asks a SECOND question (which models are offered), so degrading there
    # keeps a usable backend. `codex login status` IS the auth question, and it
    # reaches the network — the same network the review call needs. A CLI that
    # cannot answer it inside the probe bound is wedged or unreachable, and
    # calling it ready costs one adapter process per gated cluster (5 by
    # default), each re-running the same hanging probe and then burning the full
    # ~547s inner wall: five dead voices instead of one clean skip. The hint says
    # what happened, so this is a reported skip, not a silent family loss.
    #
    # NO fail-open branch. 126 was one: it is the adapter-s "could not bound this"
    # sentinel, but 126 is also what a shell returns for "found but cannot be
    # invoked" — a broken node shim, a noexec mount, a wrong-arch binary. Reading
    # that as "trust auth" reported a codex that CANNOT RUN as ready, and the
    # workflow then spawned one adapter process per gated cluster to rediscover
    # it. And "could not create a scratch file" is not a reason to proceed
    # anyway: the review call needs the same temp dir. Any non-zero rc is
    # not-ready; the hint says which case it was.
    codex)
      _probe_setup_lenient
      local codex_rc=0
      _probe_or_bare codex login status >/dev/null 2>&1 || codex_rc=$?
      # Remembered for ready_hint: it must tell "not logged in" apart from "the
      # probe hit the wall" or "the adapter could not bound it", and the rc is
      # the only thing that knows.
      _codex_probe_rc="$codex_rc"
      return "$codex_rc"
      ;;
    # Model-aware: auth alone would advertise grok even when the CLI no longer
    # offers any model the adapter can drive (grok drops/renames models
    # between releases — 0.2.101 removed grok-composer-2.5-fast).
    grok)   [[ -s "$GROK_AUTH_FILE" ]] && grok_model_offered ;;
    # ACP is the only out-of-band transport in kimi-code 0.32.0. The default
    # model is pinned for deterministic ensemble behavior and must be offered.
    kimi)   [[ -s "$KIMI_CREDENTIALS_FILE" ]] && _kimi_has_acp && kimi_model_offered "${requested_model:-$KIMI_DEFAULT_MODEL}" ;;
  esac
}

ready_hint() {
  # claude needs no hint: it is always available + ready in-session.
  local backend="$1" requested_model="${2:-}"
  case "$backend" in
    codex)
      # TWO failures reach here since the probe is bounded: a definite negative
      # (not logged in) and a probe that hit the wall (wedged CLI, dead proxy,
      # captive portal). Telling the second user to log in sends them at the
      # wrong thing, so name both and let the message say which is which.
      if (( ${_codex_probe_rc:-0} == 124 || ${_codex_probe_rc:-0} == 137 )); then
        echo "\`codex login status\` did not answer within ${_probe_timeout:-10}s — the CLI is wedged or the network is unreachable; if it is only slow, raise SWARM_PROBE_TIMEOUT (max ${SWARM_PROBE_TIMEOUT_MAX}s)"
      elif (( ${_codex_probe_rc:-0} == 126 )); then
        echo "\`codex login status\` could not be run under a bound — either the codex binary cannot be invoked (broken shim, noexec mount, wrong arch) or the adapter could not create a scratch file (check TMPDIR)"
      else
        echo "run: codex login"
      fi
      ;;
    grok)
      if [[ ! -s "$GROK_AUTH_FILE" ]]; then
        echo "run: grok login"
      else
        # TWO different failures reach here, with OPPOSITE remedies. Since
        # discovery, readiness fails when no SCHEMA-VERIFIED model is on offer —
        # which happens both when the CLI is too old (no canonical model at all)
        # and when it is NEWER than this adapter knows (canonical models listed,
        # none verified). Telling the second user to "update the grok CLI" sends
        # them to update an already-current install, and never names the actual
        # one-line fix.
        if ! _grok_has_prompt_file; then
          echo "this grok CLI has no --prompt-file — the adapter passes the prompt out-of-band so a large diff cannot hit the argv limit; update the grok CLI"
          return
        fi
        local _top; _top="$(_grok_highest_canonical)"
        if [[ -n "$_top" ]]; then
          echo "grok lists $_top but no schema-verified model — verify --json-schema on it, then add it to GROK_SCHEMA_VERIFIED in agents.sh"
        else
          echo "this grok CLI offers no canonical model (see: grok models) — update the grok CLI"
        fi
      fi
      ;;
    kimi)
      if [[ ! -s "$KIMI_CREDENTIALS_FILE" ]]; then
        echo "run: kimi login"
      elif ! _kimi_has_acp; then
        echo "this kimi CLI has no ACP stdio server — update kimi-code (verified on 0.32.0)"
      else
        echo "this kimi CLI does not offer ${requested_model:-$KIMI_DEFAULT_MODEL} (see: kimi provider list --json)"
      fi
      ;;
  esac
}

# ---------- subcommands ----------

subcmd_available() {
  _probe_setup_lenient
  local backend="${1:-}"
  [[ -z "$backend" ]] && usage
  validate_backend "$backend"
  available_version "$backend"
}

require_usable() {
  # Shared installed+ready gate for `ready` and `run`.
  # `command -v`, not available_version: the gate asks "is it installed", and
  # available_version answers a different question — it PRINTS a version string
  # and returns 0 regardless of what the probe did, so its result could never
  # fail this check. It still cost a bounded probe on every `run`, charged to
  # probe_budget_seconds and therefore subtracted from the backend-s own wall.
  # The version string is worth a probe where it is SHOWN (`available`, `list`),
  # not where it is discarded.
  local backend="$1" requested_model="${2:-}"
  local executable="$backend"
  [[ "$backend" == "kimi" ]] && executable="$KIMI_BIN"
  if [[ "$backend" != "claude" ]] && ! command -v "$executable" >/dev/null; then
    echo "$backend: not installed" >&2
    exit 1
  fi
  if ! ready_check "$backend" "$requested_model"; then
    echo "$backend: not ready — $(ready_hint "$backend" "$requested_model")" >&2
    exit 1
  fi
}

subcmd_ready() {
  _probe_setup_lenient
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
  for b in claude codex grok kimi; do
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
  _probe_setup_lenient
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
  # Machine-readable "will read+web be granted?" — the SAME condition the run
  # path gates on (_read_web_safe: working OS jail AND resolvable repo root), so
  # the /swarm:review skill's CAP_RULES + run-start notice can't promise reads/
  # web the adapter then strips. jail=no ⇒ the fail-closed degrade applies (grok
  # tool-less/no-web, codex web hard-off); the transport discards adapter
  # stderr, so this is the visible channel for that warning. Runs from the same
  # cwd as the review, so its repo root matches the run's.
  if _read_web_safe codex; then echo "jail=yes"; else echo "jail=no"; fi
}

swarm_max_prompt_bytes() {
  _resolve_int SWARM_MAX_PROMPT_BYTES "${SWARM_MAX_PROMPT_BYTES:-}" 524288 \
    $(( SWARM_CAP_HEADROOM + 1 )) "$SWARM_MAX_PROMPT_BYTES_MAX"
}

subcmd_config() {
  # The resolved, validated configuration as key=value lines, so the skill's prep
  # block can READ what the adapter will enforce instead of re-deriving it. Any
  # invalid value exits 2 here with the adapter's own message — one parser, one
  # verdict, one wording, and no way for the two sides to disagree.
  # Resolve into variables FIRST, each on its own line. `echo "x=$(resolver)"`
  # would swallow the failure: _resolve_int's `exit 2` ends only the command
  # substitution, and echo then succeeds — so `config` would print an empty value
  # and exit 0, telling the skill everything is fine. A bare assignment keeps the
  # substitution's status, so `set -e` aborts here with the adapter's message.
  local cap
  cap="$(swarm_max_prompt_bytes)"
  adapter_timeout
  probe_timeout
  echo "max_prompt_bytes=$cap"
  echo "cap_headroom=$SWARM_CAP_HEADROOM"
  echo "oversize_threshold=$(( cap - SWARM_CAP_HEADROOM ))"
  echo "timeout_seconds=$_adapter_timeout"
  echo "probe_timeout_seconds=$_probe_timeout"
  # The worst-case wall-clock this adapter can spend BEFORE the timed backend
  # call starts: every bounded probe at the bound that will ACTUALLY apply, plus
  # its kill grace. The workflow subtracts this from the Bash window to size its
  # inner cap, instead of keeping a hand-copied twin of these constants (which is
  # how a third probe slipped in unnoticed).
  # Derived from the RESOLVED probe timeout, not from its ceiling: budgeting the
  # ceiling charged every run for 20s probes even when 10s (the default) is what
  # gets enforced, and shrank every external call's usable wall time for margin
  # nothing could spend. Safe only because the workflow pins SWARM_PROBE_TIMEOUT
  # onto the adapter command line the same way it pins SWARM_TIMEOUT — the value
  # budgeted here is the value the adapter will enforce.
  echo "probe_budget_seconds=$(( SWARM_MAX_PROBES_PER_RUN * (_probe_timeout + TIMEOUT_EXPIRY_SLOP + TIMEOUT_KILL_GRACE) ))"
  # The RAILS, not just the resolved values. The workflow validates and clamps
  # what it pins onto every adapter call, and it used to do that against
  # hand-copied literals with a CI pin each — so raising a bound here meant
  # editing the mirror, its pin and its comment, and a direct (skill-less)
  # invocation clamped against whatever the mirror last said. Reporting them
  # costs nothing now that the whole config travels as ONE token: the reason the
  # copies existed (every value was a separate placeholder a model had to
  # transcribe) is gone. The workflow keeps them only as last-resort fallbacks.
  echo "max_prompt_bytes_min=$(( SWARM_CAP_HEADROOM + 1 ))"
  echo "max_prompt_bytes_max=$SWARM_MAX_PROMPT_BYTES_MAX"
  echo "probe_timeout_max_seconds=$SWARM_PROBE_TIMEOUT_MAX"
  echo "max_probes_per_run=$SWARM_MAX_PROBES_PER_RUN"
  echo "kill_grace_seconds=$TIMEOUT_KILL_GRACE"
  echo "expiry_slop_seconds=$TIMEOUT_EXPIRY_SLOP"
}

subcmd_run() {
  local backend="${1:-}"
  [[ -z "$backend" ]] && usage
  # Stamp the identity immediately: a record written by the EXIT trap on an early
  # failure otherwise reads ":smoke" with an empty backend, unusable in a report
  # whose whole job is naming which voice was lost.
  TELEMETRY_BACKEND="$backend"
  shift
  validate_backend "$backend"
  if [[ "$backend" == "claude" ]]; then
    echo "claude reviews run in-session via the Agent tool, not through this adapter" >&2
    exit 2
  fi

  local prompt_file="" lens_instr="" lens_instr_set=0 lens_instr_sum="" effort="xhigh" model="" schema="$DEFAULT_SCHEMA"
  while [[ $# -gt 0 ]]; do
    [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
    case "$1" in
      --prompt-file) prompt_file="$2"; shift 2 ;;
      --lens-instr)  lens_instr="$2";  lens_instr_set=1; shift 2 ;;
      --lens-instr-sum) lens_instr_sum="$2"; shift 2 ;;
      --effort)      effort="$2"; shift 2 ;;
      --model)       model="$2";       shift 2 ;;
      --schema)      schema="$2";      shift 2 ;;
      --telemetry)
        TELEMETRY_FILE="$2"
        # Arm the clock HERE, not only just before the backend call. A run that
        # dies in readiness/config validation exited before TELEMETRY_START was
        # ever set, so the EXIT trap wrote NOTHING — and a voice with no record is
        # indistinguishable from one that never ran, which is the exact
        # misreading this telemetry exists to prevent. The value is overwritten
        # immediately before the backend call, so a normal run still measures only
        # the backend; an early exit keeps this near-zero stamp and lands in the
        # reader's "never reached the backend" category.
        TELEMETRY_START="$(date +%s 2>/dev/null || true)"
        shift 2 ;;
      --unit)        TELEMETRY_UNIT="$2"; shift 2 ;;
      *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
  done
  case "$effort" in
    low|medium|high|xhigh|max) ;;
    *) echo "Invalid effort: $effort (low|medium|high|xhigh|max)" >&2; exit 2 ;;
  esac
  [[ -f "$schema" ]] || { echo "Schema not found: $schema" >&2; exit 2; }

  # PROMPT TRANSPORT: the prompt NEVER travels on argv. It used to, which made
  # `exec`'s per-argument limit the binding cap (Linux MAX_ARG_STRLEN = 128 KiB)
  # and forced a 120 KiB ceiling — above it the SKILL dropped ALL external
  # voices (EXTERNALS_OVERSIZE), i.e. the same damage as a backend timeout.
  # All CLIs accept the prompt out-of-band, so the adapter now normalizes every
  # input form to ONE file and hands only transport metadata to the backend:
  #   codex — `[PROMPT]` omitted or `-` reads the instructions from stdin
  #   grok  — `--prompt-file <PATH>` (present on 0.2.112; preflighted, no fallback)
  #   kimi  — the ACP client reads the file and sends its content over NDJSON stdio
  # The content is therefore never read into a shell variable either, so a large
  # diff no longer costs a full in-memory copy.
  #
  # Do NOT "solve" this instead by telling the backend to read the diff file
  # itself as a tool call: delivery would stop being verifiable (a model that
  # reads only the head of the file silently loses coverage), the untrusted diff
  # would arrive as a tool result rather than inside the nonce fence, and each
  # voice would pay an extra round-trip — the wrong direction while the 600 s
  # wall is still unfixed.
  #
  # What remains is a sanity cap on MODEL CONTEXT, not an exec limit: 512 KiB
  # (~4x the old ceiling, roughly 128k tokens of diff) leaves the models room to
  # reason and keeps a runaway range from burning a full timeout window. Raise it
  # with SWARM_MAX_PROMPT_BYTES when a review genuinely needs more — but note a
  # bigger prompt costs wall-clock, so it trades the size wall for the timeout
  # one. Measure BYTES (a multibyte prompt would slip a `${#prompt}` char count),
  # and check a file's size BEFORE copying it (a 500 MiB file must not be
  # duplicated into TMPDIR first).
  # Resolved by the ONE parser at startup (see _resolve_int): the floor is the
  # headroom, so a cap at or below it — which would make the skill's oversize
  # threshold zero or negative and silently drop every external voice — is
  # rejected loudly instead.
  local max_bytes nbytes
  max_bytes="$(swarm_max_prompt_bytes)"
  local prompt_path
  if [[ -n "$prompt_file" ]]; then
    [[ -f "$prompt_file" ]] || { echo "Prompt file not found: $prompt_file" >&2; exit 2; }
    nbytes=$(wc -c < "$prompt_file" | tr -d "[:space:]")
    (( nbytes > max_bytes )) && { echo "Prompt file too large ($nbytes bytes > $max_bytes) — narrow the diff range, or raise SWARM_MAX_PROMPT_BYTES" >&2; exit 2; }
    prompt_path="$prompt_file"
  else
    # Guard against blocking forever on an interactive/absent stdin: with no
    # --prompt-file and a TTY on fd 0, `cat` would hang waiting for input.
    [[ -t 0 ]] && { echo "No prompt: pass --prompt-file <f> or pipe the prompt on stdin" >&2; exit 2; }
    # 0600 BEFORE any content lands: the file carries the untrusted diff, and on
    # a shared host a default-umask temp file would be world-readable in the
    # window between creation and the first write.
    TMP_PROMPT="$(mktemp)" || { echo "Could not create a temp file for the prompt" >&2; exit 2; }
    chmod 600 "$TMP_PROMPT"
    # Bound the COPY, not just the check. `cat > file` would write an unbounded
    # stream to disk and only then measure it, which contradicts the size rule
    # this function exists to enforce (a huge stdin could fill TMPDIR before the
    # guard ever ran). Reading cap+1 bytes is enough to decide: exactly cap+1
    # means the input was larger than the cap.
    head -c "$(( max_bytes + 1 ))" > "$TMP_PROMPT"
    nbytes=$(wc -c < "$TMP_PROMPT" | tr -d "[:space:]")
    # Report ">" and not an exact figure: the copy stopped at cap+1, so that IS
    # the count here — printing it as the prompt's size states a number the input
    # never had, and one that contradicts the PROMPT_BYTES the skill measured.
    (( nbytes > max_bytes )) && { echo "Prompt too large (over $max_bytes bytes) — narrow the diff range, or raise SWARM_MAX_PROMPT_BYTES" >&2; exit 2; }
    prompt_path="$TMP_PROMPT"
  fi
  # A byte count alone is WEAKER than the guard this replaced: before the
  # out-of-band transport the prompt lived in a shell variable, and `[[ -z ]]`
  # after command substitution rejected whitespace-only input too (substitution
  # strips trailing newlines). Reviewing a prompt of blank lines wastes a full
  # backend call and returns nothing useful, so check for non-whitespace content.
  # Branch on grep-s rc, do not just negate it: 1 is "no match" (a genuinely
  # blank prompt) but 2 is "grep could not do its job" (unreadable file, broken
  # wrapper). Collapsing them reported a demonstrably non-empty prompt as empty
  # and sent the operator to inspect the diff instead of the tooling.
  local _grep_rc=0
  LC_ALL=C grep -q '[^[:space:]]' "$prompt_path" || _grep_rc=$?
  if (( _grep_rc == 1 )); then
    echo "Empty prompt (use --prompt-file or stdin)" >&2; exit 2
  elif (( _grep_rc > 1 )); then
    echo "Could not read the prompt file ($prompt_path) — grep exited $_grep_rc; this is a tooling or permission fault, not an empty prompt" >&2; exit 2
  fi

  # Per-cluster external voices: the WORKFLOW owns LENS_BRIEF (single source of
  # truth for the lens set) and passes the gated cluster's briefs here; the
  # adapter prepends them to the fenced-diff prompt. The assembly stays
  # DETERMINISTIC shell — never an LLM step, the same contract the skill's diff
  # fencing follows — and it is backend-agnostic, so a future voice inherits
  # per-cluster prompts for free. Checked AFTER the empty-prompt guard so a
  # lens instruction can never disguise an empty diff as a runnable prompt.
  # FAIL LOUD on a present-but-empty value: the workflow always passes a non-empty
  # instruction, so an empty one means it was lost in transport (a mangled retype,
  # a dropped shell quote). Silently running a lens-free review would be worse than
  # erroring — the workflow labels the returned findings with the cluster's lenses
  # regardless, so the coverage would be mislabeled, not merely reduced. An OMITTED
  # flag stays legal (manual/ad-hoc `run` calls have no cluster).
  if [[ "$lens_instr_set" == 1 && -z "$lens_instr" ]]; then
    echo "Empty --lens-instr: the per-cluster lens instruction was lost in transport; refusing a lens-free review the caller would mislabel" >&2; exit 2
  fi
  # INTEGRITY: the empty check above only catches a TOTAL loss. A transport that
  # shortens, paraphrases or rewords the instruction would still run, and the
  # caller would attribute the findings to lenses the backend was never told to
  # review. The caller sends a checksum of the exact text it built; a mismatch
  # means it changed in transit, so fail rather than review a different scope
  # than we report. COUPLED, not optional: an instruction WITHOUT a checksum is
  # refused, or a transport could void the guard just by dropping one flag.
  # (A checksum, not a length: `security`/`altitude` and `ONLY`/`ALSO` are
  # same-length swaps that change the scope while a byte count still matches.)
  if [[ "$lens_instr_set" == 1 && -z "$lens_instr_sum" ]]; then
    echo "--lens-instr requires --lens-instr-sum: the integrity checksum is missing, so the instruction cannot be verified; refusing to review a scope that may differ from the one being reported" >&2; exit 2
  fi
  if [[ -n "$lens_instr_sum" ]]; then
    [[ "$lens_instr_sum" =~ ^[0-9a-f]{8}$ ]] \
      || { echo "Invalid --lens-instr-sum '$lens_instr_sum' — must be 8 lowercase hex digits" >&2; exit 2; }
    local actual_sum
    # FNV-1a/32 over the raw UTF-8 bytes — the same function the workflow computes.
    actual_sum=$(printf '%s' "$lens_instr" | python3 -c '
import sys
h = 0x811c9dc5
for b in sys.stdin.buffer.read():
    h = ((h ^ b) * 0x01000193) & 0xffffffff
print("%08x" % h)') || { echo "Could not compute the --lens-instr checksum (python3 failed)" >&2; exit 2; }
    if [[ "$actual_sum" != "$lens_instr_sum" ]]; then
      echo "--lens-instr integrity check failed: caller declared checksum $lens_instr_sum, computed $actual_sum — the lens instruction was altered in transport; refusing to review a scope different from the one being reported" >&2
      exit 2
    fi
  fi
  if [[ -n "$lens_instr" ]]; then
    # Assemble instruction+diff into a NEW file rather than concatenating
    # strings: the whole point of the transport rework is that the diff never
    # enters a shell variable. Writing into a fresh file (not appending in
    # place) also keeps a caller-owned --prompt-file untouched — the workflow
    # hands the SAME prompt file to every voice, so mutating it would corrupt
    # the sibling calls running concurrently.
    local assembled prompt_dir
    # NEXT TO the caller-s prompt file, not in bare $TMPDIR. The skill creates its
    # own scratch dir and removes it with `rm -rf`, but a bare mktemp lands in
    # $TMPDIR as a SIBLING of that dir — so the one failure this branch is about
    # (the outer window SIGKILLs the adapter, no trap runs) left a full copy of
    # the reviewed diff on disk that neither cleanup could reach. Deriving the
    # directory from the prompt path needs no new flag: the caller already told
    # us where its scratch space is by handing us a file inside it.
    prompt_dir="$(dirname "$prompt_path")"
    assembled="$(mktemp "$prompt_dir/swarm-assembled.XXXXXX" 2>/dev/null || mktemp)" \
      || { echo "Could not create a temp file for the assembled prompt" >&2; exit 2; }
    chmod 600 "$assembled"
    # Hand the file to the trap BEFORE any content lands in it. Assigning
    # TMP_PROMPT only after the write left a window in which the file already
    # held the untrusted diff while cleanup() could not see it — a kill during
    # the `cat` (outer window, OOM) then left the diff on disk with nothing left
    # to remove it. Registering first is free; the old file is dropped after.
    # ACCEPTED RESIDUAL: bare `mktemp` puts it in $TMPDIR, not in the skill's
    # scratch dir, so a SIGKILL still leaves a 0600 file the skill's `rm -rf
    # "$TMPD"` cannot reach. Threading the skill's directory through the adapter
    # would add a flag for a hard-kill-only case; the trap covers every path a
    # signal handler can run at all.
    local previous="$TMP_PROMPT"
    TMP_PROMPT="$assembled"
    { printf '%s\n\n' "$lens_instr"; cat "$prompt_path"; } > "$assembled" \
      || { rm -f "$assembled"; TMP_PROMPT="$previous"; echo "Could not assemble the lens instruction and prompt" >&2; exit 2; }
    [[ -n "$previous" ]] && rm -f "$previous"
    prompt_path="$TMP_PROMPT"
    # Re-measure: the check above bounded the DIFF alone, but what the backend
    # ingests is instruction+diff.
    nbytes=$(wc -c < "$prompt_path" | tr -d "[:space:]")
    (( nbytes > max_bytes )) && { echo "Prompt too large with lens instruction ($nbytes bytes > $max_bytes) — narrow the diff range, or raise SWARM_MAX_PROMPT_BYTES" >&2; exit 2; }
  fi

  # Resolve BOTH knobs here, in the main shell, and BEFORE require_usable — its
  # readiness probes take the lenient path, so a bad value resolved there first
  # would fill the memo with the fallback and this run would never see the error
  # it is supposed to refuse.
  # Main shell, because the backend call happens inside `raw="$(sandboxed …)"`: a
  # resolution that first happened in with_timeout would memoize into that
  # subshell and die with it — the "timed out after Ns" message would print an
  # empty N, and _write_telemetry, running in the EXIT trap of the main shell,
  # would record timeout_seconds:0 for a call that was in fact capped.
  # Strict, because `run` APPLIES both values: a malformed knob is an error here,
  # not something to degrade around.
  adapter_timeout
  _probe_setup_strict
  # Both resolved above, in THIS shell — so the wall recorded in telemetry is the
  # wall that will actually apply, not a value assigned inside a subshell.
  _set_enforced_wall
  require_usable "$backend" "$model"
  require_python3

  # Start the clock as late as possible: readiness probes and validation are
  # adapter overhead, and folding them into the number would misattribute them
  # to the backend we are trying to characterize.
  if [[ -n "$TELEMETRY_FILE" ]]; then
    TELEMETRY_START="$(date +%s 2>/dev/null || true)"
    # TELEMETRY_BACKEND is NOT re-assigned here: subcmd_run stamps it before any
    # validation runs, precisely so an early failure still names the lost voice.
    TELEMETRY_EFFORT="$effort"
    TELEMETRY_MODEL="$model"
    TELEMETRY_BYTES="$nbytes"
  fi

  case "$backend" in
    codex) run_codex "$prompt_path" "$effort" "$model" "$schema" ;;
    grok)  run_grok  "$prompt_path" "$effort" "$model" "$schema" ;;
    kimi)  run_kimi  "$prompt_path" "$effort" "$model" "$schema" ;;
  esac
}

run_codex() {
  local prompt_path="$1" effort="$2" model="$3" schema="$4"
  [[ "$effort" == "max" ]] && effort="xhigh"
  # Record the EFFECTIVE effort/model (after the ladder mapping and the default
  # fill-in), not what the caller asked for — the point of the number is what
  # the backend actually ran.
  TELEMETRY_EFFORT="$effort"; TELEMETRY_MODEL="${model:-$CODEX_DEFAULT_MODEL}"

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
  local repo_args=() a
  while IFS= read -r a; do repo_args+=("$a"); done < <(_scope_args -C codex)

  # FAIL CLOSED without the OS jail: web + FS-read with no read-deny boundary
  # would let an injected read reach ~/.aws etc. and exfiltrate via web_search.
  # Degrade closes the EGRESS half: web is HARD-disabled (=false, not merely
  # omitted — an omitted flag would inherit a future codex default or a user
  # config that turns web on). FS reads remain: -s read-only is codex's most
  # restrictive sandbox tier (there is no no-read tier), the same read surface
  # codex always had in 0.5.x — the degrade is per-voice, not "tool-less", and
  # the docs describe it that way (do not over-claim).
  local web_args=(-c tools.web_search=true)
  if ! _read_web_safe codex; then
    echo "warning: no working OS jail or unresolvable repo root — codex web search HARD-disabled (fail closed); FS reads stay inside codex's own read-only sandbox (0.5.x read surface)" >&2
    web_args=(-c tools.web_search=false)
  fi

  # The schema-validated JSON lands in $TMP_OUT; codex's stdout copy of the
  # final message is discarded (its transcript goes to stderr = debug info).
  # PROMPT ON STDIN: `-` as the positional PROMPT makes codex read the
  # instructions from stdin, which is what keeps the diff off argv (see the
  # transport note in `run`). Pass it EXPLICITLY rather than omitting the
  # argument — an omitted prompt is the same code path today, but `-` states the
  # intent and cannot be re-interpreted as "no prompt given" by a future release.
  # This does NOT resurrect the documented hang: codex waits for "additional
  # input from stdin" when a prompt arrives on ARGV *and* stdin is an open pipe;
  # here stdin IS the prompt and hits EOF at the end of the file.
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
      -- - <"$prompt_path" >/dev/null 2>/dev/null || rc=$?
  TELEMETRY_RC="$rc"
  if (( rc != 0 )); then
    if _is_timeout_rc "$rc"; then echo "codex exec timed out after ${_adapter_timeout}s" >&2
    # 126 has TWO causes here and the message must not pick one: `timeout` uses it
    # for "found but could not be executed" (noexec mount, wrong-arch binary,
    # broken shim, a sandbox wrapper that failed to exec), and the watchdog uses
    # it for "could not create a scratch file". Naming only TMPDIR sent operators
    # to a temp dir that was never involved. ready_hint words it the same way.
    elif (( rc == 126 )); then echo "codex exec could not be run: either the command was found but could not be executed (noexec mount, wrong architecture, broken shim, failed sandbox wrapper) or the adapter could not create a scratch file to bound it (check TMPDIR)" >&2
    else echo "codex exec failed" >&2; fi
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
_grok_help_done=""
_grok_help_rc=0
_grok_has_prompt_file() {
  # Preflight for the out-of-band prompt flag. `--prompt-file` is what keeps the
  # diff off argv (see the transport note in `run`); an older CLI without it
  # would fail with a bare "unknown flag" and rc=1, which the caller reports as
  # a generic backend error. Probe the help text rather than parse a version:
  # the release that introduced the flag is not documented, and the capability
  # is what actually matters (~40 ms, next to a multi-minute review call).
  # Do NOT silently fall back to `--single`: that is exactly the argv path this
  # rework removed, so it would reintroduce the 120 KiB wall as a mystery
  # failure on big diffs instead of a clear "upgrade the CLI".
  # Capture into a variable instead of piping to grep: under `pipefail` an
  # early-exiting `grep -q` SIGPIPEs the CLI and the pipeline reports failure
  # even on a match. Its OWN function so the argv tests can stub it — otherwise
  # they would need a real grok on PATH to exercise run_grok.
  # BOUND the probe. This runs outside with_timeout, so an unbounded `grok --help`
  # (a wedged CLI, a stale leader socket, a blocked FS) would hang the whole review
  # before any review work started. Same rule and same bound as the readiness
  # probe, and the escalation to SIGKILL is what actually enforces it (`-k` under
  # the wrapper, the watchdog`s own kill without one): a CLI that ignores SIGTERM
  # or forks a stdout-inheriting child keeps `$(...)` blocking past the deadline.
  # Check rc BEFORE the output: a probe killed at the deadline (124), SIGKILLed
  # by `-k` (137), or never bounded at all (126) still yields EMPTY stdout, and
  # reading that as "the flag is absent" would refuse a CLI that has it — telling
  # the user to upgrade an already-current install while the voice dies. A probe
  # that did not complete says nothing: assume the capability and let the real
  # call report the truth.
  # MEMOIZED, like grok_model_fetch: this is asked twice per `run grok` (once by
  # grok_model_offered for readiness, once here) and each ask is a bounded probe.
  # Unmemoized this was a THIRD probe per run against a margin budgeted for two,
  # so on a wedged CLI the OUTER window would kill the adapter before the inner
  # cap fired, losing rc=124 and the telemetry record — exactly the diagnosis
  # this branch exists to preserve. The count is pinned by test_lens_sync.py and
  # the budget is derived from it by `config`, so the arithmetic is not restated
  # here: a number in a comment is the copy that goes stale.
  if [[ -n "$_grok_help_done" ]]; then return "$_grok_help_rc"; fi
  _grok_help_done=1
  _probe_setup_lenient
  # `_probe_or_bare`: the question must be ANSWERED on a
  # host with no coreutils too. Assuming the capability there reported an OLD CLI
  # without --prompt-file as READY, and every gated cluster then failed
  # identically at launch. `_probe_or_bare` now bounds that answer either way
  # (wrapper, else watchdog), so this no longer trades the bound for the answer.
  local help="" rc=0
  help="$(_probe_or_bare grok --help)" || rc=$?
  if (( rc != 0 )); then
    _grok_help_rc=0
    # Unconditional now: the probe is bounded on every host (watchdog fallback),
    # so a non-zero rc always means the probe itself failed — it is never the
    # "ran unbounded, cannot have timed out" case the old $_timeout_bin gate
    # silenced.
    echo "warning: \`grok --help\` probe did not complete (rc=$rc) — assuming --prompt-file is supported" >&2
    return 0
  fi
  # An EMPTY capture is not a negative answer either — same rule as a non-zero rc
  # above: a probe that produced nothing did not tell us the flag is missing.
  # It happens for real: a CLI that prints its usage to STDERR exits 0 with empty
  # stdout, and the probe helpers discard stderr by design (probe noise must not
  # reach the adapter-s own). Falling through to the `case` reported an
  # up-to-date CLI as too old and dropped the whole grok family behind an
  # upgrade-your-install message. Assume the capability and let the real call
  # report the truth, loudly.
  if [[ -z "$help" ]]; then
    _grok_help_rc=0
    echo "warning: \`grok --help\` produced no output — assuming --prompt-file is supported" >&2
    return 0
  fi
  case "$help" in
    *--prompt-file*) _grok_help_rc=0 ;;
    *) _grok_help_rc=1 ;;
  esac
  return "$_grok_help_rc"
}

GROK_READ_TOOLS="read_file,list_dir,grep"
GROK_WEB_TOOLS="web_search,web_fetch"
GROK_TOOLS="${GROK_READ_TOOLS},${GROK_WEB_TOOLS}"

run_grok() {
  local prompt_path="$1" effort="$2" model="$3" schema="$4"
  # grok's effort ladder is low|medium|high (0.2.101 dropped max) — map the two
  # higher adapter tiers down so a stale caller degrades instead of erroring,
  # mirroring codex's max→xhigh mapping.
  case "$effort" in xhigh|max) effort="high" ;; esac
  # Discovery resolves the model; the pin is only the fallback inside it.
  grok_select_model "$model"
  local grok_model="$GROK_SELECTED_MODEL"
  # Effective values, same reason as run_codex.
  TELEMETRY_EFFORT="$effort"; TELEMETRY_MODEL="$grok_model"

  # Preflight-reject any model whose schema enforcement is unverified. The gate
  # is now the VERIFIED TABLE rather than one hard-coded id: a model that merely
  # accepts --json-schema and returns structuredOutput:null fails late, after
  # burning a full review, so reject up front with a usage error.
  # An explicit override is schema-gated below, but that says nothing about
  # whether the INSTALLED CLI offers the id: readiness would pass on the
  # discovered model while every call dies at launch with "unknown model id".
  # Check it against the list we already fetched (memoized — no extra call);
  # skip silently when the list is unavailable, since that is the documented
  # trust-auth degrade rather than evidence of absence.
  if [[ -n "$model" ]]; then
    grok_model_fetch
    if [[ -n "$_grok_models" ]]; then
      _line_in_list "$grok_model" "$_grok_models" \
        || { echo "grok model '$grok_model' is not offered by this CLI (see: grok models)" >&2; exit 2; }
    fi
  fi
  if ! _grok_schema_verified "$grok_model"; then
    echo "grok model '$grok_model' is not schema-verified — the adapter requires enforced --json-schema output. Verified: $(printf '%s' "$GROK_SCHEMA_VERIFIED" | tr '\n' ' ')" >&2
    exit 2
  fi
  # Surface a discovery note (a newer unverified model on offer, or no verified
  # model at all) exactly once, on stderr. The transport discards adapter stderr,
  # so this is a local-run aid — the upgrade prompt lives here, not in the report.
  if [[ -n "$GROK_SELECT_NOTE" ]]; then
    echo "note: $GROK_SELECT_NOTE" >&2
    GROK_SELECT_NOTE=""
  fi

  _grok_has_prompt_file \
    || { echo "grok CLI has no --prompt-file (present on 0.2.112) — the adapter passes the prompt out-of-band so a large diff cannot hit the argv limit; upgrade the grok CLI" >&2; exit 2; }

  # --prompt-file <path> (not --single=<prompt>): the prompt stays out of argv,
  # so the diff size is bounded by model context, not MAX_ARG_STRLEN. grok reads
  # the file from INSIDE the OS jail, so it must be jail-readable — mktemp's
  # TMPDIR is (the denylist covers credential paths, not the temp dir). A user
  # who adds TMPDIR to SWARM_DENY_PATHS breaks their own prompt delivery.
  # Read+web posture (0.6.0): strict --tools allowlist grants file-read
  # (read_file,list_dir,grep) + web (web_search,web_fetch) so grok can find
  # out-of-diff bugs and research external knowledge. No write/shell tools.
  # --cwd pins the project root. The OS secret-jail (sandboxed) blocks
  # credential paths; the prompt egress guard (SKILL.md HDR, outside the diff
  # fence) is the model-cooperation web policy; scrub_secrets is the output
  # backstop. Do NOT re-add --disable-web-search or --tools "" unconditionally —
  # they are reserved for the no-jail fail-closed degrade below.
  local cwd_args=() a
  while IFS= read -r a; do cwd_args+=("$a"); done < <(_scope_args --cwd grok)

  # FAIL CLOSED without the OS jail: grok's file+web tools with no read-deny
  # boundary would re-open the exfil channel 0.5.x closed by flags. Degrade to
  # the 0.5.x posture (tool-less, no web) and say so — the review still runs on
  # the inlined diff, just without exploration.
  local tool_args=(--tools "$GROK_TOOLS")
  if ! _read_web_safe grok; then
    echo "warning: no working OS jail or unresolvable repo root — grok degraded to tool-less/no-web (fail closed; read+web needs the OS secret-jail AND a resolvable repo to scope+deny)" >&2
    tool_args=(--tools "" --disable-web-search)
  fi

  # Absolute before the containment test: grok resolves --prompt-file against
  # `--cwd <repo>`, so a relative path would be compared as-is here and match no
  # deny prefix while the backend reads a different file entirely.
  case "$prompt_path" in
    /*) ;;
    *)  prompt_path="$PWD/$prompt_path" ;;
  esac
  _assert_prompt_readable_in_jail "$prompt_path" grok
  local raw rc=0
  raw="$(sandboxed grok grok -m "$grok_model" --effort "$effort" \
      ${tool_args[@]+"${tool_args[@]}"} \
      ${cwd_args[@]+"${cwd_args[@]}"} \
      --json-schema "$(cat "$schema")" \
      --prompt-file "$prompt_path" </dev/null 2>/dev/null)" || rc=$?
  TELEMETRY_RC="$rc"
  if (( rc != 0 )); then
    # stderr is deliberately discarded (injection guard), so name the likely
    # cause: an older CLI that predates the pinned model reports Ready (auth
    # heuristic) yet rejects the model id at runtime.
    if _is_timeout_rc "$rc"; then echo "grok timed out after ${_adapter_timeout}s" >&2
    # 126 = the adapter refused to launch it unbounded, so grok never ran. Its own
    # warning is written to the stderr this call discards, and without this branch
    # the operator was sent to inspect a model list for a temp-dir failure.
    elif (( rc == 126 )); then echo "grok could not be run: either the command was found but could not be executed (noexec mount, wrong architecture, broken shim, failed sandbox wrapper) or the adapter could not create a scratch file to bound it (check TMPDIR)" >&2
    else echo "grok failed — check that the installed grok CLI offers model '$grok_model' (see: grok models)" >&2
    fi
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

_kimi_output_contract() {
  # Kimi has no schema-enforcement flag. Put the exact schema in its PROMPT so
  # obedience has the best chance of succeeding, then independently validate the
  # answer in kimi-acp.py. The contract follows the fenced diff: lensInstr remains
  # the first text in the prompt, preserving the workflow's scope invariant.
  local schema="$1"
  printf '\n\nOUTPUT CONTRACT (HIGH PRIORITY): Return ONLY one JSON object matching this JSON Schema. No markdown fence, preface, explanation, or trailing text. Empty findings is valid.\n' &&
    cat "$schema" &&
    printf '\nThe response must be exactly the schema object and nothing else.\n'
}

run_kimi() {
  local prompt_path="$1" effort="$2" model="$3" schema="$4"

  # kimi-code 0.32.0 exposes low|high|max through ACP session config even though
  # the top-level CLI has no --effort flag. Map missing intermediate tiers DOWN,
  # matching the conservative mappings used by codex/grok: a caller never gets a
  # more expensive tier than requested merely because this ladder is sparse.
  case "$effort" in
    low|medium) effort="low" ;;
    high|xhigh) effort="high" ;;
    max) effort="max" ;;
  esac
  local kimi_model="${model:-$KIMI_DEFAULT_MODEL}"
  TELEMETRY_EFFORT="$effort"; TELEMETRY_MODEL="$kimi_model"

  [[ -f "$KIMI_ACP_CLIENT" ]] \
    || { echo "kimi ACP client missing: $KIMI_ACP_CLIENT" >&2; exit 2; }

  # Codex/Grok get the schema through an enforcement flag; Kimi has no equivalent,
  # so append it as a strict output instruction before the local validator checks
  # the answer. Keep the caller-owned prompt immutable and account for the extra
  # bytes in both the context cap and telemetry.
  local kimi_prompt previous kimi_bytes max_bytes
  kimi_prompt="$(mktemp)" || { echo "Could not create a temp file for the Kimi schema prompt" >&2; exit 2; }
  chmod 600 "$kimi_prompt"
  { cat "$prompt_path"; _kimi_output_contract "$schema"; } >"$kimi_prompt" \
    || { rm -f "$kimi_prompt"; echo "Could not assemble the Kimi schema prompt" >&2; exit 2; }
  previous="$TMP_PROMPT"
  TMP_PROMPT="$kimi_prompt"
  [[ -n "$previous" ]] && rm -f "$previous"
  prompt_path="$TMP_PROMPT"
  kimi_bytes=$(wc -c <"$prompt_path" | tr -d "[:space:]")
  max_bytes="$(swarm_max_prompt_bytes)"
  (( kimi_bytes > max_bytes )) \
    && { echo "Kimi prompt too large with output schema ($kimi_bytes bytes > $max_bytes) — narrow the diff range, or raise SWARM_MAX_PROMPT_BYTES" >&2; exit 2; }
  TELEMETRY_BYTES="$kimi_bytes"

  # Kimi has no CLI switch that removes read/web while still accepting the full
  # prompt out-of-band. Its safe transport is ACP, whose client rejects every
  # approval-gated write/edit/shell call while auto-allowed read/search/fetch stay
  # available. Without the OS secret jail AND a resolvable repo root, those reads
  # would have no hard credential boundary, so do not run a reduced imitation:
  # fail once and let the skill omit Kimi from this host's externalVoices.
  if ! _read_web_safe kimi; then
    echo "kimi requires a working OS jail and resolvable repo root — refusing to run read/web-capable ACP without the secret boundary (fail closed)" >&2
    exit 1
  fi
  local repo; repo="$(_repo_root)"

  TMP_OUT="$(mktemp)"
  chmod 600 "$TMP_OUT"

  # FULL PROMPT OVER ACP STDIO: kimi -p is intentionally absent because it only
  # accepts the prompt on argv. The Python client starts `kimi acp`, sends the
  # prompt as an ACP ContentBlock over NDJSON, pins model/thinking/default mode,
  # rejects every permission request, and validates the final assistant text
  # against the configured schema. Kimi's own stderr is discarded inside the
  # client; this outer redirect also withholds safe-but-unnecessary diagnostics
  # from the workflow transport.
  local rc=0
  sandboxed kimi python3 "$KIMI_ACP_CLIENT" \
      --prompt-file "$prompt_path" \
      --schema "$schema" \
      --cwd "$repo" \
      --model "$kimi_model" \
      --effort "$effort" >"$TMP_OUT" 2>/dev/null || rc=$?

  if _is_timeout_rc "$rc"; then
    TELEMETRY_RC="$rc"
    echo "kimi ACP review timed out after ${_adapter_timeout}s" >&2
    exit 1
  fi

  case "$rc" in
    0) TELEMETRY_RC=0 ;;
    2)
      # Schema/config incompatibility is an adapter usage error; the backend was
      # never meaningfully entered, so leave backend_rc null in telemetry.
      TELEMETRY_RC=""
      echo "kimi ACP adapter rejected its configuration" >&2
      exit 2
      ;;
    11|13)
      # Kimi answered, but local schema/protocol/policy validation rejected it.
      # Record backend_rc=0 so telemetry can distinguish this from "never ran".
      TELEMETRY_RC=0
      echo "kimi response was rejected by the ACP schema/policy gate" >&2
      exit 1
      ;;
    12)
      # ACP negotiation/configuration failed before session/prompt. No review
      # response existed to reject, so leave backend_rc null rather than claiming
      # a successful model answer.
      TELEMETRY_RC=""
      echo "kimi ACP session negotiation failed before review" >&2
      exit 1
      ;;
    *)
      TELEMETRY_RC="$rc"
      echo "kimi ACP review failed (rc=$rc)" >&2
      exit 1
      ;;
  esac

  [[ -s "$TMP_OUT" ]] || { echo "kimi ACP produced no output" >&2; exit 1; }
  scrub_secrets <"$TMP_OUT"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    list)          subcmd_list "$@" ;;
    available)     subcmd_available "$@" ;;
    ready)         subcmd_ready "$@" ;;
    jail)          subcmd_jail ;;
    config)        subcmd_config ;;
    run)           subcmd_run "$@" ;;
    -h|--help)     print_usage; exit 0 ;;
    "")            usage ;;
    *)             echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

# Source guard: run the CLI only when executed, not when sourced. Lets tests
# `source` this file for its helpers without the sed-extraction surgery (and
# without tripping `main`'s usage exit). Executed → BASH_SOURCE[0]==$0 → run.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
