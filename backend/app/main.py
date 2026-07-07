"""StockPulse FastAPI application entrypoint.

Run locally (from the backend/ directory, with the venv active):

    uvicorn app.main:app --reload

Serves on http://localhost:8000 by default.

This is the Stage-0 scaffold: only `GET /health` is implemented. Search and
price endpoints are defined by the API contract in the top-level README and are
built by later stages against that contract.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
