#!/usr/bin/env python3
"""Tests for archive-task.sh's `autocommit` subcommand — run standalone
(`python3 test_archive_task.py`) or via scripts/check-structure.py's
"plugin tests" check.

Scope: the flag-routing logic behind /close step 10's opt-in
(`.claude/work-system-close-autocommit`) — the part that is actually
script-testable. The `archive`/`commit-push` subcommands are exercised
manually via /close; their design rationale lives in
`.claude/knowledge/features/task-archiving-on-close.md`.

The tests run against REAL git repos (not bare temp dirs) because the flag is
only honored when git-tracked and unmodified: presence alone must never
authorize skipping /close's push-approval gate.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "archive-task.sh"

FAILS = []
REL = ".claude/work-system-close-autocommit"


def check(name, cond):
    if not cond:
        FAILS.append(name)


def run(*args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True
    )


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def kv(out):
    """Parse key=value stdout lines into a dict."""
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def new_repo(root, name="repo"):
    """A real git repo with one commit, so HEAD exists for the clean-check."""
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "seed.txt")
    git(repo, "commit", "-qm", "seed")
    return repo


def commit_flag(repo, msg="add flag"):
    git(repo, "add", REL)
    git(repo, "commit", "-qm", msg)


with tempfile.TemporaryDirectory() as tmp:
    # .resolve(): git reports the CANONICAL root (on macOS /tmp -> /private/tmp),
    # so compare resolved paths or every `flag=` assertion fails on a symlinked
    # temp dir. The script echoing git's canonical path is correct behavior.
    root = Path(tmp).resolve()
    repo = new_repo(root)
    flag = repo / REL

    # --- get: no flag file -> disabled by default, no reason line ------------ #
    r = run("autocommit", "get", str(repo))
    check("no flag -> enabled=no", kv(r.stdout).get("enabled") == "no")
    check("no flag -> no reason line", "reason" not in kv(r.stdout))
    check("get exits 0", r.returncode == 0)

    # --- set: writes the file and reports where + that it must be committed -- #
    r = run("autocommit", "set", str(repo))
    check("set exits 0", r.returncode == 0)
    check("set creates the flag", flag.is_file())
    check("set writes 'yes'", flag.read_text().strip() == "yes")
    check("set echoes the resolved path", kv(r.stdout).get("flag") == str(flag))
    check("set reminds to commit", "commit" in kv(r.stdout).get("note", ""))

    # --- PROVENANCE: an uncommitted flag is NOT authorization ---------------- #
    # This is the core guard: a file a tool/agent merely wrote must not waive
    # /close's approval gate for a push to the default branch.
    r = run("autocommit", "get", str(repo))
    check("untracked flag -> enabled=no", kv(r.stdout).get("enabled") == "no")
    check("untracked flag -> reason=untracked", kv(r.stdout).get("reason") == "untracked")
    check("untracked flag ships a human note", "committed" in kv(r.stdout).get("note", ""))

    # committed -> now honored
    commit_flag(repo)
    r = run("autocommit", "get", str(repo))
    check("committed flag -> enabled=yes", kv(r.stdout).get("enabled") == "yes")
    check("enabled=yes carries no reason", "reason" not in kv(r.stdout))

    # locally modified -> falls back to asking (deliberate local disabling works)
    flag.write_text("no\n")
    r = run("autocommit", "get", str(repo))
    check("locally disabled -> enabled=no", kv(r.stdout).get("enabled") == "no")
    check("locally disabled -> reason=locally-disabled",
          kv(r.stdout).get("reason") == "locally-disabled")
    flag.write_text("yes\n")  # back to the committed content
    check("restored -> enabled=yes",
          kv(run("autocommit", "get", str(repo)).stdout).get("enabled") == "yes")

    # a local edit cannot silently ENABLE a committed-off flag either
    flag.write_text("no\n")
    commit_flag(repo, "disable flag")
    flag.write_text("yes\n")
    r = run("autocommit", "get", str(repo))
    check("local enable of committed-off -> enabled=no", kv(r.stdout).get("enabled") == "no")
    check("local enable -> reason=modified", kv(r.stdout).get("reason") == "modified")

    # REGRESSION: the index bits that make a dirty file look clean must not
    # enable the waiver. A diff-based guard reports "unmodified" here; reading
    # the value from HEAD is what actually holds the line.
    for bit in ("--assume-unchanged", "--skip-worktree"):
        git(repo, "update-index", bit, REL)          # committed content is "no"
        flag.write_text("yes\n")
        r = run("autocommit", "get", str(repo))
        check(f"{bit} cannot enable the waiver", kv(r.stdout).get("enabled") == "no")
        git(repo, "update-index", bit.replace("--", "--no-", 1), REL)
    git(repo, "checkout", "--", REL)
    flag.write_text("yes\n")
    commit_flag(repo, "re-enable")

    # ...and the same bits must not hide a deliberate local DISABLE either. A
    # `git diff --quiet` dirty-check reports "clean" here; comparing blob hashes
    # is what keeps the escape hatch working.
    for bit in ("--assume-unchanged", "--skip-worktree"):
        git(repo, "update-index", bit, REL)          # committed content is "yes"
        flag.write_text("no\n")
        r = run("autocommit", "get", str(repo))
        check(f"{bit} cannot hide a local disable", kv(r.stdout).get("enabled") == "no")
        check(f"{bit} local disable -> reason=locally-disabled",
              kv(r.stdout).get("reason") == "locally-disabled")
        git(repo, "update-index", bit.replace("--", "--no-", 1), REL)
        git(repo, "checkout", "--", REL)

    # --- content variants (committed each time; case-insensitive) ------------ #
    for content, expected, reason in [
        ("yes\n", "yes", None),
        ("true\n", "yes", None),
        ("YES\n", "yes", None),        # case-insensitive: hand-edited by design
        ("True\n", "yes", None),
        ("1\n", "yes", None),
        ("on\n", "yes", None),
        (" yes \n", "yes", None),      # whitespace-trimmed
        ("no\n", "no", None),          # recognized off -> no confusing reason
        ("false\n", "no", None),
        ("maybe\n", "no", "unrecognized-content"),
        ("autocommit=yes\n", "no", "unrecognized-content"),   # key=value form is NOT accepted
        ("", "no", "unrecognized-content"),
        # TRIM, not "delete all whitespace": `tr -d '[:space:]'` would collapse
        # these into the accepted token `yes` and waive the gate on content
        # matching no documented value.
        ("y e s\n", "no", "unrecognized-content"),
        ("ye\ns\n", "no", "unrecognized-content"),
        ("yes\nno\n", "no", "unrecognized-content"),          # multi-line is never accepted
    ]:
        flag.write_text(content)
        commit_flag(repo, f"content {content!r}")
        r = run("autocommit", "get", str(repo))
        got = kv(r.stdout)
        check(f"content {content!r} -> enabled={expected}", got.get("enabled") == expected)
        check(f"content {content!r} -> reason={reason}", got.get("reason") == reason)
        # `get` is called mid-/close and its stderr is surfaced to the user, so a
        # stray shell error (e.g. from a `grep -c ''` fallback yielding "0\n0")
        # would read as the close itself breaking.
        check(f"content {content!r} -> clean stderr", r.stderr == "")

    # --- symlink: never honored, never written through ----------------------- #
    victim = root / "victim.txt"
    victim.write_text("IMPORTANT\n")
    flag.unlink()
    flag.symlink_to(victim)
    r = run("autocommit", "get", str(repo))
    check("symlinked flag -> enabled=no", kv(r.stdout).get("enabled") == "no")
    check("symlinked flag -> reason=symlink", kv(r.stdout).get("reason") == "symlink")
    r = run("autocommit", "set", str(repo))
    check("set on symlink -> exit 2", r.returncode == 2)
    check("set on symlink explains why", "symlink" in r.stderr)
    check("set on symlink leaves victim intact", victim.read_text() == "IMPORTANT\n")
    flag.unlink()
    git(repo, "checkout", "--", REL)

    # REGRESSION: a symlinked `.claude` PARENT escapes the repo even though the
    # leaf doesn't exist — `mkdir -p` succeeds through the link and the write
    # lands outside while the reported path still looks in-repo.
    outside = root / "outside"
    outside.mkdir()
    (outside / "work-system-close-autocommit").write_text("PRECIOUS\n")
    prepo = new_repo(root, "prepo")
    (prepo / ".claude").symlink_to(outside)
    r = run("autocommit", "set", str(prepo))
    check("set through symlinked .claude -> exit 2", r.returncode == 2)
    check("set through symlinked .claude explains why", "symlink" in r.stderr)
    check("set through symlinked .claude leaves victim intact",
          (outside / "work-system-close-autocommit").read_text() == "PRECIOUS\n")
    r = run("autocommit", "unset", str(prepo))
    check("unset through symlinked .claude -> exit 2", r.returncode == 2)
    check("unset through symlinked .claude deletes nothing",
          (outside / "work-system-close-autocommit").is_file())
    check("get through symlinked .claude -> enabled=no",
          kv(run("autocommit", "get", str(prepo)).stdout).get("enabled") == "no")

    # REGRESSION: the temp path must not be PID-predictable. A pre-planted
    # `<flag>.tmp.<pid>` symlink used to be followed by the redirect (clobbering
    # the target) and then installed AS the flag by `mv`.
    #
    # A behavioral test cannot cover this honestly: the vulnerable name embeds
    # the script's own `$$`, which the test cannot know or control, so planting a
    # guessed PID range proves nothing (an earlier version of this test planted
    # PIDs 2-399 while real subprocess PIDs run in the thousands — reverting the
    # fix left every assertion passing). So assert the PROPERTY at the source: the
    # writer must not build a temp path out of `$$`. Crude, but unlike the
    # behavioral version it actually fails when the bug comes back.
    code = "\n".join(
        ln for ln in SCRIPT.read_text().splitlines()
        if not ln.lstrip().startswith("#")      # comments explain the bug by name
    )
    check("no PID-derived temp path in the writer", ".tmp.$$" not in code)
    check("writer uses mktemp", "mktemp " in code)
    # Behavioral half: the flag ends up a real file and nothing outside is touched.
    trepo = new_repo(root, "trepo")
    tvictim = root / "tempvictim.txt"
    tvictim.write_text("PRECIOUS\n")
    r = run("autocommit", "set", str(trepo))
    check("set succeeds", r.returncode == 0)
    check("unrelated file untouched", tvictim.read_text() == "PRECIOUS\n")
    check("flag is a regular file, not a symlink",
          (trepo / REL).is_file() and not (trepo / REL).is_symlink())
    check("no temp files left behind",
          not list((trepo / ".claude").glob(".work-system-close-autocommit.*")))

    # --- linked worktree: set/get address the MAIN checkout ------------------ #
    # A flag written into a disposable worktree would never be read (and /close
    # deletes the worktree with it), so all three ops resolve to the main repo
    # root. Uses a FRESH repo whose flag was never committed — otherwise
    # `git worktree add` checks the tracked flag out into the worktree and a
    # "no worktree-local flag" assertion would see git's copy, not a stray write.
    wrepo = new_repo(root, "wrepo")
    wflag = wrepo / REL
    wt = root / "wt"
    git(wrepo, "worktree", "add", "-q", "-b", "task/x", str(wt))
    if wt.is_dir():
        r = run("autocommit", "set", str(wt))
        check("set from worktree targets main repo", kv(r.stdout).get("flag") == str(wflag))
        check("set from worktree wrote the main repo flag", wflag.is_file())
        check("set from worktree writes no worktree-local flag", not (wt / REL).exists())
        # committed in the main repo -> a get from the worktree honors it
        commit_flag(wrepo, "enable in main repo")
        check("get from worktree reads main repo flag",
              kv(run("autocommit", "get", str(wt)).stdout).get("enabled") == "yes")
    else:
        FAILS.append("could not create linked worktree for the resolution test")

    # --- unset ---------------------------------------------------------------- #
    r = run("autocommit", "unset", str(repo))
    check("unset exits 0", r.returncode == 0)
    check("unset removes the flag", not flag.exists())
    check("unset echoes the path", kv(r.stdout).get("flag") == str(flag))
    # removed but still tracked in HEAD -> the deletion is a modification
    check("after unset -> enabled=no",
          kv(run("autocommit", "get", str(repo)).stdout).get("enabled") == "no")

    # --- REGRESSION: relative invocation must not resolve the sibling helper --- #
    # `$(dirname "$0")` expanded inside `( cd "$repo" && … )` resolves against the
    # TARGET repo, so a script planted there would be run (and its stdout trusted
    # as the repo root). Invoke through a relative path, from the scripts' own
    # directory, with a decoy helper in the target repo.
    drepo = new_repo(root, "drepo")
    (drepo / "scripts").mkdir()
    marker = root / "PWNED"
    (drepo / "scripts" / "main-repo-path.sh").write_text(
        f"#!/bin/sh\ntouch {marker}\necho {drepo}\n"
    )
    (drepo / "scripts" / "main-repo-path.sh").chmod(0o755)
    rel_run = subprocess.run(
        ["bash", SCRIPT.name, "autocommit", "get", str(drepo)],
        cwd=str(HERE), capture_output=True, text=True,
    )
    check("relative invocation does not run the target repo's script", not marker.exists())
    check("relative invocation still answers", "enabled=" in rel_run.stdout)

    # --- REGRESSION: gitignored .claude/ must be called out -------------------- #
    # Otherwise `git add` refuses the path and `get` says "untracked" forever
    # while `set` keeps reporting success.
    irepo = new_repo(root, "irepo")
    (irepo / ".gitignore").write_text(".claude/\n")
    git(irepo, "add", ".gitignore")
    git(irepo, "commit", "-qm", "ignore .claude")
    r = run("autocommit", "set", str(irepo))
    check("set in gitignored repo still exits 0", r.returncode == 0)
    check("set warns the path is gitignored", "gitignored" in kv(r.stdout).get("note", ""))

    # --- REGRESSION: unset reminds you to commit the deletion ------------------ #
    r = run("autocommit", "unset", str(repo))
    check("unset of a committed flag reminds to commit the deletion",
          "commit" in kv(r.stdout).get("note", ""))
    git(repo, "checkout", "--", REL)
    # ...but not when there was nothing committed to begin with
    nrepo = new_repo(root, "nrepo")
    r = run("autocommit", "unset", str(nrepo))
    check("unset with no committed flag stays quiet", "note" not in kv(r.stdout))

    # --- REGRESSION: unsupported layout is refused, never written into .git ---- #
    # `git worktree list` reports the GIT DIR under --separate-git-dir, so an
    # unchecked resolver would create .claude/ inside .git — uncommittable.
    sgd_wt = root / "sgd"
    sgd_git = root / "sgd.git"
    subprocess.run(["git", "init", "-q", f"--separate-git-dir={sgd_git}", str(sgd_wt)],
                   capture_output=True, text=True)
    if sgd_wt.is_dir():
        git(sgd_wt, "config", "user.email", "test@example.com")
        git(sgd_wt, "config", "user.name", "Test")
        (sgd_wt / "seed.txt").write_text("seed\n")
        git(sgd_wt, "add", "seed.txt")
        git(sgd_wt, "commit", "-qm", "seed")
        # Assert the INVARIANT ("set and get agree"), not today's git quirk: the
        # refusal only happens because `git worktree list` currently reports the
        # git dir for such repos. A git release that reports the work tree
        # correctly would make `set` succeed — an improvement that must not turn
        # CI red. Either outcome is acceptable; a silent wrong-success is not.
        r = run("autocommit", "set", str(sgd_wt))
        check("separate-git-dir never writes into the git dir",
              not (sgd_git / ".claude").exists())
        if r.returncode == 0:
            git(sgd_wt, "add", "-f", REL)
            git(sgd_wt, "commit", "-qm", "flag")
            check("separate-git-dir: if set succeeds, get honors it",
                  kv(run("autocommit", "get", str(sgd_wt)).stdout).get("enabled") == "yes")
        else:
            check("separate-git-dir refusal is the documented exit 2", r.returncode == 2)
            check("separate-git-dir refusal names the layout", "layout" in r.stderr)
            # get must explain it too, not answer a bare enabled=no that looks
            # like "simply not opted in".
            g = kv(run("autocommit", "get", str(sgd_wt)).stdout)
            check("separate-git-dir get explains itself", g.get("reason") == "unsupported-layout")
            check("separate-git-dir get ships a note", "unavailable" in g.get("note", ""))

    # --- REGRESSION: the verdict follows <main-branch>, not the current HEAD --- #
    # The flag authorizes a commit onto the default branch, and commit-push
    # refuses to commit anywhere else — so a flag living only on some feature
    # branch must NOT announce an auto-commit.
    brepo = new_repo(root, "brepo")          # new_repo's first branch is the default
    default_branch = git(brepo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git(brepo, "checkout", "-q", "-b", "feature")
    (brepo / ".claude").mkdir()
    (brepo / REL).write_text("yes\n")
    git(brepo, "add", REL)
    git(brepo, "commit", "-qm", "flag on feature only")
    # Checked out ON the feature branch: commit_push would refuse anyway, so the
    # unattended path must not be announced.
    g = kv(run("autocommit", "get", str(brepo), default_branch).stdout)
    check("flag only on a feature branch -> not honored", g.get("enabled") == "no")
    check("...because the checkout is not on the authorization branch",
          g.get("reason") == "checkout-not-on-main")
    # Back on the default branch the flag file isn't even in the working tree
    # (it lives only on `feature`), so this is a plain "never opted in" answer.
    git(brepo, "checkout", "-q", default_branch)
    g = kv(run("autocommit", "get", str(brepo), default_branch).stdout)
    check("flag absent on the default branch -> bare enabled=no", g.get("enabled") == "no")
    check("...with no reason line", "reason" not in g)
    # committed on the default branch -> honored
    git(brepo, "checkout", "-q", default_branch)
    (brepo / ".claude").mkdir(exist_ok=True)   # tracked only on `feature` until now
    (brepo / REL).write_text("yes\n")
    git(brepo, "add", REL)
    git(brepo, "commit", "-qm", "flag on default")
    check("flag on the default branch -> honored",
          kv(run("autocommit", "get", str(brepo), default_branch).stdout).get("enabled") == "yes")

    # --- REGRESSION: a TAG must not shadow the authorization branch ----------- #
    # git resolves refs/tags/<name> BEFORE refs/heads/<name>, and tags are
    # auto-followed on fetch — so a bare refname let a tag supply an
    # authorization value the branch never carried.
    trepo2 = new_repo(root, "trepo2")
    tbranch = git(trepo2, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    (trepo2 / ".claude").mkdir()
    (trepo2 / REL).write_text("yes\n")
    git(trepo2, "add", REL)
    git(trepo2, "commit", "-qm", "flag on")
    git(trepo2, "tag", "flagged-on")
    (trepo2 / REL).write_text("no\n")
    git(trepo2, "add", REL)
    git(trepo2, "commit", "-qm", "flag off")
    # a tag named exactly like the branch, pointing at the "yes" commit
    git(trepo2, "tag", "-f", tbranch, "flagged-on")
    check("branch really says off",
          git(trepo2, "show", f"refs/heads/{tbranch}:{REL}").stdout.strip() == "no")
    check("a same-named tag really shadows the branch for a bare refname",
          git(trepo2, "show", f"{tbranch}:{REL}").stdout.strip() == "yes")
    check("tag cannot supply the authorization",
          kv(run("autocommit", "get", str(trepo2), tbranch).stdout).get("enabled") == "no")

    # --- REGRESSION: an unresolvable authorization branch fails CLOSED -------- #
    # Falling back to HEAD here would reinstate the branch-dependent verdict in
    # the failing direction.
    g = kv(run("autocommit", "get", str(brepo), "no-such-branch").stdout)
    check("unresolvable branch -> enabled=no", g.get("enabled") == "no")
    check("unresolvable branch -> reason=unresolvable-branch",
          g.get("reason") == "unresolvable-branch")

    # --- REGRESSION: a deliberate local disable gets neutral wording ---------- #
    # "commit or revert it to make it effective" would tell the user to undo the
    # very thing they just did, on every close.
    (brepo / REL).write_text("no\n")
    g = kv(run("autocommit", "get", str(brepo), default_branch).stdout)
    check("local disable -> reason=locally-disabled", g.get("reason") == "locally-disabled")
    check("local disable note is not corrective", "revert" not in g.get("note", ""))
    (brepo / REL).write_text("garbage\n")
    g = kv(run("autocommit", "get", str(brepo), default_branch).stdout)
    check("local garbage -> reason=modified", g.get("reason") == "modified")

    # --- REGRESSION: a symlinked .claude in a repo that never opted in -------- #
    # Must answer a BARE enabled=no: callers relay any note verbatim, so warning
    # about an opt-in that was never configured fires on every close.
    srepo = new_repo(root, "srepo")
    empty_target = root / "empty-claude"
    empty_target.mkdir()
    (srepo / ".claude").symlink_to(empty_target)
    g = kv(run("autocommit", "get", str(srepo)).stdout)
    check("symlinked .claude, never opted in -> bare enabled=no", g.get("enabled") == "no")
    check("symlinked .claude, never opted in -> no reason", "reason" not in g)

    # --- REGRESSION: path types other than symlink are refused too ------------ #
    frepo = new_repo(root, "frepo")
    (frepo / ".claude").write_text("i am a file\n")
    r = run("autocommit", "set", str(frepo))
    check("regular file at .claude -> exit 2", r.returncode == 2)
    check("regular file at .claude -> refusal message", "not a directory" in r.stderr)
    drepo2 = new_repo(root, "drepo2")
    (drepo2 / ".claude").mkdir()
    (drepo2 / REL).mkdir()
    r = run("autocommit", "set", str(drepo2))
    check("directory at flag path -> exit 2", r.returncode == 2)
    check("directory at flag path -> refusal message", "not a regular file" in r.stderr)

    # --- non-git paths -------------------------------------------------------- #
    plain = root / "plain"
    plain.mkdir()
    check("non-git get -> enabled=no",
          kv(run("autocommit", "get", str(plain)).stdout).get("enabled") == "no")
    r = run("autocommit", "set", str(plain))
    check("non-git set -> exit 2", r.returncode == 2)
    check("non-git set explains why", "not a git repository" in r.stderr)
    check("non-git set creates nothing", not (plain / ".claude").exists())
    r = run("autocommit", "set", str(root / "no" / "such" / "path"))
    check("bogus path set -> exit 2", r.returncode == 2)
    check("bogus path creates no stray tree", not (root / "no").exists())
    check("non-git unset -> exit 2", run("autocommit", "unset", str(plain)).returncode == 2)

    # --- usage errors --------------------------------------------------------- #
    check("get missing repo -> exit 2", run("autocommit", "get").returncode == 2)
    check("set missing repo -> exit 2", run("autocommit", "set").returncode == 2)
    check("unknown op -> exit 2", run("autocommit", "bogus", str(repo)).returncode == 2)
    check("bare autocommit -> exit 2", run("autocommit").returncode == 2)


if FAILS:
    print("FAIL:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("archive-task.sh autocommit: all tests passed")
