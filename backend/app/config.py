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
  GROQ_API_KEY             Groq API key                       (console.groq.com)
  GROQ_MODEL               Groq model id. Default: llama-3.1-8b-instant
  OLLAMA_BASE_URL          Local Ollama endpoint. Default: http://localhost:11434
  OLLAMA_MODEL             Local model for fallback. Default: llama3.1
  ARCTIC_SHIFT_BASE_URL    Reddit data source (keyless). Default: the public host.

Reddit data comes from Arctic Shift (arctic-shift.photon-reddit.com), a free,
no-key archive API — Reddit disabled self-serve API keys in 2025, so PRAW is no
longer viable. The only credential M2 needs is the Groq key (for sentiment).
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

# Reddit data source: Arctic Shift (keyless). Overridable only for testing/mirrors.
ARCTIC_SHIFT_BASE_URL = (
    _clean(os.environ.get("ARCTIC_SHIFT_BASE_URL"))
    or "https://arctic-shift.photon-reddit.com/api"
)

GROQ_API_KEY = _clean(os.environ.get("GROQ_API_KEY"))
GROQ_MODEL = _clean(os.environ.get("GROQ_MODEL")) or "llama-3.1-8b-instant"

OLLAMA_BASE_URL = _clean(os.environ.get("OLLAMA_BASE_URL")) or "http://localhost:11434"
OLLAMA_MODEL = _clean(os.environ.get("OLLAMA_MODEL")) or "llama3.1"

# Whether OLLAMA was explicitly opted into (env var set), vs. just the default URL.
OLLAMA_ENABLED = _clean(os.environ.get("OLLAMA_BASE_URL")) is not None or _clean(
    os.environ.get("OLLAMA_MODEL")
) is not None

GROQ_CONFIGURED = bool(GROQ_API_KEY)
# Reddit data (Arctic Shift) needs no key; sentiment just needs a scorer.
SENTIMENT_CONFIGURED = GROQ_CONFIGURED or OLLAMA_ENABLED


def missing_for_sentiment() -> list[str]:
    """Human-readable list of what's still needed to enable sentiment features."""
    if SENTIMENT_CONFIGURED:
        return []
    return ["GROQ_API_KEY (console.groq.com) or an Ollama setup"]
