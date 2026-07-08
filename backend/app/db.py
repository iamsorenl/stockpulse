"""SQLite cache helper (stdlib sqlite3).

Scaffolding only for now. Later stages will use this cache table to memoize
price/indicator responses so repeated requests don't re-hit yfinance.

Schema (`cache` table):
  key        TEXT PRIMARY KEY  -- caller-defined cache key, e.g. "prices:AAPL:6mo"
  value      TEXT NOT NULL     -- serialized payload (JSON string)
  created_at TEXT NOT NULL     -- ISO-8601 UTC timestamp when the row was written

No price/fetch logic lives here yet — just connection + init helpers.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- One row per (ticker, UTC day): the day's sentiment snapshot. Accumulates over
-- time to power the M3 timeline / "on this day" views. `source` records how the
-- day was measured (reddit = LLM-scored post text; apewisdom = mention volume).
CREATE TABLE IF NOT EXISTS sentiment_snapshots (
    ticker        TEXT    NOT NULL,
    date          TEXT    NOT NULL,   -- UTC YYYY-MM-DD
    computed_at   TEXT    NOT NULL,   -- ISO-8601 UTC of the compute
    source        TEXT    NOT NULL,   -- reddit | apewisdom
    net_score     REAL    NOT NULL,
    bull          INTEGER NOT NULL,
    bear          INTEGER NOT NULL,
    neutral       INTEGER NOT NULL,
    volume        INTEGER NOT NULL,
    mentions_prev INTEGER,
    upvotes       INTEGER,
    rank          INTEGER,
    top_json      TEXT    NOT NULL DEFAULT '[]',
    news_net_score REAL,
    news_volume    INTEGER,
    PRIMARY KEY (ticker, date)
);
"""

# Columns added to sentiment_snapshots after its first release; applied as
# idempotent ALTERs in init_db so existing dev databases pick them up.
_SNAPSHOT_MIGRATIONS = (
    ("news_net_score", "REAL"),
    ("news_volume", "INTEGER"),
)

# Columns written/read for a snapshot row (order matters for the upsert).
_SNAPSHOT_COLUMNS = (
    "ticker", "date", "computed_at", "source", "net_score",
    "bull", "bear", "neutral", "volume", "mentions_prev", "upvotes", "rank", "top_json",
    "news_net_score", "news_volume",
)


def get_connection() -> sqlite3.Connection:
    """Open a new SQLite connection to the cache database.

    Callers are responsible for closing it, or should prefer `connection()`.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Context-managed connection that commits on success and always closes."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create the database file (if missing) and ensure the schema exists.

    Safe to call repeatedly; runs on application startup.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.executescript(_SCHEMA)
        # Bring pre-existing snapshot tables up to date (idempotent).
        for column, decl in _SNAPSHOT_MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE sentiment_snapshots ADD COLUMN {column} {decl}")
            except sqlite3.OperationalError:
                pass  # column already exists


# ----- Generic cache accessors -------------------------------------------------
# Kept here so all SQLite access stays in this module (per project conventions).
# Callers own the freshness policy; this layer just stores/retrieves the raw
# serialized value plus the UTC timestamp it was written.


def cache_get(key: str) -> Optional[tuple[str, datetime]]:
    """Return (value, created_at) for `key`, or None if absent.

    `created_at` is returned as a timezone-aware UTC datetime.
    """
    with connection() as conn:
        row = conn.execute(
            "SELECT value, created_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    created_at = datetime.fromisoformat(row["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return row["value"], created_at


def cache_set(key: str, value: str) -> None:
    """Upsert `value` under `key`, stamping created_at with the current UTC time."""
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        conn.execute(
            "INSERT INTO cache (key, value, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "created_at = excluded.created_at",
            (key, value, now),
        )


# ----- Sentiment snapshots (M3 timeline) ---------------------------------------
# Kept here so all SQLite access stays in this module. Callers pass/receive plain
# dicts keyed by the snapshot columns.


def snapshot_upsert(row: dict) -> None:
    """Insert or replace the (ticker, date) snapshot with the values in `row`."""
    values = [row.get(col) for col in _SNAPSHOT_COLUMNS]
    placeholders = ", ".join("?" for _ in _SNAPSHOT_COLUMNS)
    updates = ", ".join(
        f"{col} = excluded.{col}" for col in _SNAPSHOT_COLUMNS
        if col not in ("ticker", "date")
    )
    with connection() as conn:
        conn.execute(
            f"INSERT INTO sentiment_snapshots ({', '.join(_SNAPSHOT_COLUMNS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(ticker, date) DO UPDATE SET {updates}",
            values,
        )


def snapshots_get(ticker: str, since: str) -> list[dict]:
    """Return snapshots for `ticker` on/after `since` (YYYY-MM-DD), oldest-first."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sentiment_snapshots WHERE ticker = ? AND date >= ? "
            "ORDER BY date ASC",
            (ticker, since),
        ).fetchall()
    return [dict(r) for r in rows]


def snapshot_get_one(ticker: str, date: str) -> Optional[dict]:
    """Return the snapshot for `ticker` on exactly `date`, or None."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM sentiment_snapshots WHERE ticker = ? AND date = ?",
            (ticker, date),
        ).fetchone()
    return dict(row) if row is not None else None
