#!/bin/bash
# Emit the [cks N|M] (knowledge-system) status block for a given workspace dir.
#
# Usage: statusline-cks.sh <workspace-dir>
# Output: ANSI-coloured string like "[cks 12|34*2+1]" — no trailing newline.
#         Empty if neither .claude/rules nor .claude/knowledge exist in DIR,
#         or if <DIR>/.claude/.cks-statusline-off sentinel is present.
#
# CKS_STATUSLINE_VERSION=1.0.0

DIR="${1:-}"
[ -z "$DIR" ] && exit 0

# Honour per-project disable sentinel
[ -f "${DIR}/.claude/.cks-statusline-off" ] && exit 0

# Only render if the project has a knowledge-system layout
if [ ! -d "$DIR/.claude/knowledge" ] && [ ! -d "$DIR/.claude/rules" ]; then
  printf '\033[2mcks\033[0m'
  exit 0
fi

YELLOW='\033[33m'
GREEN='\033[32m'
BLUE_BRIGHT='\033[38;5;39m'
RESET='\033[0m'

cks_fmt() {
  local count=$1 mod=$2 unt=$3 accent=$4
  local out="${count}"
  [ "$mod" -gt 0 ] 2>/dev/null && out="${out}${YELLOW}*${mod}${accent}"
  [ "$unt" -gt 0 ] 2>/dev/null && out="${out}${GREEN}+${unt}${accent}"
  echo "$out"
}

RULES_COUNT=0; RULES_MOD=0; RULES_UNT=0
KNOW_COUNT=0;  KNOW_MOD=0;  KNOW_UNT=0

if [ -d "$DIR/.claude/rules" ]; then
  RULES_COUNT=$(find "$DIR/.claude/rules" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  RULES_MOD=$(git -C "$DIR" status --porcelain -- .claude/rules 2>/dev/null | grep -c '^[MADRCU]' || true)
  RULES_UNT=$(git -C "$DIR" status --porcelain -- .claude/rules 2>/dev/null | grep -c '^??' || true)
fi
if [ -d "$DIR/.claude/knowledge" ]; then
  KNOW_COUNT=$(find "$DIR/.claude/knowledge" -name '*.md' ! -name '_index.md' ! -name 'README.md' 2>/dev/null | wc -l | tr -d ' ')
  KNOW_MOD=$(git -C "$DIR" status --porcelain -- .claude/knowledge 2>/dev/null | grep -c '^[MADRCU]' || true)
  KNOW_UNT=$(git -C "$DIR" status --porcelain -- .claude/knowledge 2>/dev/null | grep -c '^??' || true)
fi

PARTS="$(cks_fmt "$RULES_COUNT" "$RULES_MOD" "$RULES_UNT" "$BLUE_BRIGHT")|$(cks_fmt "$KNOW_COUNT" "$KNOW_MOD" "$KNOW_UNT" "$BLUE_BRIGHT")"

# Optional third column: project-level knowledge/ when an _index.md exists
if [ -f "$DIR/knowledge/_index.md" ]; then
  PROJ_COUNT=$(find "$DIR/knowledge" -name '*.md' ! -name '_index.md' ! -name 'README.md' 2>/dev/null | wc -l | tr -d ' ')
  PROJ_MOD=$(git -C "$DIR" status --porcelain -- knowledge 2>/dev/null | grep -c '^[MADRCU]' || true)
  PROJ_UNT=$(git -C "$DIR" status --porcelain -- knowledge 2>/dev/null | grep -c '^??' || true)
  PARTS="${PARTS}|$(cks_fmt "$PROJ_COUNT" "$PROJ_MOD" "$PROJ_UNT" "$BLUE_BRIGHT")"
fi

printf '%b[cks %b]%b' "$BLUE_BRIGHT" "$PARTS" "$RESET"
