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
  if $as_json; then
    gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select(.createdAt > \"$since\")] | last | {createdAt: (.createdAt // \"\"), body: (.body // \"\")}"
  else
    gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select(.createdAt > \"$since\")] | last | .body // \"\""
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
    body=$(gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select(.createdAt > \"$since\")] | last | .body // \"\"")

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

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    poll)          subcmd_poll "$@" ;;
    latest)        subcmd_latest "$@" ;;
    latest-after)  subcmd_latest_after "$@" ;;
    ""|-h|--help)  usage ;;
    *)             echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
