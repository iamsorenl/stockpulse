"""Combined trend signals (SOR-167).

Flags days where price momentum and crowd/news sentiment either CONFIRM each
other (both bullish or both bearish) or DIVERGE (price up while sentiment is
bearish, or vice versa) — the divergences being the interesting "the tape and
the mood disagree" moments from the original pitch.

Pure `detect_events` over a price series + sentiment snapshots (easy to test);
`get_trend_events` wires in the live price + snapshot sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import db, stocks

# A day is only flagged when BOTH moves clear these bands (avoids noise on flat
# tape or lukewarm sentiment).
_PRICE_WINDOW = 5        # trading days of price momentum
_PRICE_THRESHOLD = 2.0   # percent
_SENT_THRESHOLD = 10.0   # net_score points (-100..100)


@dataclass
class TrendEvent:
    date: str
    kind: str            # "confirm" | "diverge"
    note: str
    price_change: float  # percent over the momentum window
    sentiment: float     # daily sentiment level


def _daily_sentiment(snap: dict[str, Any]) -> Optional[float]:
    """Best daily sentiment for a snapshot: blend real crowd + news, or None.

    ApeWisdom-only days carry no sentiment (net_score 0), so they contribute
    nothing; a day with neither real crowd nor news sentiment yields None.
    """
    parts: list[float] = []
    if snap.get("source") == "reddit" and (snap.get("volume") or 0) > 0:
        parts.append(float(snap["net_score"]))
    if snap.get("news_volume"):
        parts.append(float(snap["news_net_score"]))
    return sum(parts) / len(parts) if parts else None


def detect_events(
    candles: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    price_window: int = _PRICE_WINDOW,
    price_threshold: float = _PRICE_THRESHOLD,
    sent_threshold: float = _SENT_THRESHOLD,
) -> list[TrendEvent]:
    """Return confirm/diverge events for days with both a price move and sentiment."""
    closes = {c["date"]: c["close"] for c in candles}
    price_dates = sorted(closes)

    events: list[TrendEvent] = []
    for snap in sorted(snapshots, key=lambda s: s["date"]):
        s = _daily_sentiment(snap)
        if s is None or abs(s) < sent_threshold:
            continue
        day = snap["date"]
        if day not in closes:
            continue
        prior = [d for d in price_dates if d <= day]
        if len(prior) <= price_window:
            continue  # not enough price history for the momentum window
        close_now = closes[day]
        close_then = closes[prior[-1 - price_window]]
        if not close_then:
            continue
        price_pct = (close_now - close_then) / close_then * 100
        if abs(price_pct) < price_threshold:
            continue

        price_up = price_pct > 0
        sent_up = s > 0
        kind = "confirm" if price_up == sent_up else "diverge"
        mood = "bullish" if sent_up else "bearish"
        move = "up" if price_up else "down"
        note = (
            f"Price {move} {price_pct:+.1f}% while sentiment is {mood} ({s:+.0f}) — "
            + ("they agree" if kind == "confirm" else "they disagree")
        )
        events.append(TrendEvent(
            date=day, kind=kind, note=note,
            price_change=round(price_pct, 1), sentiment=round(s, 1),
        ))
    return events


def get_trend_events(ticker: str, range_: str = "6mo") -> dict[str, Any]:
    """Fetch price + sentiment history and return trend events (empty on no data)."""
    normalized = (ticker or "").strip().upper()
    try:
        candles = stocks.get_prices(normalized, range_).get("candles", [])
    except (ValueError, stocks.UnknownTickerError):
        candles = []
    since = (
        datetime.now(timezone.utc).date() - timedelta(days=_range_to_days(range_))
    ).isoformat()
    # Full snapshot rows (incl. news columns), not the trimmed history points.
    snapshots = db.snapshots_get(normalized, since)
    events = detect_events(candles, snapshots)
    return {"ticker": normalized, "events": [e.__dict__ for e in events]}


def _range_to_days(range_: str) -> int:
    return {"1mo": 31, "6mo": 186, "1y": 366, "5y": 1825}.get(range_, 186)
