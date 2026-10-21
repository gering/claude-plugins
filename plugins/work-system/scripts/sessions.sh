#!/usr/bin/env bash
# sessions.sh — running herdr sessions vs. installed plugin versions, and an
# idle-only `/reload-plugins` refresh. The logic is in sessions.py (see its
# header for usage, verdicts and exit codes); this entry point only hands it
# the shared composer draft check, so the "never type into a user draft" rule
# lives in herdr-agent.sh alone.
#
#   sessions.sh inventory [--json]
#   sessions.sh refresh [--workspace <id> | --all [--yes --only <pane@pid,...>]] [--wait-ms <n>] [--json]
set -u
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=herdr-agent.sh
. "$SCRIPT_DIR/herdr-agent.sh"
export HERDR_COMPOSER_PY
exec python3 "$SCRIPT_DIR/sessions.py" "$@"
