"""reflex command line.

    reflex decisions        one cycle: state -> JEV -> risk, show the table
    reflex run              ... and execute the approved ones on paper
    reflex card SYMBOL      unpack a single decision

Offline decision engine by default (no key, reproducible). Pass
--engine jev with TYPESAFE_API_KEY set to use the real TypeSafe model.
Optional --analyst grok / --grok adds advisory Grok analysis after the gate.
"""

from __future__ import annotations

import argparse
import sys

from . import render, markets
from .loop import run
from .risk import Limits
from .execution.paper import Book


def _states(a):
    return markets.generate(n=a.n, seed=a.seed)


def _analyst(a) -> str | None:
    if getattr(a, "grok", False):
        return "grok"
    val = getattr(a, "analyst", None) or None
    if val in (None, "", "none"):
        return None
    return val


def cmd_decisions(a):
    states = _states(a)
    book = Book()
    analyst = _analyst(a)
    records = run(states, book, Limits(**_lim(a)), engine=a.engine, analyst=analyst)
    print(render.header(a.engine, len(states), analyst=analyst))
    print(render.decisions_table(records))


def cmd_run(a):
    states = _states(a)
    book = Book()
    analyst = _analyst(a)
    records = run(states, book, Limits(**_lim(a)), engine=a.engine, analyst=analyst)
    print(render.header(a.engine, len(states), analyst=analyst))
    print(render.decisions_table(records))
    print()
    print(render.book_summary(book))


def cmd_card(a):
    states = _states(a)
    book = Book()
    analyst = _analyst(a)
    records = run(states, book, Limits(**_lim(a)), engine=a.engine, analyst=analyst)
    match = [r for r in records if r[0].symbol.upper() == a.symbol.upper()]
    if not match:
        raise SystemExit(f"{a.symbol} not in this batch. try: reflex decisions")
    print(render.decision_card(*match[0]))


def _lim(a):
    out = {}
    if a.min_confidence is not None:
        out["min_confidence"] = a.min_confidence
    return out


def _add_shared(p):
    """Shared flags on parent and each subcommand so either order works."""
    p.add_argument("--engine", default="offline", choices=["offline", "jev"])
    p.add_argument("--analyst", default=None, choices=["none", "grok"],
                   help="optional advisory analyst (default: none)")
    p.add_argument("--grok", action="store_true",
                   help="shorthand for --analyst grok")
    p.add_argument("--n", type=int, default=8)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--min-confidence", type=float, default=None)


def build_parser():
    p = argparse.ArgumentParser(prog="jev-bot",
                                description="JEV-powered market decision bot")
    _add_shared(p)
    sub = p.add_subparsers(dest="cmd", required=True)
    
    # Re-add shared flags on each subparser so
    # `jev-bot decisions --grok` works (README style), not only
    # `jev-bot --grok decisions`.
    d = sub.add_parser("decisions", help="show the decision table")
    _add_shared(d)
    d.set_defaults(func=cmd_decisions)

    r = sub.add_parser("run", help="execute approved decisions on paper")
    _add_shared(r)
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("card", help="unpack one decision")
    _add_shared(c)
    c.add_argument("symbol")
    c.set_defaults(func=cmd_card)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    a.func(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
