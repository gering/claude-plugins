#!/usr/bin/env bash
# kv.sh — read one value from `key=value` lines on stdin.
#
#   kv.sh get <key> [<charset>]
#
# Prints the value of the FIRST line that starts with `<key>=` (empty when no
# line does), optionally reduced to the characters of a `tr` set. Exit 0 either
# way; exit 2 on a malformed call.
#
# Why a script: the review skill's prep block used inline helpers like
# `sed -n "s/^$1=//p"`. Claude Code substitutes `$<digit>` in SKILL.md content
# with the skill's arguments at render time (`$1` = the second argument), so
# `/swarm:review --pr 65` rendered every lookup as `s/^65=//p` and the codex and
# grok model selections came back empty. The callers now forward `"$@"`, which is
# not a substitution token, and the key matching lives here, read from disk.
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
