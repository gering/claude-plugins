---
name: open
description: |
  Creates a PR after readiness checks (tests, lint, build, README,
  version, changelog, knowledge, rebase), then polls CI + first @claude review.
  Trigger: "create a PR", "open a pull request", "ready for review".
user_invocable: true
---

# Create Pull Request

> Pre-flight readiness checks (docs, tests, lint, build) → create the PR with a generated title/body → verify CI and @claude review auto-trigger → recommend next step.

## Arguments

- `$ARGUMENTS` - Optional: custom PR title. If omitted, one is generated from commits.

## Execution Principles

**No multiple-choice menus.** This skill must NEVER present the user with alternative options like "(a) do X, (b) do Y, (c) do Z". Either execute the right thing automatically, or stop with a single hard blocker question.

**Auto-resolve warnings where feasible.** If a readiness check surfaces a warning with a clear action (add knowledge entry, auto-fixable linter errors, re-run tests for confirmation), do it automatically during the check phase — do not surface it as a question.

**If everything is green, just proceed.** After all checks and auto-resolutions: if there are zero ❌ blockers and zero ⚠️ warnings, create the PR automatically without asking. Only ask for confirmation when at least one ⚠️ warning remains (something needed judgment). ❌ blockers always stop the skill — never proceed with blockers.

## Instructions

0. **Preflight (tooling)**:
   - Verify `gh` installed and authenticated (see `/cycle` step 0 for exact commands). Stop with clear error if missing.

1. **Branch & PR state**:
   - Run: `git branch --show-current`
   - If on `main`/`master`, stop: "You're on the main branch. Create a feature branch first."
   - Run: `gh pr view --json number,url 2>/dev/null`
   - If a PR already exists, stop: "PR #<N> already exists: <URL>. Use `/cycle` to push updates."
   - Detect base branch: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'` (fallback: ask user or default to `main`)

2. **Check if rebase is needed** — delegate to `/rebase --no-poll`:
   - Invoke the `/rebase` skill **with the `--no-poll` flag** (this skill handles post-creation review polling itself in step 10). Since no PR exists yet, `/rebase` falls back to upstream tracking or repo default to detect the base branch.
   - If `/rebase` rebased successfully → continue
   - If user declined the rebase → continue but remember to flag ⚠️ "Branch is N commits behind `<BASE_BRANCH>` (rebase declined)" in the readiness summary (step 4)
   - If conflicts aborted the rebase → stop this skill; user resolves manually, then re-runs `/open`

3. **Readiness checks — collect a status list**:
   Run each check and collect results into a summary table. **Do NOT stop on failure** — present everything, let the user decide.

   ### 3a. Uncommitted changes
   - Run: `git status --porcelain`
   - If changes exist → ⚠️ "Uncommitted changes — commit before creating PR"
   - Offer: "Want me to run `/cycle` instead (handles commit + push + review)?" — if yes, abort this skill

   ### 3b. Unpushed commits (for branch-existence check later)
   - Run: `git log @{u}..HEAD --oneline 2>/dev/null` — fine if it errors (upstream may not exist yet)

   ### 3c–3f. Documentation readiness (README, version, changelog, knowledge)
   Run the four documentation-readiness checks defined in the shared spec at
   `${CLAUDE_PLUGIN_ROOT}/docs/READINESS-CHECKS.md` — **read that file** for
   the detection signals, the ✅/⚠️/❌/➖ semantics, and the auto-fixable vs.
   manual classification. Do not re-derive the heuristics here; the spec is
   canonical.

   **Open-specific behavior — auto-resolve in place.** Per the "auto-resolve
   warnings where feasible" principle (top of this skill), apply each
   **auto-fixable** fix *immediately during this check phase* rather than
   surfacing it as a warning:
   - **Version** → bump the detected field(s) per the spec's suggestion
   - **Changelog** → draft the entry under the unreleased / next-version heading
   - **Knowledge** → invoke `/curate` (knowledge-system) or append to the closest knowledge file
   After resolving, mark the check ✅ "<auto-fixed>". Only **manual** gaps
   (README staleness) and fixes that genuinely need judgment remain as ⚠️ for
   step 4.

   ### 3g. Tests
   - Detect test command (check in order):
     - `package.json` → `scripts.test` (use `pnpm test`, `npm test`, or `yarn test` depending on lockfile)
     - `Makefile` → `test` target
     - `Cargo.toml` → `cargo test`
     - `pyproject.toml` / `pytest.ini` → `pytest`
     - `go.mod` → `go test ./...`
   - If found: **run it automatically** (do not ask). Inform user "Running tests…" so they see the action.
     - Pass → ✅ "<N> tests passed (<duration>)"
     - Fail → ❌ (show failing tests — this is a blocker in step 4)
     - Timeout after 5 minutes → ⚠️ "Tests still running, skipped"
   - If no test command detected → ➖ "N/A (no test command found)"

   ### 3h. Linter
   - Detect lint command:
     - `package.json` → `scripts.lint`
     - `.eslintrc*`, `biome.json` → run directly if script not present
     - `Cargo.toml` → `cargo clippy`
     - `ruff.toml` / `pyproject.toml` with ruff → `ruff check`
     - `go.mod` → `go vet ./...`
   - **Run automatically** (do not ask). If the linter supports auto-fix (`--fix`, `--write`), run that variant first, then re-run to verify clean state.
   - Result categories: ✅ / ❌ / ➖

   ### 3i. Build
   - Detect build command:
     - `package.json` → `scripts.build`
     - `Makefile` → `build` or `all` target
     - `Cargo.toml` → `cargo build`
     - `go.mod` → `go build ./...`
   - For plugin/markdown-only projects → ➖ "N/A (no build step)"
   - **Run automatically** (do not ask). Result: ✅ / ❌ / ➖

4. **Decide based on check results**:
   - Present the concise status table (all ✅/⚠️/❌/➖ items from step 3) for transparency — this is informational output, not a question.
   - **All green (zero ❌, zero ⚠️)** → proceed directly to step 6. No confirmation. Just announce: "All green — creating PR." and continue.
   - **Any ❌ blocker** → stop. Print a single line naming the blocker (file/line/reason). Do not offer a menu. User fixes and re-runs.
   - **Only ⚠️ warnings remain (no blockers)** → present the table plus title+body preview, then ask **exactly once**: "Create PR? [Y/n]"
     - `y` (default): continue to step 6
     - `n`: stop, leave state as-is
     - No other options. No "fix" branch. No alternatives.

5. *(merged into step 4)*

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
   - **Do not ask for confirmation here.** Step 4 already handled the decision (either all-green auto-proceed, or single "[Y/n]" on warnings). Generate the title/body directly per the rules above and continue to step 8.

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
      bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" latest-after <PR_NUMBER> "<TRIGGER_ISO>"
      ```
    - **If output is non-empty** → a review has started. Launch background polling to wait for completion:
      ```
      bash "${CLAUDE_PLUGIN_ROOT}/scripts/claude-review.sh" poll <PR_NUMBER> "<TRIGGER_ISO>"
      ```
      Use the **Bash tool** with `run_in_background: true`. When it completes, render the review following the shared format spec at `${CLAUDE_PLUGIN_ROOT}/docs/REVIEW-OUTPUT-FORMAT.md` — read that file before presenting. Required sections: header, status line, findings markdown table, single-line recommendation. (`/open` is always round 0 — no prior findings, so no `Status` column.)
    - **If output is empty** → no auto-trigger detected. Inform the user and suggest `/cycle` to trigger manually. Do NOT trigger automatically here — `/open` is about creation; triggering is `/cycle`'s job.

11. **Final summary**:
    ```
    ✅ PR #<N> created: <URL>

    CI:     <status>
    Review: <auto-triggered, polling in background / not auto-triggered>

    Next step:
    - [if review auto-triggered]   Review results will appear when polling completes (~1-5 min)
    - [if NOT auto-triggered]      Run `/cycle` to trigger Claude review manually
    - [if CI failed/missing]       Investigate CI config before pushing more work
    ```

12. **Sync task-tab glyphs** (best-effort, silent):
    - Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/refresh-task-glyphs.sh"`
    - Inside herdr with the work-system plugin installed, this flips the task
      tab's sidebar glyph to `◇` (in review) now that the PR exists; otherwise
      it is a silent no-op. Ignore its output — never block or report on it.

## Edge Cases

- `gh` not installed/authenticated → step 0 stops with clear error
- PR already exists → redirect to `/cycle`
- Base branch has new commits → handled by `/rebase` (delegated in step 2)
- No commits on branch vs. base → stop: "Nothing to PR — branch is identical to <BASE_BRANCH>"
- User declines to run checks → mark all as "skipped by user" in body, still create PR
- Linter/tests hang → timeout 5min, mark as ⚠️ skipped, let user decide
- Repo uses a non-default base (`develop`, `staging`) → ask user if auto-detected base seems wrong
- `@claude` bot not installed on repo → auto-trigger check returns 0, normal fallback to `/cycle` (which will also fail gracefully)

## Notes

- This skill is **interactive** — every expensive check (tests, lint, build) asks first
- Readiness checks are **advisory**: the user can override and create a draft PR even with failures
- The generated PR body includes the readiness snapshot so reviewers see what was verified
- Designed to be run **once** per PR; for subsequent updates use `/cycle`
- Respects global git/commit conventions (imperative mood, short title, English)
