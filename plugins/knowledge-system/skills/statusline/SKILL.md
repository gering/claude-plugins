---
name: statusline
description: |
  Manages a `[cks N|M]` block in Claude Code's status line showing
  `.claude/rules/` and `.claude/knowledge/` file counts with dirty-state
  modifiers. Subcommands: install, enable, disable, uninstall, status.
  Per-project opt-out via `disable`.
  Trigger: "statusline cks", "show/hide cks", "knowledge status indicator".
user_invocable: true
---

# Knowledge System Status Line Integration

> Append `[cks RULES|KNOW]` to Claude Code's status line, with `*N` (tracked changes) / `+N` (untracked) modifiers when files are dirty.

## Arguments

`$ARGUMENTS` — one of: `install`, `enable`, `disable`, `uninstall`, `status` (default: `status`). `install` accepts a trailing `--force` to overwrite a pre-existing manual cks block or force a downgrade.

## Output format

`[cks 12|34]` — first column = `.claude/rules/**/*.md` count, second = `.claude/knowledge/**/*.md` count (both recursive, excluding `_index.md` / `README.md`). An optional third column appears when project-level `knowledge/_index.md` exists at the repo root (a legacy convention from layouts predating `.claude/knowledge`).

Each column may carry `*N` and `+N` suffixes from `git status --porcelain`:
- **`*N`** — *tracked changes*: any file under the path that has a non-empty index or worktree status (modified, added, deleted, renamed, copied, unmerged — staged or unstaged). Computed as "all porcelain lines minus untracked".
- **`+N`** — *untracked*: lines starting with `??`.

When neither `.claude/rules/` nor `.claude/knowledge/` exists in the project, the renderer exits silently with no output. The marker block's `[ -n "$CKS_PLUGIN" ]` guard then leaves `$OUT` unchanged, so unrelated projects show nothing — no dim placeholder, no whitespace artefact.

## Architecture

- **Renderer** lives in the plugin at `scripts/statusline-cks.sh` (source of truth). The version is a real bash declaration — `readonly CKS_STATUSLINE_VERSION="X.Y.Z"` — parsed by `install` to gate upgrades.
- **Install** copies the renderer to a stable user-global path: `~/.claude/cks-statusline.sh`. The copied file becomes the runtime source — its path is fixed regardless of where the plugin lives.
- **Marker block** in `~/.claude/statusline.sh` calls the stable path. Format:
  ```
  # >>> knowledge-system:cks-statusline >>>
  # ... block ...
  # <<< knowledge-system:cks-statusline <<<
  ```
- **Host script contract.** The marker block expects the host `statusline.sh` to define an `OUT` variable that accumulates the rendered status line (the marker block appends `$CKS_PLUGIN` to it). The block derives the workspace dir itself via `${DIR:-${CLAUDE_PROJECT_DIR:-$PWD}}` so it works whether or not the host script defines `$DIR`.
- **Per-project disable sentinel:** `<project>/.claude/.cks-statusline-off`. The renderer checks this first and exits silently when present, so noisy projects can opt out without touching the global setup.
- **Updates** are version-gated: re-running `install` compares the plugin's `CKS_STATUSLINE_VERSION` against the installed copy and only overwrites on upgrade (`--force` overrides).

## Instructions

### 0. Resolve paths and parse argument

- `STATUSLINE="${HOME}/.claude/statusline.sh"` (the configured path — may be a symlink)
- `STATUSLINE_TARGET="$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$STATUSLINE")"` (resolved real path — where we actually read/write). All mutations operate on `STATUSLINE_TARGET` so that atomic `os.replace`/`mv` lands on the same filesystem as the file we replace, and so that symlink-managed setups (stow/chezmoi) produce a diff in the right place.
- `INSTALLED="${HOME}/.claude/cks-statusline.sh"` (stable runtime path)
- `SOURCE="${CLAUDE_PLUGIN_ROOT}/scripts/statusline-cks.sh"` (plugin's source of truth)
- `BEGIN_MARKER="# >>> knowledge-system:cks-statusline >>>"`
- `END_MARKER="# <<< knowledge-system:cks-statusline <<<"`
- Parse `$ARGUMENTS` (default to `status`). Recognise `install`, `enable`, `disable`, `uninstall`, `status`, plus the `--force` flag for `install`.
- Helper — extract version from any script path:
  `grep -m1 '^readonly CKS_STATUSLINE_VERSION=' "$path" | sed -E 's/.*=[[:space:]]*"?([0-9]+\.[0-9]+\.[0-9]+)"?.*/\1/'`.
  Validate the result against `^[0-9]+\.[0-9]+\.[0-9]+$`. Treat missing or malformed as `0.0.0`.
- **Tooling note.** Use `python3` (available on macOS and all major Linux distros by default) for symlink resolution, file mutation, and post-write verification. **Preflight: `command -v python3 >/dev/null || { echo "python3 not on PATH — required by /statusline install"; exit 1; }`** in step 0 before any further work, so the dependency is surfaced cleanly rather than failing mid-mutation. Do **not** use `awk -v "$multiline_var"` for marker injection: BSD `awk` on macOS does not allow newlines inside `-v` variables, fails with `newline in string`, and emits nothing on stdout — if the caller redirects that into a tempfile and `mv`s it, the target is silently truncated to zero bytes (`bash -n ""` passes, so the syntax check does not catch it).

### 1. `status` (default)

Report a short checklist:
- **Plugin renderer:** `<SOURCE>` exists? Version?
- **Installed renderer:** `<INSTALLED>` exists? Version? (newer / equal / older / missing vs plugin)
- **Marker block:** present in `<STATUSLINE_TARGET>` (resolved real path from step 0)? Yes/no.
- **Project sentinel:** `<pwd>/.claude/.cks-statusline-off` present? (cks disabled for this project)

End with a hint about which subcommand fits the current state (e.g. "Run `install` to upgrade" if plugin > installed).

### 2. `install`

a. **Preflight (abort cleanly on each failure):**
   - `STATUSLINE` exists. Else stop: "No `~/.claude/statusline.sh` found. Set up your custom status line first (see `~/.claude/settings.json` → `statusLine.command`)."
   - `SOURCE` exists. Else stop with the resolved path and a hint to reinstall the plugin.
   - **Symlink note (informational):** if `[ -L "$STATUSLINE" ]`, tell the user "`<STATUSLINE>` is a symlink → resolved to `<STATUSLINE_TARGET>`. Edits will modify the link target." Then continue using `STATUSLINE_TARGET` for every check below.
   - `STATUSLINE_TARGET` exists. `os.path.realpath` returns a string even for broken symlink chains, so re-verify with `[ -e "$STATUSLINE_TARGET" ]`. Else stop: "Symlink `<STATUSLINE>` resolves to `<STATUSLINE_TARGET>` which does not exist (broken link chain?). Fix the symlink and re-run."
   - `STATUSLINE_TARGET` is writable. Else stop: "`<STATUSLINE_TARGET>` is not writable (permissions/ownership?). Fix and re-run."
   - **Executable bit on `STATUSLINE_TARGET`.** Claude Code's runtime exec's the status-line command directly via `execve`, which requires the `+x` bit on the resolved file (not just on the symlink). `bash -n` passes without `+x`, so the bug surfaces only when the actual status line renders. Check `[ -x "$STATUSLINE_TARGET" ]`:
     - If executable → ok, continue.
     - If not → auto-fix: `chmod +x "$STATUSLINE_TARGET"`. Report "Set executable bit on `<STATUSLINE_TARGET>` (was missing — would have caused silent statusline failure)." If `chmod` itself fails, stop with the stderr.

b. **Copy renderer to stable path** (version-gated, with explicit failure handling):
   - Read `SRC_VERSION` from `SOURCE` and `DEST_VERSION` from `INSTALLED` (default `0.0.0` when missing or malformed).
   - Compare via `sort -V`. Cases:
     - `INSTALLED` missing **or** `SRC_VERSION > DEST_VERSION`: `cp "$SOURCE" "$INSTALLED"` followed by `chmod +x "$INSTALLED"`. **If either command fails**, stop with the stderr — "Failed to install renderer to `<INSTALLED>`: `<error>`. Marker block was not touched." Do **not** proceed to step d.
     - `SRC_VERSION == DEST_VERSION`: report `"Renderer already current (v<SRC_VERSION>)"`, skip copy.
     - `DEST_VERSION > SRC_VERSION` (downgrade): warn "Installed v<DEST> is newer than plugin v<SRC>. Skipping copy. Use `--force` to overwrite." Honour `--force` if passed.

c. **Detect existing manual cks block:**
   - Grep `STATUSLINE_TARGET` for the regex `\[cks[^]]*\]` (literal `[cks` followed by anything up to `]`) **outside** the marker block range. If found AND marker block absent: warn about duplication, require `--force`.

d. **Inject or refresh marker block** (atomic, restorable):
   - Compute a session backup path **once**: `BACKUP="${STATUSLINE_TARGET}.bak-$(date +%s)"`. Run `cp "$STATUSLINE_TARGET" "$BACKUP"`. From here on, *any* failure between mutations and the final verify restores from `$BACKUP` and aborts.
   - Count occurrences of `$BEGIN_MARKER` and `$END_MARKER` in `STATUSLINE_TARGET`. They must be equal (both 0 or both 1).
     - **Both 1:** verify the BEGIN line number is strictly less than the END line number (otherwise the markers were manually re-ordered and `sed '/BEGIN/,/END/d'` would delete to EOF). If so, strip the existing block (lines between markers, inclusive) and treat the strip point as the insertion target. Skip placement priority below.
     - **Both 0:** apply placement priority (next bullet).
     - **Mismatched (0/1, 1/0, any count > 1, or BEGIN line ≥ END line):** stop with `"Marker pair invalid in <STATUSLINE_TARGET> (<n_begin> BEGIN, <n_end> END, BEGIN@<line>, END@<line>). Resolve manually before re-running install."` Do **not** mutate.
   - **Placement priority** (first match wins, only when no existing block was found):
     1. **User-placed placeholder:** grep for the regex `^[[:space:]]*#[[:space:]]*\{\{cks\}\}[[:space:]]*$`. **Count matches first.**
        - Exactly 1 match: record its line number as `PLACEHOLDER_LINE`. Then verify ordering — find the line number of the **last** `^[[:space:]]*OUT[+]?=` assignment in the file (matches `OUT="..."`, `OUT="$OUT ..."`, and `OUT+="..."` — the `[+]?` is critical, a literal `OUT=` regex would miss `OUT+=` and silently land the placeholder before subsequent appends). Call this `LAST_OUT_LINE`.
          - If `PLACEHOLDER_LINE > LAST_OUT_LINE` → ok, replace the placeholder line with the marker block. Report `"Placed at user-defined position (# {{cks}} placeholder on line <PLACEHOLDER_LINE>)"`.
          - If `PLACEHOLDER_LINE ≤ LAST_OUT_LINE` → stop with `"# {{cks}} placeholder on line <PLACEHOLDER_LINE> is at or before the last $OUT assignment (line <LAST_OUT_LINE>). The injected block does OUT=\"$OUT $CKS_PLUGIN\" — placing it earlier means the subsequent OUT= overwrites it and cks silently disappears. Move the placeholder after line <LAST_OUT_LINE> and re-run install."` Do **not** mutate.
          - If no `OUT=` line found at all → host script does not use the `$OUT` convention. Stop with `"Could not find any OUT= assignment in <STATUSLINE_TARGET>. The injected block appends to $OUT — your statusline must define it. See the snippet in the SKILL.md for the contract."`
        - **>1 match:** stop with `"Multiple # {{cks}} placeholders found (lines N, M, ...). Keep only one and re-run install."` Do **not** mutate.
        - 0 matches: fall through to step 2.
     2. **Auto-detect fallback:** find the last line matching `(echo[[:space:]].*\$OUT|printf[[:space:]].*\$OUT)` and insert the marker block immediately **before** it. Report `"Auto-placed before <matched-line> (line N). Add a # {{cks}} comment to your statusline.sh if you want a different position."`. Caveat: this is a heuristic — verify the result and prefer the placeholder for stability.
     3. **Neither found:** stop with guidance — print the marker block snippet and tell the user to either add `# {{cks}}` to their statusline.sh and re-run install, or paste the snippet manually.
   - **Mutation pattern (use `python3`, never `awk`).** Compute the new file contents in Python so the multi-line marker block survives intact, then atomically replace the target.
   - **Line-number discovery** (before invoking the Python recipe — pass these in as env vars):
     - **Placeholder path:** `INSERT_LINE=$(grep -nE '^[[:space:]]*#[[:space:]]*\{\{cks\}\}[[:space:]]*$' "$STATUSLINE_TARGET" | head -1 | cut -d: -f1)`; `START_LINE=$INSERT_LINE`, `END_LINE=$INSERT_LINE` (single-line replacement).
     - **Auto-detect path:** find the last `(echo|printf).*\$OUT` line: `INSERT_LINE=$(grep -nE '(echo|printf)[[:space:]].*\$OUT' "$STATUSLINE_TARGET" | tail -1 | cut -d: -f1)`; the marker is inserted **before** this line, so `START_LINE=$INSERT_LINE`, `END_LINE=$((INSERT_LINE - 1))` (empty range; the recipe handles `START > END` as pure insertion).
     - **Refresh path** (existing marker block, both counts = 1): `START_LINE=$(grep -nF "$BEGIN_MARKER" "$STATUSLINE_TARGET" | head -1 | cut -d: -f1)`; `END_LINE=$(grep -nF "$END_MARKER" "$STATUSLINE_TARGET" | head -1 | cut -d: -f1)`.
     - Validate all three are non-empty positive integers before invoking Python; abort with a clear message if any is empty (this catches grep misfires).
   - **Recipe:**

     ```bash
     MARKER=$(cat <<'EOF'
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
     )
     PATH_TARGET="$STATUSLINE_TARGET" MARKER="$MARKER" \
       START_LINE="$START_LINE" END_LINE="$END_LINE" \
       python3 - <<'PY'
     import os, sys
     path = os.environ['PATH_TARGET']
     tmp = path + f".tmp.{os.getpid()}"
     try:
         marker = os.environ['MARKER']
         start = int(os.environ['START_LINE'])  # 1-based, inclusive
         end = int(os.environ['END_LINE'])      # 1-based, inclusive; pass start-1 for pure-insert
         lines = open(path).read().splitlines(keepends=True)
         # MARKER from $() loses its trailing newline; restore one so the
         # inserted block does not collide with the following line.
         block = marker + ("\n" if not marker.endswith("\n") else "")
         new_lines = lines[:start-1] + [block] + lines[end:]  # end is inclusive
         with open(tmp, 'w') as f: f.writelines(new_lines)
         os.replace(tmp, path)  # atomic on same filesystem; PATH_TARGET is the resolved real path
     except Exception as e:
         # Clean up the temp file on any failure — don't leave .tmp.<pid> debris.
         try: os.unlink(tmp)
         except FileNotFoundError: pass
         print(f"FATAL: marker injection failed: {type(e).__name__}: {e}", file=sys.stderr)
         sys.exit(1)
     PY
     ```

     If the Python invocation exits non-zero (`$? != 0`), restore from `$BACKUP` and abort with the Python stderr line.

     Do **not** use `awk -v "$MARKER"` — BSD `awk` rejects newlines in `-v` values and emits nothing on stdout, so a redirect-then-mv pipeline silently truncates the target to zero bytes.
   - **Marker block content** (written exactly):

     ```bash
     # >>> knowledge-system:cks-statusline >>>
     # Managed by /knowledge-system:statusline. Do not edit between markers —
     # run `/knowledge-system:statusline uninstall` to remove cleanly.
     if [ -x "$HOME/.claude/cks-statusline.sh" ]; then
       _CKS_DIR="${DIR:-${CLAUDE_PROJECT_DIR:-$PWD}}"
       CKS_PLUGIN=$(bash "$HOME/.claude/cks-statusline.sh" "$_CKS_DIR" 2>/dev/null)
       [ -n "$CKS_PLUGIN" ] && OUT="$OUT $CKS_PLUGIN"
     fi
     # <<< knowledge-system:cks-statusline <<<
     ```

     Notes on the snippet:
     - The renderer path is hard-coded to `$HOME/.claude/cks-statusline.sh` — no plugin-path dependency, survives plugin moves.
     - `_CKS_DIR` falls back through `${DIR:-${CLAUDE_PROJECT_DIR:-$PWD}}` so the snippet works with whichever convention the host script uses, with `$PWD` as a final safety net.
     - The block assumes `$OUT` exists. If the host script uses a differently-named accumulator, the cks block will silently produce nothing visible — see "Edge cases".

e. **Verify** (post-write sanity checks, in this order — restore from `$BACKUP` and abort on the first failure):
   1. **Non-empty:** `[ -s "$STATUSLINE_TARGET" ]`. A zero-byte file would pass `bash -n` (empty script is valid bash) and silently break the status line. This is the failsafe against a broken mutation step that wrote nothing.
   2. **Marker count:** exactly one `$BEGIN_MARKER` and one `$END_MARKER` in `STATUSLINE_TARGET`, and `BEGIN line < END line`. Anything else means the mutation produced an inconsistent state.
   3. **Syntax:** `bash -n "$STATUSLINE_TARGET"`. On parse failure, restore from `$BACKUP` and abort with the parser error.
   4. **Executable bit retained:** `[ -x "$STATUSLINE_TARGET" ]`. `os.replace` preserves the destination's mode, but check defensively in case the user's editor/linter ran between cp-backup and verify and dropped the bit. If missing, `chmod +x "$STATUSLINE_TARGET"` **and report** "Re-set executable bit on `<STATUSLINE_TARGET>` (was dropped during write)." — never silently.

f. **Confirm:**
   > "✅ Installed.
   > - Renderer: `<INSTALLED>` (v<SRC_VERSION>)
   > - Marker block: injected into `<STATUSLINE_TARGET>` (via placeholder / auto-detect)
   > - Backup: `<BACKUP>`
   > Restart Claude Code (or reload status line) to see `[cks ...]` for projects with `.claude/knowledge` or `.claude/rules`.
   > Tip: silence cks in a noisy project with `/knowledge-system:statusline disable` (run from inside that project)."

### 3. `disable` (per-project)

- Determine current project: use `$CLAUDE_PROJECT_DIR` if set, else `pwd`.
- Ensure `.claude/` exists in the project (create if absent — same convention as `/init`).
- Create `<project>/.claude/.cks-statusline-off` (`touch`).
- Confirm: `"✅ cks disabled for <project>. Renderer will skip output here. Re-enable with `enable`."`
- Reminder: this is per-project. Other projects keep showing `[cks ...]`.

### 4. `enable` (per-project)

- Determine current project (same as above).
- Remove `<project>/.claude/.cks-statusline-off` if present (idempotent).
- Confirm: `"✅ cks enabled for <project>."`
- Note: `enable` does **not** install the marker block or renderer — run `install` for that.

### 5. `uninstall`

a. Marker block in `STATUSLINE_TARGET` (resolved real path from step 0):
   - Count `$BEGIN_MARKER` and `$END_MARKER` occurrences first.
     - **Both 0:** report `"Nothing to uninstall — marker block not found in <STATUSLINE_TARGET>."` Skip step b's renderer removal? **No — still remove the renderer** (step b) since it's the global asset. But skip the backup and the strip.
     - **Both 1:** verify the BEGIN line number is strictly less than the END line number (protects against re-ordered markers — `sed '/BEGIN/,/END/d'` would otherwise delete to EOF). Only then proceed.
     - **Mismatched (1/0, 0/1, any count > 1, or BEGIN line ≥ END line):** stop with `"Marker pair invalid in <STATUSLINE_TARGET> (<n_begin> BEGIN, <n_end> END, BEGIN@<line>, END@<line>). Resolve manually before re-running uninstall."` Do **not** touch the file.
   - Compute `BACKUP="${STATUSLINE_TARGET}.bak-$(date +%s)"`, run `cp "$STATUSLINE_TARGET" "$BACKUP"`.
   - **Strip via `python3`** (not `sed -i`, not `awk` — same BSD/macOS reasoning as install). Recipe: read lines, delete the closed range `BEGIN_LINE..END_LINE` inclusive, write to a sibling temp file on the same filesystem, then `os.replace` over `STATUSLINE_TARGET`. Restore from `$BACKUP` on any exception.
   - **Verify** (same post-mutation checks as install step 2e):
     1. `[ -s "$STATUSLINE_TARGET" ]` non-empty.
     2. Zero `$BEGIN_MARKER` and zero `$END_MARKER` remaining.
     3. `bash -n "$STATUSLINE_TARGET"`: if it fails *and the original passed `bash -n`*, restore from `$BACKUP`; otherwise surface the parse error to the user (the file was already broken before uninstall).
     4. Executable bit still set on `STATUSLINE_TARGET` — `chmod +x` if dropped.

b. Remove the installed renderer: if `[ -e "$INSTALLED" ]`, run `rm "$INSTALLED"` (no `-f` — surface real failures). On error, warn but do not abort uninstall.

c. Per-project sentinels are **not** touched. They remain harmless (no renderer to check them).

d. Confirm: `"✅ Uninstalled. Marker block removed, renderer deleted, backup at <BACKUP>. Per-project sentinels were left in place."`

## Edge cases

- User has no custom `~/.claude/statusline.sh` (default Claude Code status line): `install` aborts with guidance. We do not synthesise a status line from scratch.
- Existing manual `[cks ...]` block: warn and require `--force` to avoid duplicates.
- Plugin renderer is older than installed (downgrade scenario): skip by default, `--force` overrides.
- Project has neither `.claude/rules` nor `.claude/knowledge`: renderer exits silently with empty output — no cks marker appears in the status line for that project.
- Project has the disable sentinel: renderer exits silently. Marker block's `[ -n "$CKS_PLUGIN" ]` guard leaves `$OUT` unchanged.
- Host `statusline.sh` uses an accumulator variable other than `$OUT` (e.g. `$STATUS`, `$LINE`): the injected snippet appends to `$OUT` which the host then ignores → silent no-op. The auto-detect step searches for `echo … $OUT`, so this case typically gets caught and aborts at placement step 3. If it slips through (e.g. user placed `# {{cks}}` despite differently-named accumulator), they'll see no cks block; documented in step d's snippet notes.
- `STATUSLINE` is a symlink (managed via stow/chezmoi): install warns but proceeds — edits modify the link target. Users with dotfiles management should expect a diff in their managed repo.
- Multiple `# {{cks}}` placeholders: install aborts before any mutation (see step d.1).
- Mismatched `BEGIN`/`END` marker counts (corrupted prior install): install and uninstall both refuse to mutate and ask the user to resolve manually — this protects against catastrophic `sed` deletion.

## Custom placement

The `# {{cks}}` placeholder gives you full control over where the cks block appears. Drop the comment line into `~/.claude/statusline.sh` exactly where you want the block, then run `install`:

```bash
# In ~/.claude/statusline.sh:

# ... your existing logic that builds $OUT ...
OUT="${MODEL_COLOR}[${MODEL}]${RESET}"

# {{cks}}                              ← cks block will land here

[ -n "$BRANCH" ] && OUT="$OUT $BRANCH"
echo -e "$OUT"
```

After `install`, the placeholder line is replaced by the marker block in place. `uninstall` strips the marker block but does **not** restore the placeholder — re-add `# {{cks}}` if you plan to reinstall.

If no placeholder is found, `install` falls back to placing the marker block immediately before the last line that prints `$OUT`.

## Other statusline tools

The renderer at `~/.claude/cks-statusline.sh` is independently usable — its contract is simple (one arg = workspace dir, stdout = ANSI-coloured block, no trailing newline). If you use a third-party statusline tool (ccstatusline, CCometixLine, ccusage, custom wrappers), call the renderer directly from there instead of relying on the marker block. Most tools support a "custom command" slot that takes a shell snippet like:

```bash
bash "$HOME/.claude/cks-statusline.sh" "$DIR"
```

In that case: still run `install` once to copy the renderer into place (and to keep version updates flowing), but you can skip the marker-block injection — just don't add `# {{cks}}` and accept the auto-detect fallback warning, or remove the marker block manually after install.

## Notes

- Renderer is read-only — never writes to the project. Counts come from `find`; modifier flags from `git status --porcelain`.
- The script's `readonly CKS_STATUSLINE_VERSION` line is the single source of truth for upgrade decisions. When changing the renderer's behaviour, bump that version **and** the plugin's `version` in `plugin.json`.
- `/init` mentions this skill as an optional enhancement.
