"""Tests for the global daily Groq call cap (Render deploy hardening).

No network -- `_groq_complete` must short-circuit to None once the cap is hit,
without ever reaching urllib. Run from backend/:
    ./.venv/bin/python -m tests.test_daily_cap
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, sentiment as S  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _reset_cap(limit: int):
    """Fresh cap counter + configured limit, restored by the caller."""
    original_limit = config.DAILY_LLM_CAP
    original_cap = S._groq_daily_cap
    config.DAILY_LLM_CAP = limit
    S._groq_daily_cap = S._DailyCap()
    return original_limit, original_cap


def _restore(original_limit, original_cap):
    config.DAILY_LLM_CAP = original_limit
    S._groq_daily_cap = original_cap


@case
def test_try_consume_allows_up_to_the_limit_then_blocks():
    original_limit, original_cap = _reset_cap(2)
    try:
        cap = S._groq_daily_cap
        assert cap.try_consume() is True
        assert cap.try_consume() is True
        assert cap.try_consume() is False  # 3rd call same day -> over cap
    finally:
        _restore(original_limit, original_cap)


@case
def test_cap_resets_on_a_new_day():
    original_limit, original_cap = _reset_cap(1)
    try:
        cap = S._groq_daily_cap
        assert cap.try_consume() is True
        assert cap.try_consume() is False
        cap._date = date.today() - timedelta(days=1)  # simulate a day rollover
        assert cap.try_consume() is True
    finally:
        _restore(original_limit, original_cap)


@case
def test_groq_complete_short_circuits_without_network_when_cap_hit():
    original_limit, original_cap = _reset_cap(0)  # cap already exhausted
    original_key = config.GROQ_API_KEY
    original_configured = config.GROQ_CONFIGURED
    config.GROQ_API_KEY = "fake-key"
    config.GROQ_CONFIGURED = True

    def _boom(*args, **kwargs):
        raise AssertionError("must not hit the network when the daily cap is exhausted")

    import urllib.request
    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = _boom
    try:
        result = S._groq_complete("system", "user")
        assert result is None, result
    finally:
        urllib.request.urlopen = original_urlopen
        config.GROQ_API_KEY = original_key
        config.GROQ_CONFIGURED = original_configured
        _restore(original_limit, original_cap)


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
