"""reflex checks. No network, no dependencies. Run: python tests.py

Each check is one fact about the decision loop. The real JEV engine is not
exercised here (it needs access and a key); the offline engine and the gate
are, because those are what run by default. Grok analyst checks use mocked
HTTP only — no real network and no real keys.
"""

import io
import json
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from jev_bot import jev, markets, grok
from jev_bot.types import MarketState, Decision, ACTIONS
from jev_bot.risk import Limits, Gate, check
from jev_bot.execution.paper import Book
from jev_bot.loop import run

PASS = FAIL = 0


def ok(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok    " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name)


def st(**kw):
    base = dict(symbol="X", asset_class="stock", price=100.0, change_24h=0.0,
                volume_delta=0.0, momentum=0.0, news=0.0, regime="neutral")
    base.update(kw)
    return MarketState(**base)


# --- decision engine (offline) ---------------------------------------------
d1 = jev.decide(st(momentum=0.6, change_24h=0.05, regime="bullish"))
ok("a strong bullish state decides BUY", d1.action == "BUY")

d2 = jev.decide(st(momentum=-0.6, change_24h=-0.05, regime="bearish"))
ok("a strong bearish stock decides SELL", d2.action == "SELL")

d3 = jev.decide(st(momentum=-0.6, change_24h=-0.05, asset_class="meme", regime="bearish"))
ok("a strong bearish meme decides AVOID, not SELL", d3.action == "AVOID")

ok("a flat state decides HOLD", jev.decide(st()).action == "HOLD")

ok("action is always one of the four", jev.decide(st()).action in ACTIONS)

ok("probability and confidence stay in range",
   0.4 <= d1.confidence <= 0.97 and 0.5 <= d1.probability <= 0.97)

ok("every offline decision is labelled offline", d1.source == "offline")

ok("the same state gives the same decision",
   jev.decide(st(momentum=0.4)).action == jev.decide(st(momentum=0.4)).action
   and jev.decide(st(momentum=0.4)).probability == jev.decide(st(momentum=0.4)).probability)

# --- the gate --------------------------------------------------------------
strong = Decision("X", "BUY", 0.80, 0.90, "offline")
ok("a clean decision executes", check(strong, 0, Limits()).verdict == "EXECUTE")

ok("HOLD never executes",
   check(Decision("X", "HOLD", 0.9, 0.9), 0, Limits()).verdict == "SKIP")

ok("AVOID never executes",
   check(Decision("X", "AVOID", 0.9, 0.9), 0, Limits()).verdict == "SKIP")

ok("low confidence is refused",
   check(Decision("X", "BUY", 0.9, 0.5), 0, Limits()).verdict == "SKIP")

ok("the position cap is enforced",
   check(strong, 5, Limits()).verdict == "SKIP")

# --- paper execution -------------------------------------------------------
b = Book()
b.execute(strong, 100.0)
ok("executing adds a paper fill", b.open_positions() == 1)

b.mark({"X": 110.0})
ok("a BUY profits when price rises", b.open_pnl() > 0)

b2 = Book()
b2.execute(Decision("X", "SELL", 0.8, 0.9), 100.0)
b2.mark({"X": 90.0})
ok("a SELL profits when price falls", b2.open_pnl() > 0)

# --- market source ---------------------------------------------------------
ok("the generator is deterministic",
   [m.symbol for m in markets.generate(6, 3)] == [m.symbol for m in markets.generate(6, 3)])


# --- optional Grok analyst (mocked HTTP) -----------------------------------

def _advice_json(consistency="agree", confidence=0.7):
    return json.dumps({
        "summary": "Cautious view of supplied state only.",
        "risks": ["regime uncertainty"],
        "catalysts": ["momentum alignment"],
        "consistency": consistency,
        "confidence": confidence,
    })


def _completions_body(content: str) -> bytes:
    return json.dumps({
        "choices": [{"message": {"content": content}}],
    }).encode()


@contextmanager
def _fake_key(value="test-key-not-real"):
    old = os.environ.get("XAI_API_KEY")
    os.environ["XAI_API_KEY"] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("XAI_API_KEY", None)
        else:
            os.environ["XAI_API_KEY"] = old


def _mock_urlopen(body: bytes, status=200):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return MagicMock(return_value=resp)


state_g = st(momentum=0.6, change_24h=0.05, regime="bullish")
decision_g = jev.decide(state_g)
gate_g = check(decision_g, 0, Limits())

# 1. successful Grok response
with _fake_key():
    with patch("jev_bot.grok.urlopen", _mock_urlopen(_completions_body(_advice_json("agree")))):
        advice_ok = grok.analyze(state_g, decision_g, gate_g)
ok("grok success yields ok advice with agree",
   advice_ok.ok and advice_ok.consistency == "agree" and advice_ok.summary)

# 2. malformed JSON
with _fake_key():
    with patch("jev_bot.grok.urlopen",
               _mock_urlopen(_completions_body("not-json {{"))):
        advice_bad = grok.analyze(state_g, decision_g, gate_g)
ok("grok malformed JSON -> ok=False safe error",
   (not advice_bad.ok) and "malformed" in advice_bad.error.lower()
   and "test-key" not in advice_bad.error)

# 3. API HTTP error
def _raise_http(*a, **k):
    raise HTTPError("https://api.x.ai/v1/chat/completions", 500, "err",
                    hdrs=None, fp=io.BytesIO(b""))

with _fake_key():
    with patch("jev_bot.grok.urlopen", side_effect=_raise_http):
        advice_http = grok.analyze(state_g, decision_g, gate_g)
ok("grok API HTTP error -> ok=False",
   (not advice_http.ok) and "HTTP" in advice_http.error
   and "test-key" not in advice_http.error)

# 4. timeout / network failure
with _fake_key():
    with patch("jev_bot.grok.urlopen", side_effect=URLError("timed out")):
        advice_net = grok.analyze(state_g, decision_g, gate_g)
ok("grok network/timeout -> ok=False",
   (not advice_net.ok) and advice_net.error)

with _fake_key():
    with patch("jev_bot.grok.urlopen", side_effect=TimeoutError()):
        advice_to = grok.analyze(state_g, decision_g, gate_g)
ok("grok TimeoutError -> ok=False", not advice_to.ok)

# 5. missing XAI_API_KEY -> no network call
old_key = os.environ.pop("XAI_API_KEY", None)
try:
    called = {"n": 0}

    def _must_not_call(*a, **k):
        called["n"] += 1
        raise AssertionError("urlopen should not be called")

    with patch("jev_bot.grok.urlopen", side_effect=_must_not_call):
        advice_nokey = grok.analyze(state_g, decision_g, gate_g)
    ok("missing XAI_API_KEY -> ok=False, no network",
       (not advice_nokey.ok) and called["n"] == 0
       and "XAI_API_KEY" in advice_nokey.error)
finally:
    if old_key is not None:
        os.environ["XAI_API_KEY"] = old_key

# 6. disagreement recorded; Decision/Gate unchanged
with _fake_key():
    with patch("jev_bot.grok.urlopen",
               _mock_urlopen(_completions_body(_advice_json("disagree", 0.4)))):
        advice_dis = grok.analyze(state_g, decision_g, gate_g)
ok("grok disagree recorded without changing Decision/Gate",
   advice_dis.ok and advice_dis.consistency == "disagree"
   and decision_g.action == jev.decide(state_g).action
   and gate_g.verdict == check(decision_g, 0, Limits()).verdict)

# 7. risk gate + paper-only unchanged with analyst=grok
states_batch = markets.generate(n=6, seed=7)
book_plain = Book()
recs_plain = run(states_batch, book_plain, Limits(), engine="offline")
book_grok = Book()
with _fake_key():
    with patch("jev_bot.grok.urlopen",
               _mock_urlopen(_completions_body(_advice_json("disagree")))):
        recs_grok = run(states_batch, book_grok, Limits(),
                        engine="offline", analyst="grok")

ok("analyst grok keeps same execute count as without grok",
   book_plain.open_positions() == book_grok.open_positions())

ok("EXECUTE still only from gate with grok disagree advice",
   all(r[2].verdict == "EXECUTE" for r in recs_grok if r[2].verdict == "EXECUTE")
   and all(
       (r[3] is not None and r[3].consistency == "disagree")
       for r in recs_grok
   )
   and book_grok.open_positions()
       == sum(1 for r in recs_grok if r[2].verdict == "EXECUTE"))

# disagree does not block EXECUTE when gate says EXECUTE
exec_with_disagree = [
    r for r in recs_grok
    if r[2].verdict == "EXECUTE" and r[3] and r[3].consistency == "disagree"
]
ok("grok disagree does not block EXECUTE fills",
   len(exec_with_disagree) == book_grok.open_positions()
   and len(exec_with_disagree) == book_plain.open_positions())

# 4-tuple shape
ok("records are 4-tuples with advice=None when analyst off",
   all(len(r) == 4 and r[3] is None for r in recs_plain))

ok("records carry GrokAdvice when analyst=grok",
   all(len(r) == 4 and r[3] is not None for r in recs_grok))


print(f"\n  {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
