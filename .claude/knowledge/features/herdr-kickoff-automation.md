---
title: "herdr /kickoff + /continue-reopen Automation"
createdAt: 2026-06-24
updatedAt: 2026-08-11
createdFrom: "PR #17"
updatedFrom: "session: 2026-08-11 (herdr 0.7.5+ dual launch contract)"
pluginVersion: 1.11.1
prime: false
reindexedAt: 2026-07-12
---

# herdr /kickoff + /continue-reopen Automation

Inside a herdr session, `/kickoff` replaces its manual "open a terminal yourself"
block with an automated tab launch. The launch lives in one shared, testable
helper — `plugins/work-system/scripts/herdr-launch.sh` — with two subcommands:
`launch` (called from **both** `skills/kickoff/SKILL.md` step 13 and
`skills/adopt/SKILL.md` step 13 — see the adopt note below) and `resume` (called from
`skills/continue/SKILL.md`'s reopen path — the main session with a `<task>` arg, or
a *different* task's name given from inside a worktree). The helper is the source of
truth; this entry captures the durable design and one non-obvious gotcha.

## Design decisions

- **Encapsulate the launch in a script, not skill prose.** The deterministic
  sequence (gate → `agent start` → robust pane-id parse → `pane move` → exit code)
  lives in `herdr-launch.sh` so it can be `bash`-tested and reused — realized in
  work-system 1.9.3 by `/adopt` (see the adopt note below) — per the project's
  helper-script convention and the "prose skill logic drifts" memory. The skill only
  derives the label and branches on the helper's `moved=yes|no` / exit code.
- **Spawn the worker as argv, never type it into a shell** (the LEGACY contract —
  herdr ≤0.7.4; see "herdr 0.7.5 changed the launch contract" below for what the
  modern path does instead). The launch is
  `herdr agent start "<label>" … -- <worker argv>`, which execs the worker binary
  directly. As of work-system 1.9.0 the worker argv is **resolved from the chosen
  agent** by `agent-registry.sh` (`emit_argv`), not hardcoded: a claude worker is
  `claude --model <m> -n "<label>" "/work-system:continue"` (plugin-qualified — see
  the shadowing gotcha below), while codex/grok get their own
  `-m` form — `codex -m <model> "<bootstrap prompt>"` /
  `grok -m <model> "<bootstrap prompt>"`, and kimi (1.11.0) a two-phase
  `sh -c 'kimi -m "$1" -p "$2" || …; exec kimi -c --auto' …` — it has no positional
  launch prompt (see [[kickoff-agent-selection]]). `emit_argv` is the SoT; never
  reconstruct an argv from this list.
  herdr-launch stays CLI-agnostic — it just execs the resolved `argv=` words. The
  `-- argv` form sidesteps the interactive shell entirely, so there is no keystroke
  race against shell startup (see the gotcha below) and no readiness handshake to
  maintain.
- **`agent start` splits the caller's tab → move it out** (LEGACY only). `herdr agent
  start` (without `--tab`) lands the agent as a split pane in the *invoking* tab, so a
  second step `herdr pane move "<pane>" --new-tab --label "<label>"` relocates it
  into its own background tab — one tab per task. The pane id comes from the start
  call's `result.agent.pane_id` (parsed with `python3`; `herdr pane move` does **not**
  accept an agent name as target — verified). On 0.7.5+ there is no move at all.
- **One short `LABEL` for agent name + session.** `agent start "<label>"` (immediate,
  deterministic sidebar label) and `claude -n "<label>"` use one short,
  sidebar-friendly name (filler words like `automate`/`in` dropped). The `task/<name>`
  branch is untouched, so `/continue` still resolves the task from the branch.
- **Detection gate.** Automate only when `HERDR_ENV=1`, `$HERDR_WORKSPACE_ID` is
  non-empty (an empty `--workspace` lands the tab in the *focused*, possibly
  unrelated, workspace), and both `herdr` and `python3` are on `PATH`. `--no-focus`
  keeps the kickoff session in front; any failure (empty `$pane`) degrades to the
  unchanged manual block — never block kickoff on herdr.
- **Failure diagnostics surface herdr's own error (1.9.1).** Every herdr call on a
  failure path (`agent start`, `pane move --new-tab`, resume's `tab create`, resume's
  `pane run "claude -c"`) now captures stderr instead of `2>/dev/null` and runs it
  through a shared `herdr_diag` helper: parse herdr's `{"error":{"code","message"}}`
  JSON defensively (any exception falls back to the raw stderr text, never a
  traceback), print it, then the existing generic message stays as the last-resort
  line. This was diagnosability-only (no fallback/self-heal logic — actively rejected,
  see below) after a real incident where the launch failed with only "herdr agent
  start did not return a pane id" while herdr's stderr — discarded by the old
  `2>/dev/null` — had named the exact cause. When the parsed `code` is
  `agent_placement_not_found` (or the message otherwise names the workspace target),
  `herdr_diag` appends one hint line pointing at a stale `$HERDR_WORKSPACE_ID` — the
  env var is frozen at Claude-spawn time (see the `resume`/`reused` discussion below)
  and never refreshed, so a herdr server restart / `update --handoff` that reassigns
  workspace ids strands it. Stdout contract (`pane=`/`tab=`/`moved=`/… key=value
  lines, exit codes) is untouched — only stderr got richer.
  A swarm review of this change (external-only: codex + grok) caught three
  refinements before merge, all applied: (1) the stale-workspace hint is scoped by
  a `ws_relevant` flag on `herdr_diag` — only `agent start`/`tab create` actually
  send `--workspace`, so `pane move`/`pane run` never get an
  `agent_placement_not_found` misattributed to the workspace id; (2) the message
  fallback (no parseable `code`) requires the workspace id to appear as a
  bounded token, not a loose substring (`ws=w1` no longer false-positives on a
  message naming `w12`); (3) `herdr_diag` strips control/escape bytes from
  herdr's stderr before it reaches the terminal — herdr's stderr is untrusted
  server output, so an embedded ANSI/OSC sequence must never be interpreted by
  the user's terminal. **The diagnostic is only as useful as the skill layer
  that relays it** — kickoff/continue SKILL.md's failure branches now explicitly
  instruct relaying the helper's stderr to the user (they didn't before, which
  would have silently dropped this entire improvement at exactly the layer the
  original incident was observed at: the model narrating only the generic
  guard message, never the captured herdr error).
  **A second swarm-review round on that first-round fix found it was still
  wrong in four ways** — sanitization/anchoring code is exactly the kind of
  code whose own fix deserves an adversarial pass, not just the original
  feature: (1) control bytes were stripped only from the RAW stderr blob, but a
  JSON ``-style escape is still plain printable text at that point — only
  after `python3`'s `json.load` decodes it does it become a real ESC byte, so
  `code`/`message` need their own strip pass post-decode; (2) the "workspace id
  as a bounded token" fix from round one checked token-presence and keyword-
  presence as independent ANDs, so `"workspace w1 is healthy; agent placement
  is unavailable"` still false-triggered — a fixed character window wasn't
  enough either (a short sentence puts the keyword in range regardless), so the
  check now splits the message on `;`/`.`/`,` and requires both in the SAME
  clause; (3) the ERE-escaping of `$ws` before embedding it in a `grep -E`
  pattern only escaped `. [ \ * ^ $`, leaving `+ ? ( ) { } |` live — a
  workspace id containing one of those could match unrelated text as a regex,
  not a literal; (2) and (3) together made the bash sed/grep chain fragile
  enough that it was replaced with a single `python3 re`-based check
  (`re.escape` + per-clause matching, case-insensitive) instead of iterating
  the bash version further; (4) `$HERDR_WORKSPACE_ID` itself (interpolated into
  the hint line) was never sanitized — only herdr's own stderr was. All four
  fixed, plus: the orphaned-tab `herdr tab close` cleanup call's own stderr was
  being discarded (`2>&1 >/dev/null || true`) even though the CHANGELOG claimed
  every failing call surfaces stderr — now captured too. `test_herdr_launch.py`
  (new, mirrors `test_agent_registry.py`'s stub-the-CLI-on-PATH pattern) locks
  in all of this — every case above, plus the stdout contract, as regression
  coverage the first round shipped without.

## herdr 0.7.5 changed the launch contract (work-system 1.11.1)

herdr 0.7.5 (still current in 0.8.0) redefined `agent start`: it no longer places
the agent, it **starts one in an already-open pane at an interactive shell prompt**
— `agent start <NAME> --kind <KIND> --pane <ID> [--timeout <MS>] [-- <args>]`.
Placement moved to a preceding `tab create --workspace … --cwd … --label …
--no-focus`. work-system 1.11.0 still sent the old flags, so a real launch died with
`herdr error: unknown option: --workspace` → "did not return a pane id". Durable
decisions from the fix:

- **Feature-detect the contract, never version-compare.** `detect_launch_api()` reads
  `herdr agent start --help` (bounded, read-only — no process is started to find out):
  `--kind` AND `--pane` → modern; else `--workspace` AND `--cwd` → legacy; else
  **stop before creating or starting anything**. A version compare would be wrong by
  construction: 0.7.x spans BOTH contracts, so half that range routes to the wrong
  path. `$WORK_SYSTEM_HERDR_API` overrides it (hermetic tests, and an escape hatch if
  a future help text stops naming its flags).
- **The registry declares the transport; the launcher never infers it.**
  `agent-registry.sh resolve` emits `herdr_mode=agent-start|pane-run` +
  `herdr_kind=<canonical cli>`. `agent-start` means
  argv[0] IS herdr's canonical executable for that kind, so the launcher drops
  exactly that word and passes the rest to `--kind` unchanged — a *check*, not a
  transformation, which is what keeps argument order and boundaries (codex/grok's
  one-word bootstrap prompt, claude's one-word `/work-system:continue`) intact.
  `pane-run` is for wrappers that cannot be projected onto a native kind — kimi's
  two-phase `sh -c` seed+continue — sent as the registry's `argv_shell=` in ONE
  `pane run` command (no `eval`, no re-quoting). Unknown mode → fail closed BEFORE
  `tab create`. The pair is deliberately generic so a dynamically-registered wrapper
  (cc-harness, PR #52) is `pane-run` + `herdr_kind=claude` with no launcher change.
- **`agent_pane_busy` is the real-world failure, and the ONLY retried one.** Right
  after `tab create` the pane's login shell is still running its rc files (direnv,
  sops, ssh-add — measured at ~4s here), and herdr rejects the start with that code.
  It is retried within a bounded window; every other error is retried zero times,
  because a retry loop over an unclassified error is how a launcher starts two
  workers. Exit code 2 (clap usage text: "unsupported interactive agent kind",
  "missing required --pane", "unknown option: …") and no-such-pane codes are
  *definitive* — herdr never touched the pane.
- **Roll back only what provably started nothing; never kill a maybe-worker.**
  Definitive failures close the created tab exactly once through
  `herdr-teardown.sh close-tab` (the existing close-then-verify SoT — it deliberately
  does not re-issue the close, since a recycled tab id would kill an unrelated live
  tab). Ambiguous outcomes — readiness timeout, an unclassifiable herdr error, a
  returned pane id that isn't the requested one, a wrapper detected as the wrong kind
  — leave the tab alone and return the new fail-closed `blocked=unverified` result
  (exit 0, `blocked` FIRST). An *unconfirmed* rollback downgrades to the same result:
  a tab that may still exist must not be reported as a clean failure. Callers branch
  on `blocked` before `moved`, and on it must not relaunch, print the manual worker
  command, or persist a project default — mirroring the `resume` guard's shape.
- **Wrapper readiness is herdr's own detection, not a timer.** After `pane run` the
  launcher polls `pane get` until `result.pane.agent` equals the declared kind in
  that exact pane. Verified live: herdr reports `agent=kimi` within ~5s of a
  `kimi -p` seed (so the two-phase wrapper is detectable during phase 1, long before
  `exec kimi -c --auto`), and `interactive_ready` is only set for agents started via
  `agent start` — so it can't be the wrapper's signal. Before typing, a bounded wait
  compares `pane process-info`'s `foreground_process_group_id` to `shell_pid`: equal
  = the shell itself is in the foreground, i.e. safe to type into (the `pane run`
  path has no herdr-side readiness check the way `agent start` does). An unreadable
  answer degrades to "proceed" rather than blocking a launch forever.
- **`moved=yes` is kept on the modern path** even though nothing moves — it means
  "the verified worker is in its dedicated tab", and changing the key would break
  every caller for a cosmetic gain.

### Gotcha: a supervisor cannot take its signal from terminal text

Deciding "did this worker die?" from what the pane SHOWS was attempted three times
and failed three times. Each fix looked complete and closed exactly one channel:

1. The literal must not appear in the command the launcher TYPES — a pane echoes
   its command line, so the token inside the wrapper script matched itself and
   rolled a **healthy** launch back after 2s (found live). Fixed by assembling it
   at runtime (`m=WORKER_SEED` … `${m}_FAILED`).
2. It must not appear in anything the worker READS — the kimi seed's whole job is
   reading repository files, and in a work-system repo TASK.md, the CHANGELOG and
   the tests all legitimately name the marker. Fixed with a per-launch nonce.
3. But the nonce has to REACH the wrapper, and the only channel is the environment
   of the process being supervised — so the worker can print its own death
   certificate. And a rendered `pane read` snapshot is width-wrapped, so in a
   narrow pane the match silently misses anyway. Forgeable *and* unreliable.

The signal that works is process STATE, which output cannot fake — but not the
obvious form of it. "The pane left its prompt (wrapper running) and came back
(wrapper died)" is unusable: measured live, a seed that fails on startup — the case
this exists to catch — is gone before the first 0.5s poll, and every sample reads
`ready`. What is observable is the steady state: a `sh -c` wrapper puts a child in
the foreground, so while it runs the pane is busy or its worker is detected. A pane
sitting at its OWN shell prompt, no agent, for N consecutive polls (and past a
wall-clock floor, so a `sleep` that ignores fractional seconds cannot collapse the
budget) means nothing is running there — whether the seed died or the keystrokes
never landed. Measured: a dead wrapper fails in ~5s with its tab rolled back, a
healthy one is confirmed in ~2s; the floor sits between those two numbers on
purpose. The wrapper still PRINTS its marker — for whoever reads the tab, not for
the launcher.

Generalizes twice over: a supervisor that greps a terminal must own that signal end
to end (it may not appear in what it types in, nor in anything the supervised
process can read and echo — closing only the first channel looks complete and
isn't); and when you switch to process state, verify WHICH state transition is
actually observable at your polling rate before trusting it.

**Legacy has the same question with a cleaner answer.** There the wrapper is the
tab's ROOT process, so an instantly-failing seed takes the pane and the tab with
it — and the legacy path would still print `moved=yes` for a tab herdr had already
closed. It now checks pane liveness through the pane LIST, tri-state: `gone` is
definitive, an unreadable herdr is `blocked=unverified` (never death — declaring a
live worker dead sends the caller to its manual block and invites a SECOND
unattended worker onto the worktree). Native legacy launches skip the check
entirely: their argv cannot self-terminate on a bad seed, and that contract is
meant to stay byte-identical.

## `/adopt` auto-launch: reference kickoff's prose, don't duplicate it

work-system 1.9.3 gave `/adopt` the same in-herdr tab launch as `/kickoff`: after it
builds the worktree from an existing branch, `skills/adopt/SKILL.md` step 13 calls the
identical `herdr-launch.sh launch "$LABEL" "$WORKTREE" "$HERDR_WORKSPACE_ID" "$SELECTOR"`
(step 12 resolves the worker selector exactly as kickoff does — `/adopt` grew an
optional `[agent-selector]` arg for it). Two durable decisions:

- **One copy of the intricate branching.** The launch *helper* is already the single
  source of truth for the mechanics, but the *skill prose* around it (the picker/announce
  rules of step 12, and the exit-0-`moved`/exit-2/exit-3/non-zero result branching of
  step 13) is stateful logic that drifts if copied — the "prose skill logic drifts"
  memory. So adopt's steps **reference** `kickoff/SKILL.md` step 12/13 for that shared
  logic and inline only the adopt-specific deltas, rather than a full paraphrase that
  would silently diverge under later edits.
- **Adopt-specific deltas that must stay inline.** (1) The `LABEL` derives from the
  *resolved* task name (prefix-stripped), because `/adopt` may **keep the original
  branch name** rather than rename to `task/<name>` — deriving from the branch would
  give a nonsense label. (2) The success/manual templates show `<current-branch-name>`
  (the adopted branch, possibly not `task/<name>`), not kickoff's assumed `task/<name>`.
  (3) The worktree path is built from the `<main-repo>` captured in adopt step 1, never a
  possibly-drifted CWD (adopt runs in the main-repo session — its whole cwd-safety spine).

## `resume` mode: reopen a task tab a `/exit` closed

A LEGACY kickoff tab runs Claude as its **root pane** (argv-exec above), so a clean
`/exit` — even one only meant to restart Claude Code — ends the pane and herdr closes
the whole tab; the worktree and resumable session persist, but the tab is gone. (On
0.7.5+ the worker is started INTO a shell pane, so `/exit` leaves a bare shell in
the tab instead — `resume` then finds that tab still open and only *focuses* it
(`reused=yes`, `resumed=` empty); the `claude -c` is the caller's to run, since a
cwd match cannot tell a live Claude from a surviving shell.)
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
  machinery with no way to live-verify here. So legacy kickoff tabs still die on
  `/exit`; reopen is the one-command recovery, and reopened tabs are hardened.
  **herdr 0.7.5+ settled this by force:** `agent start` requires an existing pane, so
  a modern kickoff worker is *already* a shell-pane process and its tab survives
  `/exit`. Live-verified consequence: it is still a **registered agent** (started via
  `agent start`), so `agent_status` keeps working and `/close`'s poller is unaffected
  — the marker + SessionEnd hook, not the exit itself, closes the tab.
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
  - **Fail CLOSED on uncertainty, via a single-pass tri-state lookup.**
    `worktree-tab-state` returns `<tab>` / `none` / `unverified` — not a bare empty
    string that conflates "no tab" with "couldn't check." Only a POPULATED list where
    every tab pane has a READABLE cwd and one EXACTLY matches yields a tab (reuse), or
    `none` (create) when all readable cwds miss. Everything ambiguous → `unverified`:
    herdr unreachable, an EMPTY/repopulating pane list (the empty-≠-gone hazard
    `extract_tab_present` also guards), a malformed/errored parse (any exception prints
    `unverified`, never a false `none`), OR a tab pane whose cwd is empty/unreadable
    (can't rule out that it IS the worktree tab). On `unverified` the helper emits a
    lone `blocked=unverified` (exit 0, not a generic failure) and the skill tells the
    user to CHECK herdr for an existing tab before reopening by hand — so the
    fail-closed path can't itself cause the duplicate (a plain manual block wouldn't cue
    the check). It mirrors `extract_tab`'s `norm()` via a **shared prelude string**
    concatenated into both (defined once, so the guard and `/close` can't drift on path
    matching); the match/output logic stays separate because `/close`'s `worktree-tab`
    must not inherit the tri-state.
    - **Exact-match only — subtree-matching was tried and reverted.** Round 5 made a
      pane in a worktree *subdirectory* fail closed (to catch a tab that `cd`'d into a
      subdir); that deterministically blocked auto-reopen whenever ANY unrelated pane
      sat under the worktree (e.g. a shell in `<worktree>/logs`). Reverted to exact
      match. Accepted residual gap: a task's own tab that wandered into a subdir won't
      be detected and a reopen could duplicate — narrow (reopen → `/exit` → `cd subdir`
      → reopen again), and the alternative over-blocked the common case.
  - **Search all workspaces of the current herdr SERVER (dedup only).** The lookup
    passes an empty workspace so a still-live tab for this worktree in a *different*
    workspace is also found (worktree paths are globally unique); the tab is still
    *created* in `$HERDR_WORKSPACE_ID`. This rests on one taken-on-faith assumption:
    that an unscoped `herdr pane list` spans *every* workspace of the server, not just
    the focused one — not live-verifiable here; were it workspace-local, a cross-workspace
    tab could go undetected and be duplicated. Two accepted limits: (a) `herdr pane list`
    only spans the current herdr *server*, so a session for the same worktree in a
    *separate* server (another Ghostty tab) is invisible and could duplicate — herdr
    can't be queried across servers; (b) reopening a *different* task from inside a
    worktree lands its tab in the current session's workspace, which a later `/close`
    (scoped to its own workspace) may not locate — it then prints its manual-close line
    (graceful, no data loss). The unscoped search also means one unreadable-cwd pane
    ANYWHERE makes the guard `unverified` → reopen drops to the (cued) manual path;
    fail-safe, no duplicate.
  - **Re-anchor cwd before `claude -c`.** The reopen sends `cd <worktree> && claude -c`
    (shell-quoted), not a bare `claude -c`: the pane is created with `--cwd`, but the
    shell's rc (direnv/zoxide/an unconditional `cd`) can drift the cwd on startup, and
    `claude -c` resumes the most-recent session *for the current cwd* — a drift would
    silently attach to a different task. `launch`'s argv-exec has no shell, so it's
    immune; this is the shell-pane path paying for that.
  - **Don't assert a live resume on reuse.** A cwd match can't distinguish a live
    Claude from a bare shell that survived a prior `/exit`, so the reuse branch emits
    `resumed=` (empty), and the skill tells the user to run `claude -c` if the focused
    tab is just a shell — never a false "already resumed."
  - **Report `resumed=no`/`focused=no` honestly.** A failed `pane run "claude -c"`
    send → `resumed=no` (user runs it by hand); a failed/absent `tab focus` →
    `focused=no` (skill doesn't claim a focus that didn't happen). The tab-create
    response is parsed pipe-delimited (`<pane>|<tab>`) so an empty pane id can't be
    mis-read as the tab id. If that parse yields a tab id but *no* pane id (schema
    drift / pane-less result), the just-created tab is closed (`herdr tab close`) before
    the helper bails, so a drifted response can't orphan a blank tab on every resume.

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

## Gotcha: launch the worker with `/work-system:continue`, never bare `/continue`

The claude worker's initial prompt is the **plugin-qualified** `/work-system:continue`,
not the bare `/continue` (work-system 1.9.2 fix). Bare `/continue` is not a safe way to
reach a plugin skill: a Claude Code built-in/alias `/continue` can shadow it, and — per
CC's own docs — plugin skills live under a `plugin-name:skill-name` namespace and are
only *guaranteed* reachable via that qualified form; the bare name resolves to a skill
only when nothing at a higher precedence claims it. In a fresh worktree session
(`claude -n <name>`, no prior conversation) a shadowed bare `/continue` either runs CC's
own resume (nothing to resume → worker sits idle, TASK.md never loaded) or errors as an
unknown command — either way the work-system resume flow never runs, breaking the core
kickoff→worker handoff. The exact mechanism (a built-in vs namespace-only resolution)
is CC-version-dependent and was reported intermittently; the fix is orthogonal to which
it is, because `/work-system:continue` is the one documented, unshadowable invocation.
**Do not "simplify" it back to the bare form.**

Sites carrying the qualified form (all machine-generated invocations): `agent-registry.sh`
`emit_argv` (the primary selector path), `herdr-launch.sh`'s legacy no-selector fallback,
and kickoff's outside-herdr manual block; `test_agent_registry.py` asserts the argv ends
in `/work-system:continue`. **Deployment caveat:** `agent-registry.sh` / `herdr-launch.sh`
run from the *plugin cache*, not the repo, so a launch only picks up this fix after a
`/plugin` marketplace update + reload refreshes the cache.

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

**On herdr 0.7.5+ the argv escape hatch is gone** — `agent start` needs a pane, so
every launch goes through one. The race is handled rather than avoided: for native
workers herdr owns it (it refuses with `agent_pane_busy` until the pane is at a
prompt, which the launcher retries), and for `pane run` wrappers the launcher gates
on `process-info` showing the shell itself in the foreground before typing. Same
guarantee, different owner — which is *why* the busy retry is not optional
book-keeping but the modern equivalent of argv-exec's race-freedom.

Related: [skill-composition](../architecture/skill-composition.md) (kickoff softly
drives `/continue` across a process boundary) ·
[worktree-task-file-copy](../architecture/worktree-task-file-copy.md) (why the
worktree gets a `TASK.md` *copy*, not a symlink). The "never persistent `cd`" footgun
that the worktree commands avoid is a rule, not knowledge — see `.claude/rules/cwd-safety.md`.
