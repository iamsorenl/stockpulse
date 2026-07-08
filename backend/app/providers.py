"""External price-data providers with a fallback chain (SOR-151).

yfinance (Yahoo) is the primary source. Yahoo aggressively rate-limits
unauthenticated traffic (HTTP 429 -> yfinance returns an empty frame), so when
it yields nothing we fall back to Stooq's free, no-key daily CSV endpoint. Both
sources are free and keyless, honoring the project's zero-cost constraint.

Each provider returns candle dicts (oldest-first) in the canonical shape
    {"date","open","high","low","close","volume"}
or [] when it has no data / is unreachable. Providers never raise on network
issues -- they return [] so the orchestrator can fall through to the next one.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Callable, Optional

logger = logging.getLogger("stockpulse.providers")

# range -> (yfinance period, approximate calendar-day lookback for slicing the
# full-history Stooq CSV down to the requested window).
_RANGE_SPEC: dict[str, tuple[str, int]] = {
    "1mo": ("1mo", 31),
    "6mo": ("6mo", 186),
    "1y": ("1y", 366),
    "5y": ("5y", 1827),
}

_YF_ATTEMPTS = 2          # yfinance tries before falling back (429s are transient)
_YF_BACKOFF_SECONDS = 1.0
_STOOQ_TIMEOUT_SECONDS = 12
_HTTP_UA = "Mozilla/5.0 (StockPulse price-fetch)"


def _clean_float(value: Any) -> float:
    """Coerce to a JSON-safe float; NaN/inf/unparseable -> 0.0."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return f


def fetch_yfinance(ticker: str, range_: str) -> list[dict[str, Any]]:
    """Primary source. Retries a couple of times to ride out transient 429s."""
    period, _ = _RANGE_SPEC[range_]
    for attempt in range(1, _YF_ATTEMPTS + 1):
        frame = None
        try:
            import yfinance as yf  # lazy import so the app boots without network
            frame = yf.Ticker(ticker).history(
                period=period, interval="1d", auto_adjust=False
            )
        except Exception as exc:  # noqa: BLE001 - any failure just means "try next"
            logger.warning("yfinance attempt %d for %s failed: %s", attempt, ticker, exc)
        if frame is not None and not frame.empty:
            candles: list[dict[str, Any]] = []
            for index, row in frame.iterrows():
                candles.append(
                    {
                        "date": index.date().isoformat(),
                        "open": round(_clean_float(row["Open"]), 4),
                        "high": round(_clean_float(row["High"]), 4),
                        "low": round(_clean_float(row["Low"]), 4),
                        "close": round(_clean_float(row["Close"]), 4),
                        "volume": int(_clean_float(row["Volume"])),
                    }
                )
            return candles
        if attempt < _YF_ATTEMPTS:
            time.sleep(_YF_BACKOFF_SECONDS)
    return []


def _stooq_symbol(ticker: str) -> str:
    """Map a plain US ticker to Stooq's symbol convention (lowercase + ".us")."""
    return f"{ticker.lower()}.us"


def fetch_stooq(ticker: str, range_: str) -> list[dict[str, Any]]:
    """Fallback source: Stooq's free, no-key daily CSV (oldest-first, full history).

    We fetch the whole series and slice to the requested range by date. Returns []
    if the endpoint is unreachable or answers with anything other than CSV (e.g.
    an HTML anti-bot challenge, or "N/D" for an unknown symbol).
    """
    _, lookback_days = _RANGE_SPEC[range_]
    symbol = urllib.parse.quote(_stooq_symbol(ticker), safe="")
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    try:
        with urllib.request.urlopen(request, timeout=_STOOQ_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("stooq fetch for %s failed: %s", ticker, exc)
        return []

    if not raw.lstrip().lower().startswith("date,"):
        logger.warning("stooq returned non-CSV for %s (len=%d)", ticker, len(raw))
        return []

    cutoff = date.today() - timedelta(days=lookback_days)
    candles: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(raw)):
        raw_date = row.get("Date") or ""
        try:
            row_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if row_date < cutoff:
            continue
        candles.append(
            {
                "date": raw_date,
                "open": round(_clean_float(row.get("Open")), 4),
                "high": round(_clean_float(row.get("High")), 4),
                "low": round(_clean_float(row.get("Low")), 4),
                "close": round(_clean_float(row.get("Close")), 4),
                "volume": int(_clean_float(row.get("Volume"))),
            }
        )
    return candles


# Ordered provider chain: (name, fetch(ticker, range_) -> candles).
_PROVIDERS: list[tuple[str, Callable[[str, str], list[dict[str, Any]]]]] = [
    ("yfinance", fetch_yfinance),
    ("stooq", fetch_stooq),
]


def fetch_candles(ticker: str, range_: str) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Try each provider in order; return (candles, source_name).

    Returns ([], None) when every provider yields nothing (caller -> 404).
    """
    for name, fetch in _PROVIDERS:
        candles = fetch(ticker, range_)
        if candles:
            logger.info(
                "prices for %s/%s served by %s (%d candles)",
                ticker, range_, name, len(candles),
            )
            return candles, name
    logger.warning("no provider returned data for %s/%s", ticker, range_)
    return [], None
