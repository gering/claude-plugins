---
title: "Manager Address Resolver and Milestone Pings"
createdAt: 2026-10-02
updatedAt: 2026-10-07
createdFrom: "session: 2026-10-02 (task/add-manager-address)"
pluginVersion: 1.18.0
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
  `/close` delegation question names the evidence and the user confirms.
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

## Gotchas

- **Composer draft detection** reads `herdr agent read --source visible --format
  ansi` and inspects the region between the last two horizontal rules. The UPPER
  rule carries a label (CC prints the session name into it), so a rule is "starts
  with ten `─`", not "consists only of `─`". Dim and **mid**-gray cells are not a
  draft: CC renders prompt suggestions that way, and the pilot showed suggestions
  are not user input. Near-black is NOT muted (it is the normal text color of a
  light theme). An inverse glyph is the cursor: next to muted text it is the first
  char of a suggestion (clear), alone or next to normal text it is typed (draft); an
  empty composer's cursor is an inverse space. SGR state
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
