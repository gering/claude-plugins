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
#                           detached, task_name, task_branch, branch_scope,
#                           branch_exists, branch_ambiguous.
#   assess  [<task-name>]   resolve + pr_state, pr_number, pr_url, merge_ref,
#                           branch_merged, commits_in_main, verdict, confidence
#                           (PR fields via gh when available).
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

# Resolve a task name to a branch. Sets RESOLVED_SCOPE (local|remote|none),
# RESOLVED_BRANCH (clean short name, or task/<name> by convention when nothing
# is found), RESOLVED_REF (full ref of the resolved branch, or empty), and
# RESOLVED_AMBIGUOUS (yes when a fuzzy substring match hit more than one branch).
RESOLVED_SCOPE=""; RESOLVED_BRANCH=""; RESOLVED_REF=""; RESOLVED_AMBIGUOUS="no"
resolve_named_branch() {
  local name="$1" matches first
  RESOLVED_SCOPE="none"; RESOLVED_BRANCH=""; RESOLVED_REF=""; RESOLVED_AMBIGUOUS="no"
  [ -z "$name" ] && return 0
  # Exact matches first (local, then remote) — never ambiguous.
  if ref_exists "refs/heads/task/$name"; then
    RESOLVED_SCOPE="local"; RESOLVED_BRANCH="task/$name"; RESOLVED_REF="refs/heads/task/$name"; return 0
  fi
  if ref_exists "refs/heads/$name"; then
    RESOLVED_SCOPE="local"; RESOLVED_BRANCH="$name"; RESOLVED_REF="refs/heads/$name"; return 0
  fi
  # Fuzzy local substring (clean `--format` output, literal match).
  matches="$(git branch --list --format='%(refname:short)' | grep -i -F -- "$name" || true)"
  if [ -n "$matches" ]; then
    first="$(printf '%s\n' "$matches" | head -n1)"
    RESOLVED_SCOPE="local"; RESOLVED_BRANCH="$first"; RESOLVED_REF="refs/heads/$first"
    [ "$(printf '%s\n' "$matches" | grep -c '')" -gt 1 ] && RESOLVED_AMBIGUOUS="yes"
    return 0
  fi
  if ref_exists "refs/remotes/origin/task/$name"; then
    RESOLVED_SCOPE="remote"; RESOLVED_BRANCH="task/$name"; RESOLVED_REF="refs/remotes/origin/task/$name"; return 0
  fi
  if ref_exists "refs/remotes/origin/$name"; then
    RESOLVED_SCOPE="remote"; RESOLVED_BRANCH="$name"; RESOLVED_REF="refs/remotes/origin/$name"; return 0
  fi
  # Fuzzy remote substring (exclude origin/HEAD).
  matches="$(git branch --remotes --format='%(refname:short)' | sed 's|^origin/||' \
             | grep -v -x 'HEAD' | grep -i -F -- "$name" || true)"
  if [ -n "$matches" ]; then
    first="$(printf '%s\n' "$matches" | head -n1)"
    RESOLVED_SCOPE="remote"; RESOLVED_BRANCH="$first"; RESOLVED_REF="refs/remotes/origin/$first"
    [ "$(printf '%s\n' "$matches" | grep -c '')" -gt 1 ] && RESOLVED_AMBIGUOUS="yes"
    return 0
  fi
  RESOLVED_BRANCH="task/$name"   # nothing exists; assume the convention for display
}

# Sets MAIN_REFS (space-separated refs that actually exist — safe to pass to
# `git log`) and MERGE_REF (single ref to compare against; prefers origin/<main>
# so a GitHub merge counts before a local pull).
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

# Sets MAIN_BRANCH, ON_MAIN, DETACHED, TASK_NAME, TASK_BRANCH, TASK_REF,
# BRANCH_SCOPE, BRANCH_EXISTS (local or remote), BRANCH_AMBIGUOUS.
MAIN_BRANCH=""; ON_MAIN=""; DETACHED="no"; TASK_NAME=""; TASK_BRANCH=""
TASK_REF=""; BRANCH_SCOPE=""; BRANCH_EXISTS=""; BRANCH_AMBIGUOUS="no"
compute_resolution() {
  local name="${1:-}" cur
  MAIN_BRANCH="$(detect_main_branch)"
  cur="$(git branch --show-current 2>/dev/null || true)"
  DETACHED="no"; BRANCH_AMBIGUOUS="no"

  if [ -n "$name" ]; then
    TASK_NAME="$(strip_prefix "$name")"
    resolve_named_branch "$TASK_NAME"
    TASK_BRANCH="$RESOLVED_BRANCH"; BRANCH_SCOPE="$RESOLVED_SCOPE"; TASK_REF="$RESOLVED_REF"
    BRANCH_AMBIGUOUS="$RESOLVED_AMBIGUOUS"; ON_MAIN="no"
  elif [ -z "$cur" ]; then
    # Detached HEAD (or an unborn branch) — no current branch to derive a task from.
    DETACHED="yes"; ON_MAIN="no"; TASK_NAME=""; TASK_BRANCH=""; TASK_REF=""; BRANCH_SCOPE="none"
  elif [ "$cur" != "$MAIN_BRANCH" ]; then
    TASK_BRANCH="$cur"; BRANCH_SCOPE="local"; TASK_REF="refs/heads/$cur"
    TASK_NAME="$(strip_prefix "$cur")"; ON_MAIN="no"
  else
    ON_MAIN="yes"; TASK_NAME=""; TASK_BRANCH=""; TASK_REF=""; BRANCH_SCOPE="none"
  fi

  if [ -n "$TASK_REF" ] && ref_exists "$TASK_REF"; then BRANCH_EXISTS="yes"; else BRANCH_EXISTS="no"; fi
}

print_resolution() {
  printf 'main_branch=%s\n'      "$MAIN_BRANCH"
  printf 'on_main=%s\n'          "$ON_MAIN"
  printf 'detached=%s\n'         "$DETACHED"
  printf 'task_name=%s\n'        "$TASK_NAME"
  printf 'task_branch=%s\n'      "$TASK_BRANCH"
  printf 'branch_scope=%s\n'     "$BRANCH_SCOPE"
  printf 'branch_exists=%s\n'    "$BRANCH_EXISTS"
  printf 'branch_ambiguous=%s\n' "$BRANCH_AMBIGUOUS"
}

do_assess() {
  compute_resolution "${1:-}"
  compute_main_refs "$MAIN_BRANCH"

  local pr_state="none" pr_number="" pr_url="" out rest
  if command -v gh >/dev/null 2>&1; then
    if [ -n "$TASK_BRANCH" ]; then
      # `.[0] // empty` → no output when there is no matching PR (avoids the
      # literal "null|null|null" that `.[0] | ...` would interpolate).
      out="$(gh pr list --state all --head "$TASK_BRANCH" --limit 1 \
              --json number,state,url --jq '.[0] // empty | "\(.number)|\(.state)|\(.url)"' 2>/dev/null || true)"
      if [ -n "$out" ]; then
        pr_number="${out%%|*}"; rest="${out#*|}"; pr_state="${rest%%|*}"; pr_url="${rest#*|}"
      fi
    fi
  else
    pr_state="nogh"
  fi

  # Merge check works for a local OR remote task ref: is its tip an ancestor of
  # the merge ref? (origin/<main> when it exists.) Not an ancestor → unknown,
  # since a squash/rebase merge rewrites SHAs and is never an ancestor.
  local branch_merged="na"
  if [ -n "$TASK_REF" ] && ref_exists "$TASK_REF"; then
    if git merge-base --is-ancestor "$TASK_REF" "$MERGE_REF" 2>/dev/null; then
      branch_merged="yes"
    else
      branch_merged="unknown"
    fi
  fi

  local commits=0
  if [ -n "$TASK_NAME" ]; then
    # MAIN_REFS is intentionally unquoted: a list of existing refs to search.
    # -F: match the task name literally, not as a regex.
    commits="$(git log $MAIN_REFS --oneline -F --grep="$TASK_NAME" 2>/dev/null | grep -c '' || true)"
  fi

  local verdict confidence
  if   [ "$pr_state" = "MERGED" ];     then verdict="COMPLETED";   confidence="confirmed"
  elif [ "$branch_merged" = "yes" ];   then verdict="COMPLETED";   confidence="confirmed"
  elif [ "$pr_state" = "OPEN" ];       then verdict="IN_PROGRESS"; confidence="confirmed"
  elif [ "$pr_state" = "CLOSED" ];     then verdict="IN_PROGRESS"; confidence="confirmed"
  elif [ "$BRANCH_EXISTS" = "yes" ];   then verdict="IN_PROGRESS"; confidence="likely"
  elif [ "$commits" -gt 0 ];           then verdict="COMPLETED";   confidence="likely"
  else                                      verdict="NOT_STARTED"; confidence="none"
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
