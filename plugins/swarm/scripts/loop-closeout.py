#!/usr/bin/env python3
"""Deterministic loop helpers for `/swarm:review --loop`.

The `--loop` orchestration runs in-session (Claude applies fixes between
workflow rounds), but the two pieces that must NOT drift as prose are kept
here as a script:

  step  — apply the 4-part termination decision for a round, in a fixed order,
          so the loop always breaks for the same reason under the same inputs.
  box   — render the per-round OPEN-findings trajectory as a to-zero box, so
          convergence is visible at a glance and a legitimate rise (a fix that
          surfaced new findings) is shown, never hidden.

Both are stateless: the caller holds the per-round counts in-session (it has
them from each round's presentation) and passes them in. No state file, so
there is no working-directory footgun.

Usage:
  loop-closeout.py step --round R --cap N --findings F --agreed A --changed C
      -> prints `continue` or `terminate=<reason>` (one of the 4 reasons).

  loop-closeout.py box "15 7 4 4 5 4 3 1 1 0" --reason cap
      -> prints the close-out box + the termination reason line.

Reasons: 0-findings | nothing-agreed | no-change | cap
"""
import argparse
import sys

# Termination reason -> human label. English, like the rest of the skill output;
# the presenter translates around it if the conversation is in another language.
REASONS = {
    "0-findings": "0 findings — converged clean",
    "nothing-agreed": "nothing agreed — only disagreements (❌) left open",
    "no-change": "no files changed — fixed point reached",
    "cap": "cap reached",
}


def cmd_step(a: argparse.Namespace) -> int:
    """Decide whether the loop terminates after this round.

    Fixed evaluation order — the first matching condition wins:
      1. review returned zero findings          -> 0-findings   (clean)
      2. nothing was agreed (no ✅/🟨 this round) -> nothing-agreed
      3. no files changed AND nothing is pending  -> no-change   (fixed point)
      4. this was the last allowed round          -> cap
    Otherwise: continue.

    `no-change` is a real fixed point only when the round changed nothing AND no
    agreed finding is still awaiting a user decision. `--pending` (needs-decision
    findings the user hasn't answered) suppresses no-change so the loop doesn't
    declare convergence while a choice is still open — the divergence the caller
    would otherwise hit. Inputs are range-checked so a mis-parsed flag (e.g.
    `--cap 0`) fails loudly instead of silently collapsing the loop.
    """
    for name, val, lo in (
        ("--cap", a.cap, 1), ("--round", a.round, 0), ("--findings", a.findings, 0),
        ("--agreed", a.agreed, 0), ("--changed", a.changed, 0), ("--pending", a.pending, 0),
    ):
        if val < lo:
            print(f"loop-closeout: {name} must be >= {lo} (got {val})", file=sys.stderr)
            return 2

    if a.findings <= 0:
        print("terminate=0-findings")
    elif a.agreed <= 0:
        print("terminate=nothing-agreed")
    elif a.changed <= 0 and a.pending <= 0:
        print("terminate=no-change")
    elif a.round + 1 >= a.cap:
        print("terminate=cap")
    else:
        print("continue")
    return 0


def cmd_box(a: argparse.Namespace) -> int:
    try:
        counts = [int(x) for x in a.counts.split()]
    except ValueError:
        print("loop-closeout: counts must be space-separated integers", file=sys.stderr)
        return 2
    if not counts:
        print("loop-closeout: no counts given", file=sys.stderr)
        return 2

    labels = [f"R{i}" for i in range(len(counts))]
    cells = [str(c) for c in counts]
    # One column width for the whole box: widest label or count, min 2.
    colw = max(2, max(len(s) for s in labels + cells))

    def row(items: list) -> str:
        return "│" + "│".join(f" {s:<{colw}} " for s in items) + "│"

    def rule(left: str, mid: str, right: str) -> str:
        seg = "─" * (colw + 2)
        return left + mid.join(seg for _ in labels) + right

    non_increasing = all(b <= a_ for a_, b in zip(counts, counts[1:]))
    if counts[-1] == 0:
        trend = "monotone to zero" if non_increasing else "to zero (with an intermediate rise)"
    else:
        trend = f"ended with {counts[-1]} open"

    reason = REASONS.get(a.reason, a.reason)

    print(f"Loop close-out — {len(counts)} round(s), {trend}:")
    print(rule("┌", "┬", "┐"))
    print(row(labels))
    print(rule("├", "┼", "┤"))
    print(row(cells))
    print(rule("└", "┴", "┘"))
    print(f"End: {reason}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="loop-closeout")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("step", help="4-part termination decision for a round")
    s.add_argument("--round", type=int, required=True, help="0-indexed round just completed")
    s.add_argument("--cap", type=int, required=True, help="max rounds (--loop=N, default 10)")
    s.add_argument("--findings", type=int, required=True, help="findings this round")
    s.add_argument("--agreed", type=int, required=True, help="✅+🟨 findings this round")
    s.add_argument("--changed", type=int, required=True, help="files changed this round")
    s.add_argument("--pending", type=int, default=0,
                   help="agreed findings still awaiting a user decision (default 0)")
    s.set_defaults(func=cmd_step)

    b = sub.add_parser("box", help="render the OPEN-findings close-out box")
    b.add_argument("counts", help="space-separated per-round OPEN counts, R0 first")
    b.add_argument("--reason", required=True, help="termination reason key or text")
    b.set_defaults(func=cmd_box)

    a = p.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
