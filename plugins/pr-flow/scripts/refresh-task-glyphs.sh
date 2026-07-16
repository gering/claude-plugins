#!/usr/bin/env bash
# refresh-task-glyphs.sh — soft-coupling shim: if the work-system plugin is
# installed, refresh the herdr task-tab state glyphs (○ ● ◇ ✓) after a PR
# state change or survey (/open, /merge, /cycle, /check).
#
# work-system is DETECTED, never required (skill-composition rule: plugins
# stay independently installable): silent no-op when it is absent, outside
# herdr, or on any error — this must never fail or slow the calling skill
# beyond one bounded gh call. All real logic lives in work-system's
# herdr-tab-glyph.sh; this shim only locates it.
#
# Usage: refresh-task-glyphs.sh [<dir>]   (dir defaults to $PWD)
set -u

[ "${HERDR_ENV:-}" = "1" ] || exit 0
dir="${1:-$PWD}"
root="${CLAUDE_PLUGIN_ROOT:-}"
[ -n "$root" ] || exit 0

# Dev layout (repo checkout): plugins/pr-flow and plugins/work-system siblings.
t="$root/../work-system/scripts/herdr-tab-glyph.sh"
if [ -f "$t" ]; then
  bash "$t" refresh "$dir" 2>/dev/null || true
  exit 0
fi

# Marketplace cache layout: <cache>/<marketplace>/{pr-flow,work-system}/<version>/…
# — the newest installed work-system version wins (paths differ only in the
# version segment, so a line-wise sort -V orders them correctly).
t="$(printf '%s\n' "$root"/../../work-system/*/scripts/herdr-tab-glyph.sh 2>/dev/null | sort -V | tail -1)"
if [ -n "$t" ] && [ -f "$t" ]; then
  bash "$t" refresh "$dir" 2>/dev/null || true
fi
exit 0
