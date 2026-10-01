"""Tests for POST /api/chat (ticker chat), with Groq mocked (no key/tokens).

Calls the route function directly (no app boot), like the other tests. Run
from backend/:
    ./.venv/bin/python -m tests.test_chat
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from llm_kit import DailyCap, LlmError, Reply  # noqa: E402

from app import api, chat, config, sentiment as S  # noqa: E402
from app.models import ChatRequest  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


_PRICES = {
    "ticker": "AAPL", "range": "6mo",
    "candles": [
        {"date": "2026-04-01", "open": 1, "high": 1, "low": 1, "close": 150.0, "volume": 10},
        {"date": "2026-09-29", "open": 190, "high": 195, "low": 188, "close": 193.25, "volume": 99},
    ],
    "indicators": {
        "sma20": [{"date": "2026-09-29", "value": 190.5}],
        "sma50": [{"date": "2026-09-29", "value": 185.0}],
        "pctChange": 28.8, "trend": "up",
    },
}
_SENTIMENT = {
    "ticker": "AAPL", "net_score": 40.0, "bull": 6, "bear": 2, "neutral": 2, "volume": 10,
    "computed_at": "2026-09-30T00:00:00+00:00", "top": [], "source": "reddit",
    "news": None, "combined": {"net_score": 40.0, "has_reddit": True, "has_news": False},
}


class _Patched:
    """Swap Groq/prices/cache/config for the duration of a test, then restore."""

    def __init__(self, reply=None, error=None, cap=5, prices=_PRICES, sent=_SENTIMENT,
                 errors_first=0):
        self.calls: list = []
        self.sleeps: list = []
        self.reply, self.error, self.cap = reply, error, cap
        self.prices, self.sent = prices, sent
        self.errors_first = errors_first  # raise self.error only on the first N calls

    def _compat(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.error and (not self.errors_first or len(self.calls) <= self.errors_first):
            raise self.error
        return Reply(self.reply)

    def _get_prices(self, ticker, range_):
        if self.prices is None:
            raise chat.stocks.UnknownTickerError("nope")
        return self.prices

    def _get_sentiment(self, ticker):
        if self.sent is None:
            raise RuntimeError("sentiment sources down")
        return self.sent

    def __enter__(self):
        self._saved = (S.compat_chat, S._groq_daily_cap, config.GROQ_CONFIGURED,
                       config.GROQ_API_KEY, chat.stocks.get_prices, S.get_sentiment, S.time.sleep)
        S.compat_chat = self._compat
        S._groq_daily_cap = DailyCap(self.cap)
        config.GROQ_CONFIGURED, config.GROQ_API_KEY = True, "fake-key"
        chat.stocks.get_prices = self._get_prices
        S.get_sentiment = self._get_sentiment
        S.time.sleep = self.sleeps.append
        return self

    def __exit__(self, *exc):
        (S.compat_chat, S._groq_daily_cap, config.GROQ_CONFIGURED,
         config.GROQ_API_KEY, chat.stocks.get_prices, S.get_sentiment, S.time.sleep) = self._saved


def _req(n=1, ticker="aapl", content="How is it doing?"):
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{content} {i}"}
            for i in range(n)]
    return ChatRequest(ticker=ticker, messages=msgs)


def _status(fn) -> int:
    try:
        fn()
    except HTTPException as exc:
        return exc.status_code
    raise AssertionError("expected HTTPException")


@case
def test_success_returns_plain_text_with_ticker_data_in_prompt():
    with _Patched(reply="AAPL closed at 193.25. Not financial advice.") as p:
        out = api.chat_route(_req())
        assert out == "AAPL closed at 193.25. Not financial advice.", out
        messages, kw = p.calls[0]
        system = messages[0]["content"]
        assert messages[0]["role"] == "system"
        assert "AAPL" in system and "193.25" in system and "SMA20: 190.50" in system, system
        assert "Reddit net score 40.0" in system and "News sentiment: unavailable" in system, system
        assert kw["model"] == config.GROQ_MODEL and kw["temperature"] == 0, kw
        assert kw["json_mode"] is False and kw["api_key"] == "fake-key", kw


@case
def test_history_trimmed_to_last_turns_and_long_messages_clipped():
    with _Patched(reply="ok") as p:
        req = _req(n=15, content="x" * 5000)
        api.chat_route(req)
        sent = p.calls[0][0][1:]
        assert len(sent) == chat.MAX_TURNS, len(sent)
        assert sent[-1]["content"].startswith("x") and len(sent[-1]["content"]) == chat.MAX_CHARS


@case
def test_missing_data_is_stated_not_invented():
    with _Patched(reply="ok", prices=None, sent=None) as p:
        api.chat_route(_req())
        system = p.calls[0][0][0]["content"]
        assert "Prices: unavailable." in system, system
        assert "Sentiment: unavailable right now." in system, system


@case
def test_daily_cap_hit_is_429_without_calling_groq():
    with _Patched(reply="never", cap=0) as p:
        assert _status(lambda: api.chat_route(_req())) == 429
        assert p.calls == [], p.calls


@case
def test_chat_shares_the_sentiment_daily_cap():
    with _Patched(reply="ok", cap=1):
        assert S._groq_daily_cap.try_consume() is True  # a sentiment call uses the budget
        assert _status(lambda: api.chat_route(_req())) == 429


@case
def test_groq_rate_limit_maps_to_429_and_other_errors_to_502():
    with _Patched(error=LlmError("rate-limit", "slow down", 429)) as p:
        assert _status(lambda: api.chat_route(_req())) == 429
        assert len(p.calls) == len(chat.RETRY_DELAYS) + 1 and p.sleeps == list(chat.RETRY_DELAYS)
    for kind in ("api", "timeout", "unreachable", "auth", "bad-response"):
        with _Patched(error=LlmError(kind, "boom")):
            assert _status(lambda: api.chat_route(_req())) == 502, kind


@case
def test_groq_429_is_retried_then_answers_charging_cap_once():
    with _Patched(reply="ok", error=LlmError("rate-limit", "slow down", 429), errors_first=2, cap=1) as p:
        assert api.chat_route(_req()) == "ok"
        assert len(p.calls) == 3 and p.sleeps == list(chat.RETRY_DELAYS[:2]), (p.calls, p.sleeps)


@case
def test_other_groq_errors_are_not_retried():
    with _Patched(error=LlmError("api", "boom")) as p:
        assert _status(lambda: api.chat_route(_req())) == 502
        assert len(p.calls) == 1 and p.sleeps == []


@case
def test_not_configured_is_503():
    with _Patched(reply="never") as p:
        config.GROQ_CONFIGURED = False
        assert _status(lambda: api.chat_route(_req())) == 503
        assert p.calls == []


@case
def test_request_validation_rejects_bad_ticker_and_role():
    from pydantic import ValidationError
    for bad in (
        {"ticker": "AAPL; DROP", "messages": [{"role": "user", "content": "hi"}]},
        {"ticker": "AAPL", "messages": [{"role": "system", "content": "hi"}]},
        {"ticker": "AAPL", "messages": []},
    ):
        try:
            ChatRequest(**bad)
            raise AssertionError(f"accepted {bad}")
        except ValidationError:
            pass


def main() -> int:
    failures = 0
    for fn in _CASES:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(_CASES) - failures}/{len(_CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
