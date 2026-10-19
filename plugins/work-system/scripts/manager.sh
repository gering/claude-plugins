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
#   backfill [<repo-dir>]
#       Run by the Manager (/list, /kickoff): records ITSELF (source=backfill)
#       as the kicker of every lane (registered worktree under
#       .claude/worktrees) that has NO .ws-kicker at all. An existing record —
#       valid, stale or broken — is never replaced, and a lane an agent works
#       in from another workspace is skipped. Writes only when the caller is
#       the SOLE live root agent of its workspace (never a leftmost-tab pick).
#       A backfill record that later fails revalidation counts as no record.
#       Prints recorded=|kept=|failed=<lane> <why> lines, unexcluded=<lane>,
#       backfilled=<n>, and reason= when it refuses; always exit 0.
#   A <lane-dir> argument (default: cwd) is resolved to its git toplevel first,
#   so a call from a subdirectory reads the same record and identity.
#
#   resolve [<lane-dir>]
#       Prints status=unique|none|ambiguous|unverified, evidence=, herdr_pane=,
#       herdr_tab=, herdr_workspace=, herdr_agent_session=, agent=,
#       agent_status=, sendmessage_name= (claude targets only), candidates=,
#       kicker_source=kickoff|backfill (evidence=kicker only),
#       delegate_unasked=yes|no (the /close --delegate rule: a kicker with its
#       name, or a sole root agent while the lane has no record, or only a
#       backfill record whose session is gone), plus
#       one reason= line per observation. Precedence:
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
#       The event text keeps at most BODY_TEXT_CAP (1000) chars after it is
#       folded to one line; a longer one (or a stdin past STDIN_TEXT_BYTES)
#       ends in "…" and stderr says truncated=yes orig_len=<n> (chars before
#       folding; plus stdin_clipped=yes, then a lower bound), so a cut is
#       never silent. An event that folds to nothing is usage (exit 2),
#       unless stdin was clipped: that is still a cut, sent as "…".
#   prompt [<lane-dir>] -- [<text>]
#       The herdr route: resolve again, then `herdr agent prompt` the body to the
#       Manager pane ONLY when status=unique, the agent is claude, idle/done, and
#       its composer holds no user draft (a dim CC prompt suggestion is not a
#       draft). Same body and cap as `body`; the truncation signal can come
#       with sent=no, so a cut line was delivered only on sent=yes. Prints
#       sent=yes|no and reason=; exit 0 (usage errors exit 2). Residual: the
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
# Event-text cap, one for both routes: a measured ~4000-char `herdr agent
# prompt` reached a claude composer intact, so the herdr route needs no
# shorter one. Stdin keeps STDIN_TEXT_BYTES; a longer input is flagged as cut.
BODY_TEXT_CAP=1000
STDIN_TEXT_BYTES=16384

usage() {
  echo "usage: ${0##*/} {record <worktree> | backfill [<repo>] | resolve [<lane>] | body [<lane>] -- <text> | prompt [<lane>] -- <text>}" >&2
  echo "  body/prompt: event text is capped at $BODY_TEXT_CAP chars; a cut ends in \"…\" and stderr says truncated=yes orig_len=<chars before folding> [stdin_clipped=yes: more was sent]" >&2
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
# Mirrors mandate.sh exclude_one (0 already, 1 written, 2 not excluded);
# test_mandate.py keeps the two bodies identical.
exclude_one() {
  local dir="$1" pat="$2" excl
  git -C "$dir" check-ignore -q -- "$pat" 2>/dev/null && return 0
  # A tracked file stays visible whatever the exclude says: report it, add nothing.
  git -C "$dir" ls-files --error-unmatch -- "$pat" >/dev/null 2>&1 && return 2
  excl="$(git -C "$dir" rev-parse --git-path info/exclude 2>/dev/null)" || excl=""
  [ -n "$excl" ] || return 2
  case "$excl" in /*) ;; *) excl="$dir/$excl" ;; esac
  if [ -f "$excl" ] && grep -qxF -- "/$pat" "$excl" 2>/dev/null; then return 2; fi
  mkdir -p "${excl%/*}" 2>/dev/null || return 2
  # A last line without its newline would swallow the pattern (`*.log/.ws-kicker`).
  if [ -s "$excl" ] && [ -n "$(tail -c 1 "$excl")" ]; then
    printf '\n' >> "$excl" 2>/dev/null || return 2
  fi
  printf '/%s\n' "$pat" >> "$excl" 2>/dev/null || return 2
  git -C "$dir" check-ignore -q -- "$pat" 2>/dev/null || return 2
  return 1
}

# ---- record -----------------------------------------------------------------
# The calling session's record body for <root>, or exactly one reason= line.
kicker_body() {
  local root="$1" json rec
  if [ "${HERDR_ENV:-}" != "1" ] || [ -z "${HERDR_PANE_ID:-}" ]; then
    echo "reason=not-in-herdr"; return 0
  fi
  json="$(ha_get "$HERDR_PANE_ID")" || { echo "reason=herdr-unavailable"; return 0; }
  rec="$(printf '%s' "$json" | PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$HERDR_NAME_PRELUDE
$PY_RECORD" "$root" 2>/dev/null)" || rec=""
  [ -n "$rec" ] || rec="reason=malformed-agent"
  printf '%s\n' "$rec"
}

# argv: worktree, mode; stdin: the record. Creates the temp file O_EXCL|
# O_NOFOLLOW and links/renames it by name, so a path the worker swaps for a
# symlink in its own worktree is never written through. Prints ok|exists|error.
PY_WRITE='import os, sys, secrets
wt, mode = sys.argv[1], sys.argv[2]
dst = os.path.join(wt, ".ws-kicker")
data = sys.stdin.buffer.read()
tmp = None
try:
    for _ in range(5):
        cand = os.path.join(wt, ".ws-kicker." + secrets.token_hex(6))
        try:
            fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        except FileExistsError:
            continue
        tmp = cand
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        break
    if tmp is None:
        print("error"); sys.exit(0)
    if mode == "new":
        try:
            os.link(tmp, dst, follow_symlinks=False)
        except FileExistsError:
            print("exists"); sys.exit(0)
    else:
        os.replace(tmp, dst)
        tmp = None
    print("ok")
except OSError:
    print("error")
finally:
    if tmp is not None:
        try:
            os.unlink(tmp)
        except OSError:
            pass'

# A tracked record would be committed with the lane (exclude does not apply
# to tracked paths): the same refusal as mandate.sh refuse_tracked.
refuse_tracked() {
  git -C "$1" ls-files --error-unmatch -- "$KICKER_FILE" >/dev/null 2>&1 || return 1
  echo "recorded=no"; echo "reason=kicker-file-tracked"
}

# Write <rec> to <wt>/.ws-kicker and git-exclude it. <mode> new = only where no
# record exists (a link, not a rename, so a record written meanwhile is never
# replaced); replace = over the existing one. Prints recorded= and the rest.
write_kicker() {
  local wt="$1" rec="$2" mode="$3" res rc=0
  refuse_tracked "$wt" && return 0
  res="$(printf '%s\nrecorded_at=%s\n' "$rec" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    | python3 -c "$PY_WRITE" "$wt" "$mode" 2>/dev/null)" || res=error
  case "$res" in
    ok) ;;
    exists) echo "recorded=no"; echo "reason=kicker-record-appeared"; return 0 ;;
    *) echo "recorded=no"; echo "reason=unwritable"; return 0 ;;
  esac
  exclude_one "$wt" ".ws-kicker.*" || true
  exclude_one "$wt" "$KICKER_FILE" || rc=$?
  echo "recorded=yes"
  echo "file=$wt/$KICKER_FILE"
  case "$rc" in 0) echo "excluded=already" ;; 1) echo "excluded=yes" ;; *) echo "excluded=no" ;; esac
}

do_record() {
  local wt="${1:-}" root rec
  [ -n "$wt" ] || usage
  if [ ! -d "$wt" ]; then echo "recorded=no"; echo "reason=no-such-worktree"; return 0; fi
  # Checked before any herdr call, so a tracked record is named as such.
  refuse_tracked "$wt" && return 0
  root="$(main_root "$wt")" || root=""
  if [ -z "$root" ]; then echo "recorded=no"; echo "reason=no-main-repo"; return 0; fi
  rec="$(kicker_body "$root")"
  case "$rec" in reason=*) echo "recorded=no"; printf '%s\n' "$rec"; return 0 ;; esac
  write_kicker "$wt" "$rec" replace
}

# ---- backfill ---------------------------------------------------------------
# stdin: `git worktree list --porcelain` paths; argv[1]: canonical root. Prints
# the lanes: registered worktrees that are direct children of .claude/worktrees.
PY_LANES='import sys
root, wtdir = match_roots(sys.argv[1])
for line in sys.stdin:
    kind, _, resolved = classify_cwd(line.rstrip("\n"), root, wtdir)
    if kind == "task" and resolved and "\t" not in resolved and os.path.isdir(resolved):
        print(resolved)'

# The caller's own workspace (sanitized), else empty = all workspaces.
caller_scope() {
  local scope="${HERDR_WORKSPACE_ID:-}"
  case "$scope" in -*|*[!A-Za-z0-9:_.-]*) scope="" ;; esac
  printf '%s' "$scope"
}

# stdin: agent-list JSON; argv: root, scope. Prints the lanes some agent works in
# from ANOTHER workspace: that workspace has its own Manager, never the caller.
PY_ELSEWHERE='import sys, json
root, wtdir = match_roots(sys.argv[1])
scope = sys.argv[2]
try:
    agents = json.load(sys.stdin)["result"]["agents"]
except Exception:
    agents = []
for a in agents if isinstance(agents, list) else []:
    if not isinstance(a, dict) or str(a.get("workspace_id") or "") == scope:
        continue
    # Anywhere inside a lane counts, a subdirectory included.
    cwd = os.path.realpath(str(a.get("cwd") or "/"))
    if cwd.startswith(wtdir + os.sep):
        print(os.path.join(wtdir, cwd[len(wtdir) + 1:].split(os.sep)[0]))'

do_backfill() {
  local root="$1" agents scope lanes elsewhere wt todo="" n=0 out who ev pane rec
  if [ -z "$root" ]; then echo "backfilled=0"; echo "reason=no-main-repo"; return 0; fi
  if [ "${HERDR_ENV:-}" != "1" ] || [ -z "${HERDR_PANE_ID:-}" ]; then
    echo "backfilled=0"; echo "reason=not-in-herdr"; return 0
  fi
  lanes="$(git -C "$root" worktree list --porcelain 2>/dev/null | sed -n 's/^worktree //p' \
    | PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$PY_LANES" "$root" 2>/dev/null)" || lanes=""
  # Only a lane WITHOUT any record is filled. An existing one — valid, stale,
  # broken — is never replaced: resolve already ignores a stale record, and no
  # single agent-list snapshot can prove its kicker gone everywhere.
  while IFS= read -r wt; do
    [ -n "$wt" ] || continue
    if [ -e "$wt/$KICKER_FILE" ] || [ -L "$wt/$KICKER_FILE" ]; then
      printf 'kept=%s present\n' "${wt##*/}"
    else
      todo="$todo$wt
"
    fi
  done <<LANES
$lanes
LANES
  if [ -z "$todo" ]; then echo "backfilled=0"; return 0; fi
  # The lane's own workspace is the only scope a Manager answers for.
  scope="$(caller_scope)"
  if [ -z "$scope" ]; then echo "backfilled=0"; echo "reason=no-workspace-scope"; return 0; fi
  # Only the SOLE live root agent may record itself: then no other candidate
  # exists to confuse it with. A leftmost-tab tie-break keeps asking at close
  # time; turning its winner into a kicker would make that guess unasked.
  agents="$(ha_list)" || { echo "backfilled=0"; echo "reason=herdr-unavailable"; return 0; }
  who="$(resolve_with "$agents" "$root" "")"
  ev="$(printf '%s\n' "$who" | sed -n 's/^evidence=//p')"
  pane="$(printf '%s\n' "$who" | sed -n 's/^herdr_pane=//p')"
  if [ "$ev" != sole-root-agent ] || [ "$pane" != "$HERDR_PANE_ID" ]; then
    echo "backfilled=0"; echo "reason=caller-not-sole-root-agent"; return 0
  fi
  rec="$(kicker_body "$root")"
  case "$rec" in reason=*) echo "backfilled=0"; printf '%s\n' "$rec"; return 0 ;; esac
  rec="$rec
source=backfill"
  elsewhere="$(printf '%s' "$agents" | PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$PY_ELSEWHERE" "$root" "$scope" 2>/dev/null)" || elsewhere=""
  while IFS= read -r wt; do
    [ -n "$wt" ] || continue
    if printf '%s\n' "$elsewhere" | grep -qxF -- "$wt"; then
      printf 'kept=%s other-workspace\n' "${wt##*/}"; continue
    fi
    # new = link, no clobber: a record /kickoff wrote meanwhile wins.
    out="$(write_kicker "$wt" "$rec" new)"
    case "$out" in
      recorded=yes*)
        n=$((n + 1)); printf 'recorded=%s absent\n' "${wt##*/}"
        case "$out" in *excluded=no*) printf 'unexcluded=%s\n' "${wt##*/}" ;; esac ;;
      *) printf 'failed=%s %s\n' "${wt##*/}" "$(printf '%s\n' "$out" | sed -n 's/^reason=//p' | head -1)" ;;
    esac
  done <<TODO
$todo
TODO
  echo "backfilled=$n"
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
    # kicker_source=backfill: the record was filled in later by the sole live
    # root agent of its workspace, not by the session that kicked the lane off.
    src = (rec or {}).get("source", "kickoff")
    print("kicker_source=" + ((src if src in ("kickoff", "backfill") else "unknown") if ev == "kicker" else ""))
    # The one rule for sending a close-request unasked (/close --delegate):
    # the revalidated kicker, or the only root agent when the lane has no
    # record that names anyone else. A kickoff record that failed revalidation
    # means its Manager may be elsewhere; a BACKFILL record whose session is
    # gone only ever said "the sole root agent then", so it counts as none.
    norec = "kicker-record-absent" in reasons or "kicker-backfill-stale" in reasons
    clear = (ev == "kicker" and name) or (ev == "sole-root-agent" and norec)
    print("delegate_unasked=" + ("yes" if clear else "no"))
    for r in reasons:
        print("reason=" + r)
    sys.exit(0)

root, wtdir = match_roots(root_arg)
rec = None
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
    elif rec.get("source") == "backfill" and scope and rec.get("workspace") != scope:
        # Filled in by the sole root agent of another workspace: not this lane.
        reasons.append("kicker-other-workspace")
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

# A backfill record whose session is provably gone (pane gone, or another
# session in it) counts as none. Any other failure (another workspace, moved
# cwd, not live) may still name a live Manager elsewhere, so it keeps asking.
if rec is not None and rec.get("source") == "backfill" and (
        "kicker-pane-gone" in reasons or "kicker-session-changed" in reasons):
    reasons.append("kicker-backfill-stale")

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
  local lane="$1" root="$2" agents
  if [ -z "$root" ]; then printf 'status=unverified\nreason=no-main-repo\n'; return 0; fi
  if ! agents="$(ha_list)"; then printf 'status=unverified\nreason=herdr-unavailable\n'; return 0; fi
  resolve_with "$agents" "$root" "$lane/$KICKER_FILE"
}

# Resolve over an agent list already fetched; <rec> "" resolves without a record.
resolve_with() {
  local agents="$1" root="$2" rec="$3" scope out need tabs_file
  # Scope: the caller's own workspace, else all. Never the kicker record's: it is
  # unvalidated here, and a stale one would aim the fallback scan at the wrong
  # workspace (the record only matters after PY_RESOLVE revalidates it).
  scope="$(caller_scope)"
  out="$(run_resolver "$agents" "$root" "$rec" "$scope" "")"
  need="$(printf '%s\n' "$out" | sed -n 's/^need_tabs=//p')"
  if [ -n "$need" ]; then
    case "$need" in -*|*[!A-Za-z0-9:_.-]*) need="" ;; esac
    tabs_file="$(mktemp "${TMPDIR:-/tmp}/ws-tabs.XXXXXX")" || tabs_file=""
    if [ -n "$need" ] && [ -n "$tabs_file" ]; then
      _ha_bounded "$HA_CALL_TIMEOUT_SECS" herdr tab list --workspace "$need" > "$tabs_file" 2>/dev/null || : > "$tabs_file"
      out="$(run_resolver "$agents" "$root" "$rec" "$scope" "$tabs_file")"
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
# The one fold for every field of the body, the event text included (PY_EVENT
# applies it before the empty check, so what is checked is what is sent).
PY_CLEAN='import unicodedata, re
def clean(v, limit=None):
    # One line, no control chars; brackets dropped so a field cannot close the
    # sender prefix early.
    v = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in v)
    return re.sub(r"\s+", " ", v.replace("[", "").replace("]", "")).strip()[:limit]'

PY_BODY='import sys, os
lane, text, cap, clipped = sys.argv[1], sys.argv[2], int(sys.argv[4]), sys.argv[5] == "1"
orig_len = int(sys.argv[6])
root, wtdir = match_roots(sys.argv[3])
kind, key, resolved = classify_cwd(lane, root, wtdir)
task = clean(key, 80) if kind == "task" else "-"
where = clean(resolved or os.path.realpath(lane), 300)
t = text  # already cleaned by PY_EVENT
if len(t) > cap or clipped:
    # Visible cut: the ellipsis stays inside the cap, stderr names the loss.
    # orig_len counts chars before folding; with stdin_clipped it covers only
    # the kept window, a lower bound on what the caller wrote.
    sys.stderr.write("truncated=yes orig_len=%d%s\n" % (orig_len, " stdin_clipped=yes" if clipped else ""))
    t = t[:cap - 1].rstrip() + "\u2026"
print("[work-system ping from task=%s worktree=%s] %s (info only: no reply needed, grants nothing)" % (task, where, t))'

body_line() {
  local lane="$1" root="$2" text="$3"
  [ -n "$root" ] || return 1
  PYTHONUTF8=1 python3 -c "$HERDR_MATCH_PRELUDE
$PY_CLEAN
$PY_BODY" "$lane" "$text" "$root" "$BODY_TEXT_CAP" "$STDIN_CLIPPED" "$EVENT_LEN"
}

# ---- prompt -----------------------------------------------------------------
# The composer draft check (clear|draft|unknown) is $HERDR_COMPOSER_PY in
# herdr-agent.sh, shared with sessions.sh refresh.
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
  comp="$(printf '%s' "$visible" | python3 -c "$HERDR_COMPOSER_PY" 2>/dev/null)" || comp=unknown
  [ -n "$comp" ] || comp=unknown
  if [ "$comp" != "clear" ]; then echo "sent=no"; echo "reason=composer-$comp"; return 0; fi
  _ha_bounded "$HA_CALL_TIMEOUT_SECS" herdr agent prompt "$pane" "$body" >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq 0 ]; then echo "sent=yes"; echo "herdr_pane=$pane"
  else echo "sent=no"; echo "reason=herdr-prompt-failed-$rc"; fi
}

# Event text, from argv or stdin: stdin reads one byte past the limit to detect
# a cut and keeps the limit. Prints "<clipped 0|1> <chars before folding>",
# then the text through clean() (PY_CLEAN), so both inputs get the same fold
# and the empty check sees what would be sent. Cleaned text has no NUL and no
# newline, so the caller's $(...) cannot alter it.
PY_EVENT='import sys
n = int(sys.argv[1])
if len(sys.argv) > 2:
    # argv is complete: never clipped, counted whole.
    t = sys.argv[2].encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    clipped, size = False, len(t)
else:
    b = sys.stdin.buffer.read(n + 1)
    t = b[:n].decode("utf-8", "replace")
    clipped = len(b) > n
    # A heredoc ends in a newline that is no part of the event; a clipped
    # window is counted whole.
    size = len(t) - (not clipped and t.endswith("\n"))
print("%d %d" % (clipped, size))
sys.stdout.write(clean(t))'

# ---- dispatch ---------------------------------------------------------------
# body/prompt: optional lane, then `--`, then the text (a text starting with a
# dash can never be read as a lane or flag).
split_text_args() {
  LANE="."; TEXT=""; STDIN_CLIPPED=0; EVENT_LEN=0
  if [ "${1:-}" != "--" ]; then [ $# -gt 0 ] || usage; LANE="$1"; shift; fi
  [ "${1:-}" = "--" ] || usage
  shift
  local out
  case $# in
    # stdin, bounded: a terminal is no heredoc (usage, never a hang). Python
    # reads it (PY_EVENT), so raw bytes never pass through $(...).
    0) [ -t 0 ] && usage
       out="$(PYTHONUTF8=1 python3 -c "$PY_CLEAN
$PY_EVENT" "$STDIN_TEXT_BYTES")" || usage ;;
    1) out="$(PYTHONUTF8=1 python3 -c "$PY_CLEAN
$PY_EVENT" "$STDIN_TEXT_BYTES" "$1")" || usage ;;
    *) usage ;;
  esac
  read -r STDIN_CLIPPED EVENT_LEN <<< "${out%%$'\n'*}"
  case "$out" in *$'\n'*) TEXT="${out#*$'\n'}" ;; esac
  # Checked after the fold: an event that cleans to nothing ("[ ]") is usage.
  [ -n "$TEXT" ] || [ "$STDIN_CLIPPED" = 1 ] || usage
}

# The lane and its main root, resolved ONCE per invocation.
set_lane() { LANE="$(lane_dir "$1")"; ROOT="$(main_root "$LANE")" || ROOT=""; }

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  record)  [ $# -eq 1 ] || usage; do_record "$1" ;;
  backfill) [ $# -le 1 ] || usage; set_lane "${1:-.}"; do_backfill "$ROOT" ;;
  resolve) [ $# -le 1 ] || usage; set_lane "${1:-.}"; do_resolve "$LANE" "$ROOT" ;;
  body)    split_text_args "$@"; set_lane "$LANE"
           body_line "$LANE" "$ROOT" "$TEXT" || { echo "no main repo for $LANE" >&2; exit 1; } ;;
  prompt)  split_text_args "$@"; set_lane "$LANE"; do_prompt "$LANE" "$ROOT" "$TEXT" ;;
  *) usage ;;
esac
