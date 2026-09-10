#!/usr/bin/env bash
# claude-review.sh — shared helper for pr-flow skills
#
# Subcommands:
#   poll <PR> <SINCE_ISO> [--max N] [--interval S]
#       Poll the PR for a completed Claude review created after SINCE_ISO.
#       Prints the review body on success, "TIMEOUT" and exits 1 on timeout.
#       Default: 20 iterations, 30s interval (= 10 minutes max).
#
#   latest <PR> [--json]
#       Print the body of the latest Claude review comment (any status).
#       With --json: print {createdAt, body} as JSON instead of raw body.
#       Prints empty string (or empty JSON) if no Claude comments exist.
#
#   latest-after <PR> <SINCE_ISO> [--json]
#       Like `latest`, but only considers comments created after SINCE_ISO.
#
#   has-bot [<dir>]
#       Does this repo have a comment-triggered Claude review workflow?
#       Always emits the same four keys: has_bot=yes|no|unknown, why=,
#       workflows_dir=, matched= (empty when not applicable). Anchored on the
#       repo ROOT (git rev-parse), never $PWD: /cycle can legitimately run from
#       a subdirectory or from the main repo while the PR belongs to a
#       worktree, and a cwd-relative probe reports "no bot" there.
#         yes      a top-level workflow both `uses:` anthropics/claude-code-action
#                  AND is triggered by issue_comment, as structure (not in a
#                  comment or a run: block) — @claude review will reach it
#         no       workflow files exist and none can answer a comment (nothing
#                  references the bot, or only push-triggered claude workflows)
#         unknown  could not tell: no git repo, unreadable workflows dir, NO
#                  workflows dir at all (a repo served only by the Claude GitHub
#                  App looks exactly like that), a comment-triggered workflow
#                  that mentions @claude without using the action or delegates
#                  to a reusable workflow, or a custom trigger_phrase
#       unknown is its own answer — callers must ASK, never reroute on it.
#
# Exit codes:
#   0 = success (output contains the body, possibly empty for `latest`)
#   1 = timeout (poll) or error
#   2 = invalid arguments
#   has-bot exits 0 for every answer, unknown included — the verdict is the
#   has_bot= line, never the status.

set -euo pipefail

usage() {
  # Every comment line after the shebang, up to the first non-comment line — the
  # header IS the usage text, so it must not be truncated by a hardcoded range.
  awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "$0"
  exit 2
}

require_gh() {
  command -v gh >/dev/null || { echo "gh CLI not installed" >&2; exit 1; }
  gh auth status >/dev/null 2>&1 || { echo "gh not authenticated — run: gh auth login" >&2; exit 1; }
}

subcmd_latest() {
  local pr="${1:-}"
  [[ -z "$pr" ]] && usage
  local as_json=false
  [[ "${2:-}" == "--json" ]] && as_json=true
  require_gh
  if $as_json; then
    gh pr view "$pr" --json comments \
      --jq '[.comments[] | select(.author.login == "claude")] | last | {createdAt: (.createdAt // ""), body: (.body // "")}'
  else
    gh pr view "$pr" --json comments \
      --jq '[.comments[] | select(.author.login == "claude")] | last | .body // ""'
  fi
}

subcmd_latest_after() {
  local pr="${1:-}" since="${2:-}"
  [[ -z "$pr" || -z "$since" ]] && usage
  local as_json=false
  [[ "${3:-}" == "--json" ]] && as_json=true
  require_gh
  # Strip fractional seconds from .createdAt to match the whole-second
  # precision of $since (produced by `date -u +%Y-%m-%dT%H:%M:%SZ`). Without
  # this, a comment created within the same whole second as the trigger
  # compares lexicographically as earlier ("56.5Z" < "56Z") and is missed.
  if $as_json; then
    gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select((.createdAt | sub(\"\\\\.[0-9]+Z$\"; \"Z\")) > \"$since\")] | last | {createdAt: (.createdAt // \"\"), body: (.body // \"\")}"
  else
    gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select((.createdAt | sub(\"\\\\.[0-9]+Z$\"; \"Z\")) > \"$since\")] | last | .body // \"\""
  fi
}

subcmd_poll() {
  local pr="${1:-}" since="${2:-}"
  [[ -z "$pr" || -z "$since" ]] && usage
  shift 2 || true

  local max_iters=20
  local interval=30
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max)      max_iters="$2"; shift 2 ;;
      --interval) interval="$2";  shift 2 ;;
      *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
  done

  require_gh

  for ((i=1; i<=max_iters; i++)); do
    sleep "$interval"
    local body
    # Strip fractional seconds from .createdAt for whole-second comparison
    # with $since (see note in subcmd_latest_after).
    body=$(gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select((.createdAt | sub(\"\\\\.[0-9]+Z$\"; \"Z\")) > \"$since\")] | last | .body // \"\"")

    if [[ -n "$body" ]]; then
      # "Claude Code is working" = in-progress marker; keep polling
      if [[ "$body" == *"Claude Code is working"* ]]; then
        continue
      fi
      # "**Claude finished" = completion marker
      if [[ "$body" == *"**Claude finished"* ]]; then
        printf '%s\n' "$body"
        exit 0
      fi
    fi
  done

  echo "TIMEOUT" >&2
  exit 1
}

# Detect a review bot without a network call. "Bot" means: a workflow that
# `uses:` anthropics/claude-code-action AND is triggered by issue_comment — the
# two together are what `@claude review` needs. Either alone is not a bot: a
# push-triggered claude workflow never sees the comment, and a comment-triggered
# workflow that merely mentions @claude in a doc line consumes nothing.
#
# The old inline form was a bare `grep -rlie claude .github/workflows/` copied
# into two SKILL.md files: cwd-relative, and loose enough that a cache key
# counted as a bot. The first replacement here round-tripped the file list
# through an unquoted `$hits`, so any checkout under a path with a space ("My
# Projects", iCloud) reported has_bot=no for a working bot. Paths are now
# NUL-delimited end to end and every grep gets `--` before its operand.
#
# What cannot be told locally is reported as `unknown`, not guessed: no
# workflows dir at all (a repo served only by the Claude GitHub App has none),
# an unreadable dir, or a comment workflow with a loose @claude mention. The
# caller asks in those cases — asking once beats ten minutes of polling a bot
# that is not there, and beats permanently rerouting one that is.
# Classify ONE workflow file in a single pass. Emits five 0/1 flags:
#   action   a structural `uses: anthropics/claude-code-action` step
#   comment  a structural issue_comment trigger
#   phrase   a trigger_phrase that is not @claude (the comment never fires it)
#   reusable a job-level `uses:` of another workflow file (cannot see inside)
#   mention  the bot's name anywhere at all — comments and scalars included
# "Structural" means outside a `#` comment and outside a block scalar (`run: |`),
# where the same text is a string GitHub never interprets: the substring greps
# this replaces reported a TODO comment and a shell heredoc as a working bot.
# `mention` deliberately stays raw — it only ever lowers the answer to unknown.
read -r -d '' HAS_BOT_AWK <<'AWK' || true
{
  raw = $0; sub(/\r$/, "", raw)
  if (tolower(raw) ~ /anthropics\/claude-code-action|@claude/) mention = 1
  match(raw, /^[ \t]*/); ind = RLENGTH
  if (inblock) {
    if (raw ~ /^[ \t]*$/ || ind > bind) next
    inblock = 0
  }
  line = raw
  sub(/^#.*/, "", line); sub(/[ \t]#.*/, "", line)
  if (line ~ /^[ \t]*-?[ \t]*[A-Za-z0-9_.-]+:[ \t]*[|>][-+0-9]*[ \t]*$/) { inblock = 1; bind = ind; next }
  tl = tolower(line)
  if (tl ~ /^[ \t]*-?[ \t]*uses:[ \t]*["']?anthropics\/claude-code-action/) action = 1
  else if (tl ~ /^[ \t]*uses:[ \t]*["']?[^"' \t]+\.ya?ml(@|["' \t]|$)/) reusable = 1
  if (tl ~ /^[ \t]*-?[ \t]*issue_comment[ \t]*(:.*)?$/ || tl ~ /^[ \t]*on:[ \t]*(\[.*issue_comment|issue_comment[ \t]*$)/) comment = 1
  if (tl ~ /^[ \t]*trigger_phrase:/) {
    v = tl; sub(/^[ \t]*trigger_phrase:[ \t]*/, "", v)
    if (v !~ /@claude/) phrase = 1
  }
}
END { printf "%d %d %d %d %d\n", action, comment, phrase, reusable, mention }
AWK

subcmd_has_bot() {
  local dir="${1:-.}" root wf="" f a c p r m
  local strict="" phrase="" loose="" reusable="" pushonly="" n_files=0
  # One emitter, so every path prints the same four keys in the same order —
  # a consumer that greps a promised key must never get silence on some verdicts.
  emit() {
    echo "has_bot=$1"
    echo "why=$2"
    echo "workflows_dir=$wf"
    echo "matched=${3:-}"
  }
  root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$root" ]]; then
    emit unknown "not inside a git repository"; return 0
  fi
  wf="$root/.github/workflows"
  if [[ ! -e "$wf" ]]; then
    emit unknown "no .github/workflows directory — a repo with no CI, or one served only by the Claude GitHub App; cannot tell locally"; return 0
  fi
  if [[ ! -d "$wf" || ! -r "$wf" || ! -x "$wf" ]]; then
    emit unknown ".github/workflows exists but is not readable"; return 0
  fi
  # Top level only (-maxdepth 1): GitHub reads workflows from this directory
  # itself, never from a subdirectory — an archived copy under workflows/old/
  # is not a bot. NUL-delimited so a path with spaces, globs or newlines stays
  # one path.
  while IFS= read -r -d '' f; do
    n_files=$(( n_files + 1 ))
    [[ -r "$f" ]] || { emit unknown "unreadable workflow file: $f"; return 0; }
    read -r a c p r m <<<"$(awk "$HAS_BOT_AWK" "$f")"
    if (( a && c )); then
      if (( p )); then phrase="$phrase$f "; else strict="$strict$f "; fi
    elif (( a )); then
      pushonly="$pushonly$f "
    elif (( c && r )); then
      reusable="$reusable$f "
    elif (( c && m )); then
      loose="$loose$f "
    fi
  done < <(find "$wf" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print0 2>/dev/null)

  if (( n_files == 0 )); then
    emit unknown ".github/workflows holds no workflow files — same as no directory; cannot tell locally"; return 0
  fi
  if [[ -n "$strict" ]]; then
    emit yes "a comment-triggered workflow uses anthropics/claude-code-action" "$strict"; return 0
  fi
  if [[ -n "$phrase" ]]; then
    emit unknown "a comment-triggered claude workflow sets a custom trigger_phrase — @claude review may not fire it" "$phrase"; return 0
  fi
  if [[ -n "$reusable" ]]; then
    emit unknown "a comment-triggered workflow delegates to a reusable workflow — cannot see locally whether that is the review bot" "$reusable"; return 0
  fi
  if [[ -n "$loose" ]]; then
    emit unknown "a comment-triggered workflow mentions the bot but does not use anthropics/claude-code-action — cannot tell whether @claude review reaches anything" "$loose"; return 0
  fi
  if [[ -n "$pushonly" ]]; then
    emit no "claude workflow(s) present but none triggered by issue_comment" "$pushonly"; return 0
  fi
  emit no "no workflow references the review bot"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    poll)          subcmd_poll "$@" ;;
    latest)        subcmd_latest "$@" ;;
    latest-after)  subcmd_latest_after "$@" ;;
    has-bot)       subcmd_has_bot "$@" ;;
    ""|-h|--help)  usage ;;
    *)             echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
