#!/usr/bin/env bash
# workers.sh — the status part of /work-system:manager: list the herdr workers,
# read their screens and pending questions, and raise deduplicated status-change
# events. The logic is in workers.py (see its header for usage, flags, the event
# payload and exit codes); this entry point only hands it the shared pieces of
# herdr-agent.sh, so the path classifier, the session-name sanitizer and the
# composer draft check each live in one place.
#
#   workers.sh list [--all | --workspace <wN>] [--json]
#   workers.sh screen <pane> [--lines N]
#   workers.sh question <pane>
#   workers.sh ready <worktree | pane>
#   workers.sh events --state <file> [--all | --workspace <wN>] [--ping]
#                     [--on-blocked <cmd>] [--escalate-after <secs>]
set -u
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=herdr-agent.sh
. "$SCRIPT_DIR/herdr-agent.sh"
command -v python3 >/dev/null 2>&1 || { echo "workers: python3 not on PATH" >&2; exit 3; }
export HERDR_COMPOSER_PY HERDR_MATCH_PRELUDE HERDR_NAME_PRELUDE
exec python3 -I "$SCRIPT_DIR/workers.py" "$@"
