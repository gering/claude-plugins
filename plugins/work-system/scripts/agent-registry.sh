#!/usr/bin/env bash
# agent-registry.sh — the set of worker agents /kickoff can launch.
#
# One CLI × model per entry. The canonical NAME is `cli:model` (e.g.
# claude:fable, codex:gpt-5.6-terra). Everything is registry-driven: the shell
# `REGISTRY` table below is the single source of truth, so the skill and the
# launch helper never hardcode a CLI list or an alias `if` chain.
#
# Subcommands:
#   list [--json]                 Probe every entry -> human table or JSON array
#                                 (columns: name, cli, model, available, note)
#   resolve <selector> [--session <name>]
#                                 Map a selector to launch argv + metadata.
#                                 Selectors: a shorthand flag (--fable, --opus,
#                                 --codex, --sol, --grok, --kimi), a
#                                 canonical name (claude:opus), a bare CLI
#                                 (codex -> that CLI's default model), or
#                                 cli:model (the --agent escape hatch).
#                                 Emits key=value lines incl. one `argv=` line
#                                 per exec word. Exit 3 if the entry's CLI is
#                                 unavailable (still prints available=no + note).
#   default get                   Print the repo's default agent name, or empty
#                                 if none is set (then no-flag /kickoff picks).
#   default set <name>            Persist the default in the repo's committed
#                                 .claude/work-system-agent.
#
# Launch shape per CLI (resolve builds the argv; the launch helper just execs
# the `argv=` words, so the argv-exec path — no shell-typing race — is kept):
#   claude  -> claude --model <model> [-n <session>] /work-system:continue
#              (plugin-qualified so a CC built-in /continue can't shadow the skill;
#              the work-system continue skill resumes TASK.md deterministically)
#   codex   -> codex -m <model> <bootstrap-prompt>
#   grok    -> grok  -m <model> <bootstrap-prompt>
#   kimi    -> sh -c 'if <seed>; then exec kimi -c --auto; else <marker+exit>; fi' \
#                    kimi-worker <model> <bootstrap-prompt>          (seed+continue)
#   The bootstrap prompt (codex/grok/kimi have no work-system skills) tells the
#   agent to read TASK.md and drive the task to a PR. `supports=` metadata records
#   which lifecycle hooks each agent honors, so /close and /continue can degrade
#   for non-claude workers instead of faking claude-only behavior.
#
# HERDR TRANSPORT metadata (`herdr_mode=` / `herdr_kind=`).
# herdr 0.7.5+ starts agents in an ALREADY-OPEN pane (`agent start <name> --kind
# <kind> --pane <id> -- <native args>`) instead of placing them itself. That needs
# two facts the launcher must NOT guess from a selector name or by parsing argv[0]:
#   herdr_mode=agent-start  the argv IS a canonical CLI invocation, so the launcher
#                           drops argv[0] (which MUST equal herdr_kind, herdr's
#                           canonical executable for that kind) and hands the rest
#                           to `--kind`. Native claude/codex/grok entries.
#   herdr_mode=pane-run     the argv is a WRAPPER that cannot be projected onto
#                           `--kind` (kimi's two-phase seed+continue `sh -c`). The
#                           launcher sends `argv_shell=` as one `pane run` command
#                           and then waits until herdr detects `herdr_kind` in that
#                           exact pane.
# The pair is generic on purpose: a future dynamically-registered wrapper (e.g. a
# cc-harness agent that ends up as an interactive `claude`) declares
# `pane-run` + `herdr_kind=claude` without any launcher change. An entry whose
# mode the launcher does not know must fail CLOSED before anything is created.
#
#   Why kimi needs the two-phase seed+continue shape (all probed live, 0.31.1):
#   kimi has NO positional launch prompt (`kimi "text"` -> "unknown command"), no
#   initial-prompt env var, and piped stdin only prefills the input box without
#   submitting (and would steal the TUI's tty anyway). `-p` is the only way in,
#   but it is mutually exclusive with BOTH `--auto` and `-y` and exits after one
#   answer — so `-p` alone cannot be a worker. It does run tools unattended, and
#   `kimi -c` inherits its full session history, so: phase 1 seeds+works the task
#   one-shot, phase 2 `exec`s into the interactive autonomous session with that
#   history. The `exec` matters — it re-roots the herdr pane at kimi. Phase 2 is
#   reached ONLY on a successful seed: a failed one prints the machine-readable
#   marker and exits with the seed's own code, because an empty `kimi -c --auto`
#   session that never read TASK.md is indistinguishable from a healthy worker (see
#   KIMI_LAUNCH_SCRIPT). Values travel as "$1"/"$2" positionals, never interpolated into the
#   script text: `-p` swallows the next token as its value, so an argv built by
#   concatenation is one reordering away from silently eating a flag.
#
# State & config (override for tests / relocation):
#   WORK_SYSTEM_AGENT_PROJECT_STATE  the repo's default-agent file
#                                    default: <repo-root>/.claude/work-system-agent
#   No global state and no shipped fallback — a repo with no default gets the
#   picker instead.
#
# Exit codes: 0 ok · 1 not-available / no-op · 2 usage / unknown selector ·
#             3 resolved but the entry's CLI is unavailable
set -euo pipefail

# Where this script (and its sibling helpers) actually live. `${0%/*}` is NOT a
# safe substitute: invoked as a bare name (`bash agent-registry.sh`, or via PATH)
# it has no slash, so `${0%/*}` yields the FILENAME and every sibling path built
# from it silently misses.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

HOME="${HOME:-$(cd ~ 2>/dev/null && pwd || echo /nonexistent)}"
# The ONLY persisted state: the per-repo committed `default` agent. No global
# state, no shipped fallback — if a repo has no default, /kickoff shows the
# picker (and offers to save the pick here). Defaults to
# <repo-root>/.claude/work-system-agent; overridable for tests.
PROJECT_STATE="${WORK_SYSTEM_AGENT_PROJECT_STATE:-}"
if [ -z "$PROJECT_STATE" ]; then
  # The default belongs in the MAIN repo, not a linked worktree — a `default set`
  # run from inside a worktree must still land (and commit) in the main checkout,
  # not the disposable copy that `/close` removes. `--git-common-dir` points at the
  # main repo's `.git` from anywhere; its parent is the main worktree root. Fall
  # back to `--show-toplevel` when that can't be resolved (older git / odd layout).
  _common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$_common" ] && [ "$(basename "$_common")" = ".git" ]; then
    _repo_root="$(dirname "$_common")"
  else
    _repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi
  [ -n "$_repo_root" ] && PROJECT_STATE="$_repo_root/.claude/work-system-agent"
fi
GROK_AUTH_FILE="${GROK_AUTH_FILE:-$HOME/.grok/auth.json}"
# kimi's OAuth tokens live in credentials/, NOT in the same-named oauth/ dir —
# `~/.kimi-code/oauth/kimi-code` exists but stays 0 bytes even when logged in, so
# probing that path would report every authenticated install as logged out.
KIMI_CREDENTIALS_FILE="${KIMI_CREDENTIALS_FILE:-$HOME/.kimi-code/credentials/kimi-code.json}"

# The bootstrap prompt for CLIs without work-system skills (codex, grok, kimi). One
# argv word; the launch helper passes it verbatim.
BOOTSTRAP_PROMPT='Read TASK.md in this worktree and continue the task. Commit on the current branch as you go, and open a PR when the work is complete.'

# The ASCII marker a wrapper worker prints when its seed phase fails. It names the
# failure unambiguously and states that TASK.md was never started, where the
# pre-1.11.1 shape left an empty session that looked healthy. WHO reads it depends
# on the path: on legacy herdr and on a manual `argv_shell=` paste it is the human
# looking at the tab; on the modern path the launcher detects the dead wrapper and
# rolls that tab back, so there the marker is what the seed's own error sits next
# to while it is still on screen — the launch failure itself is reported by the
# launcher, which tells the user to run the worker by hand to see it.
#
# It is deliberately NOT a machine signal. herdr-launch.sh used to grep the pane
# for it; three review rounds showed terminal text cannot carry a supervisor's
# signal at all — the pane echoes the command (so the literal matched itself), the
# seed reads repo files that legitimately name it, a per-launch nonce had to be
# handed to the very process being supervised, and a rendered snapshot wraps. The
# launcher now uses process STATE instead (the pane returning to its shell prompt
# without the worker being detected), so nothing here needs to be greppable.
SEED_FAIL_MARKER='WORKER_SEED_FAILED'

# kimi's two-phase launch script (see the header). Defined once here so the shape
# has exactly one home; emit_argv passes it as the `sh -c` word.
#
# A failed seed must be UNAMBIGUOUS, and it must not leave anything behind that
# could be mistaken for a working worker. The earlier shape ran phase 2 regardless
# (`;`) after a keypress, which produced exactly that trap: an empty `kimi -c
# --auto` session that never read TASK.md but looks alive to herdr's detection —
# and to the user. So on a seed failure this now prints the machine-readable
# marker, states that TASK.md was not started, and exits with the SEED'S exit code
# WITHOUT running phase 2 and without waiting for input (an unattended background
# tab has nobody to press Enter). Only a successful seed reaches `exec kimi -c
# --auto`; the `exec` re-roots the pane at kimi so herdr detects it.
# Consequence on the LEGACY herdr path (0.7.0-0.7.4), where the worker argv is the
# tab's ROOT process: a failed seed now ends that process, so herdr closes the tab.
# Accepted deliberately — a closed tab is honest, whereas the empty session it
# replaces was actively misleading. On modern herdr the wrapper runs inside a shell
# pane, so the marker and kimi's own error land on screen; the launcher sees the
# pane fall back to its prompt without a worker and rolls the tab back.
# Kept ASCII-only and free of backslash escapes so `shell_quote` renders it as a
# plain single-quoted word in `argv_shell=` — a `$'…'` form would be bash/zsh-only
# and near-unreadable in the copy-paste block.
KIMI_LAUNCH_SCRIPT='if kimi -m "$1" -p "$2"; then exec kimi -c --auto; else rc=$?; echo; echo "[work-system] '"$SEED_FAIL_MARKER"': kimi seed exited $rc - TASK.md was NOT started and no session was opened."; exit $rc; fi'

# ---------- registry ----------
# `flag|cli|model|supports|herdr_mode|herdr_kind`. flag `-` = no shorthand
# (name/--agent only). The
# FIRST entry of each CLI is that CLI's default model (for a bare `--agent codex`).
# `supports` is per-agent capability metadata: which lifecycle hooks each agent
# honors —
#   continue   -> `/continue`-reopen + `claude -c` session resume work
#   close-exit -> /close may inject `/exit` for a clean self-teardown
#   statusline -> the `[ws]` statusline segment tracks its session
# codex/grok/kimi get commit,pr only — they drive git + a PR but have none of the
# claude-session lifecycle hooks. RESERVED / not yet consumed: the skills
# currently hardcode the claude-vs-non-claude distinction in prose; this field is
# the seed for the manager/worker-orchestration design to read per-agent
# capabilities from one place. Keep it in sync when that lands.
#
# `herdr_mode|herdr_kind` is the modern-herdr transport contract (see the header):
# agent-start entries hand their argv TAIL to `--kind <herdr_kind>` (so argv[0]
# MUST equal herdr_kind), pane-run entries are wrappers sent as one shell command
# and then waited for until herdr detects herdr_kind in that pane.
REGISTRY='--fable|claude|fable|continue,close-exit,statusline,commit,pr|agent-start|claude
--opus|claude|opus|continue,close-exit,statusline,commit,pr|agent-start|claude
-|claude|sonnet|continue,close-exit,statusline,commit,pr|agent-start|claude
--codex|codex|gpt-5.6-terra|commit,pr|agent-start|codex
--sol|codex|gpt-5.6-sol|commit,pr|agent-start|codex
--grok|grok|grok-4.5|commit,pr|agent-start|grok
--kimi|kimi|kimi-code/k3-256k|commit,pr|pane-run|kimi'

usage() {
  # Usage = header comment from line 2 up to (not including) the registry
  # section, bounded by pattern so header edits can't truncate it.
  awk 'NR < 2 {next} /^# ----------/ {exit} {sub(/^# ?/, ""); print}' "$0" >&2
  exit 2
}

# ---------- registry access ----------
# Emit one `flag|cli|model|supports|herdr_mode|herdr_kind` record per line.
registry_rows() { printf '%s\n' "$REGISTRY"; }

# find_row <how> <want> — the ONE reader over the registry table. `how` picks what
# `want` is matched against: `name` (canonical cli:model), `flag` (shorthand, `-`
# rows skipped), or `cli` (first row of that CLI = its default model). Prints the
# matched record with a FIXED field count — so a row that were ever short a
# trailing field cannot shift the transport metadata into `supports` — or fails (1).
#
# Deliberately one function, not three near-identical loops: the `read -r` field
# list appears EXACTLY once, so adding a registry column can't be applied to two
# readers and forgotten in the third (which would make a bare `--agent codex`
# resolve a mangled row while `--codex` still worked — a bug that reads as
# agent-specific when it is really reader-specific).
find_row() {
  local how="$1" want="$2" flag cli model supports mode kind
  while IFS='|' read -r flag cli model supports mode kind; do
    [ -n "$cli" ] || continue
    case "$how" in
      name) [ "$cli:$model" = "$want" ] || continue ;;
      flag) [ "$flag" = "-" ] && continue; [ "$flag" = "$want" ] || continue ;;
      cli)  [ "$cli" = "$want" ] || continue ;;
      *)    return 1 ;;
    esac
    printf '%s|%s|%s|%s|%s|%s\n' "$flag" "$cli" "$model" "$supports" "$mode" "$kind"
    return 0
  done < <(registry_rows)
  return 1
}

row_for_name()        { find_row name "$1"; }
row_for_flag()        { find_row flag "$1"; }
row_for_cli_default() { find_row cli  "$1"; }

# Resolve any selector to a registry record. Order: shorthand flag, canonical
# cli:model name, bare CLI (its default model). Prints the record or fails (1).
row_for_selector() {
  local sel="$1"
  case "$sel" in
    --*) row_for_flag "$sel" && return 0 ;;
    *:*) row_for_name "$sel" && return 0 ;;
    *)   row_for_cli_default "$sel" && return 0 ;;
  esac
  return 1
}

# ---------- availability probes (work-system-owned; no swarm dependency) ----------
# `entry_status <cli> <model>` echoes `<avail>\t<note>` (avail = yes|no) for a
# specific CLI×model. Kept plain (no associative arrays) so the script runs on
# stock bash 3.2 too. Availability is install + auth, and — where the CLI can
# enumerate its own models — *model-level* too:
#   claude: available in-session; model aliases (fable/opus/sonnet) aren't
#           CLI-listable, so they're taken on trust (they're stable).
#   codex:  install + `codex login status`. No clean CLI model-list command, so
#           the model is trusted (both shipped codex models are stable).
#   grok:   install + auth + the model must appear in `grok models` — the CLI
#           rejects an unlisted `-m` id at launch ("unknown model id"), so a
#           per-CLI auth check alone would mislabel a model the CLI no longer
#           offers (grok drops/renames models between releases) as available.
#   kimi:   install + auth file + the model must appear in `kimi provider list
#           --json`. Model-aware for the same reason as grok, and the failure is
#           even sharper: an unconfigured `-m` id aborts at startup
#           ("Model ... is not configured in config.toml"), so a bad model would
#           give the user a worker tab that dies on sight. The model id must be
#           the QUALIFIED alias (`kimi-code/k3-256k`) — the bare model name is
#           rejected the same way. `kimi doctor` is NOT an auth check (it only
#           validates config file syntax), hence the credentials-file probe.
#           The listing is local config (~0.7s, no network), but it is bounded
#           anyway: a catalog refresh on start can make it reach out. The match
#           requires the alias in KEY position (`"<model>":`) — the real document
#           is flat-qualified (`"models": {"kimi-code/k3-256k": {…}}`, verified
#           live), so a bare substring would also fire on the alias appearing as
#           a VALUE (a `default_model`/metadata field) while `models` is empty.

# `run_bounded <seconds> <cmd...>` — the shared time bound for every probe below,
# so an external CLI can never hang `list`/the picker. Defined once in
# lib-bounded.sh (herdr-launch.sh sources the same file): two private copies had
# already drifted apart, and a sibling-script dependency is the norm here anyway.
_LIB_BOUNDED="$SCRIPT_DIR/lib-bounded.sh"
# shellcheck source=lib-bounded.sh
. "$_LIB_BOUNDED" 2>/dev/null || {
  echo "agent-registry.sh: cannot source $_LIB_BOUNDED (it ships alongside this script)" >&2
  exit 1
}

# Print `grok models` RAW output on stdout; RETURN 0 iff the fetch succeeded,
# non-zero if it was unreachable/timed out. entry_status substring-matches the
# model id against this raw text (not a positional field) so a reformatted
# listing — a moved/renamed column, a dropped `*` bullet — doesn't yield a wrong
# token and a false "model not offered". Status travels via the exit code, NOT a
# global: entry_status runs this in a command substitution (its own subshell), so
# a global flag would never propagate back. Bounded via run_bounded (never hangs).
grok_models_raw() {
  run_bounded 10 grok models 2>/dev/null
}

# Same contract as grok_models_raw, for kimi: RAW `kimi provider list --json` on
# stdout, exit code = fetch status. entry_status substring-matches the qualified
# model alias against the raw JSON rather than parsing it — no jq/python
# dependency, and a reshaped config document can't yield a wrong token.
kimi_models_raw() {
  run_bounded 10 kimi provider list --json 2>/dev/null
}

entry_status() {
  local cli="$1" model="$2" avail=no note=""
  case "$cli" in
    claude)
      # In-session claude is available by definition; PATH only sharpens the note.
      avail=yes
      command -v claude >/dev/null 2>&1 || note="in-session"
      ;;
    codex)
      # Bounded like grok: `codex login status` can touch the network, so an
      # unbounded call could hang `list`/the picker. A run_bounded TIMEOUT (124)
      # is inconclusive → trust install and assume available (mirrors grok), NOT a
      # genuine auth failure — a slow probe must not tell a logged-in user to
      # re-login and disable the backend.
      local crc=0
      if ! command -v codex >/dev/null 2>&1; then note="not installed"
      else
        run_bounded 10 codex login status >/dev/null 2>&1 || crc=$?
        if [ "$crc" -eq 0 ]; then avail=yes
        elif [ "$crc" -eq 124 ]; then avail=yes; note="codex login status timed out — availability assumed"
        else note="run: codex login"; fi
      fi
      ;;
    grok)
      if ! command -v grok >/dev/null 2>&1; then note="not installed"
      elif [ ! -s "$GROK_AUTH_FILE" ]; then note="run: grok login"
      else
        local _raw grc=0
        _raw="$(grok_models_raw)" || grc=$?   # exit code = fetch status
        if [ "$grc" -ne 0 ]; then
          # unreachable/timed out — inconclusive, not a drop. Trust auth so a
          # network hiccup doesn't wrongly block launch.
          avail=yes; note="grok models unreachable — availability assumed"
        elif [ -z "$_raw" ]; then
          # succeeded but produced nothing — inconclusive too, not "model gone".
          avail=yes; note="grok models empty — availability assumed"
        elif grep -qF -- "$model" <<<"$_raw"; then avail=yes   # substring, drift-tolerant
        else note="model not offered by this grok CLI (see: grok models)"; fi
      fi
      ;;
    kimi)
      if ! command -v kimi >/dev/null 2>&1; then note="not installed"
      elif [ ! -s "$KIMI_CREDENTIALS_FILE" ]; then note="run: kimi login"
      else
        local _kraw krc=0
        _kraw="$(kimi_models_raw)" || krc=$?   # exit code = fetch status
        if [ "$krc" -ne 0 ]; then
          # unreachable/timed out — inconclusive, trust auth (mirrors grok).
          avail=yes; note="kimi provider list unreachable — availability assumed"
        elif ! grep -qF -- '"models"' <<<"$_kraw"; then
          # Empty output fails this grep too, so it lands here — no separate `-z`
          # arm (unlike grok's, whose empty case carries its own note).
          # A document without the `models` section we key off. Unlike
          # grok's plain-text listing, a JSON reply is only self-describing while
          # the schema holds: `{"models": {}}` IS a real "no models" answer, but a
          # renamed/moved section is drift and must not read as one. Gate on the
          # section's presence, so only the former reaches the match below.
          avail=yes; note="kimi provider list unrecognized — availability assumed"
        elif grep -qF -- "\"$model\":" <<<"$_kraw"; then avail=yes
        else note="model not offered by this kimi CLI (see: kimi provider list)"; fi
      fi
      ;;
    *) note="unknown cli" ;;
  esac
  printf '%s\t%s\n' "$avail" "$note"
}

# ---------- subcommands ----------

# POSIX single-quote one word for `argv_shell=`. Not `printf %q`: bash 3.2
# renders that as per-character backslash escapes (and `$'…'` for anything
# non-ASCII) — correct, but the result is a wall of backslashes that a user
# cannot read before pasting it, and `$'…'` is bash/zsh-only. Words made only of
# safe characters are passed through bare; everything else is wrapped, with any
# embedded quote closed-escaped-reopened ('\'') the way every POSIX shell parses.
shell_quote() {
  case "$1" in
    ''|*[!A-Za-z0-9_@%+=:,./-]*)
      printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")" ;;
    *) printf '%s' "$1" ;;
  esac
}

emit_argv() {
  # Print `argv=<word>` lines for a resolved entry, then ONE `argv_shell=` line
  # with the same words shell-quoted. $1=cli $2=model $3=session.
  local cli="$1" model="$2" session="$3"
  local words=()
  case "$cli" in
    claude)
      words=(claude --model "$model")
      [ -n "$session" ] && words+=(-n "$session")
      # Plugin-qualified: a CC built-in/alias `/continue` shadows the skill, so
      # the bare form would run CC's own resume, not the work-system flow.
      words+=(/work-system:continue)
      ;;
    codex) words=(codex -m "$model" "$BOOTSTRAP_PROMPT") ;;
    grok)  words=(grok  -m "$model" "$BOOTSTRAP_PROMPT") ;;
    kimi)
      # Two-phase seed+continue (see the launch-shape note in the header). The
      # model and the prompt are passed as "$1"/"$2" positionals — NOT spliced
      # into the script text — so no amount of prompt content can reorder the
      # flags or be absorbed by `-p`.
      words=(sh -c "$KIMI_LAUNCH_SCRIPT" kimi-worker "$model" "$BOOTSTRAP_PROMPT")
      ;;
  esac
  # Guard the expansion: under `set -u` a bash 3.2 `"${words[@]}"` on an EMPTY
  # array is an unbound-variable error, which an unknown cli would hit.
  [ "${#words[@]}" -gt 0 ] || return 0
  printf 'argv=%s\n' "${words[@]}"
  # A ready-to-paste command line, quoted by printf %q rather than by whoever
  # renders the manual-launch block. That rendering used to be a prose rule, and
  # for kimi a mis-quote is not cosmetic: its argv carries `;` and `exec`, so an
  # unquoted paste would replace the USER'S OWN interactive shell with an
  # unattended agent. Skills print this verbatim instead of re-deriving it.
  local shell_cmd="" w
  for w in "${words[@]}"; do shell_cmd="$shell_cmd$(shell_quote "$w") "; done
  printf 'argv_shell=%s\n' "${shell_cmd% }"
}

subcmd_resolve() {
  local selector="" session=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --session) [ $# -ge 2 ] || { echo "Missing value for --session" >&2; exit 2; }
                 # The session is emitted as an `argv=<session>` line that the
                 # launch helper re-parses line by line — a newline in it would
                 # forge extra argv tokens. Reject control chars at the source so
                 # a crafted task name/label can't inject a worker flag.
                 case "$2" in
                   *[[:cntrl:]]*) echo "resolve: --session must not contain control characters" >&2; exit 2 ;;
                 esac
                 session="$2"; shift 2 ;;
      --*) if [ -z "$selector" ]; then selector="$1"; shift
           else echo "Unexpected argument: $1" >&2; exit 2; fi ;;
      *)   if [ -z "$selector" ]; then selector="$1"; shift
           else echo "Unexpected argument: $1" >&2; exit 2; fi ;;
    esac
  done
  [ -n "$selector" ] || { echo "resolve: missing selector" >&2; exit 2; }

  local record
  record="$(row_for_selector "$selector")" || {
    echo "Unknown agent selector: $selector" >&2
    # Derive the flag list from REGISTRY rather than restating it — a new entry
    # must not need a second edit here to appear in the hint.
    echo "Try: $(registry_rows | cut -d'|' -f1 | grep -v '^-$' | tr '\n' ' ')— a name (claude:opus), or a cli (codex)" >&2
    exit 2
  }
  local flag cli model supports mode kind
  IFS='|' read -r flag cli model supports mode kind <<<"$record"

  local avail note
  IFS=$'\t' read -r avail note < <(entry_status "$cli" "$model")

  printf 'name=%s\n' "$cli:$model"
  printf 'cli=%s\n' "$cli"
  printf 'model=%s\n' "$model"
  printf 'available=%s\n' "$avail"
  printf 'supports=%s\n' "$supports"
  # Modern-herdr transport (see the header). Always emitted — a consumer that
  # cannot interpret the mode must fail closed rather than guess from the name.
  printf 'herdr_mode=%s\n' "$mode"
  printf 'herdr_kind=%s\n' "$kind"
  [ -n "$note" ] && printf 'note=%s\n' "$note"
  emit_argv "$cli" "$model" "$session"

  [ "$avail" = yes ] || exit 3
}

subcmd_list() {
  local as_json=""
  case "${1:-}" in
    --json) as_json=1 ;;
    "") ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac

  # Build rows: name cli model available note (TAB-separated internally).
  local rows="" flag cli model supports mode kind avail note
  while IFS='|' read -r flag cli model supports mode kind; do
    [ -n "$cli" ] || continue
    IFS=$'\t' read -r avail note < <(entry_status "$cli" "$model")
    rows+="$cli:$model	$cli	$model	$avail	$note"$'\n'
  done < <(registry_rows)

  if [ -n "$as_json" ]; then
    command -v python3 >/dev/null 2>&1 || { echo "python3 required for --json" >&2; exit 1; }
    printf '%s' "$rows" | python3 -c '
import json, sys
out = []
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    name, cli, model, avail, note = (line.split("\t") + [""] * 5)[:5]
    out.append({"name": name, "cli": cli, "model": model,
                "available": avail == "yes", "note": note})
json.dump(out, sys.stdout, indent=2)
print()
'
    return
  fi

  # Human table. Use column when present; else a plain TSV still renders.
  { printf 'NAME\tCLI\tMODEL\tAVAILABLE\tNOTE\n'
    printf '%s' "$rows" | while IFS=$'\t' read -r name cli model avail note; do
      printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$cli" "$model" "$avail" "${note:--}"
    done
  } | { command -v column >/dev/null 2>&1 && column -t -s $'\t' || cat; }
}

# Read/write one `key=value` line in a state file, preserving other keys.
_kv_get() {
  local file="$1" key="$2"
  [ -n "$file" ] && [ -f "$file" ] || return 0
  sed -n "s/^$key=//p" "$file" | tail -1
}
_kv_set() {
  local file="$1" key="$2" value="$3" tmp
  mkdir -p "$(dirname "$file")"
  tmp="$(mktemp)"
  [ -f "$file" ] && grep -v "^$key=" "$file" >"$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  mv "$tmp" "$file"
}

validate_name() {
  # A stored default/last must map to a real entry, so state can't rot.
  row_for_name "$1" >/dev/null || {
    echo "unknown agent name '$1' (see \`list\`)" >&2; exit 2; }
}

subcmd_default() {
  # default get         → the repo's default agent name, or empty if none set
  #                       (or if the stored name no longer maps to a real entry)
  # default set <name>  → persist it in the project state file
  local op="${1:-get}"; shift || true
  case "$op" in
    get)
      # VALIDATE the stored value against the live registry before handing it to
      # no-flag /kickoff. The project file is committed and travels with a clone,
      # so a stale/removed/garbage (or attacker-supplied) name must NOT route the
      # launch: an unknown name is treated as "no default" → the caller shows the
      # picker, rather than failing every kickoff on a bogus committed value.
      local v; v="$(_kv_get "$PROJECT_STATE" default)"
      if [ -n "$v" ] && row_for_name "$v" >/dev/null 2>&1; then printf '%s\n' "$v"; fi
      ;;
    set)
      local name="${1:-}"
      [ -n "$name" ] || { echo "default set: missing <name>" >&2; exit 2; }
      validate_name "$name"
      [ -n "$PROJECT_STATE" ] || { echo "default set: no project config location (not inside a git repo)" >&2; exit 2; }
      _kv_set "$PROJECT_STATE" default "$name"
      ;;
    *) echo "default: expected get|set" >&2; exit 2 ;;
  esac
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    list)     subcmd_list "$@" ;;
    resolve)  subcmd_resolve "$@" ;;
    default)  subcmd_default "$@" ;;
    -h|--help) usage ;;
    "")       usage ;;
    *)        echo "Unknown subcommand: $cmd" >&2; usage ;;
  esac
}

main "$@"
