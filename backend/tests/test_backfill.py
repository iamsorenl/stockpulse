"""Fixture-based tests for historical backfill (SOR-163/164). No network, no LLM.

Run from backend/:
    ./.venv/bin/python -m tests.test_backfill
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import backfill, db, reddit_ingest, sentiment  # noqa: E402
from app.reddit_ingest import Mention  # noqa: E402
from app.sentiment import SentimentResult  # noqa: E402

# Captured before any test monkeypatches it, so the last test can use the real one.
_REAL_FETCH_DAY = backfill.fetch_day_mentions

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _with_temp_db(fn):
    orig = db.DB_PATH
    tmpdir = tempfile.mkdtemp()
    db.DB_PATH = Path(tmpdir) / "bf_test.db"
    try:
        db.init_db()
        fn()
    finally:
        db.DB_PATH = orig
        shutil.rmtree(tmpdir, ignore_errors=True)


def _mention(mid):
    return Mention(id=mid, kind="post", subreddit="stocks", author="u",
                   created_utc=0.0, text="AAPL to the moon", score=5,
                   permalink="https://www.reddit.com/r/stocks/comments/x/")


def _result(net=30.0, vol=7):
    return SentimentResult(ticker="AAPL", net_score=net, bull=5, bear=1, neutral=1,
                           volume=vol, computed_at="x", top=[])


@case
def test_backfill_day_writes_backfill_row():
    def body():
        backfill.fetch_day_mentions = lambda t, d: [_mention("m1")]
        sentiment.score_mentions = lambda t, m: _result(net=30.0, vol=7)
        row = backfill.backfill_day("aapl", date(2026, 6, 1))
        assert row is not None and row["captured"] == "backfill", row
        stored = db.snapshot_get_one("AAPL", "2026-06-01")
        assert stored["captured"] == "backfill", stored
        assert stored["source"] == "reddit" and stored["net_score"] == 30.0, stored
    _with_temp_db(body)


@case
def test_backfill_never_overwrites_live_day():
    def body():
        db.snapshot_upsert({
            "ticker": "AAPL", "date": "2026-06-01", "computed_at": "morning",
            "source": "reddit", "net_score": 45.0, "bull": 8, "bear": 2, "neutral": 2,
            "volume": 12, "mentions_prev": None, "upvotes": None, "rank": None,
            "top_json": "[]", "news_net_score": None, "news_volume": None,
            "captured": "live",
        })
        # backfill would find data, but must not touch the live row
        backfill.fetch_day_mentions = lambda t, d: [_mention("m1")]
        sentiment.score_mentions = lambda t, m: _result(net=-99.0, vol=9)
        assert backfill.backfill_day("AAPL", date(2026, 6, 1)) is None
        stored = db.snapshot_get_one("AAPL", "2026-06-01")
        assert stored["captured"] == "live" and stored["net_score"] == 45.0, stored
    _with_temp_db(body)


@case
def test_backfill_skips_empty_and_irrelevant_days():
    def body():
        backfill.fetch_day_mentions = lambda t, d: []            # no mentions
        assert backfill.backfill_day("AAPL", date(2026, 6, 2)) is None
        backfill.fetch_day_mentions = lambda t, d: [_mention("m1")]
        sentiment.score_mentions = lambda t, m: _result(vol=0)   # none relevant
        assert backfill.backfill_day("AAPL", date(2026, 6, 3)) is None
    _with_temp_db(body)


@case
def test_backfill_range_counts_and_continues():
    def body():
        backfill.time.sleep = lambda s: None  # no delay in tests
        backfill.fetch_day_mentions = lambda t, d: [_mention("m")] if d == date(2026, 6, 1) else []
        sentiment.score_mentions = lambda t, m: _result(vol=7)
        summary = backfill.backfill_range("AAPL", date(2026, 6, 1), date(2026, 6, 3))
        assert summary["written"] == 1 and summary["skipped"] == 2, summary
    _with_temp_db(body)


@case
def test_fetch_day_mentions_uses_utc_date_window():
    backfill.fetch_day_mentions = _REAL_FETCH_DAY  # undo earlier monkeypatches
    captured = {}

    def fake_get_json(path, params):
        captured["after"] = params.get("after")
        captured["before"] = params.get("before")
        if path == "/posts/search" and params.get("subreddit") == "stocks":
            return [{"id": "p1", "subreddit": "stocks", "title": "AAPL rip",
                     "selftext": "", "score": 5, "created_utc": 0, "author": "u"}]
        return []

    orig = reddit_ingest._get_json
    reddit_ingest._get_json = fake_get_json
    try:
        ms = backfill.fetch_day_mentions("AAPL", date(2026, 6, 1))
    finally:
        reddit_ingest._get_json = orig
    assert captured["after"] == "2026-06-01", captured
    assert captured["before"] == "2026-06-02", captured  # exclusive next-day bound
    assert any(m.id == "p1" for m in ms), ms


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
