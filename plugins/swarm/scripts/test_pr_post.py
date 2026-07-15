#!/usr/bin/env python3
"""Tests for pr-post.py — run standalone (`python3 test_pr_post.py`) or via
scripts/check-structure.py's "plugin tests" check.

Guards the security-critical, drift-prone bits: the per-cell sanitizer (mention
/ pipe / newline / link / bare-URL / raw-HTML / entity-injection / backtick),
the code-span sanitizer, the stale-head gate's four outcomes (match / mismatch /
empty-live / empty-reviewed), and body assembly (table shape, empty case). The
sanitizer + gate are the exact places a prose implementation kept regressing.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "pr-post.py"

# pr-post.py has a hyphen -> load it by path rather than a plain import.
_spec = importlib.util.spec_from_file_location("pr_post", SCRIPT)
pr_post = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_post)

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


sp = pr_post.sanitize_prose
sc = pr_post.sanitize_code

# --- sanitize_prose: every linkify / table trigger is neutralized ---------- #
# entity-encoded (NOT backslash-escaped): a numeric entity carries no live
# metacharacter, so an attacker-supplied backslash cannot re-enable one.
check("mention: no literal @", "@" not in sp("ping @octocat and @org/team"))
check("mention: encoded", "&#64;" in sp("ping @octocat"))
check("pipe encoded", sp("a|b") == "a&#124;b" and "|" not in sp("a|b"))
check("newline flattened", "\n" not in sp("a\nb") and sp("a\nb") == "a b")
check("tab flattened", "\t" not in sp("a\tb"))
check("bare url de-linked", "://" not in sp("see http://evil.com now"))
check("bare url visible-ish", ":&#47;&#47;" in sp("http://evil.com"))
check("www de-linked", "www." not in sp("go www.evil.com"))
check("md link broken", sp("[x](http://e.com)").startswith("&#91;x&#93;"))
check("html angle brackets encoded", "<" not in sp("<img onerror=1>") and "&lt;" in sp("<img onerror=1>"))
check("backtick encoded", sp("a`b") == "a&#96;b" and "`" not in sp("a`b"))
check("emphasis encoded", all(c not in sp("**b** _i_ ~s~") for c in "*_~"))
# the backslash bypass the old escaping had: `\|` must NOT free a live pipe
check("backslash-pipe bypass closed", "|" not in sp("\\|") and "&#124;" in sp("\\|"))
check("backslash-bracket bypass closed", "[" not in sp("\\[x\\](u)") and "]" not in sp("\\[x\\](u)"))
# an attacker-supplied entity must render literally, not as a live @ / char:
check("entity injection defanged", sp("&#64;user").startswith("&amp;"))
check("plain text untouched", sp("simple finding text") == "simple finding text")
check("glyph passthrough", sp("🔴") == "🔴")
check("none -> empty", sp(None) == "")

# --- sanitize_code: inert code span; backtick + pipe STRIPPED (not escaped) -- #
check("code wraps", sc("file.sh:42") == "`file.sh:42`")
check("code strips backtick", "`" == sc("a`b`c")[0] and sc("a`b`c") == "`abc`")
check("code strips pipe", sc("a|b") == "`ab`" and "|" not in sc("a|b"))
# a malicious filename `x\|y` cannot free a live delimiter (pipe gone, no bypass)
check("code backslash-pipe stripped", "|" not in sc("x\\|y") and sc("x\\|y") == "`x\\y`")
check("code empty -> empty", sc("") == "" and sc(None) == "")
# everything else is inert INSIDE the span (no mention/link neutralization needed):
check("code leaves @ inert", sc("@x") == "`@x`")

# --- stale_gate: four outcomes --------------------------------------------- #
check("gate ok", pr_post.stale_gate("abc123", "abc123")[0] == "ok")
check("gate stale", pr_post.stale_gate("abc123", "def456")[0] == "stale")
check("gate unknown on empty live", pr_post.stale_gate("abc123", "")[0] == "unknown")
check("gate error on empty reviewed", pr_post.stale_gate("", "abc123")[0] == "error")
check("gate trims whitespace", pr_post.stale_gate(" abc ", "abc")[0] == "ok")

# --- render_body: table shape + empty case + sanitization ------------------ #
DATA = {
    "pr_num": 29,
    "title": "Fix @maintainer's [link](http://x.io)",
    "head_oid": "9fd980cabcdef",
    "rows": [
        {"num": "1", "sev": "🔴", "ort": "a.sh:5", "befund": "bad @thing",
         "quelle": "opus·grok ✓", "v": "✅", "notiz": "pipe|here"},
    ],
    "has_quelle": True,
    "balance": "Bilanz: 1 Finding",
    "notes": ["Redactions: 1 scrubbed"],
}
body = pr_post.render_body(DATA)
check("header pins short oid", "reviewed at 9fd980c" in body)
check("title in header", "**PR #29:**" in body)
check("title sanitized in header", "@" not in body.split("\n")[2] and "&#64;maintainer" in body)
check("7-col header", "| # | Sev | Location | Finding | Source | V | Note |" in body)
check("row befund sanitized in body", "bad &#64;thing" in body)
check("row notiz pipe encoded in body", "pipe&#124;here" in body)
check("ort code span in body", "`a.sh:5`" in body)
check("balance fenced", "```\nBilanz: 1 Finding\n```" in body)
check("note present", "Redactions: 1 scrubbed" in body)
check("footer present", "not a hosted bot" in body)


def header_row(rendered):
    """The findings-table header line — located by content, not a fixed index."""
    return next((ln for ln in rendered.split("\n") if ln.startswith("| # |")), "")


# single-source (no quelle) -> 6-col header
body6 = pr_post.render_body({**DATA, "has_quelle": False})
check("6-col header", "| # | Sev | Location | Finding | V | Note |" in body6)
check("6-col omits Source", "Source" not in header_row(body6) and header_row(body6))

# header injection: a JSON pr_num carrying newlines + markdown is sanitized, not
# injected raw outside the table (the gh-target regex only guards cmd_post).
inj = pr_post.render_body({**DATA, "pr_num": "29\n\n**evil**"})
check("pr_num injection defanged", "**evil**" not in inj and "\n\n**evil**" not in inj)
# _short is hex-only, so an embedded newline can't split the single-line header
check("short oid hex-only", pr_post._short("abc\n123def") == "abc123d")
check("short oid strips junk", pr_post._short("9fd980cXYZ!!") == "9fd980c")
# a non-string JSON head_oid/pr_num must not raise in re.sub / .strip
check("short oid coerces non-string", pr_post._short(123) == "123" and pr_post._short(None) == "")

# empty -> "No issues raised.", no table
empty_body = pr_post.render_body({"head_oid": "9fd980c", "rows": [], "balance": "Bilanz: 0"})
check("empty text", "No issues raised." in empty_body)
check("empty has no table row", "| # |" not in empty_body)

# --- build subcommand round-trip via subprocess ---------------------------- #
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(DATA, f)
    inp = f.name
p = subprocess.run(["python3", str(SCRIPT), "build", "--input", inp],
                   capture_output=True, text=True)
check("build exits 0", p.returncode == 0)
check("build stdout matches render_body", p.stdout == body)
Path(inp).unlink()

# --- misuse -> exit 2, no traceback ---------------------------------------- #
def build_rc(payload):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(payload)
        name = fh.name
    r = subprocess.run(["python3", str(SCRIPT), "build", "--input", name],
                       capture_output=True, text=True)
    Path(name).unlink()
    return r


r = build_rc("not json")
check("bad json -> exit 2", r.returncode == 2)
# non-list / non-dict rows must be caught at the seam, not crash mid-render
r = build_rc('{"rows": [null]}')
check("rows[null] -> exit 2", r.returncode == 2 and "Traceback" not in r.stderr)
r = build_rc('{"rows": "corrupt"}')
check("rows non-list -> exit 2", r.returncode == 2 and "Traceback" not in r.stderr)
# a non-string cell value is a valid dict row: render, don't crash on .strip()
r = build_rc('{"rows": [{"quelle": 1, "befund": 2}], "head_oid": 9}')
check("non-string cell renders", r.returncode == 0 and "Traceback" not in r.stderr)

if FAILS:
    print("pr-post tests FAILED:", file=sys.stderr)
    for f in FAILS:
        print("  -", f, file=sys.stderr)
    sys.exit(1)
print("pr-post: all tests passed")
