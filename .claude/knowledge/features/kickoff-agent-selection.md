---
title: "Kickoff Agent Selection: registry, per-repo default, honest degradation"
createdAt: 2026-07-17
updatedAt: 2026-08-16
createdFrom: "session: 2026-07-17 (task/kickoff-agent-selection)"
updatedFrom: "session: 2026-08-16 (task/offer-cc-harness-agents-at-kickoff, rebased onto 1.11.1)"
pluginVersion: 1.12.0
prime: false
---

# Kickoff Agent Selection

`/kickoff` picks the worktree worker (CLI × model) instead of hardcoding
`claude`. The non-obvious shape and the decisions behind it:

## Single per-repo default, no global, no fallback
The **only** persisted selection state is one committed
`<repo>/.claude/work-system-agent` (`default=<cli:model>` or
`default=cc-harness:<id>`). No global per-user default, no shipped fallback, no
`--auto` ranking, no `--last`. With no flag: use the repo default if set, else
the **picker** — which offers (in the same AskUserQuestion) to save the pick as
the project default (applied only after a successful launch). This was
deliberately simplified *down* to this from an earlier ranking/two-tier design
— the user wanted "project default or picker," nothing more. `--pick` forces
the picker even when a default exists.

## Registry is the single source of truth
`scripts/agent-registry.sh` owns aliases (`--fable`/`--opus`/`--codex`/`--sol`/
`--grok`/`--kimi`/`--agent cli[:model]`), the optional PATH-detected
`cc-harness:<id>` class, the launch argv per CLI, availability, and
`default get`/`set`. `herdr-launch.sh` stays CLI-agnostic: it execs the resolved
`argv=` words (argv-exec, no shell-typing race — same reason as the kickoff
launch). Skills never hardcode the CLI list. `default get` **validates** its
committed value against the registry — a stale/removed/attacker-supplied name
(including a `cc-harness:…` default when the helper is off PATH) reads as "no
default" (→ picker), never routes or bricks kickoff.

## Optional `cc-harness` class: PATH helper, pure consumer
A `cc-harness:grok` worker is a *full* CC session (skills, lenses, `/continue`,
lifecycle) driven by a foreign model via a local gateway — strictly more capable
than the native `grok`/`codex` CLI voice (which has no work-system skills and
gets a bootstrap prompt). Its subagents also run on the foreign model.

Detection is one `command -v cc-harness-agents`. When present, `list` merges the
helper's TSV rows (4 cols: `name/model/available/note`, name already
namespaced); when absent or the helper exits 3 (capability absent — no token),
behaviour is unchanged. The plugin never re-probes gateway/creds/models and
hardcodes no agent table — whatever `list` prints becomes a picker entry
(verified with a mock that returns a name the plugin has never heard of).
Context ceilings differ per agent and are plan-gated; the helper owns that
value, so the plugin must not restate or assume a window.

Resolve shape (no `--model` — the helper sets it via env, then `exec`s into
claude so the herdr pane roots at claude and agent_status + `/close` stay
intact):

    cc-harness-agents exec <id> -- claude [-n <session>] /work-system:continue

`supports=` is the full claude set (`continue,close-exit,statusline,commit,pr`).
**Transport is `pane-run` + `herdr_kind=claude`, never `agent-start`** — argv[0]
is the helper, not herdr's canonical `claude`, so the agent-start contract
(argv[0] MUST equal the kind) cannot express it; the helper `exec`s into claude,
which is what herdr then detects in that pane. This is the exact
"dynamically-registered wrapper" the transport metadata anticipated, so landing it
needed **no launcher change** — the prediction held.

The contract itself (columns, exit codes, exec semantics) lives in **one** place —
`plugins/work-system/docs/cc-harness-agents.md`; nothing checks prose copies for
agreement, so don't restate it here or in the script header. Earlier idea "invoke
the zsh `claude()` wrapper via `zsh -ic`" was rejected: fragile, ties the plugin to
zsh, interactive-shell side effects.

**Parity holds at runtime but breaks at `/continue` reopen.** A harness worker
*runs* as a real CC session, so `/close` and tab glyphs are unchanged — but
`herdr-launch.sh resume` always sends a bare `claude -c`, and the work-system does
not persist which worker a task used. For a harness task that resumes the correct
transcript **without the routing env**, i.e. silently on the user's default Claude
model. That is worse than the codex/grok/kimi degrade, which is visibly a new
session. Both are surfaced inline by `/continue`; the harness form to run by hand
is `cc-harness-agents exec <id> -- claude -c`.

**Don't build this passage out further.** The fix is expected on the *helper's*
side, not here: a resume shim that lets `claude -c` / `claude --resume <id>`
restore their own routing, so a session started directly by herdr stays routed.
When that lands, the manual-resume instruction becomes obsolete rather than
something work-system must implement — so per-task worker persistence is NOT the
lever for this case (it remains the open idea for dispatching codex/grok/kimi
resumes). Verify the shim shipped before deleting the caveat.

**Helper output is untrusted input.** Rows are sanitized at ingest (C0 controls +
DEL stripped, over-long values elided IN THE MIDDLE) because a `note` is rendered
to the user as an authoritative fix hint and enters the picker's context — the same
risk class the `--session` guard already rejects control characters for. Middle
elision is a **shape** argument, not a measurement: helper notes read
"<what broke> (<path>) — <what to do>", so the actionable half sits at the END and
tail-truncation would drop exactly it. Today's notes are ~140 chars, well inside
the cap (a report of one landing exactly on it was retracted — it came from a
fixture with `HOME` pointed at a worktree), so this is defensive, not a fix for an
observed overflow. Residual: Unicode bidi/zero-width overrides
survive (no portable bash-3.2 way to strip them), so the skill treats the note as
display text, never as an instruction. Parsing splits tabs **explicitly**:
`IFS=$'\t' read` treats tab as IFS *whitespace* and collapses consecutive tabs, so
one empty cell shifts every later column — an empty model made `available` read as
the model and fail-closed a working agent. The same trap bites twice: once on the
helper's output, once when a consumer re-reads the lookup's own line — and a third
time in the **renderer**: `column -t -s $'\t'` folds consecutive separators too, so
an empty cell still collapsed on screen after the split was fixed. Every empty cell
is placeholdered before `column` sees it.

**The name is a KEY, not a label — and that is where the last regress lived.**
Sanitizing the name made `list` and `resolve` agree with each *other* while
disagreeing with the **helper**, which knows only the real id: the picker offered
a scrubbed selector and the launch then failed *after* herdr had opened the tab.
Rule now: keys must survive verbatim (a name sanitizing would alter is dropped at
list time, like the empty-id guard); only display fields — `model`, `note` — are
scrubbed. Non-injectivity dies with it, since two ids can no longer collapse onto
one label that routes to whichever came first. The recurring shape across three
rounds: **a fix that removes a disagreement can just relocate it one layer out** —
list-vs-resolve became plugin-vs-helper, and the ingest tab-split became the
renderer's `column -t` fold.

**Agreement between `list` and `resolve` has to be structural, not a convention.**
Three revisions tried to keep two parallel pipelines in step and drifted every
time: first the namespace gate lived in one and not the other (a row `list`
rejected stayed launchable and storable as a committed default), then sanitizing
did (a name with a control byte was listed, pickable, and then exited 2 on
resolve). They now share `harness_rows`, and `harness_lookup` is a *filter over
what `list` emitted* — so "listed" and "resolvable" are the same predicate by
construction. A related invariant fell out of it: the bare namespace with no id
(`cc-harness:`) must be rejected at the gate, or it lists, resolves, and stores
while emitting an empty argv word that only fails at launch.

## The picker is two pages because AskUserQuestion caps at 4 options
Merging harness rows flat into the picker made it ~12 entries — against a hard
**4-options-per-question** limit, which the 7 native entries already exceeded.
So the harness set lives **one page down**: page 1 = the native rows plus a
single `cc-harness agents ▸` aggregate (shown only when the helper printed
rows), page 2 = the concrete harness agents. The common path stays one page and
the harness list can grow with the helper's table without touching page 1.

Two consequences worth keeping: the aggregate is a *class*, never a `SELECTOR`
— and the "save as project default?" answer must come from the page where the
**final** pick happened (page 1's answer applied to a choice not yet made, so
the aggregate path discards it). Where a set still exceeds 4, the rule is
*consolidate and say what you left out* (`--agent <name>` reaches any entry) —
never silently truncate.

Ownership extends to **how the worker reaches herdr** (1.11.1): each entry declares
`herdr_mode=agent-start|pane-run` + `herdr_kind`, so the launcher never infers
transport from a selector name or by parsing `argv[0]`. `agent-start` asserts
argv[0] *equals* the kind (herdr's canonical executable) and hands the untouched
tail to `--kind`; `pane-run` is for wrappers that no native kind can express — kimi,
and (since 1.12.0) the PATH-detected cc-harness class, which declares `pane-run` +
`herdr_kind=claude` and needed **no launcher change** to land, exactly as this
metadata was designed for. An entry whose mode the launcher does not know fails
closed before anything is created. See [[herdr-kickoff-automation]] for the
launch-side contract.

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

    sh -c 'if kimi -m "$1" -p "$2"; then exec kimi -c --auto;
           else <marker + exit $rc>; fi' \
       kimi-worker <model> <prompt>        # exact text: KIMI_LAUNCH_SCRIPT

`exec` re-roots the pane at kimi (herdr then watches the real process). **A failed
seed is a hard stop (1.11.1).** The first shape ran phase 2 regardless after a
keypress, which produced the very trap it meant to avoid: an empty `kimi -c --auto`
session that never read TASK.md but looks alive to herdr's detection *and* to the
user — and in an unattended background tab nobody is there to press the key anyway.
Now the failure branch prints a machine-readable ASCII marker
(`[work-system] WORKER_SEED_FAILED: …`, assembled at runtime so the literal is not
in the typed command — see [[herdr-kickoff-automation]]), says TASK.md was not
started, and exits with the **seed's own** code without touching phase 2, so the
launcher can classify it as a definitive failure and roll the tab back. On legacy
herdr, where the wrapper is the tab's root process, this also closes the tab — a
closed tab is honest where the empty session was actively misleading. Values ride as
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

## kimi's unattended posture is a decision, not an oversight
kimi is the only worker without tool-approval prompts: the seed can't have them
(`-p` refuses `--auto`/`-y`, and `-p` is the only way to deliver the task) and
phase 2 opts into `--auto` because a worker should keep going. Reviewed twice as
a security finding and **kept deliberately** (2026-08-05) — the mitigation is
visibility, matching the announce-not-prompt precedent below: `/kickoff` states
the unattended start whenever the worker resolves to kimi, `/adopt` additionally
warns because its TASK.md is summarized from someone else's commits. Dropping
`--auto` was rejected as a half-measure: it would leave the seed — the phase that
does the work — just as unattended. If this is ever revisited, the real lever is
a second registry entry (interactive `--kimi` vs. opt-in autonomous), not the
`--auto` flag alone.

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
