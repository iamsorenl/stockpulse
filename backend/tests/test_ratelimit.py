"""Tests for the per-IP sliding-window rate limiter (Render deploy hardening).

No network, no FastAPI app boot -- exercises the limiter class and the
X-Forwarded-For parsing directly. Run from backend/:
    ./.venv/bin/python -m tests.test_ratelimit
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ratelimit as R  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _request(forwarded_for=None, client_host="127.0.0.1"):
    headers = {"x-forwarded-for": forwarded_for} if forwarded_for else {}
    client = SimpleNamespace(host=client_host) if client_host else None
    return SimpleNamespace(headers=headers, client=client)


@case
def test_client_ip_prefers_first_forwarded_hop():
    req = _request(forwarded_for="203.0.113.5, 10.0.0.1, 10.0.0.2")
    assert R.client_ip(req) == "203.0.113.5"


@case
def test_client_ip_falls_back_to_direct_connection():
    req = _request(forwarded_for=None, client_host="192.168.1.9")
    assert R.client_ip(req) == "192.168.1.9"


@case
def test_client_ip_unknown_when_nothing_available():
    req = _request(forwarded_for=None, client_host=None)
    assert R.client_ip(req) == "unknown"


@case
def test_limiter_blocks_after_limit_then_recovers_after_window():
    limiter = R._SlidingWindowLimiter(limit=3, window_seconds=1000)
    key = "1.2.3.4"
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is False  # 4th within the window -> blocked

    # A different key has its own independent budget.
    assert limiter.allow("5.6.7.8") is True


@case
def test_limiter_window_expires_old_hits():
    limiter = R._SlidingWindowLimiter(limit=1, window_seconds=0.05)
    key = "9.9.9.9"
    assert limiter.allow(key) is True
    assert limiter.allow(key) is False
    import time
    time.sleep(0.06)
    assert limiter.allow(key) is True, "hit outside the window should free up budget"


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
