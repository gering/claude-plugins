#!/usr/bin/env bash
# kv.sh — read one value from `key=value` lines on stdin.
#
#   kv.sh get <key> [<charset>]
#
# Prints the value of the FIRST line that starts with `<key>=` (empty when no
# line does), optionally reduced to the characters of a `tr` set. Exit 0 either
# way; exit 2 on a malformed call.
#
# A script because SKILL.md content is a template: Claude Code substitutes
# `$<digit>` with the skill's arguments, so an inline helper cannot name a
# positional parameter. Callers forward `"$@"` instead (see the knowledge entry
# architecture/skill-design-conventions.md).
set -euo pipefail

usage() { echo "usage: kv.sh get <key> [<charset>]" >&2; exit 2; }

[ "$#" -ge 2 ] && [ "$#" -le 3 ] && [ "$1" = get ] || usage
key="$2"
case "$key" in ''|*[!A-Za-z0-9_]*) echo "kv.sh: invalid key: $key" >&2; exit 2 ;; esac

# Read all of stdin (no early exit) so an upstream `printf | kv.sh` never takes
# SIGPIPE under a caller's `set -o pipefail`.
val="$(awk -v k="$key=" '!found && index($0, k) == 1 { print substr($0, length(k) + 1); found = 1 }')"
if [ "$#" -eq 3 ]; then
  val="$(printf '%s' "$val" | tr -cd "$3")"
fi
printf '%s\n' "$val"
