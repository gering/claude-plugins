---
title: "herdr /kickoff + /continue-reopen Automation"
createdAt: 2026-06-24
updatedAt: 2026-07-02
createdFrom: "branch: task/automate-kickoff-in-herdr"
updatedFrom: "branch: task/continue-reopen-herdr"
pluginVersion: 1.8.2
prime: false
---

# herdr /kickoff + /continue-reopen Automation

Inside a herdr session, `/kickoff` replaces its manual "open a terminal yourself"
block with an automated tab launch. The launch lives in one shared, testable
helper — `plugins/work-system/scripts/herdr-launch.sh` — with two subcommands:
`launch` (called from `skills/kickoff/SKILL.md` step 12) and `resume` (called from
`skills/continue/SKILL.md`'s reopen path — the main session with a `<task>` arg, or
a *different* task's name given from inside a worktree). The helper is the source of
truth; this entry captures the durable design and one non-obvious gotcha.

## Design decisions

- **Encapsulate the launch in a script, not skill prose.** The deterministic
  sequence (gate → `agent start` → robust pane-id parse → `pane move` → exit code)
  lives in `herdr-launch.sh` so it can be `bash`-tested and reused (e.g. by a
  future `/adopt` automation), per the project's helper-script convention and the
  "prose skill logic drifts" memory. The skill only derives the label and branches
  on the helper's `moved=yes|no` / exit code.
- **Spawn Claude as argv, never type it into a shell.** The launch is
  `herdr agent start "<label>" … -- claude -n "<label>" "/continue"`, which execs
  the `claude` binary directly. The `-- argv` form sidesteps the interactive shell
  entirely, so there is no keystroke race against shell startup (see the gotcha
  below) and no readiness handshake to maintain. `-n` names the real session and
  `/continue` is the launch prompt, run on startup.
- **`agent start` splits the caller's tab → move it out.** `herdr agent start`
  (without `--tab`) lands the agent as a split pane in the *invoking* tab, so a
  second step `herdr pane move "<pane>" --new-tab --label "<label>"` relocates it
  into its own background tab — one tab per task. The pane id comes from the start
  call's `result.agent.pane_id` (parsed with `python3`; `herdr pane move` does **not**
  accept an agent name as target — verified).
- **One short `LABEL` for agent name + session.** `agent start "<label>"` (immediate,
  deterministic sidebar label) and `claude -n "<label>"` use one short,
  sidebar-friendly name (filler words like `automate`/`in` dropped). The `task/<name>`
  branch is untouched, so `/continue` still resolves the task from the branch.
- **Detection gate.** Automate only when `HERDR_ENV=1`, `$HERDR_WORKSPACE_ID` is
  non-empty (an empty `--workspace` lands the tab in the *focused*, possibly
  unrelated, workspace), and both `herdr` and `python3` are on `PATH`. `--no-focus`
  keeps the kickoff session in front; any failure (empty `$pane`) degrades to the
  unchanged manual block — never block kickoff on herdr.

## `resume` mode: reopen a task tab a `/exit` closed

A kickoff tab runs Claude as its **root pane** (argv-exec above), so a clean `/exit`
— even one only meant to restart Claude Code — ends the pane and herdr closes the
whole tab; the worktree and resumable session persist, but the tab is gone.
`/continue <task>` **from the main session** recovers it via `herdr-launch.sh
resume`, which — unlike `launch` — uses `herdr tab create` + `pane run "claude -c"`
so Claude runs **inside a shell pane**. Two durable decisions:

- **Shell-pane resume is the `/exit` hardening; kickoff stays argv.** Because the
  reopened Claude is *not* the root pane, a later `/exit` drops back to the shell
  and the **tab survives** — exactly what a plain kickoff tab can't do. We
  deliberately did **not** convert kickoff to a shell-pane launch to get the same
  prevention: argv-exec's race-freedom is verified (the gotcha below), and `/close`'s
  teardown (self-exit poller on `agent_status`, SessionEnd hook keyed to
  Claude-as-root-pane) is built around the root-pane model — changing it risks that
  machinery with no way to live-verify here. So kickoff tabs still die on `/exit`;
  reopen is the one-command recovery, and reopened tabs are hardened. A race-free
  *prevention* (`agent start … -- bash -lc 'claude …; exec "$SHELL" -i'`) is possible
  but deferred pending live herdr agent-detection verification.
- **`claude -c`, no session-id stash.** Resume runs `claude -c` (most-recent session
  for the cwd). Each worktree hosts exactly one task, so its cwd is a 1:1 proxy for
  the session — `-c` is already unambiguous, and stashing a session id at kickoff
  (capture-at-argv-launch + marker lifecycle + staleness) buys no disambiguation.
  `resume` also *focuses* the reopened tab (the user is switching to it), where
  `launch` opens `--no-focus` in the background.
- **Idempotent — never spawn a second session on one worktree.** Before creating a
  tab, `resume` looks up an existing tab at the worktree cwd (reusing
  `herdr-teardown.sh worktree-tab-state`, the single source of truth for realpath cwd
  matching) and, if found, just *focuses* it (`reused=yes`). Without this guard,
  `/continue <task>` on a task that was never `/exit`-ed would start a **second**
  `claude -c` on the same working tree — two sessions clobbering each other's
  uncommitted changes. Four honesty/robustness details the guard needs to be sound:
  - **Fail CLOSED on uncertainty, via a tri-state lookup.** `worktree-tab-state`
    returns `<tab>` / `none` / `unverified` — not a bare empty string that conflates
    "no tab" with "couldn't check." `none` (populated list, no cwd match) → create;
    `unverified` (herdr unreachable, or an EMPTY/repopulating pane list — the same
    empty-≠-gone hazard `extract_tab_present` guards) → **exit 1**, so the skill shows
    the manual block and a *human* spots any existing tab. A plain empty-return guard
    fails OPEN and duplicates; this can't, and it pays no retry latency on the common
    no-tab path.
  - **Search ALL workspaces.** The lookup passes an empty workspace so a still-live
    tab for this worktree in a *different* herdr workspace is also found (worktree
    paths are globally unique); the new tab is still *created* in
    `$HERDR_WORKSPACE_ID`.
  - **Known gap: cwd is matched EXACTLY (realpath), shared with `/close`.** A pane
    whose shell wandered into a *subdirectory* of the worktree no longer matches, so a
    double-reopen after `/exit`-then-`cd subdir` could miss the surviving tab and
    duplicate. Accepted: loosening to prefix-match would change the canonical
    `extract_tab` matcher that `/close` also relies on (unverifiable here), and a
    second lenient matcher would reintroduce the very drift the shared one prevents.
  - **Don't assert a live resume on reuse.** A cwd match can't distinguish a live
    Claude from a bare shell that survived a prior `/exit`, so the reuse branch emits
    `resumed=` (empty), and the skill tells the user to run `claude -c` if the focused
    tab is just a shell — never a false "already resumed."
  - **Report `resumed=no`/`focused=no` honestly.** A failed `pane run "claude -c"`
    send → `resumed=no` (user runs it by hand); a failed/absent `tab focus` →
    `focused=no` (skill doesn't claim a focus that didn't happen). The tab-create
    response is parsed pipe-delimited (`<pane>|<tab>`) so an empty pane id can't be
    mis-read as the tab id.

### Known asymmetry: reopened tabs and `/close` teardown

A `resume`-launched Claude runs via `pane run` (a shell-foreground process), **not**
`agent start`, so herdr may not track it as a registered agent. `/close`'s Scenario-B
self-close polls the pane's `agent_status` and injects `/exit` only on `idle`/`done`;
if that status is never populated for a shell-launched Claude, the poller times out
and does not auto-close the reopened tab. This degrades **gracefully** — `/close`
always prints the manual-close line as its backstop — so a reopened task may need a
by-hand tab close where a kickoff tab would auto-close. This is the same
agent-detection question flagged as unverified for the deferred race-free-prevention
option above; both wait on live herdr verification.

## Gotcha: input into a fresh pane races shell startup

This is *why* the launch uses argv-exec, not `herdr pane run` / typed keystrokes.
Sending a command into a *just-created* pane can lose keystrokes: the pane's shell
may still be sourcing rc files, or sitting on an interactive startup prompt that
consumes the input. **Verified failure:** an earlier `tab create` + `pane run
"claude …"` implementation lost the leading `c` to oh-my-zsh's "update? [Y/n]"
prompt, leaving `laude … : command not found`, so Claude never started and herdr
reported the agent as `unknown`. A sentinel-handshake before typing works but is
fragile (the sentinel can re-match stale scrollback). The robust fix is to not type
into a shell at all: `herdr agent start … -- <argv>` execs the binary directly.

Generalizes: when a multiplexer offers both "type into a pane's shell" and "exec
argv" launch paths, prefer argv — it has no race against shell init.

The `resume` mode knowingly takes the other path (`pane run "claude -c"`, which
*does* race shell startup): a surviving-`/exit` tab **requires** a shell pane, and
this exact sequence was verified live by hand. The race is a low-probability cost on
a manual recovery action, accepted for the tab-survival payoff — not the automated,
frequently-run kickoff, where argv-exec's certainty wins.

Related: [skill-composition](../architecture/skill-composition.md) (kickoff softly
drives `/continue` across a process boundary). The "never persistent `cd`" footgun
that the worktree commands avoid is a rule, not knowledge — see `.claude/rules/cwd-safety.md`.
