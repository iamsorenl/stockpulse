"""Fixture-based tests for the ApeWisdom mention-volume source (SOR-155 fallback).

No network, no SQLite. Run from backend/:
    ./.venv/bin/python -m tests.test_apewisdom
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import apewisdom as ap  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


class _FakeCache:
    def __init__(self):
        self.store = {}
        self.set_calls = 0

    def cache_get(self, key):
        return self.store.get(key)

    def cache_set(self, key, value):
        self.set_calls += 1
        self.store[key] = (value, datetime.now(timezone.utc))


def _row(ticker, name, mentions, prev, upvotes, rank):
    return {"ticker": ticker, "name": name, "mentions": mentions,
            "mentions_24h_ago": prev, "upvotes": upvotes, "rank": rank}


def _page(results, pages=1):
    return {"count": len(results), "pages": pages, "current_page": 1, "results": results}


def _install(cache, pages_by_num):
    orig = (ap.db, ap._get_page)
    ap.db = cache
    ap._get_page = lambda p: pages_by_num.get(p)
    return orig


def _restore(orig):
    ap.db, ap._get_page = orig


@case
def test_lookup_hit_and_miss():
    cache = _FakeCache()
    pages = {1: _page([
        _row("NVDA", "NVIDIA", 216, 118, 710, 4),
        _row("MU", "Micron", 713, 518, 3720, 1),
    ], pages=1)}
    orig = _install(cache, pages)
    try:
        hit = ap.get_stats("nvda")          # case-insensitive
        miss = ap.get_stats("ZZZZ")
    finally:
        _restore(orig)
    assert hit == {"mentions": 216, "mentions_prev": 118, "upvotes": 710,
                   "rank": 4, "name": "NVIDIA"}, hit
    assert miss is None, miss


@case
def test_merges_multiple_pages_and_stops_at_page_count():
    cache = _FakeCache()
    calls = {"n": 0}
    p = {
        1: _page([_row("AAA", "A", 10, 5, 1, 1)], pages=2),
        2: _page([_row("BBB", "B", 8, 4, 2, 2)], pages=2),
        3: _page([_row("CCC", "C", 1, 0, 0, 3)], pages=2),  # must NOT be fetched
    }

    def get_page(n):
        calls["n"] += 1
        return p.get(n)

    orig = (ap.db, ap._get_page)
    ap.db = cache
    ap._get_page = get_page
    try:
        a = ap.get_stats("AAA")
        b = ap.get_stats("BBB")
        c = ap.get_stats("CCC")
    finally:
        ap.db, ap._get_page = orig
    assert a and b, (a, b)
    assert c is None, "page 3 is beyond pages=2 and must not be loaded"
    # Board is cached after the first build, so pages fetched exactly once: 1 and 2.
    assert calls["n"] == 2, calls


@case
def test_unescapes_company_name():
    cache = _FakeCache()
    pages = {1: _page([_row("SPY", "SPDR S&amp;P 500 ETF Trust", 384, 276, 3082, 2)])}
    orig = _install(cache, pages)
    try:
        stats = ap.get_stats("SPY")
    finally:
        _restore(orig)
    assert stats["name"] == "SPDR S&P 500 ETF Trust", stats["name"]


@case
def test_caches_board_and_reuses_it():
    cache = _FakeCache()
    pages = {1: _page([_row("NVDA", "NVIDIA", 216, 118, 710, 4)])}
    orig = _install(cache, pages)
    try:
        ap.get_stats("NVDA")
        ap.get_stats("NVDA")
    finally:
        _restore(orig)
    assert cache.set_calls == 1, "board fetched + cached once, second lookup reuses it"


@case
def test_source_down_returns_none_and_does_not_cache():
    cache = _FakeCache()
    orig = _install(cache, {})  # _get_page returns None for every page
    try:
        stats = ap.get_stats("NVDA")
    finally:
        _restore(orig)
    assert stats is None, stats
    assert cache.set_calls == 0, "an empty/failed board must not be cached"


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
