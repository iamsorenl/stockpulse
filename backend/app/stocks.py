"""Price data fetching, caching, and response assembly (SOR-151 + SOR-154).

Flow for GET /api/stocks/{ticker}/prices?range=:
  1. Normalize ticker + validate range.
  2. Try the SQLite cache (freshness policy below).
  3. On miss/stale, fetch OHLCV from yfinance, build candles, compute indicators,
     cache the assembled payload, and return it.
  4. Unknown/invalid ticker (no price rows) -> UnknownTickerError -> HTTP 404.

The yfinance call is isolated in `_download_candles` so it can be swapped/mocked
in tests without a network dependency.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db
from .indicators import compute_indicators

# Supported ranges -> yfinance period. All use daily ("1d") candles so SMA20 /
# SMA50 always mean "20-day" / "50-day" regardless of the selected range.
_RANGE_TO_PERIOD: dict[str, str] = {
    "1mo": "1mo",
    "6mo": "6mo",
    "1y": "1y",
    "5y": "5y",
}

SUPPORTED_RANGES = tuple(_RANGE_TO_PERIOD.keys())

# --- Cache freshness policy ----------------------------------------------------
# Cache key is "prices:{TICKER}:{range}"; the value is the full assembled JSON
# payload. Whether a cached entry is still fresh depends on how "live" its data
# is:
#   * Cross-day rule: an entry written on an EARLIER UTC calendar day is always
#     stale. A new trading session may have closed since, adding/updating the
#     most recent candle, so we refetch. (Older/historical candles never change,
#     but the tail does, so the whole range is refetched together.)
#   * Same-day + today's candle present: if the newest candle is dated *today*,
#     it's an intraday, still-forming bar that changes throughout the session.
#     We serve cache for only a short window (INTRADAY_TTL) so quotes stay live.
#   * Same-day + purely historical: if the newest candle predates today (e.g.
#     fetched on a weekend/holiday when the last bar is Friday's close), the data
#     won't change today, so a longer window (HISTORICAL_TTL) applies.
INTRADAY_TTL = timedelta(minutes=15)
HISTORICAL_TTL = timedelta(hours=12)


class UnknownTickerError(Exception):
    """Raised when yfinance returns no price data for a ticker."""


def _cache_key(ticker: str, range_: str) -> str:
    return f"prices:{ticker}:{range_}"


def _is_fresh(created_at: datetime, payload: dict[str, Any]) -> bool:
    """Apply the freshness policy documented above."""
    now = datetime.now(timezone.utc)
    if created_at.date() != now.date():
        return False  # cross-day: always refetch
    age = now - created_at
    candles = payload.get("candles") or []
    newest_date = candles[-1]["date"] if candles else None
    if newest_date == now.date().isoformat():
        return age < INTRADAY_TTL
    return age < HISTORICAL_TTL


def _clean_float(value: Any) -> float:
    """Coerce a value to a JSON-safe float (NaN/inf -> 0.0)."""
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return f


def _download_candles(ticker: str, range_: str) -> list[dict[str, Any]]:
    """Fetch daily OHLCV from yfinance and return candle dicts (oldest-first).

    Isolated so tests can monkeypatch it. Returns [] when yfinance has no data
    for the ticker (caller turns that into a 404).
    """
    import yfinance as yf  # imported lazily so the app boots without network

    period = _RANGE_TO_PERIOD[range_]
    frame = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if frame is None or frame.empty:
        return []

    candles: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        # index is a pandas Timestamp; take the calendar date.
        date = index.date().isoformat()
        candles.append(
            {
                "date": date,
                "open": round(_clean_float(row["Open"]), 4),
                "high": round(_clean_float(row["High"]), 4),
                "low": round(_clean_float(row["Low"]), 4),
                "close": round(_clean_float(row["Close"]), 4),
                "volume": int(_clean_float(row["Volume"])),
            }
        )
    return candles


def _assemble_payload(ticker: str, range_: str) -> dict[str, Any]:
    """Fetch + build the full prices response for a ticker/range (no caching)."""
    candles = _download_candles(ticker, range_)
    if not candles:
        raise UnknownTickerError(
            f"No price data found for ticker '{ticker}'. It may be invalid or delisted."
        )
    return {
        "ticker": ticker,
        "range": range_,
        "candles": candles,
        "indicators": compute_indicators(candles),
    }


def get_prices(ticker: str, range_: str) -> dict[str, Any]:
    """Return the prices payload for `ticker`/`range_`, using the SQLite cache.

    Raises ValueError for an unsupported range and UnknownTickerError for an
    unknown/delisted ticker (the router maps these to 400/404 respectively).
    """
    if range_ not in _RANGE_TO_PERIOD:
        raise ValueError(
            f"Unsupported range '{range_}'. Choose one of: {', '.join(SUPPORTED_RANGES)}."
        )

    normalized = (ticker or "").strip().upper()
    if not normalized:
        raise UnknownTickerError("No ticker provided.")

    key = _cache_key(normalized, range_)
    cached = db.cache_get(key)
    if cached is not None:
        value, created_at = cached
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            payload = None
        if payload is not None and _is_fresh(created_at, payload):
            return payload

    payload = _assemble_payload(normalized, range_)
    db.cache_set(key, json.dumps(payload))
    return payload
