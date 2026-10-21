---
title: "Manager Address Resolver and Milestone Pings"
createdAt: 2026-10-02
updatedAt: 2026-10-20
createdFrom: "session: 2026-10-02 (task/add-manager-address)"
updatedFrom: "branch: task/add-session-plugin-refresh"
pluginVersion: 1.9.0
prime: false
---

# Manager Address Resolver and Milestone Pings

`scripts/manager.sh` answers "who is this project's Manager, and how do I reach
it?" with both addresses: the herdr pane/agent session and the CC SendMessage
name. Consumers: `/continue` milestone pings (PR opened, review round started,
terminal gate) and `/close` step 1b delegation, which replaced the name-only
`herdr-teardown.sh manager-session` detector (removed, so there is one resolver).

## Decisions

- **Kicker record first, revalidated live.** `/kickoff`/`/adopt` write
  `.ws-kicker` (git-excluded the way `MANDATE.md` is) from the kicking session's
  own `herdr agent get $HERDR_PANE_ID`. It wins only if the same pane still exists,
  carries the same agent-session UUID (a reused pane must not inherit the role),
  sits at the canonical main-repo root and is live. A stale record is reported as
  a `reason=` and the root scan runs instead. The record is an address, never an
  authorization: it sits in the worker-writable worktree, so a worker could point
  it at any live root agent. `evidence=kicker` is a better guess, not proof — the
  `/close` delegation question names the evidence and the user confirms. The one
  exception is `/close --delegate` (what `/land` passes): it sends without asking
  on `delegate_unasked=yes` (kicker, or sole root agent with no record or a gone backfill session),
  after printing the recipient — an accepted residual, see
  [herdr-close-automation](herdr-close-automation.md).
- **Backfill from the Manager, never over a live record (1.24.0).** Lanes kicked off
  before the record existed have none, so with two root sessions `/land` hit the
  leftmost-tab question every time. `manager.sh backfill` (run by `/list` and
  `/kickoff`) writes the *caller* as kicker (`source=backfill`, surfaced as
  `kicker_source=`) into every lane with **no** record. Lessons from two review
  rounds on PR #82:
  - **Fill absent, never replace.** A first version also rewrote "stale" records
    (pane gone, session changed). Every proof of staleness leaked: one snapshot of
    one herdr server, a pane briefly missing while claude restarts, an empty session
    id, a record refreshed between judging and writing, workspace ids reused across
    servers. Each fix needed another guard; dropping the rewrite removed the class.
    `resolve` already ignores a stale record, so replacing it bought nothing.
  - **Only the sole root agent may backfill.** Round 1 let any root session stamp
    itself; round 2 restricted it to the resolver's pick, which still included a
    leftmost-tab winner — and a backfilled tie-break became an unasked send,
    against the task's own rule that `leftmost-tab` keeps asking (round 3). Now
    backfill resolves without a record, needs `evidence=sole-root-agent` on the
    caller's own pane and a workspace scope, and skips lanes an agent works in
    (any subdir) from another workspace.
  - **A stale backfill record counts as none** (`kicker-backfill-stale`). It only
    ever said "the sole root agent then"; without this, a Manager restart would
    turn a lane that delegated unasked into one that asks forever. A backfill
    record from another workspace is ignored (`kicker-other-workspace`).
  - **Accepted residual:** a worker that deletes its `.ws-kicker` makes its lane look
    never-recorded; no stronger than forging the record, already accepted.
  An absent record is created by `link` (no-clobber, so `/kickoff` wins a race);
  the temp file is opened `O_EXCL|O_NOFOLLOW` because the worktree is
  worker-writable.
- **The live title must still match the recorded name.** The SendMessage name is
  the pane's live title, which any process in the pane can set; a kicker whose
  title differs from the recorded name keeps its pane but gets no SendMessage
  address (`reason=kicker-name-changed`).
- **No terminal id stored.** Terminal ids change across a herdr live-handoff;
  pane + cwd + agent-session UUID survive it (pilot 2026-09-06). Identity is those
  three. The terminal id is never part of the record.
- **Leftmost tab is a stated tie-break.** With several live root agents (the
  Manager plus an advisor session is the normal case here), the leftmost tab of the
  workspace wins and the answer says so: `evidence=leftmost-tab` and
  `reason=tie-break-leftmost-of-N`. Two candidates in that one tab, candidates in
  several workspaces, or a missing tab order → `ambiguous`. An unreadable row
  anywhere in the scope, an empty or malformed list, or no herdr → `unverified`.
  Consumers act only on `unique`.
- **One classifier, one sanitizer.** Root matching reuses `classify_cwd` from
  `$HERDR_MATCH_PRELUDE`; the SendMessage name sanitizer (title-derived, control
  chars blanked, spinner glyph stripped, 64-char cap) moved into
  `$HERDR_NAME_PRELUDE` in `herdr-agent.sh` and is shared with `herdr-teardown.sh`.
- **Native workers ping too.** codex/grok/kimi never run `/continue`, so
  `agent-registry.sh bootstrap_prompt()` carries the three milestones and the
  `manager.sh prompt` call.
- **Leftmost-tab `unique` is deliberate** (a task requirement): pings are
  information only, and `evidence=` keeps the guess visible. The state-changing
  consumer (`/close` delegation) accepts any `unique` because it asks first and
  states the evidence in that question.
- **The lane is the worktree toplevel.** `resolve`/`body`/`prompt` walk a lane
  argument (default cwd) up to `git rev-parse --show-toplevel`, like `mandate.sh`;
  from a subdirectory the record and the task identity were otherwise missed.
- **Two routes, one body.** `manager.sh body` builds the single attributed line
  (`[work-system ping from task=… worktree=…] … (info only…)`) both routes send.
  SendMessage is skill-side (a script cannot call a tool) and needs exactly one
  live session with the name. The herdr route `manager.sh prompt` re-resolves and
  types only into an idle/done **claude** Manager whose composer holds no draft.
- **One event cap, never silent.** The event text keeps 1000 chars on both
  routes; a cut ends in `…` plus `truncated=yes orig_len=<n>` on stderr (a plain
  300-char slice once lost a gate ping's open decision unnoticed). No shorter
  herdr cap: a measured ~4000-char `herdr agent prompt` (0.8.2) reached a claude
  composer intact and was answered. Python reads the event (`PY_EVENT`, argv
  or stdin): one byte past 16 KiB flags a cut (`stdin_clipped=yes`) even when
  the kept window folds to nothing, and it hands back text already passed
  through the body's one `clean()`, so the empty check (usage) sees what would
  be sent: a second, slightly different fold once let `[ ]` through as a blank
  ping. Never let raw
  bytes cross `$(...)`: substitution drops NULs and trailing newlines, and three
  review rounds found a miscount in each shell-side trick (sentinel, temp file)
  in turn. `orig_len` counts chars before folding, minus a heredoc's final
  newline; with `stdin_clipped` it is a lower bound.
  On `body` the signal comes before any send (shorten, rebuild); on `prompt` it
  precedes the composer checks, so the cut line was delivered only on `sent=yes`
  (then report the cut, never resend).

## Gotchas

- **Composer draft detection** (`$HERDR_COMPOSER_PY` in `herdr-agent.sh` since
  work-system 1.26.0, shared with `sessions.sh refresh`) reads `herdr agent read --source visible --format
  ansi` and inspects the region between the last two horizontal rules. The UPPER
  rule carries a label (CC prints the session name into it), so a rule is "starts
  with ten `─`", not "consists only of `─`". Dim and **mid**-gray cells are not a
  draft: CC renders prompt suggestions that way, and the pilot showed suggestions
  are not user input. Near-black is NOT muted (it is the normal text color of a
  light theme), and one gray rule (neutral, 90..180; 180 keeps light-gray theme
  foregrounds typed) covers both encodings: a
  256-color grayscale-ramp step is mapped to its xterm RGB first, so `38;5;240` and
  `38;2;88;88;88` agree (both typed). Color-cube grays are not mapped (typed). The
  floor stays at 90 even though that drops idx 240: lowering it to keep one index
  muted made truecolor 80..89 text read `clear` (round-5 review). An inverse glyph
  is the cursor: only when the very NEXT cell is a muted glyph is it the first char
  of a suggestion (clear); a blank there (any style; round 6 showed blank styling
  is unreliable both ways) or normal text makes it typed (draft). Paste/image
  placeholders (`[Pasted text #n`, `[Image #n`) are a draft whatever their style.
  Adjacency, not
  "any muted cell in the region": a region-wide flag let an unrelated dim hint turn
  a fully inverse draft into `clear` (round-4 review). An empty composer's cursor is
  an inverse space. SGR state
  carries across lines (a wrapped suggestion). No rules found → `unknown` → no send.
  All of this errs toward `draft`: a false draft costs one ping, a false `clear`
  types into the user's text.
  The SGR parse must be a real state machine: off-codes (22 dim, 27 inverse,
  39 default fg) remove state, and the sub-parameters of `38;5;n` / `38;2;r;g;b`
  are consumed — a bare "is 2 or 7 in the param list" check read `38;5;2`
  (green) as dim and a color component 7 as inverse, so a real draft read
  `clear` (found by three families in review).
- **Fallback scope is the caller's workspace, never the record's.** The record is
  unvalidated until PY_RESOLVE revalidates it; letting it scope the root scan made
  a stale record (workspace migrated) aim the scan at the wrong workspace.
- **Check-then-send is not atomic.** herdr has no "prompt only if the composer is
  empty"; a user who starts typing between the read and `herdr agent prompt` gets
  the ping appended. Accepted residual: the window holds only the read and one call
  (lane and root are resolved once per invocation, the body is built first).
- **Event text goes in on stdin.** A quoted argument broke on an apostrophe
  (`can't`), and `$(...)` in double quotes ran in the worker's shell before
  `manager.sh` could sanitize anything; `prompt -- <<'EOF'` makes the text pure data.
- **Scope before cwd.** A row readably in another workspace is skipped before its
  cwd is read, so an agent still starting up elsewhere (cwd null) cannot veto this
  lane. A cwd-less row in scope still makes the answer `unverified`.
- **Tab order is fetched lazily, into a file.** The resolver prints
  `need_tabs=<ws>` only when a tie-break needs it; the caller then runs
  `herdr tab list` into a temp file and re-runs. A file, not env/argv (the E2BIG
  lesson from `herdr-tab-glyph.sh`).
- **Ping text is a user turn on the herdr route.** The sender prefix and the
  info-only trailer can be imitated, so the skill (and the native-worker
  bootstrap prompt) restricts event text to fixed shapes and forbids relaying
  third-party content. Task/worktree fields are sanitized and stripped of
  brackets.
- **Non-claude Managers get no herdr ping.** Native Codex trust dialogs read as
  `idle` on herdr 0.8.2, and there is no verified composer parser for them, so
  `prompt` refuses (`draft-check-unsupported-for-<agent>`). That is an accepted
  gap until a Codex composer reader exists.
- **The agent list status can disagree with `agent explain`** (seen live: list
  `working`, explain `blocked` on a form). `herdr agent prompt` rejects a blocked
  agent itself, which backs up the status gate.

Related: [manager-worker-orchestration](../architecture/manager-worker-orchestration.md),
[herdr-close-automation](herdr-close-automation.md), [lane-registry](lane-registry.md),
[worker-autonomy-mandate](worker-autonomy-mandate.md).
