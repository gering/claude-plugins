#!/usr/bin/env bash
# manager.sh — find this project's Manager session once and reach it two ways.
#
# "Who is the Manager, and how do I message it?" answered deterministically, with
# BOTH addresses: the herdr pane/agent (any worker can `herdr agent prompt` it)
# and the CC SendMessage name (claude workers, queued + attributed). Consumers:
# /continue milestone pings and /close step 1b delegation. Discovery stays on the
# invoking herdr server; multi-server discovery and run records are out of scope
# (tasks/bind-close-delegation-recipient.md, add-manager-watch-loop).
#
# Identity = pane + canonical main-repo cwd + agent-session UUID (all survive a
# herdr live-handoff); the terminal id is volatile and never stored. Every
# answer is re-derived from a live `herdr agent list` — nothing is trusted
# from a previous run, so callers resolve immediately before each send.
#
# Subcommands:
#   record <worktree>
#       Run by /kickoff and /adopt in the KICKING session. Writes the kicker's
#       herdr workspace/pane/tab, agent-session UUID, agent kind, canonical main
#       repo and SendMessage name to <worktree>/.ws-kicker (key=value), and adds
#       it to the repo's git exclude (same mechanism as MANDATE.md). Refuses a
#       kicker that does not sit at the main-repo root. Prints recorded=yes|no
#       and reason=; best-effort, always exit 0 (never blocks a kickoff).
#   A <lane-dir> argument (default: cwd) is resolved to its git toplevel first,
#   so a call from a subdirectory reads the same record and identity.
#
#   resolve [<lane-dir>]
#       Prints status=unique|none|ambiguous|unverified, evidence=, herdr_pane=,
#       herdr_tab=, herdr_workspace=, herdr_agent_session=, agent=,
#       agent_status=, sendmessage_name= (claude targets only), candidates=,
#       plus one reason= line per observation. Precedence:
#         1. the kicker record, revalidated live (pane exists, cwd == main-repo
#            root, agent-session UUID unchanged, status live) → evidence=kicker;
#         2. otherwise the live agents whose cwd IS the main-repo root (the
#            shared classify_cwd, no second path classifier): one →
#            evidence=sole-root-agent; several → the leftmost tab of the
#            workspace wins as a STATED tie-break (evidence=leftmost-tab), and
#            two candidates in that one tab → ambiguous.
#       Anything unreadable near the decision → unverified, never unique.
#       The record lives in the worker-writable worktree: evidence=kicker is an
#       address the lane kept, never proof or authority.
#   body [<lane-dir>] -- [<text>]
#       Print the one-line, attributed message for <text>: sender (task,
#       worktree) first, "info only" last. Every route sends exactly this.
#       Without <text> after `--`, the text is read from stdin; use a quoted
#       heredoc (<<'EOF') so quotes and $(...) in the event never reach a shell.
#   prompt [<lane-dir>] -- [<text>]
#       The herdr route: resolve again, then `herdr agent prompt` the body to the
#       Manager pane ONLY when status=unique, the agent is claude, idle/done, and
#       its composer holds no user draft (a dim CC prompt suggestion is not a
#       draft). Prints sent=yes|no and reason=; always exit 0. Residual: the
#       composer can change between the read and the send (herdr has no atomic
#       check-and-prompt), so that window holds only the read and one call.
#
# Exit codes: 0 (answers are in the output), 1 body only (the lane has no main
# repo; nothing on stdout), 2 usage.
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=herdr-agent.sh
. "$SCRIPT_DIR/herdr-agent.sh"

KICKER_FILE=".ws-kicker"
# herdr's 0.8 vocabulary minus `unknown` (herdr cannot tell → not confirmed live).
LIVE_STATUSES="idle working blocked done"

usage() {
  echo "usage: ${0##*/} {record <worktree> | resolve [<lane>] | body [<lane>] -- <text> | prompt [<lane>] -- <text>}" >&2
  exit 2
}

# The lane's worktree toplevel (canonical), else the dir itself: a call from a
# subdirectory must read the same .ws-kicker and identity as one from the root.
lane_dir() {
  local d="$1" top
  top="$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)" || top=""
  [ -n "$top" ] || top="$d"
  python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$top" 2>/dev/null || printf '%s\n' "$d"
}

# Canonical main-repo root for a lane dir; empty on failure. Subshell cd only.
main_root() {
  local d="$1" m
  m="$( (cd "$d" 2>/dev/null && bash "$SCRIPT_DIR/main-repo-path.sh" path) 2>/dev/null)" || return 1
  [ -n "$m" ] || return 1
  python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$m"
}

# Append one pattern to the repo's git exclude unless git already ignores it.
# Mirrors mandate.sh exclude_one (0 already, 1 written, 2 could not write).
exclude_one() {
  local dir="$1" pat="$2" excl
  git -C "$dir" check-ignore -q -- "$pat" 2>/dev/null && return 0
  excl="$(git -C "$dir" rev-parse --git-path info/exclude 2>/dev/null)" || excl=""
  [ -n "$excl" ] || return 2
  case "$excl" in /*) ;; *) excl="$dir/$excl" ;; esac
  if [ -f "$excl" ] && grep -qxF -- "/$pat" "$excl" 2>/dev/null; then return 2; fi
  mkdir -p "${excl%/*}" 2>/dev/null || return 2
  printf '/%s\n' "$pat" >> "$excl" 2>/dev/null || return 2
  return 1
}

# ---- record -----------------------------------------------------------------
do_record() {
  local wt="${1:-}" root json rec tmp rc=0
  [ -n "$wt" ] || usage
  if [ ! -d "$wt" ]; then echo "recorded=no"; echo "reason=no-such-worktree"; return 0; fi
  if [ "${HERDR_ENV:-}" != "1" ] || [ -z "${HERDR_PANE_ID:-}" ]; then
    echo "recorded=no"; echo "reason=not-in-herdr"; return 0
  fi
  # A tracked record would be committed with the lane (exclude does not apply
  # to tracked paths): the same refusal as mandate.sh refuse_tracked.
  if git -C "$wt" ls-files --error-unmatch -- "$KICKER_FILE" >/dev/null 2>&1; then
    echo "recorded=no"; echo "reason=kicker-file-tracked"; return 0
  fi
  root="$(main_root "$wt")" || root=""
  if [ -z "$root" ]; then echo "recorded=no"; echo "reason=no-main-repo"; return 0; fi
  json="$(ha_get "$HERDR_PANE_ID")" || { echo "recorded=no"; echo "reason=herdr-unavailable"; return 0; }
  rec="$(printf '%s' "$json" | PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$HERDR_NAME_PRELUDE
$PY_RECORD" "$root" 2>/dev/null)" || rec=""
  case "$rec" in
    reason=*) echo "recorded=no"; printf '%s\n' "$rec"; return 0 ;;
    "")       echo "recorded=no"; echo "reason=malformed-agent"; return 0 ;;
  esac
  tmp="$(mktemp "$wt/.ws-kicker.XXXXXX" 2>/dev/null)" || { echo "recorded=no"; echo "reason=unwritable"; return 0; }
  if ! printf '%s\nrecorded_at=%s\n' "$rec" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp" \
     || ! mv -f "$tmp" "$wt/$KICKER_FILE"; then
    rm -f "$tmp"; echo "recorded=no"; echo "reason=unwritable"; return 0
  fi
  exclude_one "$wt" ".ws-kicker.*" || true
  exclude_one "$wt" "$KICKER_FILE" || rc=$?
  echo "recorded=yes"
  echo "file=$wt/$KICKER_FILE"
  case "$rc" in 0) echo "excluded=already" ;; 1) echo "excluded=yes" ;; *) echo "excluded=no" ;; esac
}

# stdin: `herdr agent get` JSON; argv[1]: canonical root. Prints the record body
# or a single reason= line.
PY_RECORD='import sys, json
ID = re.compile(r"^[A-Za-z0-9:_.-]{1,64}$")
UUID = re.compile(r"^[0-9a-fA-F-]{8,64}$")
root = sys.argv[1]
try:
    a = json.load(sys.stdin)["result"]
    if isinstance(a.get("agent"), dict) and "pane_id" not in a:
        a = a["agent"]
except Exception:
    print("reason=malformed-agent"); sys.exit(0)
r, w = match_roots(root)
kind, _, _ = classify_cwd(str(a.get("cwd") or ""), r, w)
if kind != "main":
    print("reason=kicker-not-at-repo-root"); sys.exit(0)
sess = a.get("agent_session") or {}
uuid = str(sess.get("value") or "") if isinstance(sess, dict) else ""
f = {"workspace": a.get("workspace_id"), "pane": a.get("pane_id"), "tab": a.get("tab_id"),
     "agent": a.get("agent")}
for k, v in f.items():
    if not isinstance(v, str) or not ID.match(v):
        print("reason=malformed-" + k); sys.exit(0)
if not UUID.match(uuid):
    print("reason=no-agent-session"); sys.exit(0)
name = session_name(a) if f["agent"] == "claude" else ""
print("version=1")
for k in ("workspace", "pane", "tab", "agent"):
    print(k + "=" + f[k])
print("agent_session=" + uuid)
print("main_repo=" + root)
print("sendmessage_name=" + name)'

# ---- resolve ----------------------------------------------------------------
# stdin: agent-list JSON. argv: root, record path, scope workspace, tabs file.
# env: WS_LIVE (space-separated live statuses). An empty tabs-file argument means
# the tab order was not fetched yet: a tie-break then prints only
# need_tabs=<workspace>, and the caller re-runs with `herdr tab list` in a file
# (fetched only when needed; a file, not argv/env, so a big server cannot E2BIG).
PY_RESOLVE='import sys, json, os
ID = re.compile(r"^[A-Za-z0-9:_.-]{1,64}$")
root_arg, rec_path, scope, tabs_file = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
LIVE = set(os.environ.get("WS_LIVE", "").split())
out, reasons = {}, []

def emit(status, ev="", a=None, n=0, name=None):
    print("status=" + status)
    print("evidence=" + ev)
    a = a or {}
    sess = a.get("agent_session") if isinstance(a.get("agent_session"), dict) else {}
    print("herdr_pane=" + str(a.get("pane_id") or ""))
    print("herdr_tab=" + str(a.get("tab_id") or ""))
    print("herdr_workspace=" + str(a.get("workspace_id") or ""))
    print("herdr_agent_session=" + str((sess or {}).get("value") or ""))
    print("agent=" + str(a.get("agent") or ""))
    print("agent_status=" + str(a.get("agent_status") or ""))
    if name is None:
        name = session_name(a) if a and a.get("agent") == "claude" else ""
    print("sendmessage_name=" + name)
    print("candidates=" + str(n))
    for r in reasons:
        print("reason=" + r)
    sys.exit(0)

root, wtdir = match_roots(root_arg)
try:
    agents = json.load(sys.stdin)["result"]["agents"]
    if not isinstance(agents, list):
        raise ValueError
except Exception:
    reasons.append("agent-list-malformed"); emit("unverified")
if root is None or not agents:
    # [] is an empty/repopulating list, never proof that nobody is there.
    reasons.append("agent-list-empty"); emit("unverified")

def session_id(a):
    s = a.get("agent_session")
    return str(s.get("value") or "") if isinstance(s, dict) else ""

# 1. kicker record, revalidated live.
rec = None
if os.path.isfile(rec_path) and not os.path.islink(rec_path):
    rec = {}
    try:
        with open(rec_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                k, sep, v = line.partition("=")
                if not sep or k in rec:
                    raise ValueError
                rec[k] = v
        if not (ID.match(rec.get("pane", "")) and rec.get("agent_session") and rec.get("main_repo")):
            raise ValueError
    except Exception:
        rec = None
        reasons.append("kicker-record-malformed")
elif os.path.lexists(rec_path):
    reasons.append("kicker-record-malformed")
else:
    reasons.append("kicker-record-absent")

if rec is not None:
    hit = [a for a in agents if isinstance(a, dict) and a.get("pane_id") == rec["pane"]]
    if os.path.realpath(rec["main_repo"]) != root:
        reasons.append("kicker-repo-mismatch")
    elif not hit:
        reasons.append("kicker-pane-gone")
    elif len(hit) > 1:
        reasons.append("kicker-pane-duplicated")
    else:
        a = hit[0]
        kind, _, _ = classify_cwd(str(a.get("cwd") or ""), root, wtdir)
        if session_id(a) != rec["agent_session"]:
            reasons.append("kicker-session-changed")
        elif kind != "main":
            reasons.append("kicker-cwd-moved")
        elif str(a.get("agent_status") or "").lower() not in LIVE:
            reasons.append("kicker-not-live")
        else:
            # The SendMessage name is the live title, which any process in the
            # pane can set. One that differs from the recorded name is not
            # offered as an address (the herdr pane itself stays verified).
            want = rec.get("sendmessage_name", "")
            if want and a.get("agent") == "claude" and session_name(a) != want:
                reasons.append("kicker-name-changed")
                emit("unique", "kicker", a, 1, name="")
            emit("unique", "kicker", a, 1)

# 2. live agents at the main-repo root.
found, unknown = [], False
for a in agents:
    if not isinstance(a, dict):
        unknown = True; continue
    # A row readably in ANOTHER workspace is out of scope whatever its cwd: an
    # unrelated agent still starting up elsewhere must not veto this lane.
    if scope and str(a.get("workspace_id") or "") not in ("", scope):
        continue
    cwd = a.get("cwd")
    if not cwd or not str(cwd).strip():
        unknown = True; continue
    kind, _, _ = classify_cwd(str(cwd), root, wtdir)
    if kind != "main":
        continue
    if scope:
        aws = str(a.get("workspace_id") or "")
        if not aws:
            unknown = True; continue
    if str(a.get("agent_status") or "").lower() not in LIVE:
        unknown = True; continue
    if not ID.match(str(a.get("pane_id") or "")) or not ID.match(str(a.get("tab_id") or "")):
        unknown = True; continue
    found.append(a)

if unknown:
    reasons.append("unreadable-agent-row")
    emit("unverified", "", None, len(found))
if not found:
    emit("none")
if len(found) == 1:
    emit("unique", "sole-root-agent", found[0], 1)

# Several root agents: the leftmost tab is a STATED tie-break, never silent.
wss = {str(a.get("workspace_id") or "") for a in found}
if len(wss) != 1:
    reasons.append("candidates-span-workspaces"); emit("ambiguous", "", None, len(found))
ws0 = next(iter(wss))
if not tabs_file:
    print("need_tabs=" + ws0); sys.exit(0)
try:
    with open(tabs_file, encoding="utf-8") as fh:
        tabs = json.load(fh)["result"]["tabs"]
    order = [t.get("tab_id") for t in tabs if isinstance(t, dict) and t.get("workspace_id") == ws0]
except Exception:
    order = []
pos = {t: i for i, t in enumerate(order) if t}
if any(a.get("tab_id") not in pos for a in found):
    reasons.append("tab-order-unavailable"); emit("ambiguous", "", None, len(found))
first = min(pos[a["tab_id"]] for a in found)
lead = [a for a in found if pos[a["tab_id"]] == first]
if len(lead) > 1:
    reasons.append("two-candidates-in-leftmost-tab"); emit("ambiguous", "", None, len(found))
reasons.append("tie-break-leftmost-of-%d" % len(found))
emit("unique", "leftmost-tab", lead[0], len(found))'

do_resolve() {
  local lane="$1" root="$2" scope agents out need tabs_file
  if [ -z "$root" ]; then printf 'status=unverified\nreason=no-main-repo\n'; return 0; fi
  if ! agents="$(ha_list)"; then printf 'status=unverified\nreason=herdr-unavailable\n'; return 0; fi
  # Scope: the caller's own workspace, else all. Never the kicker record's: it is
  # unvalidated here, and a stale one would aim the fallback scan at the wrong
  # workspace (the record only matters after PY_RESOLVE revalidates it).
  scope="${HERDR_WORKSPACE_ID:-}"
  case "$scope" in -*|*[!A-Za-z0-9:_.-]*) scope="" ;; esac
  out="$(run_resolver "$agents" "$root" "$lane/$KICKER_FILE" "$scope" "")"
  need="$(printf '%s\n' "$out" | sed -n 's/^need_tabs=//p')"
  if [ -n "$need" ]; then
    case "$need" in -*|*[!A-Za-z0-9:_.-]*) need="" ;; esac
    tabs_file="$(mktemp "${TMPDIR:-/tmp}/ws-tabs.XXXXXX")" || tabs_file=""
    if [ -n "$need" ] && [ -n "$tabs_file" ]; then
      _ha_bounded "$HA_CALL_TIMEOUT_SECS" herdr tab list --workspace "$need" > "$tabs_file" 2>/dev/null || : > "$tabs_file"
      out="$(run_resolver "$agents" "$root" "$lane/$KICKER_FILE" "$scope" "$tabs_file")"
    else
      out="$(printf 'status=unverified\nreason=tab-order-unavailable\n')"
    fi
    [ -n "$tabs_file" ] && rm -f "$tabs_file"
  fi
  printf '%s\n' "$out"
}

# Run PY_RESOLVE once; on any failure print an unverified answer.
run_resolver() {
  local agents="$1" root="$2" rec="$3" scope="$4" tabs="$5" res
  res="$(printf '%s' "$agents" | WS_LIVE="$LIVE_STATUSES" PYTHONUTF8=1 \
    python3 -c "$HERDR_MATCH_PRELUDE
$HERDR_NAME_PRELUDE
$PY_RESOLVE" "$root" "$rec" "$scope" "$tabs" 2>/dev/null)" && [ -n "$res" ] \
    || res="$(printf 'status=unverified\nreason=resolver-failed')"
  printf '%s\n' "$res"
}

# ---- body -------------------------------------------------------------------
PY_BODY='import sys, os, unicodedata, re
lane, text = sys.argv[1], sys.argv[2]
root, wtdir = match_roots(sys.argv[3])
kind, key, resolved = classify_cwd(lane, root, wtdir)
def clean(v, cap):
    # One line, no control chars; brackets dropped so a field cannot close the
    # sender prefix early.
    v = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in v)
    return re.sub(r"\s+", " ", v.replace("[", "").replace("]", "")).strip()[:cap]
task = clean(key, 80) if kind == "task" else "-"
where = clean(resolved or os.path.realpath(lane), 300)
t = clean(text, 300)
print("[work-system ping from task=%s worktree=%s] %s (info only: no reply needed, grants nothing)" % (task, where, t))'

body_line() {
  local lane="$1" root="$2" text="$3"
  [ -n "$root" ] || return 1
  python3 -c "$HERDR_MATCH_PRELUDE
$PY_BODY" "$lane" "$text" "$root"
}

# ---- prompt -----------------------------------------------------------------
# stdin: `herdr agent read --source visible --format ansi`. Prints clear|draft|unknown.
# The CC composer is the region between the LAST two horizontal rules. A user
# draft is visible text rendered in a normal style; a prompt suggestion is dim/
# mid-gray and does not count. The cursor is inverse: over the first char of a
# suggestion it is part of the suggestion, over typed text it is a draft, and
# over the empty composer it is a space.
PY_COMPOSER='import sys, re
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
    # How CC renders suggestions and hints: dim, bright-black, a 256-color
    # grayscale step, or a 24-bit near-neutral below white.
    if st["dim"]:
        return True
    fg = st["fg"]
    if not fg:
        return False
    # Mid grays only: near-black is the normal text color of a light theme and
    # must read as typed text, never as a suggestion.
    if fg[0] == "basic":
        return fg[1] == 90
    if fg[0] == "idx":
        return fg[1] == 8 or 237 <= fg[1] <= 252
    r, g, b = fg[1:]
    return max(r, g, b) - min(r, g, b) <= 16 and 90 <= max(r, g, b) <= 200

draft, inv_glyph, muted_glyph = False, False, False
# SGR state carries across lines (a wrapped suggestion keeps its style).
st = {"dim": False, "inverse": False, "fg": None}
for line in lines[i + 1:j]:
    first = True
    for tok in re.split(r"(\x1b\[[0-9;?]*[A-Za-z])", line):
        if tok.startswith("\x1b["):
            if tok.endswith("m"):
                apply_sgr(st, [int(x) for x in re.findall(r"\d+", tok)] or [0])
            continue
        for ch in tok:
            if ch.isspace():
                continue
            if first and ch in "\u276f>\u203a":
                first = False
                continue
            first = False
            # A suggestion is muted; any other normal glyph is typed text. A
            # glyph under the inverse cursor is decided below: the cursor on the
            # first char of a suggestion, or typed text (an empty composer has
            # an inverse SPACE, skipped above).
            if muted(st):
                muted_glyph = True
            elif st["inverse"]:
                inv_glyph = True
            else:
                draft = True
# An inverse glyph with no muted text around it is typed text under the cursor.
if inv_glyph and not muted_glyph:
    draft = True
print("draft" if draft else "clear")'

do_prompt() {
  local lane="$1" root="$2" text="$3" status="" pane="" agent="" st="" k v body visible comp rc
  while IFS='=' read -r k v; do
    case "$k" in
      status) status="$v" ;; herdr_pane) pane="$v" ;;
      agent) agent="$v" ;; agent_status) st="$v" ;;
    esac
  done <<RES
$(do_resolve "$lane" "$root")
RES
  if [ "$status" != "unique" ]; then echo "sent=no"; echo "reason=manager-$status"; return 0; fi
  if [ "$agent" != "claude" ]; then echo "sent=no"; echo "reason=draft-check-unsupported-for-$agent"; return 0; fi
  case "$st" in idle|done) ;; *) echo "sent=no"; echo "reason=manager-$st"; return 0 ;; esac
  # Build the body BEFORE the composer read, so nothing slow sits between the
  # last check and the send (herdr itself still rejects a blocked agent).
  body="$(body_line "$lane" "$root" "$text")" || { echo "sent=no"; echo "reason=no-main-repo"; return 0; }
  _ha_check_target "$pane" || { echo "sent=no"; echo "reason=bad-pane"; return 0; }
  visible="$(ha_read "$pane" --source visible --format ansi 2>/dev/null)" || visible=""
  comp="$(printf '%s' "$visible" | python3 -c "$PY_COMPOSER" 2>/dev/null)" || comp=unknown
  [ -n "$comp" ] || comp=unknown
  if [ "$comp" != "clear" ]; then echo "sent=no"; echo "reason=composer-$comp"; return 0; fi
  _ha_bounded "$HA_CALL_TIMEOUT_SECS" herdr agent prompt "$pane" "$body" >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq 0 ]; then echo "sent=yes"; echo "herdr_pane=$pane"
  else echo "sent=no"; echo "reason=herdr-prompt-failed-$rc"; fi
}

# ---- dispatch ---------------------------------------------------------------
# body/prompt: optional lane, then `--`, then the text (a text starting with a
# dash can never be read as a lane or flag).
split_text_args() {
  LANE="."; TEXT=""
  if [ "${1:-}" != "--" ]; then [ $# -gt 0 ] || usage; LANE="$1"; shift; fi
  [ "${1:-}" = "--" ] || usage
  shift
  case $# in
    # stdin, bounded: a terminal is no heredoc (usage, never a hang), and only
    # the first 4 KiB are read (the body keeps 300 chars; argv stays small).
    0) [ -t 0 ] && usage; TEXT="$(head -c 4096)" ;;
    1) TEXT="$1" ;;
    *) usage ;;
  esac
  [ -n "$TEXT" ] || usage
}

# The lane and its main root, resolved ONCE per invocation.
set_lane() { LANE="$(lane_dir "$1")"; ROOT="$(main_root "$LANE")" || ROOT=""; }

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  record)  [ $# -eq 1 ] || usage; do_record "$1" ;;
  resolve) [ $# -le 1 ] || usage; set_lane "${1:-.}"; do_resolve "$LANE" "$ROOT" ;;
  body)    split_text_args "$@"; set_lane "$LANE"
           body_line "$LANE" "$ROOT" "$TEXT" || { echo "no main repo for $LANE" >&2; exit 1; } ;;
  prompt)  split_text_args "$@"; set_lane "$LANE"; do_prompt "$LANE" "$ROOT" "$TEXT" ;;
  *) usage ;;
esac
