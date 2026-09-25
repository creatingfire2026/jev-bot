"""Optional advisory Grok/xAI analyst.

Grok reviews a market state together with the JEV decision and risk-gate
verdict and returns a structured opinion. It is advisory only: it never
overrides Decision or Gate, never authorizes trades, and never touches
execution. Config comes only from the environment; the API key is never
logged, printed, or placed in exception messages.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .risk import Gate
from .types import Decision, MarketState

# Single documented default; override with GROK_MODEL.
DEFAULT_MODEL = "grok-4.6"
DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_TIMEOUT = 30

_CONSISTENCY = frozenset({"agree", "disagree", "uncertain"})

_SYSTEM = (
    "You are a cautious market-analysis assistant. "
    "You provide no guaranteed returns and this is not financial advice. "
    "Analyze only the supplied market state and decision context. "
    "Do not invent prices, news, indicators, or external data. "
    "Return JSON only. "
    "Do not issue execution instructions. "
    "Explain uncertainty briefly."
)


@dataclass
class GrokAdvice:
    summary: str
    risks: list = field(default_factory=list)
    catalysts: list = field(default_factory=list)
    consistency: str = "uncertain"  # agree|disagree|uncertain
    confidence: float = 0.0
    ok: bool = True
    error: str = ""


def _fail(msg: str) -> GrokAdvice:
    return GrokAdvice(
        summary="",
        risks=[],
        catalysts=[],
        consistency="uncertain",
        confidence=0.0,
        ok=False,
        error=msg,
    )


def _config() -> tuple[Optional[str], str, str, float]:
    key = os.environ.get("XAI_API_KEY") or None
    base = (os.environ.get("XAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("GROK_MODEL") or DEFAULT_MODEL
    try:
        timeout = float(os.environ.get("GROK_TIMEOUT") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = float(DEFAULT_TIMEOUT)
    return key, base, model, timeout


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        # drop opening fence line (``` or ```json)
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        else:
            s = s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _coerce_confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _parse_advice(raw: str) -> GrokAdvice:
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return _fail("malformed JSON in Grok response")

    if not isinstance(data, dict):
        return _fail("malformed JSON in Grok response")

    summary = data.get("summary")
    risks = data.get("risks")
    catalysts = data.get("catalysts")
    consistency = data.get("consistency")
    confidence = data.get("confidence")

    if not isinstance(summary, str):
        return _fail("invalid or missing fields in Grok response")
    if not isinstance(risks, list):
        return _fail("invalid or missing fields in Grok response")
    if not isinstance(catalysts, list):
        return _fail("invalid or missing fields in Grok response")
    if consistency not in _CONSISTENCY:
        return _fail("invalid consistency in Grok response")

    return GrokAdvice(
        summary=summary,
        risks=[str(x) for x in risks],
        catalysts=[str(x) for x in catalysts],
        consistency=consistency,
        confidence=_coerce_confidence(confidence),
        ok=True,
        error="",
    )


def _payload(state: MarketState, decision: Decision, gate: Gate) -> dict:
    return {
        "symbol": state.symbol,
        "asset_class": state.asset_class,
        "price": state.price,
        "change_24h": state.change_24h,
        "volume_change": state.volume_delta,
        "momentum": state.momentum,
        "news": state.news,
        "regime": state.regime,
        "jev_action": decision.action,
        "jev_probability": decision.probability,
        "jev_confidence": decision.confidence,
        "risk_gate": {"verdict": gate.verdict, "reason": gate.reason},
    }


def _safe_http_error(exc: HTTPError) -> str:
    # Never include response body (may echo auth) or request headers.
    code = getattr(exc, "code", None)
    if code is not None:
        return f"Grok API HTTP error ({code})"
    return "Grok API HTTP error"


def analyze(state: MarketState, decision: Decision, gate: Gate) -> GrokAdvice:
    """Ask Grok for advisory analysis. Never raises into the bot loop if avoidable."""
    key, base, model, timeout = _config()
    if not key:
        return _fail("XAI_API_KEY not set")

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(_payload(state, decision, gate))},
        ],
        "temperature": 0.2,
    }).encode("utf-8")

    url = f"{base}/chat/completions"
    req = Request(url, data=body, headers={
        "content-type": "application/json",
        "authorization": f"Bearer {key}",
    }, method="POST")

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return _fail(_safe_http_error(e))
    except TimeoutError:
        return _fail("Grok request timed out")
    except URLError:
        return _fail("Grok network error")
    except OSError:
        return _fail("Grok network error")
    except Exception:
        return _fail("Grok request failed")

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _fail("malformed JSON in Grok response")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return _fail("malformed Grok chat completions response")

    if not isinstance(content, str):
        return _fail("malformed Grok chat completions response")

    return _parse_advice(content)
