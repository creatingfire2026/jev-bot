"""The cycle: state -> JEV -> risk -> execution, once per market.

    for each market state:
        decision = jev.decide(state)      # BUY / SELL / HOLD / AVOID + prob
        gate     = risk.check(decision)   # may this execute?
        if gate is EXECUTE:
            book.execute(decision)        # paper
        advice   = grok.analyze(...)      # optional advisory only

Returns one record per market so the renderer can show every decision and
the reason the gate did or did not let it through. Optional Grok advice is
appended after the gate and never changes Decision, Gate, or execution.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from . import jev, risk
from .types import MarketState
from .execution.paper import Book


def run(states: Iterable[MarketState], book: Book, limits: risk.Limits,
        engine: str = "offline", analyst: Optional[str] = None) -> list:
    records = []
    use_grok = (analyst or "").lower() == "grok"
    for st in states:
        decision = jev.decide(st, engine=engine)
        gate = risk.check(decision, book.open_positions(), limits)
        if gate.verdict == "EXECUTE":
            book.execute(decision, st.price)
        advice = None
        if use_grok:
            from . import grok
            advice = grok.analyze(st, decision, gate)
        records.append((st, decision, gate, advice))
    return records
