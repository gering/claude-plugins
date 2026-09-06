#!/usr/bin/env bash
# refresh-task-glyphs.sh — soft-coupling shim: if the work-system plugin is
# installed, refresh the herdr task-tab state glyphs (○ ● ◇ ◆ ✓) after a PR
# state change or survey (/open, /merge, /cycle, /check).
#
# work-system is DETECTED, never required (skill-composition rule: plugins
# stay independently installable): silent no-op when it is absent, outside
# herdr, or on any error — this must never fail the calling skill. Without
# --cached the delegated refresh makes one synchronous gh call (bounded via
# timeout/perl-alarm inside work-system's ws-statusline.sh — the network call
# lives there, not here); a transition caller (/open, /merge, /cycle) needs the
# post-change state. With --cached it is cache-only + a non-blocking background
# refresh — for a pure-survey caller (/check) that must not block. All real
# logic is in work-system's herdr-tab-glyph.sh; this shim only calls it, and
# lib-work-system.sh does the locating.
#
# Usage: refresh-task-glyphs.sh [--cached] [<dir>]   (dir defaults to $PWD)
set -u

[ "${HERDR_ENV:-}" = "1" ] || exit 0
cached=""
[ "${1:-}" = "--cached" ] && { cached="--cached"; shift; }
dir="${1:-$PWD}"

# shellcheck source=lib-work-system.sh
. "${CLAUDE_PLUGIN_ROOT:-.}/scripts/lib-work-system.sh" 2>/dev/null \
  || . "$(dirname "$0")/lib-work-system.sh" 2>/dev/null || exit 0

t="$(ws_find scripts/herdr-tab-glyph.sh)"
[ -n "$t" ] || exit 0

# $cached unquoted so an empty value expands to no argument.
bash "$t" refresh $cached "$dir" 2>/dev/null || true
exit 0
