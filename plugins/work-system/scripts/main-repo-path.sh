#!/usr/bin/env bash
# main-repo-path.sh — resolve the main worktree for work-system skills.
#
# The shared task backlog (tasks/) lives in the main worktree. A skill invoked
# from a linked worktree must target the main worktree's tasks/, so it needs
# the main path and a way to tell the two invocations apart.
#
# Subcommands:
#   path     Print the absolute path of the main worktree (the first
#            `git worktree list` entry). Robust against paths containing
#            spaces — strips the porcelain "worktree " prefix without
#            field-splitting.
#   linked   Print "linked" if the current directory is inside a linked
#            worktree, "main" if it is the main worktree. Compares git's own
#            dir bookkeeping (--git-common-dir vs --git-dir), so it is immune
#            to symlinked paths rather than string-comparing two path forms.
#
# Exits non-zero if not run inside a git repository.
set -eu

case "${1:-path}" in
  path)
    # Porcelain output starts each block with "worktree <path>"; the first
    # block is the main worktree. Take the first line, drop the prefix.
    list="$(git worktree list --porcelain)"
    first="${list%%$'\n'*}"
    printf '%s\n' "${first#worktree }"
    ;;
  linked)
    common="$(cd "$(git rev-parse --git-common-dir)" && pwd)"
    gitdir="$(cd "$(git rev-parse --git-dir)" && pwd)"
    if [ "$common" = "$gitdir" ]; then
      echo "main"
    else
      echo "linked"
    fi
    ;;
  *)
    echo "usage: ${0##*/} [path|linked]" >&2
    exit 2
    ;;
esac
