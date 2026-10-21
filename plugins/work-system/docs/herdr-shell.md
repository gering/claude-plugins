# Interactive programs from an agent: `expect`, `handover`, `herdr-shell.sh`

An agent's shell tool has no terminal. When a command wants one, pick the tool
by what the program needs, not by what is at hand:

| The program… | Use | Why |
|---|---|---|
| asks line-based questions (y/n installer, REPL, `read`, `getpass`) | **`expect`** (ships with macOS) | Stream matching consumes its buffer: no stale matches, no screen scraping, no tab to clean up. |
| needs a credential **the user owns** (`kimi login`, 2FA, an SSO page) | **`herdr-shell.sh handover`** | The user types it. The agent waits on process state and never reads the pane while the field is up. |
| is screen-based (vim, `git rebase -i`, a TUI) or should be watchable | **`herdr-shell.sh`** (`open` / `run` / `text` / `keys` / `prompt` / `read`) | The rendered screen is the interface, and the user can take over the tab. |
| needs an **agent-held** secret at a password prompt | **`herdr-shell.sh send-secret`**, and only when its gate passes | See below. Prefer `SSH_ASKPASS`/`SUDO_ASKPASS` where the program supports it: no pane involved at all. |

Out of scope: auto-answering raw-mode credential prompts, `sudo`, network logins.

## The helper

`plugins/work-system/scripts/herdr-shell.sh` — runs only inside herdr
(`HERDR_ENV=1`; elsewhere it exits 3 and does nothing). `herdr-shell.sh help`
prints the full reference. A typical screen-based session:

```sh
HS="${CLAUDE_PLUGIN_ROOT}/scripts/herdr-shell.sh"
out="$(bash "$HS" open rebase "$PWD")" || exit
tab="$(printf '%s\n' "$out" | sed -n 's/^tab=//p')"
trap 'bash "$HS" close "$tab" >/dev/null 2>&1' EXIT   # error paths too

bash "$HS" run "$tab" 'git rebase -i HEAD~3'          # at the shell: run
bash "$HS" prompt "$tab" 'git-rebase-todo' 10000      # vim's status line
bash "$HS" text "$tab" ':2s/^pick/drop/'              # inside vim: text + keys
bash "$HS" keys "$tab" Enter
bash "$HS" text "$tab" ':wq'
bash "$HS" keys "$tab" Enter
```

Rules that came out of measuring herdr 0.8.2 on macOS with zsh + powerlevel10k
(2026-10):

- **Absolute cwd only.** herdr resolves a relative `--cwd` against its server.
- **Ready means ~1 s of shell foreground.** rc files flip the foreground several
  times while zsh starts; text typed too early shows up as literal bracketed-paste
  markers. `open` waits for that; a tab that never settles is closed again.
- **`run` at the shell, `text` + `keys Enter` inside programs.** `run` sends
  bracketed paste, which some programs (`vim -u NONE`) take as keystrokes.
- **`prompt`, not `wait-output`.** It matches the last non-empty visible line
  (trailing padding stripped — anchor as `Password:$`) after a 400 ms quiet
  window. `wait-output` matches the previous identical prompt immediately, and
  `read --lines N` returns blank rows below the cursor.
- **Process state says which program has the terminal, not whether it wants
  input.** That is why `handover` waits on it (the program ends) and `prompt`
  reads the screen.
- **Only own tabs.** `run`/`text`/`keys`/`read`/`prompt`/`state`/`handover`/
  `send-secret` work on tabs this caller recorded, and only while the live tab
  still looks like a temp tab (`tmp-` label, no agent in it, the recorded pane):
  a record whose id herdr reissued after a restart is dropped, never driven.
  `close` takes such a tab, or an unrecorded tab labelled bare `tmp-…` (no state
  glyph: `● tmp-x` is a lane tab) without an agent, never the caller's own or
  another live caller's. `open` needs `$HERDR_PANE_ID`: the record's owner. `sweep` closes this caller's records, a dead caller's, and
  every unrecorded `tmp-` tab in the workspace — a `kill -9`'d caller cannot run
  its trap, and a `tmp-` tab made by hand is swept too. Records are per state dir
  (`HERDR_SHELL_STATE_DIR`), so callers with different state dirs look unrecorded
  to each other.
- **Close temp tabs before `/close`.** A tab opened at a worktree (or the main
  checkout) has the same cwd as the lane's own tab, and `/close`/`/continue` find
  a lane's tab by cwd — a leftover `tmp-` tab there can be picked instead.
- **`handover` sees process state, not completion.** A program that ends within
  one 100 ms poll is never seen and reports `result=no-program`.

## Agent-held secrets: `send-secret`

```sh
bash "$HS" send-secret "$tab" --prompt 'passphrase.*:$' --from-keychain my-dummy-item
bash "$HS" send-secret "$tab" --prompt 'Password:$'     --from-file ~/.config/x/pw   # 0600, owned, no symlink
```

- **Socket only.** `herdr pane send-text` would put the secret into an argv,
  which exec auditing records. The helper builds the request in memory and writes
  it to `$HERDR_SOCKET_PATH`; never pass a secret to `run`/`text`.
- **The gate is termios, not the screen.** It sends only when the prompt regex
  matched **and** the pane's tty is `echo=off ∧ icanon=on` — the kernel-line-
  discipline password prompts (`read -s`, `getpass`, ssh passphrases). It
  re-checks right before sending, then sends the tty's `kill` char (clears the
  line), the secret and Enter in one socket request, and never reads the pane
  afterwards. Residual: a reader that gives up in the one round trip after the
  last check receives the secret as typed input.
- **Also refused:** no `kill` char on the tty (the line cannot be cleared); a
  secret containing any of the tty's special chars (`kill`, `erase`, `eof`,
  `eol`, `intr`, … — they act on the line, even when remapped to a printable one);
  a socket at `$HERDR_SOCKET_PATH` that is not yours; a foreground herdr
  cannot identify (setuid readers like `sudo`); a shell reading by itself unless
  it is zsh/bash (their idle prompt is raw mode; `sh`/`dash` read canonically); a prompt that changed between the
  match and the send.
- **Keychain items must be printable ASCII.** `security find-generic-password -w`
  prints other passwords as hex, which would be typed as-is.
- **What the gate cannot prove:** *which* program reads, or that it never prints
  what it read. A script that prints `Password:` and turns echo off passes. Send
  only into a program you started for that purpose.
- **Refused:** echo on (it would be displayed); raw mode (`icanon` off) — masked
  `*` prompts, editors, and zsh's own line editor at an idle prompt, where a
  "secret" would become a command line. Hand those to the user with `handover`.
- **No echo probe.** At a no-echo prompt a probe is unobservable; in a raw-mode
  program it types keybindings.
