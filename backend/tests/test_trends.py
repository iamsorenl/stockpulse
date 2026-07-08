"""Tests for combined trend-signal detection (SOR-167). No network.

Run from backend/:
    ./.venv/bin/python -m tests.test_trends
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import trends  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


# 10 rising days: 2026-06-01..06-10, close 100,102,...,118 (steadily up).
_CANDLES = [
    {"date": f"2026-06-{d:02d}", "close": 100.0 + 2 * (d - 1)} for d in range(1, 11)
]


def _snap(date, source="reddit", net=0.0, volume=5, news_net=None, news_vol=None):
    return {
        "date": date, "source": source, "net_score": net, "volume": volume,
        "bull": 0, "bear": 0, "neutral": 0,
        "news_net_score": news_net, "news_volume": news_vol,
    }


@case
def test_diverge_and_confirm_flagged():
    snaps = [
        _snap("2026-06-10", net=-30.0),   # price up ~11%, sentiment bearish -> diverge
        _snap("2026-06-09", net=40.0),    # price up, sentiment bullish -> confirm
        _snap("2026-06-06", source="none", volume=0, news_net=-40.0, news_vol=10),  # news bearish -> diverge
    ]
    ev = {e.date: e for e in trends.detect_events(_CANDLES, snaps)}
    assert ev["2026-06-10"].kind == "diverge", ev["2026-06-10"]
    assert ev["2026-06-09"].kind == "confirm", ev["2026-06-09"]
    assert ev["2026-06-06"].kind == "diverge", ev["2026-06-06"]
    assert ev["2026-06-10"].price_change > 0 and ev["2026-06-10"].sentiment == -30.0
    assert "disagree" in ev["2026-06-10"].note and "agree" in ev["2026-06-09"].note


@case
def test_weak_sentiment_and_flat_price_skipped():
    # sentiment below threshold -> no event
    ev = {e.date: e for e in trends.detect_events(_CANDLES, [_snap("2026-06-10", net=5.0)])}
    assert ev == {}, ev
    # flat price -> no event even with strong sentiment
    flat = [{"date": f"2026-06-{d:02d}", "close": 100.0} for d in range(1, 11)]
    ev2 = trends.detect_events(flat, [_snap("2026-06-10", net=50.0)])
    assert ev2 == [], ev2


@case
def test_insufficient_price_history_skipped():
    # a snapshot too early to have `price_window` prior closes is skipped
    ev = trends.detect_events(_CANDLES, [_snap("2026-06-02", net=50.0)])
    assert ev == [], ev


@case
def test_apewisdom_only_day_has_no_sentiment():
    # ApeWisdom volume carries no sentiment -> day contributes no event
    ev = trends.detect_events(_CANDLES, [_snap("2026-06-10", source="apewisdom", net=0.0, volume=200)])
    assert ev == [], ev


@case
def test_daily_sentiment_blends_reddit_and_news():
    s = trends._daily_sentiment(_snap("d", net=40.0, volume=5, news_net=-10.0, news_vol=8))
    assert s == 15.0, s  # (40 + -10) / 2
    assert trends._daily_sentiment(_snap("d", source="none", volume=0)) is None


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
