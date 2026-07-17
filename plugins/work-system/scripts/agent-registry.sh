#!/usr/bin/env bash
# agent-registry.sh — the set of worker agents /kickoff can launch.
#
# One CLI × model per entry. The canonical NAME is `cli:model` (e.g.
# claude:fable, codex:gpt-5.6-terra). Everything is registry-driven: the shell
# `REGISTRY` table below is the single source of truth, so the skill and the
# launch helper never hardcode a CLI list or an alias `if` chain.
#
# Subcommands:
#   list [--json]                 Probe every entry -> human table or JSON array
#                                 (columns: name, cli, model, available, note)
#   resolve <selector> [--session <name>]
#                                 Map a selector to launch argv + metadata.
#                                 Selectors: a shorthand flag (--fable, --opus,
#                                 --codex, --sol, --grok), a
#                                 canonical name (claude:opus), a bare CLI
#                                 (codex -> that CLI's default model), or
#                                 cli:model (the --agent escape hatch).
#                                 Emits key=value lines incl. one `argv=` line
#                                 per exec word. Exit 3 if the entry's CLI is
#                                 unavailable (still prints available=no + note).
#   default get                   Print the repo's default agent name, or empty
#                                 if none is set (then no-flag /kickoff picks).
#   default set <name>            Persist the default in the repo's committed
#                                 .claude/work-system-agent.
#
# Launch shape per CLI (resolve builds the argv; the launch helper just execs
# the `argv=` words, so the argv-exec path — no shell-typing race — is kept):
#   claude  -> claude --model <model> [-n <session>] /continue
#              (the work-system /continue skill resumes TASK.md deterministically)
#   codex   -> codex -m <model> <bootstrap-prompt>
#   grok    -> grok  -m <model> <bootstrap-prompt>
#   The bootstrap prompt (codex/grok have no work-system skills) tells the agent
#   to read TASK.md and drive the task to a PR. `supports=` metadata records
#   which lifecycle hooks each agent honors, so /close and /continue can degrade
#   for non-claude workers instead of faking claude-only behavior.
#
# State & config (override for tests / relocation):
#   WORK_SYSTEM_AGENT_PROJECT_STATE  the repo's default-agent file
#                                    default: <repo-root>/.claude/work-system-agent
#   No global state and no shipped fallback — a repo with no default gets the
#   picker instead.
#
# Exit codes: 0 ok · 1 not-available / no-op · 2 usage / unknown selector ·
#             3 resolved but the entry's CLI is unavailable
set -euo pipefail

HOME="${HOME:-$(cd ~ 2>/dev/null && pwd || echo /nonexistent)}"
# The ONLY persisted state: the per-repo committed `default` agent. No global
# state, no shipped fallback — if a repo has no default, /kickoff shows the
# picker (and offers to save the pick here). Defaults to
# <repo-root>/.claude/work-system-agent; overridable for tests.
PROJECT_STATE="${WORK_SYSTEM_AGENT_PROJECT_STATE:-}"
if [ -z "$PROJECT_STATE" ]; then
  # The default belongs in the MAIN repo, not a linked worktree — a `default set`
  # run from inside a worktree must still land (and commit) in the main checkout,
  # not the disposable copy that `/close` removes. `--git-common-dir` points at the
  # main repo's `.git` from anywhere; its parent is the main worktree root. Fall
  # back to `--show-toplevel` when that can't be resolved (older git / odd layout).
  _common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$_common" ] && [ "$(basename "$_common")" = ".git" ]; then
    _repo_root="$(dirname "$_common")"
  else
    _repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi
  [ -n "$_repo_root" ] && PROJECT_STATE="$_repo_root/.claude/work-system-agent"
fi
GROK_AUTH_FILE="${GROK_AUTH_FILE:-$HOME/.grok/auth.json}"

# The bootstrap prompt for CLIs without work-system skills (codex, grok). One
# argv word; the launch helper passes it verbatim.
BOOTSTRAP_PROMPT='Read TASK.md in this worktree and continue the task. Commit on the current branch as you go, and open a PR when the work is complete.'

# ---------- registry ----------
# `flag|cli|model|supports`. flag `-` = no shorthand (name/--agent only). The
# FIRST entry of each CLI is that CLI's default model (for a bare `--agent codex`).
# `supports` is per-agent capability metadata: which lifecycle hooks each agent
# honors —
#   continue   -> `/continue`-reopen + `claude -c` session resume work
#   close-exit -> /close may inject `/exit` for a clean self-teardown
#   statusline -> the `[ws]` statusline segment tracks its session
# codex/grok get commit,pr only — they drive git + a PR but have none of the
# claude-session lifecycle hooks. RESERVED / not yet consumed: the skills
# currently hardcode the claude-vs-codex/grok distinction in prose; this field is
# the seed for the manager/worker-orchestration design to read per-agent
# capabilities from one place. Keep it in sync when that lands.
REGISTRY='--fable|claude|fable|continue,close-exit,statusline,commit,pr
--opus|claude|opus|continue,close-exit,statusline,commit,pr
-|claude|sonnet|continue,close-exit,statusline,commit,pr
--codex|codex|gpt-5.6-terra|commit,pr
--sol|codex|gpt-5.6-sol|commit,pr
--grok|grok|grok-4.5|commit,pr'

usage() {
  # Usage = header comment from line 2 up to (not including) the registry
  # section, bounded by pattern so header edits can't truncate it.
  awk 'NR < 2 {next} /^# ----------/ {exit} {sub(/^# ?/, ""); print}' "$0" >&2
  exit 2
}

# ---------- registry access ----------
# Emit one `flag|cli|model|supports` record per line (skips blank lines).
registry_rows() { printf '%s\n' "$REGISTRY"; }

# Print the whole record for a canonical name (cli:model), or nothing.
row_for_name() {
  local want="$1" flag cli model supports
  while IFS='|' read -r flag cli model supports; do
    [ -n "$cli" ] || continue
    [ "$cli:$model" = "$want" ] && { printf '%s|%s|%s|%s\n' "$flag" "$cli" "$model" "$supports"; return 0; }
  done < <(registry_rows)
  return 1
}

# Print the record whose shorthand flag matches, or nothing.
row_for_flag() {
  local want="$1" flag cli model supports
  while IFS='|' read -r flag cli model supports; do
    [ "$flag" = "-" ] && continue
    [ "$flag" = "$want" ] && { printf '%s|%s|%s|%s\n' "$flag" "$cli" "$model" "$supports"; return 0; }
  done < <(registry_rows)
  return 1
}

# Print the default (first) record for a bare CLI name, or nothing.
row_for_cli_default() {
  local want="$1" flag cli model supports
  while IFS='|' read -r flag cli model supports; do
    [ "$cli" = "$want" ] && { printf '%s|%s|%s|%s\n' "$flag" "$cli" "$model" "$supports"; return 0; }
  done < <(registry_rows)
  return 1
}

# Resolve any selector to a registry record. Order: shorthand flag, canonical
# cli:model name, bare CLI (its default model). Prints the record or fails (1).
row_for_selector() {
  local sel="$1"
  case "$sel" in
    --*) row_for_flag "$sel" && return 0 ;;
    *:*) row_for_name "$sel" && return 0 ;;
    *)   row_for_cli_default "$sel" && return 0 ;;
  esac
  return 1
}

# ---------- availability probes (work-system-owned; no swarm dependency) ----------
# `entry_status <cli> <model>` echoes `<avail>\t<note>` (avail = yes|no) for a
# specific CLI×model. Kept plain (no associative arrays) so the script runs on
# stock bash 3.2 too. Availability is install + auth, and — where the CLI can
# enumerate its own models — *model-level* too:
#   claude: available in-session; model aliases (fable/opus/sonnet) aren't
#           CLI-listable, so they're taken on trust (they're stable).
#   codex:  install + `codex login status`. No clean CLI model-list command, so
#           the model is trusted (both shipped codex models are stable).
#   grok:   install + auth + the model must appear in `grok models` — the CLI
#           rejects an unlisted `-m` id at launch ("unknown model id"), so a
#           per-CLI auth check alone would mislabel a model the CLI no longer
#           offers (grok drops/renames models between releases) as available.
#           This is the one CLI with a usable model-list command.

# run_bounded <seconds> <cmd...> — run cmd with a hard time bound so an external
# probe can never hang `list`/the picker. Prints cmd's stdout; returns cmd's exit
# code, or non-zero if it hit the bound. Uses timeout/gtimeout when present; else
# self-bounds with a detached killer that escalates SIGTERM -> SIGKILL, so a
# process that ignores SIGTERM still can't block the caller. The killer's fds go
# to /dev/null: this runs inside a command substitution, and a background job
# holding the captured pipe would make $(...) block until it exits.
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
    # No timeout binary — self-bound. cmd stdout -> a temp file, NOT this
    # function's stdout: if the killer has to SIGKILL the cmd, an orphaned
    # grandchild (e.g. a `sleep` the cmd spawned) inherits the cmd's fds — and if
    # that were the command-substitution pipe, the capturing $(...) would block
    # until the orphan dies. Writing to $tmp means the only thing on our stdout is
    # the final `cat`, so $() returns as soon as we do. The killer escalates
    # SIGTERM -> SIGKILL so a process ignoring SIGTERM still can't hang the caller.
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

# Print `grok models` RAW output on stdout; RETURN 0 iff the fetch succeeded,
# non-zero if it was unreachable/timed out. entry_status substring-matches the
# model id against this raw text (not a positional field) so a reformatted
# listing — a moved/renamed column, a dropped `*` bullet — doesn't yield a wrong
# token and a false "model not offered". Status travels via the exit code, NOT a
# global: entry_status runs this in a command substitution (its own subshell), so
# a global flag would never propagate back. Bounded via run_bounded (never hangs).
grok_models_raw() {
  run_bounded 10 grok models 2>/dev/null
}

entry_status() {
  local cli="$1" model="$2" avail=no note=""
  case "$cli" in
    claude)
      # In-session claude is available by definition; PATH only sharpens the note.
      avail=yes
      command -v claude >/dev/null 2>&1 || note="in-session"
      ;;
    codex)
      # Bounded like grok: `codex login status` can touch the network, so an
      # unbounded call could hang `list`/the picker. A run_bounded TIMEOUT (124)
      # is inconclusive → trust install and assume available (mirrors grok), NOT a
      # genuine auth failure — a slow probe must not tell a logged-in user to
      # re-login and disable the backend.
      local crc=0
      if ! command -v codex >/dev/null 2>&1; then note="not installed"
      else
        run_bounded 10 codex login status >/dev/null 2>&1 || crc=$?
        if [ "$crc" -eq 0 ]; then avail=yes
        elif [ "$crc" -eq 124 ]; then avail=yes; note="codex login status timed out — availability assumed"
        else note="run: codex login"; fi
      fi
      ;;
    grok)
      if ! command -v grok >/dev/null 2>&1; then note="not installed"
      elif [ ! -s "$GROK_AUTH_FILE" ]; then note="run: grok login"
      else
        local _raw grc=0
        _raw="$(grok_models_raw)" || grc=$?   # exit code = fetch status
        if [ "$grc" -ne 0 ]; then
          # unreachable/timed out — inconclusive, not a drop. Trust auth so a
          # network hiccup doesn't wrongly block launch.
          avail=yes; note="grok models unreachable — availability assumed"
        elif [ -z "$_raw" ]; then
          # succeeded but produced nothing — inconclusive too, not "model gone".
          avail=yes; note="grok models empty — availability assumed"
        elif grep -qF -- "$model" <<<"$_raw"; then avail=yes   # substring, drift-tolerant
        else note="model not offered by this grok CLI (see: grok models)"; fi
      fi
      ;;
    *) note="unknown cli" ;;
  esac
  printf '%s\t%s\n' "$avail" "$note"
}

# ---------- subcommands ----------

emit_argv() {
  # Print `argv=<word>` lines for a resolved entry. $1=cli $2=model $3=session.
  local cli="$1" model="$2" session="$3"
  case "$cli" in
    claude)
      printf 'argv=%s\n' claude --model "$model"
      [ -n "$session" ] && printf 'argv=%s\n' -n "$session"
      printf 'argv=%s\n' /continue
      ;;
    codex)
      printf 'argv=%s\n' codex -m "$model" "$BOOTSTRAP_PROMPT"
      ;;
    grok)
      printf 'argv=%s\n' grok -m "$model" "$BOOTSTRAP_PROMPT"
      ;;
  esac
}

subcmd_resolve() {
  local selector="" session=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --session) [ $# -ge 2 ] || { echo "Missing value for --session" >&2; exit 2; }
                 # The session is emitted as an `argv=<session>` line that the
                 # launch helper re-parses line by line — a newline in it would
                 # forge extra argv tokens. Reject control chars at the source so
                 # a crafted task name/label can't inject a worker flag.
                 case "$2" in
                   *[[:cntrl:]]*) echo "resolve: --session must not contain control characters" >&2; exit 2 ;;
                 esac
                 session="$2"; shift 2 ;;
      --*) if [ -z "$selector" ]; then selector="$1"; shift
           else echo "Unexpected argument: $1" >&2; exit 2; fi ;;
      *)   if [ -z "$selector" ]; then selector="$1"; shift
           else echo "Unexpected argument: $1" >&2; exit 2; fi ;;
    esac
  done
  [ -n "$selector" ] || { echo "resolve: missing selector" >&2; exit 2; }

  local record
  record="$(row_for_selector "$selector")" || {
    echo "Unknown agent selector: $selector" >&2
    echo "Try: --fable --opus --codex --sol --grok, a name (claude:opus), or a cli (codex)" >&2
    exit 2
  }
  local flag cli model supports
  IFS='|' read -r flag cli model supports <<<"$record"

  local avail note
  IFS=$'\t' read -r avail note < <(entry_status "$cli" "$model")

  printf 'name=%s\n' "$cli:$model"
  printf 'cli=%s\n' "$cli"
  printf 'model=%s\n' "$model"
  printf 'available=%s\n' "$avail"
  printf 'supports=%s\n' "$supports"
  [ -n "$note" ] && printf 'note=%s\n' "$note"
  emit_argv "$cli" "$model" "$session"

  [ "$avail" = yes ] || exit 3
}

subcmd_list() {
  local as_json=""
  case "${1:-}" in
    --json) as_json=1 ;;
    "") ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac

  # Build rows: name cli model available note (TAB-separated internally).
  local rows="" flag cli model supports avail note
  while IFS='|' read -r flag cli model supports; do
    [ -n "$cli" ] || continue
    IFS=$'\t' read -r avail note < <(entry_status "$cli" "$model")
    rows+="$cli:$model	$cli	$model	$avail	$note"$'\n'
  done < <(registry_rows)

  if [ -n "$as_json" ]; then
    command -v python3 >/dev/null 2>&1 || { echo "python3 required for --json" >&2; exit 1; }
    printf '%s' "$rows" | python3 -c '
import json, sys
out = []
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    name, cli, model, avail, note = (line.split("\t") + [""] * 5)[:5]
    out.append({"name": name, "cli": cli, "model": model,
                "available": avail == "yes", "note": note})
json.dump(out, sys.stdout, indent=2)
print()
'
    return
  fi

  # Human table. Use column when present; else a plain TSV still renders.
  { printf 'NAME\tCLI\tMODEL\tAVAILABLE\tNOTE\n'
    printf '%s' "$rows" | while IFS=$'\t' read -r name cli model avail note; do
      printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$cli" "$model" "$avail" "${note:--}"
    done
  } | { command -v column >/dev/null 2>&1 && column -t -s $'\t' || cat; }
}

# Read/write one `key=value` line in a state file, preserving other keys.
_kv_get() {
  local file="$1" key="$2"
  [ -n "$file" ] && [ -f "$file" ] || return 0
  sed -n "s/^$key=//p" "$file" | tail -1
}
_kv_set() {
  local file="$1" key="$2" value="$3" tmp
  mkdir -p "$(dirname "$file")"
  tmp="$(mktemp)"
  [ -f "$file" ] && grep -v "^$key=" "$file" >"$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  mv "$tmp" "$file"
}

validate_name() {
  # A stored default/last must map to a real entry, so state can't rot.
  row_for_name "$1" >/dev/null || {
    echo "unknown agent name '$1' (see \`list\`)" >&2; exit 2; }
}

subcmd_default() {
  # default get         → the repo's default agent name, or empty if none set
  #                       (or if the stored name no longer maps to a real entry)
  # default set <name>  → persist it in the project state file
  local op="${1:-get}"; shift || true
  case "$op" in
    get)
      # VALIDATE the stored value against the live registry before handing it to
      # no-flag /kickoff. The project file is committed and travels with a clone,
      # so a stale/removed/garbage (or attacker-supplied) name must NOT route the
      # launch: an unknown name is treated as "no default" → the caller shows the
      # picker, rather than failing every kickoff on a bogus committed value.
      local v; v="$(_kv_get "$PROJECT_STATE" default)"
      if [ -n "$v" ] && row_for_name "$v" >/dev/null 2>&1; then printf '%s\n' "$v"; fi
      ;;
    set)
      local name="${1:-}"
      [ -n "$name" ] || { echo "default set: missing <name>" >&2; exit 2; }
      validate_name "$name"
      [ -n "$PROJECT_STATE" ] || { echo "default set: no project config location (not inside a git repo)" >&2; exit 2; }
      _kv_set "$PROJECT_STATE" default "$name"
      ;;
    *) echo "default: expected get|set" >&2; exit 2 ;;
  esac
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    list)     subcmd_list "$@" ;;
    resolve)  subcmd_resolve "$@" ;;
    default)  subcmd_default "$@" ;;
    -h|--help) usage ;;
    "")       usage ;;
    *)        echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
