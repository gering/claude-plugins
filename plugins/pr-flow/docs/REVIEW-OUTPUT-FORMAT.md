# Shared Review Output Format

> Canonical format for presenting Claude review results. Used by `/cycle`
> (after polling completes), `/check` (for the latest review section), `/fix`
> (for the checklist before asking which to fix), and `/open` + `/rebase` (for
> the auto-triggered review after opening / rebasing a PR).
>
> This is the same findings-table *family* as `/swarm:review` — same columns,
> icon-only judgment cells, a separate short note — but not byte-identical: a
> single-source Claude review omits swarm's `Quelle` (`Agents`+`Verifier`)
> column (see "Swarm-only columns" below), and swarm compresses harder for that
> extra column (a one-char `V` header, tighter cell budgets). pr-flow, one
> column lighter, uses the more readable `Verdict` header and wider budgets.

## Language

Match the language of the user conversation. Structure below uses English
labels; when the conversation is in German, translate `Location`, `Finding`,
and `Note` to `Ort`, `Befund`, `Notiz`. Keep `Sev` and `Verdict` as fixed
tokens in every language — the authoritative German header is
`# | Sev | Ort | Befund | Verdict | Notiz` (plus `Status` on re-reviews), the
same one every skill references. **Do not translate content** from the review —
quote it as-is.

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

- **`#`** — finding number (lets user say "fix #1 and #3"). **Stable within a
  single `/cycle` loop**, which holds its findings in-session: across its
  re-review rounds a recurring finding keeps its number and only new findings
  take the next free number — never renumber mid-loop. A standalone `/fix` or a
  fresh `/check` re-parses the raw review with no memory of earlier numbers, so
  it numbers from #1; that's expected, not a violation.
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

### Status column (`/cycle --loop` re-reviews only)

The `Status` column needs memory of the prior round's findings — their `#`,
verdict, and disposition. The **only** producer of that memory is a `/cycle
--loop` run: it holds the previous round's findings in-session and carries them
forward (see the loop's "Fix agreed" step). On its re-review rounds, append a
`Status` column so the user sees at a glance what happened to each prior finding
— this replaces a separate "previously raised" table:

```
| # | Sev | Location | Finding | Verdict | Note | Status |
|---|-----|----------|---------|---------|------|--------|
| 1 | 🔴 | `src/foo.ts:42` | "Empty catch swallows the error" | ✅ | fixed last cycle | 🔧 fixed |
| 2 | 🟡 | `src/bar.ts:17` | "Magic number 86400" | 🟨 | intentional | ⏭️ skipped |
```

`Status` values: 🔧 fixed · ⏭️ skipped · 🔁 recurred · 🆕 new (raised this round).
Match a finding across cycles by **`(file, mechanism)`, not `(file, line)`** —
lines drift after edits, so match on the *nature of the defect*, not its
location. **`mechanism`** = what is wrong + which code element, independent of
line number — e.g. "unchecked null on `user.email`" or "off-by-one in the loop
bound": a later edit that shifts the line leaves the mechanism unchanged, so the
finding still matches. A matched finding keeps its `#`; only a 🆕 finding takes
the next free number. Round 0 (the first review) omits the `Status` column — as
do all stateless single-shot presentations (`/check`, `/open`, `/rebase`, and a
standalone `/fix`): they re-parse the raw latest review with no prior-round
memory, so they always render the round-0 table.

### Table formatting requirements

- Must be a **real markdown table** with `|` separators and header separator row
  (`|---|---|`). Not prose with bullet hyphens. Not a numbered list.
- All rows must have the same column count.
- Use the exact column headers above (translated to German if conversing in
  German: `# | Sev | Ort | Befund | Verdict | Notiz`, plus `Status` on
  re-reviews).
- If a review has zero findings: skip the table, write `No issues raised. LGTM.`
  under the Findings heading. **Exception (re-reviews):** when prior findings
  still need accounting for, render the Status table with their 🔧 fixed /
  ⏭️ skipped rows even if there are zero *new* findings — the LGTM shortcut is
  for round 0 (nothing raised yet, nothing to account for).

## Recommendation (REQUIRED — exactly one line)

Pick exactly one:

- **Any 🔴 blocking present** — including a carried-over 🔴 still ⏭️ skipped or
  ❌ disagreed on a `--loop` re-review → `Run /fix to work through them, then /cycle again.`
- **Only 🟡/⚪ findings** → `Address if you want. Otherwise: run /merge when ready.`
- **Zero findings** — round 0, or a re-review with every prior 🔴 resolved →
  `Ready to merge — run /merge.`
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
