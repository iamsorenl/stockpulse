"""Tests for LLM sentiment scoring (SOR-156), with a mocked LLM (no key/tokens).

Run from backend/:
    ./.venv/bin/python -m tests.test_sentiment
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import sentiment as S  # noqa: E402
from app.reddit_ingest import Mention  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _m(id, text, score=0):
    return Mention(id=id, kind="post", subreddit="stocks", author="u",
                   created_utc=0.0, text=text, score=score, permalink=f"http://x/{id}")


@case
def test_classify_batch_parses_and_drops_irrelevant():
    S._llm_json = lambda system, user: {"results": [
        {"i": 0, "relevant": True, "sentiment": "bullish"},
        {"i": 1, "relevant": False, "sentiment": "bearish"},   # dropped
        {"i": 2, "relevant": True, "sentiment": "neutral"},
    ]}
    out = S._classify_batch("AAPL", [_m("a", "x"), _m("b", "y"), _m("c", "z")])
    assert out == {0: "bullish", 2: "neutral"}, out


@case
def test_aggregation_counts_and_net_score():
    # 3 bullish, 1 bearish -> net (3-1)/4*100 = 50
    S._classify_batch = lambda ticker, batch: {
        i: ("bullish" if i < 3 else "bearish") for i in range(len(batch))
    }
    ms = [_m(str(i), "AAPL good", score=i) for i in range(4)]
    r = S.score_mentions("aapl", ms)
    assert (r.bull, r.bear, r.neutral) == (3, 1, 0), (r.bull, r.bear, r.neutral)
    assert r.volume == 4, r.volume
    assert r.net_score == 50.0, r.net_score
    assert r.ticker == "AAPL", r.ticker
    assert r.computed_at  # ISO timestamp present


@case
def test_top_sorted_by_reddit_score_and_capped():
    S._classify_batch = lambda ticker, batch: {i: "bullish" for i in range(len(batch))}
    ms = [_m(str(i), "AAPL", score=i) for i in range(10)]
    r = S.score_mentions("AAPL", ms)
    assert len(r.top) == S._TOP_N, len(r.top)
    scores = [t.score for t in r.top]
    assert scores == sorted(scores, reverse=True), scores
    assert scores[0] == 9, scores  # highest-upvoted first


@case
def test_batches_respect_batch_size():
    calls = {"n": 0}
    def fake_classify(ticker, batch):
        calls["n"] += 1
        assert len(batch) <= S._BATCH_SIZE
        return {}
    S._classify_batch = fake_classify
    S.score_mentions("AAPL", [_m(str(i), "x") for i in range(20)])
    assert calls["n"] == 3, calls["n"]  # 8 + 8 + 4


@case
def test_no_llm_yields_wellformed_zero():
    S._llm_json = lambda system, user: None  # LLM unavailable
    # restore real _classify_batch (which will call the (mocked) _llm_json)
    import importlib
    importlib.reload(S)
    S._llm_json = lambda system, user: None
    r = S.score_mentions("AAPL", [_m("a", "AAPL moon"), _m("b", "AAPL dump")])
    assert r.volume == 0 and r.net_score == 0.0, (r.volume, r.net_score)
    assert r.bull == 0 and r.bear == 0 and r.neutral == 0
    assert r.top == []


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
