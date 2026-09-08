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
# Usage: mandate-shim.sh {show|allows <action>|round} [<dir>]
#
# Exits: 0 allowed / 1 denied or unlisted (stop and ask) / 3 unknown — no
# mandate recorded, or work-system not installed (stop and ask) / 2 usage.
# 1 and 3 are both "don't proceed unasked", but only 1 is a decision the user
# actually made; never report a missing plugin as a refusal.
set -u

case "${1:-}" in
  show|allows|round) ;;
  *) echo "usage: ${0##*/} {show|allows <action>|round} [<dir>]" >&2; exit 2 ;;
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

exec bash "$t" "$@"
