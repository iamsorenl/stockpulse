"""Trend indicators computed from a candle series (SOR-154).

Pure functions — no network, no pandas — so the math is trivially testable and
matches a hand calculation. Everything operates on the same list-of-dict candle
shape the API returns (each candle has "date" and "close" keys), and the outputs
line up 1:1 with the contract's `indicators` object.

Definitions
-----------
SMA(n) — simple moving average of the closing price over a trailing window of
    `n` candles. The value at candle i is the mean of closes[i-n+1 .. i]. It is
    only defined once there are at least `n` candles, so the series is *aligned
    to candle dates* but starts at the (n-1)-th candle. We do NOT emit nulls for
    the warm-up period; we simply omit those leading dates. This keeps each
    IndicatorPoint a real (date, value) pair the chart can plot directly.

pctChange — percent change of close over the *entire selected range*:
    (last_close - first_close) / first_close * 100.

trend — a coarse label derived from pctChange over the selected range:
    threshold band = +/- SIDEWAYS_THRESHOLD_PCT (2.0%).
      pctChange >  +2.0%  -> "up"
      pctChange <  -2.0%  -> "down"
      otherwise           -> "sideways"
    The "window" for the trend read is the full selected range (same window as
    pctChange), and the 2% dead-band filters out noise so a flat stock doesn't
    flip up/down on rounding. Documented threshold so later tuning is explicit.
"""

from __future__ import annotations

from typing import Any

# Trailing windows (in candles) for the two moving averages the contract asks for.
SMA_SHORT_WINDOW = 20
SMA_LONG_WINDOW = 50

# Dead-band (percent) around zero within which a range is called "sideways".
SIDEWAYS_THRESHOLD_PCT = 2.0

# Rounding: prices to cents, indicator/percent values to 4 dp for chart smoothness.
_PRICE_DP = 4
_PCT_DP = 2


def simple_moving_average(
    candles: list[dict[str, Any]], window: int
) -> list[dict[str, Any]]:
    """Trailing simple moving average of `close`, aligned to candle dates.

    Returns a list of {"date", "value"} points. Empty until `window` candles
    are available (no warm-up nulls emitted). O(n) via a running sum.
    """
    points: list[dict[str, Any]] = []
    if window <= 0:
        return points

    running = 0.0
    closes = [float(c["close"]) for c in candles]
    for i, close in enumerate(closes):
        running += close
        if i >= window:
            running -= closes[i - window]
        if i >= window - 1:
            points.append(
                {
                    "date": candles[i]["date"],
                    "value": round(running / window, _PRICE_DP),
                }
            )
    return points


def pct_change_over_range(candles: list[dict[str, Any]]) -> float:
    """Percent change of close from the first to the last candle in the range."""
    if len(candles) < 2:
        return 0.0
    first = float(candles[0]["close"])
    last = float(candles[-1]["close"])
    if first == 0:
        return 0.0
    return round((last - first) / first * 100.0, _PCT_DP)


def classify_trend(pct_change: float) -> str:
    """Map a range pctChange to "up" | "down" | "sideways" via the dead-band."""
    if pct_change > SIDEWAYS_THRESHOLD_PCT:
        return "up"
    if pct_change < -SIDEWAYS_THRESHOLD_PCT:
        return "down"
    return "sideways"


def compute_indicators(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the full `indicators` object for a candle series."""
    pct = pct_change_over_range(candles)
    return {
        "sma20": simple_moving_average(candles, SMA_SHORT_WINDOW),
        "sma50": simple_moving_average(candles, SMA_LONG_WINDOW),
        "pctChange": pct,
        "trend": classify_trend(pct),
    }
