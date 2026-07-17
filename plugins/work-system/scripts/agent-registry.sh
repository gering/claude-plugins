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
#   auto                          Print the first AVAILABLE entry name per the
#                                 ranking (exit 1 if none available).
#   default get | default set <name>
#   last    get | last    set <name>
#                                 Persisted selection state (see STATE below).
#   rank                          Print the effective --auto ranking, one name
#                                 per line.
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
#   WORK_SYSTEM_AGENT_STATE      selection state file (default/last)
#                                default: ~/.claude/work-system-agent
#   WORK_SYSTEM_AGENT_RANK       inline ranking (whitespace-separated names)
#   WORK_SYSTEM_AGENT_RANK_FILE  ranking file, one name per line, # comments
#                                default: ~/.claude/work-system-agent-rank
#
# Exit codes: 0 ok · 1 not-available / no-op · 2 usage / unknown selector ·
#             3 resolved but the entry's CLI is unavailable
set -euo pipefail

HOME="${HOME:-$(cd ~ 2>/dev/null && pwd || echo /nonexistent)}"
STATE_FILE="${WORK_SYSTEM_AGENT_STATE:-$HOME/.claude/work-system-agent}"
RANK_FILE="${WORK_SYSTEM_AGENT_RANK_FILE:-$HOME/.claude/work-system-agent-rank}"
GROK_AUTH_FILE="${GROK_AUTH_FILE:-$HOME/.grok/auth.json}"

# The bootstrap prompt for CLIs without work-system skills (codex, grok). One
# argv word; the launch helper passes it verbatim.
BOOTSTRAP_PROMPT='Read TASK.md in this worktree and continue the task. Commit on the current branch as you go, and open a PR when the work is complete.'

# ---------- registry ----------
# `flag|cli|model|supports`. flag `-` = no shorthand (name/--agent only). The
# FIRST entry of each CLI is that CLI's default model (for a bare `--agent codex`).
# `supports` is per-agent capability metadata consumed by the picker (annotate
# non-claude limits) and the /close + /continue degradation paths:
#   continue   -> `/continue`-reopen + `claude -c` session resume work
#   close-exit -> /close may inject `/exit` for a clean self-teardown
#   statusline -> the `[ws]` statusline segment tracks its session
# codex/grok get commit,pr only — they drive git + a PR but have none of the
# claude-session lifecycle hooks.
REGISTRY='--fable|claude|fable|continue,close-exit,statusline,commit,pr
--opus|claude|opus|continue,close-exit,statusline,commit,pr
-|claude|sonnet|continue,close-exit,statusline,commit,pr
--codex|codex|gpt-5.6-terra|commit,pr
--sol|codex|gpt-5.6-sol|commit,pr
--grok|grok|grok-4.5|commit,pr'

# Shipped default --auto ranking (first available wins). Deterministic, not an
# LLM judgment; override via WORK_SYSTEM_AGENT_RANK[_FILE]. This list is also the
# hook where future task-aware routing plugs in.
DEFAULT_RANK='claude:fable claude:opus codex:gpt-5.6-sol codex:gpt-5.6-terra grok:grok-4.5'

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

# grok's model list, memoized for the process (it may be a network call — fetch
# at most once, with a short timeout so `list` never hangs on it).
_grok_models_done=""
_grok_models=""
grok_model_list() {
  if [ -z "$_grok_models_done" ]; then
    _grok_models_done=1
    local raw
    if command -v timeout >/dev/null 2>&1; then
      raw="$(timeout 10 grok models 2>/dev/null || true)"
    else
      raw="$(grok models 2>/dev/null || true)"
    fi
    # Lines look like "  * grok-4.5 (default)" — take the id after the bullet.
    _grok_models="$(printf '%s\n' "$raw" | awk '/^[[:space:]]*\*/ {print $2}')"
  fi
  printf '%s\n' "$_grok_models"
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
      if ! command -v codex >/dev/null 2>&1; then note="not installed"
      elif codex login status >/dev/null 2>&1; then avail=yes
      else note="run: codex login"; fi
      ;;
    grok)
      if ! command -v grok >/dev/null 2>&1; then note="not installed"
      elif [ ! -s "$GROK_AUTH_FILE" ]; then note="run: grok login"
      elif grok_model_list | grep -qxF "$model"; then avail=yes
      else note="model not offered by this grok CLI (see: grok models)"; fi
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

effective_rank() {
  # Print the ranking, one name per line: env override > file > shipped default.
  # Split on whitespace only — a name is `cli:model`, so the colon is part of the
  # name, never a separator.
  if [ -n "${WORK_SYSTEM_AGENT_RANK:-}" ]; then
    printf '%s\n' $WORK_SYSTEM_AGENT_RANK
  elif [ -f "$RANK_FILE" ]; then
    grep -vE '^\s*(#|$)' "$RANK_FILE" | awk '{print $1}'
  else
    printf '%s\n' $DEFAULT_RANK
  fi
}

subcmd_rank() { effective_rank; }

subcmd_auto() {
  local name flag cli model supports record avail note
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    record="$(row_for_name "$name")" || continue   # skip unknown names in a custom rank
    IFS='|' read -r flag cli model supports <<<"$record"
    IFS=$'\t' read -r avail note < <(entry_status "$cli" "$model")
    if [ "$avail" = yes ]; then
      printf '%s\n' "$name"
      return 0
    fi
  done < <(effective_rank)
  echo "no available agent in the ranking" >&2
  exit 1
}

state_get() {
  # $1 = key (default|last). Prints the stored value or nothing.
  local key="$1"
  [ -f "$STATE_FILE" ] || return 0
  sed -n "s/^$key=//p" "$STATE_FILE" | tail -1
}

state_set() {
  # $1 = key, $2 = value. Rewrites the one key, preserving the other.
  local key="$1" value="$2" other other_val
  case "$key" in default) other=last ;; last) other=default ;; esac
  other_val="$(state_get "$other")"
  mkdir -p "$(dirname "$STATE_FILE")"
  {
    printf '%s=%s\n' "$key" "$value"
    [ -n "$other_val" ] && printf '%s=%s\n' "$other" "$other_val"
  } >"$STATE_FILE"
}

subcmd_state() {
  local key="$1"; shift   # default | last
  local op="${1:-get}"
  case "$op" in
    get) state_get "$key" ;;
    set)
      local name="${2:-}"
      [ -n "$name" ] || { echo "$key set: missing <name>" >&2; exit 2; }
      # Validate against a real entry so state can't rot to a bogus name.
      row_for_name "$name" >/dev/null || {
        echo "$key set: unknown agent name '$name' (see \`list\`)" >&2; exit 2; }
      state_set "$key" "$name"
      ;;
    *) echo "$key: expected get|set" >&2; exit 2 ;;
  esac
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    list)     subcmd_list "$@" ;;
    resolve)  subcmd_resolve "$@" ;;
    auto)     subcmd_auto "$@" ;;
    default)  subcmd_state default "$@" ;;
    last)     subcmd_state last "$@" ;;
    rank)     subcmd_rank "$@" ;;
    -h|--help) usage ;;
    "")       usage ;;
    *)        echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
