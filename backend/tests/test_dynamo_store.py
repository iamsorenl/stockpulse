"""Tests for the DynamoDB store used on the AWS Lambda deploy.

Runs against moto's in-memory DynamoDB, so no AWS account or network. Needs
`pip install moto[dynamodb]`; skips cleanly without it. Run from backend/:
    ./.venv/bin/python -m tests.test_dynamo_store
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

try:
    import boto3
    from moto import mock_aws
except ModuleNotFoundError:
    print("SKIP test_dynamo_store: moto not installed (pip install 'moto[dynamodb]')")
    raise SystemExit(0)

from app import config, dynamo_store as D  # noqa: E402

_CASES: list = []


def case(fn):
    _CASES.append(fn)
    return fn


def _fresh_table():
    """Create the table the way infra/main.tf does; reset the store's handle."""
    boto3.client("dynamodb").create_table(
        TableName=config.DYNAMODB_TABLE,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                   {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                              {"AttributeName": "sk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    D._table = None


@case
@mock_aws
def test_cache_round_trip_and_miss():
    _fresh_table()
    assert D.cache_get("prices:AAPL:6mo") is None
    big = '{"candles": [' + ",".join(['{"c": 123.45}'] * 30000) + "]}"  # ~450 KB raw
    D.cache_set("prices:AAPL:6mo", big)
    value, created_at = D.cache_get("prices:AAPL:6mo")
    assert value == big
    assert created_at.tzinfo is not None


@case
@mock_aws
def test_snapshots_since_filter_and_order():
    _fresh_table()
    for day in ("2026-10-03", "2026-10-01", "2026-10-02"):
        D.snapshot_upsert({"ticker": "AMD", "date": day, "net_score": 1.5, "top_json": "[]"})
    D.snapshot_upsert({"ticker": "TSLA", "date": "2026-10-02", "net_score": -2.0})
    rows = D.snapshots_get("AMD", "2026-10-02")
    assert [r["date"] for r in rows] == ["2026-10-02", "2026-10-03"]
    assert rows[0]["net_score"] == 1.5
    # upsert replaces the same (ticker, date)
    D.snapshot_upsert({"ticker": "AMD", "date": "2026-10-02", "net_score": 9.0})
    assert D.snapshot_get_one("AMD", "2026-10-02")["net_score"] == 9.0
    assert D.snapshot_get_one("AMD", "2026-09-30") is None


@case
@mock_aws
def test_groq_cap_is_shared_across_instances():
    _fresh_table()
    # Two caps = two Lambda instances. Together they get the limit, not 2x it.
    a, b = D.SharedDailyCap(3), D.SharedDailyCap(3)
    results = [a.try_consume(), b.try_consume(), a.try_consume(), b.try_consume(), a.try_consume()]
    assert results == [True, True, True, False, False], results


@case
@mock_aws
def test_meter_switches_function_off_at_limit():
    _fresh_table()
    calls = []
    fake_lambda = SimpleNamespace(put_function_concurrency=lambda **kw: calls.append(kw))
    real_boto3, saved = D.boto3, (config.METER_LIMIT_REQUESTS, config.METER_LIMIT_GB_SECONDS)
    D.boto3 = SimpleNamespace(resource=real_boto3.resource, client=lambda name: fake_lambda)
    config.METER_LIMIT_REQUESTS, config.METER_LIMIT_GB_SECONDS = 3, 10**9
    os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "stockpulse"
    try:
        D._init_pending = True
        for _ in range(2):
            D.meter_request(0.5)
        assert calls == []
        D.meter_request(0.5)  # third request hits the limit of 3
        assert calls == [{"FunctionName": "stockpulse", "ReservedConcurrentExecutions": 0}]
        item = D.table().get_item(Key={"pk": "meter#lambda",
                                       "sk": D.datetime.now(D.timezone.utc).strftime("%Y-%m")})["Item"]
        # 3 x (0.5 s + 10 ms) + one 10 s startup estimate, at 1 GB
        assert float(item["gb_seconds"]) == 11.53, item
    finally:
        D.boto3 = real_boto3
        config.METER_LIMIT_REQUESTS, config.METER_LIMIT_GB_SECONDS = saved


@case
@mock_aws
def test_meter_carries_usage_from_a_failed_write():
    _fresh_table()
    D._init_pending = False
    real_table = D.table
    D.table = lambda: (_ for _ in ()).throw(RuntimeError("throttled"))
    try:
        D.meter_request(1.0)  # write fails; usage must not be lost
        raise AssertionError("expected the failed write to raise")
    except RuntimeError:
        pass
    finally:
        D.table = real_table
    D.meter_request(1.0)
    item = D.table().get_item(Key={"pk": "meter#lambda",
                                   "sk": D.datetime.now(D.timezone.utc).strftime("%Y-%m")})["Item"]
    assert int(item["requests"]) == 2, item
    assert float(item["gb_seconds"]) == 2.02, item


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
