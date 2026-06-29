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
# script never commits *without /close's approval*: `archive` only moves+records
# and reports `committable`; `commit-push` runs only after the y/n gate.
#
# CWD-safe: every path is explicit, the script never `cd`s (see cwd-safety rule).
#
# Subcommands:
#   archive <main-repo-path> <task-name> <task-branch> [--pr <n>] [--sha <sha>]
#       Move <main-repo>/tasks/<task-name>.md → tasks/archive/<name>.md with a
#       stamp line, and append an _index.md line. With --pr the stamp records a
#       merged PR (and --sha its merge commit, shortened — an empty or literal
#       "null" sha is treated as no-sha); without --pr it records a manual close.
#       A slash-bearing name is flattened to a single archive filename; on a name
#       collision the file is suffixed -2, -3, … (never clobbered); a fresh
#       _index.md line keyed to the actual archived basename is appended either way.
#   commit-push <main-repo-path> <task-name> <archived-rel-path> <main-branch>
#       After /close's user approval: stage exactly the archive change (the new
#       file when not gitignored, _index.md, and the original's removal when
#       tracked — never a blanket `git add tasks/`), commit it onto <main-branch>,
#       and fast-forward push to origin. Refuses if the main repo isn't on
#       <main-branch>. Never force-pushes; push failure is non-fatal.
#
# `archive` output: key=value lines on stdout (paths relative to the main repo) —
#   archived_path=tasks/archive/<name>[-N].md
#   collision=no | yes
#   committable=yes | no   (yes = there is a git change to commit — the archive is
#                           not gitignored, OR the source file was tracked)
# `commit-push` output: result=committed-pushed | committed-local [reason=…] |
#   nothing-to-commit | wrong-branch [current=…].
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
  # `gh ... --jq '.mergeCommit.oid'` prints the literal "null" for an unmerged /
  # not-yet-populated mergeCommit, so guard against it as well as the empty string.
  local mid
  if [ -n "$pr" ]; then
    if [ -n "$sha" ] && [ "$sha" != "null" ]; then mid="PR #$pr (merged @ ${sha:0:7})"; else mid="PR #$pr (merged)"; fi
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
  # the temp is removed and we abort with the source still intact.
  local tmp="$dest.tmp.$$"
  if ! { printf '%s\n\n' "$stamp"; cat "$src"; } > "$tmp"; then
    rm -f "$tmp" 2>/dev/null || true
    echo "failed to write archive $dest" >&2
    exit 1
  fi
  mv "$tmp" "$dest"

  # Append-only overview log; seed a header when first created. The identifier is
  # the actual archived basename, so a -2/-3 collision entry maps back to its file.
  # If the index write fails, roll the moved archive back so a re-run stays clean
  # (no orphaned, unrecorded archive) and the source is still intact below.
  local index="$tasks_dir/archive/_index.md"
  if [ ! -f "$index" ]; then
    printf '# Archived tasks\n\n' > "$index" || { rm -f "$dest" 2>/dev/null || true; echo "failed to seed $index" >&2; exit 1; }
  fi
  if ! printf -- '- %s · %s · %s — %s\n' "$date" "$mid" "$base_noext" "$title" >> "$index"; then
    rm -f "$dest" 2>/dev/null || true
    echo "failed to write $index" >&2
    exit 1
  fi

  rm -f "$src"

  # committable = there is a git change worth committing: the new archive file is
  # not gitignored, OR the source was a TRACKED file (its removal is a real change
  # even when the archive path is ignored — don't leave that deletion dangling).
  local committable="no"
  if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    if ! git -C "$repo" check-ignore -q "tasks/archive/$base"; then
      committable="yes"
    elif git -C "$repo" ls-files --error-unmatch -- "tasks/$name.md" >/dev/null 2>&1; then
      committable="yes"
    fi
  fi

  printf 'archived_path=tasks/archive/%s\n' "$base"
  printf 'collision=%s\n'    "$collision"
  printf 'committable=%s\n'  "$committable"
}

commit_push() {
  local repo="${1:-}" name="${2:-}" archived="${3:-}" mainbr="${4:-}"
  if [ -z "$repo" ] || [ -z "$name" ] || [ -z "$archived" ] || [ -z "$mainbr" ]; then
    echo "usage: ${0##*/} commit-push <main-repo-path> <task-name> <archived-rel-path> <main-branch>" >&2
    exit 2
  fi

  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || { echo "result=nothing-to-commit"; return 0; }

  # Never commit onto the wrong branch — the archive must land on <main-branch>.
  # main_branch is the default-branch NAME; the main repo's HEAD may point elsewhere.
  local cur; cur="$(git -C "$repo" branch --show-current 2>/dev/null || true)"
  if [ "$cur" != "$mainbr" ]; then
    printf 'result=wrong-branch\ncurrent=%s\n' "$cur"
    return 0
  fi

  # Stage precisely (never a blanket `git add tasks/`): the archived file only when
  # it isn't gitignored (`git add` errors on an ignored path), the index likewise,
  # and the original's removal (a no-op for an untracked-by-omission file).
  git -C "$repo" check-ignore -q "$archived"                || git -C "$repo" add -- "$archived" 2>/dev/null || true
  git -C "$repo" check-ignore -q "tasks/archive/_index.md"  || git -C "$repo" add -- "tasks/archive/_index.md" 2>/dev/null || true
  git -C "$repo" add -A -- "tasks/$name.md" 2>/dev/null || true

  if git -C "$repo" diff --cached --quiet; then
    echo "result=nothing-to-commit"
    return 0
  fi

  git -C "$repo" commit -m "Archive task $name" >/dev/null 2>&1 || { echo "result=nothing-to-commit"; return 0; }

  git -C "$repo" remote get-url origin >/dev/null 2>&1 || { printf 'result=committed-local\nreason=no-origin\n'; return 0; }

  # Step 5 already fast-forwarded local <main-branch> to origin/<main-branch>, so
  # the archive commit sits one commit on top — a clean ff. Never force-push.
  if git -C "$repo" push origin "$mainbr" >/dev/null 2>&1; then
    echo "result=committed-pushed"
  else
    printf 'result=committed-local\nreason=push-failed\n'
  fi
}

case "${1:-}" in
  archive)     shift; archive "$@" ;;
  commit-push) shift; commit_push "$@" ;;
  *) echo "usage: ${0##*/} {archive <repo> <name> <branch> [--pr <n>] [--sha <s>] | commit-push <repo> <name> <archived-rel-path> <main-branch>}" >&2; exit 2 ;;
esac
