"""StockPulse FastAPI application entrypoint.

Run locally (from the backend/ directory, with the venv active):

    uvicorn app.main:app --reload

Serves on http://localhost:8000 by default.

Endpoints:
  GET /health                              liveness probe
  GET /api/search?q=                       symbol/name suggestions (SOR-152)
  GET /api/stocks/{ticker}/prices?range=   OHLCV + trend indicators (SOR-151/154)

The search and price routes live in app/api.py and are mounted below.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router as api_router
from .config import CORS_ORIGINS
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the SQLite cache database + schema exist before serving requests.
    init_db()
    yield


app = FastAPI(title="StockPulse API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Also used by the frontend home page to prove wiring/CORS."""
    return {"status": "ok"}


app.include_router(api_router)
