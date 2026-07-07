"""API routes for search and price data (SOR-151 / SOR-152 / SOR-154).

Mounted under the FastAPI app in main.py. All paths live beneath /api except
/health, which stays in main.py as the liveness probe.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import stocks
from .models import PricesResponse, SearchResponse
from .symbols import search_symbols

router = APIRouter(prefix="/api")


@router.get("/search", response_model=SearchResponse)
def search(q: str = Query(default="", description="Symbol or company-name query")):
    """Return symbol + company-name suggestions matching `q`.

    Matches symbol OR name (case-insensitive substring). Empty/no-match query
    returns an empty results list — never an error.
    """
    return {"results": search_symbols(q)}


@router.get("/stocks/{ticker}/prices", response_model=PricesResponse)
def prices(
    ticker: str,
    range: str = Query(
        default="6mo",
        description="Time range: one of 1mo, 6mo, 1y, 5y.",
    ),
):
    """Return OHLCV candles + trend indicators for `ticker` over `range`.

    Cached in SQLite (see stocks.get_prices). Unknown ticker -> 404;
    unsupported range -> 400.
    """
    try:
        return stocks.get_prices(ticker, range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except stocks.UnknownTickerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
