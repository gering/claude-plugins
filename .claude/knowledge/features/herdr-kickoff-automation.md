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

Inside a herdr session (`HERDR_ENV=1` + the `herdr` CLI on `PATH`), `/kickoff`
replaces its manual "open a terminal yourself" block with an automated tab launch.
Exact commands live in `plugins/work-system/skills/kickoff/SKILL.md` step 12 — this
entry captures the durable design and one non-obvious gotcha.

## Design decisions

- **Launch with the initial prompt, don't inject keystrokes.** The new pane runs
  `claude -n "<label>" "/continue"` — the *same* command the manual block prints.
  Passing `/continue` as the launch prompt makes Claude run the resume flow itself
  once input-ready, so there is **no** `herdr wait`/ready-match and **no** timeout
  fallback to maintain. This superseded an earlier sketch that booted bare `claude`,
  waited for a ready string, then injected `/rename` + `/continue`.
- **One `LABEL` for tab, herdr agent, and session.** `--label`, `herdr agent rename`
  (immediate, deterministic — covers the boot gap before Claude's title updates),
  and `claude -n` all use one short, sidebar-friendly name. The `task/<name>` branch
  is untouched, so `/continue` still resolves the task from the branch inside the
  worktree.
- **`--workspace "$HERDR_WORKSPACE_ID"` is mandatory.** Without it the tab lands in
  the *focused* workspace, which may be an unrelated project. `--no-focus` keeps the
  kickoff session in front.
- **Graceful fallback.** A missing/broken socket despite `HERDR_ENV` degrades to the
  unchanged manual block — never block kickoff on herdr.

## Gotcha: input into a fresh pane races shell startup

Sending a command into a *just-created* pane can lose keystrokes: the pane's shell
may still be sourcing rc files, or sitting on an interactive startup prompt that
consumes the input. **Verified failure:** oh-my-zsh's "update? [Y/n]" prompt ate the
leading `c` of `claude`, leaving `laude … : command not found`, so Claude never
started and herdr reported the agent as `unknown`.

Fix — a **readiness handshake** before the real launch (safe: it never types into a
running Claude, because Claude is sent only after the shell confirms it executes
commands):
1. `herdr pane send-keys "$pane" Enter` — dismiss a stray `[Y/n]`; harmless at a prompt.
2. Echo a sentinel whose **output** differs from its echoed command text
   (e.g. `printf 'herdr-%s-ok\n' ready` → `herdr-ready-ok`), then `herdr wait output
   --match` on the output string — matching the output, not the command echo, proves
   the shell actually ran it.
3. Only then send `claude -n "<label>" "/continue"`.

This generalizes: any "type a command into a freshly spawned pane/terminal"
automation has the same race; the sentinel handshake is the portable fix.

Related: [skill-composition](../architecture/skill-composition.md) (kickoff softly
drives `/continue` across a process boundary). The "never persistent `cd`" footgun
that the worktree commands avoid is a rule, not knowledge — see `.claude/rules/cwd-safety.md`.
