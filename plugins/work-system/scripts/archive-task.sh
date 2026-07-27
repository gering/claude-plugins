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
# ignored too (local-only); otherwise the move is a committable change.
#
# `archive` NEVER commits: it only moves+records and reports `committable`. All
# git-stateful work is in `commit-push`, which /close runs after the y/n gate —
# OR, without asking, when the repo carries the committed per-repo opt-in flag
# `.claude/work-system-close-autocommit` (see the `autocommit` subcommand). That
# flag is the durable authorization for exactly this one narrow action; it is
# honored only when tracked + unmodified, and it changes nothing about
# commit-push's own guards (archive-scoped pathspec, ff-only, never force-push,
# refuses on unpushed history).
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
#   autocommit get <main-repo-path> [<main-branch>]
#       Print `enabled=yes` iff the repo opts into skipping /close's commit+push
#       prompt, else `enabled=no`. FAIL-SAFE: anything unresolved or suspicious
#       reports `no`, which merely keeps the prompt. Pass <main-branch>: the flag
#       authorizes a commit onto THAT branch (commit-push refuses any other), so
#       it is read from that ref — reading HEAD would make the verdict depend on
#       whichever branch the main checkout is parked on. Falls back to HEAD when
#       omitted. The value is read from the COMMITTED object, never the worktree —
#       presence alone is not authorization, and a working-tree edit can never
#       silently enable the waiver (not even via `git update-index
#       --assume-unchanged` / `--skip-worktree`, which fool a diff-based guard).
#       What this does and does NOT buy you: it stops a flag that was merely
#       WRITTEN (stray file, tool, an agent's file write); it does NOT stop an
#       actor who can already commit in the repo. commit-push's own guards
#       (archive-scoped pathspec, ff-only, never force-push, refusal on unpushed
#       history) are what bound the damage in that case.
#       A repo that never opted in answers a BARE `enabled=no` — that bare form
#       is reserved for exactly that case. Every other non-honored outcome adds
#       two lines saying why: `reason=` one of
#       symlink | not-a-file | untracked | modified | locally-disabled |
#       unrecognized-content | git-error | unsupported-layout | not-a-repo |
#       unresolvable-branch | checkout-not-on-main, plus
#       `note=<ready-to-print sentence>` — callers relay the note rather than
#       re-spelling this vocabulary in their own prose (it drifts).
#       Accepted content (case-insensitive, leading/trailing whitespace trimmed,
#       single line): yes/true/1/on → on; no/false/0/off → off.
#       Per-repo only — no global default.
#   autocommit set <main-repo-path>
#       Write `yes` to the repo's `.claude/work-system-close-autocommit` via an
#       exclusively-created mktemp file + rename (no PID-predictable temp path to
#       pre-plant), refusing when the flag OR its `.claude` parent is a symlink or
#       resolves outside the repo. Echoes `flag=<path>` plus a reminder that it
#       takes effect only once COMMITTED.
#   autocommit unset <main-repo-path> [<main-branch>]
#       Remove the repo's `.claude/work-system-close-autocommit` (same path
#       guards), reverting to the default ask-once behavior. Echoes `flag=<path>`,
#       plus a reminder to commit the deletion when the flag is still live on the
#       authorization ref (pass <main-branch> so that check matches get's).
#   All three resolve <main-repo-path> to the MAIN checkout by delegating to
#   main-repo-path.sh — the same resolver /close uses, so `set` can never write
#   where `get` doesn't look. A non-git path is a usage error for set/unset, and
#   for get an `enabled=no` carrying `reason=not-a-repo` + `note=`.
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

# Absolute directory of THIS script, resolved once, before anything can change
# the working directory. Sibling helpers must be addressed through it: a
# `$(dirname "$0")` expanded inside a `( cd <other-repo> && … )` subshell would
# resolve against that other repo when $0 is relative — looking up (and running)
# whatever happens to sit at that path there.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# `:(literal)` pathspec prefix. Task names flow in from task files and reach git
# as PATHSPECS, where `*`/`?`/`[` are globs: a task named `x*` yields
# `tasks/archive/x*.md`, matching every archived file with that prefix. The rule
# is NOT "mutating commands only" — it is: prefix every git command that
# INTERPRETS pathspec magic (add, commit, diff, ls-files), and omit it for
# `check-ignore`, which rejects magic outright ("pathspec magic not supported by
# this command") and would fail every probe if prefixed. Drifting either way is a
# real bug: unprefixed `ls-files` mis-detects a neighbouring file, prefixed
# `check-ignore` breaks gitignore detection entirely.
lit() { printf ':(literal)%s' "$1"; }

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
  #
  # mktemp, not a `$dest.tmp.$$` name: a PID-derived path is predictable, so
  # anything able to create files in tasks/archive/ could pre-plant it as a
  # symlink and have this redirect write through it. (Same reasoning as the
  # autocommit writer below — keep the two consistent.)
  local tmp
  tmp="$(mktemp "$tasks_dir/archive/.archive.XXXXXX" 2>/dev/null || true)"
  if [ -z "$tmp" ]; then
    echo "failed to create a temp file in $tasks_dir/archive" >&2
    exit 1
  fi
  if ! { printf '%s\n\n' "$stamp"; cat "$src"; } > "$tmp"; then
    rm -f "$tmp" 2>/dev/null || true
    echo "failed to write archive $dest" >&2
    exit 1
  fi
  # mktemp creates 600; the archive is ordinary repo content, so restore the mode
  # the caller's umask would have produced for a plain redirect — do NOT force
  # 644, which would override a deliberately stricter umask and make archived
  # task files world-readable in a private checkout.
  chmod "$(printf '%o' "$(( 0666 & ~0$(umask) ))")" "$tmp" 2>/dev/null || true
  mv "$tmp" "$dest"
  # The source is removed next, so make sure a REAL archive is in place first: if
  # the destination is not a regular file (a swapped-in symlink, an interrupted
  # rename), removing the source would destroy the task with no copy left.
  if [ ! -f "$dest" ] || [ -L "$dest" ]; then
    echo "archive at $dest is not a regular file — refusing to remove the original" >&2
    exit 1
  fi

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
    elif git -C "$repo" ls-files --error-unmatch -- "$(lit "tasks/$safe.md")" >/dev/null 2>&1; then
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
  #
  # Pathspec handling: see lit()'s contract at the top of the file.
  local archive_ignored=no
  if git -C "$repo" check-ignore -q -- "$archived"; then archive_ignored=yes; fi
  local paths=()
  if [ "$archive_ignored" = no ]; then paths+=("$archived"); fi
  git -C "$repo" check-ignore -q -- "tasks/archive/_index.md" || paths+=("tasks/archive/_index.md")
  if git -C "$repo" ls-files --error-unmatch -- "$(lit "$src_rel")" >/dev/null 2>&1; then paths+=("$src_rel"); fi
  [ "${#paths[@]}" -eq 0 ] && { echo "result=nothing-to-commit"; return 0; }

  # Stage each path INDEPENDENTLY: `git add -A a b` aborts wholesale on one
  # non-matching element (e.g. a stale archived_path), which would silently drop
  # the valid source-deletion too. Then commit ONLY the paths that actually carry a
  # staged change — a pathspec commit, so unrelated pre-staged work is never swept
  # in, and a bad/empty element is simply absent from the commit.
  local p staged=() archive_committed=no
  for p in "${paths[@]}"; do
    git -C "$repo" add -A -- "$(lit "$p")" 2>/dev/null || true
    if ! git -C "$repo" diff --cached --quiet -- "$(lit "$p")"; then
      staged+=("$(lit "$p")")
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

# Resolve <repo> to the MAIN checkout root: the opt-in flag must live in the main
# worktree, not in a disposable linked worktree that /close later removes (a flag
# written there would never be read and would vanish with the worktree).
#
# DELEGATES to main-repo-path.sh — the plugin's one main-worktree resolver, and
# the very helper that produces the <main-repo-path> /close passes to `autocommit
# get`. Sharing it is the point: a second, independent resolver here could
# disagree with /close's, so `set` would write a flag `get` never reads — the
# exact failure this resolution exists to prevent. (An earlier cut open-coded a
# `--git-common-dir` + `basename == .git` heuristic; that duplicated
# agent-registry.sh and broke on separate-git-dir/submodule layouts.)
#
# The resolver's answer is CROSS-CHECKED before use, not trusted: under
# `git init --separate-git-dir`, `git worktree list --porcelain` reports the GIT
# DIR as the first worktree (verified), so an unchecked result would have `set`
# cheerfully create `.claude/` INSIDE `.git` — a flag that can never be committed,
# reported as success. `--is-inside-work-tree` + `--show-toplevel` costs one call
# and turns that silent-wrong-success into a clean refusal. (Fixing the exotic
# layout itself belongs in main-repo-path.sh; this only refuses to act on it.)
#
# The subshell `cd` is scoped (never a persistent cd — see the cwd-safety rule);
# main-repo-path.sh reads the current directory rather than taking a path. The
# helper is addressed via the absolute $SCRIPT_DIR — NOT `$(dirname "$0")`, which
# inside this subshell would resolve against "$repo" when $0 is relative.
# Prints the root, or nothing when <repo> does not resolve to a real work tree.
# Always returns 0, so a caller's `root="$(autocommit_root …)"` can't trip `set -e`.
autocommit_root() {
  local repo="$1" root=""
  [ -d "$repo" ] || return 0
  root="$( cd "$repo" 2>/dev/null && bash "$SCRIPT_DIR/main-repo-path.sh" path 2>/dev/null || true )"
  [ -n "$root" ] && [ -d "$root" ] || return 0
  # Must be an actual checkout, and must agree with git's own idea of its top.
  [ "$(git -C "$root" rev-parse --is-inside-work-tree 2>/dev/null || true)" = "true" ] || return 0
  local top; top="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$top" ] || return 0
  printf '%s\n' "$top"
  return 0
}

# Normalize a flag value to one token: `on`, `off`, or `bad`. ONE implementation
# for both the committed value and the working-tree copy — they had drifted apart
# (only one rejected multi-line content), which is how a value accepted in one
# place and rejected in the other becomes possible.
# Trim leading/trailing whitespace ONLY: deleting all whitespace would collapse
# `y e s` into an accepted token. Multi-line is never a valid flag.
autocommit_normalize() {
  local raw="$1" v
  case "$raw" in
    *"
"*) printf 'bad\n'; return 0 ;;
  esac
  v="$(printf '%s' "$raw" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')"
  case "$v" in
    yes|true|1|on)  printf 'on\n' ;;
    no|false|0|off) printf 'off\n' ;;
    *)              printf 'bad\n' ;;
  esac
}

# Explain WHY the main checkout could not be resolved. "Not a git repository" is
# wrong (and sends the user hunting in the wrong direction) when the path IS in a
# repo whose layout the resolver can't handle — e.g. a separate-git-dir checkout,
# where `git worktree list` reports the git dir rather than the work tree.
autocommit_no_root_error() {
  local op="$1" repo="$2"
  if [ "$(git -C "$repo" rev-parse --is-inside-work-tree 2>/dev/null || true)" = "true" ]; then
    # Do NOT suggest placing the flag by hand: `get` cannot resolve this layout
    # either, so a hand-placed flag would never be read. Say it is unavailable.
    echo "autocommit $op: cannot resolve the main checkout for $repo — unsupported repository layout (e.g. separate-git-dir); the autocommit opt-in is unavailable here" >&2
  else
    echo "autocommit $op: not a git repository: $repo" >&2
  fi
}

# Refuse to write/delete through a redirected path. Guarding only the LEAF is not
# enough: with no `.claude` yet, a symlinked `.claude` DIRECTORY makes `mkdir -p`
# succeed through the link and the write land outside the repo entirely, while the
# reported path still looks in-repo. So check the parent as well, and verify the
# parent physically resolves INSIDE the repo root. Returns 1 (with a message) when
# the caller must abort. $3 is the subcommand name, for the message only.
autocommit_guard_paths() {
  local root="$1" file="$2" op="$3" dir; dir="$(dirname "$file")"
  if [ -L "$dir" ]; then
    echo "autocommit $op: refusing — $dir is a symlink" >&2; return 1
  fi
  if [ -L "$file" ]; then
    echo "autocommit $op: refusing — $file is a symlink" >&2; return 1
  fi
  # TYPE, not just symlink-ness. Otherwise a plain file at `.claude` let `mkdir
  # -p` fail with a raw error and an undocumented exit 1, and a DIRECTORY at the
  # flag path passed the guard entirely — `mv` then moved the temp file *into* it
  # and `set` reported success for an opt-in that could never work.
  if [ -e "$dir" ] && [ ! -d "$dir" ]; then
    echo "autocommit $op: refusing — $dir exists and is not a directory" >&2; return 1
  fi
  if [ -e "$file" ] && [ ! -f "$file" ]; then
    echo "autocommit $op: refusing — $file exists and is not a regular file" >&2; return 1
  fi
  # Physical containment: if .claude already exists, its resolved path must sit
  # under the resolved repo root. Skipped when it doesn't exist yet (mkdir -p will
  # create a real directory — the -L check above already ruled out a link).
  if [ -d "$dir" ]; then
    local rdir rroot
    rdir="$( cd "$dir" 2>/dev/null && pwd -P || true )"
    rroot="$( cd "$root" 2>/dev/null && pwd -P || true )"
    if [ -z "$rdir" ] || [ -z "$rroot" ] || [ "${rdir#"$rroot"/}" = "$rdir" ]; then
      echo "autocommit $op: refusing — $dir does not resolve inside $root" >&2; return 1
    fi
  fi
  return 0
}

autocommit() {
  local op="${1:-}" repo="${2:-}" mainbr="${3:-}"
  case "$op" in
    get|set|unset) ;;
    *) echo "usage: ${0##*/} autocommit {get|set|unset} <main-repo-path> [<main-branch>]" >&2; exit 2 ;;
  esac
  if [ -z "$repo" ]; then
    echo "usage: ${0##*/} autocommit $op <main-repo-path> [<main-branch>]  (branch omitted: get/unset fall back to HEAD)" >&2; exit 2
  fi

  local rel=".claude/work-system-close-autocommit"
  local root; root="$(autocommit_root "$repo")"
  local file=""; [ -n "$root" ] && file="$root/$rel"

  case "$op" in
    get)
      # FAIL-SAFE by construction: every unresolved or suspicious case reports
      # enabled=no, which simply keeps /close's approval prompt. `reason=` (a
      # machine code) plus `note=` (the ready-to-print human sentence, so callers
      # never re-spell the vocabulary in prose) are emitted ONLY when a flag file
      # exists but was not honored — the confusing case, where the feature would
      # otherwise just look broken. A plainly absent flag stays a bare enabled=no.
      if [ -z "$root" ]; then
        # Not silent: `set` refuses these layouts with a diagnostic, so `get`
        # must say the same thing rather than looking like a plain "not opted in"
        # — otherwise the opt-in is unreachable AND unexplained.
        if [ "$(git -C "$repo" rev-parse --is-inside-work-tree 2>/dev/null || true)" = "true" ]; then
          printf 'enabled=no\nreason=unsupported-layout\nnote=%s\n' \
            "cannot resolve this repository's main checkout (unsupported layout) — the autocommit opt-in is unavailable here"
        else
          printf 'enabled=no\nreason=not-a-repo\nnote=%s\n' \
            "not a git repository — the autocommit opt-in is unavailable here"
        fi
        return 0
      fi
      # NOTHING CONFIGURED comes first: a repo that never opted in must answer a
      # bare `enabled=no`, with no reason/note. Testing the symlink before this
      # made every /close in a repo with a symlinked `.claude` (a normal dotfiles
      # layout) warn about an opt-in the user never set up — and callers are told
      # a note means "an existing flag was not honored".
      if [ ! -e "$file" ] && [ ! -L "$file" ]; then printf 'enabled=no\n'; return 0; fi
      # A symlinked flag — or a symlinked `.claude` parent — means the on-disk
      # state is redirected; never honor it. (The value itself comes from the
      # committed object below, so this cannot change the answer, but a redirected
      # checkout is exactly the situation to fall back to asking in.)
      if [ -L "$file" ] || [ -L "$root/.claude" ]; then
        printf 'enabled=no\nreason=symlink\nnote=%s\n' \
          "the autocommit flag (or its .claude directory) is a symlink — refusing to honor it"
        return 0
      fi
      if [ ! -f "$file" ]; then
        printf 'enabled=no\nreason=not-a-file\nnote=%s\n' \
          "the autocommit flag path is not a regular file — refusing to honor it"
        return 0
      fi

      # PROVENANCE — presence alone is NOT authorization. Read the value from the
      # COMMITTED object (`git show HEAD:<rel>`), never from the working tree:
      # that single call is both the tracked-check (it fails when the path isn't
      # in HEAD) and the value read, and it is immune to the working-tree tricks
      # that fool a `diff`-based guard — `git update-index --assume-unchanged` /
      # `--skip-worktree` make `ls-files`/`diff --quiet` report a modified file as
      # clean, but they cannot change what HEAD contains.
      #
      # Scope of this guard (documented honestly — see the knowledge entry): it
      # stops a flag that was merely WRITTEN (a tool, a stray file, an agent's
      # file write) from waiving the prompt. It does NOT stop an actor who can
      # already commit in your repo — that actor can commit the flag like any
      # other change. What bounds the damage there is commit-push itself:
      # archive-scoped pathspec, fast-forward only, never a force-push, and a
      # refusal when <main-branch> carries other unpushed commits.
      # WHICH REF: the archive lands on <main-branch>, and commit_push refuses to
      # commit anywhere else, so the authorization must be read from that same
      # branch — not from whatever the main checkout happens to be parked on.
      #
      # FULLY QUALIFIED (`refs/heads/<branch>`), never the bare name: git resolves
      # `refs/tags/<name>` BEFORE `refs/heads/<name>`, so a tag named like the
      # default branch shadows it — and tags are auto-followed on fetch. A bare
      # `$mainbr` therefore let a tag supply the authorization value that the
      # branch itself never carried. Verified.
      local ref=""
      if [ -n "$mainbr" ]; then
        if git -C "$root" rev-parse --verify -q "refs/heads/$mainbr" >/dev/null 2>&1; then
          ref="refs/heads/$mainbr"
        else
          # FAIL CLOSED. Falling back to HEAD here would reinstate exactly the
          # branch-dependent verdict this argument exists to remove — and in the
          # failing direction (a flag on the parked branch would read as
          # authorization for a branch that has none).
          printf 'enabled=no\nreason=unresolvable-branch\nnote=%s\n' \
            "the authorization branch could not be resolved in this repository — falling back to asking"
          return 0
        fi
        # commit_push refuses unless the checkout is ON that branch, so an
        # `enabled=yes` here would only announce an auto-commit the next command
        # declines. Answer for what can actually happen.
        local cur; cur="$(git -C "$root" branch --show-current 2>/dev/null || true)"
        if [ "$cur" != "$mainbr" ]; then
          printf 'enabled=no\nreason=checkout-not-on-main\nnote=%s\n' \
            "the main checkout is not on the branch the archive would be committed to — falling back to asking"
          return 0
        fi
      else
        # No branch passed (standalone/manual use): HEAD is the only sensible ref.
        ref="HEAD"
      fi
      local head_val rc=0
      head_val="$(git -C "$root" show "$ref:$rel" 2>/dev/null)" || rc=$?
      if [ "$rc" -ne 0 ]; then
        # Not on that ref (never committed there), or git could not read it at
        # all. Both are "not authorized"; distinguish them so debugging isn't
        # misdirected.
        if git -C "$root" rev-parse --verify -q "$ref" >/dev/null 2>&1; then
          printf 'enabled=no\nreason=untracked\nnote=%s\n' \
            "the autocommit flag is not committed on $ref — it takes effect only once committed there (it is authorization, not a scratch file)"
        else
          printf 'enabled=no\nreason=git-error\nnote=%s\n' \
            "could not read the autocommit flag from git — falling back to asking"
        fi
        return 0
      fi
      # The committed value is what authorizes, so a local edit can never silently
      # ENABLE the skip. A local edit SHOULD still be able to disable it (that is
      # a deliberate act by whoever holds the checkout), so a flag whose working
      # copy differs from HEAD falls back to asking.
      #
      # Compare BLOB HASHES, not `git diff --quiet`: the index bits that hide a
      # dirty file (`--assume-unchanged`, `--skip-worktree`) make diff report
      # "clean", which would silently ignore a deliberate local disable. Hashing
      # the file and the HEAD blob is index-independent, so both directions hold.
      local head_oid work_oid
      head_oid="$(git -C "$root" rev-parse "$ref:$rel" 2>/dev/null || true)"
      work_oid="$(git -C "$root" hash-object -- "$file" 2>/dev/null || true)"
      if [ -z "$work_oid" ] || [ "$work_oid" != "$head_oid" ]; then
        # Deliberately disabling locally is a SUPPORTED move, so don't answer it
        # with "commit or revert it to make it effective" — that tells the user to
        # undo the thing they just did, on every single close. Only content that
        # is neither the committed value nor an explicit off gets the corrective
        # wording.
        local local_val
        local_val="$(autocommit_normalize "$(cat "$file" 2>/dev/null || true)")"
        if [ "$local_val" = off ]; then
          printf 'enabled=no\nreason=locally-disabled\nnote=%s\n' \
            "the autocommit opt-in is switched off in your working copy (the committed flag is unchanged)"
        else
          printf 'enabled=no\nreason=modified\nnote=%s\n' \
            "the autocommit flag differs from the committed version — commit or revert it to make it effective"
        fi
        return 0
      fi

      case "$(autocommit_normalize "$head_val")" in
        on)  printf 'enabled=yes\n' ;;
        off) printf 'enabled=no\n' ;;
        *)   printf 'enabled=no\nreason=unrecognized-content\nnote=%s\n' \
               "the autocommit flag's content is not a recognized value (expected yes/true/1/on)" ;;
      esac
      ;;
    set)
      if [ -z "$root" ]; then autocommit_no_root_error set "$repo"; exit 2; fi
      autocommit_guard_paths "$root" "$file" set || exit 2
      mkdir -p "$root/.claude"
      # RE-VALIDATE after mkdir: the guard above runs before the directory
      # exists, so between the two a concurrent process could plant `.claude` as
      # a symlink and `mkdir -p` would succeed straight through it, landing every
      # later write outside the repo while `flag=` still reads in-repo.
      autocommit_guard_paths "$root" "$file" set || exit 2
      # Write through a mktemp'd file IN THE TARGET DIRECTORY, then rename. A
      # fixed `$file.tmp.$$` is PID-predictable: anything able to create files in
      # .claude/ could pre-plant that path as a symlink, have the redirect write
      # through it (clobbering an arbitrary file), and then have `mv` install the
      # symlink AS the flag. mktemp creates the file exclusively (O_EXCL) with a
      # random name, so there is nothing to pre-plant. Same directory keeps the
      # final `mv` a rename, so the flag is REPLACED, never written through.
      local tmp
      tmp="$(mktemp "$root/.claude/.work-system-close-autocommit.XXXXXX" 2>/dev/null || true)"
      if [ -z "$tmp" ]; then
        echo "autocommit set: could not create a temp file in $root/.claude" >&2; exit 1
      fi
      if ! printf 'yes\n' > "$tmp"; then
        rm -f "$tmp" 2>/dev/null || true
        echo "autocommit set: could not write $file" >&2; exit 1
      fi
      # Set the mode on the TEMP file, before it has the well-known name: a
      # `chmod` after the rename could follow a symlink swapped in behind it and
      # change some other file's mode. (mktemp creates 600; the flag is ordinary
      # repo content.)
      chmod 644 "$tmp" 2>/dev/null || true
      # Final re-check of both parent and leaf immediately before the rename.
      autocommit_guard_paths "$root" "$file" set || { rm -f "$tmp" 2>/dev/null || true; exit 2; }
      mv -f "$tmp" "$file"
      printf 'flag=%s\n' "$file"
      # A gitignored `.claude/` makes the whole opt-in impossible — `git add`
      # refuses the path, so `get` would report `untracked` forever while `set`
      # kept claiming success. Say so instead of sending the user in circles.
      if git -C "$root" check-ignore -q -- "$rel" 2>/dev/null; then
        printf 'note=%s\n' "$rel is gitignored — the opt-in cannot take effect until the path is un-ignored (or added with git add -f)"
      else
        printf 'note=%s\n' "commit this file — the opt-in takes effect only once it is committed"
      fi
      ;;
    unset)
      if [ -z "$root" ]; then autocommit_no_root_error unset "$repo"; exit 2; fi
      # Guard the PARENT here too: `rm -f` through a symlinked .claude would
      # delete a file outside the repo. (`rm` on a symlinked LEAF is safe — it
      # removes the link, not its target — but a symlinked parent is not.)
      autocommit_guard_paths "$root" "$file" unset || exit 2
      # Check the SAME ref `get` authorizes from — a HEAD-based check skipped the
      # reminder in exactly the case that needs it (flag still live on the
      # authorization branch while the checkout sits elsewhere).
      local unset_ref="HEAD"
      if [ -n "$mainbr" ] && git -C "$root" rev-parse --verify -q "refs/heads/$mainbr" >/dev/null 2>&1; then
        unset_ref="refs/heads/$mainbr"
      fi
      local was_committed=no
      git -C "$root" cat-file -e "$unset_ref:$rel" 2>/dev/null && was_committed=yes
      rm -f "$file"
      printf 'flag=%s\n' "$file"
      # Deleting the file makes `get` report disabled immediately, which LOOKS
      # done — but HEAD still carries the flag, so a fresh clone, a teammate, or a
      # `git restore` re-enables it. Mirror set's reminder.
      if [ "$was_committed" = yes ]; then
        printf 'note=%s\n' "commit this deletion — the opt-in stays active for any checkout that still has the committed file"
      fi
      ;;
  esac
}

case "${1:-}" in
  archive)     shift; archive "$@" ;;
  commit-push) shift; commit_push "$@" ;;
  autocommit)  shift; autocommit "$@" ;;
  *) echo "usage: ${0##*/} {archive <repo> <name> <branch> [--pr <n>] [--sha <s>] | commit-push <repo> <name> <archived-rel-path> <main-branch> | autocommit {get|set|unset} <repo> [<main-branch>]}" >&2; exit 2 ;;
esac
