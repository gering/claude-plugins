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
# Task names are flattened (slashes → dashes) everywhere a source/dest path is
# built: /define and /adopt write flat kebab files (an adopted feature/foo/bar →
# tasks/foo-bar.md) while task-status.sh keeps inner slashes (foo/bar), so the
# lookup must flatten to match.
#
# Subcommands:
#   archive <main-repo-path> <task-name> <task-branch> [--pr <n>] [--sha <sha>]
#       Move <main-repo>/tasks/<name>.md → tasks/archive/<name>.md with a stamp,
#       and append an _index.md line. With --pr the stamp records a merged PR (and
#       --sha its merge commit, shortened — an empty or literal "null" sha = no
#       sha); without --pr it records a manual close. On a name collision the file
#       is suffixed -2, -3, … (never clobbered).
#   commit-push <main-repo-path> <task-name> <archived-rel-path> <main-branch>
#       After /close's user approval: stage exactly the archive change (the new
#       file when not gitignored, _index.md, and the original's removal when
#       tracked — never a blanket `git add tasks/`), commit ONLY those paths (a
#       pathspec commit, so unrelated pre-staged work is never swept in), and
#       fast-forward push to origin. Refuses if the main repo isn't on
#       <main-branch>. Never force-pushes.
#
# `archive` output: key=value lines (paths relative to the main repo) —
#   archived_path=tasks/archive/<name>[-N].md
#   collision=no | yes
#   committable=yes | no   (yes = a git change to commit — archive not gitignored,
#                           OR the source file was tracked)
# `commit-push` output: result=committed-pushed | committed-local [reason=no-origin|
#   push-failed|unpushed-history] | commit-failed | archive-not-staged |
#   nothing-to-commit | wrong-branch [current=…]. The committed-* results also emit
#   archive_committed=yes|no (no = a gitignored archive whose commit recorded only
#   the source removal). Pushes only when the archive commit is the sole one ahead
#   of origin/<main-branch>, never sweeping unrelated unpushed commits onto it.
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

  # Flatten slashes (see header) so the SOURCE lookup matches the flat kebab file,
  # and the archive filename is always flat (no un-created tasks/archive/<sub>/).
  local safe="${name//\//-}"
  local tasks_dir="$repo/tasks"
  local src="$tasks_dir/$safe.md"
  [ -f "$src" ] || { echo "no task file at $src" >&2; exit 3; }

  mkdir -p "$tasks_dir/archive"

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

  # Remove the original BEFORE recording the index, rolling the moved archive back
  # if the remove fails (immutable/locked source, read-only parent). This keeps the
  # state clean and /close re-runnable — and crucially avoids ever leaving the
  # source on disk next to a committable archive, which commit-push would otherwise
  # turn into a pushed DUPLICATE of both. The non-zero exit is surfaced by /close
  # (step 10's exit≠3 branch); the source is intact, nothing else was changed.
  if ! rm -f "$src"; then
    rm -f "$dest" 2>/dev/null || true
    echo "failed to remove original $src — archive rolled back" >&2
    exit 1
  fi

  # Append-only overview log; seed a header when first created. The identifier is
  # the actual archived basename, so a -2/-3 collision entry maps back to its file.
  # Best-effort: the archived file itself is the record, so a failed index write
  # (disk full) costs only the one-line log entry, not the archive — warn, continue.
  local index="$tasks_dir/archive/_index.md"
  if [ ! -f "$index" ]; then
    printf '# Archived tasks\n\n' > "$index" 2>/dev/null || echo "warning: could not seed $index" >&2
  fi
  printf -- '- %s · %s · %s — %s\n' "$date" "$mid" "$base_noext" "$title" >> "$index" 2>/dev/null \
    || echo "warning: archived $dest but could not record it in $index" >&2

  # committable = there is a git change worth committing: the new archive file is
  # not gitignored, OR the source was a TRACKED file (its removal is a real change
  # even when the archive path is ignored — don't leave that deletion dangling).
  local committable="no"
  if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    if ! git -C "$repo" check-ignore -q "tasks/archive/$base"; then
      committable="yes"
    elif git -C "$repo" ls-files --error-unmatch -- "tasks/$safe.md" >/dev/null 2>&1; then
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

  local safe="${name//\//-}"
  local src_rel="tasks/$safe.md"

  # Build the EXACT pathspec to commit (never a blanket `git add tasks/`, which
  # would sweep in unrelated pending task files): the archived file and _index.md
  # only when not gitignored (`git add` errors on an ignored path), and the
  # original's removal only when it was tracked.
  local archive_ignored=no
  if git -C "$repo" check-ignore -q "$archived"; then archive_ignored=yes; fi
  local paths=()
  if [ "$archive_ignored" = no ]; then paths+=("$archived"); fi
  git -C "$repo" check-ignore -q "tasks/archive/_index.md" || paths+=("tasks/archive/_index.md")
  if git -C "$repo" ls-files --error-unmatch -- "$src_rel" >/dev/null 2>&1; then paths+=("$src_rel"); fi
  [ "${#paths[@]}" -eq 0 ] && { echo "result=nothing-to-commit"; return 0; }

  # Stage each path INDEPENDENTLY: `git add -A a b` aborts wholesale on one
  # non-matching element (e.g. a stale archived_path), which would silently drop
  # the valid source-deletion too. Then commit ONLY the paths that actually carry a
  # staged change — a pathspec commit, so unrelated pre-staged work is never swept
  # in, and a bad/empty element is simply absent from the commit.
  local p staged=() archive_committed=no
  for p in "${paths[@]}"; do
    git -C "$repo" add -A -- "$p" 2>/dev/null || true
    if ! git -C "$repo" diff --cached --quiet -- "$p"; then
      staged+=("$p")
      if [ "$p" = "$archived" ]; then archive_committed=yes; fi
    fi
  done
  [ "${#staged[@]}" -eq 0 ] && { echo "result=nothing-to-commit"; return 0; }

  # Integrity guard: when the archive file WAS meant to be committed (not ignored)
  # but failed to stage (lost/moved/stale archived_path), do NOT report success —
  # committing only the source deletion would erase the task from history with no
  # archived replacement in git. Unstage what we staged (leave the index as we found
  # it, so nothing half-staged lingers) and surface it so /close can warn instead.
  if [ "$archive_ignored" = no ] && [ "$archive_committed" = no ]; then
    git -C "$repo" reset -q HEAD -- "${staged[@]}" 2>/dev/null || true
    echo "result=archive-not-staged"
    return 0
  fi

  # A non-zero exit here (these paths DO have staged changes) is a REAL failure — a
  # rejecting pre-commit hook, GPG signing misconfig, locked index — NOT an empty
  # commit; surface it instead of masking it as "nothing-to-commit".
  if ! git -C "$repo" commit -m "Archive task $safe" -- "${staged[@]}" >/dev/null 2>&1; then
    echo "result=commit-failed"
    return 0
  fi

  # archive_committed lets /close word the outcome honestly: =no means a gitignored
  # archive whose commit recorded only the source removal (the archive stays local).
  git -C "$repo" remote get-url origin >/dev/null 2>&1 || { printf 'result=committed-local\nreason=no-origin\narchive_committed=%s\n' "$archive_committed"; return 0; }

  # Push ONLY when our archive commit is the SOLE commit ahead of origin/<main-branch>
  # (step 5 ff'd local <main-branch> to origin, so ahead should be exactly 1). If the
  # user had other unpushed commits on <main-branch>, pushing the whole branch would
  # publish them under this archive-scoped approval — leave them for the user.
  local ahead; ahead="$(git -C "$repo" rev-list --count "origin/$mainbr..$mainbr" 2>/dev/null || echo unknown)"
  if [ "$ahead" != "1" ]; then
    printf 'result=committed-local\nreason=unpushed-history\narchive_committed=%s\n' "$archive_committed"
    return 0
  fi

  # Clean fast-forward. Never force-push; a rejected push (offline, protected, or
  # origin moved since step 5) is non-fatal.
  if git -C "$repo" push origin "$mainbr" >/dev/null 2>&1; then
    printf 'result=committed-pushed\narchive_committed=%s\n' "$archive_committed"
  else
    printf 'result=committed-local\nreason=push-failed\narchive_committed=%s\n' "$archive_committed"
  fi
}

case "${1:-}" in
  archive)     shift; archive "$@" ;;
  commit-push) shift; commit_push "$@" ;;
  *) echo "usage: ${0##*/} {archive <repo> <name> <branch> [--pr <n>] [--sha <s>] | commit-push <repo> <name> <archived-rel-path> <main-branch>}" >&2; exit 2 ;;
esac
