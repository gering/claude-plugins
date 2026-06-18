---
title: "CI Structure Checks"
createdAt: 2026-06-18
updatedAt: 2026-06-18
createdFrom: "PR #6"
updatedFrom: "PR #6"
pluginVersion: 1.7.0
prime: false
---

# CI Structure Checks

This repo is declarative Markdown + JSON with **no build step**, so there is no
compiler to catch structural regressions — they stay invisible until live use.
`scripts/check-structure.py` is the **single automated guard**, run identically
in CI (`.github/workflows/structure-checks.yml`) and locally before pushing.

## What it verifies

The script groups its checks into four functions:

1. **JSON validity + version sync** — every `plugin.json` / `marketplace.json`
   parses, and each plugin's version matches between the two. Version drift is a
   hard error, so it acts as a merge gate (see [[version-sync]]).
2. **SKILL.md frontmatter** — required fields present, and the `description`
   word budget: **>40 words is an error, >30 a warning**. Enforced mechanically
   because an over-budget description gets silently truncated in sessions and
   can stop matching its triggers (see [[skill-design-conventions]]).
3. **Internal `${CLAUDE_PLUGIN_ROOT}` references** — paths referenced in skills
   actually exist, catching dangling cross-references.
4. **Shell script syntax** — bundled `.sh` files parse.

## Why it exists

The architecture review named "zero verification" the system's single biggest
risk: for a system whose "code" is prose, CI is the only substitute for a
compiler. This check closes the most common failure class (broken JSON, version
drift, dangling refs, budget violations) cheaply and mechanically. Keep it green;
it runs on every PR and push to main.

Related: [[version-sync]], [[skill-design-conventions]].
