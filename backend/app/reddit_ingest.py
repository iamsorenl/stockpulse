"""Reddit mention ingestion via the Arctic Shift API (SOR-155).

Reddit disabled self-serve API keys in 2025 (Responsible Builder Policy), so
instead of PRAW we read public Reddit data through Arctic Shift
(arctic-shift.photon-reddit.com) — a free, no-key archive of posts and comments.

We over-fetch recent mentions of a ticker across finance subreddits; the
sentiment stage (SOR-156) does relevance filtering, so noisy common-word tickers
(SNOW, META) are handled downstream rather than with brittle matching here.

Results are cached per ticker in SQLite (freshness window) so repeated page
views don't re-hit the API. The network layer never raises — on maintenance,
rate-limit, or transport errors it returns [] so callers degrade gracefully.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from . import config, db

logger = logging.getLogger("stockpulse.reddit_ingest")

# Finance subreddits to sweep. Ordered by signal quality; WSB is highest-volume.
SUBREDDITS = ("stocks", "wallstreetbets", "investing", "StockMarket")

_HTTP_UA = "StockPulse/0.1 (research; +https://github.com/iamsorenl/stockpulse)"
_TIMEOUT_SECONDS = 15
_PER_SOURCE_LIMIT = 25          # posts (and comments) per subreddit per fetch
_CACHE_TTL_SECONDS = 3 * 60 * 60  # 3h: Reddit chatter moves, but not minute-to-minute


@dataclass(frozen=True)
class Mention:
    id: str
    kind: str          # "post" | "comment"
    subreddit: str
    author: str
    created_utc: float
    text: str
    score: int
    permalink: str


def _cache_key(ticker: str) -> str:
    return f"reddit:{ticker}"


def _get_json(path: str, params: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """GET {base}{path}?{params} and return the record list, or None on any failure.

    Arctic Shift wraps results as {"data": [...]} and errors (incl. "Under
    maintenance") as {"data": null, "error": "..."}.
    """
    url = f"{config.ARCTIC_SHIFT_BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("arctic-shift %s failed: %s", path, exc)
        return None
    if isinstance(payload, dict):
        if payload.get("error"):
            logger.warning("arctic-shift %s error: %s", path, payload["error"])
            return None
        data = payload.get("data")
    else:
        data = payload
    return data if isinstance(data, list) else None


def _permalink(record: dict[str, Any], subreddit: str, kind: str) -> str:
    """Prefer Reddit's own permalink; otherwise construct a working URL."""
    pl = record.get("permalink")
    if isinstance(pl, str) and pl:
        return "https://www.reddit.com" + pl if pl.startswith("/") else pl
    rid = str(record.get("id", ""))
    if kind == "post":
        return f"https://www.reddit.com/r/{subreddit}/comments/{rid}/"
    # comment: link_id looks like "t3_<postid>"
    link_id = str(record.get("link_id", ""))
    post_id = link_id.split("_", 1)[1] if "_" in link_id else link_id
    return f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/_/{rid}/"


def _to_mention(record: dict[str, Any], kind: str) -> Optional[Mention]:
    rid = record.get("id")
    if not rid:
        return None
    subreddit = str(record.get("subreddit", "") or "")
    if kind == "post":
        title = str(record.get("title", "") or "")
        selftext = str(record.get("selftext", "") or "")
        text = (title + "\n" + selftext).strip()
    else:
        text = str(record.get("body", "") or "").strip()
    if not text:
        return None
    try:
        score = int(record.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    try:
        created = float(record.get("created_utc", 0) or 0)
    except (TypeError, ValueError):
        created = 0.0
    return Mention(
        id=str(rid),
        kind=kind,
        subreddit=subreddit,
        author=str(record.get("author", "") or "[unknown]"),
        created_utc=created,
        text=text,
        score=score,
        permalink=_permalink(record, subreddit, kind),
    )


def _fetch_kind(ticker: str, subreddit: str, kind: str) -> list[Mention]:
    """Fetch posts (query=) or comments (body=) mentioning `ticker` in a subreddit."""
    text_param = "query" if kind == "post" else "body"
    path = "/posts/search" if kind == "post" else "/comments/search"
    records = _get_json(
        path,
        {
            "subreddit": subreddit,
            text_param: ticker,
            "limit": _PER_SOURCE_LIMIT,
            "sort": "desc",
        },
    )
    if not records:
        return []
    mentions = [_to_mention(r, kind) for r in records]
    return [m for m in mentions if m is not None]


def fetch_mentions(ticker: str, *, use_cache: bool = True) -> list[Mention]:
    """Return recent Reddit mentions of `ticker`, cached per ticker in SQLite.

    Sweeps posts + comments across the finance subreddits, dedupes by id, and
    returns newest-first. Returns [] if the source is unavailable (maintenance/
    rate-limit) — callers treat that as "no discussion found".
    """
    normalized = (ticker or "").strip().upper()
    if not normalized:
        return []

    key = _cache_key(normalized)
    if use_cache:
        cached = db.cache_get(key)
        if cached is not None:
            value, created_at = cached
            age = (datetime.now(timezone.utc) - created_at).total_seconds()
            if age < _CACHE_TTL_SECONDS:
                try:
                    return [Mention(**m) for m in json.loads(value)]
                except (json.JSONDecodeError, TypeError):
                    pass  # fall through and refetch

    seen: set[tuple[str, str]] = set()
    mentions: list[Mention] = []
    for subreddit in SUBREDDITS:
        for kind in ("post", "comment"):
            for m in _fetch_kind(normalized, subreddit, kind):
                dedupe_key = (m.kind, m.id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                mentions.append(m)

    mentions.sort(key=lambda m: m.created_utc, reverse=True)
    db.cache_set(key, json.dumps([asdict(m) for m in mentions]))
    return mentions
