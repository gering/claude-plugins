#!/bin/bash
# Emit the [ks §N ◈M] (knowledge-system) status block for a given workspace dir.
#
# Usage: statusline-cks.sh <workspace-dir>
# Output: ANSI-coloured string like "[ks §12 ◈34*2+1]" — no trailing newline.
#         Empty if neither .claude/rules nor .claude/knowledge exist in DIR,
#         or if <DIR>/.claude/.cks-statusline-off sentinel is present.

# Parsed by /knowledge-system:statusline install — bump in lockstep with
# plugin.json. Format must stay `readonly CKS_STATUSLINE_VERSION="X.Y.Z"`.
readonly CKS_STATUSLINE_VERSION="1.1.0"

DIR="${1:-}"
[ -z "$DIR" ] && exit 0

# Honour per-project disable sentinel
[ -f "${DIR}/.claude/.cks-statusline-off" ] && exit 0

# Only render if the project has a knowledge-system layout. Without one,
# exit silently so the ks block does not appear in unrelated projects.
[ -d "$DIR/.claude/knowledge" ] || [ -d "$DIR/.claude/rules" ] || exit 0

YELLOW='\033[33m'
GREEN='\033[32m'
BLUE_BRIGHT='\033[38;5;39m'
RESET='\033[0m'

# Type glyphs, one per count category (single-width, same ambiguous-width class
# as the work-system [ws] set). These are TYPE icons, not ws's STATE glyphs.
GLYPH_RULES='§'
GLYPH_KNOW='◈'
GLYPH_PROJ='❖'   # legacy repo-root knowledge/ (third column)

cks_fmt() {
  local count=$1 mod=$2 unt=$3 accent=$4
  local out="${count}"
  [ "$mod" -gt 0 ] && out="${out}${YELLOW}*${mod}${accent}"
  [ "$unt" -gt 0 ] && out="${out}${GREEN}+${unt}${accent}"
  echo "$out"
}

# Count tracked changes (staged or unstaged) and untracked files separately.
# `git status --porcelain` outputs `XY <path>`: X = index status, Y = worktree
# status, `??` = untracked. `^[MADRCU]` alone would miss the common ` M file`
# case (unstaged worktree edit), so split as ALL-minus-untracked.
count_changes() {
  local pathspec=$1 kind=$2 all unt
  all=$(git -C "$DIR" status --porcelain -- "$pathspec" 2>/dev/null | grep -c .)
  unt=$(git -C "$DIR" status --porcelain -- "$pathspec" 2>/dev/null | grep -c '^??')
  if [ "$kind" = "untracked" ]; then
    echo "$unt"
  else
    echo $((all - unt))
  fi
}

RULES_COUNT=0; RULES_MOD=0; RULES_UNT=0
KNOW_COUNT=0;  KNOW_MOD=0;  KNOW_UNT=0

if [ -d "$DIR/.claude/rules" ]; then
  RULES_COUNT=$(find "$DIR/.claude/rules" -name '*.md' ! -name '_index.md' ! -name 'README.md' 2>/dev/null | wc -l | tr -d ' ')
  RULES_MOD=$(count_changes .claude/rules tracked)
  RULES_UNT=$(count_changes .claude/rules untracked)
fi
if [ -d "$DIR/.claude/knowledge" ]; then
  KNOW_COUNT=$(find "$DIR/.claude/knowledge" -name '*.md' ! -name '_index.md' ! -name 'README.md' 2>/dev/null | wc -l | tr -d ' ')
  KNOW_MOD=$(count_changes .claude/knowledge tracked)
  KNOW_UNT=$(count_changes .claude/knowledge untracked)
fi

PARTS="${GLYPH_RULES}$(cks_fmt "$RULES_COUNT" "$RULES_MOD" "$RULES_UNT" "$BLUE_BRIGHT") ${GLYPH_KNOW}$(cks_fmt "$KNOW_COUNT" "$KNOW_MOD" "$KNOW_UNT" "$BLUE_BRIGHT")"

# Optional third column: project-level knowledge/ at the repo root (a convention
# from project-knowledge layouts that predate .claude/knowledge). Surfaced
# without forcing migration.
if [ -f "$DIR/knowledge/_index.md" ]; then
  PROJ_COUNT=$(find "$DIR/knowledge" -name '*.md' ! -name '_index.md' ! -name 'README.md' 2>/dev/null | wc -l | tr -d ' ')
  PROJ_MOD=$(count_changes knowledge tracked)
  PROJ_UNT=$(count_changes knowledge untracked)
  PARTS="${PARTS} ${GLYPH_PROJ}$(cks_fmt "$PROJ_COUNT" "$PROJ_MOD" "$PROJ_UNT" "$BLUE_BRIGHT")"
fi

printf '%b[ks %b]%b' "$BLUE_BRIGHT" "$PARTS" "$RESET"
