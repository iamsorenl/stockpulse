"""Fixture-based tests for the sentiment service (SOR-157).

No network, no LLM, no SQLite. Exercises sentiment.get_sentiment directly by
monkeypatching fetch_mentions, score_mentions, and the db cache. Run from
backend/:
    ./.venv/bin/python -m tests.test_sentiment_endpoint
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import sentiment as sent  # noqa: E402
from app.sentiment import ScoredMention, SentimentResult  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


class _FakeCache:
    """In-memory stand-in for app.db, stamping created_at on set."""

    def __init__(self, seed=None):
        # key -> (value, created_at)
        self.store = dict(seed or {})
        self.get_calls = 0
        self.set_calls = 0
        self.snapshots = []  # rows passed to snapshot_upsert

    def cache_get(self, key):
        self.get_calls += 1
        return self.store.get(key)

    def cache_set(self, key, value):
        self.set_calls += 1
        self.store[key] = (value, datetime.now(timezone.utc))

    def snapshot_upsert(self, row):
        self.snapshots.append(row)


def _install(monkey_cache, fetch_fn, score_fn):
    """Swap module-level dependencies; return the original tuple to restore.

    News fetching is stubbed to [] by default so get_sentiment never hits the
    network for articles during these tests.
    """
    original = (sent.db, sent.fetch_mentions, sent.score_mentions, sent.fetch_articles)
    sent.db = monkey_cache
    sent.fetch_mentions = fetch_fn
    sent.score_mentions = score_fn
    sent.fetch_articles = lambda ticker: []
    return original


def _restore(original):
    sent.db, sent.fetch_mentions, sent.score_mentions, sent.fetch_articles = original


def _sample_result(ticker="AAPL", volume=16):
    return SentimentResult(
        ticker=ticker,
        net_score=12.5,
        bull=8,
        bear=5,
        neutral=3,
        volume=volume,
        computed_at="2026-07-08T00:34:59.477350+00:00",
        top=[
            ScoredMention(
                id="abc", kind="post", subreddit="stocks", score=120,
                permalink="https://www.reddit.com/r/stocks/comments/abc/",
                text="AAPL to the moon", sentiment="bullish",
            )
        ],
    )


@case
def test_cache_miss_computes_and_caches():
    cache = _FakeCache()
    fetch_calls = {"n": 0}
    score_calls = {"n": 0}

    def fetch(ticker):
        fetch_calls["n"] += 1
        assert ticker == "AAPL", ticker  # normalized to upper
        return ["m1", "m2"]

    def score(ticker, mentions):
        score_calls["n"] += 1
        assert ticker == "AAPL", ticker
        assert mentions == ["m1", "m2"], mentions
        return _sample_result()

    original = _install(cache, fetch, score)
    try:
        data = sent.get_sentiment("aapl")  # lower-case in
    finally:
        _restore(original)

    assert fetch_calls["n"] == 1, fetch_calls
    assert score_calls["n"] == 1, score_calls
    # Wrote to cache under the normalized key.
    assert cache.set_calls == 1, cache.set_calls
    assert "sentiment:AAPL" in cache.store, list(cache.store)
    stored_value, _ = cache.store["sentiment:AAPL"]
    assert json.loads(stored_value) == data, "cached JSON must round-trip to the returned dict"
    assert data["ticker"] == "AAPL", data


@case
def test_cache_hit_skips_recompute():
    fresh = json.dumps(sent.result_to_dict(_sample_result(volume=16)))
    cache = _FakeCache(seed={"sentiment:AAPL": (fresh, datetime.now(timezone.utc))})
    fetch_calls = {"n": 0}
    score_calls = {"n": 0}

    def fetch(ticker):
        fetch_calls["n"] += 1
        return []

    def score(ticker, mentions):
        score_calls["n"] += 1
        return _sample_result()

    original = _install(cache, fetch, score)
    try:
        data = sent.get_sentiment("AAPL")
    finally:
        _restore(original)

    # Fresh hit: neither dependency invoked, nothing re-written.
    assert fetch_calls["n"] == 0, fetch_calls
    assert score_calls["n"] == 0, score_calls
    assert cache.set_calls == 0, cache.set_calls
    assert data == json.loads(fresh), data


@case
def test_stale_cache_recomputes():
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=sent._CACHE_TTL_SECONDS + 60)
    old = json.dumps(sent.result_to_dict(_sample_result(volume=99)))
    cache = _FakeCache(seed={"sentiment:AAPL": (old, stale_at)})
    fetch_calls = {"n": 0}

    def fetch(ticker):
        fetch_calls["n"] += 1
        return ["m1"]

    def score(ticker, mentions):
        return _sample_result(volume=16)

    original = _install(cache, fetch, score)
    try:
        data = sent.get_sentiment("AAPL")
    finally:
        _restore(original)

    assert fetch_calls["n"] == 1, "stale entry must trigger a recompute"
    assert data["volume"] == 16, data  # fresh value, not the stale 99
    assert cache.set_calls == 1, cache.set_calls


@case
def test_response_shape():
    cache = _FakeCache()

    def fetch(ticker):
        return ["m1"]   # non-empty -> reddit text path

    def score(ticker, mentions):
        return _sample_result()

    original = _install(cache, fetch, score)
    try:
        data = sent.get_sentiment("AAPL")
    finally:
        _restore(original)

    for key in ("ticker", "net_score", "bull", "bear", "neutral", "volume",
                "computed_at", "top", "source", "mentions_prev", "upvotes", "rank"):
        assert key in data, f"missing top-level key {key!r}: {data}"
    assert data["source"] == "reddit", data["source"]
    assert isinstance(data["net_score"], float), data["net_score"]
    assert isinstance(data["volume"], int), data["volume"]
    assert isinstance(data["top"], list) and data["top"], data["top"]
    m = data["top"][0]
    for key in ("id", "kind", "subreddit", "score", "permalink", "text", "sentiment"):
        assert key in m, f"missing mention key {key!r}: {m}"
    assert m["kind"] in ("post", "comment"), m["kind"]
    assert m["sentiment"] in ("bullish", "bearish", "neutral"), m["sentiment"]


@case
def test_falls_back_to_apewisdom_when_no_mentions():
    cache = _FakeCache()
    orig_stats = sent.get_mention_stats
    # fetch_mentions empty (Arctic down) -> ApeWisdom volume fallback.
    original = _install(cache, lambda t: [], lambda t, m: _sample_result())
    sent.get_mention_stats = lambda t: {
        "mentions": 216, "mentions_prev": 118, "upvotes": 710, "rank": 4, "name": "NVDA",
    }
    try:
        data = sent.get_sentiment("nvda")
    finally:
        _restore(original)
        sent.get_mention_stats = orig_stats

    assert data["source"] == "apewisdom", data
    assert data["volume"] == 216, data
    assert data["mentions_prev"] == 118 and data["upvotes"] == 710 and data["rank"] == 4, data
    assert data["net_score"] == 0.0 and data["top"] == [], data
    assert cache.set_calls == 1, "apewisdom result is cached"


@case
def test_none_source_when_nothing_available():
    cache = _FakeCache()
    orig_stats = sent.get_mention_stats
    original = _install(cache, lambda t: [], lambda t, m: _sample_result())
    sent.get_mention_stats = lambda t: None   # not on ApeWisdom board either
    try:
        data = sent.get_sentiment("ZZZZ")
    finally:
        _restore(original)
        sent.get_mention_stats = orig_stats

    assert data["source"] == "none", data
    assert data["volume"] == 0 and data["top"] == [], data
    assert cache.set_calls == 1, "zero result is still cached"


@case
def test_ttl_is_source_aware():
    # aged past the 5-min "none" window but well inside the 1h "reddit" window.
    aged = datetime.now(timezone.utc) - timedelta(seconds=sent._TTL_BY_SOURCE["none"] + 60)

    # A "none" entry of that age must be recomputed...
    none_payload = json.dumps(sent.result_to_dict(SentimentResult(
        ticker="AAPL", net_score=0.0, bull=0, bear=0, neutral=0, volume=0,
        computed_at="2026-07-08T00:00:00+00:00", top=[], source="none",
    )))
    cache = _FakeCache(seed={"sentiment:AAPL": (none_payload, aged)})
    fetch_calls = {"n": 0}
    orig_stats = sent.get_mention_stats

    def fetch(ticker):
        fetch_calls["n"] += 1
        return ["m1"]

    original = _install(cache, fetch, lambda t, m: _sample_result(volume=16))
    sent.get_mention_stats = lambda t: None
    try:
        data = sent.get_sentiment("AAPL")
    finally:
        _restore(original)
        sent.get_mention_stats = orig_stats
    assert fetch_calls["n"] == 1, "aged 'none' entry must recompute"
    assert data["volume"] == 16, data

    # ...but a "reddit" result of the SAME age is still a cache hit (1h window).
    real = json.dumps(sent.result_to_dict(_sample_result(volume=16)))  # source defaults 'reddit'
    cache2 = _FakeCache(seed={"sentiment:AAPL": (real, aged)})
    fetch_calls["n"] = 0
    original = _install(cache2, fetch, lambda t, m: _sample_result(volume=16))
    try:
        data2 = sent.get_sentiment("AAPL")
    finally:
        _restore(original)
    assert fetch_calls["n"] == 0, "same-age 'reddit' result must still be a cache hit"
    assert data2 == json.loads(real), data2


@case
def test_fresh_reddit_compute_writes_snapshot():
    cache = _FakeCache()
    original = _install(cache, lambda t: ["m1"], lambda t, m: _sample_result(volume=16))
    try:
        sent.get_sentiment("AAPL")
    finally:
        _restore(original)
    assert len(cache.snapshots) == 1, cache.snapshots
    snap = cache.snapshots[0]
    assert snap["ticker"] == "AAPL" and snap["source"] == "reddit", snap
    assert snap["volume"] == 16 and snap["date"], snap
    assert isinstance(snap["top_json"], str), snap  # top serialized to JSON text


@case
def test_apewisdom_fallback_writes_snapshot():
    cache = _FakeCache()
    orig_stats = sent.get_mention_stats
    original = _install(cache, lambda t: [], lambda t, m: _sample_result())
    sent.get_mention_stats = lambda t: {
        "mentions": 216, "mentions_prev": 118, "upvotes": 710, "rank": 4, "name": "NVDA",
    }
    try:
        sent.get_sentiment("NVDA")
    finally:
        _restore(original)
        sent.get_mention_stats = orig_stats
    assert len(cache.snapshots) == 1, cache.snapshots
    snap = cache.snapshots[0]
    assert snap["source"] == "apewisdom" and snap["volume"] == 216, snap
    assert snap["mentions_prev"] == 118 and snap["upvotes"] == 710 and snap["rank"] == 4, snap


@case
def test_cache_hit_writes_no_snapshot():
    fresh = json.dumps(sent.result_to_dict(_sample_result(volume=16)))
    cache = _FakeCache(seed={"sentiment:AAPL": (fresh, datetime.now(timezone.utc))})
    original = _install(cache, lambda t: ["m1"], lambda t, m: _sample_result())
    try:
        sent.get_sentiment("AAPL")
    finally:
        _restore(original)
    assert cache.snapshots == [], "cache hit must not write a snapshot"


@case
def test_none_source_writes_no_snapshot():
    cache = _FakeCache()
    orig_stats = sent.get_mention_stats
    original = _install(cache, lambda t: [], lambda t, m: _sample_result())
    sent.get_mention_stats = lambda t: None
    try:
        sent.get_sentiment("ZZZZ")
    finally:
        _restore(original)
        sent.get_mention_stats = orig_stats
    assert cache.snapshots == [], "source 'none' must not be snapshotted"


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
