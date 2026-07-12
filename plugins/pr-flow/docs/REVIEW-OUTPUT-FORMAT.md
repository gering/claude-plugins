# Shared Review Output Format

> Canonical format for presenting Claude review results. Used by `/cycle`
> (after polling completes), `/check` (for the latest review section), and
> `/fix` (for the checklist before asking which to fix).
>
> This is the same findings-table layout `/swarm:review` renders — narrow
> icon-only judgment columns, a separate short note. The one difference: a
> single-source Claude review omits swarm's `Quelle` (`Agents`+`Verifier`)
> column (see "Swarm-only columns" below).

## Language

Match the language of the user conversation. Structure below uses English
labels; translate labels (`Sev`, `Location`, `Finding`, `Verdict`, `Note`) to
German when the conversation is in German. **Do not translate content** from
the review — quote it as-is.

## Required sections

Every presentation MUST include, in this order:

1. **Header** — one line with PR number + verdict emoji
2. **Status line** — CI, reviews, staleness
3. **Findings table** — all issues as a markdown table
4. **Recommendation** — exactly one actionable next step

In re-review cycles the findings table gains a `Status` column (see below) —
there is no separate "previously raised" table.

## Header + status

```
## Review — PR #<N>

**Verdict:** ✅ clean  |  ⚠️ <N> blocker(s)  |  ❌ merge blocked
**CI:** <N passed, N failed, N running>
**Stale:** no  |  ⚠️ yes — new push since review (<commits> commits)
```

One of the three Verdict variants — pick based on severity mix:
- ✅ no blocking findings, no failed CI → "clean"
- ⚠️ blocking findings present but CI OK → "<N> blocker(s)"
- ❌ failed required CI checks or merge-state CONFLICTING → "merge blocked"

## Findings table (REQUIRED — markdown table format)

```
### Findings

| # | Sev | Location | Finding | Verdict | Note |
|---|-----|----------|---------|---------|------|
| 1 | 🔴 | `src/foo.ts:42` | "Empty catch swallows the error" | ✅ | log or rethrow |
| 2 | 🟡 | `src/bar.ts:17` | "Magic number 86400" | 🟨 | accept const, skip rename |
| 3 | ⚪ | `README.md:5` | "Typo: recieve → receive" | ✅ | trivial one-liner |
```

### Column rules

- **`#`** — stable finding number (lets user say "fix #1 and #3"). In re-review
  cycles it **stays the same** for a recurring finding; only new findings take
  the next free number. Never renumber.
- **`Sev`** — **icon only** (no text label), one of:
  - 🔴 blocking — bug, correctness issue, missing test for critical path
  - 🟡 suggestion — improvement the reviewer justified
  - ⚪ nit — style, trivial, typo
- **`Location`** — `` `file:line` `` in backticks. Multiple lines:
  `src/foo.ts:42,58`. No specific line: `src/foo.ts`. File-less: `—`
- **`Finding`** — quote the reviewer's key phrase in double quotes, keep it
  short (≤ ~120 chars); no emoji here; omit the "Fix: …" suggestion (that goes
  in `Note` if you agree with it).
- **`Verdict`** — YOUR assessment, **icon only**, the action gate:
  - ✅ agree
  - 🟨 partial agree
  - ❌ disagree
- **`Note`** — the *why*, short (≤ ~80 chars — let the renderer wrap it into a
  taller cell, never widen the row). **REQUIRED for every 🟨/❌** (what part you
  accept / why you disagree); optional for ✅ when useful (a fix hint, reason, or
  "trivial one-liner"). No line breaks inside the cell.

### Swarm-only columns

`/swarm:review` inserts a `Quelle` column (`Agents`+`Verifier` — who raised the
finding + ensemble confidence) between `Finding` and `Verdict`. A single-source
Claude review has no ensemble to attribute, so **pr-flow omits that column** —
use the six columns above.

### Status column (re-review cycles only)

On a re-review (a later `/cycle`, or after `/fix`), append a `Status` column so
the user sees at a glance what happened to each prior finding — this replaces a
separate "previously raised" table:

```
| # | Sev | Location | Finding | Verdict | Note | Status |
|---|-----|----------|---------|---------|------|--------|
| 1 | 🔴 | `src/foo.ts:42` | "Empty catch swallows the error" | ✅ | fixed last cycle | 🔧 fixed |
| 2 | 🟡 | `src/bar.ts:17` | "Magic number 86400" | 🟨 | intentional | ⏭️ skipped |
```

`Status` values: 🔧 fixed · ⏭️ skipped · 🔁 recurred · 🆕 new (raised this round).
Match a finding across cycles by **`(file, mechanism)`, not `(file, line)`** —
lines drift after edits. A matched finding keeps its `#`; only a 🆕 finding takes
the next free number. Round 0 (the first review) omits the `Status` column.

### Table formatting requirements

- Must be a **real markdown table** with `|` separators and header separator row
  (`|---|---|`). Not prose with bullet hyphens. Not a numbered list.
- All rows must have the same column count.
- Use the exact column headers above (translated to German if conversing in
  German: `# | Sev | Ort | Befund | Verdict | Notiz`, plus `Status` on
  re-reviews).
- If a review has zero findings: skip the table, write `No issues raised. LGTM.`
  under the Findings heading.

## Recommendation (REQUIRED — exactly one line)

Pick exactly one:

- **Any 🔴 blocking present** → `Run /fix to work through them, then /cycle again.`
- **Only 🟡/⚪ findings** → `Address if you want. Otherwise: run /merge when ready.`
- **Zero findings** → `Ready to merge — run /merge.`
- **CI failing or merge-blocked** → `Investigate CI failure before proceeding.`
- **Review is stale** → `Push latest changes and run /cycle for a fresh review.`

## Forbidden formatting patterns

**Do not** use any of these — they break the contract and make output inconsistent:

- ❌ Prose paragraphs listing findings (like: "Claude raised three issues: first, …")
- ❌ Nested bulleted "cards" per finding (like the example the user pushed back on):
  ```
  #: 1
  Typ: Bug
  Thema: …
  Meine Einschätzung: …
  ```
- ❌ Headings per finding (`### Finding 1`, `### Finding 2`)
- ❌ Combining findings into groups (e.g. "minor issues bundle")
- ❌ Skipping the table when findings exist — always use the table
- ❌ Emojis or text labels in `Sev`/`Verdict` beyond the single icon (no
  `🔴 blocking`, no `✅ Agree` — icon only)
- ❌ Emojis inside the `Finding` column (they belong in `Sev`/`Verdict` only)

## After the presentation

- **Do NOT immediately start fixing anything** — wait for the user to indicate which items to address
- User may reply with `#N`, `#N, #M`, `#N-M`, `all`, `blocking`, or `skip`
- If user wants to fix: hand off to `/fix` for the interactive fix flow
