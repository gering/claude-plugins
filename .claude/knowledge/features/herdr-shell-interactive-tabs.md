---
title: "herdr-shell.sh: interactive programs in temporary herdr tabs"
createdAt: 2026-10-21
updatedAt: 2026-10-21
createdFrom: "PR #87"
updatedFrom: "PR #87"
pluginVersion: 1.9.0
prime: false
---

# herdr-shell.sh: interactive programs in temporary herdr tabs

`plugins/work-system/scripts/herdr-shell.sh` drives screen-based programs in
`tmp-` tabs and hands user-only flows (logins, 2FA) to the user. When to use it
instead of `expect`, and the measured screen/readiness rules, live in
`plugins/work-system/docs/herdr-shell.md`. Read that first. This entry keeps
the design decisions and traps that are not obvious from the doc.

## Decisions

- **Gate on termios, not on the screen.** An agent-held secret is sent only
  when the pane's tty is canonical and no-echo (`stty -a -f /dev/<tty>` from
  outside). zle and every raw-mode prompt have icanon off, so they are refused
  and go to `handover`. `read -s` is a shell *builtin*: the shell stays in the
  foreground while it waits, so "a program holds the foreground" can't be part
  of the gate. The gate cannot tell *which* program reads, which is why the doc
  says to send only into a program you started for that purpose.
- **Socket, never argv.** `herdr pane send-text` puts its text into an argv.
  `send-secret` writes one `pane.send_input` request (kill char + secret + Enter)
  to `$HERDR_SOCKET_PATH`. herdr answers one request per connection and then
  closes. A timeout after the request went out is reported as "may have been
  delivered — do not resend". The socket must be the user's own. There is no
  `--no-enter`: a secret left unsubmitted would become typed input for whatever
  reads next.
- **The secret is fetched before the gate.** A Keychain lookup can block on an
  approval dialog. The prompt match, the termios checks and a final re-read of
  the prompt line therefore all run after it, right before the send.
- **The foreground is decided by which pids hold it**, the same way as
  herdr-launch.sh's `extract_shell_ready`. Comparing the fg pgid with the shell
  pid fails without job control. A setuid foreground (top, sudo) makes herdr
  omit the fields. That is `unknown`: `send-secret` refuses it, and `handover`
  counts it as "program running" only while `shell_pid` stays readable.
- **Record ownership.** One TSV per herdr socket, written under a single flock.
  `open` holds the lock from `tab create` until the record is written, and
  `sweep` takes its tab-list and record snapshot under the same lock. Otherwise
  a concurrent sweep closes a freshly created tab before it is recorded.
  Every use of a record re-checks the live tab (`Live` in the script): still
  there, prefixed label, no agent in it, the recorded pane still in it.
  Otherwise the record is dropped, never driven, because herdr can reissue ids
  after a restart on the same socket, and `open` replaces any older row for the
  same id. Agent-hosting tabs are never temp tabs. An *unrecorded* tab also needs
  the bare prefix (`tmp-x`, no state glyph), because a lane tab is stamped
  `● tmp-x` at launch and keeps the glyph after its worker exits.
  `close`/`sweep` never close a tab that another live caller pane recorded.
  `sweep` refuses to run without a workspace, because its unrecorded-`tmp-` pass
  trusts labels alone.

## Traps

- **`python3 -c` puts the cwd first on `sys.path`.** The core is embedded
  Python, so a `json.py` in the caller's repo would shadow the stdlib and run
  inside `send-secret`. It runs as `python3 -I -c`. Keep `-I` on any embedded
  Python that handles secrets.
- **A heredoc inside `$(…)` makes bash scan the body** for quotes and parens,
  and Python source trips it. The script captures the body with
  `IFS= read -r -d '' VAR <<'PY'`.
- **`pane run` sends bracketed paste.** `vim -u NONE` takes it as keystrokes
  (`:q!` never quit), so inside programs use `text` + `keys Enter`.

## Known gap

`herdr-teardown.sh` (used by `/close` and `/continue`) finds a lane's tab by pane
cwd. pane-list JSON carries no tab label, so a leftover `tmp-` tab at the
worktree cwd can be picked instead of the worker's tab. The doc tells callers to
close temp tabs before `/close`. The proper fix is to skip `tmp-` tabs in the
teardown lookup.

## Tests

- `test_herdr_shell.py` is hermetic. It uses a stateful fake `herdr`
  (`"<key>@after"` switches a response once another call has run N times), fake
  `ps`/`stty`/`security`, and a fake socket server. It also compiles the embedded
  Python, which `bash -n` never sees, and checks that the glyph set matches
  `herdr-tab-glyph.sh`'s `strip_glyph`.
- `smoke_herdr_shell.py` is the live run inside herdr. It is named `smoke_*` so
  `check-structure.py` (which runs `test_*.py`) never opens tabs in a user's
  session. It closes only its own tab: a workspace-wide `sweep` from a test would
  close other callers' unrecorded `tmp-` tabs.
