#!/usr/bin/env bash
# herdr-tab-glyph.sh — mirror work-system task states onto herdr tab/agent
# names as a leading state glyph (○ not-started · ● active · ◇ in review ·
# ✓ merged), so the herdr sidebar speaks the same visual language as the
# [ws …] status-line segment.
#
# The state→glyph mapping and its precedence live in ws-statusline.sh
# (`states` mode) — the ONE source both surfaces read; this script only
# applies the result to herdr agent names. Everything is best-effort: prefix
# and refresh exit 0 and degrade silently when they cannot act (outside a
# herdr session, no herdr/python3, herdr unreachable, not a git repo, no
# backlog) — callers are launch paths and skill triggers that must never fail
# on cosmetics.
#
# Subcommands:
#   prefix <label> <worktree-abs-path>
#       Print the label prefixed with the current state glyph of the task
#       whose worktree this is ("<glyph> <label>") — the launch-time stamp
#       used by herdr-launch.sh. Strips any existing leading glyph first
#       (idempotent, re-runs never stack) and prints the plain label when the
#       state cannot be resolved. Always exits 0.
#   refresh [<dir>]
#       Re-stamp the glyph on every herdr agent whose cwd IS a task worktree
#       of the repo containing <dir> (default: $PWD) — exact realpath match on
#       <main>/.claude/worktrees/<task>, across ALL workspaces of this herdr
#       server, so one refresh fixes every open task tab of the repo. Agents
#       outside task worktrees are never touched; a rename is only issued when
#       the name actually changes. Prints `checked=N updated=M` when herdr was
#       reachable; silent no-op otherwise. Always exits 0.
set -u

SCRIPT_DIR="${0%/*}"

# Strip every leading "<glyph> " so re-prefixing is idempotent even if a prior
# bug stacked glyphs. case-prefix matching is byte-exact, so the multibyte
# glyphs are safe under any locale (a bracket expression would match per byte).
strip_glyph() {
  local l="$1"
  while :; do
    case "$l" in
      "○ "*|"● "*|"◇ "*|"✓ "*) l="${l#* }" ;;
      *) break ;;
    esac
  done
  printf '%s' "$l"
}

# Exact-match TSV lookup: print field 3 of the row whose field 1 == $1, from
# stdin. The needle travels via ENVIRON, NOT `awk -v` — -v escape-expands
# backslashes (a name like `fix\net` would silently never match).
glyph_lookup() {
  T="$1" awk -F'\t' '$1==ENVIRON["T"] { print $3; exit }'
}

# Glyph for task $1 of the repo containing dir $2, via ws-statusline.sh states
# (which sync-refreshes the PR cache first). Empty when unresolvable.
task_glyph() {
  local ws="$SCRIPT_DIR/ws-statusline.sh"
  [ -f "$ws" ] || return 0
  bash "$ws" states "$2" 2>/dev/null | glyph_lookup "$1"
}

# Emit "pane_id\ttask\tcurrent_name" for every agent whose realpath(cwd) is
# EXACTLY <main>/.claude/worktrees/<task> (argv[1] = main repo path). Exact
# match mirrors herdr-teardown.sh's cwd philosophy: an unrelated agent merely
# cd'd into a worktree subdir is never renamed. Malformed JSON emits nothing.
# The agent fields are UNTRUSTED (any tool in the session can set a name): a
# tab/newline embedded in a field would forge extra TSV records and aim a
# rename at an arbitrary pane — so the pane id must match herdr shape, a task
# name that would break the framing is skipped, and the free-form name is
# scrubbed (it is display data; a space is a faithful stand-in). The pane
# pattern forbids a LEADING dash (first char excludes `-`) so a value like
# `-x`/`--foo` can never reach `herdr agent rename` as an option flag; herdr
# ids are `wN:pM` and never start with a dash, so nothing legitimate is lost.
extract_task_agents='import sys, json, os, re
main = sys.argv[1] if len(sys.argv) > 1 else ""
if not main.strip():
    sys.exit(0)
wtdir = os.path.join(os.path.realpath(main), ".claude", "worktrees")
try:
    agents = json.load(sys.stdin)["result"]["agents"]
except Exception:
    sys.exit(0)
for a in agents:
    cwd = (a.get("cwd") or "").rstrip("/")
    pane = a.get("pane_id") or ""
    if not cwd or not re.fullmatch(r"[A-Za-z0-9:_.][A-Za-z0-9:_.-]*", pane):
        continue
    cwd = os.path.realpath(cwd)
    if os.path.dirname(cwd) != wtdir:
        continue
    task = os.path.basename(cwd)
    if re.search(r"[\t\r\n]", task):
        continue
    name = re.sub(r"[\t\r\n]", " ", a.get("name") or "")
    print("\t".join([pane, task, name]))'

cmd_prefix() {
  local label="${1:-}" worktree="${2:-}" base name glyph
  base="$(strip_glyph "$label")"
  [ -n "$base" ] || base="$label"
  # The fallback is ALWAYS the plain label — a failed lookup must not eat the
  # name the caller is about to stamp onto a tab.
  if [ -z "$worktree" ] || [ ! -d "$worktree" ]; then
    printf '%s\n' "$base"
    return 0
  fi
  name="$(basename "$worktree")"   # worktree dirs are named after the task
  glyph="$(task_glyph "$name" "$worktree" || true)"
  if [ -n "$glyph" ]; then
    printf '%s %s\n' "$glyph" "$base"
  else
    printf '%s\n' "$base"
  fi
}

cmd_refresh() {
  local dir="${1:-$PWD}"
  # The documented contract (and the pr-flow shim + kickoff/close/continue
  # precedent): outside a herdr session this is a silent no-op — the herdr CLI
  # could reach a server via its default socket even without the env, but
  # renaming tabs from outside the session would contradict the skills' prose.
  [ "${HERDR_ENV:-}" = "1" ]   || return 0
  command -v herdr   >/dev/null 2>&1 || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || return 0
  # Main worktree (first porcelain entry) — the backlog + worktrees live there.
  local main states list agents
  main="$(git -C "$dir" worktree list --porcelain 2>/dev/null | head -1)"
  main="${main#worktree }"
  [ -n "$main" ] || return 0
  states="$(bash "$SCRIPT_DIR/ws-statusline.sh" states "$dir" 2>/dev/null || true)"
  [ -n "$states" ] || return 0     # no backlog → nothing to stamp
  # Empty list output = herdr unreachable (binary present, server down) →
  # silent no-op, NOT `checked=0` — that line means "reachable, nothing to do".
  list="$(herdr agent list 2>/dev/null || true)"
  [ -n "$list" ] || return 0
  agents="$(printf '%s' "$list" \
    | python3 -c "$extract_task_agents" "$main" 2>/dev/null || true)"
  if [ -z "$agents" ]; then
    echo "checked=0 updated=0"
    return 0
  fi
  local checked=0 updated=0 pane task name glyph base new
  while IFS=$'\t' read -r pane task name; do
    [ -n "$pane" ] && [ -n "$task" ] || continue
    glyph="$(printf '%s\n' "$states" | glyph_lookup "$task")"
    [ -n "$glyph" ] || continue    # worktree without a backlog task — leave alone
    checked=$((checked + 1))
    base="$(strip_glyph "$name")"
    [ -n "$base" ] || base="$task" # unnamed agent → fall back to the task name
    new="$glyph $base"
    [ "$new" = "$name" ] && continue   # already correct — no rename churn
    # No `--` guard here: `herdr agent rename` treats `--` as the target itself
    # (verified), so it can't end option parsing. The no-leading-dash pane regex
    # in extract_task_agents is the sole (and sufficient) injection guard — pane
    # can never be a `-`-prefixed value that rename would read as a flag.
    if herdr agent rename "$pane" "$new" >/dev/null 2>&1; then
      updated=$((updated + 1))
    fi
  done <<<"$agents"
  echo "checked=$checked updated=$updated"
}

case "${1:-}" in
  prefix)  shift; cmd_prefix "$@" ;;
  refresh) shift; cmd_refresh "$@" ;;
  *)
    echo "usage: ${0##*/} {prefix <label> <worktree>|refresh [<dir>]}" >&2
    exit 2
    ;;
esac
exit 0
