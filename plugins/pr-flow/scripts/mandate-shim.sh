#!/usr/bin/env bash
# mandate-shim.sh — soft-coupling shim: read the lane's autonomy mandate from
# the work-system plugin, so pr-flow does not re-ask for something /kickoff
# already recorded the user's grant for (checks, PR open, agreed fixes).
#
# work-system is DETECTED, never required. Unlike the glyph shim, this one is
# NOT a silent no-op when work-system is missing: a caller asking "may I open a
# PR without confirming?" must hear "unknown" rather than nothing, or a missing
# plugin would read as a denial. All mandate semantics live in work-system's
# mandate.sh; this shim only locates it and passes the verdict through.
#
# Usage: mandate-shim.sh {show|allows <action>|round|reviewed [--head <rev>]|fixed --after <rev>|covered [--head <rev>]|lane <branch>|actions|deploy-owner} [<dir>]
#
# <dir> is the LANE, not the cwd: resolve it first with `lane <branch>` (prints
# the worktree holding that branch), so a /cycle run from the main repo reads the
# PR's mandate rather than whatever sits at the session root.
#
# Exits, passed through from work-system's mandate.sh:
#   0  allowed (allows) / covered (covered) / success (show, round,
#      reviewed, fixed, lane, deploy-owner)
#   1  denied or unlisted — a decision the user made: stop and ask. For
#      `covered`/`fixed` it is NOT a user decision: no recorded review covers
#      that head, or the fixes could not be recorded — reason= says why
#   2  usage, or the mandate file itself is unreadable (duplicate key, open
#      fence, symlink) — show stderr, stop and ask; never read as 1 or 3
#   3  unknown — no mandate recorded, no worktree for that branch (lane),
#      deploy ownership unresolvable (deploy-owner: in_lane=unknown),
#      work-system not installed (verdict=no-work-system), or one too old to
#      know the review-record verbs (verdict=old-work-system): stop and ask —
#      except deploy-owner with verdict=no-work-system: no work-system means
#      no lane and no Manager, so the project's own deploy instructions apply
#   4  a round or review record could not be persisted (round, reviewed,
#      fixed) — stop; looping on an in-session count would restart the budget
#      on resume, and an unrecorded review covers nothing
# 1 and 3 are both "don't proceed unasked", but only 1 is a decision the user
# actually made; never report a missing plugin as a refusal.
set -u

case "${1:-}" in
  show|allows|round|reviewed|fixed|covered|lane|actions|deploy-owner) ;;
  *) echo "usage: ${0##*/} {show|allows <action>|round|reviewed [--head <rev>]|fixed --after <rev>|covered [--head <rev>]|lane <branch>|actions|deploy-owner} [<dir>]" >&2; exit 2 ;;
esac

# Source the locator from THIS script's own directory — never from a path built
# out of an unset env var. `${CLAUDE_PLUGIN_ROOT:-.}` made the fallback "the
# current working directory", so any checked-out repo carrying
# scripts/lib-work-system.sh got its code executed the moment this shim ran, in
# the main session with no sandbox.
# shellcheck source=lib-work-system.sh
WS_SHIM_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || WS_SHIM_DIR=""
if [ -z "$WS_SHIM_DIR" ] || [ ! -f "$WS_SHIM_DIR/lib-work-system.sh" ]; then
  echo "verdict=no-work-system"; exit 3
fi
. "$WS_SHIM_DIR/lib-work-system.sh" || { echo "verdict=no-work-system"; exit 3; }

t="$(ws_find scripts/mandate.sh)"
if [ -z "$t" ]; then
  # Old work-system versions have no mandate.sh either — same answer: unknown.
  echo "verdict=no-work-system"
  exit 3
fi

# The review-record verbs are newer than the shim's other verbs. An older
# mandate.sh answers them with its usage error (exit 2), which callers read as
# "a record nobody can vouch for" and hard-stop on — every /merge blocked by a
# version skew. Ask its usage line instead; a missing verb is "unknown" (3).
case "$1" in
  reviewed|fixed|covered)
    case "$(bash "$t" 2>&1 </dev/null)" in
      *"|covered|"*) ;;
      *) echo "verdict=old-work-system"; exit 3 ;;
    esac ;;
esac

exec bash "$t" "$@"
