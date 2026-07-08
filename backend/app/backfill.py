"""Historical sentiment backfill via Arctic Shift date windows (SOR-162/163/164).

Arctic Shift's /posts/search + /comments/search accept `after`/`before` date
bounds, so backfilling a past day is the same ingest pipeline as live — just
scoped to that UTC day. Each day's mentions are scored by the existing Groq
pipeline and upserted into sentiment_snapshots with captured='backfill', so the
timeline can distinguish reconstructed history from live-captured days.

Idempotent + safe: a day that already has a LIVE snapshot is never overwritten
by backfill (live data wins). Re-running a range is safe (upsert by ticker+date).

NOTE: this needs Arctic Shift to be up. It's built against the documented API
and fixture-tested; the live run is the one step deferred until the source
returns from maintenance.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from . import db, reddit_ingest, sentiment

logger = logging.getLogger("stockpulse.backfill")

_PER_SOURCE_LIMIT = 100          # Arctic max per request
_COURTESY_SLEEP_SECONDS = 0.5    # between day-windows, to respect rate limits


def fetch_day_mentions(ticker: str, day: date) -> list[reddit_ingest.Mention]:
    """Fetch posts + comments mentioning `ticker` created on `day` (UTC)."""
    after = day.isoformat()
    before = (day + timedelta(days=1)).isoformat()
    seen: set[tuple[str, str]] = set()
    mentions: list[reddit_ingest.Mention] = []
    for subreddit in reddit_ingest.SUBREDDITS:
        for kind in ("post", "comment"):
            text_param = "query" if kind == "post" else "body"
            path = "/posts/search" if kind == "post" else "/comments/search"
            records = reddit_ingest._get_json(path, {
                "subreddit": subreddit, text_param: ticker,
                "after": after, "before": before,
                "limit": _PER_SOURCE_LIMIT, "sort": "desc",
            })
            for record in records or []:
                m = reddit_ingest._to_mention(record, kind)
                if m is None:
                    continue
                key = (m.kind, m.id)
                if key in seen:
                    continue
                seen.add(key)
                mentions.append(m)
    return mentions


def backfill_day(ticker: str, day: date) -> Optional[dict[str, Any]]:
    """Backfill one day's sentiment snapshot. Returns the row, or None if skipped.

    Skips (returns None) when the day already has a live snapshot or when no
    mentions were found for the day.
    """
    normalized = (ticker or "").strip().upper()
    day_str = day.isoformat()

    existing = db.snapshot_get_one(normalized, day_str)
    if existing and existing.get("captured") == "live":
        return None  # never overwrite live-captured data with backfill

    mentions = fetch_day_mentions(normalized, day)
    if not mentions:
        return None

    result = sentiment.score_mentions(normalized, mentions)
    if result.volume == 0:
        return None  # nothing relevant that day

    row = {
        "ticker": normalized, "date": day_str,
        "computed_at": datetime.now(timezone.utc).isoformat(), "source": "reddit",
        "net_score": result.net_score, "bull": result.bull, "bear": result.bear,
        "neutral": result.neutral, "volume": result.volume,
        "mentions_prev": None, "upvotes": None, "rank": None,
        "top_json": json.dumps(sentiment.result_to_dict(result)["top"]),
        "news_net_score": None, "news_volume": None, "captured": "backfill",
    }
    db.snapshot_upsert(row)
    return row


def backfill_range(ticker: str, start: date, end: date) -> dict[str, Any]:
    """Backfill each UTC day in [start, end]. Returns a summary of what happened.

    One failing day is logged and skipped — it never aborts the whole run.
    """
    normalized = (ticker or "").strip().upper()
    written = 0
    skipped = 0
    day = start
    while day <= end:
        try:
            row = backfill_day(normalized, day)
            if row is not None:
                written += 1
                logger.info("backfilled %s %s: net=%s vol=%s",
                            normalized, day, row["net_score"], row["volume"])
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001 - one bad day must not abort the run
            skipped += 1
            logger.warning("backfill failed for %s %s: %s", normalized, day, exc)
        day += timedelta(days=1)
        if day <= end:
            time.sleep(_COURTESY_SLEEP_SECONDS)
    return {"ticker": normalized, "written": written, "skipped": skipped,
            "start": start.isoformat(), "end": end.isoformat()}
