"""Application configuration.

Central place for env-configurable settings. Every later stage should read
config from here rather than calling os.environ directly, so the conventions
stay in one file.

Environment variables (all optional; sensible defaults for local dev):
  STOCKPULSE_DB_PATH       Path to the SQLite cache file. Default: backend/stockpulse.db
  STOCKPULSE_CORS_ORIGINS  Comma-separated list of allowed browser origins.
                           Default: http://localhost:5173 (the Vite dev server).
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/ directory (parent of the app package).
BACKEND_DIR = Path(__file__).resolve().parent.parent

# SQLite cache database location.
DB_PATH = Path(os.environ.get("STOCKPULSE_DB_PATH", BACKEND_DIR / "stockpulse.db"))

# CORS: the Vite dev server origin by default. Override with a comma-separated list.
_default_origins = "http://localhost:5173"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("STOCKPULSE_CORS_ORIGINS", _default_origins).split(",")
    if origin.strip()
]
