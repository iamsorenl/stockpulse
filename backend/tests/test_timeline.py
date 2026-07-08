"""Tests for the M3 timeline reads (SOR-159 history, SOR-160 on-this-day).

Seeds real snapshot rows into a throwaway temp SQLite DB, then exercises the
sentiment.get_history / get_on_this_day service functions. Run from backend/:
    ./.venv/bin/python -m tests.test_timeline
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, sentiment  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _with_temp_db(fn):
    orig = db.DB_PATH
    tmpdir = tempfile.mkdtemp()
    db.DB_PATH = Path(tmpdir) / "tl_test.db"
    try:
        db.init_db()
        fn()
    finally:
        db.DB_PATH = orig
        shutil.rmtree(tmpdir, ignore_errors=True)  # remove dir + any -wal/-shm files


def _seed(ticker, d, source="reddit", net=10.0, vol=5, top=None):
    db.snapshot_upsert({
        "ticker": ticker, "date": d, "computed_at": f"{d}T00:00:00+00:00",
        "source": source, "net_score": net, "bull": 3, "bear": 1, "neutral": 1,
        "volume": vol, "mentions_prev": None, "upvotes": None, "rank": None,
        "top_json": json.dumps(top or []),
        "news_net_score": None, "news_volume": None, "captured": "live",
    })


@case
def test_history_windows_and_orders():
    def body():
        today = date.today()
        for n in (1, 3, 10, 100):  # 100 days ago is outside a 30-day window
            _seed("AAPL", (today - timedelta(days=n)).isoformat(), net=float(n))
        res = sentiment.get_history("aapl", days=30)
        assert res["ticker"] == "AAPL", res
        dates = [p["date"] for p in res["points"]]
        assert dates == sorted(dates), "oldest-first"
        assert len(res["points"]) == 3, dates  # the 100-day-old point is excluded
    _with_temp_db(body)


@case
def test_history_empty_when_no_snapshots():
    def body():
        res = sentiment.get_history("ZZZZ", days=90)
        assert res == {"ticker": "ZZZZ", "points": []}, res
    _with_temp_db(body)


@case
def test_on_this_day_with_snapshot_and_runup():
    def body():
        today = date.today()
        target = today.isoformat()
        _seed("AAPL", target, net=42.0, vol=9,
              top=[{"id": "a", "kind": "post", "subreddit": "stocks", "score": 5,
                    "permalink": "http://x/a", "text": "AAPL good", "sentiment": "bullish"}])
        # run-up: 3 of the prior 7 days
        for n in (1, 2, 5):
            _seed("AAPL", (today - timedelta(days=n)).isoformat(), net=float(-n))
        # an 8-day-old point must NOT be in the run-up window
        _seed("AAPL", (today - timedelta(days=8)).isoformat())

        res = sentiment.get_on_this_day("AAPL", target)
        assert res["snapshot"] is not None, res
        assert res["snapshot"]["net_score"] == 42.0 and res["snapshot"]["volume"] == 9, res
        assert res["snapshot"]["top"][0]["sentiment"] == "bullish", res["snapshot"]["top"]
        runup_dates = [p["date"] for p in res["runup"]]
        assert len(runup_dates) == 3, runup_dates          # only the 3 within 7 days
        assert target not in runup_dates, "target day excluded from its own run-up"
        assert runup_dates == sorted(runup_dates), "run-up oldest-first"
    _with_temp_db(body)


@case
def test_on_this_day_missing_returns_null_snapshot():
    def body():
        res = sentiment.get_on_this_day("AAPL", "2020-01-01")
        assert res["snapshot"] is None, res
        assert res["runup"] == [], res
        assert res["date"] == "2020-01-01", res
    _with_temp_db(body)


@case
def test_on_this_day_bad_date_raises_valueerror():
    def body():
        raised = False
        try:
            sentiment.get_on_this_day("AAPL", "not-a-date")
        except ValueError:
            raised = True
        assert raised, "malformed date must raise ValueError (route -> 400)"
    _with_temp_db(body)


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
