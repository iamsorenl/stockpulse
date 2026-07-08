"""Pydantic response models mirroring the API contract (README + frontend/src/api.ts).

Kept 1:1 with the TypeScript types in the frontend so the JSON shapes agree.
FastAPI uses these as `response_model`s for validation + docs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

PriceRange = Literal["1mo", "6mo", "1y", "5y"]
Trend = Literal["up", "down", "sideways"]
MentionKind = Literal["post", "comment"]
Sentiment = Literal["bullish", "bearish", "neutral"]


class SearchResult(BaseModel):
    symbol: str
    name: str


class SearchResponse(BaseModel):
    results: list[SearchResult]


class Candle(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class IndicatorPoint(BaseModel):
    date: str
    value: float


class Indicators(BaseModel):
    sma20: list[IndicatorPoint]
    sma50: list[IndicatorPoint]
    pctChange: float
    trend: Trend


class PricesResponse(BaseModel):
    ticker: str
    range: PriceRange
    candles: list[Candle]
    indicators: Indicators


class ScoredMention(BaseModel):
    id: str
    kind: MentionKind
    subreddit: str
    score: int
    permalink: str
    text: str
    sentiment: Sentiment


class SentimentResponse(BaseModel):
    ticker: str
    net_score: float          # -100..100
    bull: int
    bear: int
    neutral: int
    volume: int               # 0 = no discussion found
    computed_at: str          # ISO-8601 string
    top: list[ScoredMention]
