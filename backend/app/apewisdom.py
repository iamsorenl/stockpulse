"""ApeWisdom mention-volume source (SOR-155 fallback).

When the primary text source (Arctic Shift) is unavailable, we still want to
show real Reddit crowd interest. ApeWisdom (apewisdom.io) is a free, no-key API
that aggregates how often each ticker is mentioned across finance subreddits —
mentions, upvotes, and 24h-ago comparisons. It has NO post text and NO sentiment
label, so it powers a "mention volume" view, not the LLM sentiment breakdown.

The API returns a paginated leaderboard of the ~960 most-discussed tickers (no
per-ticker lookup), so we fetch the whole board once, cache it in SQLite, and do
O(1) lookups. A ticker absent from the board simply isn't being widely discussed.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from . import db

logger = logging.getLogger("stockpulse.apewisdom")

_BASE = "https://apewisdom.io/api/v1.0/filter/all-stocks/page"
_MAX_PAGES = 10
_HTTP_UA = "StockPulse/0.1 (+https://github.com/iamsorenl/stockpulse)"
_TIMEOUT_SECONDS = 12
_CACHE_KEY = "apewisdom:board"
_CACHE_TTL_SECONDS = 60 * 60  # 1h: the leaderboard updates slowly


def _get_page(page: int) -> Optional[dict[str, Any]]:
    url = f"{_BASE}/{page}"
    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("apewisdom page %d failed: %s", page, exc)
        return None


def _fetch_board() -> dict[str, dict[str, Any]]:
    """Fetch all pages into {TICKER: {mentions, mentions_prev, upvotes, rank, name}}."""
    board: dict[str, dict[str, Any]] = {}
    for page in range(1, _MAX_PAGES + 1):
        data = _get_page(page)
        if not data:
            break
        for row in data.get("results", []):
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            board[ticker] = {
                "mentions": int(row.get("mentions", 0) or 0),
                "mentions_prev": int(row.get("mentions_24h_ago", 0) or 0),
                "upvotes": int(row.get("upvotes", 0) or 0),
                "rank": int(row.get("rank", 0) or 0),
                "name": html.unescape(str(row.get("name", "") or "")),
            }
        if page >= int(data.get("pages", page)):
            break
    return board


def _load_board() -> dict[str, dict[str, Any]]:
    """Return the cached board, refetching when stale/missing. {} if unavailable."""
    cached = db.cache_get(_CACHE_KEY)
    if cached is not None:
        value, created_at = cached
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        if age < _CACHE_TTL_SECONDS:
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass  # corrupt entry: refetch

    board = _fetch_board()
    if board:  # never cache an empty board (that would mask a transient outage)
        db.cache_set(_CACHE_KEY, json.dumps(board))
    return board


def get_stats(ticker: str) -> Optional[dict[str, Any]]:
    """Return {mentions, mentions_prev, upvotes, rank, name} for `ticker`, or None.

    None means the ticker isn't on ApeWisdom's board (not widely discussed) or the
    source is unreachable.
    """
    normalized = (ticker or "").strip().upper()
    if not normalized:
        return None
    return _load_board().get(normalized)
