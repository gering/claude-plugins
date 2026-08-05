---
title: "Kickoff Agent Selection: registry, per-repo default, honest degradation"
createdAt: 2026-07-17
updatedAt: 2026-08-03
createdFrom: "session: 2026-07-17 (task/kickoff-agent-selection)"
updatedFrom: "session: 2026-08-03 (task/add-kimi-worker-support)"
pluginVersion: 1.11.0
prime: false
---

# Kickoff Agent Selection

`/kickoff` picks the worktree worker (CLI × model) instead of hardcoding
`claude`. The non-obvious shape and the decisions behind it:

## Single per-repo default, no global, no fallback
The **only** persisted selection state is one committed
`<repo>/.claude/work-system-agent` (`default=<cli:model>`). No global per-user
default, no shipped fallback, no `--auto` ranking, no `--last`. With no flag:
use the repo default if set, else the **picker** — which offers (in the same
AskUserQuestion) to save the pick as the project default (applied only after a
successful launch). This was deliberately simplified *down* to this from an
earlier ranking/two-tier design — the user wanted "project default or picker,"
nothing more. `--pick` forces the picker even when a default exists.

## Registry is the single source of truth
`scripts/agent-registry.sh` owns aliases (`--fable`/`--opus`/`--codex`/`--sol`/
`--grok`/`--kimi`/`--agent cli[:model]`), the launch argv per CLI, availability, and
`default get`/`set`. `herdr-launch.sh` stays CLI-agnostic: it execs the resolved
`argv=` words (argv-exec, no shell-typing race — same reason as the kickoff
launch). Skills never hardcode the CLI list. `default get` **validates** its
committed value against the registry — a stale/removed/attacker-supplied name
reads as "no default" (→ picker), never routes or bricks kickoff.

## grok availability is model-aware and bounded
grok drops/renames models between releases (composer `grok-composer-2.5-fast`
died in 0.2.10x; `grok-build`→`grok-4.5` before that). So grok availability
checks the model is in `grok models`, not just auth. The probe is **always
bounded** (timeout → gtimeout → a self-contained background-killer watchdog with
fds detached so the command substitution doesn't block) so `list`/the picker
never hangs. A failed *or* empty-but-successful (reformatted) `grok models` is
**inconclusive → trust auth (available)**, not "model gone" — a network hiccup
or format drift must not disable the backend. codex/claude stay auth-only (no
clean model-list command). See [[swarm-backend-adapter]] for the sibling probe.

## kimi: the launch shape a CLI's flags can force on you
kimi (added 1.11.0, `--kimi` → `kimi:kimi-code/k3-256k`) is the first worker whose
argv is not `<cli> -m <model> <prompt>`, because **no such form exists**. Probed
live on 0.31.1: no positional launch prompt (`kimi "text"` → "unknown command"),
no initial-prompt env var, and piped stdin only prefills the input box without
submitting — and in a pane it would steal the TUI's tty anyway. `-p` is the sole
entry point but is mutually exclusive with **both** `--auto` and `-y` and exits
after one answer, so it cannot *be* the worker. What makes it work: `-p` runs
tools unattended, and `kimi -c` inherits its session history. Hence two phases:

    sh -c 'kimi -m "$1" -p "$2"; exec kimi -c --auto' kimi-worker <model> <prompt>

`exec` re-roots the pane at kimi (herdr then watches the real process). A failed
seed must neither kill the pane (`&&`) nor pass unnoticed (a bare `;` — the TUI's
first repaint scrolls the error away, leaving a tab that looks like a working
worker but never read TASK.md), so the script **reports and waits for a keypress**
before handing over. Verified: a failed seed in a fresh worktree yields a NEW empty
session, never a foreign one — `kimi -c` is scoped to the working directory, so two
worktrees cannot cross-resume. Values ride as
`"$1"`/`"$2"` positionals, never spliced into the script text — `-p` swallows the
next token, so a concatenated argv is one reordering away from silently eating a
flag (`kimi -p --auto "…"` makes `--auto` the prompt). The test asserts the exact
word list *and* executes the argv against a logging stub, because string checks
can't prove the shell binds values the way you think. Two more traps: `-m` needs
the **qualified** alias (`kimi-code/k3-256k`; the bare name aborts at startup like
an unknown model — hence model-aware readiness), and auth lives in
`credentials/`, not the same-named `oauth/` dir, which stays 0 bytes when logged
in. `kimi doctor` only validates config syntax; it is not an auth check.

**JSON breaks grok's "empty = inconclusive" rule.** grok treats empty output as
drift and trusts auth. For kimi's `provider list --json`, `{"models": {}}` is
non-empty yet a *real* "no models" answer. So the gate is the **section's
presence**: no `"models"` key → drift → trust auth; present but no match → truly
unavailable. Transplanting the sibling's rule verbatim would have mislabeled
either case.

## `argv_shell=`: quoting belongs to the registry, not to prose
`resolve` emits, next to the `argv=` words, one `argv_shell=` line with the same
words POSIX-single-quoted (`shell_quote`, not `printf %q` — bash 3.2 renders that
as per-character backslashes and `$'…'` for non-ASCII: correct but unreadable and
bash/zsh-only). The skills print that line **verbatim** for their outside-herdr
block. Before this, quoting was a *prose rule* the model had to follow; for kimi
that is safety-critical, because its argv carries `;` and `exec` — a mis-quoted
render would run the `;` in the **user's own interactive shell** and replace it
with an unattended agent. Same reason the seed message stays ASCII-only: any
backslash escape or non-ASCII char forces the unreadable `$'…'` form.

## Non-claude degradation: document, don't fake
codex/grok/kimi have no work-system skills, so a launched worker gets a bootstrap
prompt (read TASK.md → commit → PR) instead of `/continue`. Everything
git/PR-derived (`/status`, `/list`, `[ws]` statusline, `/close` tab teardown)
is CLI-agnostic — `agent_name` comes from the registry's `name=`, never argv[0],
and `agent_status` from herdr's own pane hooks, so kimi's `sh -c` wrapper changes
nothing. `/close` Scenario B (`/exit` self-teardown) is claude-only *by
construction* (only a claude session can invoke `/close` from inside its tab).
`/continue` reopen **always sends `claude -c`** — the worker is not persisted
per task (per-task agent memory is a deliberate later idea), so for a
codex/grok/kimi task the user resumes the real worker themselves (`codex resume
--last` / `grok -c` / `kimi -c`); the skill surfaces this inline rather than
pretending. `supports=` in the registry is **reserved** metadata
(the seed for the manager/worker-orchestration design) — not yet consumed.

## Security: announce, don't prompt
A committed external-worker default routes worktree code to a third-party CLI.
The chosen mitigation is to **announce** ("Launching codex — project default…")
before launch, not a consent prompt — an explicit product decision (a cloned
repo can already run hooks/CLAUDE.md, and the committed-default-launches-silently
UX was intentional).
