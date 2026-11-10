#!/usr/bin/env bash
# rebase-lease.sh — pin /rebase's force-push to the remote branch state seen
# BEFORE the rebase, so nothing that changes the remote-tracking ref in between
# (an IDE or background `git fetch`) can turn the lease into a silent overwrite.
#
# A bare `git push --force-with-lease` compares the remote against the
# remote-tracking ref *at push time*. A fetch between the rebase and the push
# moves that ref to a teammate's new commit, the lease then passes, and the
# commit is gone. An explicit `--force-with-lease=<ref>:<sha>` does not move.
#
# Usage:
#   rebase-lease.sh record        before the rebase; current branch's upstream
#   rebase-lease.sh push <sha>    after the rebase; <sha> from `record`, or
#                                 `absent` when record said the remote ref
#                                 was not there
#
# record output (key=value):
#   lease=pinned  remote= ref= sha=   exit 0  push may proceed with this sha
#   lease=absent  remote= ref=        exit 0  upstream configured, no tracking
#                                             ref: the push must find no branch
#   lease=none    reason=             exit 0  nothing to push (no upstream,
#                                             detached HEAD, local upstream)
#   lease=diverged sha= missing=<n>   exit 3  the tracking ref has commits HEAD
#                                             does not contain (fetched, never
#                                             integrated): a force-push would
#                                             drop them, so stop before rebasing
# push output: push=ok (exit 0) | push=rejected reason=lease (exit 3, the
# remote moved since `record`) | push=failed (exit 1, other error; git's own
# stderr is passed through). Exit 2 = usage error.
set -u

usage() {
  echo "usage: rebase-lease.sh record | push <sha|absent>" >&2
  exit 2
}

# Upstream of the current branch as remote + remote-side ref, from config —
# the same pair `git push` resolves for a tracking branch.
resolve_upstream() {
  branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) || { reason=detached; return 1; }
  remote=$(git config --get "branch.$branch.remote" 2>/dev/null) || { reason=no-upstream; return 1; }
  ref=$(git config --get "branch.$branch.merge" 2>/dev/null) || { reason=no-upstream; return 1; }
  [ "$remote" = "." ] && { reason=local-upstream; return 1; }
  return 0
}

case "${1:-}" in
  record)
    [ $# -eq 1 ] || usage
    if ! resolve_upstream; then
      printf 'lease=none\nreason=%s\n' "$reason"
      exit 0
    fi
    tracking=$(git rev-parse --symbolic-full-name '@{upstream}' 2>/dev/null) || tracking=""
    sha=""
    [ -n "$tracking" ] && sha=$(git rev-parse --verify --quiet "$tracking^{commit}" 2>/dev/null)
    if [ -z "$sha" ]; then
      printf 'lease=absent\nremote=%s\nref=%s\n' "$remote" "$ref"
      exit 0
    fi
    missing=$(git rev-list --count "HEAD..$sha" 2>/dev/null) || missing=""
    if [ "$missing" != "0" ]; then
      printf 'lease=diverged\nsha=%s\nmissing=%s\n' "$sha" "${missing:-unknown}"
      exit 3
    fi
    printf 'lease=pinned\nremote=%s\nref=%s\nsha=%s\n' "$remote" "$ref" "$sha"
    ;;
  push)
    [ $# -eq 2 ] || usage
    expect=$2
    if [ "$expect" = absent ]; then
      expect=""   # `<ref>:` with an empty value = the ref must not exist
    elif ! [[ "$expect" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
      usage
    fi
    if ! resolve_upstream; then
      printf 'push=failed\nreason=%s\n' "$reason"
      exit 1
    fi
    err=$(mktemp "${TMPDIR:-/tmp}/rebase-lease.XXXXXX") || { echo "push=failed"; exit 1; }
    trap 'rm -f "$err"' EXIT
    LC_ALL=C git push "--force-with-lease=$ref:$expect" "$remote" "HEAD:$ref" \
      >/dev/null 2>"$err"
    rc=$?
    cat "$err" >&2
    if [ "$rc" -eq 0 ]; then
      echo "push=ok"
      exit 0
    fi
    if grep -q 'stale info' "$err"; then
      printf 'push=rejected\nreason=lease\n'
      exit 3
    fi
    echo "push=failed"
    exit 1
    ;;
  *) usage ;;
esac
