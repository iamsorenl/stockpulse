"""Ticker chat: answer questions about one ticker from the data StockPulse has.

Reads only what's already computed: the cached 6mo prices payload (fetched on a
cache miss, same as the chart) and the cached sentiment result. It never
triggers a sentiment compute, so a chat message costs exactly one Groq call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import db, sentiment, stocks

logger = logging.getLogger("stockpulse.chat")

MAX_TURNS = 10  # most recent messages sent to the model
MAX_CHARS = 2000  # per-message clip before it reaches the model

_SYSTEM = """You are StockPulse's assistant for the stock ticker {ticker}.
Answer using ONLY the data below plus general financial knowledge.
Never invent numbers: if a figure is not in the data, say it isn't available here.
If the data section says something is unavailable, tell the user that plainly.
Keep answers short and plain. You may end with one line: "Not financial advice."

DATA for {ticker}:
{data}"""


def _price_lines(ticker: str) -> list[str]:
    try:
        p = stocks.get_prices(ticker, "6mo")
    except Exception as exc:  # noqa: BLE001 - missing prices just means less context
        logger.warning("chat: prices unavailable for %s: %s", ticker, exc)
        return ["Prices: unavailable."]
    candles = p.get("candles") or []
    if not candles:
        return ["Prices: unavailable."]
    last, first = candles[-1], candles[0]
    ind = p.get("indicators") or {}
    lines = [
        f"Last close: {last['close']:.2f} on {last['date']} (open {last['open']:.2f}, "
        f"high {last['high']:.2f}, low {last['low']:.2f}, volume {last['volume']}).",
        f"6-month change: {ind.get('pctChange')}% from {first['close']:.2f} on {first['date']}; "
        f"trend: {ind.get('trend')}.",
        f"6-month high close: {max(c['close'] for c in candles):.2f}; "
        f"low close: {min(c['close'] for c in candles):.2f}.",
    ]
    for name in ("sma20", "sma50"):
        series = ind.get(name) or []
        if series:
            lines.append(f"{name.upper()}: {series[-1]['value']:.2f} (as of {series[-1]['date']}).")
    return lines


def _sentiment_lines(ticker: str) -> list[str]:
    cached = db.cache_get(sentiment._cache_key(ticker))
    try:
        s: dict[str, Any] | None = json.loads(cached[0]) if cached else None
    except (json.JSONDecodeError, TypeError):
        s = None
    if not s:
        return ["Sentiment: not computed yet for this ticker."]
    lines = [f"Sentiment computed at {s.get('computed_at')} (source: {s.get('source')})."]
    if s.get("source") == "reddit":
        lines.append(
            f"Reddit net score {s.get('net_score')} (-100 bearish..+100 bullish) from "
            f"{s.get('volume')} relevant mentions: {s.get('bull')} bullish, "
            f"{s.get('bear')} bearish, {s.get('neutral')} neutral."
        )
    elif s.get("source") == "apewisdom":
        lines.append(
            f"Reddit mention volume (ApeWisdom, no sentiment): {s.get('volume')} mentions, "
            f"{s.get('mentions_prev')} 24h earlier, rank {s.get('rank')}."
        )
    else:
        lines.append("Reddit: no relevant discussion found.")
    news = s.get("news")
    if news and news.get("volume"):
        lines.append(
            f"News net score {news['net_score']} from {news['volume']} articles "
            f"({news['bull']} bullish, {news['bear']} bearish, {news['neutral']} neutral)."
        )
        lines += [f"- Headline: {a['title']} ({a['outlet']}, {a['sentiment']})" for a in news.get("top", [])]
    else:
        lines.append("News sentiment: unavailable.")
    if s.get("combined"):
        lines.append(f"Combined net score: {s['combined'].get('net_score')}.")
    return lines


def build_system_prompt(ticker: str) -> str:
    return _SYSTEM.format(ticker=ticker, data="\n".join(_price_lines(ticker) + _sentiment_lines(ticker)))


def answer(ticker: str, messages: list[dict[str, str]]) -> str:
    """One Groq call (shared daily cap). Raises llm_kit.LlmError on failure."""
    t = ticker.strip().upper()
    recent = [
        {"role": m["role"], "content": m["content"][:MAX_CHARS]}
        for m in messages[-MAX_TURNS:]
        if m["content"].strip()
    ]
    convo = [{"role": "system", "content": build_system_prompt(t)}] + recent
    return sentiment.groq_chat(convo)
