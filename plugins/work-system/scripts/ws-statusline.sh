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
# Self-contained by design: the installer copies THIS file to
# ~/.claude/ws-statusline.sh with no siblings, so it must not source other
# work-system scripts at render time.

# Parsed by /work-system:statusline install — bump in lockstep with plugin.json.
# Format must stay `readonly WS_STATUSLINE_VERSION="X.Y.Z"`.
readonly WS_STATUSLINE_VERSION="1.0.0"

DIR="${1:-}"
[ -z "$DIR" ] && exit 0

# Honour per-project disable sentinel.
[ -f "${DIR}/.claude/.ws-statusline-off" ] && exit 0

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
  # Bound the refresh so a hung gh can't hold the lock until the 5-min stale
  # sweep. `timeout` isn't on stock macOS — use it only when present.
  local gh_to=""
  command -v timeout >/dev/null 2>&1 && gh_to="timeout 20"
  # Detach with stdout/stderr closed so the host statusline's command
  # substitution does not wait on this child holding the pipe open.
  (
    # --limit 500 is a bounded cache: a repo with more matching PRs than this
    # could drop a task's branch from the window, but backlog tasks have recent
    # PRs and 500 is far above the old 100 cliff — acceptable for a glance segment.
    if out="$(cd "$DIR" && $gh_to gh pr list --state all --limit 500 \
                --json state,headRefName \
                --jq '.[] | "\(.headRefName)\t\(.state)"' 2>/dev/null)"; then
      printf '%s\n' "$out" > "$CACHE.tmp.$$" && mv "$CACHE.tmp.$$" "$CACHE"
    else
      # gh failed (offline / not authed): reset the mtime so we back off for a
      # full TTL instead of retrying every render — but keep any prior data.
      touch "$CACHE" 2>/dev/null || : > "$CACHE" 2>/dev/null || true
    fi
    rmdir "$lock" 2>/dev/null || true
  ) >/dev/null 2>&1 &
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

maybe_refresh_prs

# ---- count tasks by state ---------------------------------------------------
# Non-recursive tasks/*.md, excluding index/readme; tasks/archive/ is a subdir
# and so is naturally excluded by -maxdepth 1.

# Does branch $1 currently have a linked worktree?
has_worktree() { printf '%s\n' "$WORKTREE_BRANCHES" | grep -qxF "$1"; }

TOTAL=0; NS=0; ACT=0; PR=0; DONE=0
# -print0 + `read -d ''` so a task filename with an embedded newline can't split
# into two iterations; process substitution (not a pipe) keeps the counters in
# THIS shell.
while IFS= read -r -d '' f; do
  TOTAL=$((TOTAL + 1))
  name="$(basename "$f" .md)"
  branch="task/$name"          # /kickoff convention; adopt-renamed branches are
                               # an accepted blind spot for this glance segment.
  state="$(pr_state_of "$branch")"
  if [ "$state" = "MERGED" ]; then
    DONE=$((DONE + 1))                          # ✓ merged, ready to /close
  elif [ "$state" = "OPEN" ]; then
    PR=$((PR + 1))                              # ◇ in review
  elif [ "$state" = "CLOSED" ]; then
    # PR closed WITHOUT merging → the task is not done; fall back to its local
    # state (still has a worktree → active, otherwise back in the backlog).
    if has_worktree "$branch"; then ACT=$((ACT + 1)); else NS=$((NS + 1)); fi
  elif has_worktree "$branch"; then
    ACT=$((ACT + 1))                            # ● active (has a worktree)
  else
    NS=$((NS + 1))                              # ○ not started
  fi
done < <(find "$MAIN/tasks" -maxdepth 1 -type f -name '*.md' \
    ! -name '_index.md' ! -name 'README.md' -print0 2>/dev/null)

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
add_col "$NS"   "$C_NS" "○"
add_col "$ACT"  "$C_AC" "●"
add_col "$PR"   "$C_RV" "◇"
add_col "$DONE" "$C_MG" "✓"

printf '%b[ws %b%b]%b' "$LABEL" "$SEG" "$LABEL" "$RESET"
