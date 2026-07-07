"""Fixture-based tests for the price provider chain (SOR-151 fallback).

No network and no third-party test deps: run with the project venv from the
backend/ directory:

    ./.venv/bin/python -m tests.test_providers

Stooq/yfinance are exercised through stubs (monkeypatched urlopen / provider
list), so these pass offline. Live end-to-end verification against real Yahoo /
Stooq still happens on a machine with unrestricted network.
"""

from __future__ import annotations

import os
import sys
import urllib.error
from datetime import date, timedelta

# Make `app` importable when run as a script from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import providers  # noqa: E402
from app import stocks  # noqa: E402


# --- helpers ------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._data = text.encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _patch_urlopen(text: str | None, *, raises: Exception | None = None):
    """Return (stub, captured) where captured['url'] records the requested URL."""
    captured: dict[str, str] = {}

    def stub(request, timeout=None):
        captured["url"] = getattr(request, "full_url", str(request))
        if raises is not None:
            raise raises
        return _FakeResponse(text or "")

    return stub, captured


def _stooq_csv(rows: list[tuple[str, float, float, float, float, int]]) -> str:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for d, o, h, l, c, v in rows:
        lines.append(f"{d},{o},{h},{l},{c},{v}")
    return "\n".join(lines) + "\n"


_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


# --- fetch_stooq --------------------------------------------------------------

@case
def test_stooq_parses_and_maps_symbol():
    recent = date.today() - timedelta(days=3)
    csv = _stooq_csv([(recent.isoformat(), 10.0, 11.0, 9.5, 10.5, 1_000_000)])
    stub, captured = _patch_urlopen(csv)
    orig = providers.urllib.request.urlopen
    providers.urllib.request.urlopen = stub
    try:
        candles = providers.fetch_stooq("AAPL", "1mo")
    finally:
        providers.urllib.request.urlopen = orig
    assert "s=aapl.us" in captured["url"], captured["url"]
    assert len(candles) == 1, candles
    c = candles[0]
    assert c == {
        "date": recent.isoformat(),
        "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1_000_000,
    }, c


@case
def test_stooq_slices_to_range():
    recent = date.today() - timedelta(days=5)
    csv = _stooq_csv([
        ("1999-01-04", 1.0, 1.0, 1.0, 1.0, 1),           # ancient -> excluded for 1mo
        (recent.isoformat(), 10.0, 11.0, 9.5, 10.5, 100),  # within 1mo -> kept
    ])
    stub, _ = _patch_urlopen(csv)
    orig = providers.urllib.request.urlopen
    providers.urllib.request.urlopen = stub
    try:
        candles = providers.fetch_stooq("MSFT", "1mo")
    finally:
        providers.urllib.request.urlopen = orig
    dates = [c["date"] for c in candles]
    assert dates == [recent.isoformat()], dates


@case
def test_stooq_html_challenge_returns_empty():
    stub, _ = _patch_urlopen("<!DOCTYPE html><html>verify your browser</html>")
    orig = providers.urllib.request.urlopen
    providers.urllib.request.urlopen = stub
    try:
        assert providers.fetch_stooq("AAPL", "6mo") == []
    finally:
        providers.urllib.request.urlopen = orig


@case
def test_stooq_network_error_returns_empty():
    stub, _ = _patch_urlopen(None, raises=urllib.error.URLError("no route"))
    orig = providers.urllib.request.urlopen
    providers.urllib.request.urlopen = stub
    try:
        assert providers.fetch_stooq("AAPL", "6mo") == []
    finally:
        providers.urllib.request.urlopen = orig


# --- fetch_candles chain ------------------------------------------------------

@case
def test_chain_falls_through_to_stooq_when_yfinance_empty():
    served = [{"date": "2026-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    orig = providers._PROVIDERS
    providers._PROVIDERS = [
        ("yfinance", lambda t, r: []),
        ("stooq", lambda t, r: served),
    ]
    try:
        candles, source = providers.fetch_candles("AAPL", "6mo")
    finally:
        providers._PROVIDERS = orig
    assert source == "stooq", source
    assert candles == served, candles


@case
def test_chain_prefers_yfinance_when_present():
    yf_rows = [{"date": "2026-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    orig = providers._PROVIDERS
    providers._PROVIDERS = [
        ("yfinance", lambda t, r: yf_rows),
        ("stooq", lambda t, r: [{"date": "x"}]),  # must not be reached
    ]
    try:
        candles, source = providers.fetch_candles("AAPL", "6mo")
    finally:
        providers._PROVIDERS = orig
    assert source == "yfinance", source
    assert candles == yf_rows, candles


@case
def test_chain_all_empty_returns_none_source():
    orig = providers._PROVIDERS
    providers._PROVIDERS = [("yfinance", lambda t, r: []), ("stooq", lambda t, r: [])]
    try:
        candles, source = providers.fetch_candles("NOPE", "1y")
    finally:
        providers._PROVIDERS = orig
    assert candles == [] and source is None, (candles, source)


# --- get_prices integration (provider chain -> assembled payload) -------------

@case
def test_get_prices_assembles_from_provider_chain():
    # 60-day ascending ramp so SMA20/SMA50 have points and trend resolves.
    base = date.today() - timedelta(days=90)
    rows = []
    for i in range(60):
        px = 100 + i
        rows.append({
            "date": (base + timedelta(days=i)).isoformat(),
            "open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1000 + i,
        })
    # Isolate from the real SQLite cache: force a miss and swallow the write so
    # the test never poisons stockpulse.db with synthetic data.
    orig_fetch = providers.fetch_candles
    orig_get, orig_set = stocks.db.cache_get, stocks.db.cache_set
    providers.fetch_candles = lambda t, r: (rows, "stooq")
    stocks.db.cache_get = lambda key: None
    stocks.db.cache_set = lambda key, value: None
    try:
        payload = stocks.get_prices("aapl", "6mo")   # lowercase -> normalized
    finally:
        providers.fetch_candles = orig_fetch
        stocks.db.cache_get, stocks.db.cache_set = orig_get, orig_set
    assert payload["ticker"] == "AAPL", payload["ticker"]
    assert payload["range"] == "6mo"
    assert len(payload["candles"]) == 60
    ind = payload["indicators"]
    assert ind["sma20"] and ind["sma50"], ind
    assert ind["trend"] == "up", ind["trend"]          # steady climb
    assert ind["pctChange"] > 0, ind["pctChange"]


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
