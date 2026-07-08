"""Tests for the sentiment_snapshots table + accessors (SOR-158).

Runs against a throwaway temp SQLite file (not the app DB). Run from backend/:
    ./.venv/bin/python -m tests.test_snapshots
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone  # noqa: E402

from app import db, sentiment as S  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _with_temp_db(fn):
    """Point db at a fresh temp file, init schema, run fn(), always clean up."""
    orig = db.DB_PATH
    tmpdir = tempfile.mkdtemp()
    db.DB_PATH = Path(tmpdir) / "snap_test.db"
    try:
        db.init_db()
        fn()
    finally:
        db.DB_PATH = orig
        shutil.rmtree(tmpdir, ignore_errors=True)  # remove dir + any -wal/-shm files


def _row(ticker, date, source="reddit", net=10.0, vol=5, **kw):
    base = {
        "ticker": ticker, "date": date, "computed_at": f"{date}T00:00:00+00:00",
        "source": source, "net_score": net, "bull": 3, "bear": 1, "neutral": 1,
        "volume": vol, "mentions_prev": None, "upvotes": None, "rank": None,
        "top_json": "[]", "news_net_score": None, "news_volume": None, "captured": "live",
    }
    base.update(kw)
    return base


@case
def test_upsert_and_get_one():
    def body():
        db.snapshot_upsert(_row("AAPL", "2026-07-01", net=25.0, vol=8))
        got = db.snapshot_get_one("AAPL", "2026-07-01")
        assert got is not None, "row should exist"
        assert got["net_score"] == 25.0 and got["volume"] == 8, got
        assert db.snapshot_get_one("AAPL", "2026-06-30") is None, "missing day -> None"
    _with_temp_db(body)


@case
def test_upsert_same_day_replaces():
    def body():
        db.snapshot_upsert(_row("AAPL", "2026-07-01", net=10.0, vol=5))
        db.snapshot_upsert(_row("AAPL", "2026-07-01", net=-30.0, vol=99, source="apewisdom"))
        got = db.snapshot_get_one("AAPL", "2026-07-01")
        assert got["net_score"] == -30.0 and got["volume"] == 99, got
        assert got["source"] == "apewisdom", got  # latest compute wins
        # still a single row for that (ticker, date)
        assert len(db.snapshots_get("AAPL", "2026-01-01")) == 1
    _with_temp_db(body)


@case
def test_snapshots_get_range_ordered():
    def body():
        for d in ("2026-07-03", "2026-07-01", "2026-07-02", "2026-06-20"):
            db.snapshot_upsert(_row("AAPL", d))
        db.snapshot_upsert(_row("MSFT", "2026-07-01"))  # other ticker, excluded
        rows = db.snapshots_get("AAPL", "2026-07-01")
        dates = [r["date"] for r in rows]
        assert dates == ["2026-07-01", "2026-07-02", "2026-07-03"], dates  # since-filter + asc
    _with_temp_db(body)


@case
def test_apewisdom_fields_persist():
    def body():
        db.snapshot_upsert(_row(
            "NVDA", "2026-07-01", source="apewisdom", net=0.0, vol=216,
            mentions_prev=118, upvotes=710, rank=4,
        ))
        got = db.snapshot_get_one("NVDA", "2026-07-01")
        assert got["mentions_prev"] == 118 and got["upvotes"] == 710 and got["rank"] == 4, got
    _with_temp_db(body)


@case
def test_write_snapshot_preserves_stronger_crowd_source():
    """A later weak (apewisdom) same-day compute must not clobber real reddit."""
    def body():
        today = datetime.now(timezone.utc).date().isoformat()
        reddit = {
            "ticker": "AAPL", "computed_at": "morning", "source": "reddit",
            "net_score": 45.0, "bull": 8, "bear": 2, "neutral": 2, "volume": 12,
            "mentions_prev": None, "upvotes": None, "rank": None,
            "top": [{"id": "x"}], "news": None,
        }
        S._write_snapshot(reddit)
        # Evening: Arctic down -> apewisdom-only, WITH some news.
        ape = {
            "ticker": "AAPL", "computed_at": "evening", "source": "apewisdom",
            "net_score": 0.0, "bull": 0, "bear": 0, "neutral": 0, "volume": 200,
            "mentions_prev": 100, "upvotes": 500, "rank": 3, "top": [],
            "news": {"net_score": 20.0, "volume": 15},
        }
        S._write_snapshot(ape)

        row = db.snapshot_get_one("AAPL", today)
        # crowd fields preserved from the stronger reddit read...
        assert row["source"] == "reddit", row["source"]
        assert row["net_score"] == 45.0 and row["volume"] == 12, row
        # ...while the fresh news signal is still recorded.
        assert row["news_net_score"] == 20.0 and row["news_volume"] == 15, row

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
