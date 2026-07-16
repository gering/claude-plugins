#!/usr/bin/env bash
# refresh-task-glyphs.sh — soft-coupling shim: if the work-system plugin is
# installed, refresh the herdr task-tab state glyphs (○ ● ◇ ✓) after a PR
# state change or survey (/open, /merge, /cycle, /check).
#
# work-system is DETECTED, never required (skill-composition rule: plugins
# stay independently installable): silent no-op when it is absent, outside
# herdr, or on any error — this must never fail the calling skill. The refresh
# it delegates to makes one gh call (bounded via timeout/perl-alarm inside
# work-system's ws-statusline.sh where available — the network call lives
# there, not here). All real logic is in work-system's herdr-tab-glyph.sh;
# this shim only locates it.
#
# Usage: refresh-task-glyphs.sh [<dir>]   (dir defaults to $PWD)
set -u

[ "${HERDR_ENV:-}" = "1" ] || exit 0
dir="${1:-$PWD}"
root="${CLAUDE_PLUGIN_ROOT:-}"
[ -n "$root" ] || exit 0

run_helper() { bash "$1" refresh "$dir" 2>/dev/null || true; exit 0; }

# Dev layout (repo checkout): plugins/pr-flow and plugins/work-system siblings.
t="$root/../work-system/scripts/herdr-tab-glyph.sh"
[ -f "$t" ] && run_helper "$t"

# Marketplace layout — prefer the ENABLED work-system install from Claude
# Code's installed-plugins manifest: the version cache is never pruned, so a
# newest-cached glob would keep executing a version the user rolled back from.
if command -v python3 >/dev/null 2>&1; then
  t="$(python3 - <<'PY' 2>/dev/null
import json, os
p = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
try:
    plugins = json.load(open(p))["plugins"]
except Exception:
    raise SystemExit
for key, entries in plugins.items():
    if key.split("@", 1)[0] == "work-system" and entries:
        ip = entries[0].get("installPath") or ""
        if ip:
            print(os.path.join(ip, "scripts", "herdr-tab-glyph.sh"))
        break
PY
)"
  [ -n "$t" ] && [ -f "$t" ] && run_helper "$t"
fi

# Fallback (manifest missing/unparsable): newest cached work-system version —
# <cache>/<marketplace>/{pr-flow,work-system}/<version>/…; paths differ only in
# the version segment, so a line-wise sort -V orders them. Heuristic: after a
# rollback this can pick a newer-than-enabled version (accepted limitation —
# the manifest path above is the accurate one).
t="$(printf '%s\n' "$root"/../../work-system/*/scripts/herdr-tab-glyph.sh 2>/dev/null | sort -V | tail -1)"
[ -n "$t" ] && [ -f "$t" ] && run_helper "$t"
exit 0
