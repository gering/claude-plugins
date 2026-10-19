#!/usr/bin/env bash
# review-record.sh — tell work-system what a finished local review saw, so a
# lane's /land and /pr-flow:merge can gate the merge on it (mandate.sh covered).
#
# work-system is DETECTED, never required: outside a lane, without a mandate, or
# without work-system this is a silent no-op that reports why. The record itself
# — what is hashed, where it is stored, when it is refused — lives entirely in
# work-system's mandate.sh; this script only locates it.
#
# Usage: review-record.sh reviewed --head <rev> [<dir>]
#          review-record.sh fixed --after <rev> [<dir>]
#   reviewed  the review just presented saw commit <sha> of the lane at <dir>
#   fixed     the agreed fixes of review <sha> are applied, with no re-review
#
# Output: mandate.sh's key=value lines, or `recorded=no reason=<why>`.
# Exit: 0 recorded · 3 nothing to record into (no work-system, too old, no
# mandate) · 1 refused (reason=) · 2/4 an error the caller shows the user.
set -u

# The flag is required here, not optional as in mandate.sh: without it
# `reviewed` would record the tree as it is NOW, edits made while the review ran
# included.
case "${1:-} ${2:-}" in
  "reviewed --head"|"fixed --after") [ -n "${3:-}" ] || set -- ;;
  *) set -- ;;
esac
[ $# -gt 0 ] || { echo "usage: ${0##*/} {reviewed --head <rev>|fixed --after <rev>} [<dir>]" >&2; exit 2; }

# Sourced from this script's own directory, never from a path built out of an
# unset env var — see pr-flow's mandate-shim.sh for the incident.
SW_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || SW_DIR=""
if [ -z "$SW_DIR" ] || [ ! -f "$SW_DIR/lib-work-system.sh" ]; then
  echo "recorded=no"; echo "reason=no-work-system"; exit 3
fi
# shellcheck source=lib-work-system.sh
. "$SW_DIR/lib-work-system.sh" || { echo "recorded=no"; echo "reason=no-work-system"; exit 3; }
t="$(ws_find scripts/mandate.sh)"
if [ -z "$t" ]; then echo "recorded=no"; echo "reason=no-work-system"; exit 3; fi
case "$(bash "$t" 2>&1 </dev/null)" in
  *"|reviewed|"*) ;;
  *) echo "recorded=no"; echo "reason=old-work-system"; exit 3 ;;
esac

out="$(bash "$t" "$@")"; rc=$?
if [ "$rc" = 3 ]; then echo "recorded=no"; echo "reason=no-mandate"; exit 3; fi
[ -z "$out" ] || printf '%s\n' "$out"
exit "$rc"
