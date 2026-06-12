# Shared Documentation-Readiness Checks

> Canonical definitions for the four documentation-readiness checks shared by
> `/open` (pre-PR, step 3c–3f) and `/merge` (pre-merge, step 8): README
> freshness, version bump, changelog, knowledge/conventions. Defines the
> detection signals, the ✅/⚠️/❌/➖ semantics, and the auto-fixable vs. manual
> split. Consumers add only their stage-specific behavior — they do not
> redefine the checks here.

## Status semantics

Every check resolves to exactly one of:

- ✅ **pass** — requirement met, or correctly not applicable to *these* changes
- ⚠️ **warning** — a gap that needs a decision (auto-fix or accept). These
  doc checks emit ⚠️, never ❌ — a stale doc never hard-blocks.
- ➖ **N/A** — the project doesn't use this convention; nothing to check
- ❌ **blocker** — reserved; the documentation checks do not produce blockers

## Shared inputs

All four checks read the branch diff against the base:

    git diff origin/<BASE_BRANCH>...HEAD --name-only

- **User-facing changes** — the diff touches user-visible code (`src/`,
  `lib/`, `plugins/`, `skills/`, public entry points), or commit messages
  describe `feat` / `fix` / `breaking`.
- **Internal only** — tests, configs, refactors, comments. Internal-only
  changes resolve every check below to ✅ ("no action needed").

## The checks

### 3c · README freshness  —  *manual*

- **Signal:** user-facing code changed, but `README.md` itself was not
  touched. Other `*.md` files (CONTRIBUTING, CHANGELOG, …) do **not** count as
  a README update.
- ✅ `README.md` was touched, or changes are internal only
- ⚠️ "Code changed but docs untouched — README may be stale"
- ➖ no `README.md` exists
- **Classification: manual** — prose can't be auto-written; needs human
  judgment about what to document.

### 3d · Version bump  —  *auto-fixable*

- **First detect whether the project versions releases** (warn only if it
  does). Any of:
  - `package.json` / `plugin.json` / `Cargo.toml` / `pyproject.toml` /
    `*.csproj` with a version field that changed in the last ~10 commits
  - semver git tags: `git tag --sort=-v:refname | head -5`
  - `.changeset/`, `release-please` config, or similar release automation
- **No versioning signal** → ➖ "N/A (project does not appear to version
  releases)"
- **Versioned** — check the diff for a bump:
  `git diff origin/<BASE_BRANCH>...HEAD -- '**/package.json' '**/plugin.json' '**/Cargo.toml' '**/pyproject.toml'`
  - ✅ version was bumped — "Version bumped to <new>"
  - ✅ not bumped, changes internal only — "No bump needed (internal)"
  - ⚠️ not bumped, changes user-facing → "Version bump may be needed.
    Detected: <feat|fix|breaking>. Suggest: <patch|minor|major>"
- **Monorepo / multi-package awareness:** if several version files exist
  (e.g. each plugin's `plugin.json` + a root `marketplace.json`), check which
  one(s) the diff affects and remind per package — keep them in sync.
- Respect repo-specific semver conventions from memory / CLAUDE.md (e.g.
  "patch for small changes, minor for new features").
- **Classification: auto-fixable** — bump the detected field(s) per the
  suggestion; update every in-sync file together.

### 3e · Changelog  —  *auto-fixable*

- **Detect presence:** `CHANGELOG.md`, `CHANGELOG`, `HISTORY.md`,
  `RELEASES.md`, `docs/changelog/`, `.changeset/`
- **None** → ➖ "N/A (no changelog)"
- **Exists** — check whether it was updated on this branch:
  `git diff origin/<BASE_BRANCH>...HEAD --name-only | grep -iE '(changelog|history|releases|\.changeset/)'`
  - ✅ updated, or changes internal only
  - ⚠️ not updated, changes user-facing → "Changelog unchanged — add an
    entry for this PR". Suggest the section from the change type: `feat` →
    Added, `fix` → Fixed, `breaking` → Changed/Removed.
- **Link to version bump (3d):** if the version bumped but the changelog
  didn't (or vice versa), flag the inconsistency explicitly.
- **Classification: auto-fixable** — draft an entry under the unreleased /
  next-version heading (use Keep-a-Changelog sections if that format is
  detected from headings).

### 3f · Knowledge / conventions  —  *auto-fixable*

- **Detect a conventions/knowledge location** (system-agnostic — any of):
  - `.claude/knowledge/`, `.claude/rules/` (gering `knowledge-system`)
  - `.cursor/rules/`, `.cursorrules` (Cursor)
  - `.github/copilot-instructions.md` (Copilot)
  - `AGENTS.md`, `CONVENTIONS.md`, `CONTRIBUTING.md`
  - `docs/adr/`, `docs/decisions/` (ADRs)
  - `CLAUDE.md` with documented conventions beyond setup
- **None** → ➖ "N/A (no knowledge/convention system detected)"
- **Exists** — does the branch introduce **new patterns, conventions, or
  generalizable fixes**? Heuristic: commit messages like "add <pattern>",
  "refactor to <approach>", "fix <recurring bug>", or many similar files
  changed the same way.
  - ✅ knowledge location was touched — "Conventions documented"
  - ✅ nothing generalizable — "No new patterns to capture"
  - ⚠️ new patterns likely but knowledge location untouched → "Knowledge gap"
- **Auto-fix target:** if `knowledge-system` is detected, invoke `/curate`
  with the detected pattern; otherwise append to the closest topical file
  (fallback: the generic `AGENTS.md` / `CONVENTIONS.md`).
- **Classification: auto-fixable** — unless the new pattern *conflicts* with
  an existing documented rule, which needs judgment (then it's a manual ⚠️).

## Auto-fixable vs. manual — summary

| Check | Classification | Auto-fix action |
|---|---|---|
| 3c README freshness | manual | — (human writes the prose) |
| 3d Version bump | auto-fixable | bump version field(s); keep multi-package in sync |
| 3e Changelog | auto-fixable | draft entry under unreleased / next-version heading |
| 3f Knowledge / conventions | auto-fixable | `/curate`, or append to closest knowledge file |

## How consumers apply this

The checks above are identical for both skills. **Only the stage behavior
differs** — that, and nothing else, lives in the skills:

- **`/open` step 3c–3f (pre-PR) — auto-resolve in place.** Per its
  "auto-resolve warnings where feasible" principle, `/open` applies each
  **auto-fixable** fix *immediately during the check phase* (bump the
  version, draft the changelog entry, `/curate` the knowledge gap) so it
  rarely surfaces as a warning. Only **manual** gaps (README) and fixes that
  genuinely need judgment reach the step-4 decision as ⚠️.
- **`/merge` step 8 (pre-merge) — read-only.** `/merge` **never mutates
  here.** It collects every ⚠️ into `DOC_WARNINGS` (tagged auto-fixable vs.
  manual) and surfaces them in the final plan (step 13), where the user picks
  `[f]` fix / `[m]` merge anyway / `[a]` abort. Auto-fix runs only after `[f]`
  — and then commits locally + hands off to `/cycle`, never pushing directly.
