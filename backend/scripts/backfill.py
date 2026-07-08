"""Backfill historical sentiment snapshots for a ticker + date range (SOR-163).

    ./.venv/bin/python -m scripts.backfill --ticker AAPL --start 2026-06-01 --end 2026-06-30

Reconstructs each UTC day's Reddit sentiment via Arctic Shift date windows and
stores it with captured='backfill'. Idempotent; a day that already has live data
is never overwritten. Needs Arctic Shift to be up (currently under maintenance).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import backfill, db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical sentiment snapshots.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--start", required=True, help="First UTC day, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last UTC day, YYYY-MM-DD")
    args = parser.parse_args()

    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError:
        print("Dates must be YYYY-MM-DD.")
        return 1
    if start > end:
        print("--start must be on or before --end.")
        return 1

    db.init_db()
    summary = backfill.backfill_range(args.ticker, start, end)
    print(f"{summary['ticker']}: wrote {summary['written']}, skipped {summary['skipped']} "
          f"({summary['start']}..{summary['end']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
