#!/usr/bin/env bash
# claude-review.sh — shared helper for pr-flow skills
#
# Subcommands:
#   poll <PR> <SINCE_ISO> [--max N] [--interval S]
#       Poll the PR for a completed Claude review created after SINCE_ISO.
#       Prints the review body on success, "TIMEOUT" and exits 1 on timeout.
#       Default: 20 iterations, 30s interval (= 10 minutes max).
#
#   latest <PR> [--json]
#       Print the body of the latest Claude review comment (any status).
#       With --json: print {createdAt, body} as JSON instead of raw body.
#       Prints empty string (or empty JSON) if no Claude comments exist.
#
#   latest-after <PR> <SINCE_ISO> [--json]
#       Like `latest`, but only considers comments created after SINCE_ISO.
#
#   has-bot [<dir>]
#       Does this repo have a comment-triggered Claude review workflow?
#       Always emits the same four keys: has_bot=yes|no|unknown, why=,
#       workflows_dir=, matched= (empty when not applicable). Anchored on the
#       repo ROOT (git rev-parse), never $PWD: /cycle can legitimately run from
#       a subdirectory or from the main repo while the PR belongs to a
#       worktree, and a cwd-relative probe reports "no bot" there.
#         yes      a top-level workflow both `uses:` anthropics/claude-code-action
#                  AND is triggered by issue_comment, as structure (not in a
#                  comment or a run: block) — @claude review will reach it
#         no       reserved, and in practice unreachable: no local signal can
#                  rule out the GitHub App, which answers comments with no
#                  workflow file at all. Kept as a verdict for a future
#                  authoritative source (an API probe), not emitted today.
#         unknown  everything else, and it is the NORMAL answer: a scan proves
#                  presence, never absence — the Claude GitHub App answers with
#                  no workflow file at all, so "no workflow references the bot"
#                  cannot be told apart from "the App is installed". Also: no
#                  git repo, unreadable workflows dir, no workflows dir, a
#                  comment-triggered workflow that mentions @claude without
#                  using the action or delegates to a reusable workflow, and a
#                  custom trigger_phrase.
#       unknown is its own answer — never reroute on it. A caller that TRIGGERS
#       may try the bot and let a bounded poll settle it; a recommend-only
#       caller names both routes.
#
# Exit codes:
#   0 = success (output contains the body, possibly empty for `latest`)
#   1 = timeout (poll) or error
#   2 = invalid arguments
#   has-bot exits 0 for every answer, unknown included — the verdict is the
#   has_bot= line, never the status.

set -euo pipefail

usage() {
  # Every comment line after the shebang, up to the first non-comment line — the
  # header IS the usage text, so it must not be truncated by a hardcoded range.
  awk 'NR == 1 { next } !/^#/ { exit } { sub(/^# ?/, ""); print }' "$0"
  exit 2
}

require_gh() {
  command -v gh >/dev/null || { echo "gh CLI not installed" >&2; exit 1; }
  gh auth status >/dev/null 2>&1 || { echo "gh not authenticated — run: gh auth login" >&2; exit 1; }
}

subcmd_latest() {
  local pr="${1:-}"
  [[ -z "$pr" ]] && usage
  local as_json=false
  [[ "${2:-}" == "--json" ]] && as_json=true
  require_gh
  if $as_json; then
    gh pr view "$pr" --json comments \
      --jq '[.comments[] | select(.author.login == "claude")] | last | {createdAt: (.createdAt // ""), body: (.body // "")}'
  else
    gh pr view "$pr" --json comments \
      --jq '[.comments[] | select(.author.login == "claude")] | last | .body // ""'
  fi
}

subcmd_latest_after() {
  local pr="${1:-}" since="${2:-}"
  [[ -z "$pr" || -z "$since" ]] && usage
  local as_json=false
  [[ "${3:-}" == "--json" ]] && as_json=true
  require_gh
  # Strip fractional seconds from .createdAt to match the whole-second
  # precision of $since (produced by `date -u +%Y-%m-%dT%H:%M:%SZ`). Without
  # this, a comment created within the same whole second as the trigger
  # compares lexicographically as earlier ("56.5Z" < "56Z") and is missed.
  if $as_json; then
    gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select((.createdAt | sub(\"\\\\.[0-9]+Z$\"; \"Z\")) > \"$since\")] | last | {createdAt: (.createdAt // \"\"), body: (.body // \"\")}"
  else
    gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select((.createdAt | sub(\"\\\\.[0-9]+Z$\"; \"Z\")) > \"$since\")] | last | .body // \"\""
  fi
}

subcmd_poll() {
  local pr="${1:-}" since="${2:-}"
  [[ -z "$pr" || -z "$since" ]] && usage
  shift 2 || true

  local max_iters=20
  local interval=30
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max)      max_iters="$2"; shift 2 ;;
      --interval) interval="$2";  shift 2 ;;
      *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
  done

  require_gh

  for ((i=1; i<=max_iters; i++)); do
    sleep "$interval"
    local body
    # Strip fractional seconds from .createdAt for whole-second comparison
    # with $since (see note in subcmd_latest_after).
    body=$(gh pr view "$pr" --json comments \
      --jq "[.comments[] | select(.author.login == \"claude\") | select((.createdAt | sub(\"\\\\.[0-9]+Z$\"; \"Z\")) > \"$since\")] | last | .body // \"\"")

    if [[ -n "$body" ]]; then
      # "Claude Code is working" = in-progress marker; keep polling
      if [[ "$body" == *"Claude Code is working"* ]]; then
        continue
      fi
      # "**Claude finished" = completion marker
      if [[ "$body" == *"**Claude finished"* ]]; then
        printf '%s\n' "$body"
        exit 0
      fi
    fi
  done

  echo "TIMEOUT" >&2
  exit 1
}

# Detect a review bot without a network call. "Bot" means: a workflow that
# `uses:` anthropics/claude-code-action AND is triggered by issue_comment — the
# two together are what `@claude review` needs. Either alone is not a bot: a
# push-triggered claude workflow never sees the comment, and a comment-triggered
# workflow that merely mentions @claude in a doc line consumes nothing.
#
# The old inline form was a bare `grep -rlie claude .github/workflows/` copied
# into two SKILL.md files: cwd-relative, and loose enough that a cache key
# counted as a bot. The first replacement here round-tripped the file list
# through an unquoted `$hits`, so any checkout under a path with a space ("My
# Projects", iCloud) reported has_bot=no for a working bot. Paths are now
# NUL-delimited end to end and every grep gets `--` before its operand.
#
# What cannot be told locally is reported as `unknown`, not guessed: no
# workflows dir at all (a repo served only by the Claude GitHub App has none),
# an unreadable dir, or a comment workflow with a loose @claude mention. The
# caller asks in those cases — asking once beats ten minutes of polling a bot
# that is not there, and beats permanently rerouting one that is.
# Classify ONE workflow file in a single pass. Emits five 0/1 flags:
#   action   a structural `uses: anthropics/claude-code-action` step
#   comment  a structural issue_comment trigger
#   phrase   a trigger_phrase that is not @claude (the comment never fires it)
#   reusable a job-level `uses:` of another workflow file (cannot see inside)
#   mention  the bot's name anywhere at all — comments and scalars included
# "Structural" means outside a `#` comment and outside a block scalar (`run: |`),
# where the same text is a string GitHub never interprets: the substring greps
# this replaces reported a TODO comment and a shell heredoc as a working bot.
# `mention` deliberately stays raw — it only ever lowers the answer to unknown.
read -r -d '' HAS_BOT_AWK <<'AWK' || true
{
  raw = $0; sub(/\r$/, "", raw)
  if (tolower(raw) ~ /anthropics\/claude-code-action|@claude/) mention = 1
  match(raw, /^[ \t]*/); ind = RLENGTH
  if (inblock) {
    if (raw ~ /^[ \t]*$/ || ind > bind) next
    inblock = 0
  }
  line = raw
  sub(/^#.*/, "", line); sub(/[ \t]#.*/, "", line)
  if (line ~ /^[ \t]*-?[ \t]*[A-Za-z0-9_.-]+:[ \t]*[|>][-+0-9]*[ \t]*$/) { inblock = 1; bind = ind; next }
  tl = tolower(line)
  if (tl ~ /^[ \t]*-?[ \t]*uses:[ \t]*["']?anthropics\/claude-code-action/) action = 1
  else if (tl ~ /^[ \t]*uses:[ \t]*["']?[^"' \t]+\.ya?ml(@|["' \t]|$)/) reusable = 1
  # `issue_comment` counts as a TRIGGER only under the top-level `on:` mapping.
  # Matching it at any indentation made a `with:\n  issue_comment: true` step, or
  # a job `env:` entry, look like a comment trigger on a push-only workflow —
  # and the caller then commented into the void and polled to the timeout.
  # Keys may be quoted in valid YAML (`"issue_comment":`, `"on":`), and `on:`
  # also takes flow style (`on: [issue_comment]`).
  q = tl; gsub(/["]/, "", q); gsub(/\047/, "", q)
  if (q ~ /^on[ \t]*:/) {
    rest = q; sub(/^on[ \t]*:[ \t]*/, "", rest)
    # Flow style: `on: [issue_comment, push]`. Match the TOKEN, not a substring —
    # `on: {push: {branches: [issue_comment]}}` names a branch, not a trigger.
    if (rest ~ /^\[/ && rest ~ /(^|[\[, ])issue_comment([],]|[ \t]*$)/) comment = 1
    in_on = (rest ~ /^$/) ? 1 : 0
    on_ind = -1
  } else if (q ~ /^[^ \t]/) {
    in_on = 0            # any other column-0 key ends the on: block
  } else if (in_on) {
    # Only a DIRECT child of `on:` is a trigger. Matching at ANY depth below it
    # made `push: { branches: [issue_comment] }` and a `workflow_call` input
    # named issue_comment read as comment triggers, so the caller commented into
    # the void and polled to the timeout. The first indented line fixes the
    # child depth; anything deeper belongs to that child, not to `on:`.
    match(q, /^[ \t]*/); qind = RLENGTH
    if (on_ind < 0) on_ind = qind
    if (qind == on_ind && q ~ /^[ \t]*-?[ \t]*issue_comment[ \t]*(:.*)?$/) comment = 1
  }
  if (tl ~ /^[ \t]*trigger_phrase:/) {
    v = tl; sub(/^[ \t]*trigger_phrase:[ \t]*/, "", v)
    if (v !~ /@claude/) phrase = 1
  }
}
END { printf "%d %d %d %d %d\n", action, comment, phrase, reusable, mention }
AWK

# The ref an issue_comment workflow would actually run from. GitHub resolves
# `issue_comment`-triggered workflows from the repository DEFAULT BRANCH, not
# from the PR head — so probing the checked-out tree answers a question nobody
# asked: a task branch that adds the workflow would probe `yes` and poll into
# the void, one that removes it would probe `no` and reroute a working bot.
# Prints "<ref> <how>": the ref to read, and whether it is the repo's actual
# default branch (`head`) or a guess (`guess`). Empty output = no default branch
# resolvable (a fresh `git init`, no remote), and the caller then falls back to
# the working tree and says so.
#
# Two things this gets right that the first version did not. The remote is the
# CURRENT BRANCH's upstream remote, not a hardcoded `origin` — a repo tracking
# `upstream` whose default is `trunk` was probed against a stale `origin/main`.
# And candidates are verified as FULLY-QUALIFIED refs: `rev-parse main` resolves
# a *tag* named main ahead of the branch, so an auto-fetched tag decided the
# answer. (`archive-task.sh` already encodes the qualified-ref rule.)
probe_ref() {
  local dir="$1" branch remote head cand
  branch="$(git -C "$dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  remote=""
  [[ -n "$branch" ]] && remote="$(git -C "$dir" config --get "branch.$branch.remote" 2>/dev/null || true)"
  [[ -n "$remote" ]] || remote=origin
  head="$(git -C "$dir" symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null || true)"
  if [[ -n "$head" ]] \
     && git -C "$dir" rev-parse --verify --quiet "refs/remotes/$head^{commit}" >/dev/null 2>&1; then
    printf '%s head\n' "refs/remotes/$head"; return 0
  fi
  for cand in "refs/remotes/$remote/main" "refs/remotes/$remote/master" \
              refs/remotes/origin/main refs/remotes/origin/master \
              refs/heads/main refs/heads/master; do
    if git -C "$dir" rev-parse --verify --quiet "$cand^{commit}" >/dev/null 2>&1; then
      printf '%s guess\n' "$cand"; return 0
    fi
  done
  return 0
}

subcmd_has_bot() {
  local dir="${1:-.}" root wf="" ref="" how="" src tree="" f a c p r m
  local strict="" phrase="" loose="" reusable="" pushonly="" n_files=0
  # One emitter, so every path prints the same four keys in the same order —
  # a consumer that greps a promised key must never get silence on some verdicts.
  emit() {
    echo "has_bot=$1"
    echo "why=$2"
    echo "workflows_dir=$wf"
    echo "matched=${3:-}"
  }
  root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$root" ]]; then
    emit unknown "not inside a git repository"; return 0
  fi
  read -r ref how <<<"$(probe_ref "$root")"
  if [[ -n "$ref" ]]; then
    src="$ref"
    [[ "$how" = head ]] || src="$ref (guessed: the repo does not record a default branch)"
    wf="$ref:.github/workflows"
    # ONE ls-tree, reused. Running it once as an existence probe and again to
    # list meant a concurrent fetch between the two could make `why=` claim the
    # directory exists while the listing came back empty.
    #
    # Newline-delimited, NOT -z: a command substitution cannot carry NUL bytes
    # (bash drops them), so `-z` here produced one unterminated field and the
    # loop below saw NO files at all — a working bot reported as absent.
    # `core.quotePath=false` keeps UTF-8 names literal; a name containing a real
    # newline still comes back C-quoted, fails the *.yml test, and is skipped —
    # which yields `unknown`, the safe direction.
    if ! tree="$(git -C "$root" -c core.quotePath=false ls-tree --name-only "$ref:.github/workflows" 2>/dev/null)"; then
      emit unknown "no .github/workflows on the default branch ($src) — a repo with no CI, or one served only by the Claude GitHub App; cannot tell locally"; return 0
    fi
  else
    # No default branch to read (fresh repo, no remote): the working tree is
    # the only thing there is. Say which ref was inspected either way, or a
    # wrong answer is unfalsifiable from the output.
    src="worktree"
    wf="$root/.github/workflows"
    if [[ ! -e "$wf" ]]; then
      emit unknown "no .github/workflows directory (no default branch to read; inspected the working tree) — a repo with no CI, or one served only by the Claude GitHub App; cannot tell locally"; return 0
    fi
    if [[ ! -d "$wf" || ! -r "$wf" || ! -x "$wf" ]]; then
      emit unknown ".github/workflows exists but is not readable"; return 0
    fi
  fi
  # Top level only: GitHub reads workflows from that directory itself, never
  # from a subdirectory — an archived copy under workflows/old/ is not a bot.
  # Newline-delimited on BOTH paths (see the ls-tree note above for why NUL
  # cannot survive the ref path). Spaces and UTF-8 are fine; a name containing a
  # literal newline is the one case that degrades, and it degrades to `unknown`.
  while IFS= read -r f; do
    f="${f##*/}"          # the worktree path arrives absolute, the ref name bare
    [[ -n "$f" ]] || continue
    case "$f" in *.yml|*.yaml) ;; *) continue ;; esac
    n_files=$(( n_files + 1 ))
    if [[ "$src" = "worktree" ]]; then
      [[ -r "$wf/$f" ]] || { emit unknown "unreadable workflow file: $wf/$f"; return 0; }
      read -r a c p r m <<<"$(awk "$HAS_BOT_AWK" "$wf/$f")"
    else
      read -r a c p r m <<<"$(git -C "$root" show "$ref:.github/workflows/$f" 2>/dev/null | awk "$HAS_BOT_AWK")"
    fi
    if (( a && c )); then
      if (( p )); then phrase="$phrase$f "; else strict="$strict$f "; fi
    elif (( a )); then
      pushonly="$pushonly$f "
    elif (( c && r )); then
      reusable="$reusable$f "
    elif (( c && m )); then
      loose="$loose$f "
    fi
  done < <(
    if [[ "$src" = "worktree" ]]; then
      find "$wf" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print 2>/dev/null
    else
      printf '%s\n' "$tree"
    fi
  )

  if (( n_files == 0 )); then
    emit unknown ".github/workflows holds no workflow files (inspected $src) — same as no directory; cannot tell locally"; return 0
  fi
  if [[ -n "$strict" ]]; then
    emit yes "a comment-triggered workflow uses anthropics/claude-code-action (inspected $src)" "$strict"; return 0
  fi
  if [[ -n "$phrase" ]]; then
    emit unknown "a comment-triggered claude workflow sets a custom trigger_phrase — @claude review may not fire it" "$phrase"; return 0
  fi
  if [[ -n "$reusable" ]]; then
    emit unknown "a comment-triggered workflow delegates to a reusable workflow — cannot see locally whether that is the review bot" "$reusable"; return 0
  fi
  if [[ -n "$loose" ]]; then
    emit unknown "a comment-triggered workflow mentions the bot but does not use anthropics/claude-code-action — cannot tell whether @claude review reaches anything" "$loose"; return 0
  fi
  if [[ -n "$pushonly" ]]; then
    # NOT `no`. A `pull_request`-triggered claude workflow (Anthropic ships one)
    # is evidence about that WORKFLOW, not about the GitHub App — a repo can run
    # both, and `no` is the one verdict no consumer is allowed to question. The
    # same "a scan proves presence, never absence" rule that governs the
    # fall-through below governs here.
    emit unknown "a claude workflow is present but not triggered by issue_comment (inspected $src) — the Claude GitHub App may still answer comments, which cannot be seen locally" "$pushonly"; return 0
  fi
  # NOT `no`. A workflow scan can PROVE a bot (a matching workflow is there) but
  # never disprove one: the Claude GitHub App answers @claude review with no
  # workflow file of its own, and unrelated CI in the same directory says
  # nothing about whether the App is installed. Answering `no` here rerouted a
  # working bot permanently and silently — the exact failure REVIEW-ROUTING.md
  # warns about — because `no` is the one answer no consumer asks about.
  emit unknown "no workflow references the review bot (inspected $src) — but the Claude GitHub App needs none, so its absence cannot be shown locally"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    poll)          subcmd_poll "$@" ;;
    latest)        subcmd_latest "$@" ;;
    latest-after)  subcmd_latest_after "$@" ;;
    has-bot)       subcmd_has_bot "$@" ;;
    ""|-h|--help)  usage ;;
    *)             echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
