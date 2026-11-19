#!/usr/bin/env python3
"""Deterministic publish path for `/swarm:review --pr`.

The `--pr` review posts an output-gated comment to a GitHub PR. Its body is
built from LLM findings derived from an **attacker-controllable diff**, so the
whole publish path is security-sensitive and must NOT live as model-interpreted
prose (every prose fix spawned a new prose-shell bug — see the task origin).
This script owns the three pieces that must stay mechanical:

  build  — assemble the exact comment body from structured, gated rows +
           balance + PR metadata, running EVERY cell (and the PR title) through
           a deterministic per-cell sanitizer. Emits the body to stdout so the
           caller can show it for the one human confirm gate — no temp file to
           strand.
  post   — the real stale-head gate (re-read the live head via `gh`; on a
           mismatch DO NOT post, return a status the caller acts on), then
           `gh pr comment --body-file` with a self-cleaning temp body file
           rebuilt from the SAME input, so what was shown is what is posted.

  loop-target / loop-tree / loop-build / loop-post — the `--loop --pr` loop
           report: resolve the PR the loop reports to (local HEAD must be its
           head), pin the tree the loop left, render one
           summary comment opening with a machine-readable marker, and create it
           or update this user's earlier one. See `render_loop_body`.

`build` is a pure render (no I/O beyond reading its JSON input), which is what
makes the sanitizer + body shape unit-testable. `post` layers the gate + the
`gh` calls on top.

Input JSON (via --input <path>, or --input - for stdin):
  {
    "pr_num": 29,
    "title": "<PR title — UNTRUSTED contributor input>",
    "head_oid": "<full reviewed SHA>",
    "rows": [ {num,sev,ort,befund,quelle,v,notiz[,kind,lens]}, ... ],  # gated findings
    "has_quelle": true,        # optional; defaults to: any row has a quelle
    "balance": "Bilanz: …\nAgents: …\nLenses: …",
    "agents": ["claude", "codex", "grok", "kimi"],      # backends that entered the workflow
    "notes": ["Redactions: …", "Backend error: …"],        # optional extra lines
    "empty": false             # true -> "No issues raised." instead of a table
  }

Every finding cell (ort/befund/quelle/notiz) and the title are attacker-
influenced. `sev`/`v`/`num` are model glyphs; they pass through the same
sanitizer unharmed (glyphs contain none of the neutralized characters).

Row `kind`/`lens` are pass-throughs from the workflow's findings: `kind:
"design"` rows render AFTER all defect rows (design suggestions must not dilute
the defect ranking) and their finding cell gets a visible `[lens]` prefix —
ordering + prefixing are enforced HERE, deterministically, so the caller passes
rows through verbatim and never hand-orders or hand-prefixes (the prose version
of that rule is exactly the drift class this script exists to close). Bucketing
keys on `kind` ALONE: anything other than an explicit `"design"` — including a
missing/dropped kind — is a defect, the safe bucket. The lens is NOT a fallback
signal (it can't tell a genuine design row that lost its kind from a defect
carrying a design lens, so guessing from it could hide a bug in the Design
table); the workflow emits kind on every finding, so a real design row has it.

Exit-code convention (matches loop-closeout.py + the skill's step-1 block):
  operational outcomes (stale head, unverifiable head, gh missing, post failure)
  -> token + exit 0, the caller branches on the token;
  programmer misuse (bad JSON, non-list rows, missing field) -> stderr + exit 2.

`post` fails CLOSED: a mismatched head (SWARM_PR_STALE) OR an unreadable live
head (SWARM_PR_HEAD_UNVERIFIED) both stop before posting — publishing a possibly
stale review under the user's identity is worse than a retry.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

GH_TIMEOUT = 60  # seconds for any single gh call

_AGENT_LABELS = {
    "claude": "session-model lenses",
    "codex": "codex",
    "grok": "grok",
    "kimi": "kimi",
}


def render_footer(data) -> str:
    """Name only the backend families that actually entered the workflow."""
    raw = data.get("agents")
    active = {item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()
    # Unknown backends keep their raw name rather than vanishing: the footer
    # claims to name every family that entered the workflow.
    labels = [_AGENT_LABELS.get(b, b) for b in _AGENT_LABELS if b in active]
    # ...but sanitized + redacted: an unknown name is caller-supplied text.
    labels += sorted(sanitize_prose(redact(b)[0]) for b in active if b not in _AGENT_LABELS)
    suffix = f" ({' + '.join(labels)})" if labels else ""
    return (
        f"<sub>Local mixture-of-agents review{suffix} run from the author's "
        "machine — not a hosted bot. Verdicts (✅/\U0001f7e8/❌) are the "
        "runner's own assessment.</sub>"
    )


# --------------------------------------------------------------------------- #
# Sanitizers — deterministic, per-cell. The ONE place untrusted content leaves
# the sandbox to a public venue under the user's identity, so fail closed.
# --------------------------------------------------------------------------- #
def sanitize_prose(text) -> str:
    """Make an untrusted string safe to drop into a Markdown TABLE CELL.

    Order matters: encode existing ``&`` FIRST so an attacker cannot smuggle a
    live entity (``&#64;`` -> ``&amp;#64;``, rendered literally), then insert
    our OWN entities last so they are not double-encoded. Every transform is a
    linkify/table trigger:

      - flatten all whitespace / control chars -> single spaces (a newline would
        break the table row);
      - ``&`` -> ``&amp;`` (see above);
    Every neutralized character is **entity-encoded, never backslash-escaped.**
    Backslash-escaping is defeated by an attacker-supplied backslash: ``\\|``
    would become ``\\\\|`` — Markdown eats the ``\\\\`` as one literal backslash
    and the pipe is live again (same for ``\\[label\\](url)`` re-opening a link).
    An HTML numeric entity has no such bypass: the table row splits on a *literal*
    ``|`` before inline parsing, and ``&#124;`` carries none, so the delimiter can
    never re-form; the browser decodes it back to a visible ``|`` on render.

      - ``&`` -> ``&amp;`` FIRST, so an attacker cannot smuggle a live entity
        (``&#124;`` -> ``&amp;#124;``, shown literally) and our own entities below
        (inserted after) are not double-encoded;
      - ``<`` ``>`` -> entities: neutralizes ALL raw HTML (no tag can form);
      - ``|`` -> ``&#124;``: the cell can never split the table column;
      - ``[`` ``]`` -> entities: breaks ``[text](url)`` / ``![](…)`` link + image;
      - backtick -> ``&#96;``: no code span can open;
      - ``*`` ``_`` ``~`` -> entities: no emphasis / strikethrough can form;
      - ``@`` -> ``&#64;``: no ``@mention`` can autolink;
      - ``://`` and ``www.`` -> entity-broken: GFM autolinks ``scheme://host``
        and ``www.host``; breaking the trigger de-links a bare URL to inert text.
    """
    s = "" if text is None else str(text)
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)   # CR/LF/TAB + other control chars
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("&", "&amp;")               # must precede our own entities
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("|", "&#124;")
    s = s.replace("[", "&#91;").replace("]", "&#93;")
    s = s.replace("`", "&#96;")
    s = s.replace("*", "&#42;").replace("_", "&#95;").replace("~", "&#126;")
    s = s.replace("@", "&#64;")
    s = s.replace("://", ":&#47;&#47;")
    s = re.sub(r"(?i)\bwww\.", "www&#46;", s)
    return s


def sanitize_code(text) -> str:
    """Render an untrusted value (e.g. ``file:line``) as an inert code span.

    A code span never mentions, autolinks, or renders HTML, so the only things
    to handle are the two characters that would break it or the table. Both are
    **stripped, not escaped** — entities do not decode inside a code span, and
    backslash-escaping is bypassable (an attacker ``\\|`` in a filename yields
    ``\\\\|``, an even backslash count, so the table splitter treats the pipe as
    a live delimiter). A real ``file:line`` never contains ``|`` or a backtick,
    so dropping them is lossless in practice and leaves nothing to re-form. An
    empty value yields an empty string, not an empty ``````` span.
    """
    s = "" if text is None else str(text)
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("`", "").replace("|", "")
    return f"`{s}`" if s else ""


def fence_safe(text) -> str:
    """Neutralize triple-backtick runs so balance text can't break its ``` fence."""
    return re.sub(r"`{3,}", "``", "" if text is None else str(text))


# --------------------------------------------------------------------------- #
# Stale-head gate — a pure decision, so it is unit-testable without gh.
# --------------------------------------------------------------------------- #
def stale_gate(expected: str, now: str):
    """Compare the reviewed head SHA against the live head.

    Returns ``(status, message)``:
      - ``error``   — no reviewed SHA was passed (caller misuse; must not post);
      - ``stale``   — live head differs from the reviewed one -> DO NOT post;
      - ``unknown`` — the live head could not be read (empty ``now``) ->
                      indeterminate; the caller fails CLOSED (does not post),
                      since a stale revision can't be ruled out;
      - ``ok``      — live head matches the reviewed SHA -> safe to post.
    """
    expected = (expected or "").strip()
    now = (now or "").strip()
    if not expected:
        return "error", "no reviewed head SHA provided"
    if not now:
        return "unknown", "could not read the live PR head"
    if now != expected:
        return "stale", f"PR advanced ({expected} → {now}) since the review"
    return "ok", "live head matches the reviewed revision"


# --------------------------------------------------------------------------- #
# Body assembly.
# --------------------------------------------------------------------------- #
def _short(oid) -> str:
    """Short SHA for the header — hex only, so an embedded newline or markdown in
    a JSON `head_oid` can't split or inject the single-line header. `str(...)`
    first so a non-string JSON value (e.g. an int) can't raise in `re.sub`."""
    return re.sub(r"[^0-9a-fA-F]", "", str(oid) if oid else "")[:7]


def _safe_pr_num(pr_num):
    """A header-safe PR number. A bare integer string passes through; anything
    else (a JSON `pr_num` like ``29\\n\\n**evil**``) is run through the cell
    sanitizer so it can't inject markdown into the header outside the table."""
    if pr_num is None:
        return None
    if re.fullmatch(r"[0-9]+", str(pr_num)):
        return str(pr_num)
    return sanitize_prose(pr_num)


# Design-cluster lens names, mirrored from swarm-review.js LENS_CLUSTERS.design
# (kept in sync by test_lens_sync.py). NOT a kind fallback (the retired
# DESIGN_LENSES was — bucketing keys on `kind` ALONE, see _row_kind): this runs
# only INSIDE the already-decided design branch, purely to recognize a "[lens] "
# self-tag the finder may already have baked into the summary, so we don't add a
# second prefix. It can never move a row between the defect/design tables.
DESIGN_LENS_TAGS = ("reuse", "simplification", "efficiency", "altitude")
# Match a leading "[lens]" tag the SAME way the workflow's own parser does
# (/^\s*\[([\w-]+)\]/) — no trailing-whitespace requirement. Requiring a space
# after "]" would miss a valid but unspaced self-tag ("[reuse]ExtractHelper" or a
# bare "[reuse]"), which the workflow accepts as tagged, and re-prefix it to
# "[reuse] [reuse]…" — the exact double-prefix this guard exists to prevent.
_DESIGN_PREFIX_RE = re.compile(
    r"\s*\[(" + "|".join(DESIGN_LENS_TAGS) + r")\]", re.IGNORECASE
)


def _row_kind(r: dict) -> str:
    """Row kind. 'design' ONLY when explicitly tagged `kind: "design"`; anything
    else — including a missing/junk kind — is a defect, the safe bucket. The lens
    is deliberately NOT a backup signal: a lens can't distinguish a genuine design
    row that dropped its kind from a defect that keeps a design lens (a mixed
    cluster resolved to defect with a design plurality lens, or a reclassified
    bug), so guessing 'design' from the lens could hide a real defect in the
    Design table. Hiding a bug is worse than showing a suggestion among defects,
    so an absent kind falls to defect — the workflow emits kind on every finding
    and step 5 passes it through, so a real design row carries it explicitly."""
    return "design" if str(r.get("kind") or "").strip().lower() == "design" else "defect"


def _ordered_rows(rows):
    """Defect rows first, design after — a stable partition, so the caller's
    severity order survives within each kind."""
    defect_rows, design_rows = [], []
    for r in rows:
        (design_rows if _row_kind(r) == "design" else defect_rows).append(r)
    return defect_rows + design_rows


def _finding_cell(r: dict):
    """The finding text, with a design row's visible "[lens] " prefix unless the
    text already opens with a known design-lens self-tag (no double prefix)."""
    befund = r.get("befund")
    if _row_kind(r) != "design":
        return befund
    body = "" if befund is None else str(befund)
    if _DESIGN_PREFIX_RE.match(body):
        return body
    lens = str(r.get("lens") or "").strip() or "design"
    return f"[{lens}] {body}".rstrip()


def render_body(data: dict) -> str:
    """Assemble the full comment body (GitHub-flavored Markdown) from `data`.

    Deterministic: same input -> byte-identical output, which is why `build`
    (what the user confirms) and `post` (what is sent) can each call this from
    the same JSON and be guaranteed identical.
    """
    # Ordering enforced here (not by the calling prose) so a posted comment can
    # never interleave suggestions into the defect ranking.
    rows = _ordered_rows(data.get("rows") or [])
    has_quelle = data.get("has_quelle")
    if has_quelle is None:
        # str(...) so a non-string cell value (e.g. {"quelle": 1}) can't raise
        # AttributeError here — cells are otherwise coerced by the sanitizers.
        has_quelle = any(str(r.get("quelle") or "").strip() for r in rows)

    head_short = _short(data.get("head_oid", ""))
    parts = [f"## \U0001f41d Swarm review (local ensemble) · reviewed at {head_short}", ""]

    # PR # + title identify the reviewed PR in the posted comment. The title is
    # UNTRUSTED contributor input, so it goes through the SAME per-cell sanitizer
    # before it lands in this header (resolves the title-escaping finding).
    pr_num = _safe_pr_num(data.get("pr_num"))
    title = sanitize_prose(data.get("title", ""))
    if pr_num is not None or title:
        label = f"PR #{pr_num}" if pr_num is not None else "PR"
        parts += [f"**{label}:** {title}".rstrip(), ""]

    if data.get("empty") or not rows:
        parts.append("No issues raised.")
    else:
        if has_quelle:
            header = ["#", "Sev", "Location", "Finding", "Source", "V", "Note"]
        else:
            header = ["#", "Sev", "Location", "Finding", "V", "Note"]
        parts.append("| " + " | ".join(header) + " |")
        parts.append("|" + "|".join("---" for _ in header) + "|")
        for r in rows:
            # Design rows carry their lens as a visible "[lens] " prefix
            # (_finding_cell), added BEFORE sanitizing: the brackets come out
            # entity-encoded and render back as a literal [lens].
            befund = _finding_cell(r)
            cells = [
                sanitize_prose(r.get("num", "")),
                sanitize_prose(r.get("sev", "")),
                sanitize_code(r.get("ort", "")),
                sanitize_prose(befund),
            ]
            if has_quelle:
                cells.append(sanitize_prose(r.get("quelle", "")))
            cells += [
                sanitize_prose(r.get("v", "")),
                sanitize_prose(r.get("notiz", "")),
            ]
            parts.append("| " + " | ".join(cells) + " |")

    balance = data.get("balance")
    if balance:
        parts += ["", "```", fence_safe(balance).rstrip("\n"), "```"]

    for note in data.get("notes") or []:
        note = sanitize_prose(note)
        if note:
            parts += ["", note]

    parts += ["", render_footer(data)]
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# gh wrappers — thin, so the testable logic stays in the pure functions above.
# --------------------------------------------------------------------------- #
def _gh(args, input_file=None):
    """Run gh; return (ok, stdout, stderr). The one subprocess seam for gh."""
    cmd = ["gh", *args] + (["--input", input_file] if input_file else [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=GH_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "", str(e)
    return p.returncode == 0, p.stdout.strip(), p.stderr.strip()


def _gh_live_head(pr_num: str):
    """Return the live head SHA of the PR, or "" if it can't be read."""
    ok, out, _ = _gh(["pr", "view", str(pr_num), "--json", "headRefOid", "--jq", ".headRefOid"])
    return out if ok else ""


def _gh_comment(pr_num: str, body_file: str):
    """Post the comment. Returns (ok, url_or_error)."""
    ok, out, err = _gh(["pr", "comment", str(pr_num), "--body-file", body_file])
    return (True, out) if ok else (False, err or out or "gh pr comment failed")


# --------------------------------------------------------------------------- #
# Loop report — one comment per PR summarizing a finished `--loop --pr` run.
# --------------------------------------------------------------------------- #
# The first line of the body. Machine-readable on purpose: a later merge gate
# reads `head=` to learn that a loop review ran on that exact commit, and
# `loop-post` finds the comment to update by this prefix (own comments only).
LOOP_MARKER = "swarm-loop-report:v1"
LOOP_MARKER_PREFIX = f"<!-- {LOOP_MARKER} "
GIT_TIMEOUT = 60  # seconds for any single git call


def loop_reasons() -> dict:
    """Termination reasons + labels, from loop-closeout.py — the script that
    decides them, so the report never names a reason the loop can't produce.
    Loaded on first use, so a broken sibling cannot take `build`/`post` down."""
    global _LOOP_REASONS
    if _LOOP_REASONS is None:
        try:
            spec = importlib.util.spec_from_file_location(
                "loop_closeout", os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop-closeout.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _LOOP_REASONS = dict(mod.REASONS)
        except Exception as e:  # any failure of the sibling is a usage error here, not a traceback
            raise ValueError(f"cannot load loop-closeout.py: {e}") from e
    return _LOOP_REASONS


_LOOP_REASONS = None
# Reasons that fire BEFORE the last round's re-review: its fixes went unreviewed.
LOOP_UNREVIEWED = ("cap", "design-only")

SEV_BUCKETS = {"🔴": "critical", "🟡": "warning", "⚪": "minor"}
BUCKETS = ("critical", "warning", "minor", "design")
BUCKET_LABELS = {"critical": "🔴 critical", "warning": "🟡 warning", "minor": "⚪ minor", "design": "Design"}

# The repo is public, so no secret and no local path may reach the comment.
# Same patterns as work-system's `migrate` (task-store.py SECRET_PATTERNS +
# PUBLIC_PATTERNS) — copied, not imported: a plugin must not depend on another
# plugin's file layout; test_loop_report.py keeps the copy in sync. Some are
# deliberately stronger here, because migrate only NAMES a hit while this
# REPLACES it: private-key takes the whole PEM block (a header-only match would
# publish the key body), quoted-secret takes a quoted value with spaces whole,
# and local-path takes the user name (also behind /mnt/c, on Windows with spaces,
# and in Claude Code's dash-encoded project dirs) but leaves repo-relative
# `src/home/x` alone. Matches are REPLACED (the loop is over; refusing would
# lose the report), and the body says how many were. Order matters: a quoted
# value goes before the unquoted assignment patterns would cut it short.
REDACT_PATTERNS = [
    ("quoted-secret", r"(?i)\b[A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
                      r"credentials?)[A-Za-z0-9_]*['\"]?\s*[:=]\s*(?:\"[^\"\n]{6,}\"|'[^'\n]{6,}')"),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    ("aws-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
                    r"(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|[\s\S]*)"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("api-key", r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|\bAIza[0-9A-Za-z_-]{35}\b"
                r"|\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|\bglpat-[A-Za-z0-9_-]{20,}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}"),
    ("url-credentials", r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
    ("env-assignment", r"(?m)^[ \t]*(?:export[ \t]+)?[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|"
                       r"API_?KEY|PRIVATE_KEY|CREDENTIALS?)[A-Z0-9_]*\s*=\s*['\"]?[^\s'\"]{6,}"),
    ("secret-assignment", r"(?i)\b[a-z0-9_]*(?:password|passwd|secret|api[_-]?key|"
                          r"access[_-]?token|client[_-]?secret)[a-z0-9_]*['\"]?\s*[:=]\s*"
                          r"['\"]?[^\s'\"`]{8,}"),
    ("private-host", r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|internal|lan|corp|intranet|"
                     r"home\.arpa)\b(?!\.?[\w-])"),
    ("private-ip", r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    ("local-path", r"(?<![\w.-])(?:/[\w.-]+)*?/(?:Users|home)/[A-Za-z0-9._-]+"
                   r"|\b[A-Z]:\\Users\\[^\\\n]*"
                   r"|(?<![\w.])-(?:Users|home)-[^/\s]*"),
]
_REDACT_RES = [(kind, re.compile(pat)) for kind, pat in REDACT_PATTERNS]


def redact(text):
    """Return (text, count) with every secret / local-path shape replaced."""
    s = "" if text is None else str(text)
    total = 0
    for kind, rx in _REDACT_RES:
        s, n = rx.subn(f"[redacted:{kind}]", s)
        total += n
    return s, total


def _full_oid(oid) -> str:
    """A full SHA-1/SHA-256 hex id, or "" — the marker never carries anything else."""
    s = str(oid or "").strip()
    return s.lower() if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", s) else ""


def _is_count(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def validate_loop(data: dict) -> dict:
    """Check the loop-report input and derive the counted fields.

    Raises ValueError on misuse. Everything the marker states is either
    validated here or derived (never trusted as free text): head and trees are
    full hex ids, the reason a loop-closeout key consistent with the counts,
    rounds = len(trajectory), open counts come from the open rows, and the last
    trajectory entry must equal the number of open rows (a miscount fails
    loudly, not in public).
    """
    reasons = loop_reasons()
    pr = data.get("pr_num")
    if pr is None or not re.fullmatch(r"[0-9]+", str(pr)):
        raise ValueError("pr_num must be a bare PR number")
    head = _full_oid(data.get("head_oid"))
    if not head:
        raise ValueError("head_oid must be a full commit SHA")
    tree, reviewed = _full_oid(data.get("tree")), _full_oid(data.get("reviewed_tree"))
    if not tree or not reviewed:
        raise ValueError("tree and reviewed_tree must be full tree ids from `loop-tree`")
    end = data.get("end")
    if not isinstance(end, str) or end not in reasons:
        raise ValueError(f"end must be one of {', '.join(reasons)}")
    traj = data.get("open_trajectory")
    if not isinstance(traj, list) or not traj or not all(_is_count(n) for n in traj):
        raise ValueError("open_trajectory must be a non-empty list of integers >= 0")
    cap = data.get("cap")
    if cap is not None and (not _is_count(cap) or cap < len(traj)):
        raise ValueError("cap must be an integer >= the number of rounds")
    if end == "cap" and cap != len(traj):
        raise ValueError("end=cap needs cap == the number of rounds")
    fixed = data.get("fixed", {})
    if not isinstance(fixed, dict) or set(fixed) - set(BUCKETS) or not all(_is_count(v) for v in fixed.values()):
        raise ValueError(f"fixed must map {'/'.join(BUCKETS)} to integers >= 0")
    rows = data.get("open_rows", [])
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("open_rows must be a list of objects")
    if end == "0-findings" and rows:
        raise ValueError("end=0-findings with open rows")
    open_counts = dict.fromkeys(BUCKETS, 0)
    for r in rows:
        if _row_kind(r) == "design":
            open_counts["design"] += 1
            continue
        bucket = SEV_BUCKETS.get(str(r.get("sev") or "").strip())
        if bucket is None:
            raise ValueError(f"open row #{r.get('num')}: sev must be one of {' '.join(SEV_BUCKETS)}")
        open_counts[bucket] += 1
    if traj[-1] != len(rows):
        raise ValueError(f"last open_trajectory entry ({traj[-1]}) != number of open_rows ({len(rows)})")
    fixed_counts = {b: fixed.get(b, 0) for b in BUCKETS}
    # FIXES_TOTAL is counted per applied fix, the buckets per finding: a
    # disagreement means a fix was dropped or double-counted.
    total = data.get("fixes_total")
    if not _is_count(total) or total != sum(fixed_counts.values()):
        raise ValueError(f"fixes_total ({total!r}) must equal the sum of fixed ({sum(fixed_counts.values())})")
    notes = data.get("coverage_notes", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise ValueError("coverage_notes must be a list of strings")
    rereviewed = end not in LOOP_UNREVIEWED
    # A reason that fires AFTER the last review claims the tree it saw is the
    # tree the loop left; anything that changed it since makes that false.
    if rereviewed and tree != reviewed:
        raise ValueError(f"end={end} claims the last review saw the final tree, but the tree "
                         "changed after that review's prep — re-review, or no report")
    return {
        "pr": str(pr), "head": head, "tree": tree, "end": end, "traj": traj, "cap": cap,
        "rounds": len(traj), "rows": rows,
        "open": open_counts, "fixed": fixed_counts,
        "open_total": len(rows), "fixed_total": total,
        "rereviewed": rereviewed, "notes": notes,
        "clean": end == "0-findings",
    }


def render_loop_body(data: dict) -> str:
    """The loop-report comment. Deterministic like render_body, so `loop-build`
    (what the user confirms) and `loop-post` (what is sent) match byte for byte."""
    return _render_loop(validate_loop(data), data)


def _render_loop(v: dict, data: dict) -> str:
    yn = lambda b: "yes" if b else "no"  # noqa: E731
    marker = (f"{LOOP_MARKER_PREFIX}pr={v['pr']} head={v['head']} rounds={v['rounds']} "
              f"end={v['end']} clean={yn(v['clean'])} rereviewed={yn(v['rereviewed'])} "
              f"degraded={yn(v['notes'])} "
              f"open={v['open_total']} fixed={v['fixed_total']} -->")
    rounds = f"{v['rounds']}" + (f" (cap {v['cap']})" if v["cap"] is not None else "")
    parts = [
        marker,
        f"## \U0001f41d Swarm loop report · head {v['head'][:7]}",
        "",
        f"**End:** {'clean' if v['clean'] else 'not clean'} — {loop_reasons()[v['end']]}",
        f"**Head:** `{v['head']}` · **Rounds:** {rounds} · **Fixes applied:** {v['fixed_total']}",
        f"**Open per round:** {' → '.join(str(n) for n in v['traj'])}",
    ]
    if v["end"] == "cap":   # design-only's label already carries this caveat
        parts.append("**Note:** the last round's fixes were applied but not re-reviewed.")
    parts += [
        "",
        "| | " + " | ".join(BUCKET_LABELS[b] for b in BUCKETS) + " |",
        "|---|" + "---|" * len(BUCKETS),
        "| Fixed | " + " | ".join(str(v["fixed"][b]) for b in BUCKETS) + " |",
        "| Open | " + " | ".join(str(v["open"][b]) for b in BUCKETS) + " |",
    ]
    redacted = 0
    if v["rows"]:
        parts += ["", "### Open items", "", "| # | Sev | Location | Finding | Status |", "|---|---|---|---|---|"]
        for r in _ordered_rows(v["rows"]):
            cells = []
            for val, fn in ((r.get("num"), sanitize_prose), (r.get("sev"), sanitize_prose),
                            # stripped BEFORE redacting, or a `|` could split a secret
                            # the pattern then misses and sanitize_code rejoins
                            (re.sub(r"[`|]", "", str(r.get("ort") or "")), sanitize_code),
                            (_finding_cell(r), sanitize_prose),
                            (r.get("status"), sanitize_prose)):
                clean, n = redact(val)
                redacted += n
                cells.append(fn(clean))
            parts.append("| " + " | ".join(cells) + " |")
    for note in v["notes"]:   # lost coverage (a timed-out voice) is part of the result
        clean, n = redact(note)
        redacted += n
        if sanitize_prose(clean):
            parts += ["", "⚠️ " + sanitize_prose(clean)]
    agents = data.get("agents")
    if isinstance(agents, list):   # the footer's unknown names, deduplicated like render_footer
        redacted += sum(redact(b)[1] for b in {b for b in agents if isinstance(b, str)} if b not in _AGENT_LABELS)
    if redacted:
        parts += ["", f"{redacted} value(s) redacted (secret or local-path shapes)."]
    parts += ["", render_footer(data)]
    body = "\n".join(parts) + "\n"
    if len(body) > GITHUB_COMMENT_MAX:
        raise ValueError(f"report is {len(body)} chars, over GitHub's {GITHUB_COMMENT_MAX}-char comment limit")
    return body


GITHUB_COMMENT_MAX = 65536

# Untracked files a lane carries that never belong to the reviewed tree — the
# same set the prep block's RECORD_HEAD check ignores (test-synced). ONE list:
# the dirty check and the tree snapshot both read it, so they cannot drift.
LANE_UNTRACKED = r"(TASK\.md|MANDATE\.md|\.MANDATE\.[^/]*|\.swarm-workflow\.[^/]*/.*)"
_LANE_UNTRACKED_RE = re.compile(r"^\?\? " + LANE_UNTRACKED + "$")   # the prep block's spelling
_LANE_PATH_RE = re.compile(LANE_UNTRACKED + r"\Z")


def _git(*args, env=None, cwd=None):
    """Run git; stdout on success, None on any failure. Decoded without newline
    translation and with surrogateescape, so a `-z` path list round-trips."""
    try:
        p = subprocess.run(["git", *args], capture_output=True, timeout=GIT_TIMEOUT, env=env, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.decode("utf-8", "surrogateescape") if p.returncode == 0 else None


def _toplevel() -> str:
    out = _git("rev-parse", "--show-toplevel")
    return out.rstrip("\n") if out else ""   # rstrip only: a path may end in a space


def local_state():
    """(HEAD sha or "", HEAD tree or "", dirty: bool|None). None = could not tell."""
    out = (_git("rev-parse", "HEAD", "HEAD^{tree}") or "").split()
    head, tree = (out + ["", ""])[:2]
    top = _toplevel()
    status = _git("status", "--porcelain", "-z", "--untracked-files=all", cwd=top or None) if top else None
    if status is None:
        return head, tree, None
    dirty, entries, i = False, status.split("\0"), 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if not e:
            continue
        code, path = e[:2], e[3:]
        if code[0] in "RC":
            i += 1                         # -z prints a rename's source as its own entry
        if not (code == "??" and _LANE_PATH_RE.match(path)):
            dirty = True
    return head, tree, dirty


def worktree_tree() -> str:
    """The tree id of the working tree as the loop left it: HEAD plus every
    tracked or staged change (lane-named tracked files included) and every untracked,
    non-ignored file except the lane's own. Built in a throwaway index, so the
    real index is never touched. "" on failure.

    Untracked files are named explicitly rather than excluded by pathspec: an
    exclude pathspec naming a gitignored file makes `git add` fail outright."""
    top = _toplevel()
    if not top:
        return ""
    real_index = (_git("rev-parse", "--path-format=absolute", "--git-path", "index", cwd=top) or "").rstrip("\n")
    with tempfile.TemporaryDirectory(prefix="swarm-loop-index.") as d:
        # Seeded from a COPY of the real index (force-added ignored files stay
        # in); literal pathspecs, so a file named `T*` or `:x` is just a name.
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(d, "index"), "GIT_LITERAL_PATHSPECS": "1"}
        try:
            shutil.copyfile(real_index, env["GIT_INDEX_FILE"])
        except OSError:
            if _git("read-tree", "HEAD", env=env, cwd=top) is None:   # no index yet
                return ""
        if _git("add", "-u", "--", ".", env=env, cwd=top) is None:
            return ""
        others = _git("ls-files", "--others", "--exclude-standard", "-z", env=env, cwd=top)
        if others is None:
            return ""
        new = [p for p in others.split("\0") if p and not _LANE_PATH_RE.match(p)]
        if new:
            listing = os.path.join(d, "paths")
            with open(listing, "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write("\0".join(new) + "\0")
            if _git("add", f"--pathspec-from-file={listing}", "--pathspec-file-nul",
                    env=env, cwd=top) is None:
                return ""
        return _full_oid(_git("write-tree", env=env, cwd=top))


def cmd_loop_tree(a: argparse.Namespace) -> int:
    tree = worktree_tree()
    print(f"LOOP_TREE={tree}" if tree else "SWARM_PR_ERR=could not compute the working tree id")
    return 0


def find_loop_comment(pr_num: str):
    """Return (ok, comment_id or None). Only the CURRENT gh user's comments
    count: anyone can paste the marker, and only our own comment is ours to
    update. ok=False -> the lookup failed; the caller must not post (a blind
    create could duplicate the report)."""
    ok, login, _ = _gh(["api", "user", "--jq", ".login"])
    if not ok or not login:
        return False, None
    ok, out, _ = _gh(["api", "--paginate", f"repos/{{owner}}/{{repo}}/issues/{pr_num}/comments?per_page=100",
                      "--jq", f'.[] | [.id, .user.login, (.body | startswith("{LOOP_MARKER_PREFIX}"))]'])
    if not ok:
        return False, None
    found = None
    for line in out.splitlines():
        try:
            cid, who, marked = json.loads(line)
        except (ValueError, TypeError):
            return False, None
        if marked and who == login:
            found = cid          # newest wins (the API lists oldest first)
    return True, found


def cmd_loop_build(a: argparse.Namespace) -> int:
    try:
        data = _load_input(a.input)
        body = render_loop_body(data)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"pr-post: cannot read loop input: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(body)
    return 0


def cmd_loop_target(a: argparse.Namespace) -> int:
    """Resolve the PR a `--loop --pr` run reports to, before round 0."""
    if a.pr is not None and not re.fullmatch(r"[0-9]+", a.pr):
        print(f"SWARM_PR_ERR=--pr expects a bare PR number, got: {a.pr}")
        return 0
    if shutil.which("gh") is None:
        print("SWARM_PR_ERR=gh CLI not found")
        return 0
    view = ["pr", "view"] + ([a.pr] if a.pr else []) + ["--json", "number,headRefOid,state,baseRefName"]
    ok, out, err = _gh(view)
    try:
        meta = json.loads(out) if ok else None
    except ValueError:
        meta = None
    if not isinstance(meta, dict):
        print(f"SWARM_PR_ERR=cannot read the PR{' #' + a.pr if a.pr else ' of the current branch'}: {err}")
        return 0
    num, oid = str(meta.get("number") or ""), _full_oid(meta.get("headRefOid"))
    if meta.get("state") != "OPEN":
        print(f"SWARM_PR_ERR=PR #{num} is not open")
        return 0
    # Every round diffs against the default branch, so only a PR into it is fully
    # covered — a stacked PR's diff would claim commits no round reviewed.
    default = (_git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD") or "").strip()
    default = default.split("/", 1)[1] if "/" in default else default
    if meta.get("baseRefName") not in {default or "main", "main", "master"}:
        print(f"SWARM_PR_ERR=PR #{num} targets {meta.get('baseRefName')!r}, not the default branch; "
              "the loop reviews the delta to the default branch, so its report would not cover this PR")
        return 0
    head = (_git("rev-parse", "HEAD") or "").strip()
    if not oid or head != oid:
        print(f"SWARM_PR_ERR=local HEAD is not the head of PR #{num}; the loop reviews the local "
              f"tree, so check the PR out first: gh pr checkout {num}")
        return 0
    print(f"LOOP_PR_NUM={num}")
    print(f"LOOP_PR_HEAD_OID={oid}")
    return 0


def _live_gate(v: dict):
    """None when the live PR head is the report's head, else the token line."""
    status, msg = stale_gate(v["head"], _gh_live_head(v["pr"]))
    if status == "stale":
        return f"SWARM_PR_STALE={msg} — NOT posting (push the loop's fixes first)"
    if status != "ok":
        return f"SWARM_PR_HEAD_UNVERIFIED={msg} — NOT posting (re-run to retry)"
    return None


def cmd_loop_post(a: argparse.Namespace) -> int:
    try:
        data = _load_input(a.input)
        v = validate_loop(data)
        body = _render_loop(v, data)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"pr-post: cannot read loop input: {e}", file=sys.stderr)
        return 2
    if shutil.which("gh") is None:
        print("SWARM_PR_POST_ERR=gh CLI not found")
        return 0
    # The report pins a head; it may only be posted when that head IS the tree
    # the loop left (committed, nothing on top) AND what the PR now shows.
    head, head_tree, dirty = local_state()
    if dirty is None:
        print("SWARM_PR_LOCAL_UNVERIFIED=could not read the working tree — NOT posting")
        return 0
    if head != v["head"] or dirty:
        print(f"SWARM_PR_LOCAL_MISMATCH=the report pins {v['head'][:7]} but the checkout is at "
              f"{head[:7] or '?'}{' with uncommitted changes' if dirty else ''} — commit and push "
              "the loop's fixes, then rebuild — NOT posting")
        return 0
    # The commit must hold exactly the tree the loop left (`loop-tree` at
    # close-out): a hook, a pull or another session's commit in between would
    # otherwise be stamped as reviewed.
    if head_tree != v["tree"]:
        print(f"SWARM_PR_TREE_MISMATCH=commit {v['head'][:7]} does not hold the tree the loop left "
              "(something changed it after the close-out) — NOT posting")
        return 0
    gate = _live_gate(v)
    if gate:
        print(gate)
        return 0
    ok, cid = find_loop_comment(v["pr"])
    if not ok:
        print("SWARM_PR_POST_ERR=could not list the PR's comments to find an earlier loop "
              "report — NOT posting (a blind post could duplicate it)")
        return 0
    gate = _live_gate(v)            # again, as late as possible: the lookup can be slow
    if gate:
        print(gate)
        return 0
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", prefix="swarm-loop-body.", delete=False)
    except OSError as e:
        print(f"SWARM_PR_POST_ERR=could not create temp body file: {e}")
        return 0
    try:
        json.dump({"body": body}, tmp)
        tmp.close()
        if cid is None:
            args = ["api", "-X", "POST", f"repos/{{owner}}/{{repo}}/issues/{v['pr']}/comments"]
        else:
            args = ["api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{cid}"]
        ok, url, err = _gh(args + ["--jq", ".html_url"], input_file=tmp.name)
    except OSError as e:
        print(f"SWARM_PR_POST_ERR=could not write temp body file: {e}")
        return 0
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    if not ok:
        print(f"SWARM_PR_POST_ERR={err or 'gh api failed'}")
    elif cid is None:
        print(f"SWARM_PR_POSTED={url}")
    else:
        print(f"SWARM_PR_UPDATED={url}")
    return 0


# --------------------------------------------------------------------------- #
# Input + subcommands.
# --------------------------------------------------------------------------- #
def _load_input(path: str) -> dict:
    if path == "-":
        raw = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as f:   # closed on any parse/validate failure
            raw = f.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    # `rows` must be a list of objects — render_body indexes each row with
    # `.get`, so a non-list or a non-dict element would raise an uncaught
    # AttributeError/TypeError and exit 1 with a traceback instead of the
    # documented misuse contract (clean stderr + exit 2). Validate at the seam.
    rows = data.get("rows")
    if rows is not None:
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("input.rows must be a list of objects")
    return data


def cmd_build(a: argparse.Namespace) -> int:
    try:
        data = _load_input(a.input)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"pr-post: cannot read input: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render_body(data))
    return 0


def cmd_post(a: argparse.Namespace) -> int:
    try:
        data = _load_input(a.input)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"pr-post: cannot read input: {e}", file=sys.stderr)
        return 2

    pr_num = a.pr if a.pr is not None else data.get("pr_num")
    if pr_num is None or not re.fullmatch(r"[0-9]+", str(pr_num)):
        print(f"pr-post: --pr must be a bare PR number (got {pr_num!r})", file=sys.stderr)
        return 2
    expected = a.head_oid if a.head_oid is not None else data.get("head_oid", "")

    # Thread the RESOLVED target (after CLI overrides) back into `data` so the
    # rendered body's header labels the same PR/revision the gate checks and the
    # post targets — a `--pr`/`--head-oid` override must not leave the body
    # showing the stale JSON values.
    pr_num = str(pr_num)
    data["pr_num"] = pr_num
    data["head_oid"] = expected

    if shutil.which("gh") is None:
        print("SWARM_PR_POST_ERR=gh CLI not found")
        return 0

    # Build the body first so the stale-head gate is the LAST thing before the
    # post — the render is in-memory (no I/O), shrinking the gate→post window to
    # the `gh pr comment` call itself. A residual TOCTOU remains (a push can land
    # between this read and the comment; `gh pr comment` cannot pin to a head) —
    # accepted, since the confirm gate + this check catch the common case.
    body = render_body(data)

    status, msg = stale_gate(expected, _gh_live_head(pr_num))
    if status == "error":
        print(f"pr-post: {msg} (pass --head-oid or head_oid in the input)", file=sys.stderr)
        return 2
    if status == "stale":
        print(f"SWARM_PR_STALE={msg} — NOT posting")
        return 0
    if status == "unknown":
        # Fail CLOSED: the live head could not be read, so we cannot rule out
        # that the PR advanced past the reviewed revision. Publishing a possibly
        # stale review under the user's identity is worse than a retry — do not
        # post; the caller can re-run once `gh` is reachable.
        print(f"SWARM_PR_HEAD_UNVERIFIED={msg} — NOT posting (re-run to retry)")
        return 0

    # Write the body to a self-cleaning temp file (rebuilt above from the SAME
    # input, so what was shown at `build` time is exactly what is posted). An
    # OSError creating/writing the file is an OPERATIONAL failure (full/read-only
    # temp dir) — surface it as a token, not a traceback + exit 1, per the
    # module's exit-code convention.
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".md", prefix="swarm-pr-body.", delete=False
        )
    except OSError as e:
        print(f"SWARM_PR_POST_ERR=could not create temp body file: {e}")
        return 0
    try:
        tmp.write(body)
        tmp.close()
        ok, result = _gh_comment(pr_num, tmp.name)
    except OSError as e:
        print(f"SWARM_PR_POST_ERR=could not write temp body file: {e}")
        return 0
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if ok:
        print(f"SWARM_PR_POSTED={result}")
    else:
        print(f"SWARM_PR_POST_ERR={result}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="pr-post")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="render the comment body to stdout (for the confirm gate)")
    b.add_argument("--input", required=True, help="path to the findings/meta JSON, or - for stdin")
    b.set_defaults(func=cmd_build)

    o = sub.add_parser("post", help="stale-head gate, then gh pr comment --body-file")
    o.add_argument("--input", required=True, help="path to the findings/meta JSON, or - for stdin")
    o.add_argument("--pr", help="PR number (overrides input.pr_num)")
    o.add_argument("--head-oid", help="reviewed head SHA (overrides input.head_oid)")
    o.set_defaults(func=cmd_post)

    lt = sub.add_parser("loop-target", help="resolve + check the PR a --loop --pr run reports to")
    lt.add_argument("--pr", help="PR number (default: the current branch's PR)")
    lt.set_defaults(func=cmd_loop_target)

    lr = sub.add_parser("loop-tree", help="print the tree id of the working tree the loop left")
    lr.set_defaults(func=cmd_loop_tree)

    lb = sub.add_parser("loop-build", help="render the loop-report body to stdout (for the confirm gate)")
    lb.add_argument("--input", required=True, help="path to the loop-report JSON, or - for stdin")
    lb.set_defaults(func=cmd_loop_build)

    lp = sub.add_parser("loop-post", help="local + stale-head gates, then create or update the one loop report")
    lp.add_argument("--input", required=True, help="path to the loop-report JSON, or - for stdin")
    lp.set_defaults(func=cmd_loop_post)

    a = p.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
