"""Capture today's sentiment snapshot for a set of tracked tickers (SOR-161).

Snapshots normally accumulate only when someone views a ticker in the app. Run
this by hand, or on a schedule (cron/launchd), to capture tracked tickers daily
without a page visit — so the M3 timeline keeps filling in even for tickers
nobody happened to look at.

    ./.venv/bin/python -m scripts.snapshot_tickers --tickers AAPL,NVDA,TSLA
    STOCKPULSE_TRACKED_TICKERS=AAPL,NVDA ./.venv/bin/python -m scripts.snapshot_tickers

Each ticker runs through app.sentiment.get_sentiment, which upserts today's row.
One failing ticker is logged and skipped — it never aborts the run.

Optional daily cron (install yourself; we don't touch your crontab):
    0 23 * * *  cd /path/to/stockpulse/backend && STOCKPULSE_TRACKED_TICKERS=AAPL,NVDA,TSLA ./.venv/bin/python -m scripts.snapshot_tickers >> snapshots.log 2>&1
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, sentiment  # noqa: E402

_COURTESY_SLEEP_SECONDS = 1.0  # pause between tickers to respect upstream rate limits


def _resolve_tickers(arg: str | None) -> list[str]:
    raw = arg if arg is not None else os.environ.get("STOCKPULSE_TRACKED_TICKERS", "")
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot sentiment for tracked tickers.")
    parser.add_argument(
        "--tickers",
        help="Comma-separated tickers; overrides STOCKPULSE_TRACKED_TICKERS.",
    )
    args = parser.parse_args()

    tickers = _resolve_tickers(args.tickers)
    if not tickers:
        print("No tickers. Pass --tickers AAPL,NVDA or set STOCKPULSE_TRACKED_TICKERS.")
        return 1

    if not config.SENTIMENT_CONFIGURED:
        print("Note: GROQ_API_KEY not set — post-level sentiment is skipped; "
              "ApeWisdom mention volume still records.")

    db.init_db()
    ok = 0
    for i, ticker in enumerate(tickers):
        try:
            data = sentiment.get_sentiment(ticker)
            print(f"{ticker}: source={data['source']} volume={data['volume']} "
                  f"net={data['net_score']}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not abort the run
            print(f"{ticker}: ERROR {type(exc).__name__}: {exc}")
        if i < len(tickers) - 1:
            time.sleep(_COURTESY_SLEEP_SECONDS)

    print(f"\n{ok}/{len(tickers)} tickers snapshotted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
