# PR Flow Plugin

PR review feedback loop for Claude Code. Commit, push, trigger the `@claude` GitHub review bot, poll for feedback in the background, work through issues interactively, and merge safely — without ever opening a browser.

## Features

### Background polling — don't block the session on a review

**What it does.** `/cycle` and `/open` trigger the `@claude` review bot, then launch the polling script as a **background Bash task**. You get the review results pushed back into the conversation when they arrive (1–5 minutes typical). In the meantime you keep working.

**Why it matters.** Waiting in the foreground for a review bot would waste 3+ minutes of session time per iteration. Polling runs on a shared shell script (`scripts/claude-review.sh`), so there is no duplicated logic between `/open` and `/cycle`.

### Auto-trigger detection — no duplicate reviews

**What it does.** Before manually pushing `@claude review` as a comment, `/cycle` and `/open` check whether the repo's CI/webhook already auto-triggered a review on the latest push. If yes, the skill skips the manual trigger and goes straight to polling.

**Why it matters.** Many repos install a GitHub Action that runs Claude review on every push. Triggering again would run the review twice, pay for it twice, and leave two noisy comments on the PR. The detection uses a timestamp window (`latest-after`) on the PR comment feed — precise and cheap.

### Authoritative base-branch detection

**What it does.** `/rebase` does not trust the local default branch. It reads the PR's actual base via `gh pr view --json baseRefName` and rebases against that. If a PR retargets from `main` to `develop` mid-flight, `/rebase` follows.

**Why it matters.** Rebasing against the wrong base silently breaks the diff. `gh pr view` is the authoritative source — we use it.

### Delegation with `--no-poll` — no double polling

**What it does.** `/cycle`, `/open`, and `/merge` all need to check for rebase. Instead of duplicating rebase logic, they delegate to `/rebase --no-poll`. The flag tells `/rebase` not to start its own polling — the parent skill handles that step.

**Why it matters.** Without `--no-poll`, a single `/cycle` would spawn two background polls (one from `/rebase`, one from `/cycle`), and both would race for the same review. The flag is a small contract that keeps the skills composable.

### Outdated reviews hidden automatically

**What it does.** When `/cycle` pushes new commits, it first walks the PR's existing `@claude` comments and minimizes each one as `OUTDATED` via the GitHub GraphQL API. Only the fresh review remains expanded on the PR page.

**Why it matters.** A PR with five stacked "previous" reviews is unreadable. The reviewer (human or bot) sees one current report. Old reports stay collapsed but reachable for history.

### Merge-method auto-detection

**What it does.** `/merge` does not rely on a hard-coded convention. It checks:
- **Repo settings** — which merge methods are enabled (rebase / squash / merge)
- **Historical pattern** — the merge method used by the last 20 merged PRs (via `gh api graphql`)

If a clear pattern emerges (e.g. 18 of 20 last PRs were squashed), `/merge` suggests that method. If it's ambiguous, it asks. No per-repo config file needed.

**Why it matters.** Teams have conventions, but they live in history, not in a settings file. `/merge` reads them directly.

### Pre-merge documentation check

**What it does.** Before executing a merge, `/merge` runs a readiness pass: checks the README for staleness, verifies a version bump happened if the branch mentions version changes, checks for a changelog entry, checks knowledge-system entries for new patterns. On warnings, it asks: `[f]ix / [m]erge anyway / [a]bort`.

**Why it matters.** The right moment to update docs is at merge time, not "later." The three-way prompt keeps you in the loop without being paternalistic.

### Safe branch cleanup — backup convention aware

**What it does.** After a successful merge, `/merge` cleans up the local branch. If the repo already uses a backup convention (`backup/*`, `archive/*`, or `old/*` branches detected via `git branch`), it renames the merged branch with the matching prefix (`<prefix>/<original-name>`) instead of hard-deleting. Otherwise, plain delete.

**Why it matters.** If your team keeps backups, `/merge` respects that. If you don't, it doesn't create clutter. Zero config — detected from repo state.

### Force-push safety

**What it does.** Any force-push inside `/rebase` uses `--force-with-lease`, never `--force`. If the remote advanced since the last fetch (e.g., a teammate pushed), the push fails fast with a clear message.

**Why it matters.** `--force` overwrites teammate commits silently. `--force-with-lease` is the safer default. The skills never bypass it, even under `--no-verify` scenarios — if a hook fails, we investigate, we don't skip.

### Structured review output

**What it does.** `/cycle`, `/check`, and `/fix` all render review output through a **single shared format spec** (`docs/REVIEW-OUTPUT-FORMAT.md`). Required: header, status line, a markdown findings table (columns: # · Severity · Location · Finding · My assessment), optional previously-raised section, one-line recommendation.

**Why it matters.** Every review looks the same. You learn the format once. `/fix` can parse the table and turn rows into an interactive checklist because the format is deterministic.

### Interactive fix walkthrough

**What it does.** `/fix` parses the latest review into a numbered checklist with severity labels (blocking / suggestion / nit). You pick by number (`1`, `1,3,5`, `1-4`, `all`, `blocking`). For each picked finding, Claude gives its own assessment first, then applies a minimal targeted edit.

**Why it matters.** Reviews have noise. You skip the nits and fix the blockers. Claude won't run through everything unbidden — you decide.

### Read-only status snapshot

**What it does.** `/check` is a pure read — it never mutates. Surfaces CI state, human approvals, the latest Claude review (with staleness detection), uncommitted/unpushed local drift, and a one-line merge-readiness verdict.

**Why it matters.** "How's the PR looking?" should be answerable in one command without side effects. `/check` is that command.

## Commands

| Skill | What it does |
|---|---|
| `/open` | Readiness checks (rebase, README, version, changelog, knowledge, tests, lint, build) → create PR → verify CI/review auto-trigger |
| `/cycle` | Full loop: stage → commit → push → trigger `@claude review` → poll in background → present structured results |
| `/check` | Read-only snapshot: CI status, human reviews, latest Claude feedback, merge-readiness verdict |
| `/fix` | Walk through issues from the latest review as a numbered checklist and implement fixes interactively |
| `/rebase` | Standalone rebase check against the PR's actual base branch; execute cleanly on confirmation |
| `/merge` | Merge the PR safely — detect method, verify CI + reviews + open issues, execute, clean up |

## Typical workflow

```
  make changes
       │
       ▼
  /open   ──►  PR opened, CI + review auto-triggered
       │
       ▼
  /cycle  ──────►  review comes back with issues
       │                       │
       │                       ▼
       │                  /fix   (walk through issues interactively)
       │                       │
       └◄──────────────────────┘   (re-run /cycle)
       │
       ▼
  /merge  ──►  verify everything, merge safely, cleanup

  any time:  /check    ──►  read-only status snapshot
  any time:  /rebase   ──►  standalone rebase against PR base
```

## Usage examples

### Open a PR with full readiness checks

```
> /open
```

Runs tests, linter, build. Checks README staleness, version bump expectations, changelog, knowledge-system entries. If everything is green, creates the PR in one step. If something is yellow, shows a single summary prompt — no per-check pop-ups.

### Iterate on review feedback

```
> /cycle "fix review findings"
```

Stages, commits with that message, pushes, hides outdated reviews, checks for auto-trigger, triggers manually if needed, polls in the background. You continue working. When the review comes back, it's rendered as a structured table.

### Work through issues one at a time

```
> /fix
```

Parses the latest review. You pick:
- `1,3` — fix issues 1 and 3
- `blocking` — fix all blocking findings
- `all` — walk through everything
- `skip 2` — skip issue 2 for now

For each picked item, Claude gives its own take first (may disagree with the reviewer on judgment calls), then implements.

### Check status without changing anything

```
> /check
```

Returns: CI ✅/❌, human reviews (0 approved, 1 changes requested), Claude review (fresh / stale / none), local drift (3 unpushed commits), verdict (`blocked on CI` / `ready to merge` / `waiting on review`).

### Merge when ready

```
> /merge
```

Verifies CI green, required approvals present, no open blocking Claude issues, branch up-to-date with base. Detects merge method from history. Runs the pre-merge documentation pass. If all checks are green, merges directly without an additional confirmation prompt (v1.1.4+). If a warning surfaces, asks for `[f]ix / [m]erge anyway / [a]bort`. Cleans up the local branch (backup-or-delete per repo convention).

## Requirements

- `gh` CLI installed and authenticated (`gh auth login`)
- GitHub repo with the `@claude` review bot installed (Claude GitHub App or a workflow that responds to `@claude` mentions)
- Active PR on a non-default branch

Each skill runs a preflight check and stops with a clear message if requirements are missing.

## Design principles

- **Interactive by default** — no silent commits, pushes, or fixes without user confirmation
- **Read-only where it matters** — `/check` never mutates anything
- **User stays in control** — `/fix` does not auto-trigger `/cycle`; you decide when to re-push
- **Root cause over workaround** — `/merge` refuses `--admin` bypass. A failing required check is a signal to fix the check, not to skip it
- **Composable** — skills delegate cleanly (`--no-poll` flags) instead of duplicating logic
- **Complementary, not duplicative** — for deep local static analysis (silent failures, test coverage, type design), install the `pr-review-toolkit` plugin alongside

## Relationship to other plugins

- **`work-system`** — finish a task with `/close`, then `/open` to create the PR and `/cycle` for the review loop before `/merge`
- **`pr-review-toolkit`** (external, Anthropic) — local analysis agents. Complementary, not required. Install via `/plugin install pr-review-toolkit@claude-plugins-official`

## Installation

Part of the [gering-plugins](https://github.com/gering/claude-plugins) marketplace:

```
/plugin marketplace add gering/claude-plugins
/plugin install pr-flow
```
