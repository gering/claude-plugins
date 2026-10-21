#!/usr/bin/env bash
# herdr-agent.sh — the ONE wrapper over the herdr *agent* primitives, plus the
# ONE realpath cwd↔worktree classification the lane registry (lanes.sh) and the
# tab-glyph refresh (herdr-tab-glyph.sh) share.
#
# Two ways to use it:
#
#   1. As a CLI — thin, guarded wrappers over `herdr agent …`:
#        herdr-agent.sh list                       (validated agent-list JSON)
#        herdr-agent.sh get  <target>              (validated single-agent JSON)
#        herdr-agent.sh read <target> [flags…]     (best-effort text, never a schema)
#        herdr-agent.sh wait <target> [--until S] [--timeout MS]  (bounded)
#          S = idle|working|blocked|done|unknown (repeatable). Flag names are
#          herdr-version-coupled (verified 0.8: --until S, not --status and not
#          --until=S). Leftover --status and --until=S / dangling --until are
#          rejected (exit 2), never translated. No version probe — a rename
#          fails closed at the first real caller.
#      Every wrapper DEGRADES rather than blocks: a missing herdr/python3, an
#      unreachable server, or malformed JSON is a non-zero exit with empty
#      stdout — never a hang, never a partial/garbage line. Exit codes below.
#
#   2. As a sourced library — `. herdr-agent.sh` pulls in, with NO side effects:
#        $HERDR_MATCH_PRELUDE   a python3 source string defining match_roots()
#                               and classify_cwd() — prepend it to a consumer
#                               python snippet so the cwd match logic is reused,
#                               never copied (the "realpath cwd match prelude").
#        $HERDR_NAME_PRELUDE    a python3 source string defining session_name():
#                               the sanitized SendMessage name of an agent row.
#        $HERDR_COMPOSER_PY     a python3 program: ansi pane text on stdin →
#                               clear|draft|unknown for the claude composer.
#        ha_have / ha_list / ha_get / ha_read / ha_wait   the shell wrappers.
#      Sourcing must stay side-effect free (the CLI dispatch at the foot is
#      guarded by "am I the executed script?") so a consumer can take the
#      prelude without running a subcommand.
#
# Exit codes (CLI + ha_* functions):
#   0  success
#   2  usage — missing/invalid <target>, or a leftover --status on wait
#   3  herdr and/or python3 not on PATH        (degrade — tools absent)
#   4  herdr call failed / empty output         (degrade — server unreachable)
#   5  output was not the expected JSON shape   (degrade — malformed)
# read/wait forward herdr's own non-zero (e.g. wait timeout) unchanged.
#
# `set -u` is enabled ONLY on the executed-CLI path (foot of the file), never at
# source time: sourcing for $HERDR_MATCH_PRELUDE / the ha_* helpers must not
# mutate the caller's shell options (the side-effect-free contract). The wrappers
# guard their own expansions with ${x:-}, so they are correct either way.

# ---- shared realpath cwd↔worktree match (the "prelude") ---------------------
# A python3 source string, apostrophe-free so it stays single-quotable. Both
# consumers prepend it to their own snippet:
#     python3 -c "$HERDR_MATCH_PRELUDE"$'\n'"$their_snippet" …
# so the exact-match philosophy (mirrors herdr-teardown.sh: an agent merely cd'd
# into a SUBDIR of a worktree/root is neither) lives in exactly one place.
HERDR_MATCH_PRELUDE='import os
def match_roots(main):
    # Realpath the repo root and its worktrees dir once. Empty main -> (None, None)
    # so a caller can bail before touching agents. realpath the whole worktrees
    # path (not just root): a symlinked .claude/worktrees would otherwise never
    # match, since the cwd side below is resolved.
    if not main or not main.strip():
        return None, None
    root = os.path.realpath(main)
    wtdir = os.path.realpath(os.path.join(root, ".claude", "worktrees"))
    return root, wtdir

def classify_cwd(cwd, root, wtdir):
    # Exact realpath match. Returns a 3-tuple (kind, key, resolved):
    #   ("main", <repo dir name>, <root>)         cwd IS the main repo root
    #   ("task", <worktree dir name>, <worktree>) cwd IS a direct child of worktrees
    #   (None, None, None)                         neither (incl. a mere subdir)
    # `resolved` is the realpath of cwd — returned so a caller keying by full path
    # (lanes.sh) reuses this resolution instead of computing realpath a second time.
    # cwd is realpath-resolved here; root/wtdir come pre-resolved from match_roots.
    cwd = (cwd or "").rstrip("/")
    if not cwd or root is None:
        return None, None, None
    cwd = os.path.realpath(cwd)
    if cwd == root:
        return "main", os.path.basename(root), cwd
    if os.path.dirname(cwd) == wtdir:
        return "task", os.path.basename(cwd), cwd
    return None, None, None'

# ---- shared session-name derivation (the "name prelude") --------------------
# A python3 source string defining session_name(agent_row): the CC SendMessage
# address of a herdr agent row, used by manager.sh resolve. Kept here, beside
# the match prelude, so the sanitizing lives in exactly one place. Apostrophe-
# free like the match prelude. Needs `import re, unicodedata` (included).
HERDR_NAME_PRELUDE='import re, unicodedata
def session_name(a):
    # The SendMessage address is the CLAUDE SESSION name, which Claude writes into
    # the terminal title -- NOT the herdr agent name (verified live: a herdr agent
    # named gcp-auth-159 hosts the session "answer-gcp-auth-questions-buchhalter-159").
    # NO fallback to the herdr agent `name`: it is a launch label, so emitting it
    # would hand the caller a string that is not an address. No title -> "".
    t = str(a.get("terminal_title_stripped") or a.get("terminal_title") or "")
    # Blank EVERY control/format char first (Cc/Cf, plus surrogates/private use):
    # newlines would forge extra output lines, and ANSI escapes or a bidi override
    # could make the printed name read differently than it matches.
    t = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co") else c for c in t)
    # Drop ONE leading symbol+space (the herdr/claude spinner glyph) and collapse.
    # The space is required, so a punctuation-led name (/habemus-event) survives.
    t = re.sub(r"^[^\w\s]\s+", "", t.strip())
    t = re.sub(r"\s+", " ", t).strip()
    # 64 chars: the value is echoed into prompts before any gate runs, and a pane
    # title is settable by any process in that pane -- prose there is an injection
    # surface, not an address.
    return "" if len(t) > 64 else t'

# ---- shared composer draft check (the "composer prelude") -----------------
# A python3 program string, run as `python3 -I -c "$HERDR_COMPOSER_PY"` (-I: the
# caller's cwd stays off sys.path, so a planted re.py cannot fake "clear"). Used by
# manager.sh prompt and sessions.sh refresh before typing into a claude pane:
# only a composer that reads `clear` is typed into.
# stdin: `herdr agent read --source visible --format ansi`. Prints clear|draft|unknown.
# The CC composer is the region between the LAST two horizontal rules. A user
# draft is visible text rendered in a normal style; a prompt suggestion is dim/
# mid-gray and does not count. The cursor is inverse: over the first char of a
# suggestion it is part of the suggestion, over typed text it is a draft, and
# over the empty composer it is a space.
HERDR_COMPOSER_PY='import sys, re
raw = sys.stdin.read()
lines = raw.replace("\r", "").split("\n")
plain = [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", l) for l in lines]
# A rule may carry a label (CC prints the session name into the upper one).
rules = [i for i, p in enumerate(plain) if p.strip().startswith("─" * 10)]
if len(rules) < 2:
    print("unknown"); sys.exit(0)
i, j = rules[-2], rules[-1]
if j - i < 2:
    print("unknown"); sys.exit(0)

def apply_sgr(st, nums):
    # A real SGR state machine: off-codes (22/27/39) remove what they turn off,
    # a new fg replaces the old one, and the sub-parameters of 38/48 (5;n and
    # 2;r;g;b) are consumed, never read as codes of their own (38;5;2 is green,
    # not dim; 38;2;7;.. is a color, not inverse).
    k = 0
    while k < len(nums):
        n = nums[k]
        if n == 0:
            st.update(dim=False, inverse=False, fg=None)
        elif n == 2:
            st["dim"] = True
        elif n == 22:
            st["dim"] = False
        elif n == 7:
            st["inverse"] = True
        elif n == 27:
            st["inverse"] = False
        elif 30 <= n <= 37 or 90 <= n <= 97:
            st["fg"] = ("basic", n)
        elif n == 39:
            st["fg"] = None
        elif n in (38, 48) and k + 1 < len(nums):
            mode = nums[k + 1]
            width = 3 if mode == 5 else 5 if mode == 2 else 2
            if n == 38 and mode == 5 and k + 2 < len(nums):
                st["fg"] = ("idx", nums[k + 2])
            elif n == 38 and mode == 2 and k + 4 < len(nums):
                st["fg"] = ("rgb",) + tuple(nums[k + 2:k + 5])
            k += width
            continue
        k += 1

def muted(st):
    # How CC renders suggestions and hints: dim, bright-black, or a mid gray
    # (24-bit near-neutral 90..180, or the 256-color ramp steps 241..249).
    if st["dim"]:
        return True
    fg = st["fg"]
    if not fg:
        return False
    # Mid grays only: near-black is the normal text color of a light theme and
    # must read as typed text, never as a suggestion. One rule for both
    # encodings: a 256-color grayscale-ramp step is mapped to its xterm RGB
    # value (232+n = 8+10n) and judged like a 24-bit color. Color-cube grays
    # (59/102/145/188) are not mapped and read as typed text (toward draft).
    if fg[0] == "basic":
        return fg[1] == 90
    if fg[0] == "idx":
        if fg[1] == 8:
            return True
        if not 232 <= fg[1] <= 255:
            return False
        v = 8 + 10 * (fg[1] - 232)
        r = g = b = v
    else:
        r, g, b = fg[1:]
    return max(r, g, b) - min(r, g, b) <= 16 and 90 <= max(r, g, b) <= 180

draft = False
# SGR state carries across lines (a wrapped suggestion keeps its style).
st = {"dim": False, "inverse": False, "fg": None}
for line in lines[i + 1:j]:
    first, cursor = True, False
    for tok in re.split(r"(\x1b\[[0-9;?]*[A-Za-z])", line):
        if tok.startswith("\x1b["):
            if tok.endswith("m"):
                apply_sgr(st, [int(x) for x in re.findall(r"\d+", tok)] or [0])
            continue
        m = muted(st)
        for ch in tok:
            # A glyph under the inverse cursor is a suggestion first char only
            # when the very NEXT cell is a muted glyph; a blank (whatever its
            # style) or normal text there makes it typed. An empty composer has
            # an inverse SPACE, which never sets the cursor.
            if cursor:
                cursor = False
                if ch.isspace() or not m:
                    draft = True
            if ch.isspace():
                continue
            if first and ch in "\u276f>\u203a":
                first = False
                continue
            first = False
            if m:
                continue
            if st["inverse"]:
                cursor = True
            else:
                draft = True
    if cursor:
        draft = True
# Paste and image placeholders are user content whatever their style.
plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", "\n".join(lines[i + 1:j]))
if re.search(r"\[(Pasted text|Image) #[0-9]", plain):
    draft = True
print("draft" if draft else "clear")'

# Default bound for `wait` when the caller passes none — a wrapper must never
# wait unboundedly (that would be the busy loop this script exists to avoid).
HA_WAIT_DEFAULT_TIMEOUT_MS="${HA_WAIT_DEFAULT_TIMEOUT_MS:-10000}"

# Wall-clock bound for the one-shot list/get/read calls, so a herdr server that
# accepts a request but never answers can't hang a survey (the header's "never a
# hang" promise). `wait` is excluded — the caller's --timeout already bounds it.
HA_CALL_TIMEOUT_SECS="${HA_CALL_TIMEOUT_SECS:-10}"

# ---- primitive wrappers -----------------------------------------------------

# Both tools present? (herdr for the call, python3 for the JSON validate.)
ha_have() {
  command -v herdr   >/dev/null 2>&1 || return 1
  command -v python3 >/dev/null 2>&1 || return 1
}

# A usable <target>: non-empty and not a leading-dash value (which `herdr agent`
# would parse as an option flag, so an untrusted id can't smuggle a flag). Fails
# with 2 (usage). Shared by get/read/wait so the guard lives in exactly one place.
_ha_check_target() {
  [ -n "${1:-}" ] || return 2
  case "$1" in -*) return 2 ;; esac
}

# Run "$@" under a $1-second wall bound. `timeout` isn't on stock macOS; fall back
# to gtimeout, then perl's alarm (ships with macOS; the timer survives exec).
# A host with none runs unbounded — accepted residual, same tradeoff as
# ws-statusline.sh's run_bounded. A timeout kills the child → non-zero exit,
# which the callers already map to their degrade code.
_ha_bounded() {
  local secs="$1"; shift
  if   command -v timeout  >/dev/null 2>&1; then timeout  "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout "$secs" "$@"
  elif command -v perl     >/dev/null 2>&1; then perl -e 'alarm shift; exec @ARGV' "$secs" "$@"
  else "$@"
  fi
}

# Validate that stdin is JSON whose result.<key> is a list; exit 0/1. Used to
# reject a malformed/blank blob before a consumer trusts it.
_ha_validate_list() {
  python3 -c 'import sys, json
key = sys.argv[1]
try:
    v = json.load(sys.stdin)["result"][key]
except Exception:
    sys.exit(1)
sys.exit(0 if isinstance(v, list) else 1)' "$1" 2>/dev/null
}

# `herdr agent list` → validated JSON on stdout (result.agents is a list).
ha_list() {
  ha_have || return 3
  local json
  json="$(_ha_bounded "$HA_CALL_TIMEOUT_SECS" herdr agent list 2>/dev/null)" || return 4
  [ -n "$json" ] || return 4
  printf '%s' "$json" | _ha_validate_list agents || return 5
  printf '%s\n' "$json"
}

# `herdr agent get <target>` → validated JSON on stdout (has a result object).
ha_get() {
  ha_have || return 3
  local target="${1:-}"
  _ha_check_target "$target" || return 2
  local json
  json="$(_ha_bounded "$HA_CALL_TIMEOUT_SECS" herdr agent get "$target" 2>/dev/null)" || return 4
  [ -n "$json" ] || return 4
  # Explicit if/exit, NOT assert: `python3 -O` / PYTHONOPTIMIZE strips asserts, so
  # an assert would silently pass a malformed body through in an optimized runtime.
  printf '%s' "$json" | python3 -c 'import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if isinstance(d.get("result"), dict) else 1)' 2>/dev/null || return 5
  printf '%s\n' "$json"
}

# `herdr agent read <target> [flags…]` — best-effort, per-agent, NEVER format-
# required: whatever herdr prints is forwarded verbatim and herdr's own rc is
# returned. Only the tools-absent guard degrades to code 3. No JSON validation
# by design (read is free-form pane text, not a schema).
ha_read() {
  ha_have || return 3
  local target="${1:-}"
  _ha_check_target "$target" || return 2
  shift
  _ha_bounded "$HA_CALL_TIMEOUT_SECS" herdr agent read "$target" "$@"
}

# `herdr agent wait <target> [--until S] [--timeout MS]` — BOUNDED: if the
# caller omits --timeout, inject the default so the call can never block
# forever. herdr blocks server-side until a matched status is reached or the
# timeout elapses (no busy poll here); its rc is forwarded (0 reached,
# non-zero timeout/error).
#
# --until S is repeatable; valid S: idle|working|blocked|done|unknown.
# herdr 0.8 accepts `--until S` only (NOT `--until=S` — unknown option), so
# equals-form is rejected, never forwarded and never treated as "present".
# has_until flips only after a real STATUS is consumed: a dangling `--until`
# (last, or followed by another flag) is exit 2, otherwise the injected
# `--timeout` would be parsed as STATUS. When the caller passes no --until,
# inject herdr 0.8's documented default (idle, done, blocked) so the match
# set is explicit rather than inherited. A leftover --status / --status=*
# is REJECTED (exit 2) in flag AND value position — never rewritten, never
# swallowed as the value of --until/--timeout. Flag vocabulary is
# herdr-version-coupled; there is no version probe.
ha_wait() {
  ha_have || return 3
  local target="${1:-}"
  _ha_check_target "$target" || return 2
  shift
  # Detect BOTH `--timeout MS` and `--timeout=MS`, so a caller's explicit bound in
  # either form is honoured (no duplicate appended), and capture its value to size
  # the client wall-bound below. `--until` is space-separated only (herdr 0.8).
  local a has_timeout=0 next=0 eff_ms="$HA_WAIT_DEFAULT_TIMEOUT_MS"
  local has_until=0 until_next=0
  for a in "$@"; do
    if [ "$next" = 1 ]; then
      case "$a" in
        --status|--status=*)
          echo "ha_wait: --status is not a herdr flag; use --until <idle|working|blocked|done|unknown> (repeatable)" >&2
          return 2
          ;;
      esac
      eff_ms="$a"; next=0; continue
    fi
    if [ "$until_next" = 1 ]; then
      case "$a" in
        --status|--status=*)
          echo "ha_wait: --status is not a herdr flag; use --until <idle|working|blocked|done|unknown> (repeatable)" >&2
          return 2
          ;;
        -*)
          echo "ha_wait: --until requires a status <idle|working|blocked|done|unknown>" >&2
          return 2
          ;;
      esac
      has_until=1
      until_next=0
      continue
    fi
    case "$a" in
      --timeout)           has_timeout=1; next=1 ;;
      --timeout=*)         has_timeout=1; eff_ms="${a#--timeout=}" ;;
      --until)             until_next=1 ;;
      --until=*)
        echo "ha_wait: --until=S is not a herdr flag; use --until <idle|working|blocked|done|unknown> (repeatable)" >&2
        return 2
        ;;
      --status|--status=*)
        echo "ha_wait: --status is not a herdr flag; use --until <idle|working|blocked|done|unknown> (repeatable)" >&2
        return 2
        ;;
    esac
  done
  if [ "$until_next" = 1 ]; then
    echo "ha_wait: --until requires a status <idle|working|blocked|done|unknown>" >&2
    return 2
  fi
  if [ "$has_timeout" -eq 0 ]; then
    set -- "$@" --timeout "$HA_WAIT_DEFAULT_TIMEOUT_MS"
    eff_ms="$HA_WAIT_DEFAULT_TIMEOUT_MS"
  fi
  if [ "$has_until" -eq 0 ]; then
    set -- "$@" --until idle --until done --until blocked
  fi
  # Client wall-bound sized ABOVE the server --timeout (+5s margin), so it only
  # fires if the server wedges and ignores its own timeout — the "never a hang"
  # contract holds for wait too, without cutting a legitimately long wait short.
  case "$eff_ms" in ''|*[!0-9]*) eff_ms="$HA_WAIT_DEFAULT_TIMEOUT_MS" ;; esac
  _ha_bounded "$(( eff_ms / 1000 + 5 ))" herdr agent wait "$target" "$@"
}

# ---- CLI dispatch (only when executed directly, never when sourced) ---------
ha_main() {
  local cmd="${1:-}"
  [ $# -gt 0 ] && shift
  case "$cmd" in
    list) ha_list ;;
    get)  ha_get  "$@" ;;
    read) ha_read "$@" ;;
    wait) ha_wait "$@" ;;
    *)
      echo "usage: ${0##*/} {list | get <target> | read <target> [flags…] | wait <target> [--until S] [--timeout MS]}" >&2
      return 2
      ;;
  esac
}

# BASH_SOURCE[0] == $0 only when this file is the executed program; when another
# script sources it for $HERDR_MATCH_PRELUDE / the ha_* helpers the two differ,
# so no subcommand runs.
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ] && [ -n "${BASH_SOURCE[0]:-}" ]; then
  set -u                 # CLI path only — never leaked into a sourcing shell
  ha_main "$@"
  exit $?
fi
