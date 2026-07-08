"""Fixture-based tests for news RSS ingestion (SOR-165). No network, no keys.

Run from backend/:
    ./.venv/bin/python -m tests.test_news_ingest
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import news_ingest as news  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _isolate_cache():
    news.db.cache_get = lambda key: None
    news.db.cache_set = lambda key, value: None


_YAHOO = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Apple hits new high</title>
    <link>https://finance.yahoo.com/news/a1</link>
    <description>&lt;p&gt;Strong &lt;b&gt;earnings&lt;/b&gt; beat&lt;/p&gt;</description>
    <pubDate>Wed, 08 Jul 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Second story</title>
    <link>https://finance.yahoo.com/news/a2</link>
    <description>plain summary</description>
    <pubDate>Tue, 07 Jul 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Malicious link</title>
    <link>javascript:alert(1)</link>
    <description>x</description>
  </item>
</channel></rss>"""

_GOOGLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Apple analysis - CNBC</title>
    <link>https://news.google.com/g1</link>
    <source url="https://cnbc.com">CNBC</source>
    <pubDate>Mon, 06 Jul 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Second Story</title>
    <link>https://news.google.com/g2</link>
    <pubDate>Sun, 05 Jul 2026 12:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


def _stub_feeds():
    news._get = lambda url: _YAHOO if "yahoo.com" in url else _GOOGLE


@case
def test_parses_both_feeds_strips_html_and_sets_outlet():
    _isolate_cache()
    _stub_feeds()
    arts = {a.url: a for a in news.fetch_articles("AAPL", use_cache=False)}
    a1 = arts["https://finance.yahoo.com/news/a1"]
    assert a1.title == "Apple hits new high", a1.title
    assert a1.summary == "Strong earnings beat", a1.summary  # tags + entities stripped
    assert a1.outlet == "Yahoo Finance", a1.outlet
    g1 = arts["https://news.google.com/g1"]
    assert g1.outlet == "CNBC", g1.outlet  # from <source> element


@case
def test_drops_non_http_links():
    _isolate_cache()
    _stub_feeds()
    urls = [a.url for a in news.fetch_articles("AAPL", use_cache=False)]
    assert all(u.startswith("https://") for u in urls), urls
    assert not any("javascript" in u for u in urls), urls


@case
def test_dedupes_by_title_across_feeds():
    _isolate_cache()
    _stub_feeds()
    # Yahoo "Second story" and Google "Second Story" are the same title (case-insensitive)
    titles = [a.title.lower() for a in news.fetch_articles("AAPL", use_cache=False)]
    assert titles.count("second story") == 1, titles


@case
def test_sorted_newest_first():
    _isolate_cache()
    _stub_feeds()
    arts = news.fetch_articles("AAPL", use_cache=False)
    ts = [a.published_utc for a in arts]
    assert ts == sorted(ts, reverse=True), ts
    assert arts[0].url.endswith("/a1"), arts[0].url  # Jul 08 is newest


@case
def test_all_feeds_down_yields_empty():
    _isolate_cache()
    news._get = lambda url: None
    assert news.fetch_articles("AAPL", use_cache=False) == []


@case
def test_xxe_billion_laughs_is_rejected():
    # An entity-expansion attack must be refused by defusedxml, not expanded.
    xxe = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<rss version="2.0"><channel><item><title>&lol3;</title>
<link>https://x/1</link></item></channel></rss>"""
    # _parse_feed must return [] rather than raise or expand.
    assert news._parse_feed(xxe, "X") == [], "entity attack must be rejected"


@case
def test_malformed_xml_yields_empty():
    assert news._parse_feed("<rss><channel><item", "X") == []


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
