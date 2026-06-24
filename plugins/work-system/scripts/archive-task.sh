#!/usr/bin/env bash
# archive-task.sh — archive a finished work-system task on /close.
#
# /close used to `rm tasks/<name>.md`, discarding finished-task context for good
# (tasks/ has no git history to fall back on — it is untracked by design). This
# instead MOVES the file into tasks/archive/<name>.md with a closed-stamp header,
# and appends a one-line summary to tasks/archive/_index.md — a queryable record
# of completed work (goal, acceptance criteria, which PR shipped it).
#
# Tracking is adaptive and needs no .gitignore surgery: the archive simply
# inherits whatever tasks/ does. If tasks/ is gitignored the archived file is
# ignored too (local-only); if tasks/ is tracked the move is a committable
# change. The script never commits — it reports `tracked=yes/no` so /close can
# ask the user, honoring the "never commit without approval" rule.
#
# CWD-safe: every path is explicit, the script never `cd`s (see cwd-safety rule).
#
# Subcommand:
#   archive <main-repo-path> <task-name> <task-branch> [--pr <n>] [--sha <sha>]
#       Move <main-repo>/tasks/<task-name>.md → tasks/archive/<task-name>.md with
#       a stamp line, and append an _index.md line. With --pr the stamp records a
#       merged PR (and --sha its merge commit, shortened); without --pr it records
#       a manual close ("closed manually (no merged PR)"). On a name collision the
#       archived file is suffixed -2, -3, … (the existing archive is never
#       clobbered); a fresh _index.md line is still appended either way.
#
# Output: key=value lines on stdout (paths relative to the main repo) —
#   archived_path=tasks/archive/<name>[-N].md
#   index_path=tasks/archive/_index.md
#   collision=no | yes
#   tracked=yes | no        (yes = archive path is NOT gitignored → committable)
# Exit 0 on success; 2 on a usage error; 3 when the task file does not exist.
set -eu

archive() {
  local repo="${1:-}" name="${2:-}" branch="${3:-}"
  if [ -z "$repo" ] || [ -z "$name" ] || [ -z "$branch" ]; then
    echo "usage: ${0##*/} archive <main-repo-path> <task-name> <task-branch> [--pr <n>] [--sha <sha>]" >&2
    exit 2
  fi
  shift 3 || true

  local pr="" sha=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pr)  pr="${2:-}";  shift 2 || shift ;;
      --sha) sha="${2:-}"; shift 2 || shift ;;
      *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
  done

  local tasks_dir="$repo/tasks"
  local src="$tasks_dir/$name.md"
  [ -f "$src" ] || { echo "no task file at $src" >&2; exit 3; }

  local archive_dir="$tasks_dir/archive"
  mkdir -p "$archive_dir"

  # Collision-free destination: never clobber a prior archive of the same name.
  local dest="$archive_dir/$name.md" collision="no" n=2
  while [ -e "$dest" ]; do
    dest="$archive_dir/$name-$n.md"
    collision="yes"
    n=$((n + 1))
  done
  local base; base="$(basename "$dest")"

  # Stamp middle segment: merged PR (with short merge SHA when known) vs manual.
  local mid
  if [ -n "$pr" ]; then
    if [ -n "$sha" ]; then mid="PR #$pr (merged @ ${sha:0:7})"; else mid="PR #$pr (merged)"; fi
  else
    mid="closed manually (no merged PR)"
  fi
  local date; date="$(date +%F 2>/dev/null || echo unknown)"
  local stamp="> Archived $date · $mid · $branch"

  # Title for the index line: first heading of the task, else the task name.
  local title
  title="$(grep -m1 '^#' "$src" 2>/dev/null | sed 's/^#\{1,\}[[:space:]]*//' || true)"
  [ -n "$title" ] || title="$name"

  # Write stamped copy, then drop the original (a move that prepends the stamp).
  { printf '%s\n\n' "$stamp"; cat "$src"; } > "$dest"
  rm -f "$src"

  # Append-only overview log; seed a header when first created.
  local index="$archive_dir/_index.md"
  [ -f "$index" ] || printf '# Archived tasks\n\n' > "$index"
  printf -- '- %s · %s · %s — %s\n' "$date" "$mid" "$name" "$title" >> "$index"

  # Committable iff the archive path is NOT gitignored (it inherits tasks/).
  local tracked="no"
  if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    if git -C "$repo" check-ignore -q "tasks/archive/$base"; then tracked="no"; else tracked="yes"; fi
  fi

  printf 'archived_path=tasks/archive/%s\n' "$base"
  printf 'index_path=tasks/archive/_index.md\n'
  printf 'collision=%s\n' "$collision"
  printf 'tracked=%s\n'   "$tracked"
}

case "${1:-}" in
  archive) shift; archive "$@" ;;
  *) echo "usage: ${0##*/} archive <main-repo-path> <task-name> <task-branch> [--pr <n>] [--sha <sha>]" >&2; exit 2 ;;
esac
