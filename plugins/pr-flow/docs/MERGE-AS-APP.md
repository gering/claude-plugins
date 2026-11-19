# Merging as a GitHub App (`/merge --as <app>`)

`/pr-flow:merge --as <app>` (and work-system's `/land --as <app>`) runs **only** the
`gh pr merge` command under a GitHub App's identity. pr-flow ships no code for this:
it prefixes that one command with `GH_APP_MERGE='<app>'` and relies on a `gh` shim on
your machine to switch identity. Every other `gh` call in the session keeps your
default login.

## The contract pr-flow relies on

A shim earlier on `PATH` than the real `gh` must:

1. **Answer the probe.** `GH_APP_MERGE='<app>' gh __shim-probe` prints exactly
   `merge_app=<app>` and exits 0 when that app is set up and allowed; otherwise it
   exits non-zero. `/merge` runs the probe before it changes anything and stops on
   any other answer. Without a shim the real `gh` rejects the unknown command, so
   the run stops instead of merging under your login.
2. **Switch identity for the merge only.** With `GH_APP_MERGE` set, `gh pr merge`
   runs with a token of that app's installation. Without it, the shim passes every
   call through unchanged.
3. **Refuse, never fall back.** An app without a key on this machine, or one not on
   your allowlist (a missing allowlist allows nothing), is an error, not a merge
   under your own login.
4. **Name apps by their GitHub slug.** `<app>` is the App's slug, so the `mergedBy`
   check can compare it with what GitHub reports.

After the merge, `/merge` reads `mergedBy` and warns in its summary when it is not
`<app>` / `<app>[bot]`.

## Reference shim (example, not shipped)

Assumptions, adapt to your setup: each app's private key lives at
`~/.config/github-app-<app>/private-key.pem`; a helper `gh-as <app> <gh args…>`
mints an installation token for that app and runs the **real** `gh` (by absolute
path, never `gh` from `PATH`, which would re-enter the shim) with it as
`GH_TOKEN`; the real `gh` is `/usr/local/bin/gh`; the allowlist
`~/.config/gh-shim/merge-allow` holds one app name per line and is required.

```sh
#!/bin/sh
# ~/.local/bin/gh — must come before the real gh on PATH
REAL_GH=/usr/local/bin/gh
CFG="$HOME/.config/gh-shim"
GH_AS="$HOME/.local/bin/gh-as"
app="${GH_APP_MERGE:-}"

if [ "${1:-}" = __shim-probe ] || { [ -n "$app" ] && [ "${1:-}" = pr ] && [ "${2:-}" = merge ]; }; then
  case "$app" in -*|*[!a-z0-9-]*|"") echo "gh-shim: invalid app name '$app'" >&2; exit 2;; esac
  if [ ! -r "$CFG/merge-allow" ] || ! grep -qxF -- "$app" "$CFG/merge-allow"; then
    echo "gh-shim: '$app' is not listed in $CFG/merge-allow. No fallback." >&2; exit 2
  fi
  if [ ! -r "$HOME/.config/github-app-$app/private-key.pem" ] || [ ! -x "$GH_AS" ]; then
    echo "gh-shim: app '$app' is not set up on this machine. No fallback." >&2; exit 2
  fi
  [ "$1" = __shim-probe ] && { echo "merge_app=$app"; exit 0; }
  unset GH_APP_MERGE
  exec "$GH_AS" "$app" "$@" || exit 2
fi
exec "$REAL_GH" "$@"
```

Notes:

- Never `export GH_APP_MERGE` in a shell profile: the shim cannot tell an exported
  value from the inline prefix, so a plain `/merge` would then merge as the app
  without the probe or the `mergedBy` check.
- `/pr-flow:merge` stays model-invocable because `/land` invokes it, so the rule
  that only a typed `--as` counts is prompt text, not a hard fence. The allowlist
  and the permission prompt below are the boundaries; keep the allowlist to apps
  you would let merge on your behalf.
- A shim that hands calls straight to the real `gh` whenever `GH_TOKEN` is set
  makes the probe fail there; `/merge` then stops (fail-closed), which is intended.
- The app needs *Contents: write* and *Pull requests: write* on the repo, and branch
  protection must let it merge; `/merge` never passes `--admin`.
- Claude Code usually asks before running a command with an env prefix, even when
  `Bash(gh:*)` is allowed. That prompt is the visible confirmation of a merge under
  another identity; don't allowlist it away.
- Approving needs no plugin change: with the shim, extend the `pr merge` match to
  `pr review` and type `GH_APP_MERGE=<app> gh pr review <N> --approve` yourself.
  GitHub refuses only an approval of the app's own PR.
