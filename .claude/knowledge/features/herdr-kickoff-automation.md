---
title: "herdr /kickoff Automation"
createdAt: 2026-06-24
updatedAt: 2026-06-24
createdFrom: "branch: task/automate-kickoff-in-herdr"
updatedFrom: "branch: task/automate-kickoff-in-herdr"
pluginVersion: 1.8.2
prime: false
---

# herdr /kickoff Automation

Inside a herdr session, `/kickoff` replaces its manual "open a terminal yourself"
block with an automated tab launch. The launch lives in one shared, testable
helper — `plugins/work-system/scripts/herdr-launch.sh` (called from
`skills/kickoff/SKILL.md` step 12) — which is the source of truth; this entry
captures the durable design and one non-obvious gotcha.

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

Related: [skill-composition](../architecture/skill-composition.md) (kickoff softly
drives `/continue` across a process boundary). The "never persistent `cd`" footgun
that the worktree commands avoid is a rule, not knowledge — see `.claude/rules/cwd-safety.md`.
