"""Application configuration.

Central place for env-configurable settings. Every later stage should read
config from here rather than calling os.environ directly, so the conventions
stay in one file. Values are loaded from the process environment, with
`backend/.env` loaded first (if present) for local dev — real env vars always
win over the file.

Environment variables (all optional; sensible defaults for local dev):
  STOCKPULSE_DB_PATH       SQLite cache file. Default: backend/stockpulse.db
  STOCKPULSE_CORS_ORIGINS  Comma-separated allowed browser origins.
                           Default: http://localhost:5173 (Vite dev server).

  --- M2 sentiment integrations (optional; features degrade cleanly if unset) ---
  REDDIT_CLIENT_ID         Reddit "script" app client id      (reddit.com/prefs/apps)
  REDDIT_CLIENT_SECRET     Reddit "script" app secret
  REDDIT_USER_AGENT        Identifying UA string for Reddit API
  GROQ_API_KEY             Groq API key                       (console.groq.com)
  GROQ_MODEL               Groq model id. Default: llama-3.1-8b-instant
  OLLAMA_BASE_URL          Local Ollama endpoint. Default: http://localhost:11434
  OLLAMA_MODEL             Local model for fallback. Default: llama3.1
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/ directory (parent of the app package).
BACKEND_DIR = Path(__file__).resolve().parent.parent

# Load backend/.env if present (does not override already-set real env vars).
try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except ModuleNotFoundError:  # dotenv is a normal dep; guard keeps import safe
    pass


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# --- Core (M1) -----------------------------------------------------------------

# SQLite cache database location.
DB_PATH = Path(os.environ.get("STOCKPULSE_DB_PATH", BACKEND_DIR / "stockpulse.db"))

# CORS: the Vite dev server origin by default. Override with a comma-separated list.
_default_origins = "http://localhost:5173"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("STOCKPULSE_CORS_ORIGINS", _default_origins).split(",")
    if origin.strip()
]

# --- M2 sentiment integrations -------------------------------------------------

REDDIT_CLIENT_ID = _clean(os.environ.get("REDDIT_CLIENT_ID"))
REDDIT_CLIENT_SECRET = _clean(os.environ.get("REDDIT_CLIENT_SECRET"))
REDDIT_USER_AGENT = _clean(os.environ.get("REDDIT_USER_AGENT")) or "StockPulse/0.1"

GROQ_API_KEY = _clean(os.environ.get("GROQ_API_KEY"))
GROQ_MODEL = _clean(os.environ.get("GROQ_MODEL")) or "llama-3.1-8b-instant"

OLLAMA_BASE_URL = _clean(os.environ.get("OLLAMA_BASE_URL")) or "http://localhost:11434"
OLLAMA_MODEL = _clean(os.environ.get("OLLAMA_MODEL")) or "llama3.1"

# Whether OLLAMA was explicitly opted into (env var set), vs. just the default URL.
OLLAMA_ENABLED = _clean(os.environ.get("OLLAMA_BASE_URL")) is not None or _clean(
    os.environ.get("OLLAMA_MODEL")
) is not None

REDDIT_CONFIGURED = bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)
GROQ_CONFIGURED = bool(GROQ_API_KEY)
# Sentiment needs Reddit for data plus at least one scorer (Groq or Ollama).
SENTIMENT_CONFIGURED = REDDIT_CONFIGURED and (GROQ_CONFIGURED or OLLAMA_ENABLED)


def missing_for_sentiment() -> list[str]:
    """Human-readable list of what's still needed to enable sentiment features."""
    missing: list[str] = []
    if not REDDIT_CONFIGURED:
        missing.append("REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET (reddit.com/prefs/apps)")
    if not (GROQ_CONFIGURED or OLLAMA_ENABLED):
        missing.append("GROQ_API_KEY (console.groq.com) or an Ollama setup")
    return missing
