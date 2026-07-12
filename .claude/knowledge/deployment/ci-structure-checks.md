---
title: "CI Structure Checks"
createdAt: 2026-06-18
updatedAt: 2026-06-19
createdFrom: "PR #6"
updatedFrom: "PR #6"
pluginVersion: 1.8.0
prime: false
---

# CI Structure Checks

This repo is declarative Markdown + JSON with **no build step**, so there is no
compiler to catch structural regressions — they stay invisible until live use.
`scripts/check-structure.py` is the **single automated guard**, run identically
in CI (`.github/workflows/structure-checks.yml`) and locally before pushing.

## What it verifies

The script groups its checks into five functions:

1. **JSON validity + version sync** — every `plugin.json` / `marketplace.json`
   parses, and each plugin's version matches between the two. Version drift is a
   hard error, so it acts as a merge gate (the two-file version rule itself is
   documented in CLAUDE.md's "Versioning" section).
2. **SKILL.md frontmatter** — required fields present, and the `description`
   word budget (thresholds live in the script's `DESC_WORDS_*` constants).
   Enforced mechanically because an over-budget description gets silently
   truncated in sessions and can stop matching its triggers (see
   [[skill-design-conventions]]).
3. **Internal `${CLAUDE_PLUGIN_ROOT}` references** — paths referenced in skills
   actually exist, catching dangling cross-references.
4. **Shell script syntax** — bundled `.sh` files parse.
5. **Plugin tests** — runs every `plugins/*/scripts/test_*.py` (bounded timeout,
   stdin closed); a plugin drops a self-contained assert-based test there and CI
   runs it. It executes discovered files, so the CI job must never expose secrets
   / a write token to untrusted PRs (GitHub withholds both from fork PRs).

## Why it exists

The architecture review named "zero verification" the system's single biggest
risk: for a system whose "code" is prose, CI is the only substitute for a
compiler. This check closes the most common failure class (broken JSON, version
drift, dangling refs, budget violations) cheaply and mechanically. Keep it green;
it runs on every PR and push to main.

Related: [[skill-design-conventions]].
