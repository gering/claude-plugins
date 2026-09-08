#!/usr/bin/env bash
# lib-work-system.sh — locate a script inside the installed work-system plugin.
#
# Sourced, not executed. work-system is DETECTED, never required
# (skill-composition rule: plugins stay independently installable), so every
# caller must treat "not found" as a normal outcome, not an error.
#
# Usage:
#   . "$(dirname "$0")/lib-work-system.sh"
#   ws_find scripts/herdr-tab-glyph.sh   # prints an absolute path, or nothing
#
# The resolution layers exist because the same plugin lives in three shapes;
# they are tried in order of accuracy, not convenience.
#
# Both this file's own directory AND $CLAUDE_PLUGIN_ROOT are used as anchors.
# The env var alone was not enough: a shim invoked outside a skill's
# `${CLAUDE_PLUGIN_ROOT}` expansion (a hook, an absolute-path call, a worker
# running it directly) resolved nothing and reported a granted action as
# "no work-system" — failing safe, but silently disabling the feature in a real
# dev layout sitting right next to it.

# Captured at SOURCE time: inside a function BASH_SOURCE[0] is the function's
# defining file, which is this one, but capturing here keeps it obvious.
WS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || WS_LIB_DIR=""

# Print the absolute path of <relative-path> inside work-system, or nothing.
ws_find() {
  local rel="$1" root t
  [ -n "$rel" ] || return 0

  # Dev layout (repo checkout): plugins/pr-flow and plugins/work-system siblings.
  # Anchored on this file's own location first, then the env var.
  for root in "${WS_LIB_DIR:+$WS_LIB_DIR/..}" "${CLAUDE_PLUGIN_ROOT:-}"; do
    [ -n "$root" ] || continue
    t="$root/../work-system/$rel"
    [ -f "$t" ] && { printf '%s\n' "$t"; return 0; }
  done

  # Marketplace layout — resolve the installed work-system from Claude Code's
  # installed-plugins manifest, which lists only INSTALLED versions (unlike the
  # version cache, which is never pruned, so a newest-cached glob keeps
  # executing a version the user rolled back from). The manifest holds every
  # historical record in insertion order across scopes, so pick the HIGHEST
  # version (not entries[0], an arbitrary first record) — that matches a
  # rollback (the rolled-back-from version leaves the manifest even while it
  # lingers in cache).
  if command -v python3 >/dev/null 2>&1; then
    t="$(WS_REL="$rel" python3 - <<'PY' 2>/dev/null
import json, os, re
p = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
try:
    plugins = json.load(open(p))["plugins"]
except Exception:
    raise SystemExit
def vkey(v):
    # numeric-aware version sort; non-numeric segments sink below any real version
    return [int(x) if x.isdigit() else -1 for x in re.split(r"[.\-+]", str(v))]
best = None
for key, entries in plugins.items():
    if key.split("@", 1)[0] != "work-system":
        continue
    for e in entries or []:
        ip = e.get("installPath") or ""
        if ip and (best is None or vkey(e.get("version", "")) > best[0]):
            best = (vkey(e.get("version", "")), ip)
if best:
    print(os.path.join(best[1], os.environ["WS_REL"]))
PY
)"
    [ -n "$t" ] && [ -f "$t" ] && { printf '%s\n' "$t"; return 0; }
  fi

  # Fallback (manifest missing/unparsable): newest cached work-system version —
  # <cache>/<marketplace>/{pr-flow,work-system}/<version>/…; paths differ only
  # in the version segment, so a line-wise sort -V orders them. Heuristic: after
  # a rollback this can pick a newer-than-enabled version (accepted limitation —
  # the manifest path above is the accurate one).
  for root in "${CLAUDE_PLUGIN_ROOT:-}" "${WS_LIB_DIR:+$WS_LIB_DIR/..}"; do
    [ -n "$root" ] || continue
    t="$(printf '%s\n' "$root"/../../work-system/*/"$rel" 2>/dev/null | sort -V | tail -1)"
    [ -n "$t" ] && [ -f "$t" ] && { printf '%s\n' "$t"; return 0; }
  done
  return 0
}
