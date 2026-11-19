#!/usr/bin/env bash
# sync-main.sh — fast-forward the main checkout's <main-branch> to origin (/close step 5).
#
# Usage: sync-main.sh <main-repo-path> <main-branch>
#
# Fetches origin/<main-branch>, counts behind/ahead and fast-forwards only when
# local is strictly behind. Never rebases, merges or resets: a diverged branch
# (local commits origin lacks) is reported, not touched — that is the user's
# call, and separate from closing a task. A checkout parked on another branch is
# left alone too: `merge --ff-only` would move THAT branch.
#
# Output (key=value):
#   result=no-origin | in-sync | fast-forwarded | ahead | diverged | wrong-branch |
#          dirty | fetch-failed | ff-failed
#   behind=<n> ahead=<n>   (whenever both could be counted)
#   current=<branch>       (wrong-branch; empty = detached HEAD)
#   files=<paths>          (dirty: up to 5 tracked files with local changes)
#   error=<git's message>  (fetch-failed, ff-failed: e.g. local edits in the way)
# Only `in-sync` and `fast-forwarded` mean: the checkout is ON <main-branch>, it
# equals the fetched origin/<main-branch> and no tracked file has local changes
# (`dirty` is checked before any fast-forward) — the condition /close step 10b
# needs before a deploy. `ahead` (local commits origin lacks, nothing to pull)
# and `wrong-branch` (checked even when nothing is to pull: a deploy runs from
# the working tree) never do.
#
# Exit 0 for every result above (each is an outcome to report, not a crash);
# 2 on a usage error, a path that is not a git repository, or an invalid
# branch name.
# CWD-safe: every git call names the repo with -C; the script never cd's.
set -eu

repo="${1:-}" mainbr="${2:-}"
if [ -z "$repo" ] || [ -z "$mainbr" ] || [ "$#" -ne 2 ]; then
  echo "usage: ${0##*/} <main-repo-path> <main-branch>" >&2
  exit 2
fi
git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || { echo "${0##*/}: not a git repository: $repo" >&2; exit 2; }
# A name git would read as an option (-x) or a refspec (a:b) must never reach
# fetch/merge: check-ref-format refuses both.
git -C "$repo" check-ref-format --branch "$mainbr" >/dev/null 2>&1 && case "$mainbr" in -*) false ;; esac \
  || { echo "${0##*/}: not a valid branch name: $mainbr" >&2; exit 2; }

oneline() { tr '\n' ' ' | sed 's/  */ /g; s/ $//' | cut -c1-300; }

git -C "$repo" remote get-url origin >/dev/null 2>&1 || { echo "result=no-origin"; exit 0; }

# An explicit refspec: a plain `fetch origin <branch>` updates the tracking ref
# only when remote.origin.fetch maps it, and a narrowed fetch config would then
# compare against a stale origin/<branch>.
if ! err="$(git -C "$repo" fetch --quiet origin "+refs/heads/$mainbr:refs/remotes/origin/$mainbr" 2>&1)"; then
  printf 'result=fetch-failed\nerror=%s\n' "$(printf '%s' "$err" | oneline)"
  exit 0
fi
counts="$(git -C "$repo" rev-list --left-right --count "refs/heads/$mainbr...refs/remotes/origin/$mainbr" 2>/dev/null)" || counts=""
ahead="${counts%%[[:space:]]*}"; behind="${counts##*[[:space:]]}"
case "$ahead$behind" in ''|*[!0-9]*)
  printf 'result=fetch-failed\nerror=%s\n' "cannot compare $mainbr with origin/$mainbr"
  exit 0 ;;
esac
counts() { printf 'behind=%s\nahead=%s\n' "$behind" "$ahead"; }

if [ "$behind" != 0 ] && [ "$ahead" != 0 ]; then
  echo "result=diverged"; counts; exit 0
fi
if [ "$behind" = 0 ] && [ "$ahead" != 0 ]; then
  echo "result=ahead"; counts; exit 0
fi
cur="$(git -C "$repo" branch --show-current 2>/dev/null || true)"
if [ "$cur" != "$mainbr" ]; then
  printf 'result=wrong-branch\ncurrent=%s\n' "$cur"; counts; exit 0
fi
# Refs alone do not make a deploy-safe checkout: a deploy runs from the working
# tree, so a tracked edit (staged or not) would ship code origin does not have.
# Untracked files are not counted (build output, local config).
dirty="$(git -C "$repo" status --porcelain --untracked-files=no 2>/dev/null | head -n 5 | cut -c4- | tr '\n' ' ' | sed 's/ $//')"
if [ -n "$dirty" ]; then
  printf 'result=dirty\nfiles=%s\n' "$dirty"; counts; exit 0
fi
if [ "$behind" = 0 ]; then
  echo "result=in-sync"; counts; exit 0
fi
if err="$(git -C "$repo" merge --ff-only --quiet "refs/remotes/origin/$mainbr" 2>&1)"; then
  echo "result=fast-forwarded"; counts
else
  printf 'result=ff-failed\nerror=%s\n' "$(printf '%s' "$err" | oneline)"; counts
fi
