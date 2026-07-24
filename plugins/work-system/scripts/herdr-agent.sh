#!/usr/bin/env bash
# herdr-agent.sh — the ONE wrapper over the herdr *agent* primitives, plus the
# ONE realpath cwd↔worktree classification the lane registry (lanes.sh) and the
# tab-glyph refresh (herdr-tab-glyph.sh) share.
#
# Two ways to use it:
#
#   1. As a CLI — thin, guarded wrappers over `herdr agent …`:
#        herdr-agent.sh list                       (validated agent-list JSON)
#        herdr-agent.sh get  <target>              (validated single-agent JSON)
#        herdr-agent.sh read <target> [flags…]     (best-effort text, never a schema)
#        herdr-agent.sh wait <target> --status S [--timeout MS]   (bounded)
#      Every wrapper DEGRADES rather than blocks: a missing herdr/python3, an
#      unreachable server, or malformed JSON is a non-zero exit with empty
#      stdout — never a hang, never a partial/garbage line. Exit codes below.
#
#   2. As a sourced library — `. herdr-agent.sh` pulls in, with NO side effects:
#        $HERDR_MATCH_PRELUDE   a python3 source string defining match_roots()
#                               and classify_cwd() — prepend it to a consumer
#                               python snippet so the cwd match logic is reused,
#                               never copied (the "realpath cwd match prelude").
#        ha_have / ha_list / ha_get / ha_read / ha_wait   the shell wrappers.
#      Sourcing must stay side-effect free (the CLI dispatch at the foot is
#      guarded by "am I the executed script?") so a consumer can take the
#      prelude without running a subcommand.
#
# Exit codes (CLI + ha_* functions):
#   0  success
#   3  herdr and/or python3 not on PATH        (degrade — tools absent)
#   4  herdr call failed / empty output         (degrade — server unreachable)
#   5  output was not the expected JSON shape   (degrade — malformed)
# read/wait forward herdr's own non-zero (e.g. wait timeout) unchanged.
set -u

# ---- shared realpath cwd↔worktree match (the "prelude") ---------------------
# A python3 source string, apostrophe-free so it stays single-quotable. Both
# consumers prepend it to their own snippet:
#     python3 -c "$HERDR_MATCH_PRELUDE"$'\n'"$their_snippet" …
# so the exact-match philosophy (mirrors herdr-teardown.sh: an agent merely cd'd
# into a SUBDIR of a worktree/root is neither) lives in exactly one place.
HERDR_MATCH_PRELUDE='import os
def match_roots(main):
    # Realpath the repo root and its worktrees dir once. Empty main -> (None, None)
    # so a caller can bail before touching agents. realpath the whole worktrees
    # path (not just root): a symlinked .claude/worktrees would otherwise never
    # match, since the cwd side below is resolved.
    if not main or not main.strip():
        return None, None
    root = os.path.realpath(main)
    wtdir = os.path.realpath(os.path.join(root, ".claude", "worktrees"))
    return root, wtdir

def classify_cwd(cwd, root, wtdir):
    # Exact realpath match. Returns:
    #   ("main", <repo dir name>)        cwd IS the main repo root
    #   ("task", <worktree dir name>)    cwd IS a direct child of the worktrees dir
    #   (None, None)                     neither (incl. a mere subdir of either)
    # cwd is realpath-resolved here; root/wtdir come pre-resolved from match_roots.
    cwd = (cwd or "").rstrip("/")
    if not cwd or root is None:
        return None, None
    cwd = os.path.realpath(cwd)
    if cwd == root:
        return "main", os.path.basename(root)
    if os.path.dirname(cwd) == wtdir:
        return "task", os.path.basename(cwd)
    return None, None'

# Default bound for `wait` when the caller passes none — a wrapper must never
# wait unboundedly (that would be the busy loop this script exists to avoid).
HA_WAIT_DEFAULT_TIMEOUT_MS="${HA_WAIT_DEFAULT_TIMEOUT_MS:-10000}"

# ---- primitive wrappers -----------------------------------------------------

# Both tools present? (herdr for the call, python3 for the JSON validate.)
ha_have() {
  command -v herdr   >/dev/null 2>&1 || return 1
  command -v python3 >/dev/null 2>&1 || return 1
}

# Validate that stdin is JSON whose result.<key> is a list; exit 0/1. Used to
# reject a malformed/blank blob before a consumer trusts it.
_ha_validate_list() {
  python3 -c 'import sys, json
key = sys.argv[1]
try:
    v = json.load(sys.stdin)["result"][key]
except Exception:
    sys.exit(1)
sys.exit(0 if isinstance(v, list) else 1)' "$1" 2>/dev/null
}

# `herdr agent list` → validated JSON on stdout (result.agents is a list).
ha_list() {
  ha_have || return 3
  local json
  json="$(herdr agent list 2>/dev/null)" || return 4
  [ -n "$json" ] || return 4
  printf '%s' "$json" | _ha_validate_list agents || return 5
  printf '%s\n' "$json"
}

# `herdr agent get <target>` → validated JSON on stdout (has a result object).
ha_get() {
  ha_have || return 3
  local target="${1:-}"
  [ -n "$target" ] || return 4
  local json
  json="$(herdr agent get "$target" 2>/dev/null)" || return 4
  [ -n "$json" ] || return 4
  printf '%s' "$json" | python3 -c 'import sys, json
try:
    d = json.load(sys.stdin)
    assert isinstance(d.get("result"), dict)
except Exception:
    sys.exit(1)' 2>/dev/null || return 5
  printf '%s\n' "$json"
}

# `herdr agent read <target> [flags…]` — best-effort, per-agent, NEVER format-
# required: whatever herdr prints is forwarded verbatim and herdr's own rc is
# returned. Only the tools-absent guard degrades to code 3. No JSON validation
# by design (read is free-form pane text, not a schema).
ha_read() {
  ha_have || return 3
  local target="${1:-}"
  [ -n "$target" ] || return 4
  shift
  herdr agent read "$target" "$@"
}

# `herdr agent wait <target> --status S [--timeout MS]` — BOUNDED: if the caller
# omits --timeout, inject the default so the call can never block forever. herdr
# blocks server-side until the status is reached or the timeout elapses (no busy
# poll here); its rc is forwarded (0 reached, non-zero timeout/error).
ha_wait() {
  ha_have || return 3
  local target="${1:-}"
  [ -n "$target" ] || return 4
  shift
  local a has_timeout=0
  for a in "$@"; do
    [ "$a" = "--timeout" ] && { has_timeout=1; break; }
  done
  if [ "$has_timeout" -eq 0 ]; then
    set -- "$@" --timeout "$HA_WAIT_DEFAULT_TIMEOUT_MS"
  fi
  herdr agent wait "$target" "$@"
}

# ---- CLI dispatch (only when executed directly, never when sourced) ---------
ha_main() {
  local cmd="${1:-}"
  [ $# -gt 0 ] && shift
  case "$cmd" in
    list) ha_list ;;
    get)  ha_get  "$@" ;;
    read) ha_read "$@" ;;
    wait) ha_wait "$@" ;;
    *)
      echo "usage: ${0##*/} {list | get <target> | read <target> [flags…] | wait <target> --status S [--timeout MS]}" >&2
      return 2
      ;;
  esac
}

# BASH_SOURCE[0] == $0 only when this file is the executed program; when another
# script sources it for $HERDR_MATCH_PRELUDE / the ha_* helpers the two differ,
# so no subcommand runs.
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ] && [ -n "${BASH_SOURCE[0]:-}" ]; then
  ha_main "$@"
  exit $?
fi
