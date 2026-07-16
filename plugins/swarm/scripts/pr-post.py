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
    "notes": ["Redactions: …", "Backend error: …"],        # optional extra lines
    "empty": false             # true -> "No issues raised." instead of a table
  }

Every finding cell (ort/befund/quelle/notiz) and the title are attacker-
influenced. `sev`/`v`/`num` are model glyphs; they pass through the same
sanitizer unharmed (glyphs contain none of the neutralized characters).

Row `kind`/`lens` are optional pass-throughs from the workflow's findings:
`kind: "design"` rows render AFTER all defect rows (design suggestions must not
dilute the defect ranking) and their finding cell gets a visible `[lens]`
prefix — ordering + prefixing are enforced HERE, deterministically, so the
caller passes rows through verbatim and never hand-orders or hand-prefixes
(the prose version of that rule is exactly the drift class this script exists
to close). Any other/missing `kind` is a defect — the safe bucket, matching
the workflow's lensKind derivation.

Exit-code convention (matches loop-closeout.py + the skill's step-1 block):
  operational outcomes (stale head, unverifiable head, gh missing, post failure)
  -> token + exit 0, the caller branches on the token;
  programmer misuse (bad JSON, non-list rows, missing field) -> stderr + exit 2.

`post` fails CLOSED: a mismatched head (SWARM_PR_STALE) OR an unreadable live
head (SWARM_PR_HEAD_UNVERIFIED) both stop before posting — publishing a possibly
stale review under the user's identity is worse than a retry.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

GH_TIMEOUT = 60  # seconds for any single gh call

FOOTER = (
    "<sub>Local mixture-of-agents review (Claude lenses + codex + grok) run "
    "from the author's machine — not a hosted bot. Verdicts "
    "(✅/\U0001f7e8/❌) are the runner's own assessment.</sub>"
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


# DRIFT WARNING: hand-mirrors LENS_CLUSTERS.design in workflows/swarm-review.js
# (edit together; test_lens_sync.py asserts the two sets stay equal).
DESIGN_LENSES = {"reuse", "simplification", "efficiency", "altitude"}


def _row_kind(r: dict) -> str:
    """Row kind. 'design' when explicitly tagged; an explicit 'defect' always
    wins (the workflow's kind vote can defect a mixed cluster whose dominant
    lens is a design lens). With kind missing/junk — the model-mediated step-5
    handoff can drop it — the LENS is the backup signal: a design lens implies
    design, anything else is a defect (the safe bucket)."""
    kind = str(r.get("kind") or "").strip().lower()
    if kind == "design":
        return "design"
    if kind != "defect" and str(r.get("lens") or "").strip().lower() in DESIGN_LENSES:
        return "design"
    return "defect"


def render_body(data: dict) -> str:
    """Assemble the full comment body (GitHub-flavored Markdown) from `data`.

    Deterministic: same input -> byte-identical output, which is why `build`
    (what the user confirms) and `post` (what is sent) can each call this from
    the same JSON and be guaranteed identical.
    """
    rows = data.get("rows") or []
    # Defects first, design after — a stable partition, so the caller's
    # severity order survives within each kind. Enforced here (not by the
    # calling prose) so a posted comment can never interleave suggestions
    # into the defect ranking. Single pass: one _row_kind call per row.
    defect_rows, design_rows = [], []
    for r in rows:
        (design_rows if _row_kind(r) == "design" else defect_rows).append(r)
    rows = defect_rows + design_rows
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
            # Design rows carry their lens as a visible "[lens] " prefix on the
            # finding cell (one table, kind still readable). Prefix BEFORE
            # sanitizing: the brackets come out entity-encoded like any other
            # cell content and render back as literal [lens] — an untrusted
            # lens value gets the full sanitizer, same as the finding text.
            befund = r.get("befund")
            if _row_kind(r) == "design":
                lens = str(r.get("lens") or "").strip() or "design"
                befund = f"[{lens}] {'' if befund is None else befund}".rstrip()
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

    parts += ["", FOOTER]
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# gh wrappers — thin, so the testable logic stays in the pure functions above.
# --------------------------------------------------------------------------- #
def _gh_live_head(pr_num: str):
    """Return the live head SHA of the PR, or "" if it can't be read."""
    try:
        p = subprocess.run(
            ["gh", "pr", "view", str(pr_num), "--json", "headRefOid", "--jq", ".headRefOid"],
            capture_output=True, text=True, timeout=GH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _gh_comment(pr_num: str, body_file: str):
    """Post the comment. Returns (ok, url_or_error)."""
    try:
        p = subprocess.run(
            ["gh", "pr", "comment", str(pr_num), "--body-file", body_file],
            capture_output=True, text=True, timeout=GH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if p.returncode == 0:
        return True, p.stdout.strip()
    return False, (p.stderr.strip() or p.stdout.strip() or "gh pr comment failed")


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

    a = p.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
