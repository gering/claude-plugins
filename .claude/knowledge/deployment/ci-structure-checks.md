---
title: "CI Structure Checks"
createdAt: 2026-06-18
updatedAt: 2026-10-27
createdFrom: "PR #6"
updatedFrom: "stabilize-local-check-structure"
pluginVersion: 1.9.0
prime: false
reindexedAt: 2026-07-12
---

# CI Structure Checks

This repo is declarative Markdown + JSON with **no build step**, so there is no
compiler to catch structural regressions — they stay invisible until live use.
`scripts/check-structure.py` is the **single automated guard**, run identically
in CI (`.github/workflows/structure-checks.yml`) and locally before pushing.

**Prerequisites: python3 AND `node` (since swarm 0.11.1).** The script discovers
and runs the per-plugin `test_*.py` files, and one of them
(`plugins/swarm/scripts/test_voice_accounting.py`) executes swarm's workflow
JavaScript — the repo's only JS, and therefore the one file the Python suite
cannot otherwise reach. It **fails hard rather than skipping** when node is
absent, because a test guarding against silently lost review voices that silently
skips itself reproduces the bug it guards. CI installs node via
`actions/setup-node`; a contributor without it sees a FATAL, which is why the
prerequisite is stated in README.md and the script's own docstring as well.
A local-only contract ("python3 + bash") that CI quietly compensates for is how
a green pipeline and a red working copy drift apart.

## What it verifies

The script groups its checks into six functions:

1. **JSON validity + version sync** — every `plugin.json` / `marketplace.json`
   parses, and each plugin's version matches between the two. Version drift is a
   hard error, so it acts as a merge gate (the two-file version rule itself is
   documented in CLAUDE.md's "Versioning" section).
2. **SKILL.md frontmatter** — required fields present, and the `description`
   word budget (thresholds live in the script's `DESC_WORDS_*` constants).
   Enforced mechanically because an over-budget description gets silently
   truncated in sessions and can stop matching its triggers (see
   [skill-design-conventions](../architecture/skill-design-conventions.md)).
3. **SKILL.md positional parameters** — no `$<digit>` anywhere in a SKILL.md.
   Claude Code substitutes `$N` with the skill's arguments at render time, code
   blocks included, so an inline shell helper's `$1` silently became a PR number.
   The on-disk file looks right; only this check sees it.
4. **Internal `${CLAUDE_PLUGIN_ROOT}` references** — paths referenced in
   Markdown (skills, READMEs) *and* in the hook manifests (`hooks/*.json`)
   actually exist, catching dangling cross-references.
5. **Shell script syntax** — bundled `.sh` files parse.
6. **Plugin tests** — runs every `plugins/*/scripts/test_*.py` (per-test
   timeout, stdin closed); a plugin drops a self-contained assert-based test there and CI
   runs it. It executes discovered files, so the CI job must never expose secrets
   / a write token to untrusted PRs (GitHub withholds both from fork PRs).

## Why local runs went red while CI stayed green

Measured 2026-10-27 (task `stabilize-local-check-structure`), not guessed:

- **macOS assesses every new executable on its first exec** (syspolicyd,
  ~150 ms; a symlink to an already-run file costs ~4 ms). It is cached per
  file, and parallel runs pile up on it. Tests that wrote a fresh stub per
  case or per `Env` spent most of their wall time waiting: `test_kimi_acp`
  was 47 s alone (12 s CPU) and 419–590 s with three to twelve runs in
  parallel; `test_agent_registry` was 32 s alone and 444 s in parallel. Linux CI
  has no such check. **Rule:** write each distinct stub body once and link it
  into each sandbox, and never write through the link. If the case only needs
  an interpreter, run `python3 file.py` instead of exec'ing a shebang file.
- **Deadlines shorter than an interpreter start.** `test_codex_models` gave
  the helper 0.4–2 s, and a working fake peer had to start inside that.
  Under load it failed 12 of 12 runs. **Rule:** keep the deadline *under test*
  short, and give everything that must succeed a generous bound. Prove that a
  failure was a detection and not a timeout with `assertNotIn("timed out")`.
- **Shared `$TMPDIR` reads.** A "no temp files left behind" check that
  globs the real `$TMPDIR` sees a parallel run's live files. Give the call
  under test its own `TMPDIR`.
- **Fixed polling sleeps** in product code (`sessions.py`'s 1 s per pane)
  get an env seam (`SESSIONS_POLL_MS`) the test sets low.
- **The per-test timeout is a hang guard.** Fix the idle wait first. Only a
  test that measures as simply long gets its own `TEST_TIMEOUTS` entry, and
  the 120 s default stays tight. A stale entry is an error. After these
  fixes a full run under 3× parallel load went from 1360 s to 391 s.
- A `git archive` export is no stand-in for a checkout: `test_sandbox_deny`
  needs a git repo.

## Why it exists

The architecture review named "zero verification" the system's single biggest
risk: for a system whose "code" is prose, CI is the only substitute for a
compiler. This check closes the most common failure class (broken JSON, version
drift, dangling refs, budget violations) cheaply and mechanically. Keep it green;
it runs on every PR and push to main.

Related: [skill-design-conventions](../architecture/skill-design-conventions.md).
