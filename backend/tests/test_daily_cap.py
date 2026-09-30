"""Tests for the global daily Groq call cap (Render deploy hardening).

No network -- `groq_chat` must short-circuit with a rate-limit LlmError once the cap is hit,
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
    S._groq_daily_cap = S.DailyCap(limit)
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
def test_groq_chat_short_circuits_without_network_when_cap_hit():
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
        try:
            S.groq_chat([{"role": "user", "content": "hi"}])
            raise AssertionError("expected LlmError")
        except S.LlmError as exc:
            assert exc.kind == "rate-limit", exc.kind
        # The sentiment path swallows it and returns None (Ollama not enabled).
        original_ollama = config.OLLAMA_ENABLED
        config.OLLAMA_ENABLED = False
        try:
            assert S._llm_json("system", "user") is None
        finally:
            config.OLLAMA_ENABLED = original_ollama
    finally:
        urllib.request.urlopen = original_urlopen
        config.GROQ_API_KEY = original_key
        config.GROQ_CONFIGURED = original_configured
        _restore(original_limit, original_cap)


@case
def test_llm_json_falls_back_to_ollama_when_groq_fails():
    from llm_kit import LlmError, Reply
    original_limit, original_cap = _reset_cap(5)
    saved = (S.compat_chat, S.ollama_chat, config.GROQ_CONFIGURED, config.GROQ_API_KEY,
             config.OLLAMA_ENABLED)
    seen = {}

    def _groq_down(messages, **kw):
        seen["groq"] = kw
        raise LlmError("api", "groq 500", 500)

    def _ollama(messages, model, **kw):
        seen["ollama"] = (model, kw)
        return Reply('```json\n{"results": []}\n```')

    S.compat_chat, S.ollama_chat = _groq_down, _ollama
    config.GROQ_CONFIGURED, config.GROQ_API_KEY, config.OLLAMA_ENABLED = True, "k", True
    try:
        assert S._llm_json("system", "user") == {"results": []}
        assert seen["groq"]["json_mode"] is True and seen["groq"]["temperature"] == 0
        model, kw = seen["ollama"]
        assert model == config.OLLAMA_MODEL and kw["format"] == "json", kw
        assert kw["url"] == config.OLLAMA_BASE_URL and kw["temperature"] == 0, kw
        S.ollama_chat = lambda *a, **k: Reply("not json")
        assert S._llm_json("system", "user") is None
    finally:
        (S.compat_chat, S.ollama_chat, config.GROQ_CONFIGURED, config.GROQ_API_KEY,
         config.OLLAMA_ENABLED) = saved
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
