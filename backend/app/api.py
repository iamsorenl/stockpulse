"""API routes for search and price data (SOR-151 / SOR-152 / SOR-154).

Mounted under the FastAPI app in main.py. All paths live beneath /api except
/health, which stays in main.py as the liveness probe.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import config, sentiment, stocks, trends
from .models import (
    OnThisDayResponse,
    PricesResponse,
    SearchResponse,
    SentimentHistoryResponse,
    SentimentResponse,
    TrendEventsResponse,
)
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


@router.get("/stocks/{ticker}/sentiment", response_model=SentimentResponse)
def sentiment_route(ticker: str):
    """Return Reddit-derived sentiment for `ticker` (cache-first, ~1h window).

    Requires a configured LLM scorer (Groq or Ollama); returns 503 when none is
    set. A `volume` of 0 means no discussion was found — still a 200 response.
    """
    if not config.SENTIMENT_CONFIGURED:
        raise HTTPException(
            status_code=503,
            detail="Sentiment scoring not configured. Set GROQ_API_KEY in backend/.env.",
        )
    return sentiment.get_sentiment(ticker)


@router.get("/stocks/{ticker}/sentiment/history", response_model=SentimentHistoryResponse)
def sentiment_history(
    ticker: str,
    days: int = Query(default=90, ge=1, le=1825, description="Look-back window in days."),
):
    """Daily sentiment snapshots for `ticker` over the last `days` (oldest-first).

    Reads accumulated snapshots — no live compute — so it's cheap and returns an
    empty list until history builds up.
    """
    return sentiment.get_history(ticker, days)


@router.get("/stocks/{ticker}/sentiment/on", response_model=OnThisDayResponse)
def sentiment_on_this_day(
    ticker: str,
    date: str = Query(..., description="Target day as YYYY-MM-DD."),
):
    """That day's snapshot (or null) plus the prior 7 days' run-up."""
    try:
        return sentiment.get_on_this_day(ticker, date)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid date; expected YYYY-MM-DD."
        ) from exc


@router.get("/stocks/{ticker}/trend-events", response_model=TrendEventsResponse)
def trend_events(
    ticker: str,
    range: str = Query(default="6mo", description="Time range: 1mo, 6mo, 1y, 5y."),
):
    """Days where price momentum and sentiment confirm or diverge.

    Reads accumulated snapshots + prices — empty until enough history builds up.
    """
    return trends.get_trend_events(ticker, range)
