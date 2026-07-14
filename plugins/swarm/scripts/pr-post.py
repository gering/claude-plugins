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
    "rows": [ {num,sev,ort,befund,quelle,v,notiz}, ... ],  # gated findings
    "has_quelle": true,        # optional; defaults to: any row has a quelle
    "balance": "Bilanz: …\nAgents: …\nLenses: …",
    "notes": ["Redactions: …", "Backend error: …"],        # optional extra lines
    "empty": false             # true -> "No issues raised." instead of a table
  }

Every finding cell (ort/befund/quelle/notiz) and the title are attacker-
influenced. `sev`/`v`/`num` are model glyphs; they pass through the same
sanitizer unharmed (glyphs contain none of the neutralized characters).

Exit-code convention (matches loop-closeout.py + the skill's step-1 block):
  operational outcomes (stale head, gh missing, post failure) -> token + exit 0,
  the caller branches on the token;
  programmer misuse (bad JSON, missing required field) -> stderr + exit 2.
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
      - ``<`` ``>`` -> entities: neutralizes ALL raw HTML (no tag can form);
      - ``[`` ``]`` -> ``\\[`` ``\\]``: breaks ``[text](url)`` / ``![](…)`` link
        + image syntax;
      - ``|`` -> ``\\|``: keeps the cell from splitting the table column;
      - backtick -> ``\\``` ``: a literal backtick, no code span opens;
      - ``@`` -> ``&#64;``: no ``@mention`` can autolink (no literal ``@`` left);
      - ``://`` and ``www.`` -> entity-broken: GFM autolinks ``scheme://host``
        and ``www.host``; breaking the trigger de-links a bare URL to inert text.
    """
    s = "" if text is None else str(text)
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)   # CR/LF/TAB + other control chars
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("&", "&amp;")               # must precede our own entities
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("[", "\\[").replace("]", "\\]")
    s = s.replace("|", "\\|")
    s = s.replace("`", "\\`")
    s = s.replace("@", "&#64;")
    s = s.replace("://", ":&#47;&#47;")
    s = re.sub(r"(?i)\bwww\.", "www&#46;", s)
    return s


def sanitize_code(text) -> str:
    """Render an untrusted value (e.g. ``file:line``) as an inert code span.

    A code span never mentions, autolinks, or renders HTML, so the ONLY things
    to handle are the two characters that would break it or the table: a literal
    backtick (can't nest in a span) is stripped, and ``|`` is escaped (GFM needs
    ``\\|`` even inside code within a table cell). Everything else is inert. An
    empty value yields an empty string, not an empty ``````` span.
    """
    s = "" if text is None else str(text)
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("`", "").replace("|", "\\|")
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
                      indeterminate; caller may proceed with a warning (parity
                      with the prior prose, which only blocked on a definite
                      mismatch);
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
def _short(oid: str) -> str:
    return (oid or "").strip()[:7]


def render_body(data: dict) -> str:
    """Assemble the full comment body (GitHub-flavored Markdown) from `data`.

    Deterministic: same input -> byte-identical output, which is why `build`
    (what the user confirms) and `post` (what is sent) can each call this from
    the same JSON and be guaranteed identical.
    """
    rows = data.get("rows") or []
    has_quelle = data.get("has_quelle")
    if has_quelle is None:
        has_quelle = any((r.get("quelle") or "").strip() for r in rows)

    head_short = _short(data.get("head_oid", ""))
    parts = [f"## \U0001f41d Swarm review (local ensemble) · reviewed at {head_short}", ""]

    # PR # + title identify the reviewed PR in the posted comment. The title is
    # UNTRUSTED contributor input, so it goes through the SAME per-cell sanitizer
    # before it lands in this header (resolves the title-escaping finding).
    pr_num = data.get("pr_num")
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
            cells = [
                sanitize_prose(r.get("num", "")),
                sanitize_prose(r.get("sev", "")),
                sanitize_code(r.get("ort", "")),
                sanitize_prose(r.get("befund", "")),
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
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
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

    if shutil.which("gh") is None:
        print("SWARM_PR_POST_ERR=gh CLI not found")
        return 0

    # Stale-head gate FIRST — re-read the live head and act on it. A mismatch
    # stops here (no post); an unreadable head is indeterminate and proceeds
    # with a warning (the caller already confirmed the reviewed revision).
    status, msg = stale_gate(expected, _gh_live_head(pr_num))
    if status == "error":
        print(f"pr-post: {msg} (pass --head-oid or head_oid in the input)", file=sys.stderr)
        return 2
    if status == "stale":
        print(f"SWARM_PR_STALE={msg} — NOT posting")
        return 0
    if status == "unknown":
        print(f"SWARM_PR_WARN={msg}; posting the reviewed revision {_short(expected)}")

    # Rebuild the body from the SAME input into a self-cleaning temp file, so
    # what was shown at `build` time is exactly what is posted.
    body = render_body(data)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".md", prefix="swarm-pr-body.", delete=False
    )
    try:
        tmp.write(body)
        tmp.close()
        ok, result = _gh_comment(pr_num, tmp.name)
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
