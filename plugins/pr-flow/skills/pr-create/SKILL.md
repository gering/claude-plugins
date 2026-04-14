---
name: pr-create
description: Create a PR after verifying readiness — README, knowledge docs, tests, linter, build — then detect whether CI and @claude review auto-trigger
user_invocable: true
---

# Create Pull Request

> Pre-flight readiness checks (docs, tests, lint, build) → create the PR with a generated title/body → verify CI and @claude review auto-trigger → recommend next step.

## Arguments

- `$ARGUMENTS` - Optional: custom PR title. If omitted, one is generated from commits.

## Instructions

0. **Preflight (tooling)**:
   - Verify `gh` installed and authenticated (see `/pr-cycle` step 0 for exact commands). Stop with clear error if missing.

1. **Branch & PR state**:
   - Run: `git branch --show-current`
   - If on `main`/`master`, stop: "You're on the main branch. Create a feature branch first."
   - Run: `gh pr view --json number,url 2>/dev/null`
   - If a PR already exists, stop: "PR #<N> already exists: <URL>. Use `/pr-cycle` to push updates."
   - Detect base branch: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'` (fallback: ask user or default to `main`)

2. **Check if rebase is needed** — delegate to `/pr-rebase`:
   - Invoke the `/pr-rebase` skill. Since no PR exists yet, it will fall back to upstream tracking or repo default to detect the base branch.
   - If `/pr-rebase` rebased successfully → continue
   - If user declined the rebase → continue but remember to flag ⚠️ "Branch is N commits behind `<BASE_BRANCH>` (rebase declined)" in the readiness summary (step 4)
   - If conflicts aborted the rebase → stop this skill; user resolves manually, then re-runs `/pr-create`

3. **Readiness checks — collect a status list**:
   Run each check and collect results into a summary table. **Do NOT stop on failure** — present everything, let the user decide.

   ### 3a. Uncommitted changes
   - Run: `git status --porcelain`
   - If changes exist → ⚠️ "Uncommitted changes — commit before creating PR"
   - Offer: "Want me to run `/pr-cycle` instead (handles commit + push + review)?" — if yes, abort this skill

   ### 3b. Unpushed commits (for branch-existence check later)
   - Run: `git log @{u}..HEAD --oneline 2>/dev/null` — fine if it errors (upstream may not exist yet)

   ### 3c. README freshness heuristic
   - Run: `git diff origin/<BASE_BRANCH>...HEAD --name-only`
   - If the diff touches user-visible code (`src/`, `lib/`, `plugins/`, `skills/`, public entry points) but NOT `README.md` or `*.md` → ⚠️ "Code changed but docs untouched — README may be stale"
   - If `README.md` WAS touched → ✅
   - If only internal changes (tests, configs, refactors) → ➖ "N/A"

   ### 3d. Version bump reminder (only if versioning is an established convention)
   - **First detect whether this project versions releases** — do NOT warn on projects that don't. Signals:
     - `package.json` has a `version` field AND git log shows prior version bumps (e.g. commits like "bump to 1.2.3", "v1.2.3", or version-field changes in recent history)
     - `plugin.json` / `pyproject.toml` / `Cargo.toml` / `*.csproj` with a version field that has changed in the last ~10 commits
     - Git tags following semver (`git tag --sort=-v:refname | head -5`)
     - `.changeset/` directory, `release-please` config, or similar release-automation
   - If **no** versioning signal → ➖ "N/A (project does not appear to version releases)"
   - If versioning is used:
     - Check whether any version field was bumped on this branch vs. base:
       `git diff origin/<BASE_BRANCH>...HEAD -- '**/package.json' '**/plugin.json' '**/Cargo.toml' '**/pyproject.toml'`
     - If version was bumped → ✅ "Version bumped to <new>"
     - If NOT bumped AND the changes look user-facing (new feature, bug fix, breaking change — inferred from commit messages/diff) → ⚠️ "Version bump may be needed. Detected: <feat|fix|breaking>. Suggest: <patch|minor|major>"
     - If NOT bumped AND changes are internal only (tests, docs, refactor) → ✅ "No bump needed (internal changes)"
   - **Monorepo / multi-package awareness**: if multiple version files exist (e.g. this marketplace's `plugin.json` per plugin + root `marketplace.json`), check which one(s) are affected by the diff and remind per-package
   - Respect any repo-specific semver conventions found in memory/CLAUDE.md (e.g. "patch for small changes, minor for new features")

   ### 3e. Release notes / changelog (only if the project maintains one)
   - Detect presence: `CHANGELOG.md`, `CHANGELOG`, `HISTORY.md`, `RELEASES.md`, `docs/changelog/`, `.changeset/`
   - If none exist → ➖ "N/A (no changelog)"
   - If a changelog exists:
     - Check whether it was updated on this branch: `git diff origin/<BASE_BRANCH>...HEAD --name-only | grep -iE '(changelog|history|releases|\.changeset/)'`
     - If updated → ✅ "Changelog updated"
     - If NOT updated AND changes look user-facing → ⚠️ "Changelog unchanged — add an entry for this PR"
       - Also suggest the section: did the version bump (2d) indicate `feat` → Added, `fix` → Fixed, `breaking` → Changed/Removed?
       - If the changelog follows Keep-a-Changelog format (detect by headings), offer: "Want me to draft an entry?"
     - If internal only → ✅ "No changelog entry needed (internal changes)"
   - Link 2d ↔ 2e: if version bumped but changelog not, or vice versa, flag the inconsistency explicitly

   ### 3f. Project knowledge / conventions (system-agnostic)
   - Detect whether the project maintains a place for conventions, rules, or learnings. Any of:
     - `.claude/knowledge/`, `.claude/rules/` (gering `knowledge-system`)
     - `.cursor/rules/`, `.cursorrules` (Cursor)
     - `.github/copilot-instructions.md` (Copilot)
     - `AGENTS.md`, `CONVENTIONS.md`, `CONTRIBUTING.md`
     - `docs/adr/`, `docs/decisions/` (ADRs)
     - `CLAUDE.md` with documented conventions beyond setup
   - If none detected → ➖ "N/A (no knowledge/convention system detected)"
   - If detected:
     - Check if this branch introduces **new patterns, new conventions, or generalizable fixes** (heuristic: commit messages like "add <new pattern>", "refactor to <new approach>", "fix <recurring bug>", or many similar files changed the same way)
     - If likely AND the knowledge location was NOT touched on this branch → ⚠️ "Consider documenting this in `<detected-location>`"
       - If project uses `knowledge-system` specifically, suggest: "run `/curate`"
       - Otherwise suggest the generic path: "add an entry to `<file>`"
     - If knowledge location WAS touched → ✅ "Conventions documented"
     - If nothing generalizable → ✅ "No new patterns to capture"

   ### 3g. Tests
   - Detect test command (check in order):
     - `package.json` → `scripts.test` (use `pnpm test`, `npm test`, or `yarn test` depending on lockfile)
     - `Makefile` → `test` target
     - `Cargo.toml` → `cargo test`
     - `pyproject.toml` / `pytest.ini` → `pytest`
     - `go.mod` → `go test ./...`
   - If found: ask user "Run tests? (`<command>`) — [y/N]"
     - If yes: run it, capture pass/fail
       - Pass → ✅
       - Fail → ❌ (show failing tests, do not yet abort)
       - Timeout after 5 minutes → ⚠️ "Tests still running, skipped"
     - If no: ➖ "Skipped by user"
   - If no test command detected → ➖ "N/A (no test command found)"

   ### 3h. Linter
   - Detect lint command:
     - `package.json` → `scripts.lint`
     - `.eslintrc*`, `biome.json` → run directly if script not present
     - `Cargo.toml` → `cargo clippy`
     - `ruff.toml` / `pyproject.toml` with ruff → `ruff check`
     - `go.mod` → `go vet ./...`
   - Same ask-first pattern as tests
   - Same result categories: ✅ / ❌ / ➖

   ### 3i. Build
   - Detect build command:
     - `package.json` → `scripts.build`
     - `Makefile` → `build` or `all` target
     - `Cargo.toml` → `cargo build`
     - `go.mod` → `go build ./...`
   - For plugin/markdown-only projects → ➖ "N/A (no build step)"
   - Same ask-first pattern

4. **Present readiness summary**:
   ```
   Readiness for PR creation:

   ✅ Branch up-to-date with <BASE_BRANCH>
   ✅ No uncommitted changes
   ⚠️  README may be stale (src/ changed, README.md untouched)
   ⚠️  Version not bumped (detected feat: suggest minor bump 1.2.0 → 1.3.0)
   ⚠️  CHANGELOG.md not updated (user-facing changes)
   ✅ Knowledge system: N/A
   ✅ Tests passed (42 tests, 1.2s)
   ❌ Linter: 3 errors in src/foo.ts
   ➖ Build: N/A

   Blocking issues: linter errors
   Warnings: README freshness, version bump, changelog entry

   Proceed with PR creation?
   - [y] yes, create PR anyway
   - [n] no, fix issues first
   - [fix] help me fix the blockers/warnings before continuing
   ```

5. **Handle user decision**:
   - `y`: continue to step 6
   - `n`: stop, leave state as-is
   - `fix`: address the failing checks interactively (similar to `/pr-fix` flow but for tooling output), then loop back to step 3

6. **Ensure branch is pushed**:
   - Run: `git push -u origin HEAD` (or `git push` if upstream exists)
   - If push fails, show error and stop

7. **Generate PR title & body**:
   - **Title**:
     - If `$ARGUMENTS` provided, use it (trimmed, single line, ≤72 chars)
     - Otherwise derive from commits:
       - If single commit → use its subject
       - If multiple → summarize the theme (not a list of commits; one coherent title)
     - Imperative mood, English, no trailing period (per global conventions)
   - **Body** (use this structure):
     ```markdown
     ## Summary
     <2-4 bullets covering what changed and why>

     ## Changes
     <bulleted list of the main modifications, grouped by area if helpful>

     ## Readiness
     <copy the status summary from step 3, as checkmarks>

     ## Test plan
     - [ ] <concrete verification step>
     - [ ] <another>

     🤖 Generated with [Claude Code](https://claude.com/claude-code)
     ```
   - Show the generated title + body to the user for confirmation/editing before submitting

8. **Create the PR**:
   - Run via HEREDOC:
     ```bash
     gh pr create --base <BASE_BRANCH> --title "<title>" --body "$(cat <<'EOF'
     <body>
     EOF
     )"
     ```
   - Offer `--draft` flag if any readiness check was ❌ or ⚠️ (default: non-draft if everything green)
   - Capture the resulting PR URL + number

9. **Verify CI auto-trigger**:
   - Store TRIGGER_ISO: `date -u +%Y-%m-%dT%H:%M:%SZ`
   - Wait ~10 seconds
   - Run: `gh pr checks <PR_NUMBER>`
   - If any checks present → ✅ "CI started (<N> checks running)"
   - If empty → ⚠️ "No CI detected — repo may not have workflows on PRs, or GitHub Actions are disabled"

10. **Verify @claude review auto-trigger**:
   - Wait ~15 seconds (auto-trigger workflows typically fire within 10-20s after PR open)
   - Run:
     ```
     gh pr view <PR_NUMBER> --json comments --jq '[.comments[] | select(.author.login == "claude") | select(.createdAt > "<TRIGGER_ISO>")] | length'
     ```
   - If count > 0 → ✅ "Claude review auto-triggered — polling via `/pr-cycle` or `/pr-check`"
   - If count == 0 → ℹ️ "No auto-trigger detected. Run `/pr-cycle` to trigger manually."

11. **Final summary**:
    ```
    ✅ PR #<N> created: <URL>

    CI:     <status>
    Review: <auto-triggered / not auto-triggered>

    Next step:
    - [if review auto-triggered]   Run `/pr-check` in a minute to see results
    - [if NOT auto-triggered]      Run `/pr-cycle` to trigger Claude review
    - [if CI failed/missing]       Investigate CI config before pushing more work
    ```

## Edge Cases

- `gh` not installed/authenticated → step 0 stops with clear error
- PR already exists → redirect to `/pr-cycle`
- Base branch has new commits → handled by `/pr-rebase` (delegated in step 2)
- No commits on branch vs. base → stop: "Nothing to PR — branch is identical to <BASE_BRANCH>"
- User declines to run checks → mark all as "skipped by user" in body, still create PR
- Linter/tests hang → timeout 5min, mark as ⚠️ skipped, let user decide
- Repo uses a non-default base (`develop`, `staging`) → ask user if auto-detected base seems wrong
- `@claude` bot not installed on repo → auto-trigger check returns 0, normal fallback to `/pr-cycle` (which will also fail gracefully)

## Notes

- This skill is **interactive** — every expensive check (tests, lint, build) asks first
- Readiness checks are **advisory**: the user can override and create a draft PR even with failures
- The generated PR body includes the readiness snapshot so reviewers see what was verified
- Designed to be run **once** per PR; for subsequent updates use `/pr-cycle`
- Respects global git/commit conventions (imperative mood, short title, English)
