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
#       Emits has_bot=yes|no|unknown plus workflows_dir / matched. Anchored on
#       the repo ROOT (git rev-parse), never $PWD: /cycle can legitimately run
#       from a subdirectory or from the main repo while the PR belongs to a
#       worktree, and a cwd-relative probe reports "no bot" there.
#       has_bot=unknown means "could not tell" (no git repo, unreadable dir) —
#       callers must ASK, not silently reroute.
#
# Exit codes:
#   0 = success (output contains the body, possibly empty for `latest`)
#   1 = timeout (poll) or error
#   2 = invalid arguments

set -euo pipefail

usage() {
  sed -n '2,20p' "$0" | sed 's|^# \{0,1\}||'
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

# Detect a review bot without a network call. Two signals, both cheap:
#   1. a workflow that reacts to comments (`on: issue_comment`) — that is what
#      `@claude review` actually needs;
#   2. any workflow mentioning the bot at all.
# The old inline form was a bare `grep -rlie claude .github/workflows/` copied
# into two SKILL.md files: cwd-relative, and loose enough that a cache key or a
# comment mentioning claude counted as a bot.
#
# Known limitation, deliberately surfaced rather than hidden: a repo driven only
# by the Claude GitHub App with no workflow file of its own reports has_bot=no.
# Detecting that needs an authenticated API round-trip on every call; callers are
# told to present the local route as an offer, not to act on it silently.
subcmd_has_bot() {
  local dir="${1:-.}" root wf hits comment_hits
  root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$root" ]]; then
    echo "has_bot=unknown"
    echo "why=not inside a git repository"
    return 0
  fi
  wf="$root/.github/workflows"
  echo "workflows_dir=$wf"
  if [[ ! -d "$wf" ]]; then
    echo "has_bot=no"
    echo "why=no .github/workflows directory"
    return 0
  fi
  hits="$(grep -rlie 'anthropics/claude-code-action\|@claude' "$wf" 2>/dev/null | head -5 || true)"
  comment_hits=""
  if [[ -n "$hits" ]]; then
    # Narrow to workflows that can actually be triggered by a PR comment.
    comment_hits="$(grep -rle 'issue_comment' $hits 2>/dev/null | head -5 || true)"
  fi
  if [[ -n "$comment_hits" ]]; then
    echo "has_bot=yes"
    echo "matched=$(printf '%s' "$comment_hits" | tr '\n' ' ')"
  elif [[ -n "$hits" ]]; then
    # A claude workflow exists but nothing reacts to comments — e.g. a
    # push-triggered review. `@claude review` would not fire it.
    echo "has_bot=no"
    echo "why=claude workflow(s) present but none triggered by issue_comment"
    echo "matched=$(printf '%s' "$hits" | tr '\n' ' ')"
  else
    echo "has_bot=no"
    echo "why=no workflow references the review bot"
  fi
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
