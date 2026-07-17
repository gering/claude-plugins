#!/usr/bin/env bash
# herdr-tab-glyph.sh — mirror work-system task states onto herdr tab/agent
# names as a leading state glyph (○ not-started · ● active · ◇ in review ·
# ◆ approved · ✓ merged), so the herdr sidebar speaks the same visual language
# as the [ws …] status-line segment. A session sitting in the MAIN repo root
# instead gets ◉ — the "Manager" hub among the task satellites. ◉ marks the
# location, not an identity: if several tabs sit at the main root, they ALL
# carry it.
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
#   refresh [--cached] [<dir>]
#       Re-stamp the glyph on the LABEL of every herdr tab whose agent cwd IS a
#       task worktree of the repo containing <dir> (default: $PWD) — exact
#       realpath match on <main>/.claude/worktrees/<task> — or IS the main repo
#       root itself (→ ◉), across ALL workspaces of this herdr server, so one
#       refresh fixes every open tab of the repo. Both matches are exact: an
#       agent merely cd'd into a subdir of either is never touched, nor is
#       anything outside the repo. A rename is only issued when the label
#       actually changes. Prints `checked=N updated=M` when herdr was reachable;
#       silent no-op otherwise — including for a repo with an empty backlog,
#       where the ◉ stamp is skipped too (no workers, no manager). Always
#       exits 0.
#
# ONE NAMESPACE: the sidebar renders a tab's LABEL, so both the launch-time
# stamp (herdr-launch.sh, via `prefix`) and this refresh write the label and
# nothing else. A herdr *agent* also has a `name` — a different field, in
# herdr's agent registry, which other tooling owns. Never write it: 1.8.0's
# refresh did, which is why it silently never showed up in the sidebar.
#       --cached forwards to `ws-statusline.sh states --cached` (read the PR
#       cache + non-blocking background refresh, never a synchronous gh call) —
#       for pure-survey callers (/status, /list, /check, /close). Without it the
#       state is refreshed synchronously, for callers reacting to a PR change.
set -u

SCRIPT_DIR="${0%/*}"

# Strip every leading "<glyph> " so re-prefixing is idempotent even if a prior
# bug stacked glyphs. case-prefix matching is byte-exact, so the multibyte
# glyphs are safe under any locale (a bracket expression would match per byte).
strip_glyph() {
  local l="$1"
  while :; do
    case "$l" in
      "○ "*|"● "*|"◇ "*|"◆ "*|"✓ "*|"◉ "*) l="${l#* }" ;;
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

# Emit "tab_id\tkind\tkey\tcurrent_label" for every TAB whose agent cwd is
# EXACTLY <main>/.claude/worktrees/<task> (kind=task, key=<task>) or EXACTLY the
# main repo root (kind=main, key=the repo dir name). argv[1] = main repo path,
# argv[2] = `herdr tab list` JSON; stdin = `herdr agent list` JSON. Exact match
# mirrors herdr-teardown.sh's cwd philosophy: an agent merely cd'd into a subdir
# of either is never renamed. `key` doubles as the label fallback for an
# unlabelled tab. Malformed JSON emits nothing.
#
# THE SIDEBAR RENDERS THE TAB LABEL, NOT THE AGENT NAME — the two are separate
# herdr namespaces (`tab rename <tab_id>` vs `agent rename <pane_id>`). We join
# the two lists because only agents carry `cwd` (the match) while only tabs
# carry `label` (what we prefix and diff against). Renaming agents instead is
# invisible; that was the 1.8.0 bug.
#
# A tab is emitted ONCE (first matching agent wins). A tab whose panes sit in
# *different* matching dirs would otherwise flip-flop each refresh; first-wins
# is stable given herdr's stable agent order — an accepted residual for that
# rare mixed-pane tab.
#
# The agent/tab fields are UNTRUSTED (any tool in the session can set them): a
# tab/newline embedded in a field would forge extra TSV records and aim a rename
# at an arbitrary tab — so the tab id must match herdr shape, a task name that
# would break the framing is skipped, and the free-form label is scrubbed (it is
# display data; a space is a faithful stand-in). The id pattern forbids a LEADING
# dash (first char excludes `-`) so a value like `-x`/`--foo` can never reach
# `herdr tab rename` as an option flag; herdr ids are `wN:tM` and never start
# with a dash, so nothing legitimate is lost.
extract_glyph_tabs='import sys, json, os, re
main = sys.argv[1] if len(sys.argv) > 1 else ""
if not main.strip():
    sys.exit(0)
root = os.path.realpath(main)
wtdir = os.path.join(root, ".claude", "worktrees")
try:
    agents = json.load(sys.stdin)["result"]["agents"]
except Exception:
    sys.exit(0)
try:
    tabs = json.loads(sys.argv[2])["result"]["tabs"]
except Exception:
    sys.exit(0)
labels = {t.get("tab_id"): (t.get("label") or "") for t in tabs}
seen = set()
for a in agents:
    cwd = (a.get("cwd") or "").rstrip("/")
    tab = a.get("tab_id") or ""
    if not cwd or tab in seen:
        continue
    if not re.fullmatch(r"[A-Za-z0-9:_.][A-Za-z0-9:_.-]*", tab) or tab not in labels:
        continue
    cwd = os.path.realpath(cwd)
    if cwd == root:
        kind, key = "main", os.path.basename(root)
    elif os.path.dirname(cwd) == wtdir:
        kind, key = "task", os.path.basename(cwd)
    else:
        continue
    if not key or re.search(r"[\t\r\n]", key):
        continue
    seen.add(tab)
    label = re.sub(r"[\t\r\n]", " ", labels[tab])
    print("\t".join([tab, kind, key, label]))'

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
  local cached=""
  [ "${1:-}" = "--cached" ] && { cached="--cached"; shift; }
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
  local main states list tablist tabs
  main="$(git -C "$dir" worktree list --porcelain 2>/dev/null | head -1)"
  main="${main#worktree }"
  [ -n "$main" ] || return 0
  # $cached is unquoted so an empty value expands to no argument (not "").
  states="$(bash "$SCRIPT_DIR/ws-statusline.sh" states $cached "$dir" 2>/dev/null || true)"
  [ -n "$states" ] || return 0     # no backlog → nothing to stamp
  # Empty list output = herdr unreachable (binary present, server down) →
  # silent no-op, NOT `checked=0` — that line means "reachable, nothing to do".
  # Agents carry the cwd we match on; tabs carry the label we stamp. Both.
  list="$(herdr agent list 2>/dev/null || true)"
  [ -n "$list" ] || return 0
  tablist="$(herdr tab list 2>/dev/null || true)"
  [ -n "$tablist" ] || return 0
  tabs="$(printf '%s' "$list" \
    | python3 -c "$extract_glyph_tabs" "$main" "$tablist" 2>/dev/null || true)"
  if [ -z "$tabs" ]; then
    echo "checked=0 updated=0"
    return 0
  fi
  local checked=0 updated=0 tab kind key label glyph base new
  while IFS=$'\t' read -r tab kind key label; do
    [ -n "$tab" ] && [ -n "$key" ] || continue
    if [ "$kind" = "main" ]; then
      glyph="◉"                    # the Manager hub — no state, just the place
    else
      glyph="$(printf '%s\n' "$states" | glyph_lookup "$key")"
      [ -n "$glyph" ] || continue  # worktree without a backlog task — leave alone
    fi
    checked=$((checked + 1))
    base="$(strip_glyph "$label")"
    [ -n "$base" ] || base="$key"  # unlabelled tab → task name / repo dir name
    new="$glyph $base"
    [ "$new" = "$label" ] && continue   # already correct — no rename churn
    # No `--` guard here: `herdr tab rename` treats `--` as the target itself
    # (verified), so it can't end option parsing. The no-leading-dash id regex
    # in extract_glyph_tabs is the sole (and sufficient) injection guard — tab
    # can never be a `-`-prefixed value that rename would read as a flag.
    if herdr tab rename "$tab" "$new" >/dev/null 2>&1; then
      updated=$((updated + 1))
    fi
  done <<<"$tabs"
  echo "checked=$checked updated=$updated"
}

case "${1:-}" in
  prefix)  shift; cmd_prefix "$@" ;;
  refresh) shift; cmd_refresh "$@" ;;
  *)
    echo "usage: ${0##*/} {prefix <label> <worktree>|refresh [--cached] [<dir>]}" >&2
    exit 2
    ;;
esac
exit 0
