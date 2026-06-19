---
title: "Model Economics: Picking the Model per Skill"
createdAt: 2026-06-18
updatedAt: 2026-06-19
createdFrom: "branch: task/dogfood-knowledge-system"
updatedFrom: "branch: task/dogfood-knowledge-system"
pluginVersion: 1.8.0
prime: true
---

# Model Economics

Each subagent-backed skill picks its model from the **task shape and invocation
frequency**, not a default. The reasoning is recorded in each skill's "Model
rationale" section; this is the cross-cutting summary.

## The assignments and why

- **`/query` → Haiku** (the knowledge agent's `model: haiku`). The task is a
  bounded lookup: read the index, pick relevant files, summarize. No reasoning
  over ambiguous requirements, no architectural judgment. `/query` is invoked
  *often* — potentially before every non-trivial change — so cost compounds;
  Haiku keeps it cheap enough to stay usable, and fast (<2s).

- **`/query` Explore fallback → session model (no override).** When knowledge
  has a gap and the skill falls back to codebase exploration, that work is
  open-ended (which files matter, when to stop) and needs session-model
  reasoning quality. It runs rarely, so its higher cost is negligible.

- **`/reindex` → Sonnet, background.** A semantic reasoning task — judging
  duplicates, cross-links, frontmatter correctness across the whole graph.
  Haiku would miss subtle overlaps; Opus is overkill for structured analysis.
  Sonnet hits the quality/cost sweet spot. It runs `run_in_background: true`
  because a thorough pass reads many files over 1–3 minutes; blocking the
  session would be hostile.

## The principle

Match model tier to the *kind* of thinking required, then weight by how often
the skill runs:

- **Bounded, mechanical, frequent** → cheapest capable model (Haiku).
- **Semantic judgment, occasional** → mid tier (Sonnet).
- **Open-ended reasoning, rare** → inherit the session model rather than pin a
  tier.

A second axis is **context hygiene**: `/query` and `/reindex` run as subagents
(not in-context) specifically to keep the main window clean — they read many
files and return a dense answer instead of dragging file contents into the
primary session. `/prime` is the deliberate inverse: it loads content *into*
the main context on purpose, so it runs in-context with no subagent.

Related: [[skill-design-conventions]], [[skill-composition]].
