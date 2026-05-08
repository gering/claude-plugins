---
name: statusline
description: |
  Manages a `[cks N|M]` block in Claude Code's status line showing
  `.claude/rules/` and `.claude/knowledge/` file counts with dirty-state
  modifiers. Subcommands: install, enable, disable, uninstall, status.
  Trigger: "statusline cks", "show/hide cks", "knowledge status indicator".
user_invocable: true
---

# Knowledge System Status Line Integration

> Append `[cks RULES|KNOW]` to Claude Code's status line, with `*mod` / `+untracked` modifiers when files are dirty.

## Arguments

`$ARGUMENTS` — one of: `install`, `enable`, `disable`, `uninstall`, `status` (default: `status`). `install` accepts a trailing `--force` to overwrite a pre-existing manual cks block.

## Output format

`[cks 12|34]` — first column = `.claude/rules/*.md` count, second = `.claude/knowledge/**/*.md` count (excluding `_index.md` / `README.md`). A third column appears when project-level `knowledge/_index.md` exists. Each column may carry `*N` (yellow, modified) and `+N` (green, untracked) suffixes from `git status --porcelain`.

When neither `.claude/rules/` nor `.claude/knowledge/` exists in the project, the block falls back to a dim `cks` placeholder.

## Architecture

- **Renderer** lives in the plugin at `scripts/statusline-cks.sh` (source of truth, versioned via `CKS_STATUSLINE_VERSION=X.Y.Z` header).
- **Install** copies the renderer to a stable user-global path: `~/.claude/cks-statusline.sh`. The copied file becomes the runtime source for the status line — its path is fixed regardless of where the plugin lives.
- **Marker block** in `~/.claude/statusline.sh` calls the stable path. Format:
  ```
  # >>> knowledge-system:cks-statusline >>>
  # ... block ...
  # <<< knowledge-system:cks-statusline <<<
  ```
- **Per-project disable sentinel:** `<project>/.claude/.cks-statusline-off`. The renderer checks this first and exits silently when present, so noisy projects can opt out without touching the global setup.
- **Updates** are version-gated: re-running `install` compares the plugin's `CKS_STATUSLINE_VERSION` against the installed copy and only overwrites on upgrade.

## Instructions

### 0. Resolve paths and parse argument

- `STATUSLINE="${HOME}/.claude/statusline.sh"`
- `INSTALLED="${HOME}/.claude/cks-statusline.sh"` (stable runtime path)
- `SOURCE="${CLAUDE_PLUGIN_ROOT}/scripts/statusline-cks.sh"` (plugin's source of truth)
- `BEGIN_MARKER="# >>> knowledge-system:cks-statusline >>>"`
- `END_MARKER="# <<< knowledge-system:cks-statusline <<<"`
- Parse `$ARGUMENTS` (default to `status`). Recognise `install`, `enable`, `disable`, `uninstall`, `status`, plus the `--force` flag for `install`.
- Helper — extract version from any script path: `grep -m1 '^# CKS_STATUSLINE_VERSION=' "$path" | cut -d= -f2 | tr -d '[:space:]'`. Treat missing as `0.0.0`.

### 1. `status` (default)

Report a short checklist:
- **Plugin renderer:** `<SOURCE>` exists? Version?
- **Installed renderer:** `<INSTALLED>` exists? Version? (newer / equal / older / missing vs plugin)
- **Marker block:** present in `<STATUSLINE>`? Yes/no.
- **Project sentinel:** `<pwd>/.claude/.cks-statusline-off` present? (cks disabled for this project)

End with a hint about which subcommand fits the current state (e.g. "Run `install` to upgrade" if plugin > installed).

### 2. `install`

a. **Preflight:**
   - `STATUSLINE` exists. Else stop: "No `~/.claude/statusline.sh` found. Set up your custom status line first (see `~/.claude/settings.json` → `statusLine.command`)."
   - `SOURCE` exists. Else stop with the resolved path and a hint to reinstall the plugin.
   - `STATUSLINE` is writable.

b. **Copy renderer to stable path** (version-gated):
   - Read `SRC_VERSION` from `SOURCE` and `DEST_VERSION` from `INSTALLED` (defaulting to `0.0.0` if missing).
   - Compare via `sort -V` (or simple per-component compare). If `SRC_VERSION > DEST_VERSION` **or** `INSTALLED` is missing: `cp "$SOURCE" "$INSTALLED"` and `chmod +x "$INSTALLED"`. Report `"Renderer updated: <DEST_VERSION> → <SRC_VERSION>"` (or `"Renderer installed at <INSTALLED> (v<SRC_VERSION>)"`).
   - If equal: report `"Renderer already current (v<SRC_VERSION>)"`, skip copy.
   - If `DEST_VERSION > SRC_VERSION` (downgrade): warn — "Installed v<DEST> is newer than plugin v<SRC>. Skipping copy. Use `--force` to overwrite." Honour `--force` if passed.

c. **Detect existing manual cks block** (only relevant for the marker injection, not the renderer copy):
   - Grep `STATUSLINE` for `\[cks ` outside the marker block. If found AND marker block absent: warn about duplication, require `--force`.

d. **Inject or refresh marker block:**
   - Backup once per session: `cp "$STATUSLINE" "${STATUSLINE}.bak-$(date +%s)"`.
   - If marker block exists: strip it (sed between `BEGIN_MARKER` and `END_MARKER` inclusive) and re-insert. (Lets us refresh content if we ever change the snippet.) Skip the placement logic below — the existing markers define the position.
   - **Placement priority** (first match wins):
     1. **User-placed placeholder:** grep for a line matching the regex `^[[:space:]]*#[[:space:]]*\{\{cks\}\}[[:space:]]*$` (literal `# {{cks}}` with optional surrounding whitespace). If found, **replace that line** with the marker block. This is the explicit-placement contract — users put `# {{cks}}` exactly where they want the cks block to appear. Report `"Placed at user-defined position (# {{cks}} placeholder on line N)"`.
     2. **Auto-detect fallback:** find the last `echo` line that prints `$OUT` (e.g. `echo -e "$OUT"` / `echo "$OUT"` / `printf … "$OUT"`) and insert **before** that line. Report `"Auto-placed before final $OUT echo (line N). Add a # {{cks}} comment if you want a different position."`.
     3. **Neither found:** stop with guidance — print the marker block snippet and tell the user to either add `# {{cks}}` to their statusline.sh and re-run install, or paste the snippet manually.
   - Insert the marker block:

     ```bash
     # >>> knowledge-system:cks-statusline >>>
     # Managed by /knowledge-system:statusline. Do not edit between markers —
     # run `/knowledge-system:statusline uninstall` to remove cleanly.
     if [ -x "$HOME/.claude/cks-statusline.sh" ]; then
       CKS_PLUGIN=$(bash "$HOME/.claude/cks-statusline.sh" "$DIR" 2>/dev/null)
       [ -n "$CKS_PLUGIN" ] && OUT="$OUT $CKS_PLUGIN"
     fi
     # <<< knowledge-system:cks-statusline <<<
     ```

     Note the path is hard-coded to `$HOME/.claude/cks-statusline.sh` — no plugin-path dependency, survives plugin moves.

e. **Verify:** `bash -n "$STATUSLINE"`. On parse failure: restore from backup, abort with the parser error.

f. **Confirm:**
   > "✅ Installed.
   > - Renderer: `<INSTALLED>` (v<SRC_VERSION>)
   > - Marker block: injected into `<STATUSLINE>`
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

a. Marker block in `STATUSLINE`:
   - Backup: `cp "$STATUSLINE" "${STATUSLINE}.bak-$(date +%s)"`.
   - Strip block between markers (inclusive).
   - `bash -n "$STATUSLINE"` — restore on failure.

b. Remove the installed renderer: `rm -f "$INSTALLED"`.

c. Per-project sentinels are **not** touched. They remain harmless (no renderer to check them).

d. Confirm: `"✅ Uninstalled. Marker block removed, renderer deleted, backup at <STATUSLINE>.bak-…. Per-project sentinels were left in place."`

## Edge cases

- User has no custom `~/.claude/statusline.sh` (default Claude Code status line): `install` aborts with guidance. We do not synthesise a status line from scratch.
- Existing manual `[cks ...]` block: warn and require `--force` to avoid duplicates.
- Plugin renderer is older than installed (downgrade scenario): skip by default, `--force` overrides.
- Project has neither `.claude/rules` nor `.claude/knowledge`: renderer prints a dim `cks` placeholder.
- Project has the disable sentinel: renderer exits with empty output. The marker block then evaluates `[ -n "$CKS_PLUGIN" ]` as false and leaves `$OUT` unchanged — no whitespace artefact.

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
- The version header is the single source of truth for upgrade decisions. When changing the renderer's behaviour, bump `CKS_STATUSLINE_VERSION` in the script header **and** the plugin's `version` in `plugin.json`.
- `/init` mentions this skill as an optional enhancement.
