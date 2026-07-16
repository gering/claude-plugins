#!/bin/bash
# Emit the [ws ...] work-system status block for a given workspace dir.
#
# Usage: ws-statusline.sh <workspace-dir>
# Output: ANSI-coloured string like "[ws ○2 ●1 ◇1 ✓1]" — no trailing newline.
#         Single-width glyphs (only non-zero columns shown):
#           ○ not-started · ● active (has a worktree) · ◇ in review (open PR) ·
#           ✓ merged (ready to /close).
#         Empty when DIR is not a git repo, has no tasks/ backlog, an empty
#         backlog, or the <DIR>/.claude/.ws-statusline-off sentinel is present.
#
# Second mode (used by herdr-tab-glyph.sh, NOT by the status line):
#   ws-statusline.sh states <workspace-dir>
# prints one "<task>\t<state>\t<glyph>" line per backlog task (state ∈
# not-started|active|review|merged) and refreshes the PR cache SYNCHRONOUSLY
# first — its callers are skill triggers reacting to a PR state change and need
# the post-change state, not the cached one. Same tasks, same precedence, same
# glyphs as the rendered segment: one file, so the two surfaces cannot drift.
#
# Self-contained by design: the installer copies THIS file to
# ~/.claude/ws-statusline.sh with no siblings, so it must not source other
# work-system scripts at render time.

# Parsed by /work-system:statusline install — bump in lockstep with plugin.json.
# Format must stay `readonly WS_STATUSLINE_VERSION="X.Y.Z"`.
readonly WS_STATUSLINE_VERSION="1.1.0"

MODE=render
[ "${1:-}" = "states" ] && { MODE=states; shift; }

DIR="${1:-}"
[ -z "$DIR" ] && exit 0

# Honour per-project disable sentinel (render only — it silences the status
# LINE; states feeds the herdr tab glyphs, a different surface).
[ "$MODE" = render ] && [ -f "${DIR}/.claude/.ws-statusline-off" ] && exit 0

# Only render inside a git repo (tasks/ lives in the main worktree).
git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1 || exit 0

# Resolve the MAIN worktree — the shared tasks/ backlog lives there, not in a
# linked worktree. `git worktree list --porcelain` lists main first; strip the
# "worktree " prefix without field-splitting so paths with spaces survive.
MAIN="$(git -C "$DIR" worktree list --porcelain 2>/dev/null | head -1)"
MAIN="${MAIN#worktree }"
[ -n "$MAIN" ] && [ -d "$MAIN/tasks" ] || exit 0

# Branches that currently have a linked worktree → the "active" set. `sed -n`
# extracts the short branch name from each `branch refs/heads/<name>` line.
WORKTREE_BRANCHES="$(git -C "$DIR" worktree list --porcelain 2>/dev/null \
  | sed -n 's#^branch refs/heads/##p')"

# ---- PR cache (never blocks render) -----------------------------------------
# Render reads ONLY a cache file (headRef<TAB>state); a stale/missing cache
# triggers a DETACHED background `gh` refresh that writes it for the NEXT render.
# So a render never waits on the network — the first render after a change shows
# PR columns one refresh late, which is fine for a glanceable segment.

# Absolute path of the shared git dir (common across worktrees) → one cache per
# repo, stored inside .git so it never dirties the working tree.
GIT_COMMON="$(cd "$DIR" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd)"
CACHE="${GIT_COMMON:+$GIT_COMMON/ws-statusline-prs}"

# Refresh needed when the cache is missing or older than the TTL (1 minute).
refresh_needed() {
  [ -f "$CACHE" ] || return 0
  [ -z "$(find "$CACHE" -mmin -1 2>/dev/null)" ]
}

# One gh invocation shared by both refresh paths (async render / sync states).
# Prints "headRef\tstate" rows; non-zero when gh fails. Bounded so a hung gh
# can't stall a caller — `timeout` isn't on stock macOS, use it only if present.
fetch_prs() {
  local gh_to=""
  command -v timeout >/dev/null 2>&1 && gh_to="timeout 20"
  # --limit 500 is a bounded cache: a repo with more matching PRs than this
  # could drop a task's branch from the window, but backlog tasks have recent
  # PRs and 500 is far above the old 100 cliff — acceptable for a glance segment.
  (cd "$DIR" && $gh_to gh pr list --state all --limit 500 \
     --json state,headRefName \
     --jq '.[] | "\(.headRefName)\t\(.state)"' 2>/dev/null)
}

maybe_refresh_prs() {
  [ -n "$CACHE" ] || return 0
  command -v gh >/dev/null 2>&1 || return 0
  refresh_needed || return 0
  local lock="$CACHE.lock"
  # Clear a dead lock (a refresh that died mid-flight) older than 5 minutes.
  if [ -d "$lock" ] && [ -z "$(find "$lock" -mmin -5 2>/dev/null)" ]; then
    rmdir "$lock" 2>/dev/null || true
  fi
  # mkdir is atomic: whoever wins runs the single refresh; everyone else skips.
  mkdir "$lock" 2>/dev/null || return 0
  # Detach with stdout/stderr closed so the host statusline's command
  # substitution does not wait on this child holding the pipe open.
  (
    if out="$(fetch_prs)"; then
      printf '%s\n' "$out" > "$CACHE.tmp.$$" && mv "$CACHE.tmp.$$" "$CACHE"
    else
      # gh failed (offline / not authed): reset the mtime so we back off for a
      # full TTL instead of retrying every render — but keep any prior data.
      touch "$CACHE" 2>/dev/null || : > "$CACHE" 2>/dev/null || true
    fi
    rmdir "$lock" 2>/dev/null || true
  ) >/dev/null 2>&1 &
}

# states mode refreshes the cache INLINE (no TTL, no lock): its callers fire
# right after a PR changed state, so the async path would hand them the
# pre-change state. A failed fetch keeps the previous cache; racing an in-flight
# async refresh is benign (both writes are atomic tmp+mv of valid data).
sync_refresh_prs() {
  [ -n "$CACHE" ] || return 0
  command -v gh >/dev/null 2>&1 || return 0
  local out
  if out="$(fetch_prs)"; then
    printf '%s\n' "$out" > "$CACHE.tmp.$$" && mv "$CACHE.tmp.$$" "$CACHE"
  fi
}

# State of the PR whose head branch == $1, from the cache. A branch reused across
# PRs has several rows (`gh --state all`) in no guaranteed order, so pick the most
# authoritative state (MERGED > OPEN > CLOSED > other) instead of the first row —
# a first-match `exit` could surface a stale CLOSED over a newer OPEN/MERGED.
pr_state_of() {
  [ -n "$CACHE" ] && [ -f "$CACHE" ] || return 0
  awk -F'\t' -v b="$1" '
    $1==b {
      p = ($2=="MERGED")?3 : ($2=="OPEN")?2 : ($2=="CLOSED")?1 : 0
      if (p > best) { best=p; st=$2 }
    }
    END { if (st != "") print st }
  ' "$CACHE" 2>/dev/null
}

if [ "$MODE" = states ]; then sync_refresh_prs; else maybe_refresh_prs; fi

# ---- task state (single decision for BOTH modes) ----------------------------

# Does branch $1 currently have a linked worktree?
has_worktree() { printf '%s\n' "$WORKTREE_BRANCHES" | grep -qxF "$1"; }

# State of the task named $1: PR state wins (merged > open); a PR closed
# WITHOUT merging means the task is not done and falls back to the local
# signals — a linked worktree → active, otherwise back in the backlog.
# Branch is the /kickoff convention; adopt-renamed branches are an accepted
# blind spot for this glance surface.
task_state() {  # echoes not-started | active | review | merged
  local branch="task/$1" pr
  pr="$(pr_state_of "$branch")"
  if [ "$pr" = "MERGED" ]; then echo merged        # ✓ ready to /close
  elif [ "$pr" = "OPEN" ]; then echo review        # ◇ in review
  elif has_worktree "$branch"; then echo active    # ● has a worktree
  else echo not-started                            # ○ backlog
  fi
}

# The state→glyph mapping — THE single source for the status line AND the
# herdr tab glyphs (herdr-tab-glyph.sh consumes it via states mode).
glyph_of() {
  case "$1" in
    merged) printf '✓' ;;
    review) printf '◇' ;;
    active) printf '●' ;;
    *)      printf '○' ;;
  esac
}

# ---- walk the backlog -------------------------------------------------------
# Non-recursive tasks/*.md, excluding index/readme; tasks/archive/ is a subdir
# and so is naturally excluded by -maxdepth 1.

TOTAL=0; NS=0; ACT=0; PR=0; DONE=0
# -print0 + `read -d ''` so a task filename with an embedded newline can't split
# into two iterations; process substitution (not a pipe) keeps the counters in
# THIS shell.
while IFS= read -r -d '' f; do
  TOTAL=$((TOTAL + 1))
  name="$(basename "$f" .md)"
  state="$(task_state "$name")"
  if [ "$MODE" = states ]; then
    printf '%s\t%s\t%s\n' "$name" "$state" "$(glyph_of "$state")"
    continue
  fi
  case "$state" in
    merged) DONE=$((DONE + 1)) ;;
    review) PR=$((PR + 1)) ;;
    active) ACT=$((ACT + 1)) ;;
    *)      NS=$((NS + 1)) ;;
  esac
done < <(find "$MAIN/tasks" -maxdepth 1 -type f -name '*.md' \
    ! -name '_index.md' ! -name 'README.md' -print0 2>/dev/null)

[ "$MODE" = states ] && exit 0
[ "$TOTAL" -eq 0 ] && exit 0

# ---- render -----------------------------------------------------------------
# Muted, single-width glyphs (not emoji) so stacked statusline segments stay
# calm and aligned. Each column is self-coloured then reset; zero columns are
# dropped. TOTAL>0 guarantees at least one column, so SEG is never empty here.
LABEL='\033[38;5;170m'    # ws label — purple
C_NS='\033[38;5;245m'     # ○ not started — grey (passive backlog)
C_AC='\033[38;5;39m'      # ● active — blue (in progress)
C_RV='\033[38;5;179m'     # ◇ in review — amber (waiting on a PR)
C_MG='\033[38;5;40m'      # ✓ merged — green (ready to /close)
RESET='\033[0m'

SEG=""
add_col() {   # count color glyph
  [ "$1" -gt 0 ] || return 0
  SEG="${SEG:+$SEG }${2}${3}${1}${RESET}"
}
add_col "$NS"   "$C_NS" "$(glyph_of not-started)"
add_col "$ACT"  "$C_AC" "$(glyph_of active)"
add_col "$PR"   "$C_RV" "$(glyph_of review)"
add_col "$DONE" "$C_MG" "$(glyph_of merged)"

printf '%b[ws %b%b]%b' "$LABEL" "$SEG" "$LABEL" "$RESET"
