---
description: Never rely on persistent cwd in skills/scripts — use git -C, subshells, verify pwd
---

# CWD Safety in Skills & Scripts

Skills run shell commands across multiple invocations, and the working
directory can drift — especially in worktree workflows where a skill operates
on the main repo and a worktree in the same run. Bugs distilled from
`work-system` (`kickoff`/`adopt`/`close`) gave these rules:

- **Never `cd` persistently.** A bare `cd` changes state the next command
  inherits, and a later command may assume a different directory. Working-dir
  also does not reliably persist between separate tool calls.
- **Target the repo explicitly:** prefer `git -C <path> ...` over `cd <path> &&
  git ...`.
- **Scope directory changes to a subshell:** `( cd <path> && ... )` so the
  change cannot leak out.
- **Verify before acting:** when a command's correctness depends on location,
  confirm with `pwd` / `git rev-parse --show-toplevel` rather than assuming.

These are footgun-class bugs: they pass in the happy path and corrupt the wrong
repo in the edge case. Default to explicit-path commands.
