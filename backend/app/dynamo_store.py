"""DynamoDB storage for the AWS Lambda deploy (STOCKPULSE_STORE=dynamodb).

Same five functions as db.py's SQLite versions, so callers don't change. Lambda
runs many short-lived instances, each with its own /tmp, so SQLite there would
split the cache and lose snapshot history; one shared table fixes both. Also
holds the shared Groq daily cap, which an in-memory counter can't enforce
across instances.

One table, two string keys (pk, sk):
  cache       pk="cache#<key>"       sk="-"           value (zlib JSON), created_at, ttl
  snapshots   pk="snap#<TICKER>"     sk="YYYY-MM-DD"  data (JSON row)
  groq cap    pk="cap#groq"          sk="YYYY-MM-DD"  n (call count), ttl
  meter       pk="meter#lambda"      sk="YYYY-MM"     requests, gb_seconds
`ttl` is epoch seconds; DynamoDB deletes expired items on its own.
"""

from __future__ import annotations

import json
import logging
import os
import time
import zlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from . import config

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 2 * 24 * 3600  # cleanup only; callers apply their own freshness
_CAP_TTL_SECONDS = 3 * 24 * 3600

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(config.DYNAMODB_TABLE)
    return _table


def init_db() -> None:
    """Nothing to create at runtime; Terraform owns the table."""


def cache_get(key: str) -> Optional[tuple[str, datetime]]:
    item = table().get_item(Key={"pk": f"cache#{key}", "sk": "-"}).get("Item")
    if item is None:
        return None
    value = zlib.decompress(item["value"].value).decode()
    return value, datetime.fromisoformat(item["created_at"])


def cache_set(key: str, value: str) -> None:
    # Compressed because DynamoDB caps an item at 400 KB and price payloads
    # reach ~240 KB as plain JSON.
    table().put_item(Item={
        "pk": f"cache#{key}",
        "sk": "-",
        "value": zlib.compress(value.encode()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ttl": int(time.time()) + _CACHE_TTL_SECONDS,
    })


def snapshot_upsert(row: dict) -> None:
    table().put_item(Item={
        "pk": f"snap#{row['ticker']}",
        "sk": row["date"],
        "data": json.dumps(row),
    })


def snapshots_get(ticker: str, since: str) -> list[dict]:
    """Oldest-first, like the SQLite version (DynamoDB sorts by sk ascending)."""
    rows: list[dict] = []
    kwargs = {
        "KeyConditionExpression": "pk = :pk AND sk >= :since",
        "ExpressionAttributeValues": {":pk": f"snap#{ticker}", ":since": since},
    }
    while True:
        resp = table().query(**kwargs)
        rows.extend(json.loads(i["data"]) for i in resp["Items"])
        if "LastEvaluatedKey" not in resp:
            return rows
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def snapshot_get_one(ticker: str, date: str) -> Optional[dict]:
    item = table().get_item(Key={"pk": f"snap#{ticker}", "sk": date}).get("Item")
    return json.loads(item["data"]) if item else None


class SharedDailyCap:
    """Drop-in for llm_kit.DailyCap, counted in DynamoDB so every Lambda
    instance draws from one budget. The condition makes check-and-increment a
    single atomic step: two instances racing for the last call can't both win.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def try_consume(self) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            table().update_item(
                Key={"pk": "cap#groq", "sk": today},
                UpdateExpression="ADD n :one SET #ttl = :ttl",
                ConditionExpression="attribute_not_exists(n) OR n < :limit",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":one": 1,
                    ":limit": self.limit,
                    ":ttl": int(time.time()) + _CAP_TTL_SECONDS,
                },
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise


# ----- Free-tier meter ---------------------------------------------------------
# Every Lambda invocation adds itself to this month's totals. Once either total
# reaches its limit (default 80% of Lambda's monthly free allowance), the
# function sets its own reserved concurrency to 0: AWS then rejects all
# requests, so nothing more is billed. Re-enable with
#   aws lambda delete-function-concurrency --function-name stockpulse
#
#   meter    pk="meter#lambda"   sk="YYYY-MM"   requests, gb_seconds

# Each new instance's startup is billed too but can't be timed from inside the
# app, so the first request of every instance adds a fixed estimate (the logs
# show 3-10 s).
_init_pending = True


def meter_request(seconds: float) -> None:
    """Record one invocation of `seconds`; switch the function off at the limit."""
    global _init_pending
    if _init_pending:
        seconds += config.METER_INIT_SECONDS
        _init_pending = False
    memory_gb = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "1024")) / 1024
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    totals = table().update_item(
        Key={"pk": "meter#lambda", "sk": month},
        UpdateExpression="ADD requests :one, gb_seconds :gbs",
        ExpressionAttributeValues={
            ":one": 1,
            # +10 ms per call covers the adapter's own time; Decimal because
            # DynamoDB numbers can't be floats.
            ":gbs": Decimal(str(round((seconds + 0.01) * memory_gb, 3))),
        },
        ReturnValues="UPDATED_NEW",
    )["Attributes"]
    if (totals["requests"] >= config.METER_LIMIT_REQUESTS
            or totals["gb_seconds"] >= config.METER_LIMIT_GB_SECONDS):
        logger.error("free-tier meter limit reached (%s); switching function off", totals)
        boto3.client("lambda").put_function_concurrency(
            FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            ReservedConcurrentExecutions=0,
        )
