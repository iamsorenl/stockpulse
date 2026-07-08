"""Fixture-based tests for Arctic Shift Reddit ingestion (SOR-155).

No network, no keys. Run from backend/:
    ./.venv/bin/python -m tests.test_reddit_ingest
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import reddit_ingest as ri  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _isolate_cache():
    """Stub the SQLite cache so tests never touch stockpulse.db."""
    ri.db.cache_get = lambda key: None
    ri.db.cache_set = lambda key, value: None


def _post(id, sub, title, selftext="", score=0, created=0.0, permalink=None):
    r = {"id": id, "subreddit": sub, "title": title, "selftext": selftext,
         "score": score, "created_utc": created, "author": "u1"}
    if permalink:
        r["permalink"] = permalink
    return r


def _comment(id, sub, body, link_id="t3_post1", score=0, created=0.0):
    return {"id": id, "subreddit": sub, "body": body, "link_id": link_id,
            "score": score, "created_utc": created, "author": "u2"}


def _stub(posts_by_sub=None, comments_by_sub=None):
    """Build a _get_json stub returning fixtures keyed by (path, subreddit)."""
    posts_by_sub = posts_by_sub or {}
    comments_by_sub = comments_by_sub or {}

    def stub(path, params):
        sub = params.get("subreddit")
        if path == "/posts/search":
            return posts_by_sub.get(sub, [])
        if path == "/comments/search":
            return comments_by_sub.get(sub, [])
        return []

    return stub


@case
def test_normalizes_and_sorts_posts_and_comments():
    _isolate_cache()
    ri._get_json = _stub(
        posts_by_sub={"stocks": [_post("p1", "stocks", "AAPL to the moon", "buying more", score=42, created=100.0)]},
        comments_by_sub={"stocks": [_comment("c1", "stocks", "I sold my AAPL", score=5, created=200.0)]},
    )
    ms = ri.fetch_mentions("aapl", use_cache=False)
    assert len(ms) == 2, ms
    # newest first (comment created 200 > post 100)
    assert ms[0].id == "c1" and ms[0].kind == "comment", ms[0]
    assert ms[1].id == "p1" and ms[1].kind == "post", ms[1]
    # post text combines title + selftext
    assert "AAPL to the moon" in ms[1].text and "buying more" in ms[1].text, ms[1].text
    assert ms[0].text == "I sold my AAPL", ms[0].text


@case
def test_dedupes_same_id_across_subreddits():
    _isolate_cache()
    dup = _post("p1", "stocks", "AAPL earnings", created=10.0)
    ri._get_json = _stub(posts_by_sub={"stocks": [dup], "wallstreetbets": [dup]})
    ms = ri.fetch_mentions("AAPL", use_cache=False)
    assert len(ms) == 1, ms  # same (kind,id) collapsed


@case
def test_drops_empty_text():
    _isolate_cache()
    ri._get_json = _stub(
        posts_by_sub={"stocks": [_post("p1", "stocks", "", "")]},  # no title/body
        comments_by_sub={"stocks": [_comment("c1", "stocks", "   ")]},  # whitespace
    )
    assert ri.fetch_mentions("AAPL", use_cache=False) == []


@case
def test_permalink_construction():
    _isolate_cache()
    ri._get_json = _stub(
        posts_by_sub={"stocks": [_post("p1", "stocks", "AAPL")]},
        comments_by_sub={"stocks": [_comment("c1", "stocks", "AAPL good", link_id="t3_abc")]},
    )
    ms = {m.id: m for m in ri.fetch_mentions("AAPL", use_cache=False)}
    assert ms["p1"].permalink == "https://www.reddit.com/r/stocks/comments/p1/", ms["p1"].permalink
    assert ms["c1"].permalink == "https://www.reddit.com/r/stocks/comments/abc/_/c1/", ms["c1"].permalink


@case
def test_prefers_reddit_permalink_field():
    _isolate_cache()
    ri._get_json = _stub(
        posts_by_sub={"stocks": [_post("p1", "stocks", "AAPL", permalink="/r/stocks/comments/p1/aapl_moon/")]},
    )
    ms = ri.fetch_mentions("AAPL", use_cache=False)
    assert ms[0].permalink == "https://www.reddit.com/r/stocks/comments/p1/aapl_moon/", ms[0].permalink


@case
def test_untrusted_permalink_scheme_is_rejected():
    # A tampered archive record with a javascript: (or other non-reddit) permalink
    # must NOT flow through to the href — we construct a safe reddit URL instead.
    _isolate_cache()
    ri._get_json = _stub(
        posts_by_sub={"stocks": [_post("p1", "stocks", "AAPL", permalink="javascript:alert(1)")]},
        comments_by_sub={"stocks": [_comment("c1", "stocks", "AAPL", link_id="t3_abc")]},
    )
    ms = {m.id: m for m in ri.fetch_mentions("AAPL", use_cache=False)}
    assert ms["p1"].permalink == "https://www.reddit.com/r/stocks/comments/p1/", ms["p1"].permalink
    assert ms["p1"].permalink.startswith("https://www.reddit.com/"), ms["p1"].permalink
    # a full off-site https URL is also rejected (only reddit.com is trusted verbatim)
    _isolate_cache()
    ri._get_json = _stub(posts_by_sub={"stocks": [_post("p2", "stocks", "AAPL", permalink="https://evil.example/x")]})
    ms2 = ri.fetch_mentions("AAPL", use_cache=False)
    assert ms2[0].permalink == "https://www.reddit.com/r/stocks/comments/p2/", ms2[0].permalink


@case
def test_maintenance_or_error_yields_empty():
    _isolate_cache()
    ri._get_json = lambda path, params: None  # simulate "Under maintenance" / transport error
    assert ri.fetch_mentions("AAPL", use_cache=False) == []


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
