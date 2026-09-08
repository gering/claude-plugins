#!/usr/bin/env bash
# mandate.sh — record and read a task's autonomy mandate (MANDATE.md).
#
# A mandate is the *authorization record* for one lane: what the user explicitly
# allowed the worker to do without asking again, and where it must stop. It is
# written once by /kickoff from an answer the user actually gave, and read by
# /continue and pr-flow across process boundaries. (swarm has no mandate
# integration of its own — pr-flow checks `allows local-review` before invoking
# it; a directly invoked /swarm:review does not consult this file.)
#
# Hard rule the format enforces: authorization lives ONLY in MANDATE.md's
# frontmatter. TASK.md prose describes the work, never the consent — a worker
# must never infer permission from a task description, from a prior session, or
# from the mere fact that it was launched. Two mechanisms back that up: only the
# LEADING `---` block is parsed, and a duplicate key anywhere in it is a hard
# error rather than a first-one-wins race (see parse_frontmatter).
#
# Storage: <worktree-root>/MANDATE.md, beside TASK.md (same copy-in mechanics,
# visible and hand-editable). Like TASK.md it is ephemeral worktree state;
# /kickoff adds it to the repo's git exclude rather than relying on every
# consumer repo carrying a .gitignore rule.
#
# Subcommands:
#   path  [<dir>]            Absolute MANDATE.md path for the worktree holding <dir>.
#   show  [<dir>]            Emit key=value lines (always incl. mandate_exists).
#   init  [<dir>] k=v ...    Write the mandate. Refuses to clobber unless --force.
#                            --preset standard|draft-only|merge-delegated seeds
#                            allow/deny/terminal_gate/review_budget; explicit k=v wins.
#   allows <action> [<dir>]  Exit 0 allowed / 1 denied or unlisted / 3 no mandate.
#   round [<dir>]            Consume one review round; emit the new counters.
#   actions                  List the known action vocabulary, one per line.
#
# Output: `key=value` lines on stdout. Exit 0 on success, 2 on usage error,
# 3 when a mandate is required but absent, 4 when a write could not be
# persisted. `allows` additionally uses exit 1 for a denied action — callers
# MUST distinguish 1 (denied: stop) from 3 (unknown: ask the user), never
# collapsing both into "not allowed".
set -eu

MANDATE_FILE="MANDATE.md"
MANDATE_VERSION=1

# Keys carried in the frontmatter, in emit order. `allow`/`deny` are
# comma-separated action lists; everything else is a scalar.
KEYS="mandate_version task recorded_at recorded_by authorized_by scope terminal_gate allow deny review_budget review_rounds_used"

# The canonical action vocabulary. It lives HERE, not in skill prose, because
# `allows` reports an unknown token as `unlisted` (exit 1) — indistinguishable
# from a deliberate denial. A typo would therefore be reported to the caller as
# a decision the user made, which is exactly the 1-vs-3 collapse this contract
# exists to prevent. `init` rejects anything not on this list.
KNOWN_ACTIONS="commit push-own-branch open-pr local-review agreed-fixes rebase-own-branch merge deploy force-push-shared destructive"
KNOWN_GATES="reviewed-pr merged pushed-branch"

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

# Read the whole leading frontmatter block in ONE pass and set FM_<key> for every
# known key. One awk over the file instead of one per key: `show` is on the hot
# path of /continue, /open and every --loop round.
#
# A duplicate key is a hard error, not a last-one-wins merge. `read_key` used to
# take the FIRST match, so a second `allow:` line injected above the real one
# silently won — and MANDATE.md's own `scope` text is model-authored from TASK.md,
# which under /adopt is summarized from someone else's commits. init now refuses
# to write a value containing a newline, and this refuses to *read* a file that
# somehow acquired one anyway.
FM_mandate_version=""; FM_task=""; FM_recorded_at=""; FM_recorded_by=""
FM_authorized_by=""; FM_scope=""; FM_terminal_gate=""; FM_allow=""; FM_deny=""
FM_review_budget=""; FM_review_rounds_used=""
parse_frontmatter() {
  local file="$1" line k v
  FM_mandate_version=""; FM_task=""; FM_recorded_at=""; FM_recorded_by=""
  FM_authorized_by=""; FM_scope=""; FM_terminal_gate=""; FM_allow=""; FM_deny=""
  FM_review_budget=""; FM_review_rounds_used=""
  [ -f "$file" ] || return 0
  local parsed
  parsed="$(awk -v keys=" $KEYS " '
    NR == 1 { if ($0 != "---") exit; infm = 1; next }
    infm && $0 == "---" { exit }
    infm {
      i = index($0, ":")
      if (i == 0) next
      name = substr($0, 1, i - 1)
      gsub(/^[ \t]+|[ \t]+$/, "", name)
      if (index(keys, " " name " ") == 0) next
      if (name in seen) { print "__dup=" name; exit }
      seen[name] = 1
      val = substr($0, i + 1)
      gsub(/^[ \t]+|[ \t]+$/, "", val)
      print name "=" val
    }
  ' "$file")"
  while IFS='=' read -r k v; do
    [ -n "$k" ] || continue
    case "$k" in
      __dup) die "MANDATE.md has a duplicate '$v:' key in its frontmatter — refusing to guess which one is the authorization ($file)" ;;
      mandate_version)    FM_mandate_version="$v" ;;
      task)               FM_task="$v" ;;
      recorded_at)        FM_recorded_at="$v" ;;
      recorded_by)        FM_recorded_by="$v" ;;
      authorized_by)      FM_authorized_by="$v" ;;
      scope)              FM_scope="$v" ;;
      terminal_gate)      FM_terminal_gate="$v" ;;
      allow)              FM_allow="$v" ;;
      deny)               FM_deny="$v" ;;
      review_budget)      FM_review_budget="$v" ;;
      review_rounds_used) FM_review_rounds_used="$v" ;;
    esac
  done <<EOF
$parsed
EOF
}

# Is <action> a member of a comma-separated list? Pure bash, exact token match
# after trimming. It used to be `grep -qx -- "$want"`: anchored, but still a
# REGEX with no -F, so `allows '.*'` matched every list and satisfied the
# documented whole-action guarantee with a metacharacter. No subprocess also
# means an `allows` question costs no forks at all.
list_has() {
  local list="$1" want="$2" field rest
  [ -n "$want" ] || return 1
  rest="$list"
  while [ -n "$rest" ]; do
    case "$rest" in
      *,*) field="${rest%%,*}"; rest="${rest#*,}" ;;
      *)   field="$rest"; rest="" ;;
    esac
    # trim surrounding whitespace
    field="${field#"${field%%[![:space:]]*}"}"
    field="${field%"${field##*[![:space:]]}"}"
    [ "$field" = "$want" ] && return 0
  done
  return 1
}

# Emit review_rounds_left / review_budget_exhausted from a budget+used pair.
# One helper, because `show` and `round` both report exhaustion and callers mix
# the two (pr-flow reads `show` for the cap and `round` per iteration) — two
# copies of the clamp would let them disagree about the same file.
emit_budget() {
  local budget="$1" used="$2" left
  case "$budget" in ''|*[!0-9]*) budget="" ;; esac
  case "$used"   in ''|*[!0-9]*) used=0 ;; esac
  if [ -n "$budget" ]; then
    left=$(( budget - used ))
    [ "$left" -lt 0 ] && left=0
    printf 'review_rounds_left=%s\n' "$left"
    if [ "$left" -eq 0 ]; then printf 'review_budget_exhausted=yes\n'
    else                       printf 'review_budget_exhausted=no\n'; fi
  else
    # No budget recorded is UNKNOWN, never exhausted: /kickoff may legitimately
    # record no limit, and that must not read as "stop reviewing".
    printf 'review_rounds_left=\n'
    printf 'review_budget_exhausted=\n'
  fi
}

do_show() {
  local key val
  resolve_mandate_path "${1:-.}"
  printf 'mandate_file=%s\n' "$MANDATE_PATH"
  if [ ! -f "$MANDATE_PATH" ]; then
    printf 'mandate_exists=no\n'
    return 0
  fi
  printf 'mandate_exists=yes\n'
  parse_frontmatter "$MANDATE_PATH"
  for key in $KEYS; do
    eval "val=\${FM_$key}"
    printf '%s=%s\n' "$key" "$val"
  done
  emit_budget "$FM_review_budget" "$FM_review_rounds_used"
}

do_allows() {
  local action="$1"
  [ -n "$action" ] || die "usage: ${0##*/} allows <action> [<dir>]"
  resolve_mandate_path "${2:-.}"
  # No mandate = no authorization on record. Exit 3 means "unknown, ask the
  # user" — deliberately distinct from 1 ("recorded as out of bounds").
  [ -f "$MANDATE_PATH" ] || { printf 'verdict=no-mandate\n'; exit 3; }
  parse_frontmatter "$MANDATE_PATH"
  if [ -n "$FM_deny" ] && list_has "$FM_deny" "$action"; then
    printf 'verdict=denied\n'; exit 1
  fi
  if [ -n "$FM_allow" ] && list_has "$FM_allow" "$action"; then
    printf 'verdict=allowed\n'; exit 0
  fi
  # Silence is not consent: an action nobody wrote down is unknown, not allowed.
  printf 'verdict=unlisted\n'; exit 1
}

do_round() {
  local budget used tmp
  resolve_mandate_path "${1:-.}"
  [ -f "$MANDATE_PATH" ] || { printf 'verdict=no-mandate\n'; exit 3; }
  parse_frontmatter "$MANDATE_PATH"
  budget="$FM_review_budget"; used="$FM_review_rounds_used"
  case "$used" in ''|*[!0-9]*) used=0 ;; esac
  used=$(( used + 1 ))

  # mktemp beside the file, not a fixed "$file.tmp": the fixed name is a
  # predictable target a pre-existing symlink can redirect, and a failed run left
  # it behind for an autonomous `git add -A` to commit.
  tmp="$(mktemp "${MANDATE_PATH%/*}/.MANDATE.XXXXXX")" \
    || { echo "${0##*/}: could not persist the consumed round — no temp file could be created beside $MANDATE_PATH" >&2; exit 4; }
  trap 'rm -f "$tmp"' EXIT

  # Rewrite the counter in place, keeping prose and any hand-written body. If the
  # key is ABSENT (hand-edited away, or a mandate from another tool), insert it
  # before the closing fence — the previous version substituted only, so the
  # counter silently never persisted and the review budget became unbounded.
  awk -v n="$used" '
    NR == 1 && $0 == "---" { infm = 1; print; next }
    infm && $0 == "---" {
      if (!seen) print "review_rounds_used: " n
      infm = 0; print; next
    }
    infm && $0 ~ /^[ \t]*review_rounds_used[ \t]*:/ { seen = 1; print "review_rounds_used: " n; next }
    { print }
  ' "$MANDATE_PATH" > "$tmp" && mv "$tmp" "$MANDATE_PATH" || {
    # Report the failure instead of printing a consumed round: the old form put
    # the printfs after an `&&` list, so a read-only worktree produced exit 0 and
    # a round the file never recorded — the one thing the persisted counter exists
    # to prevent.
    echo "${0##*/}: could not persist the consumed round to $MANDATE_PATH" >&2
    exit 4
  }
  trap - EXIT

  printf 'review_rounds_used=%s\n' "$used"
  emit_budget "$budget" "$used"
}

# Presets exist because the choice offered at kickoff is one of three named
# grants, and prose around a single hardcoded `init` line wrote the standard
# authorization whichever one the user picked. The triple lives here so the
# selection is what actually reaches the file.
apply_preset() {
  case "$1" in
    standard)
      v_allow="commit,push-own-branch,open-pr,local-review,agreed-fixes,rebase-own-branch"
      v_deny="merge,deploy,force-push-shared,destructive"
      v_terminal_gate="reviewed-pr"; v_review_budget="2" ;;
    draft-only)
      v_allow="commit,push-own-branch"
      v_deny="open-pr,merge,deploy,force-push-shared,destructive"
      v_terminal_gate="pushed-branch"; v_review_budget="0" ;;
    merge-delegated)
      v_allow="commit,push-own-branch,open-pr,local-review,agreed-fixes,rebase-own-branch,merge"
      v_deny="deploy,force-push-shared,destructive"
      v_terminal_gate="merged"; v_review_budget="2" ;;
    *) die "unknown preset: $1 (known: standard, draft-only, merge-delegated)" ;;
  esac
}

# A value is written verbatim into the frontmatter, so a newline in it injects
# further keys — and `scope`/`task` are model-authored from TASK.md, which under
# /adopt is summarized from someone else's commits. Reject every control
# character rather than escaping: a mandate value has no legitimate use for one.
check_value() {
  case "$2" in
    *[[:cntrl:]]*) die "$1 must not contain a newline or control character (it would inject frontmatter keys)" ;;
  esac
}

check_actions() {
  local list="$1" label="$2" field rest
  rest="$list"
  while [ -n "$rest" ]; do
    case "$rest" in
      *,*) field="${rest%%,*}"; rest="${rest#*,}" ;;
      *)   field="$rest"; rest="" ;;
    esac
    field="${field#"${field%%[![:space:]]*}"}"
    field="${field%"${field##*[![:space:]]}"}"
    [ -n "$field" ] || continue
    case " $KNOWN_ACTIONS " in
      *" $field "*) ;;
      *) die "unknown action in $label: $field (known: $KNOWN_ACTIONS)" ;;
    esac
  done
}

do_init() {
  local dir="." force="no" arg key val preset="" want_dir=""
  # Defaults describe the *shape* of a mandate, not consent: /kickoff must fill
  # authorized_by/allow/deny from an answer the user actually gave.
  local v_task="" v_recorded_at="" v_recorded_by="kickoff" v_authorized_by=""
  local v_scope="" v_terminal_gate="reviewed-pr" v_allow="" v_deny=""
  local v_review_budget="" v_review_rounds_used="0"

  # Two passes: --preset seeds the triple, explicit k=v then overrides it, no
  # matter which order they were written in.
  local take_preset="no"
  for arg in "$@"; do
    if [ "$take_preset" = "yes" ]; then preset="$arg"; take_preset="no"; continue; fi
    case "$arg" in
      --preset=*) preset="${arg#--preset=}" ;;
      --preset)   take_preset="yes" ;;
    esac
  done
  [ "$take_preset" = "yes" ] && die "--preset needs a value: --preset <standard|draft-only|merge-delegated>"
  [ -n "$preset" ] && apply_preset "$preset"

  local expect_preset="no"
  for arg in "$@"; do
    if [ "$expect_preset" = "yes" ]; then expect_preset="no"; continue; fi
    case "$arg" in
      --force) force="yes" ;;
      --preset) expect_preset="yes" ;;
      --preset=*) ;;
      *=*)
        key="${arg%%=*}"; val="${arg#*=}"
        check_value "$key" "$val"
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
      *)  want_dir="$arg" ;;
    esac
  done
  [ -n "$want_dir" ] && dir="$want_dir"

  # `--preset` counts as the answer for allow/deny; authorized_by never does.
  [ -n "$v_authorized_by" ] || die "init needs authorized_by= (who granted this — never assume)"
  [ -n "$v_allow" ] || die "init needs allow= or --preset (an empty mandate authorizes nothing)"
  check_actions "$v_allow" "allow"
  check_actions "$v_deny" "deny"
  case " $KNOWN_GATES " in
    *" $v_terminal_gate "*) ;;
    *) die "unknown terminal_gate: $v_terminal_gate (known: $KNOWN_GATES)" ;;
  esac
  if [ -n "$v_review_budget" ]; then
    case "$v_review_budget" in *[!0-9]*) die "review_budget must be a whole number (got: $v_review_budget)" ;; esac
  fi
  case "$v_review_rounds_used" in *[!0-9]*) die "review_rounds_used must be a whole number (got: $v_review_rounds_used)" ;; esac
  [ -n "$v_recorded_at" ] || v_recorded_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  resolve_mandate_path "$dir"
  if [ -f "$MANDATE_PATH" ] && [ "$force" != "yes" ]; then
    parse_frontmatter "$MANDATE_PATH"
    printf 'mandate_file=%s\n' "$MANDATE_PATH"
    printf 'mandate_exists=yes\n'
    printf 'written=no\n'
    printf 'existing_task=%s\n' "$FM_task"
    # A mandate for a DIFFERENT task is not a mandate for this lane. That happens
    # when a consumer repo committed MANDATE.md and a fresh worktree inherited it
    # from main — the previous lane's allow list, its spent budget, and nobody
    # re-asked. Say so loudly; the caller must re-record, not reuse.
    if [ -n "$v_task" ] && [ -n "$FM_task" ] && [ "$v_task" != "$FM_task" ]; then
      printf 'task_mismatch=yes\n'
      echo "${0##*/}: the existing mandate was recorded for task '$FM_task', not '$v_task' — it does not authorize this lane; re-record with --force after asking the user" >&2
      exit 2
    fi
    printf 'task_mismatch=no\n'
    echo "${0##*/}: a mandate already exists — re-record only with --force" >&2
    exit 2
  fi

  cat > "$MANDATE_PATH" <<EOF
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
narrow the mandate; \`/kickoff\` never rewrites an existing one. Keep each value
on one line and each key unique — a duplicate key is refused rather than
resolved.
EOF

  printf 'mandate_file=%s\n' "$MANDATE_PATH"
  printf 'mandate_exists=yes\n'
  printf 'written=yes\n'
}

case "${1:-}" in
  path)    shift || true; resolve_mandate_path "${1:-.}"; printf '%s\n' "$MANDATE_PATH" ;;
  show)    shift || true; do_show "${1:-.}" ;;
  init)    shift || true; do_init "$@" ;;
  allows)  shift || true; do_allows "${1:-}" "${2:-.}" ;;
  round)   shift || true; do_round "${1:-.}" ;;
  actions) printf '%s\n' $KNOWN_ACTIONS ;;
  *) echo "usage: ${0##*/} {path|show|init|allows|round|actions} [...]" >&2; exit 2 ;;
esac
