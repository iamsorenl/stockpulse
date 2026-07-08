"""LLM sentiment scoring for Reddit mentions (SOR-156).

Groq (free tier) is the primary scorer; an optional local Ollama is the
fallback. Each mention is classified for two things:
  * relevance  - is it actually about THIS ticker? (drops common-word noise
                 like SNOW/META without brittle string matching)
  * sentiment  - bullish / bearish / neutral toward the stock

Mentions are batched per LLM call to stay well inside free-tier limits. Relevant
mentions are aggregated into a net score, a bull/bear/neutral breakdown, and a
volume count. The LLM layer never raises — on error it returns None and the
batch is skipped, so a flaky model degrades the sample rather than crashing.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import config, db
from .apewisdom import get_stats as get_mention_stats
from .news_ingest import Article, fetch_articles
from .reddit_ingest import Mention, fetch_mentions

logger = logging.getLogger("stockpulse.sentiment")

# Groq sits behind a WAF that 403s the default python-urllib User-Agent, so we
# send an explicit one on every LLM request.
_HTTP_UA = "StockPulse/0.1 (+https://github.com/iamsorenl/stockpulse)"

_BATCH_SIZE = 8              # mentions per LLM call
_MAX_MENTIONS = 64          # cap work per ticker (keeps latency + tokens bounded)
_TEXT_CLIP = 400            # chars of each mention shown to the model
_TOP_N = 5                  # representative posts surfaced to the UI
_HTTP_TIMEOUT = 30

_SENTIMENTS = ("bullish", "bearish", "neutral")

# Sentiment freshness window, per data source. Full text sentiment (Reddit
# posts -> LLM) is expensive and stable, so it's memoized for an hour. The
# ApeWisdom volume fallback gets a shorter window so we re-probe the richer text
# source sooner, and an outright miss ("none") expires fast so an upstream
# outage self-heals within minutes of recovery.
_TTL_BY_SOURCE = {"reddit": 60 * 60, "apewisdom": 15 * 60, "none": 5 * 60}
_CACHE_TTL_SECONDS = _TTL_BY_SOURCE["reddit"]  # default / back-compat

_SYSTEM_PROMPT = (
    "You are a precise financial sentiment classifier. You are given a stock "
    "ticker and a numbered list of Reddit texts. For EACH text decide: "
    "(1) relevant - is the text actually discussing that specific stock/company? "
    "(2) sentiment toward the stock - one of bullish, bearish, neutral. "
    'Respond ONLY with a JSON object of the form '
    '{"results":[{"i":<number>,"relevant":<true|false>,'
    '"sentiment":"bullish|bearish|neutral"}]} with one entry per input.'
)


@dataclass
class ScoredMention:
    id: str
    kind: str
    subreddit: str
    score: int
    permalink: str
    text: str
    sentiment: str


@dataclass
class SentimentResult:
    ticker: str
    net_score: float          # -100 (all bearish) .. +100 (all bullish)
    bull: int
    bear: int
    neutral: int
    volume: int               # reddit: relevant mentions scored; apewisdom: total mentions
    computed_at: str          # ISO-8601 UTC
    top: list[ScoredMention] = field(default_factory=list)
    # Which source produced this result:
    #   "reddit"    - post text scored by the LLM (net_score/breakdown/top valid)
    #   "apewisdom" - mention-volume fallback (volume + mentions_prev/upvotes/rank)
    #   "none"      - nothing available (all zero)
    source: str = "reddit"
    mentions_prev: Optional[int] = None   # apewisdom: mentions 24h ago
    upvotes: Optional[int] = None         # apewisdom: total upvotes
    rank: Optional[int] = None            # apewisdom: trending rank


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _groq_complete(system: str, user: str) -> Optional[str]:
    if not config.GROQ_CONFIGURED:
        return None
    body = json.dumps(
        {
            "model": config.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": _HTTP_UA,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return payload["choices"][0]["message"]["content"]
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
        logger.warning("groq completion failed: %s", exc)
        return None


def _ollama_complete(system: str, user: str) -> Optional[str]:
    if not config.OLLAMA_ENABLED:
        return None
    body = json.dumps(
        {
            "model": config.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{config.OLLAMA_BASE_URL}/api/chat",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _HTTP_UA},
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return payload["message"]["content"]
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
        logger.warning("ollama completion failed: %s", exc)
        return None


def _llm_json(system: str, user: str) -> Optional[dict[str, Any]]:
    """Complete via Groq (then Ollama) and parse the JSON object, or None."""
    raw = _groq_complete(system, user) or _ollama_complete(system, user)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        logger.warning("LLM returned non-JSON content")
        return None


def _classify_batch(ticker: str, texts: list[str]) -> dict[int, str]:
    """Return {local_index: sentiment} for RELEVANT texts in the batch.

    Works for any short text — Reddit mentions or news headlines — so both
    sources share one LLM classifier.
    """
    lines = [
        f"{i}. {(t or '').replace(chr(10), ' ').strip()[:_TEXT_CLIP]}"
        for i, t in enumerate(texts)
    ]
    user = f'Ticker: {ticker}\nTexts:\n' + "\n".join(lines)

    parsed = _llm_json(_SYSTEM_PROMPT, user)
    if not parsed:
        return {}
    out: dict[int, str] = {}
    for entry in parsed.get("results", []):
        if not isinstance(entry, dict) or not entry.get("relevant"):
            continue
        idx = entry.get("i")
        sentiment = entry.get("sentiment")
        if isinstance(idx, int) and 0 <= idx < len(texts) and sentiment in _SENTIMENTS:
            out[idx] = sentiment
    return out


def score_mentions(ticker: str, mentions: list[Mention]) -> SentimentResult:
    """Classify + aggregate mentions into a SentimentResult.

    Irrelevant mentions are excluded. With no relevant mentions (or no LLM
    available) the result is a well-formed zero: net_score 0, empty breakdown.
    """
    normalized = (ticker or "").strip().upper()
    subset = mentions[:_MAX_MENTIONS]

    scored: list[ScoredMention] = []
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for start in range(0, len(subset), _BATCH_SIZE):
        batch = subset[start : start + _BATCH_SIZE]
        for idx, sentiment in _classify_batch(normalized, [m.text for m in batch]).items():
            m = batch[idx]
            counts[sentiment] += 1
            scored.append(
                ScoredMention(
                    id=m.id, kind=m.kind, subreddit=m.subreddit, score=m.score,
                    permalink=m.permalink, text=m.text[:_TEXT_CLIP], sentiment=sentiment,
                )
            )

    volume = sum(counts.values())
    net = round((counts["bullish"] - counts["bearish"]) / volume * 100, 1) if volume else 0.0
    # Surface the highest-upvoted relevant mentions as representative posts.
    top = sorted(scored, key=lambda s: s.score, reverse=True)[:_TOP_N]

    return SentimentResult(
        ticker=normalized,
        net_score=net,
        bull=counts["bullish"],
        bear=counts["bearish"],
        neutral=counts["neutral"],
        volume=volume,
        computed_at=_now_iso(),
        top=top,
    )


def result_to_dict(result: SentimentResult) -> dict[str, Any]:
    """JSON-serializable dict (top mentions expanded)."""
    data = asdict(result)
    return data


# ----- News sentiment (M5: SOR-166) --------------------------------------------


@dataclass
class ScoredArticle:
    title: str
    outlet: str
    url: str
    published_utc: float
    sentiment: str


@dataclass
class NewsSentiment:
    net_score: float
    bull: int
    bear: int
    neutral: int
    volume: int
    computed_at: str
    top: list[ScoredArticle] = field(default_factory=list)


def score_articles(ticker: str, articles: list[Article]) -> NewsSentiment:
    """Classify + aggregate news articles into a NewsSentiment (same LLM path)."""
    normalized = (ticker or "").strip().upper()
    subset = articles[:_MAX_MENTIONS]

    scored: list[ScoredArticle] = []
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for start in range(0, len(subset), _BATCH_SIZE):
        batch = subset[start : start + _BATCH_SIZE]
        texts = [f"{a.title}. {a.summary}" for a in batch]
        for idx, sentiment in _classify_batch(normalized, texts).items():
            a = batch[idx]
            counts[sentiment] += 1
            scored.append(ScoredArticle(
                title=a.title, outlet=a.outlet, url=a.url,
                published_utc=a.published_utc, sentiment=sentiment,
            ))

    volume = sum(counts.values())
    net = round((counts["bullish"] - counts["bearish"]) / volume * 100, 1) if volume else 0.0
    top = sorted(scored, key=lambda s: s.published_utc, reverse=True)[:_TOP_N]
    return NewsSentiment(
        net_score=net, bull=counts["bullish"], bear=counts["bearish"],
        neutral=counts["neutral"], volume=volume, computed_at=_now_iso(), top=top,
    )


def _cache_key(ticker: str) -> str:
    return f"sentiment:{ticker}"


def get_sentiment(ticker: str) -> dict[str, Any]:
    """Return the sentiment dict for `ticker`, cache-first.

    Serves a fresh cached result (within `_CACHE_TTL_SECONDS`) when available;
    otherwise fetches Reddit mentions, scores them, caches the JSON, and returns
    it. Depends on the module-level `fetch_mentions`, `score_mentions`, and `db`
    so tests can monkeypatch them without touching the network or SQLite.

    A `volume` of 0 ("no discussion found") is a valid, cacheable result.
    """
    normalized = (ticker or "").strip().upper()
    key = _cache_key(normalized)

    cached = db.cache_get(key)
    if cached is not None:
        value, created_at = cached
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        try:
            payload = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            payload = None  # corrupt cache entry: fall through and recompute
        if payload is not None:
            ttl = _TTL_BY_SOURCE.get(payload.get("source", "reddit"), _CACHE_TTL_SECONDS)
            if age < ttl:
                return payload

    # Primary: real Reddit post text -> LLM sentiment.
    mentions = fetch_mentions(normalized)
    if mentions:
        result = score_mentions(normalized, mentions)  # source defaults to "reddit"
        if result.volume == 0:
            result.source = "none"  # posts found but none relevant -> empty state
    else:
        # Fallback: ApeWisdom mention-volume (no post text, so no LLM sentiment).
        stats = get_mention_stats(normalized)
        if stats:
            result = SentimentResult(
                ticker=normalized, net_score=0.0, bull=0, bear=0, neutral=0,
                volume=stats["mentions"], computed_at=_now_iso(), top=[],
                source="apewisdom", mentions_prev=stats.get("mentions_prev"),
                upvotes=stats.get("upvotes"), rank=stats.get("rank"),
            )
        else:
            result = SentimentResult(
                ticker=normalized, net_score=0.0, bull=0, bear=0, neutral=0,
                volume=0, computed_at=_now_iso(), top=[], source="none",
            )

    data = result_to_dict(result)

    # News sentiment — independent of Reddit, so it works even while Arctic Shift
    # is down. Combined score blends the two, shown transparently (50/50).
    articles = fetch_articles(normalized)
    news = score_articles(normalized, articles) if articles else None
    data["news"] = asdict(news) if news is not None else None
    data["combined"] = _combined(data, news)

    db.cache_set(key, json.dumps(data))
    _write_snapshot(data)
    return data


def _combined(crowd: dict[str, Any], news: Optional[NewsSentiment]) -> dict[str, Any]:
    """Blend crowd + news net scores 50/50, keeping each side transparent.

    Crowd sentiment counts only when it's real LLM-scored Reddit text (source
    'reddit' with volume) — ApeWisdom volume carries no sentiment. Whichever
    sides have signal are averaged equally; the flags let the UI show the split.
    """
    crowd_net = (
        crowd["net_score"]
        if crowd.get("source") == "reddit" and crowd.get("volume", 0) > 0
        else None
    )
    news_net = news.net_score if news is not None and news.volume > 0 else None
    parts = [x for x in (crowd_net, news_net) if x is not None]
    net = round(sum(parts) / len(parts), 1) if parts else 0.0
    return {"net_score": net, "has_reddit": crowd_net is not None, "has_news": news_net is not None}


def _write_snapshot(data: dict[str, Any]) -> None:
    """Persist a fresh compute as today's (ticker, UTC date) snapshot (M3 timeline).

    Skips source == "none" (no signal is not zero sentiment). Best-effort: a
    snapshot failure is logged but never breaks the sentiment response.
    """
    news = data.get("news") or {}
    has_news = bool(news.get("volume", 0))
    # Snapshot when there's crowd signal OR news signal (either builds the timeline).
    if data.get("source") not in ("reddit", "apewisdom") and not has_news:
        return
    row = {
        "ticker": data["ticker"],
        "date": datetime.now(timezone.utc).date().isoformat(),
        "computed_at": data["computed_at"],
        "source": data["source"],
        "net_score": data["net_score"],
        "bull": data["bull"],
        "bear": data["bear"],
        "neutral": data["neutral"],
        "volume": data["volume"],
        "mentions_prev": data.get("mentions_prev"),
        "upvotes": data.get("upvotes"),
        "rank": data.get("rank"),
        "top_json": json.dumps(data.get("top", [])),
        "news_net_score": news.get("net_score") if has_news else None,
        "news_volume": news.get("volume") if has_news else None,
    }
    try:
        _merge_with_existing_snapshot(row, has_news)
        db.snapshot_upsert(row)
    except Exception as exc:  # noqa: BLE001 - snapshotting must never break the response
        logger.warning("snapshot upsert failed for %s: %s", data.get("ticker"), exc)


# Higher tier = stronger crowd signal. A weaker same-day compute must not clobber
# a stronger stored one (e.g. an evening ApeWisdom-only read overwriting the
# morning's real Reddit sentiment for the date).
_SOURCE_RANK = {"reddit": 2, "apewisdom": 1, "none": 0}


def _merge_with_existing_snapshot(row: dict[str, Any], has_news: bool) -> None:
    """Mutate `row` in place to preserve the day's best crowd + news signal.

    If today's row already holds a stronger crowd source, keep its crowd fields
    (only news gets refreshed). If this compute has no news but the stored row
    did, keep the stored news. Prevents a later low-quality compute from
    destroying earlier real sentiment for the same UTC day.
    """
    existing = db.snapshot_get_one(row["ticker"], row["date"])
    if not existing:
        return
    if _SOURCE_RANK.get(existing["source"], 0) > _SOURCE_RANK.get(row["source"], 0):
        for field_name in (
            "source", "net_score", "bull", "bear", "neutral", "volume",
            "mentions_prev", "upvotes", "rank", "top_json",
        ):
            row[field_name] = existing[field_name]
    if not has_news and existing.get("news_volume"):
        row["news_net_score"] = existing["news_net_score"]
        row["news_volume"] = existing["news_volume"]


# ----- Timeline reads (M3: SOR-159 history, SOR-160 on-this-day) ----------------

_MAX_HISTORY_DAYS = 1825  # ~5y, matching the longest price range


def get_history(ticker: str, days: int = 90) -> dict[str, Any]:
    """Return the sentiment snapshot series for `ticker` over the last `days`."""
    normalized = (ticker or "").strip().upper()
    days = max(1, min(int(days), _MAX_HISTORY_DAYS))
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    points = [
        {
            "date": r["date"], "source": r["source"], "net_score": r["net_score"],
            "volume": r["volume"], "bull": r["bull"], "bear": r["bear"], "neutral": r["neutral"],
        }
        for r in db.snapshots_get(normalized, since)
    ]
    return {"ticker": normalized, "points": points}


def _snapshot_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    try:
        top = json.loads(row.get("top_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        top = []
    return {
        "date": row["date"], "computed_at": row["computed_at"], "source": row["source"],
        "net_score": row["net_score"], "bull": row["bull"], "bear": row["bear"],
        "neutral": row["neutral"], "volume": row["volume"],
        "mentions_prev": row.get("mentions_prev"), "upvotes": row.get("upvotes"),
        "rank": row.get("rank"), "top": top,
    }


def get_on_this_day(ticker: str, date_str: str) -> dict[str, Any]:
    """Return the snapshot for `date_str` (YYYY-MM-DD) + the prior 7 days' run-up.

    Raises ValueError on a malformed date (the route maps that to HTTP 400). A
    day with no snapshot returns snapshot=None (not an error).
    """
    normalized = (ticker or "").strip().upper()
    target = datetime.strptime(date_str, "%Y-%m-%d").date()  # ValueError -> 400
    day = target.isoformat()

    row = db.snapshot_get_one(normalized, day)
    snapshot = _snapshot_row_to_dict(row) if row is not None else None

    runup_since = (target - timedelta(days=7)).isoformat()
    runup = [
        {"date": r["date"], "source": r["source"],
         "net_score": r["net_score"], "volume": r["volume"]}
        for r in db.snapshots_get(normalized, runup_since)
        if r["date"] < day
    ]
    return {"date": day, "snapshot": snapshot, "runup": runup}
