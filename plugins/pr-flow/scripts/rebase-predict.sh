#!/usr/bin/env bash
# rebase-predict.sh — predict whether rebasing HEAD onto a base would conflict,
# without touching the worktree, the index or any ref.
#
# Usage: rebase-predict.sh <base-ref> [<head-ref>]     (head defaults to HEAD)
#
# Output (key=value, always exit 0 once the arguments are valid):
#   prediction=clean                      git would merge both sides cleanly
#   prediction=conflict  + file=<path>…   content conflicts in these paths
#   prediction=unknown   + reason=<why>   no prediction (old git, bad ref,
#                                         merge commits on the branch, …)
# Exit 2 = usage error.
#
# It runs `git merge-tree --write-tree` (git >= 2.38): a three-way merge of the
# two tips in memory; the resulting tree object is written to the object store
# but referenced by nothing. That is a merge, not a commit-by-commit replay, so
# it is a prediction: a rebase can still hit a conflict in an intermediate
# commit that the end-state merge does not. Callers keep their abort-on-
# conflict path as the safety net; `unknown` means "proceed and rely on it".
# A branch with merge commits is `unknown`, not judged: rebase drops them and
# replays the commits below, so a conflict resolved in a merge from the base
# comes back, while the tip merge calls it clean.
set -u

if [ $# -lt 1 ] || [ $# -gt 2 ] || [ -z "${1:-}" ]; then
  echo "usage: rebase-predict.sh <base-ref> [<head-ref>]" >&2
  exit 2
fi
base_ref=$1
head_ref=${2:-HEAD}

unknown() { printf 'prediction=unknown\nreason=%s\n' "$1"; exit 0; }

# merge-tree exits 1 both for "conflicts" and for an unresolvable ref, so the
# refs are resolved first and only then is exit 1 read as a conflict.
base=$(git rev-parse --verify --quiet "${base_ref}^{commit}" 2>/dev/null) || unknown "bad-ref:base"
head=$(git rev-parse --verify --quiet "${head_ref}^{commit}" 2>/dev/null) || unknown "bad-ref:head"

mb=$(git merge-base "$head" "$base" 2>/dev/null) || unknown "no-merge-base"
if [ -n "$(git rev-list --merges -n 1 "$mb..$head" 2>/dev/null)" ]; then
  unknown "merge-commits"
fi

out=$(mktemp "${TMPDIR:-/tmp}/rebase-predict.XXXXXX") || unknown "no-tempfile"
trap 'rm -f "$out"' EXIT

git merge-tree --write-tree --name-only --no-messages -z "$head" "$base" >"$out" 2>/dev/null
rc=$?

# -z output: <tree-oid> NUL [<conflicted-path> NUL]…
files=()
{
  IFS= read -r -d '' tree || tree=""
  while IFS= read -r -d '' tok; do
    [ -n "$tok" ] && files+=("$tok")
  done
} <"$out"

# A tree oid is 40 (SHA-1) or 64 (SHA-256) hex chars; anything else means
# merge-tree did not run its --write-tree mode (git < 2.38 prints usage).
if ! [[ "$tree" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  unknown "merge-tree-unavailable:rc=$rc"
fi

case "$rc" in
  0) echo "prediction=clean" ;;
  1)
    echo "prediction=conflict"
    # One path per line: a newline inside a name can't forge another key.
    for f in "${files[@]+"${files[@]}"}"; do
      printf 'file=%s\n' "${f//$'\n'/?}"
    done
    ;;
  *) unknown "merge-tree-failed:rc=$rc" ;;
esac
