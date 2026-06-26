#!/usr/bin/env bash
# archive-task.sh — archive a finished work-system task on /close.
#
# /close used to `rm tasks/<name>.md`, discarding finished-task context for good
# (tasks/ has no git history to fall back on — it is untracked by design). This
# instead MOVES the file into tasks/archive/<name>.md with a closed-stamp header,
# and appends a one-line summary to tasks/archive/_index.md — a queryable record
# of completed work (goal, acceptance criteria, which PR shipped it).
#
# Committability is adaptive and needs no .gitignore surgery: the archive simply
# inherits whatever tasks/ does. If tasks/ is gitignored the archived file is
# ignored too (local-only); otherwise the move is a committable change. The
# script never commits — `archive` reports `committable=yes/no` and `stage`
# stages precisely, but the commit itself is left to /close (user-approved),
# honoring the "never commit without approval" rule.
#
# CWD-safe: every path is explicit, the script never `cd`s (see cwd-safety rule).
#
# Subcommands:
#   archive <main-repo-path> <task-name> <task-branch> [--pr <n>] [--sha <sha>]
#       Move <main-repo>/tasks/<task-name>.md → tasks/archive/<name>.md with a
#       stamp line, and append an _index.md line. With --pr the stamp records a
#       merged PR (and --sha its merge commit, shortened); without --pr it records
#       a manual close ("closed manually (no merged PR)"). A slash-bearing name is
#       flattened to a single archive filename; on a name collision the file is
#       suffixed -2, -3, … (the existing archive is never clobbered); a fresh
#       _index.md line keyed to the actual archived basename is appended either way.
#   stage <main-repo-path> <task-name> <archived-rel-path>
#       Stage exactly the archive change (new file + _index.md + the original's
#       removal) for a commit — never a blanket `git add tasks/`. Does NOT commit.
#
# `archive` output: key=value lines on stdout (paths relative to the main repo) —
#   archived_path=tasks/archive/<name>[-N].md
#   collision=no | yes
#   committable=yes | no    (yes = archive path is NOT gitignored)
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
      # Require a value: a bare trailing `--pr` must error, not silently fall
      # through to a "closed manually" stamp on a genuinely merged task.
      --pr)  [ $# -ge 2 ] || { echo "--pr needs a value" >&2; exit 2; }; pr="$2";  shift 2 ;;
      --sha) [ $# -ge 2 ] || { echo "--sha needs a value" >&2; exit 2; }; sha="$2"; shift 2 ;;
      *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
  done

  local tasks_dir="$repo/tasks"
  local src="$tasks_dir/$name.md"
  [ -f "$src" ] || { echo "no task file at $src" >&2; exit 3; }

  mkdir -p "$tasks_dir/archive"

  # Flatten any slashes (a multi-segment task name — e.g. from an adopted
  # feature/a/b branch whose prefix-strip leaves "a/b") so the archive filename is
  # always flat. Otherwise dest would point into an un-created tasks/archive/<sub>/
  # and the write would fail mid-/close, after the worktree/branch are already gone.
  local safe="${name//\//-}"

  # Collision-free destination: never clobber a prior archive of the same name.
  local dest="$tasks_dir/archive/$safe.md" collision="no" n=2
  while [ -e "$dest" ]; do
    dest="$tasks_dir/archive/$safe-$n.md"
    collision="yes"
    n=$((n + 1))
  done
  local base; base="$(basename "$dest")"
  local base_noext="${base%.md}"

  # Stamp middle segment: merged PR (with short merge SHA when known) vs manual.
  local mid
  if [ -n "$pr" ]; then
    if [ -n "$sha" ]; then mid="PR #$pr (merged @ ${sha:0:7})"; else mid="PR #$pr (merged)"; fi
  else
    mid="closed manually (no merged PR)"
  fi
  local date; date="$(date +%F 2>/dev/null || echo unknown)"
  local stamp="> Archived $date · $mid · $branch"

  # Title for the index line: the document title is the FIRST non-blank line when it
  # is an ATX heading ('#'-run THEN a space). Looking only at the first non-blank
  # line (not any '#' line anywhere, which grep would catch inside a leading code
  # fence) keeps a shebang or fenced '# comment' from masquerading as the title.
  local title
  title="$(awk 'NF{ if ($0 ~ /^#{1,6} /) { sub(/^#+[[:space:]]*/, ""); print } exit }' "$src" 2>/dev/null || true)"
  [ -n "$title" ] || title="$safe"

  # Write the stamped copy to a temp file, then mv it into place — atomic, so an
  # interrupted write (disk full / signal) never leaves a truncated archive the
  # collision loop would later orphan as a real-looking file. On a write failure
  # the temp is removed and we abort with the source still intact. The original is
  # dropped only AFTER the index records the archive, so a failed index write also
  # leaves the source intact (/close re-runnable) — never a moved-but-unrecorded
  # archive with the original gone.
  local tmp="$dest.tmp.$$"
  if ! { printf '%s\n\n' "$stamp"; cat "$src"; } > "$tmp"; then
    rm -f "$tmp" 2>/dev/null || true
    echo "failed to write archive $dest" >&2
    exit 1
  fi
  mv "$tmp" "$dest"

  # Append-only overview log; seed a header when first created. The identifier is
  # the actual archived basename, so a -2/-3 collision entry maps back to its file.
  local index="$tasks_dir/archive/_index.md"
  [ -f "$index" ] || printf '# Archived tasks\n\n' > "$index"
  printf -- '- %s · %s · %s — %s\n' "$date" "$mid" "$base_noext" "$title" >> "$index"

  rm -f "$src"

  # Committable iff the archive path is NOT gitignored (it inherits tasks/'s ignore
  # status). NOTE: "not ignored" ≠ "git-tracked": an untracked-by-omission tasks/
  # also reports committable=yes by design — the project opts task files into git
  # on the first archive (gitignore tasks/ to keep the archive local instead).
  local committable="no"
  if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    if git -C "$repo" check-ignore -q "tasks/archive/$base"; then committable="no"; else committable="yes"; fi
  fi

  printf 'archived_path=tasks/archive/%s\n' "$base"
  printf 'collision=%s\n'    "$collision"
  printf 'committable=%s\n'  "$committable"
}

stage() {
  local repo="${1:-}" name="${2:-}" archived="${3:-}"
  if [ -z "$repo" ] || [ -z "$name" ] || [ -z "$archived" ]; then
    echo "usage: ${0##*/} stage <main-repo-path> <task-name> <archived-rel-path>" >&2
    exit 2
  fi
  # Stage exactly the archive change — never a blanket `git add tasks/`, which
  # would sweep in unrelated pending task files.
  git -C "$repo" add -- "$archived" "tasks/archive/_index.md"
  # Stage the original's removal when it was tracked; a no-op (not an error) for an
  # untracked-by-omission file that simply vanished.
  git -C "$repo" add -A -- "tasks/$name.md" 2>/dev/null || true
}

case "${1:-}" in
  archive) shift; archive "$@" ;;
  stage)   shift; stage "$@" ;;
  *) echo "usage: ${0##*/} {archive <repo> <name> <branch> [--pr <n>] [--sha <s>] | stage <repo> <name> <archived-rel-path>}" >&2; exit 2 ;;
esac
