# Changelog

All notable changes to the plugins in this marketplace are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/). Each
plugin is versioned independently and follows [Semantic Versioning](https://semver.org/);
entries are grouped per plugin, newest first.

> **Maintaining this file:** every version-bump PR must add an entry to the
> relevant plugin section. `/open` (readiness checks) and `/merge` (pre-merge
> documentation check) enforce this automatically once the file exists.

## knowledge-system

### 1.9.0 — 2026-07-15
- Modernize the statusline segment: rename the visible label `[cks …]` → `[ks …]` and replace the positional `RULES|KNOW` layout with type glyphs (`§` rules, `◈` knowledge, `❖` legacy project knowledge). Display-only (renderer 1.1.0) — no identifiers, paths, or markers change.

### 1.8.2 — 2026-06-21
- Sharper knowledge curation grounding and staleness detection.

### 1.8.1 — 2026-06-19
- Harden `/init` scaffolding: absorb unmarked sections, lazy domain dirs, never overwrite user content.

### 1.8.0 — 2026-06-17
- Extract `/statusline` install logic into a tested script.

### 1.7.0 — 2026-06-12
- Consolidate rules into one per-project surface; rework the usage-rule staleness check to a template version.

### 1.6.0 — 2026-06-12
- Add `/prime` skill to load foundational docs (architecture + overviews) into context, plus the `prime` frontmatter field that marks which knowledge docs it pulls in.

### 1.5.1 — 2026-05-12
- Fix `/statusline` install bugs surfaced in live testing.

### 1.5.0 — 2026-05-08
- Add `/statusline` skill.

### 1.4.0 — 2026-04-18
- Add `/backfill-knowledge` skill and `--origin` override for `/curate`; add `createdFrom`/`updatedFrom` origin metadata to the knowledge schema.

### 1.3.0 — 2026-04-17
- Add `/reindex` skill; extend `/curate` with frontmatter maintenance; add auto-prime and feature docs.

### 1.2.0 — 2026-04-14
- Drop the `knowledge-` prefix from `/init` and `/migrate`.

### 1.1.1 — 2026-04-14
- Expand skill descriptions for auto-trigger matching.

### 1.1.0 — 2026-03-29
- Add auto-query rule (check knowledge before diving into code) and rewrite the auto-curate rule with concrete trigger moments.

## work-system

### 1.11.1 — 2026-08-12
- Fix automated `/kickoff` and `/adopt` worker launches under **herdr 0.7.5+ (incl. 0.8)**, which failed with `herdr error: unknown option: --workspace` → "did not return a pane id". `agent start` no longer places the agent; it starts one in an **already-open pane** (`agent start <name> --kind <kind> --pane <id> [-- <args>]`), with placement moved to a preceding `tab create`. The launcher now creates the task's final tab first and starts the worker in its root pane.
- The contract is **feature-detected** from `herdr agent start --help` (read-only, bounded) rather than compared against a version — 0.7.x spans both contracts, so a version check would route half of it wrong. herdr 0.7.0–0.7.4 keeps its exact previous sequence, diagnostics and stdout contract; an unrecognizable `agent start` fails before creating a tab or starting anything.
- `agent-registry.sh resolve` now declares the transport per entry: `herdr_mode=agent-start` + `herdr_kind` for claude/codex/grok (argv[0] must equal the kind, is dropped, and every remaining argument keeps its order and boundaries), `herdr_mode=pane-run` for kimi's two-phase wrapper, which no native kind can express. Unknown transport metadata fails closed before anything is created. The metadata is generic so a dynamically-registered wrapper entry needs no launcher change.
- `agent_pane_busy` — herdr's rejection while the new pane's login shell is still running its rc files — is retried within a bounded window, and no other error is ever retried. Wrapper workers get a `pane process-info`-based shell-prompt wait before their command is typed, then are confirmed by polling until herdr detects the expected kind in that exact pane.
- New fail-closed launch result `blocked=unverified` (exit 0, `blocked` first): a tab exists and a worker may be running, but the launch could not be confirmed (readiness timeout, unclassifiable herdr error, pane-id mismatch, wrong detected kind, or an unconfirmed rollback). `/kickoff` and `/adopt` branch on it **before** `moved=`, send you to inspect that tab, and must not relaunch, print the manual worker command, or persist a project default. Failures that provably started nothing roll their tab back exactly once instead.
- A failed kimi seed is now a hard stop: it prints a marker, states TASK.md was not started, and exits with the seed's own code — no keypress wait and no empty `kimi -c --auto` session that looks like a healthy worker. That marker is for the **human** who opens the tab; the launcher does not grep it. Terminal text cannot carry a supervisor's signal: the pane echoes the command it was given (which rolled a *healthy* launch back in live 0.8 testing), the seed reads repository files that legitimately name the marker, salting it with a per-launch nonce means handing that nonce to the very process being supervised, and a rendered snapshot is width-wrapped. 
- A dead wrapper is detected from **process state** instead: a pane sitting at its own shell prompt, with no worker detected, for several consecutive polls means nothing is running there — whether the seed died or its keystrokes never landed. Measured live: the busy→ready *transition* is not usable (a seed that fails on startup is gone before the first poll), the steady state is. A failed launch is reported in ~5s and its tab rolled back; a healthy one is confirmed in ~2s. What this cannot catch is a seed that fails minutes later — the seed phase *is* the task work — and that is an ordinary worker crash, not a bad launch.
- On the legacy path a wrapper worker is the tab's root process, so a seed that exits at once takes the tab with it. The launcher now confirms the pane is still alive before reporting `moved=yes`, instead of pointing the user at a tab herdr already closed. Native legacy launches keep their byte-identical fast path.
- Lifecycle docs corrected: on 0.7.5+ the worker runs **inside** a shell pane, so a clean `/exit` no longer closes its tab — `/close`'s armed marker plus the `SessionEnd` hook is the primary teardown there. No teardown behavior changed; the worker is still a registered agent, so `agent_status` polling is unaffected.

### 1.11.0 — 2026-08-03
- `/kickoff` can launch the **kimi CLI** (kimi-code) as a worker: `--kimi` → `kimi:kimi-code/k3-256k`, joining claude/codex/grok in `agent-registry.sh`. It appears in the picker and can be saved as the repo default, where it announces like the other third-party workers.
- kimi is the first worker without a `<cli> -m <model> <prompt>` launch form — it has no positional launch prompt, no initial-prompt env var, and piped stdin only prefills the input box (and would steal the TUI's tty). Its `-p` flag is the only entry point, but it cannot be combined with `--auto`/`-y` and exits after one answer. Since `-p` does run tools unattended and `kimi -c` inherits its history, the launch is two-phase: `sh -c 'kimi -m "$1" -p "$2" || <report+wait>; exec kimi -c --auto' …` — the seed works the task through once, then `exec` hands over to the interactive autonomous session. So a kimi tab has already made progress by the time you switch to it.
- The model and prompt travel as `"$1"`/`"$2"` positionals rather than spliced into the script text: `-p` consumes the next token, so a concatenated argv can silently swallow a flag (`kimi -p --auto "…"` turns `--auto` into the prompt). Tests assert the exact word list, that structure, and — by executing the resolved argv against a logging stub — what each phase actually received.
- Readiness is model-aware via `kimi provider list --json` (bounded), because an unconfigured `-m` id aborts kimi at startup; the id must be the **qualified** alias (`kimi-code/k3-256k`). Auth probes `credentials/kimi-code.json`, not the same-named `oauth/` file, which stays 0 bytes even when logged in. A listing without the `models` section counts as format drift and falls back to trusting auth, while a well-formed listing offering nothing means unavailable — grok's "empty output = inconclusive" rule doesn't transfer to JSON.
- `/continue`'s reopen caveat and the README now name `kimi -c` alongside `codex resume --last` / `grok -c`. Lifecycle is unchanged: `agent_name` comes from the registry and `agent_status` from herdr's pane hooks, so the `sh -c` wrapper (which `exec`s into kimi) doesn't affect tab teardown or state.
- `resolve` now also emits `argv_shell=` — the same words POSIX-single-quoted — and `/kickoff`/`/adopt` print that line verbatim in their outside-herdr block instead of re-deriving the quoting from a prose rule. For kimi this is safety-critical: its argv carries `;` and `exec`, so a mis-quoted render would run the `;` in the user's own interactive shell.
- A failed kimi seed is no longer silent. `&&` would kill the pane and a bare `;` let the error scroll away behind the TUI's first repaint, leaving a tab that looked like a working worker; the launch script now reports the failure and waits for a keypress before handing over to an (empty) session. Covered by a test that runs the resolved argv against a stub whose seed exits non-zero.
- kimi's model check matches the alias in **key** position (`"<model>":`) instead of anywhere in the document, so the alias appearing as a value (a `default_model`-style field) can't report an empty `models` set as available. The real listing is flat-qualified, verified live.
- `/kickoff` announces kimi's **unattended** start (no tool-approval prompts in either phase) whenever the resolved worker is kimi, and `/adopt` warns additionally — its TASK.md is summarized from another branch's commits, so it is the one path where an unattended worker acts on content the user did not write. Announce, not prompt: the autonomy is the intended shape.
- Selector surfaces swept for the new worker: `/kickoff`'s description + flag table, `/adopt`'s two selector enumerations and its manual-launch block (incl. the `sh -c` quoting caution), `/close`'s worker-degradation prose, `herdr-launch.sh`'s usage header, `plugin.json`'s description, and the `herdr-kickoff-automation` knowledge entry. `/kickoff`'s non-claude announce rule now tests "not `claude:`" instead of a per-CLI allowlist, so a future registry entry is covered without another edit.

### 1.10.0 — 2026-07-24
- `/close` step 10's commit+push prompt is now skippable per repo: a committed `.claude/work-system-close-autocommit` flag (mirrors the `.claude/work-system-agent` default precedent) routes straight to `archive-task.sh commit-push` — no `AskUserQuestion` — and reports the result exactly as the manual path does. Off by default; unset repos keep today's ask-once behavior. Per-repo only, no global default. `archive-task.sh` grew an `autocommit get|set|unset` subcommand as the single source of truth for the flag.
- The flag is honored **only once committed**: `get` reads the value from the committed object on the default branch (`git show refs/heads/<main-branch>:<rel>` — fully qualified, so a same-named tag cannot shadow the branch), never from the working tree, so a file a tool or a worktree agent merely wrote cannot waive the prompt — and neither can a working-tree edit hidden behind `git update-index --assume-unchanged`/`--skip-worktree`, which fools a diff-based guard. A locally edited flag still falls back to asking, so deliberate local disabling works. Scope stated honestly: this raises the bar from "any file write" to "a commit", not to "human-reviewed"; `commit-push`'s own guards (archive-scoped pathspec, ff-only, never force-push, refusal on unpushed history) are what bound the damage.
- The verdict is read from **`<main-branch>`**, not `HEAD`: the flag authorizes a commit onto that branch (`commit-push` refuses any other), so a flag living only on some feature branch no longer announces an auto-commit that then fails. A repo that never opted in answers a bare `enabled=no`; every other non-honored case adds a machine `reason=` and a ready-to-print `note=` that callers relay verbatim instead of duplicating the vocabulary in prose. Accepted content is `yes`/`true`/`1`/`on` (case-insensitive, trimmed, single line); `no`/`false`/`0`/`off` disables, and a deliberate local disable is reported neutrally rather than being told to revert itself. `/close` treats **anything that is not literally `enabled=yes`** — including empty output from an older installed plugin without the subcommand — as "ask", so the check can never fail open, and it prints the same scoped `git status` preview on the auto path: standing authorization is not authorization to act unseen.
- `commit-push` now passes its paths as `:(literal)` pathspecs, so a task name containing glob characters (`x*`) can no longer sweep other archived files into the commit — covered by a regression test that fails when the prefix is removed. Both writers in the script use `mktemp` rather than a PID-derived temp name.
- Path handling: resolution delegates to `main-repo-path.sh` (the same resolver `/close` uses, so `set` can't write where `get` won't look); `set`/`unset` refuse a symlinked flag **or `.claude` parent** and verify `.claude` resolves inside the repo; `set` writes via an exclusively-created `mktemp` file in the target directory instead of a PID-predictable `.tmp.$$` path, and echoes the resolved `flag=`.

### 1.9.4 — 2026-07-24
- Add two more herdr tab-glyph refresh triggers: `/kickoff` (after the launch step, so the freshly-created tab is included) and `/define` (after the task file is written). Both call the same best-effort, silent `herdr-tab-glyph.sh refresh --cached <main-repo>` that `/close`/`/list`/`/status` already use — outside herdr, or on failure, it's a silent no-op. Fixes the Manager tab's `◉` hub mark sometimes not appearing until an unrelated `/close`/`/list`/`/status` happened to run in a repo where you mostly `/kickoff`.

### 1.9.3 — 2026-07-24
- `/adopt` now auto-launches inside herdr, exactly like `/kickoff`: after it builds the worktree from an existing branch it opens the task's tab and starts the chosen worker there (outside herdr it prints the manual launch block unchanged). Reuses the shared `herdr-launch.sh launch` helper — no launch logic duplicated; the intricate result-branching and picker/announce rules stay a single source in `/kickoff`'s steps 12/13, which `/adopt` references. `/adopt` grew an optional `[agent-selector]` argument (`--fable`/`--opus`/`--sol`/`--grok`/`--codex`/`--agent <cli[:model]>`/`--pick`, same set as `/kickoff`); the branch is parsed position-independently from the selector. The tab label derives from the *resolved* task name, so it stays sensible when `/adopt` keeps the original branch name rather than renaming to `task/<name>`. READMEs and the `herdr-kickoff-automation` knowledge entry updated to cover both callers.

### 1.9.2 — 2026-07-21
- Fix: `/kickoff`-launched Claude workers no longer run the wrong `/continue`. The machine-generated launch prompt is now the plugin-qualified `/work-system:continue`, not the bare `/continue` — a Claude Code built-in/alias `/continue` can shadow the plugin skill (and plugin skills are only *guaranteed* reachable via their `plugin:skill` namespace), so a freshly-launched worker was running CC's own resume (nothing to resume in a new worktree → idle, TASK.md never loaded) instead of the work-system resume flow. Changed at all machine-invocation sites: `agent-registry.sh` `emit_argv` (the selector path), `herdr-launch.sh`'s legacy no-selector fallback, kickoff's outside-herdr manual block, and the README launch examples; `test_agent_registry.py` asserts the qualified form. Human-typed reopen instructions (`/work-system:continue <task>`) updated to match. **Deployment note:** `agent-registry.sh` / `herdr-launch.sh` run from the *plugin cache*, not the repo, so live launches only pick up this fix after a `/plugin` marketplace update + reload refreshes the cache.

### 1.9.1 — 2026-07-20
- `herdr-launch.sh` no longer swallows herdr's own error on a launch/resume failure (`2>/dev/null`). The `agent start`, `pane move`, `tab create`, and `pane run` calls (plus the `tab close` orphan-cleanup that can run after a malformed `tab create`) now capture stderr, strip control/escape bytes both before AND after JSON-decoding a message (a ``-style escape only becomes a real control byte once decoded), parse herdr's `{"error":{"code","message"}}` JSON defensively (raw text as fallback, never a traceback), and print it before the existing generic diagnostic — `pane run`'s failure line now follows the same before-the-generic-message order as the other call sites. When the error names an invalid workspace placement (`agent_placement_not_found`, or the message names the sanitized workspace id as a bounded token within the same clause as "not found"/"placement", case-insensitively), a one-line actionable hint is appended pointing at a stale `HERDR_WORKSPACE_ID` — scoped to the two calls that actually send `--workspace` (`agent start`, `tab create`), since `pane move`/`pane run` can't have a workspace-id problem. `kickoff`/`continue` SKILL.md now explicitly instruct relaying the helper's stderr diagnostic to the user on every failure path, so the richer diagnostic actually reaches the user instead of staying in the tool output — worded per call site, since the stale-workspace hint can only ever appear on the `agent start`/`tab create` paths. Diagnostics only — stdout key=value lines, exit codes, and `moved`/`reused`/`resumed`/`focused` semantics are unchanged. Covered by new `test_herdr_launch.py`.

### 1.9.0 — 2026-07-17
- `/kickoff` no longer hardcodes Claude as the worktree worker. With no flag it launches the repo's **default** agent — a single committed per-project setting (`.claude/work-system-agent`); if none is set, `/kickoff` shows a picker and offers to save your choice as that default. No global default and no shipped fallback. Override per run with a flag: `--fable`/`--opus`, `--codex`/`--sol`, `--grok`, `--agent <cli[:model]>`, or `--pick` (force the picker). New `scripts/agent-registry.sh` is the single source of truth (registry-driven aliases, the project `default get`/`set`, and an availability probe — CLI install + auth, plus model-level for grok via `grok models` so a model the CLI no longer offers reads as unavailable instead of failing at launch); `herdr-launch.sh` execs the resolved worker argv instead of a hardcoded `claude`. Non-Claude workers degrade honestly: they get a bootstrap prompt (read `TASK.md`, drive to a PR) instead of `/continue`, `/close` teardown stays CLI-agnostic, and `/continue`'s reopen documents the per-CLI resume command. Covered by `test_agent_registry.py`.

### 1.8.1 — 2026-07-17
- Mark the main-repo session in the herdr sidebar: a tab sitting exactly at the main repo root is now prefixed with `◉` — the Manager hub among the `○ ● ◇ ◆ ✓` task satellites. Stateless and non-exclusive (it marks the location, so every tab at the root gets it), stamped by the existing `refresh` sweep — no new trigger. The chosen tab label is preserved (prefix only) and `◉` joins the idempotency strip, so hub↔task moves swap glyphs cleanly.
- Fix: state-glyph refreshes never reached the sidebar. herdr keeps two names per tab — the tab label (what the sidebar renders) and the agent registry's own name — and 1.8.0's `refresh` rewrote the latter, so a tab kept its launch-time glyph forever (a task sat at `●` while its PR was in review). `refresh` now rewrites the tab label, joining `herdr agent list` (carries `cwd`) with `herdr tab list` (carries `label`).
- The glyph now lives in the tab label only: `/kickoff` passes the plain label to `herdr agent start` and `claude -n`, so the agent and session names stay stable identities instead of freezing a launch-time glyph. Existing tabs correct themselves on the next refresh; agent names stamped by 1.8.0 are left as-is (rename them yourself if the leftover glyph bothers you).

### 1.8.0 — 2026-07-16
- Mirror task states onto herdr tab names as a leading state glyph (`○ ● ◇ ◆ ✓`: not-started / active / in-review / approved / merged), matching the `[ws …]` statusline. The mapping + precedence stay in `ws-statusline.sh` (new `states` mode, single source of truth); the new `herdr-tab-glyph.sh` stamps the glyph at launch (`/kickoff`, `/continue`) and re-stamps it idempotently on `/status`, `/list`, and `/close` (and via pr-flow's PR-lifecycle skills). `◆` approved is derived from the PR's `reviewDecision`. Survey surfaces (`/status`, `/list`, `/check`, `/close`) read the PR cache without blocking; state-changing skills (`/open`, `/merge`, `/cycle`) do a bounded synchronous refresh.

### 1.7.0 — 2026-07-15
- Add `/statusline` skill: a `[ws ○… ●… ◇… ✓…]` task-backlog segment for Claude Code's status line. Counts `tasks/*.md` by state (not-started / active / in-review / merged) with muted single-width glyphs; PR state comes from a short-TTL `.git/` cache refreshed by a detached background `gh` call, so rendering never blocks on the network. Own marker segment coexists with the knowledge-system `[cks …]` block.

### 1.6.0 — 2026-07-02
- Add `/continue` reopen mode to recover `/exit`-closed herdr tabs.

### 1.5.1 — 2026-06-30
- Harden herdr `/close` teardown against silent orphan tabs.

### 1.5.0 — 2026-06-29
- Archive the task file on `/close` instead of deleting it.

### 1.4.1 — 2026-06-24
- Automate `/close` herdr tab teardown.

### 1.4.0 — 2026-06-24
- Automate `/kickoff` worktree launch inside herdr (via agent start, extracted into a tested helper).

### 1.3.1 — 2026-06-23
- Route `/kickoff` and `/adopt` through the shared main-repo-path helper.

### 1.3.0 — 2026-06-17
- Refresh work-system: safe dependency install, markdown tables, sharper `/status`.

### 1.2.5 — 2026-06-15
- Make `/define` worktree-aware — write task files to the main repo.

### 1.2.4 — 2026-05-11
- Prevent CWD contamination in worktree skills.

### 1.2.3 — 2026-04-18
- `/list` contextual next-step hint.

### 1.2.2 — 2026-04-18
- `/close` syncs main after a task merges.

### 1.2.1 — 2026-04-15
- Simplify the `/kickoff` one-liner.

### 1.2.0 — 2026-04-15
- Drop the `work-` prefix from all skills.

### 1.1.7 — 2026-04-14
- Expand skill descriptions for auto-trigger matching.

### 1.1.6 — 2026-04-01
- Use the short task name for session naming in `/work-start` and `/work-adopt`.

### 1.1.5 — 2026-03-26
- Fix `gh pr list` to use `--head` instead of `--search` for branch matching.

### 1.1.4 — 2026-03-24
- Fix branch-deletion false positive with the rebase-merge strategy.

### 1.1.3 — 2026-03-23
- Fix `/close` handling of gitignored task files and `TASK.md`.

### 1.1.2 — 2026-03-20
- Add session naming to the `/work-start`/`/work-adopt` one-liners; `/work-continue` auto-installs deps and suggests a session rename.

### 1.1.1 — 2026-03-18
- Add session rename to `/work-continue`.

### 1.1.0 — 2026-03-18
- Add `/work-adopt` skill; store worktrees under `.claude/worktrees/`.

## pr-flow

### 1.3.0 — 2026-07-16
- `/open`, `/merge`, `/cycle`, and `/check` refresh the work-system herdr tab glyphs after PR state changes (soft-coupled via `scripts/refresh-task-glyphs.sh` — silent no-op when work-system or herdr is absent). `/check` uses `--cached` (read-only survey, no blocking `gh` call); the state-changing skills refresh synchronously.

### 1.2.3 — 2026-07-13
- Align the `/cycle` review table with the swarm findings-table layout.

### 1.2.2 — 2026-06-12
- Make `/rebase` risk-based: auto-proceed when changed files don't overlap, show menus otherwise.

### 1.2.1 — 2026-06-12
- Extract a shared readiness-checks spec.

### 1.2.0 — 2026-06-12
- Add `--loop` mode to `/cycle` (auto-fix agreed findings and re-cycle until clean).

### 1.1.9 — 2026-05-18
- Add the enforce-merge-skill rule.

### 1.1.8 — 2026-05-12
- Ship compact skill descriptions to fit the listing budget.

### 1.1.7 — 2026-04-18
- Skip the rebase prompt when invoked by `/merge` or `/cycle`.

### 1.1.6 — 2026-04-18
- Single confirmation for rebase + force-push.

### 1.1.5 — 2026-04-18
- Review-audit follow-ups.

### 1.1.4 — 2026-04-15
- Remove redundant prompts from `/merge` when all checks are green.

### 1.1.3 — 2026-04-15
- `/merge` adds a pre-merge documentation-readiness check.

### 1.1.2 — 2026-04-15
- `/rebase` polls for a review after force-push.

### 1.1.1 — 2026-04-15
- Rename `/create` to `/open` and drop the `pr-` prefix from all skills.

### 1.0.5 — 2026-04-14
- Shared review output format spec.

### 1.0.4 — 2026-04-14
- Expand skill descriptions for auto-trigger matching.

### 1.0.3 — 2026-04-14
- `/pr-create` polls for first-review completion.

### 1.0.2 — 2026-04-14
- Extract polling into a shared script.

### 1.0.1 — 2026-04-14
- Auto-execute readiness checks in `/pr-create`; remove menus.

### 1.0.0 — 2026-04-14
- Initial pr-flow plugin with the PR review workflow skills.

## swarm

### 0.7.0 — 2026-07-27
- **Per-cluster external voices (default):** `codex` and `grok` no longer run one broad multi-lens review each — they fan out over the **same gated lens clusters** as the Claude finders (one call per cluster; per lens under `--max`). The gate now prunes calls for *everyone*: a fully-gated-out cluster spawns nothing for any voice. Cost is `live-backends × units` external calls (≤2×4 default, ≤2×11 under `--max`) and is logged at fan-out — never silently capped.
- **Authoritative lens tags:** each external voice *is* its cluster, so a finding's `[lens]` prefix no longer depends on a broad prompt self-tagging correctly. Untagged findings from a single-lens external unit now resolve to that lens (same rule the Claude finders already used) instead of falling back to `unspecified`.
- **`LENS_BRIEF` becomes single-source:** new adapter flag `agents.sh run --lens-instr <s>` prepends the workflow-supplied cluster briefs to the fenced-diff prompt in **deterministic shell** — the same "assembly is never an LLM step" contract the diff fencing follows, and backend-agnostic, so a future voice inherits per-cluster prompts for free. The SKILL.md external-prompt HDR drops its hand-mirrored lens list; `test_lens_sync.py` flips that mirror check to a negative one and pins the `--lens-instr` wiring.
- **Mandatory-lens floor.** Because the gate now prunes for every voice, a lens it wrongly drops is reviewed by nobody — the old full-width external calls used to absorb that, and the gate's only other protection is a sentence in its own prompt (injection-reachable via the diff it classifies). `MANDATORY_LENSES` (`security`, `adversarial`, `correctness`) can never be pruned; accepted cost is that the `breakage` and `threat` clusters always spawn, leaving the gate only `design`/`consistency` to prune. The report's coverage line now partitions the full lens set: floored-in lenses are written back to `gate.run`, and lenses the gate listed in neither field are materialized as gated-out (a live run silently swallowed `adversarial` this way).
- **Transport integrity is content-bound.** `--lens-instr-sum` (FNV-1a/32 of the exact instruction, computed in pure JS by the workflow and recomputed in python3 by the adapter) replaces the first attempt at a byte-length check — `security`/`altitude` and `ONLY`/`ALSO` are same-length swaps that change the review scope while a byte count still matches. It is **required** whenever `--lens-instr` is given, so a transport cannot void the guard by dropping one flag. The prep block now also decides the oversize skip deterministically (`EXTERNALS_OVERSIZE`) instead of leaving the arithmetic to the model.
- **Gate-floor and coverage-line integrity.** `MANDATORY_LENSES` is asserted to be a subset of the lens set at startup and in CI (an explicit list is a mirror: a renamed lens would silently void the floor). The gate coverage line drops hallucinated lens names and asserts run/skip stay disjoint, so every lens appears in exactly one column.
- The `--max` profile now lifts **every** voice to per-lens granularity (previously Claude only). A `claude: false` control run keeps full-width external coverage (no gate exists to prune it), just split per cluster.
- Grok no longer needs a diff-only brief variant: since 0.6.0 both externals read project files, so both get the full cluster briefs.
- The skill's oversize threshold drops to 118784 bytes (4 KiB under the adapter's 120 KiB cap) — `exec` now sees lens instruction + diff, so a prompt that only just fit would otherwise fail per call as a backend error.

### 0.6.0 — 2026-07-20
- **Posture change: external voices get file-read + always-on web research** (hardened egress). codex runs `-s read-only -C <repo> -c tools.web_search=true`; grok runs a strict `--tools` allowlist (`read_file,list_dir,grep,web_search,web_fetch`) + `--cwd <repo>` — no write/shell tools, no `--disable-web-search`. Enables out-of-diff bug finding and external knowledge (API docs, CVEs) without re-opening the secret-exfil hole.
- **Keep + extend the OS secret-jail**: repo-**root** `.env*`, `data/`, `*.pem`, SSH id keys (`id_rsa*`/`id_ed25519*`/`id_ecdsa*`/`id_dsa*`), `*.key`, `.npmrc`, `.pypirc`, `credentials.json` join the HOME denylist — which itself gains `~/.gitconfig`, `~/.config/git`, and `~/.cargo/credentials.toml` (git keeps working via `GIT_CONFIG_GLOBAL/SYSTEM=/dev/null`, so the denied global config is never opened by git yet stays unreadable to a direct read). Root-level only — nested secrets via `SWARM_DENY_PATHS`, which also takes per-repo extras. New `test_sandbox_deny.py` regression-checks the denylist, the fail-closed degrade argv, and (e2e) blocks a temp `.env` when `sandbox-exec` works.
- **Egress guard** in the external prompt header (outside the untrusted-diff fence): web is for external general knowledge only — never put repository content into a search query or fetched URL. Prompt-policy (model-cooperation-dependent), not transport-enforced; the secret-jail is the hard boundary. Residual risk documented in knowledge + SKILL posture block. `scrub_secrets` / output gate stay as output-only backstops.
- **Fail closed without a working OS jail** (per voice, honestly described): `_jail_available` probe-runs the wrapper (a present-but-broken `sandbox-exec`/`bwrap` counts as no jail); grok degrades to tool-less/no-web (0.5.x flags), codex gets web **hard-disabled** (`tools.web_search=false`) while its FS reads remain inside its own `-s read-only` sandbox — codex has no no-read tier, so that is its 0.5.x read surface. The degraded posture is announced via the new `agents.sh jail` subcommand (run-start notice) and the external prompt drops its read/web capability lines on jail-less hosts.
- **Worktree-aware secret denies**: in a linked worktree the deny globs also cover the **main checkout's** root (resolved with a bare `git -C "$repo" rev-parse --git-common-dir` + bash `pwd -P` — version-proof, no git ≥ 2.31 `--path-format` floor that would fail-open on old-git bwrap hosts) — untracked `.env`/`data/` live there, not in the worktree. The repo's own `.git/config` is deliberately **left readable** (git treats an EPERM on it as fatal, which would break the externals' git-based exploration); a repo-config-embedded token is an accepted, documented residual.
- Run-start notice once per review when external voices are live. Docs + knowledge rewritten off the old tool-less/inline-only claims.

### 0.5.1 — 2026-07-18
- Make `--fix` re-confirm design-aware: a `kind: "design"` finding has no line-local defect to re-find, so an agreed (✅) design fix was silently reported skipped-stale and never applied. Step 1 now branches on kind — for design findings it re-confirms the suggestion still applies (reuse target / duplication / simpler form / waste still present), only skipping when that target is genuinely gone.
- Make `--loop` converge on design churn: the tally made no defect/design distinction, so once only design suggestions remained (each applied simplification spawning a fresh one) it ran to the cap. `loop-closeout.py step` gains `--defects D` and a new `design-only` reason (fixed order: after no-change, before cap); `--pending` is now defect-scoped (design never holds the loop open). Omitting `--defects` disables the reason (legacy callers unaffected).
- Guard `pr-post.py` against a design-row `[lens]` double-prefix (`[reuse] [reuse] …`): if the finding cell already opens with a known design-lens self-tag (spaced or not, matching the workflow tag parser), keep it and don't add a second (`DESIGN_LENS_TAGS`, sync-checked by `test_lens_sync.py`; not a kind fallback). Untagged findings deliberately stay `kind: "defect"` (the safe bucket): a design finder may report a real off-lens bug, and inferring `design` from cluster homogeneity would route that bug to applicability verify (wrong rubric) and out of the `--loop` defect tally — dropping a bug is worse than mis-filing a suggestion.
- Show the lens in the in-session Design table too: design rows now carry a `[lens]` `Befund` prefix, matching the PR-comment path (both surfaces read identically).
- Accept the cluster-default's loss of per-lens failure isolation as a documented tradeoff (one crashed cluster finder drops that cluster's Claude coverage as a visible `backendError`; `--max` restores per-lens isolation).

### 0.5.0 — 2026-07-17
- Grow the review lens set from 5 to 11 (all default-on): methodological `removed-behavior` + `cross-file-trace` (factual, normal verify) and design-quality `reuse` / `simplification` / `efficiency` / `altitude` (suggestion-shaped, `kind: "design"`).
- Organize the lenses into 4 clusters (`LENS_CLUSTERS` — single source of truth): breakage / threat / design / consistency. Claude fan-out runs one finder per cluster by default (≤4 agents); `--max` splits to one finder per lens (≤11). The gate still prunes per-lens.
- Verify design findings with a kind-aware applicability prompt (reuse target real? simpler form behavior-identical?) through the same 3-state verifier — consensus design clusters included (agreement isn't applicability); report them in their own `Design` table so they never dilute the defect ranking (`balance.design` counts them).
- Extend the external backend prompt with the six new angles so cross-family consensus can form on design findings too.
- Harden the lens plumbing after the first dogfooding run (swarm reviewed its own diff): keep validly tagged off-cluster lens prefixes (validate against the global set, not the finder's subset), derive `CANDIDATE_LENSES` from `LENS_CLUSTERS` (one list), untagged external findings no longer re-kind a merged cluster, and `pr-post.py` owns design-row ordering + `[lens]` prefixing via new optional `kind`/`lens` row fields (unit-tested).
- Harden a second time after the `--max` dogfooding round (per-lens split, 13 voices): never auto-accept an all-untagged consensus cluster (no lens backs it — verified like a solo, via a single `needsVerify` partition); the design verifier now sees the finding's `recommendation` (the proposal it tests) and carries an escape hatch for defects mis-filed under a design lens; workflow-assigned stable `num` per finding; merge-agent lens validated against the lens set; improvement invitation scoped to design finder units; `LENS_BRIEF` startup assertion + `test_lens_sync.py` guards the lens mirrors; `REFUTED` is its own balance segment (a refuted consensus design cluster is not a solo); doc sweep of the stale "verifies solos" wording.
- Harden a third time after an external-only `--max` dogfooding round (codex + grok-4.5 + composer, no Claude lenses): the merge-supplied cluster lens is accepted only when a member actually tagged it (a globally-valid lens no member carried no longer corrupts the `[lens]` prefix / `survivingPerLens`), and `unspecified` never wins the majority tally; the design verifier's mis-filed-bug exception now reclassifies the finding to `kind: "defect"` (`reclassifyToDefect`) so a bug wearing a design lens leaves the Design table; a methodological-lens (`removed-behavior` / `cross-file-trace`) cross-family consensus with no repo-reading Claude voice is verified rather than auto-accepted (diff-only externals can't confirm a repo fact); `test_lens_sync.py` pins `METHODOLOGICAL_LENSES` to the cluster set.
- Harden a fourth time after the external-only round's fixes were re-reviewed by a full-ensemble `--max` pass (the verifier read the real repo and confirmed two regressions in the prior commit): the methodological-consensus guard now checks that a Claude voice actually *tagged* the methodological lens (member `(backend, lens)`), not mere family presence — plurality lens resolution could otherwise label a cluster `cross-file-trace` off two externals while the lone Claude member tagged `[correctness]` and never checked the claim; `needsVerify` fires on any design-tagged member so a mixed design+defect cluster still applicability-checks its proposal, and the design fence carries the proposal for all-untagged clusters too; a `reclassifyToDefect` finding now also strips its design lens (else pr-post re-buckets it to Design when the step-5 handoff drops `kind`) and drops to solo (an applicability pass alone must not mint a consensus defect); README pipeline diagram + consensus blurb corrected to the 0.5.0 verify gate (the earlier sweep missed them).
- Harden a fifth time after a full-ensemble `--max` confirmation pass (which REFUTED all re-flags of the prior round's fixes but caught fresh ones): `pr-post.py` buckets rows by `kind` ALONE — a dropped kind falls to defect (the safe bucket), the lens is no longer a design fallback that could hide a reclassified/mixed-cluster bug in the Design table (`DESIGN_LENSES` retired); the design applicability verifier redacts out-of-repo path tokens from the proposal before fencing, so a hostile `--pr` proposal naming `~/.aws/…` can't lure the verifier into an out-of-repo read; the dead `c.kind === 'design'` verify-rubric disjunct is dropped; `test_lens_sync.py` upgraded from a subset to a completeness check (`METHODOLOGICAL_LENSES == breakage − topical`); the SKILL `num`-verbatim rule is scoped to round 0 (cross-round `#` is presenter-owned) and two stale doc comments corrected.

### 0.4.3 — 2026-07-17
- Remove the `grok-composer-2.5-fast` backend: grok CLI 0.2.101 dropped the model, so the composer voice (adapter path, defensive parser, workflow voice, docs) failed at runtime. `grok-4.5` is now the only grok model, and the ensemble is three voices (Claude lenses + codex + grok-4.5).
- Make grok readiness model-aware: `ready`/`list` now require `grok-4.5` to appear in `grok models`, not just auth — so a dropped or renamed model reads as "not ready" with an actionable hint instead of failing mid-review. The probe runs unjailed like the sibling `codex login status` check (a readiness check passes no untrusted diff, so it needs no sandbox and stays free of the review path's python3 profile-build), bounded by `SWARM_PROBE_TIMEOUT` (10s, `timeout -k` so a SIGTERM-ignoring CLI can't hang it; separate from the review-length `SWARM_TIMEOUT`). It falls back to the auth check — with a warning on stderr, never silently — whenever it can't produce a clean answer (no coreutils `timeout`, non-zero exit, timeout, or an empty/unparseable list), rather than dropping grok from the fan-out.

### 0.4.2 — 2026-07-15
- Fix grok CLI 0.2.101 compat: pin `grok-4.5` (upstream renamed `grok-build`), cap grok effort at `high` (the `max` tier is gone; the adapter maps `xhigh`/`max` → `high` so stale callers degrade instead of erroring).

### 0.4.1 — 2026-07-15
- Extract the `/swarm:review --pr` publish path into a deterministic, unit-tested `scripts/pr-post.py` (per-cell sanitizer, stale-head gate, `gh` post); shrink `SKILL.md` step 5 to orchestration + the human confirm gate.

### 0.4.0 — 2026-07-13
- Add `/swarm:review --pr`: review a PR diff and post the gated result via `gh`.

### 0.3.1 — 2026-07-13
- Fence finding text structurally in the merge/verify prompts.

### 0.3.0 — 2026-07-12
- Add `/swarm:review --fix` / `--loop` / `--max` actions (P5).

### 0.2.1 — 2026-07-12
- Pin the codex backend to `gpt-5.6-terra`.

### 0.2.0 — 2026-07-08
- Add the `/swarm:review` mixture-of-agents pipeline: scope → fan-out → merge → verify (P2).

### 0.1.0 — 2026-07-03
- Initial swarm plugin: local mixture-of-agents review adapter (P1).

## settings

### 0.1.0 — 2026-07-16
- Initial settings plugin (phase 1: config surface). Per-plugin TOML config resolved over schema defaults, with `list` / `show [--overrides]` / `get` / `set` / `unset` / `validate` / `defaults` via `scripts/settings.py` (Python 3.11+ stdlib) and the `/settings` skill. Each plugin owns a `schema/settings.schema.json` (types, enums, defaults, config filename); work-system, knowledge-system, and pr-flow ship schemas whose defaults match current behavior. Includes a `[related_projects]` sibling-project address book (path-existence warnings, not errors). Consumer wiring lands in a follow-up.
