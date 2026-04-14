# Shared Review Output Format

> Canonical format for presenting Claude review results. Used by `/pr-cycle`
> (after polling completes), `/pr-check` (for the latest review section), and
> `/pr-fix` (for the checklist before asking which to fix).

## Language

Match the language of the user conversation. Structure below uses English
labels; translate labels (`Severity`, `Finding`, `My assessment`, etc.) to
German when the conversation is in German. **Do not translate content** from
the review — quote it as-is.

## Required sections

Every presentation MUST include, in this order:

1. **Header** — one line with PR number + verdict emoji
2. **Status line** — CI, reviews, staleness
3. **Findings table** — all issues as a markdown table
4. **Previously raised** (optional) — only if prior cycle had issues
5. **Recommendation** — exactly one actionable next step

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

| # | Severity | Location | Finding | My assessment |
|---|----------|----------|---------|---------------|
| 1 | 🔴 blocking | `src/foo.ts:42` | "Empty catch block swallows the error" | Agree — log or rethrow |
| 2 | 🟡 suggestion | `src/bar.ts:17` | "Magic number 86400" | Partial agree — extract to const |
| 3 | ⚪ nit | `README.md:5` | "Typo: recieve → receive" | Agree, trivial |
```

### Column rules

- **`#`** — sequential 1, 2, 3… (lets user say "fix #1 and #3")
- **`Severity`** — one of the three severities with emoji:
  - 🔴 `blocking` — bug, correctness issue, missing test for critical path
  - 🟡 `suggestion` — improvement that the reviewer justified
  - ⚪ `nit` — style, trivial, typo
- **`Location`** — `` `file:line` `` in backticks. If multiple lines: `src/foo.ts:42,58`. If no specific line: `src/foo.ts`. If file-less: `—`
- **`Finding`** — quote the reviewer's key phrase in double quotes, keep it to ≤120 chars; omit the "Fix: …" suggestion (that goes in your assessment if you agree with it)
- **`My assessment`** — one of:
  - `Agree` — plus one-clause reason or proposed fix
  - `Partial agree` — plus what part you accept
  - `Disagree` — plus why (with link to counter-evidence if possible)
  - Keep to ≤80 chars. No essays.

### Table formatting requirements

- Must be a **real markdown table** with `|` separators and header separator row (`|---|---|`). Not prose with bullet hyphens. Not a numbered list.
- All rows must have the same column count.
- Use the exact column headers above (translated to German if conversing in German: `# | Severity | Ort | Befund | Meine Einschätzung`).
- If a review has zero findings: skip the table, write `No issues raised. LGTM.` under the Findings heading.

## Previously raised (optional)

Only include if this isn't the first review cycle and previous findings exist:

```
### Previously raised

| # | Issue | Status |
|---|-------|--------|
| — | Missing null check on user input | ✅ fixed last cycle |
| — | Extract magic number 86400 | ⏭️ skipped (intentional) |
```

## Recommendation (REQUIRED — exactly one line)

Pick exactly one:

- **Any 🔴 blocking present** → `Run /pr-fix to work through them, then /pr-cycle again.`
- **Only 🟡/⚪ findings** → `Address if you want. Otherwise: run /pr-merge when ready.`
- **Zero findings** → `Ready to merge — run /pr-merge.`
- **CI failing or merge-blocked** → `Investigate CI failure before proceeding.`
- **Review is stale** → `Push latest changes and run /pr-cycle for a fresh review.`

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
- ❌ Emojis inside the Finding column (they belong in Severity only)

## After the presentation

- **Do NOT immediately start fixing anything** — wait for the user to indicate which items to address
- User may reply with `#N`, `#N, #M`, `#N-M`, `all`, `blocking`, or `skip`
- If user wants to fix: hand off to `/pr-fix` for the interactive fix flow
