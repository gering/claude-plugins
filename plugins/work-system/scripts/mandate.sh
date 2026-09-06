#!/usr/bin/env bash
# mandate.sh — record and read a task's autonomy mandate (MANDATE.md).
#
# A mandate is the *authorization record* for one lane: what the user explicitly
# allowed the worker to do without asking again, and where it must stop. It is
# written once by /kickoff from an answer the user actually gave, and read by
# /continue, pr-flow and swarm across process boundaries.
#
# Hard rule the format enforces: authorization lives ONLY in MANDATE.md's
# frontmatter. TASK.md prose describes the work, never the consent — a worker
# must never infer permission from a task description, from a prior session, or
# from the mere fact that it was launched.
#
# Storage: <worktree-root>/MANDATE.md, beside TASK.md (same copy-in mechanics,
# visible and hand-editable). Like TASK.md it is ephemeral worktree state and
# belongs in the consumer repo's .gitignore.
#
# Subcommands:
#   path  [<dir>]            Absolute MANDATE.md path for the worktree holding <dir>.
#   show  [<dir>]            Emit key=value lines (always incl. mandate_exists).
#   init  [<dir>] k=v ...    Write the mandate. Refuses to clobber unless --force.
#   allows <action> [<dir>]  Exit 0 allowed / 1 denied / 3 no mandate recorded.
#   round [<dir>]            Consume one review round; emit the new counters.
#
# Output: `key=value` lines on stdout. Exit 0 on success, 2 on usage error,
# 3 when a mandate is required but absent. `allows` additionally uses exit 1 for
# a denied action — callers MUST distinguish 1 (denied: stop) from 3 (unknown:
# ask the user), never collapsing both into "not allowed".
set -eu

MANDATE_FILE="MANDATE.md"
MANDATE_VERSION=1

# Keys carried in the frontmatter, in emit order. `allow`/`deny` are
# comma-separated action lists; everything else is a scalar.
KEYS="mandate_version task recorded_at recorded_by authorized_by scope terminal_gate allow deny review_budget review_rounds_used"

die() { echo "${0##*/}: $*" >&2; exit 2; }

# Resolve MANDATE.md for the worktree holding <dir> into MANDATE_PATH (works
# from the main repo and from linked worktrees). Sets a global instead of
# printing: called inside `$( )` a failing `die` would only kill the subshell,
# and the caller would silently continue with a truncated path like /MANDATE.md.
MANDATE_PATH=""
resolve_mandate_path() {
  local dir="${1:-.}" root
  [ -d "$dir" ] || die "not a directory: $dir"
  root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || true
  [ -n "$root" ] || die "not inside a git repository: $dir"
  MANDATE_PATH="$root/$MANDATE_FILE"
}

# Read one frontmatter key. Only the leading `---` block is parsed, so prose in
# the body can mention "merge:" or "scope:" without becoming authorization.
read_key() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  awk -v k="$key" '
    NR == 1 { if ($0 != "---") exit; infm = 1; next }
    infm && $0 == "---" { exit }
    infm {
      i = index($0, ":")
      if (i == 0) next
      name = substr($0, 1, i - 1)
      gsub(/^[ \t]+|[ \t]+$/, "", name)
      if (name != k) next
      val = substr($0, i + 1)
      gsub(/^[ \t]+|[ \t]+$/, "", val)
      print val
      exit
    }
  ' "$file"
}

# Is <action> a member of a comma-separated list? Exact match after trimming;
# no substring matching, so "merge" can never be satisfied by "no-merge".
list_has() {
  local list="$1" want="$2"
  printf '%s\n' "$list" | tr ',' '\n' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g' \
    | grep -qx -- "$want"
}

do_show() {
  local file key val budget used left
  resolve_mandate_path "${1:-.}"; file="$MANDATE_PATH"
  printf 'mandate_file=%s\n' "$file"
  if [ ! -f "$file" ]; then
    printf 'mandate_exists=no\n'
    return 0
  fi
  printf 'mandate_exists=yes\n'
  for key in $KEYS; do
    val="$(read_key "$file" "$key")"
    printf '%s=%s\n' "$key" "$val"
  done
  budget="$(read_key "$file" review_budget)"
  used="$(read_key "$file" review_rounds_used)"
  case "$budget" in ''|*[!0-9]*) budget="" ;; esac
  case "$used"   in ''|*[!0-9]*) used=0 ;; esac
  if [ -n "$budget" ]; then
    left=$(( budget - used ))
    [ "$left" -lt 0 ] && left=0
    printf 'review_rounds_left=%s\n' "$left"
    if [ "$left" -eq 0 ]; then printf 'review_budget_exhausted=yes\n'
    else                       printf 'review_budget_exhausted=no\n'; fi
  else
    printf 'review_rounds_left=\n'
    printf 'review_budget_exhausted=\n'
  fi
}

do_allows() {
  local action="$1" file allow deny
  [ -n "$action" ] || die "usage: ${0##*/} allows <action> [<dir>]"
  resolve_mandate_path "${2:-.}"; file="$MANDATE_PATH"
  # No mandate = no authorization on record. Exit 3 means "unknown, ask the
  # user" — deliberately distinct from 1 ("recorded as out of bounds").
  [ -f "$file" ] || { printf 'verdict=no-mandate\n'; exit 3; }
  deny="$(read_key "$file" deny)"
  if [ -n "$deny" ] && list_has "$deny" "$action"; then
    printf 'verdict=denied\n'; exit 1
  fi
  allow="$(read_key "$file" allow)"
  if [ -n "$allow" ] && list_has "$allow" "$action"; then
    printf 'verdict=allowed\n'; exit 0
  fi
  # Silence is not consent: an action nobody wrote down is unknown, not allowed.
  printf 'verdict=unlisted\n'; exit 1
}

do_round() {
  local file budget used left
  resolve_mandate_path "${1:-.}"; file="$MANDATE_PATH"
  [ -f "$file" ] || { printf 'verdict=no-mandate\n'; exit 3; }
  budget="$(read_key "$file" review_budget)"
  used="$(read_key "$file" review_rounds_used)"
  case "$budget" in ''|*[!0-9]*) budget="" ;; esac
  case "$used"   in ''|*[!0-9]*) used=0 ;; esac
  used=$(( used + 1 ))
  # Rewrite the counter in place; the rest of the file (prose, ledger) is kept.
  awk -v n="$used" '
    NR == 1 && $0 == "---" { infm = 1; print; next }
    infm && $0 == "---" { infm = 0; print; next }
    infm && $0 ~ /^[ \t]*review_rounds_used[ \t]*:/ { print "review_rounds_used: " n; next }
    { print }
  ' "$file" > "$file.tmp" && mv "$file.tmp" "$file"
  printf 'review_rounds_used=%s\n' "$used"
  if [ -n "$budget" ]; then
    left=$(( budget - used ))
    [ "$left" -lt 0 ] && left=0
    printf 'review_rounds_left=%s\n' "$left"
    if [ "$left" -eq 0 ]; then printf 'review_budget_exhausted=yes\n'
    else                       printf 'review_budget_exhausted=no\n'; fi
  else
    printf 'review_rounds_left=\n'
    printf 'review_budget_exhausted=\n'
  fi
}

do_init() {
  local dir="." force="no" file arg key val
  # Defaults describe the *shape* of a mandate, not consent: /kickoff must fill
  # authorized_by/allow/deny from an answer the user actually gave.
  local v_task="" v_recorded_at="" v_recorded_by="kickoff" v_authorized_by=""
  local v_scope="" v_terminal_gate="reviewed-pr" v_allow="" v_deny=""
  local v_review_budget="" v_review_rounds_used="0"

  for arg in "$@"; do
    case "$arg" in
      --force) force="yes" ;;
      *=*)
        key="${arg%%=*}"; val="${arg#*=}"
        case "$key" in
          task)               v_task="$val" ;;
          recorded_at)        v_recorded_at="$val" ;;
          recorded_by)        v_recorded_by="$val" ;;
          authorized_by)      v_authorized_by="$val" ;;
          scope)              v_scope="$val" ;;
          terminal_gate)      v_terminal_gate="$val" ;;
          allow)              v_allow="$val" ;;
          deny)               v_deny="$val" ;;
          review_budget)      v_review_budget="$val" ;;
          review_rounds_used) v_review_rounds_used="$val" ;;
          *) die "unknown key: $key" ;;
        esac ;;
      -*) die "unknown flag: $arg" ;;
      *)  dir="$arg" ;;
    esac
  done

  [ -n "$v_authorized_by" ] || die "init needs authorized_by= (who granted this — never assume)"
  [ -n "$v_allow" ] || die "init needs allow= (an empty mandate authorizes nothing)"
  [ -n "$v_recorded_at" ] || v_recorded_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  resolve_mandate_path "$dir"; file="$MANDATE_PATH"
  if [ -f "$file" ] && [ "$force" != "yes" ]; then
    printf 'mandate_file=%s\n' "$file"
    printf 'mandate_exists=yes\n'
    printf 'written=no\n'
    echo "${0##*/}: a mandate already exists — re-record only with --force" >&2
    exit 2
  fi

  cat > "$file" <<EOF
---
mandate_version: $MANDATE_VERSION
task: $v_task
recorded_at: $v_recorded_at
recorded_by: $v_recorded_by
authorized_by: $v_authorized_by
scope: $v_scope
terminal_gate: $v_terminal_gate
allow: $v_allow
deny: $v_deny
review_budget: $v_review_budget
review_rounds_used: $v_review_rounds_used
---

# Mandate — ${v_task:-this task}

Authorization for this worktree, recorded at kickoff. The frontmatter above is
the machine-read record; this body is for humans.

- **Scope:** ${v_scope:-see TASK.md}
- **Terminal gate:** $v_terminal_gate
- **Pre-authorized:** $v_allow
- **Never without new authorization:** ${v_deny:-(nothing recorded)}
- **Review budget:** ${v_review_budget:-unbounded} round(s)

Anything not listed under \`allow\` is unlisted, and unlisted is not consent —
ask before doing it. Editing this file by hand is a legitimate way to widen or
narrow the mandate; \`/kickoff\` never rewrites an existing one.
EOF

  printf 'mandate_file=%s\n' "$file"
  printf 'mandate_exists=yes\n'
  printf 'written=yes\n'
}

case "${1:-}" in
  path)   shift || true; resolve_mandate_path "${1:-.}"; printf '%s\n' "$MANDATE_PATH" ;;
  show)   shift || true; do_show "${1:-.}" ;;
  init)   shift || true; do_init "$@" ;;
  allows) shift || true; do_allows "${1:-}" "${2:-.}" ;;
  round)  shift || true; do_round "${1:-.}" ;;
  *) echo "usage: ${0##*/} {path|show|init|allows|round} [...]" >&2; exit 2 ;;
esac
