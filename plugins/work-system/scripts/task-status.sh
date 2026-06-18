#!/usr/bin/env bash
# task-status.sh — resolve a work-system task's branch and completion status.
#
# Encapsulates the git/gh logic that /status, /close and /continue otherwise
# carry as drift-prone prose: main-branch detection, task-branch resolution
# (the current branch when inside a worktree, or task/<name> with fallbacks
# when given a name), offline-safe main-ref handling, and squash/rebase-aware
# merge detection. Resolution reads branch names via `--format` so it never
# trips on the `* `/leading-space prefixes of plain `git branch` output.
#
# Run from inside the repo or any linked worktree — branches/refs are shared.
#
# Subcommands:
#   resolve [<task-name>]   Fast, no network. Emits: main_branch, on_main,
#                           task_name, task_branch, branch_scope, branch_exists.
#   assess  [<task-name>]   resolve + pr_state, pr_number, pr_url (via gh when
#                           available), branch_merged, commits_in_main, verdict,
#                           confidence.
#
# Output: `key=value` lines on stdout (empty value = unknown/none). Callers read
# the keys they need. Exits 0 on success, 2 on a bad subcommand, non-zero when
# not inside a git repository.
set -eu

PREFIXES_RE='^(task|feature|fix|bugfix|hotfix|chore|refactor)/'

strip_prefix() { printf '%s\n' "$1" | sed -E "s#${PREFIXES_RE}##"; }
ref_exists()   { git rev-parse --verify --quiet "$1" >/dev/null 2>&1; }

detect_main_branch() {
  local m c
  m="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || true)"
  if [ -n "$m" ]; then printf '%s\n' "$m"; return 0; fi
  for c in main master; do
    if ref_exists "refs/heads/$c"; then printf '%s\n' "$c"; return 0; fi
  done
  printf 'main\n'
}

# Sets RESOLVED_SCOPE (local|remote|none) and RESOLVED_BRANCH (clean short name,
# or task/<name> by convention when nothing is found) for a given task name.
RESOLVED_SCOPE=""; RESOLVED_BRANCH=""
resolve_named_branch() {
  local name="$1" b
  RESOLVED_SCOPE="none"; RESOLVED_BRANCH=""
  [ -z "$name" ] && return 0
  if ref_exists "refs/heads/task/$name"; then RESOLVED_SCOPE="local";  RESOLVED_BRANCH="task/$name"; return 0; fi
  if ref_exists "refs/heads/$name";      then RESOLVED_SCOPE="local";  RESOLVED_BRANCH="$name";      return 0; fi
  b="$(git branch --list --format='%(refname:short)' | grep -i -m1 -F -- "$name" || true)"
  if [ -n "$b" ]; then RESOLVED_SCOPE="local"; RESOLVED_BRANCH="$b"; return 0; fi
  if ref_exists "refs/remotes/origin/task/$name"; then RESOLVED_SCOPE="remote"; RESOLVED_BRANCH="task/$name"; return 0; fi
  b="$(git branch --remotes --format='%(refname:short)' | sed 's|^origin/||' | grep -i -m1 -F -- "$name" || true)"
  if [ -n "$b" ]; then RESOLVED_SCOPE="remote"; RESOLVED_BRANCH="$b"; return 0; fi
  RESOLVED_BRANCH="task/$name"   # nothing exists; assume the convention for display
}

# Sets MAIN_REFS (space-separated refs that actually exist — safe to pass to
# `git log`) and MERGE_REF (single ref to compare `--merged` against; prefers
# origin/<main> so a GitHub merge counts before a local pull).
MAIN_REFS=""; MERGE_REF=""
compute_main_refs() {
  local main="$1" refs=""
  ref_exists "refs/heads/$main" && refs="$main"
  if ref_exists "refs/remotes/origin/$main"; then
    refs="${refs:+$refs }origin/$main"
    MERGE_REF="origin/$main"
  else
    MERGE_REF="$main"
  fi
  MAIN_REFS="${refs:-$main}"
}

# Sets MAIN_BRANCH, ON_MAIN, TASK_NAME, TASK_BRANCH, BRANCH_SCOPE, BRANCH_EXISTS.
MAIN_BRANCH=""; ON_MAIN=""; TASK_NAME=""; TASK_BRANCH=""; BRANCH_SCOPE=""; BRANCH_EXISTS=""
compute_resolution() {
  local name="${1:-}" cur
  MAIN_BRANCH="$(detect_main_branch)"
  cur="$(git branch --show-current 2>/dev/null || true)"

  if [ -n "$name" ]; then
    TASK_NAME="$(strip_prefix "$name")"
    resolve_named_branch "$TASK_NAME"
    TASK_BRANCH="$RESOLVED_BRANCH"; BRANCH_SCOPE="$RESOLVED_SCOPE"; ON_MAIN="no"
  elif [ -n "$cur" ] && [ "$cur" != "$MAIN_BRANCH" ]; then
    TASK_BRANCH="$cur"; BRANCH_SCOPE="local"; TASK_NAME="$(strip_prefix "$cur")"; ON_MAIN="no"
  else
    ON_MAIN="yes"; TASK_NAME=""; TASK_BRANCH=""; BRANCH_SCOPE="none"
  fi

  if [ "$BRANCH_SCOPE" = "local" ] && ref_exists "refs/heads/$TASK_BRANCH"; then
    BRANCH_EXISTS="yes"
  else
    BRANCH_EXISTS="no"
  fi
}

print_resolution() {
  printf 'main_branch=%s\n'   "$MAIN_BRANCH"
  printf 'on_main=%s\n'       "$ON_MAIN"
  printf 'task_name=%s\n'     "$TASK_NAME"
  printf 'task_branch=%s\n'   "$TASK_BRANCH"
  printf 'branch_scope=%s\n'  "$BRANCH_SCOPE"
  printf 'branch_exists=%s\n' "$BRANCH_EXISTS"
}

do_assess() {
  compute_resolution "${1:-}"
  compute_main_refs "$MAIN_BRANCH"

  local pr_state="none" pr_number="" pr_url="" out rest
  if command -v gh >/dev/null 2>&1 && [ -n "$TASK_BRANCH" ]; then
    out="$(gh pr list --state all --head "$TASK_BRANCH" --limit 1 \
            --json number,state,url --jq '.[0] | "\(.number)|\(.state)|\(.url)"' 2>/dev/null || true)"
    if [ -n "$out" ] && [ "$out" != "null" ]; then
      pr_number="${out%%|*}"; rest="${out#*|}"; pr_state="${rest%%|*}"; pr_url="${rest#*|}"
    fi
  elif ! command -v gh >/dev/null 2>&1; then
    pr_state="nogh"
  fi

  local branch_merged="na"
  if [ "$BRANCH_EXISTS" = "yes" ]; then
    if git branch --merged "$MERGE_REF" --format='%(refname:short)' 2>/dev/null \
         | grep -qxF -- "$TASK_BRANCH"; then
      branch_merged="yes"
    else
      branch_merged="unknown"   # not an ancestor — may be squash/rebase-merged
    fi
  fi

  local commits=0
  if [ -n "$TASK_NAME" ]; then
    # MAIN_REFS is intentionally unquoted: it is a list of existing refs to search.
    commits="$(git log $MAIN_REFS --oneline --grep="$TASK_NAME" 2>/dev/null | grep -c '' || true)"
  fi

  local verdict confidence
  if   [ "$pr_state" = "MERGED" ];                            then verdict="COMPLETED";   confidence="confirmed"
  elif [ "$branch_merged" = "yes" ];                          then verdict="COMPLETED";   confidence="confirmed"
  elif [ "$pr_state" = "OPEN" ];                              then verdict="IN_PROGRESS"; confidence="confirmed"
  elif [ "$BRANCH_EXISTS" = "no" ] && [ "$commits" -gt 0 ];   then verdict="COMPLETED";   confidence="likely"
  elif [ "$BRANCH_EXISTS" = "yes" ];                          then verdict="IN_PROGRESS"; confidence="likely"
  elif [ "$commits" -gt 0 ];                                  then verdict="COMPLETED";   confidence="likely"
  else                                                             verdict="NOT_STARTED"; confidence="none"
  fi

  print_resolution
  printf 'pr_state=%s\n'        "$pr_state"
  printf 'pr_number=%s\n'       "$pr_number"
  printf 'pr_url=%s\n'          "$pr_url"
  printf 'merge_ref=%s\n'       "$MERGE_REF"
  printf 'branch_merged=%s\n'   "$branch_merged"
  printf 'commits_in_main=%s\n' "$commits"
  printf 'verdict=%s\n'         "$verdict"
  printf 'confidence=%s\n'      "$confidence"
}

case "${1:-}" in
  resolve) shift || true; compute_resolution "${1:-}"; print_resolution ;;
  assess)  shift || true; do_assess "${1:-}" ;;
  *) echo "usage: ${0##*/} {resolve|assess} [<task-name>]" >&2; exit 2 ;;
esac
