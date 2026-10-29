"""StockPulse FastAPI application entrypoint.

Run locally (from the backend/ directory, with the venv active):

    uvicorn app.main:app --reload

Serves on http://localhost:8000 by default.

Endpoints:
  GET /health                              liveness probe
  GET /api/search?q=                       symbol/name suggestions (SOR-152)
  GET /api/stocks/{ticker}/prices?range=   OHLCV + trend indicators (SOR-151/154)
  POST /api/chat                           ticker Q&A over cached data (plain text)

The search and price routes live in app/api.py and are mounted below.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .api import router as api_router
from .config import CORS_ORIGINS
from .db import init_db

logger = logging.getLogger(__name__)


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


if config.STORE == "dynamodb":
    from .dynamo_store import meter_request

    @app.middleware("http")
    async def free_tier_meter(request: Request, call_next):
        """Count each Lambda invocation toward the monthly free allowance.
        Only requests that came through Lambda carry x-amzn-request-context;
        the adapter's own local /health readiness checks don't, and aren't billed."""
        start = time.monotonic()
        response = await call_next(request)
        if "x-amzn-request-context" in request.headers:
            try:
                meter_request(time.monotonic() - start)
            except Exception:  # noqa: BLE001 - a meter failure must not break the request
                logger.exception("free-tier meter update failed")
        return response


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Also used by the frontend home page to prove wiring/CORS."""
    return {"status": "ok"}


app.include_router(api_router)


class _SPAStaticFiles(StaticFiles):
    """Serves the built frontend, falling back to index.html for client-side
    routes (e.g. deep-linking to a ticker) so React Router-less client nav
    still resolves. Leaves unknown /api/* paths as a plain 404 instead of the
    SPA shell, since /api and /health are matched above before this mount is
    ever reached.
    """

    async def get_response(self, path: str, scope):
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api/"):
                resp = await super().get_response("index.html", scope)
            else:
                raise
        # Hashed assets/ files are safe to cache; the HTML shell must revalidate
        # or browsers keep running the previous deploy's JS for hours.
        if not path.startswith("assets/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


# Single-service deploy: FastAPI also serves the built frontend at "/", so one
# Render web service covers both the API and the UI. Registered last so it
# never shadows /health or /api/* above. No-op locally unless you've run
# `npm run build` in frontend/ (frontend/dist/ is gitignored).
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", _SPAStaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
