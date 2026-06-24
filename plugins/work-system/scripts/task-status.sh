#!/usr/bin/env bash
# task-status.sh — resolve a work-system task's branch and completion status.
#
# Encapsulates the git/gh logic that /status, /close and /continue otherwise
# carry as drift-prone prose: main-branch detection, task-branch resolution
# (the current branch when inside a worktree, or an exact task/<name> ref when
# given a name), offline-safe main-ref handling, and squash/rebase-aware merge
# detection. Resolution is exact-only — real refs are checked with
# `git rev-parse --verify`, the current branch via `git branch --show-current`.
#
# Run from inside the repo or any linked worktree — branches/refs are shared.
#
# Subcommands:
#   resolve [<task-name>]   Fast, no network. Emits: main_branch, on_main,
#                           detached, task_name, task_branch, branch_scope,
#                           branch_exists.
#   assess  [<task-name>]   resolve + pr_state, pr_number, pr_url, merge_sha,
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

# Resolve a task name to a branch by EXACT match only. Sets RESOLVED_SCOPE
# (local|remote|none), RESOLVED_BRANCH (clean short name, or task/<name> by
# convention when nothing is found), and RESOLVED_REF (full ref, or empty).
# No fuzzy substring matching: a substring guess could bind an unrelated branch
# (or even main) and feed /close's destructive steps, so resolution is limited to
# the four real refs below. An /adopt'd branch that kept a non-task/ name is found
# via the current worktree branch (see compute_resolution), not by name lookup.
RESOLVED_SCOPE=""; RESOLVED_BRANCH=""; RESOLVED_REF=""
resolve_named_branch() {
  local name="$1"
  RESOLVED_SCOPE="none"; RESOLVED_BRANCH=""; RESOLVED_REF=""
  [ -z "$name" ] && return 0
  # Exact matches — local then remote.
  if ref_exists "refs/heads/task/$name"; then
    RESOLVED_SCOPE="local"; RESOLVED_BRANCH="task/$name"; RESOLVED_REF="refs/heads/task/$name"; return 0
  fi
  if ref_exists "refs/heads/$name"; then
    RESOLVED_SCOPE="local"; RESOLVED_BRANCH="$name"; RESOLVED_REF="refs/heads/$name"; return 0
  fi
  if ref_exists "refs/remotes/origin/task/$name"; then
    RESOLVED_SCOPE="remote"; RESOLVED_BRANCH="task/$name"; RESOLVED_REF="refs/remotes/origin/task/$name"; return 0
  fi
  if ref_exists "refs/remotes/origin/$name"; then
    RESOLVED_SCOPE="remote"; RESOLVED_BRANCH="$name"; RESOLVED_REF="refs/remotes/origin/$name"; return 0
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
# BRANCH_SCOPE, BRANCH_EXISTS (local or remote). BRANCH_EXISTS=yes means the
# branch was resolved to a real ref (exact name match, or the checked-out worktree
# branch); =no means nothing matched and task_branch is just the task/<name>
# convention for display.
MAIN_BRANCH=""; ON_MAIN=""; DETACHED="no"; TASK_NAME=""; TASK_BRANCH=""
TASK_REF=""; BRANCH_SCOPE=""; BRANCH_EXISTS=""
compute_resolution() {
  local name="${1:-}" cur
  MAIN_BRANCH="$(detect_main_branch)"
  cur="$(git branch --show-current 2>/dev/null || true)"
  DETACHED="no"

  if [ -n "$name" ]; then
    TASK_NAME="$(strip_prefix "$name")"
    resolve_named_branch "$TASK_NAME"
    if [ "$RESOLVED_SCOPE" != "none" ] && [ "$RESOLVED_BRANCH" = "$MAIN_BRANCH" ]; then
      # The name resolved to the main branch itself — never a task. Surface like
      # on_main so /status and /close route to "pick a real task", never letting
      # the main branch feed /close's destructive worktree/branch deletion.
      ON_MAIN="yes"; TASK_NAME=""; TASK_BRANCH=""; TASK_REF=""; BRANCH_SCOPE="none"
    else
      TASK_BRANCH="$RESOLVED_BRANCH"; BRANCH_SCOPE="$RESOLVED_SCOPE"; TASK_REF="$RESOLVED_REF"; ON_MAIN="no"
    fi
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
}

do_assess() {
  compute_resolution "${1:-}"
  compute_main_refs "$MAIN_BRANCH"

  # pr_merge_sha is named distinctly from the topology `merge_sha` (the MERGE_REF
  # tip) computed below — they are different SHAs and must not collide.
  local pr_state="none" pr_number="" pr_url="" pr_merge_sha="" out rest
  if command -v gh >/dev/null 2>&1; then
    if [ -n "$TASK_BRANCH" ]; then
      # `.[0] // empty` → no output when there is no matching PR (avoids the
      # literal "null|null|null" that `.[0] | ...` would interpolate). mergeCommit
      # is null until the PR is merged, then carries the merge commit (squash/rebase
      # included) — /close stamps the archive with it, so no second `gh` round-trip.
      out="$(gh pr list --state all --head "$TASK_BRANCH" --limit 1 \
              --json number,state,url,mergeCommit \
              --jq '.[0] // empty | "\(.number)|\(.state)|\(.url)|\(.mergeCommit.oid // "")"' 2>/dev/null || true)"
      if [ -n "$out" ]; then
        pr_number="${out%%|*}"; rest="${out#*|}"
        pr_state="${rest%%|*}"; rest="${rest#*|}"
        pr_url="${rest%%|*}"; pr_merge_sha="${rest#*|}"
      fi
    fi
  else
    pr_state="nogh"
  fi

  # Merge state — reported as EVIDENCE ONLY; topology never *confirms* a merge.
  # A branch whose tip is an ancestor of main may be genuinely (ff / merge-commit)
  # merged OR just a never-committed branch sitting at/behind main — indistinguishable
  # without the branch point — and a squash/rebase merge rewrites SHAs so it is never
  # an ancestor anyway. So only a MERGED PR confirms completion (see the verdict
  # cascade); branch_merged just labels the topology so callers can word their report:
  #   na      — no task ref to check
  #   no      — tip == merge ref: no commits beyond main (fresh / in sync, or fully
  #             fast-forwarded) — nothing distinct to confirm either way
  #   unknown — anything else (behind-but-reachable, or a possible squash/rebase merge)
  local branch_merged="na" task_sha merge_sha
  if [ -n "$TASK_REF" ] && ref_exists "$TASK_REF"; then
    task_sha="$(git rev-parse "$TASK_REF" 2>/dev/null || true)"
    merge_sha="$(git rev-parse "$MERGE_REF" 2>/dev/null || true)"
    if [ -n "$task_sha" ] && [ "$task_sha" = "$merge_sha" ]; then
      branch_merged="no"
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

  # A bare commit-message match only implies completion when the task name is
  # specific enough not to collide with unrelated history — skip generic words
  # like "fix"/"api"/"test": require multi-segment kebab, or >= 6 chars.
  local name_specific="no"
  case "$TASK_NAME" in *-*) name_specific="yes";; esac
  [ "${#TASK_NAME}" -ge 6 ] && name_specific="yes"

  # Only a MERGED PR *confirms* completion. A branch that still exists (with no
  # merged PR) is always IN_PROGRESS — topology can't prove a merge, so /close must
  # warn+ask before destroying it. The commits-in-main fallback only fires once the
  # branch is gone locally AND remotely (work plausibly merged then cleaned up).
  local verdict confidence
  if   [ "$pr_state" = "MERGED" ];                           then verdict="COMPLETED";   confidence="confirmed"
  elif [ "$pr_state" = "OPEN" ];                             then verdict="IN_PROGRESS"; confidence="confirmed"
  elif [ "$pr_state" = "CLOSED" ];                           then verdict="IN_PROGRESS"; confidence="confirmed"
  elif [ "$BRANCH_EXISTS" = "yes" ];                         then verdict="IN_PROGRESS"; confidence="likely"
  elif [ "$commits" -gt 0 ] && [ "$name_specific" = "yes" ]; then verdict="COMPLETED";   confidence="likely"
  else                                                            verdict="NOT_STARTED"; confidence="none"
  fi

  print_resolution
  printf 'pr_state=%s\n'        "$pr_state"
  printf 'pr_number=%s\n'       "$pr_number"
  printf 'pr_url=%s\n'          "$pr_url"
  printf 'merge_sha=%s\n'       "$pr_merge_sha"
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
