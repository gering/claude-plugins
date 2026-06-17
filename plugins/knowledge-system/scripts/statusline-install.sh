#!/usr/bin/env bash
# statusline-install.sh — install/manage the [cks N|M] knowledge-system status
# block in Claude Code's custom status line.
#
# The render half is scripts/statusline-cks.sh; this is the install half. It is
# the source of truth for /knowledge-system:statusline — the SKILL.md is a thin
# wrapper that parses the argument, runs this, and relays the output.
#
# Subcommands:
#   status     (default) report plugin/installed renderer versions, marker-block
#              presence, and the current project's disable sentinel.
#   install    copy the renderer to ~/.claude/cks-statusline.sh (version-gated)
#              and inject a marker block into ~/.claude/statusline.sh.
#   enable     remove this project's disable sentinel.
#   disable    create this project's disable sentinel (per-project opt-out).
#   uninstall  strip the marker block and delete the installed renderer.
#
# Flags:
#   --force    (install only) overwrite a pre-existing manual cks block, or
#              force a downgrade of the installed renderer.
#
# python3 is required (symlink resolution + atomic file mutation). BSD awk on
# macOS rejects newlines in -v values and silently truncates the target, so all
# multi-line mutation goes through python3 with an atomic os.replace.
#
# set -u (catch unset vars) but NOT -e: the flow relies on grep returning
# non-zero for "no match" all over the place; errors are handled explicitly.
set -u

# ---- paths and constants ----------------------------------------------------

STATUSLINE="${HOME}/.claude/statusline.sh"          # configured path (maybe a symlink)
INSTALLED="${HOME}/.claude/cks-statusline.sh"        # stable runtime renderer path
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="${SCRIPT_DIR}/statusline-cks.sh"             # plugin's renderer source of truth
BEGIN_MARKER="# >>> knowledge-system:cks-statusline >>>"
END_MARKER="# <<< knowledge-system:cks-statusline <<<"

STATUSLINE_TARGET=""   # resolved real path of STATUSLINE (set by resolve_target)
BACKUP=""              # session backup path (set by backup_target)

MARKER_BLOCK="$(cat <<'EOF'
# >>> knowledge-system:cks-statusline >>>
# Managed by /knowledge-system:statusline. Do not edit between markers —
# run `/knowledge-system:statusline uninstall` to remove cleanly.
if [ -x "$HOME/.claude/cks-statusline.sh" ]; then
  _CKS_DIR="${DIR:-${CLAUDE_PROJECT_DIR:-$PWD}}"
  CKS_PLUGIN=$(bash "$HOME/.claude/cks-statusline.sh" "$_CKS_DIR" 2>/dev/null)
  [ -n "$CKS_PLUGIN" ] && OUT="$OUT $CKS_PLUGIN"
fi
# <<< knowledge-system:cks-statusline <<<
EOF
)"

# ---- helpers ----------------------------------------------------------------

die() { printf '%s\n' "$*" >&2; exit 1; }

require_python3() {
  command -v python3 >/dev/null 2>&1 \
    || die "python3 not on PATH — required by /statusline $1"
}

# Resolve a path's real target (handles symlink chains; returns the input even
# for broken chains, matching os.path.realpath — callers re-check existence).
realpath_of() {
  python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

# Extract X.Y.Z from a script's `readonly CKS_STATUSLINE_VERSION=` line.
# Missing or malformed → 0.0.0.
script_version() {
  local v
  v=$(grep -m1 '^readonly CKS_STATUSLINE_VERSION=' "$1" 2>/dev/null \
    | sed -E 's/.*=[[:space:]]*"?([0-9]+\.[0-9]+\.[0-9]+)"?.*/\1/')
  if printf '%s' "$v" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    printf '%s' "$v"
  else
    printf '0.0.0'
  fi
}

# Compare two X.Y.Z versions. Echoes: newer (a>b), equal, older (a<b).
version_cmp() {
  local a=$1 b=$2 highest
  [ "$a" = "$b" ] && { echo equal; return; }
  highest=$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -1)
  [ "$highest" = "$a" ] && echo newer || echo older
}

resolve_target() {
  STATUSLINE_TARGET=$(realpath_of "$STATUSLINE")
  if [ -L "$STATUSLINE" ]; then
    printf 'Note: %s is a symlink → resolved to %s. Edits modify the link target.\n' \
      "$STATUSLINE" "$STATUSLINE_TARGET"
  fi
}

backup_target() {
  BACKUP="${STATUSLINE_TARGET}.bak-$(date +%s)"
  cp "$STATUSLINE_TARGET" "$BACKUP" || die "Failed to create backup at $BACKUP"
}

restore_backup() {
  [ -n "$BACKUP" ] && cp "$BACKUP" "$STATUSLINE_TARGET" 2>/dev/null || true
}

# Ensure +x on a file; report (never silently). ctx: preflight | post-write.
ensure_executable() {
  local f=$1 ctx=$2
  [ -x "$f" ] && return
  chmod +x "$f" || die "Failed to set executable bit on $f."
  if [ "$ctx" = "preflight" ]; then
    printf 'Set executable bit on %s (was missing — would have caused silent statusline failure).\n' "$f"
  else
    printf 'Re-set executable bit on %s (was dropped during write).\n' "$f"
  fi
}

# Count lines containing a fixed string (0 on missing file / no match).
count_fixed() {
  local n
  n=$(grep -cF "$1" "$2" 2>/dev/null || true)
  printf '%s' "${n:-0}"
}

# First line number of a fixed string (empty if absent).
line_of_fixed() {
  grep -nF "$1" "$2" 2>/dev/null | head -1 | cut -d: -f1
}

# Replace lines START..END (inclusive, 1-based) of STATUSLINE_TARGET with the
# marker text (arg 3). Empty marker = pure deletion. Pure insertion before line
# L: pass START=L END=L-1. Atomic via os.replace on the resolved real path.
mutate_file() {
  local start=$1 end=$2 marker=${3:-}
  PATH_TARGET="$STATUSLINE_TARGET" MARKER="$marker" \
    START_LINE="$start" END_LINE="$end" \
    python3 - <<'PY'
import os, sys
path = os.environ['PATH_TARGET']
tmp = path + f".tmp.{os.getpid()}"
try:
    marker = os.environ.get('MARKER', '')
    start = int(os.environ['START_LINE'])  # 1-based, inclusive
    end = int(os.environ['END_LINE'])      # 1-based, inclusive; start-1 = insert-only
    lines = open(path).read().splitlines(keepends=True)
    block = []
    if marker:
        # MARKER from $() loses its trailing newline; restore one so the block
        # does not collide with the following line.
        block = [marker + ("\n" if not marker.endswith("\n") else "")]
    new_lines = lines[:start-1] + block + lines[end:]  # end inclusive
    with open(tmp, 'w') as f:
        f.writelines(new_lines)
    os.replace(tmp, path)  # atomic on same filesystem; PATH_TARGET is the real path
except Exception as e:
    try: os.unlink(tmp)
    except FileNotFoundError: pass
    print(f"FATAL: file mutation failed: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

# ---- status -----------------------------------------------------------------

cmd_status() {
  require_python3 status
  local src_v inst_v="" cmp marker_state="absent" proj

  src_v=$(script_version "$SOURCE")
  printf 'cks statusline status\n'

  if [ -e "$SOURCE" ]; then
    printf -- '- Plugin renderer: %s (v%s)\n' "$SOURCE" "$src_v"
  else
    printf -- '- Plugin renderer: MISSING (%s) — reinstall the plugin\n' "$SOURCE"
  fi

  if [ -e "$INSTALLED" ]; then
    inst_v=$(script_version "$INSTALLED")
    cmp=$(version_cmp "$src_v" "$inst_v")
    case "$cmp" in
      equal) printf -- '- Installed renderer: %s (v%s, current)\n' "$INSTALLED" "$inst_v" ;;
      newer) printf -- '- Installed renderer: %s (v%s, older than plugin v%s — run install to upgrade)\n' "$INSTALLED" "$inst_v" "$src_v" ;;
      older) printf -- '- Installed renderer: %s (v%s, newer than plugin v%s — downgrade scenario)\n' "$INSTALLED" "$inst_v" "$src_v" ;;
    esac
  else
    printf -- '- Installed renderer: missing (%s)\n' "$INSTALLED"
  fi

  if [ -e "$STATUSLINE" ]; then
    resolve_target
    local nb ne
    nb=$(count_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
    ne=$(count_fixed "$END_MARKER" "$STATUSLINE_TARGET")
    if [ "$nb" = "1" ] && [ "$ne" = "1" ]; then
      marker_state="present"
    elif [ "$nb" = "0" ] && [ "$ne" = "0" ]; then
      marker_state="absent"
    else
      marker_state="INVALID ($nb BEGIN / $ne END — resolve manually)"
    fi
    printf -- '- Marker block in %s: %s\n' "$STATUSLINE_TARGET" "$marker_state"
  else
    printf -- '- Host statusline: %s not found (set one up, then run install)\n' "$STATUSLINE"
  fi

  proj="${CLAUDE_PROJECT_DIR:-$(pwd)}"
  if [ -f "$proj/.claude/.cks-statusline-off" ]; then
    printf -- '- Project sentinel: present → cks disabled for %s\n' "$proj"
  else
    printf -- '- Project sentinel: absent → cks enabled for %s\n' "$proj"
  fi

  if [ ! -e "$INSTALLED" ] || [ "$marker_state" = "absent" ]; then
    printf 'Hint: run `install` to set up the cks status block.\n'
  elif [ -n "$inst_v" ] && [ "$(version_cmp "$src_v" "$inst_v")" = "newer" ]; then
    printf 'Hint: run `install` to upgrade the renderer (v%s → v%s).\n' "$inst_v" "$src_v"
  else
    printf 'Hint: cks is installed and current.\n'
  fi
}

# ---- install ----------------------------------------------------------------

# Copy the renderer to INSTALLED, version-gated. Sets RENDERER_MSG. Honours FORCE
# for downgrades. Aborts (without touching the marker block) on copy failure.
copy_renderer() {
  local src_v dest_v cmp
  src_v=$(script_version "$SOURCE")
  if [ ! -e "$INSTALLED" ]; then
    cp "$SOURCE" "$INSTALLED" \
      || die "Failed to install renderer to $INSTALLED. Marker block was not touched."
    chmod +x "$INSTALLED" \
      || die "Failed to set executable bit on $INSTALLED. Marker block was not touched."
    RENDERER_MSG="Renderer installed: $INSTALLED (v$src_v)"
    return
  fi
  dest_v=$(script_version "$INSTALLED")
  cmp=$(version_cmp "$src_v" "$dest_v")
  case "$cmp" in
    newer)
      cp "$SOURCE" "$INSTALLED" \
        || die "Failed to upgrade renderer at $INSTALLED. Marker block was not touched."
      chmod +x "$INSTALLED" \
        || die "Failed to set executable bit on $INSTALLED. Marker block was not touched."
      RENDERER_MSG="Renderer upgraded: v$dest_v → v$src_v ($INSTALLED)"
      ;;
    equal)
      RENDERER_MSG="Renderer already current (v$src_v)"
      ;;
    older)
      if [ "$FORCE" -eq 1 ]; then
        cp "$SOURCE" "$INSTALLED" \
          || die "Failed to overwrite renderer at $INSTALLED. Marker block was not touched."
        chmod +x "$INSTALLED" \
          || die "Failed to set executable bit on $INSTALLED. Marker block was not touched."
        RENDERER_MSG="Renderer force-downgraded: v$dest_v → v$src_v ($INSTALLED)"
      else
        RENDERER_MSG="Installed v$dest_v is newer than plugin v$src_v — skipped copy (use --force to overwrite)."
      fi
      ;;
  esac
}

# Decide where the marker block goes when none exists yet. Sets START_LINE,
# END_LINE (caller-owned locals via dynamic scope) and PLACEMENT_MSG, or dies.
determine_placement() {
  local ph_count ph_line last_out auto_line ph_lines
  ph_count=$(grep -cE '^[[:space:]]*#[[:space:]]*\{\{cks\}\}[[:space:]]*$' "$STATUSLINE_TARGET" 2>/dev/null || true)
  ph_count=${ph_count:-0}

  if [ "$ph_count" -gt 1 ]; then
    ph_lines=$(grep -nE '^[[:space:]]*#[[:space:]]*\{\{cks\}\}[[:space:]]*$' "$STATUSLINE_TARGET" | cut -d: -f1 | paste -sd, -)
    die "Multiple # {{cks}} placeholders found (lines $ph_lines). Keep only one and re-run install."
  fi

  if [ "$ph_count" -eq 1 ]; then
    ph_line=$(grep -nE '^[[:space:]]*#[[:space:]]*\{\{cks\}\}[[:space:]]*$' "$STATUSLINE_TARGET" | head -1 | cut -d: -f1)
    # Last OUT= or OUT+= assignment. The [+]? is critical: a bare OUT= regex
    # misses OUT+= and would let the placeholder land before later appends.
    last_out=$(grep -nE '^[[:space:]]*OUT[+]?=' "$STATUSLINE_TARGET" | tail -1 | cut -d: -f1)
    if [ -z "$last_out" ]; then
      die "Could not find any OUT= assignment in $STATUSLINE_TARGET. The injected block appends to \$OUT — your statusline must define it. See the snippet in SKILL.md for the contract."
    fi
    if [ "$ph_line" -gt "$last_out" ]; then
      START_LINE=$ph_line
      END_LINE=$ph_line
      PLACEMENT_MSG="placed at user-defined position (# {{cks}} placeholder on line $ph_line)"
      return
    fi
    die "# {{cks}} placeholder on line $ph_line is at or before the last \$OUT assignment (line $last_out). The injected block does OUT=\"\$OUT \$CKS_PLUGIN\" — placing it earlier means the later OUT= overwrites it and cks silently disappears. Move the placeholder after line $last_out and re-run install."
  fi

  # No placeholder → auto-detect: insert before the last line that prints $OUT.
  auto_line=$(grep -nE '(echo[[:space:]].*\$OUT|printf[[:space:]].*\$OUT)' "$STATUSLINE_TARGET" | tail -1 | cut -d: -f1)
  if [ -n "$auto_line" ]; then
    START_LINE=$auto_line
    END_LINE=$((auto_line - 1))   # empty range → pure insertion before auto_line
    PLACEMENT_MSG="auto-placed before line $auto_line (add a # {{cks}} comment to choose a different position)"
    return
  fi

  die "No # {{cks}} placeholder and no 'echo/printf \$OUT' line found in $STATUSLINE_TARGET.
Add a # {{cks}} comment where you want the cks block (after your last OUT= assignment) and re-run install, or paste this block manually:

$MARKER_BLOCK"
}

# Post-write sanity checks. Restore from backup and abort on the first failure.
verify_after_install() {
  if [ ! -s "$STATUSLINE_TARGET" ]; then
    restore_backup
    die "Post-write check failed: $STATUSLINE_TARGET is empty. Restored from backup ($BACKUP)."
  fi
  local nb ne bl el err
  nb=$(count_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
  ne=$(count_fixed "$END_MARKER" "$STATUSLINE_TARGET")
  if [ "$nb" != "1" ] || [ "$ne" != "1" ]; then
    restore_backup
    die "Post-write check failed: expected one marker pair, found $nb BEGIN / $ne END. Restored from backup ($BACKUP)."
  fi
  bl=$(line_of_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
  el=$(line_of_fixed "$END_MARKER" "$STATUSLINE_TARGET")
  if [ "$bl" -ge "$el" ]; then
    restore_backup
    die "Post-write check failed: BEGIN@$bl not before END@$el. Restored from backup ($BACKUP)."
  fi
  if ! bash -n "$STATUSLINE_TARGET" 2>/dev/null; then
    err=$(bash -n "$STATUSLINE_TARGET" 2>&1 || true)
    restore_backup
    die "Post-write check failed: $STATUSLINE_TARGET has a syntax error. Restored from backup ($BACKUP). ($err)"
  fi
  ensure_executable "$STATUSLINE_TARGET" post-write
}

cmd_install() {
  require_python3 install

  # a. Preflight
  [ -e "$STATUSLINE" ] \
    || die "No ~/.claude/statusline.sh found. Set up your custom status line first (see ~/.claude/settings.json → statusLine.command)."
  [ -e "$SOURCE" ] \
    || die "Renderer source not found at $SOURCE. Reinstall the knowledge-system plugin."
  resolve_target
  [ -e "$STATUSLINE_TARGET" ] \
    || die "Symlink $STATUSLINE resolves to $STATUSLINE_TARGET which does not exist (broken link chain?). Fix the symlink and re-run."
  [ -w "$STATUSLINE_TARGET" ] \
    || die "$STATUSLINE_TARGET is not writable (permissions/ownership?). Fix and re-run."
  ensure_executable "$STATUSLINE_TARGET" preflight

  # b. Copy renderer (version-gated)
  local SRC_VERSION RENDERER_MSG
  SRC_VERSION=$(script_version "$SOURCE")
  copy_renderer

  # c/d. Inspect marker pair → refresh, place fresh, or refuse.
  local nb ne bl el START_LINE END_LINE PLACEMENT_MSG
  nb=$(count_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
  ne=$(count_fixed "$END_MARKER" "$STATUSLINE_TARGET")

  if [ "$nb" = "1" ] && [ "$ne" = "1" ]; then
    bl=$(line_of_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
    el=$(line_of_fixed "$END_MARKER" "$STATUSLINE_TARGET")
    [ "$bl" -lt "$el" ] \
      || die "Marker pair invalid in $STATUSLINE_TARGET ($nb BEGIN, $ne END, BEGIN@$bl, END@$el). Resolve manually before re-running install."
    START_LINE=$bl
    END_LINE=$el
    PLACEMENT_MSG="refreshed existing marker block (lines ${bl}-${el})"
  elif [ "$nb" = "0" ] && [ "$ne" = "0" ]; then
    # Manual cks block + no marker block → duplication risk; require --force.
    if grep -nE '\[cks[^]]*\]' "$STATUSLINE_TARGET" >/dev/null 2>&1 && [ "$FORCE" -ne 1 ]; then
      die "Found an existing [cks ...] block in $STATUSLINE_TARGET but no marker block. Installing would duplicate it. Re-run with --force to inject anyway."
    fi
    determine_placement
  else
    bl=$(line_of_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
    el=$(line_of_fixed "$END_MARKER" "$STATUSLINE_TARGET")
    die "Marker pair invalid in $STATUSLINE_TARGET ($nb BEGIN, $ne END, BEGIN@${bl:-?}, END@${el:-?}). Resolve manually before re-running install."
  fi

  # e. Mutate atomically, then verify (both restore-on-failure).
  backup_target
  if ! mutate_file "$START_LINE" "$END_LINE" "$MARKER_BLOCK"; then
    restore_backup
    die "Marker injection failed; restored $STATUSLINE_TARGET from backup ($BACKUP)."
  fi
  verify_after_install

  # f. Confirm
  printf '✅ Installed.\n'
  printf -- '- %s\n' "$RENDERER_MSG"
  printf -- '- Marker block: %s in %s\n' "$PLACEMENT_MSG" "$STATUSLINE_TARGET"
  printf -- '- Backup: %s\n' "$BACKUP"
  printf 'Restart Claude Code (or reload the status line) to see [cks ...] in projects with .claude/knowledge or .claude/rules.\n'
  printf 'Tip: silence cks in a noisy project with `/knowledge-system:statusline disable` (run from inside it).\n'
}

# ---- enable / disable (per-project) -----------------------------------------

cmd_disable() {
  local proj="${CLAUDE_PROJECT_DIR:-$(pwd)}"
  mkdir -p "$proj/.claude" || die "Failed to create $proj/.claude"
  touch "$proj/.claude/.cks-statusline-off" || die "Failed to create the disable sentinel in $proj/.claude"
  printf '✅ cks disabled for %s. The renderer will skip output here. Re-enable with `enable`.\n' "$proj"
  printf 'Note: this is per-project — other projects keep showing [cks ...].\n'
}

cmd_enable() {
  local proj="${CLAUDE_PROJECT_DIR:-$(pwd)}"
  rm -f "$proj/.claude/.cks-statusline-off" 2>/dev/null || true
  printf '✅ cks enabled for %s.\n' "$proj"
  printf 'Note: enable does not install the marker block or renderer — run `install` for that.\n'
}

# ---- uninstall --------------------------------------------------------------

# Post-strip checks. orig_ok=1 means the file parsed cleanly before the strip,
# so a parse error now is ours → restore. Otherwise the file was already broken.
verify_after_uninstall() {
  local orig_ok=$1 nb ne err
  if [ ! -s "$STATUSLINE_TARGET" ]; then
    restore_backup
    die "Post-strip check failed: $STATUSLINE_TARGET is empty. Restored from backup ($BACKUP)."
  fi
  nb=$(count_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
  ne=$(count_fixed "$END_MARKER" "$STATUSLINE_TARGET")
  if [ "$nb" != "0" ] || [ "$ne" != "0" ]; then
    restore_backup
    die "Post-strip check failed: markers still present ($nb BEGIN / $ne END). Restored from backup ($BACKUP)."
  fi
  if ! bash -n "$STATUSLINE_TARGET" 2>/dev/null; then
    err=$(bash -n "$STATUSLINE_TARGET" 2>&1 || true)
    if [ "$orig_ok" = "1" ]; then
      restore_backup
      die "Post-strip check failed: strip introduced a syntax error. Restored from backup ($BACKUP). ($err)"
    fi
    printf 'WARNING: %s still has a syntax error after strip, but it was already broken before uninstall — not restoring. (%s)\n' "$STATUSLINE_TARGET" "$err"
  fi
  ensure_executable "$STATUSLINE_TARGET" post-write
}

cmd_uninstall() {
  require_python3 uninstall
  local removed_block=0 renderer_msg

  if [ -e "$STATUSLINE" ]; then
    resolve_target
    local nb ne bl el orig_ok
    nb=$(count_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
    ne=$(count_fixed "$END_MARKER" "$STATUSLINE_TARGET")
    if [ "$nb" = "0" ] && [ "$ne" = "0" ]; then
      printf 'Marker block not found in %s — nothing to strip.\n' "$STATUSLINE_TARGET"
    elif [ "$nb" = "1" ] && [ "$ne" = "1" ]; then
      bl=$(line_of_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
      el=$(line_of_fixed "$END_MARKER" "$STATUSLINE_TARGET")
      [ "$bl" -lt "$el" ] \
        || die "Marker pair invalid in $STATUSLINE_TARGET ($nb BEGIN, $ne END, BEGIN@$bl, END@$el). Resolve manually before re-running uninstall."
      orig_ok=0
      bash -n "$STATUSLINE_TARGET" 2>/dev/null && orig_ok=1
      backup_target
      if ! mutate_file "$bl" "$el"; then
        restore_backup
        die "Marker strip failed; restored $STATUSLINE_TARGET from backup ($BACKUP)."
      fi
      verify_after_uninstall "$orig_ok"
      removed_block=1
    else
      bl=$(line_of_fixed "$BEGIN_MARKER" "$STATUSLINE_TARGET")
      el=$(line_of_fixed "$END_MARKER" "$STATUSLINE_TARGET")
      die "Marker pair invalid in $STATUSLINE_TARGET ($nb BEGIN, $ne END, BEGIN@${bl:-?}, END@${el:-?}). Resolve manually before re-running uninstall."
    fi
  else
    printf 'No %s found — skipping marker removal.\n' "$STATUSLINE"
  fi

  # Remove the global renderer regardless of marker state (no -f: surface real
  # failures, but warn rather than abort).
  if [ -e "$INSTALLED" ]; then
    if rm "$INSTALLED" 2>/dev/null; then
      renderer_msg="renderer deleted ($INSTALLED)"
    else
      renderer_msg="WARNING: failed to delete renderer at $INSTALLED — remove manually"
    fi
  else
    renderer_msg="renderer not present ($INSTALLED)"
  fi

  printf '✅ Uninstalled.\n'
  [ "$removed_block" = "1" ] && printf -- '- Marker block removed from %s (backup: %s)\n' "$STATUSLINE_TARGET" "$BACKUP"
  printf -- '- %s\n' "$renderer_msg"
  printf -- '- Per-project sentinels (.cks-statusline-off) were left in place.\n'
}

# ---- argument parsing + dispatch --------------------------------------------

CMD=""
FORCE=0
for arg in "$@"; do
  case "$arg" in
    install|enable|disable|uninstall|status)
      [ -z "$CMD" ] && CMD="$arg" ;;
    --force)
      FORCE=1 ;;
    *)
      die "Unknown argument: $arg (expected: install | enable | disable | uninstall | status [--force])" ;;
  esac
done
[ -z "$CMD" ] && CMD="status"

case "$CMD" in
  status)    cmd_status ;;
  install)   cmd_install ;;
  enable)    cmd_enable ;;
  disable)   cmd_disable ;;
  uninstall) cmd_uninstall ;;
esac
