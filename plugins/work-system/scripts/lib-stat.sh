#!/usr/bin/env bash
# lib-stat.sh — portable `stat` utilities for BSD/GNU compatibility.
#
# SOURCE this file (`. "<dir>/lib-stat.sh"`); it defines functions and runs
# nothing. Both insights-handoff.sh and archive-task.sh need the same portable
# stat helpers for cross-platform inode and link-count lookups.
#
# The two implementations disagree fundamentally on the -f flag: BSD reads it as
# the format string, GNU as --file-system. A fallback like `stat -f '%i' X ||
# stat -c '%i' X` does NOT degrade cleanly — on GNU the first call prints
# filesystem info (exit non-zero because the format operand is not a path), and
# the output is appended to the fallback's real answer inside the same `$( )`.
# The caller then compares multi-line strings and refuses every lookup. This
# script probes ONCE and picks the dialect instead.

# Portable inode / link-count lookups. Probed once at sourcing time and cached
# in STAT_STYLE so every call reads the global directly, avoiding redundant
# subprocess spawns inside command substitution.
STAT_STYLE=""

# Detect stat dialect (gnu or bsd) once at sourcing time.
# Called automatically below; do not call this directly.
_init_stat_style() {
  if stat --version >/dev/null 2>&1; then
    STAT_STYLE=gnu
  elif stat -f '%i' . >/dev/null 2>&1; then
    STAT_STYLE=bsd
  else
    STAT_STYLE=none
  fi
}

# stat_field <inode|links> <path> [--deref] — prints the number, or nothing.
# Uses STAT_STYLE global set at sourcing time, avoiding redundant probes.
stat_field() {
  local what="$1" path="$2" deref="${3:-}"
  case "$STAT_STYLE:$what" in
    gnu:inode) stat ${deref:+-L} -c '%i' -- "$path" 2>/dev/null ;;
    gnu:links) stat ${deref:+-L} -c '%h' -- "$path" 2>/dev/null ;;
    bsd:inode) stat ${deref:+-L} -f '%i' -- "$path" 2>/dev/null ;;
    bsd:links) stat ${deref:+-L} -f '%l' -- "$path" 2>/dev/null ;;
    *) return 0 ;;
  esac
  # Always succeed: the caller reads the VALUE (empty means unknown) and decides.
  # Under `set -e` a failing substitution aborts the assignment itself, which
  # turned a missing file into exit 1 instead of the caller's deliberate exit 2.
  return 0
}

# Initialize stat style once at sourcing time.
_init_stat_style
