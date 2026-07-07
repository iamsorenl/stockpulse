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
from typing import Iterator

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


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
