# PR Flow Plugin

PR review feedback loop for Claude Code. Commit, push, trigger the `@claude` GitHub review bot, poll for feedback in the background, and work through issues interactively — without ever opening a browser.

## Skills

| Skill | What it does |
|---|---|
| `/pr-create` | Readiness checks (rebase, README, version, changelog, knowledge, tests, lint, build) → create PR → verify CI/review auto-trigger |
| `/pr-cycle` | Full loop: stage → commit → push → trigger `@claude review` → poll → present structured results |
| `/pr-check` | Read-only snapshot: CI status, human reviews, latest Claude feedback, merge-readiness verdict |
| `/pr-fix` | Walk through issues from the latest review as a numbered checklist and implement fixes interactively |
| `/pr-rebase` | Check whether the branch needs a rebase against its PR's base branch; execute cleanly on confirmation |
| `/pr-merge` | Merge the PR safely — detect method, verify CI + reviews + open issues, execute, clean up |

## Typical Workflow

```
  make changes
       │
       ▼
  /pr-create  ──►  PR opened, CI + review auto-triggered (or /pr-cycle)
       │
       ▼
  /pr-cycle  ──────►  review comes back with issues
       │                       │
       │                       ▼
       │                  /pr-fix   (work through issues)
       │                       │
       └◄──────────────────────┘   (re-run /pr-cycle)
       │
       ▼
  /pr-merge  ──►  verify everything, merge safely, cleanup

  any time:  /pr-check    ──►  read-only status snapshot
  any time:  /pr-rebase   ──►  standalone rebase against PR base
```

## Requirements

- `gh` CLI installed and authenticated (`gh auth login`)
- GitHub repo with the `@claude` review bot installed (Claude GitHub App)
- Active PR on a non-default branch

Each skill runs a preflight check and stops with a clear message if requirements are missing.

## Design Principles

- **Interactive by default** — no silent commits, pushes, or fixes without user confirmation
- **Read-only where it matters** — `/pr-check` never mutates anything
- **User stays in control** — `/pr-fix` does not auto-trigger `/pr-cycle`; you decide when to re-push
- **Complementary, not duplicative** — for deep local static analysis (silent failures, test coverage, type design), install the `pr-review-toolkit` plugin alongside

## Relationship to other plugins

- **`work-system`**: finish a task with `/close`, then `/pr-cycle` for the review loop before merging
- **`pr-review-toolkit`** (external, Anthropic): local analysis agents — complementary, not required
