#!/usr/bin/env bash
# rebase-lease.sh — pin a force-push (/rebase, /cycle --loop's squash) to the
# remote branch state seen BEFORE history is rewritten, so nothing that changes
# the remote-tracking ref in between (an IDE or background `git fetch`) can turn
# the lease into a silent overwrite.
#
# A bare `git push --force-with-lease` compares the remote against the
# remote-tracking ref *at push time*. A fetch between the rewrite and the push
# moves that ref to a teammate's new commit, the lease then passes, and the
# commit is gone. An explicit `--force-with-lease=<ref>:<sha>` does not move.
#
# Usage:
#   rebase-lease.sh record        before the rewrite, on the branch to push
#   rebase-lease.sh push <sha>    after it; <sha> from `record`, or `absent`
#                                 when record said the remote branch was not
#                                 there
#
# The push destination is resolved the way `git push` resolves it: the remote
# from %(push:remotename) (branch.pushRemote > remote.pushDefault >
# branch.remote), the remote branch from push.default — `simple` (the default)
# requires the upstream to carry the local branch's name unless the push goes
# to a different remote than the upstream (then: same name), `current` uses the
# same name, `upstream`/`tracking` the configured upstream. An upstream with
# ANOTHER name (a branch cut from origin/main) is never a force-push target, in
# any mode — under `upstream` git would push there, and that is the default
# branch. Instead the same-named remote branch is the target when one is
# already published (pushed without -u: the PR's branch), so its lease and the
# diverged check still apply; when none exists it is `none`: the rebase stays
# local and `git push -u <remote>` is the caller's next step.
# Anything this script cannot mirror exactly — `matching`, `nothing`, a
# remote.<name>.push refspec — is `unresolved`: never guess a target for a
# force-push. The lease is read from refs/remotes/<remote>/<branch> (the
# standard fetch refspec).
#
# record output (key=value):
#   lease=pinned  remote= ref= branch=   exit 0
#                 sha=                     push with this sha; also
#                 [rewritten=yes]                 `rewritten=yes` when the
#                                                 remote tip is a commit this
#                                                 branch itself held and has
#                                                 since rewritten (amend,
#                                                 autosquash, a rebase whose
#                                                 push failed) — found in the
#                                                 branch's reflog
#   lease=absent  remote= ref= branch=   exit 0  no tracking ref: the push must
#                                                 find no remote branch
#   lease=none    reason= [remote=]      exit 0  nothing to push (no upstream,
#                                                 detached HEAD, local upstream,
#                                                 upstream-name-mismatch:<name>
#                                                 with no same-named remote
#                                                 branch; that one also prints
#                                                 the remote to push -u to)
#   lease=diverged remote= ref= branch=  exit 3  the remote tip has commits HEAD
#                  sha= missing=<n>
#                                                 lacks and this branch never
#                                                 held (a teammate's push): a
#                                                 force-push would drop them
#   lease=unresolved reason=             exit 4  no single safe push target
# `branch=` is the remote branch name (ref without refs/heads/).
# push output:
#   push=ok remote= ref= branch=                    exit 0
#   push=rejected reason=lease remote= ref= branch= exit 3  the remote moved
#                                                           since `record`
#   push=unresolved reason=                         exit 4  the target no
#                    longer resolves (a config change, not worth a retry)
#   push=failed                                     exit 1  other error
#                    (network, auth); git's own stderr is passed through, a
#                    retry with the same sha is safe
# Exit 2 = usage error.
set -u

usage() {
  echo "usage: rebase-lease.sh record | push <sha|absent>" >&2
  exit 2
}

# Sets branch, remote, name (remote branch name), ref (refs/heads/<name>) and
# tracking; on failure sets kind (none|unresolved) and reason. Globals on
# purpose: both callers print them, so never declare `name` local here.
resolve_target() {
  local upstream_remote merge mode
  remote=""
  branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) || { kind=none; reason=detached; return 1; }
  upstream_remote=$(git config --get "branch.$branch.remote" 2>/dev/null) || { kind=none; reason=no-upstream; return 1; }
  merge=$(git config --get "branch.$branch.merge" 2>/dev/null) || { kind=none; reason=no-upstream; return 1; }
  [ "$upstream_remote" = "." ] && { kind=none; reason=local-upstream; return 1; }
  remote=$(git for-each-ref --format='%(push:remotename)' "refs/heads/$branch" 2>/dev/null)
  [ -n "$remote" ] || remote=$upstream_remote
  if git config --get-all "remote.$remote.push" >/dev/null 2>&1; then
    kind=unresolved; reason=push-refspec; return 1
  fi
  mode=$(git config --get push.default 2>/dev/null) || mode=simple
  case "$mode" in
    simple|current|upstream|tracking) ;;
    *) kind=unresolved; reason="push-default:$mode"; return 1 ;;
  esac
  if [ "$mode" != current ] && [ "$remote" = "$upstream_remote" ] && [ "$merge" != "refs/heads/$branch" ] \
     && ! git rev-parse --verify --quiet "refs/remotes/$remote/$branch" >/dev/null 2>&1; then
    kind=none; reason="upstream-name-mismatch:${merge#refs/heads/}"; return 1
  fi
  case "$mode" in
    upstream|tracking)
      [ "$remote" = "$upstream_remote" ] || { kind=unresolved; reason=upstream-on-other-remote; return 1; } ;;
  esac
  name=$branch
  ref="refs/heads/$name"
  tracking="refs/remotes/$remote/$name"
  return 0
}

case "${1:-}" in
  record)
    [ $# -eq 1 ] || usage
    if ! resolve_target; then
      printf 'lease=%s\nreason=%s\n' "$kind" "$reason"
      case "$reason" in upstream-name-mismatch:*) printf 'remote=%s\n' "$remote" ;; esac
      [ "$kind" = unresolved ] && exit 4
      exit 0
    fi
    if ! sha=$(git rev-parse --verify --quiet "$tracking^{commit}" 2>/dev/null); then
      printf 'lease=absent\nremote=%s\nref=%s\nbranch=%s\n' "$remote" "$ref" "$name"
      exit 0
    fi
    rewritten=""
    if ! git merge-base --is-ancestor "$sha" HEAD 2>/dev/null; then
      # Not contained: our own rewrite if this branch once pointed at it,
      # otherwise someone else's commit we would drop.
      if git rev-list --walk-reflogs "refs/heads/$branch" 2>/dev/null | grep -qx "$sha"; then
        rewritten=yes
      else
        missing=$(git rev-list --count "HEAD..$sha" 2>/dev/null) || missing=unknown
        printf 'lease=diverged\nremote=%s\nref=%s\nbranch=%s\nsha=%s\nmissing=%s\n' "$remote" "$ref" "$name" "$sha" "$missing"
        exit 3
      fi
    fi
    printf 'lease=pinned\nremote=%s\nref=%s\nbranch=%s\nsha=%s\n' "$remote" "$ref" "$name" "$sha"
    [ -n "$rewritten" ] && echo "rewritten=yes"
    exit 0
    ;;
  push)
    [ $# -eq 2 ] || usage
    expect=$2
    if [ "$expect" = absent ]; then
      expect=""   # `<ref>:` with an empty value = the ref must not exist
    elif ! [[ "$expect" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
      usage
    fi
    if ! resolve_target; then
      printf 'push=unresolved\nreason=%s\n' "$reason"
      exit 4
    fi
    err=$(LC_ALL=C git push "--force-with-lease=$ref:$expect" "$remote" "HEAD:$ref" 2>&1 >/dev/null)
    rc=$?
    [ -n "$err" ] && printf '%s\n' "$err" >&2
    if [ "$rc" -eq 0 ]; then
      printf 'push=ok\nremote=%s\nref=%s\nbranch=%s\n' "$remote" "$ref" "$name"
      exit 0
    fi
    case "$err" in
      *"stale info"*) printf 'push=rejected\nreason=lease\nremote=%s\nref=%s\nbranch=%s\n' "$remote" "$ref" "$name"; exit 3 ;;
    esac
    echo "push=failed"
    exit 1
    ;;
  *) usage ;;
esac
