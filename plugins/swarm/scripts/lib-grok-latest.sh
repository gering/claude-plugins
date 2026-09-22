#!/usr/bin/env bash
# lib-grok-latest.sh — "which canonical Grok is the newest one this CLI offers?"
#
# SOURCE this file; it defines functions and runs nothing. It ships BYTE-IDENTICAL
# in plugins/swarm/scripts/ and plugins/work-system/scripts/ (pinned by
# test_grok_latest.py in both): the two plugins must agree on what "latest"
# means, but neither may require the other to be installed, so the algorithm is
# one file with two homes rather than two algorithms. Edit one, copy to the other.
#
# Pure text in, text out: no network, no `grok` call, no globals written. The
# caller fetches `grok models` under its OWN bounded-probe contract and hands the
# raw listing in. Runs on stock bash 3.2 (no associative arrays, no `=~`).
#
# What "canonical" means here — exactly `grok-(4|5).<minor>`:
#   accepted: grok-4.5  grok-4.7  grok-4.20  grok-5.0
#   rejected: bare majors (grok-5), patch versions (grok-4.7.1), every suffix
#             (grok-4.7-build-fast, grok-4.20-0309-reasoning, -preview, dated,
#             composer, imagine), majors 3 and 6+, and anything malformed.
# A rejected id can still be run as a deliberate explicit pin — it is only never
# chosen AUTOMATICALLY. Major 6+ is excluded on purpose: a new generation is a
# decision (pricing, behavior), not a drop-in upgrade; raising the ceiling is a
# one-line edit here.

# Longest minor accepted. A catalog is untrusted input: an absurd digit run would
# overflow the shell arithmetic below, and no real release is near 4 digits.
GROK_LATEST_MINOR_MAX_DIGITS=4

# grok_latest_is_canonical <id> — rc 0 iff <id> may be selected automatically.
grok_latest_is_canonical() {
  local minor
  case "$1" in
    grok-4.*|grok-5.*) ;;
    *) return 1 ;;
  esac
  minor="${1#grok-?.}"
  case "$minor" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "${#minor}" -le "$GROK_LATEST_MINOR_MAX_DIGITS" ]
}

# grok_latest_newer <a> <b> — rc 0 iff canonical <a> is strictly newer than
# canonical <b>. Integer major, then integer minor: 5.0 > 4.20 > 4.9. Never a
# string or decimal compare (4.20 is the 20th minor, not "4.2"). A non-canonical
# argument answers "not newer", so a caller keeps what it had.
grok_latest_newer() {
  grok_latest_is_canonical "$1" || return 1
  grok_latest_is_canonical "$2" || return 1
  local a="${1#grok-}" b="${2#grok-}"
  # 10# forces decimal: "08"/"09" are digit-only yet invalid octal.
  local a_major=$((10#${a%%.*})) a_minor=$((10#${a#*.}))
  local b_major=$((10#${b%%.*})) b_minor=$((10#${b#*.}))
  if [ "$a_major" -ne "$b_major" ]; then
    [ "$a_major" -gt "$b_major" ]
    return
  fi
  [ "$a_minor" -gt "$b_minor" ]
}

# grok_latest_pick — model ids on stdin (one per line) → the newest canonical id
# on stdout, or nothing when none qualifies.
grok_latest_pick() {
  local best="" id
  while IFS= read -r id || [ -n "$id" ]; do
    grok_latest_is_canonical "$id" || continue
    if [ -z "$best" ] || grok_latest_newer "$id" "$best"; then best="$id"; fi
  done
  printf '%s' "$best"
}

# grok_latest_parse — raw `grok models` listing on stdin → offered ids, one per
# line. One id per BULLET line (`*` or `-`), the id as the FIRST token, followed
# by nothing or a BRACKETED annotation; withdrawal wording drops the line. The
# asymmetry is deliberate: harvesting prose as a model selects an id the CLI does
# not offer (silent loss at launch), while rejecting an unfamiliar line style
# empties the list (a loud, reported degrade).
grok_latest_parse() {
  # NOTE: no apostrophe may appear inside this awk program, comments included —
  # it would end the shell quoting and silently corrupt the parser.
  awk '
    /^[[:space:]]*[*-][[:space:]]/ {
      low = tolower($0)
      if (low ~ /(retired|deprecated|unavailable|not available|sunset|end of life|end-of-life|coming soon|disabled|removed)/) next
      if (NF >= 2 && (NF == 2 || $3 ~ /^[(\[{]/)) {
        tok = $2
        gsub(/[`"]/, "", tok)
        sub(/[.,;:]+$/, "", tok)
        if (tok ~ /^grok-[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$/) print tok
      }
    }
'
}

# grok_latest_from_listing <fetch_rc> — raw listing on stdin, the fetch's exit
# code as $1. Prints `key=value` lines and always returns 0; the STATE is the
# answer, and the four states are deliberately distinct:
#   catalog=unreachable   the fetch failed/timed out — says NOTHING about models
#   catalog=unparseable   fetched, but no model id could be read from it
#   catalog=no-candidate  a valid catalog that offers no canonical 4.x/5.x
#   catalog=ok            latest=<id> is the newest canonical id on offer
# `offered=` carries every parsed id (space-separated) for pin membership checks.
grok_latest_from_listing() {
  local fetch_rc="${1:-0}" ids latest
  if [ "$fetch_rc" -ne 0 ]; then
    printf 'catalog=unreachable\nfetch_rc=%s\n' "$fetch_rc"
    return 0
  fi
  ids="$(grok_latest_parse)"
  if [ -z "$ids" ]; then
    printf 'catalog=unparseable\n'
    return 0
  fi
  latest="$(printf '%s\n' "$ids" | grok_latest_pick)"
  if [ -z "$latest" ]; then
    printf 'catalog=no-candidate\n'
  else
    printf 'catalog=ok\nlatest=%s\n' "$latest"
  fi
  printf 'offered=%s\n' "$(printf '%s\n' "$ids" | tr '\n' ' ' | sed 's/ *$//')"
}
