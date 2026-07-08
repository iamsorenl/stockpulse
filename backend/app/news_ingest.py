"""News article ingestion per ticker via free RSS feeds (SOR-165).

Two keyless sources: Yahoo Finance RSS (structured, ticker-scoped headlines) and
Google News RSS (broader web coverage). Parsed with the stdlib xml.etree — no
new dependencies. The network layer never raises: on a malformed feed or
transport error it yields [], so callers degrade to "no news".

Results are cached per ticker in SQLite (freshness window), like the other
sources. Article URLs are rendered later in <a href>, so only http(s) links are
kept (defense-in-depth against a feed injecting a javascript: link).
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as SafeET  # XXE / billion-laughs-safe parsing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from . import db

logger = logging.getLogger("stockpulse.news_ingest")

_HTTP_UA = "Mozilla/5.0 (StockPulse; +https://github.com/iamsorenl/stockpulse)"
_TIMEOUT_SECONDS = 12
_CACHE_TTL_SECONDS = 60 * 60  # 1h
_MAX_ARTICLES = 30
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    summary: str
    url: str
    published_utc: float   # epoch seconds; 0.0 if unknown
    outlet: str


def _cache_key(ticker: str) -> str:
    return f"news:{ticker}"


def _feeds(ticker: str) -> list[tuple[str, str]]:
    """(url, default_outlet) pairs for a ticker."""
    q = urllib.parse.quote(ticker, safe="")
    return [
        (f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={q}&region=US&lang=en-US",
         "Yahoo Finance"),
        (f"https://news.google.com/rss/search?q={q}+stock&hl=en-US&gl=US&ceid=US:en",
         "Google News"),
    ]


def _get(url: str) -> Optional[str]:
    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("news feed fetch failed (%s): %s", url[:60], exc)
        return None


def _clean_text(raw: str) -> str:
    return html.unescape(_TAG_RE.sub("", raw or "")).strip()


def _parse_pubdate(raw: str) -> float:
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, IndexError):
        return 0.0


def _parse_feed(xml_text: str, default_outlet: str) -> list[Article]:
    """Parse an RSS 2.0 document into Articles. Returns [] on any parse error."""
    try:
        # defusedxml raises DefusedXmlException (a ValueError) on entity attacks.
        root = SafeET.fromstring(xml_text)
    except (ParseError, ValueError) as exc:
        logger.warning("news feed parse error: %s", exc)
        return []

    articles: list[Article] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue  # only real http(s) links (rendered in an href later)
        title = _clean_text(item.findtext("title") or "")
        if not title:
            continue
        summary = _clean_text(item.findtext("description") or "")
        # Google News carries the outlet in a <source> element; Yahoo doesn't.
        source_el = item.find("source")
        outlet = (source_el.text.strip() if source_el is not None and source_el.text
                  else default_outlet)
        articles.append(Article(
            id=link, title=title, summary=summary, url=link,
            published_utc=_parse_pubdate(item.findtext("pubDate") or ""),
            outlet=outlet,
        ))
    return articles


def fetch_articles(ticker: str, *, use_cache: bool = True) -> list[Article]:
    """Return recent news articles about `ticker`, deduped and cached.

    Sweeps both feeds, dedups by URL and by (lowercased) title, newest-first,
    capped. Returns [] if every feed is unreachable/empty.
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
                    return [Article(**a) for a in json.loads(value)]
                except (json.JSONDecodeError, TypeError):
                    pass

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    articles: list[Article] = []
    for url, outlet in _feeds(normalized):
        text = _get(url)
        if not text:
            continue
        for art in _parse_feed(text, outlet):
            title_key = art.title.lower()
            if art.url in seen_urls or title_key in seen_titles:
                continue
            seen_urls.add(art.url)
            seen_titles.add(title_key)
            articles.append(art)

    articles.sort(key=lambda a: a.published_utc, reverse=True)
    articles = articles[:_MAX_ARTICLES]
    db.cache_set(key, json.dumps([asdict(a) for a in articles]))
    return articles
