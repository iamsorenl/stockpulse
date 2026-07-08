"""Pydantic response models mirroring the API contract (README + frontend/src/api.ts).

Kept 1:1 with the TypeScript types in the frontend so the JSON shapes agree.
FastAPI uses these as `response_model`s for validation + docs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

PriceRange = Literal["1mo", "6mo", "1y", "5y"]
Trend = Literal["up", "down", "sideways"]
MentionKind = Literal["post", "comment"]
Sentiment = Literal["bullish", "bearish", "neutral"]
SentimentSource = Literal["reddit", "apewisdom", "none"]


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
    net_score: float          # -100..100 (meaningful only when source == "reddit")
    bull: int
    bear: int
    neutral: int
    volume: int               # reddit: relevant mentions; apewisdom: total mentions; none: 0
    computed_at: str          # ISO-8601 string
    top: list[ScoredMention]
    source: SentimentSource = "reddit"
    mentions_prev: Optional[int] = None   # apewisdom: mentions 24h ago
    upvotes: Optional[int] = None         # apewisdom: total upvotes
    rank: Optional[int] = None            # apewisdom: trending rank


# --- M3 timeline ---------------------------------------------------------------

class SentimentHistoryPoint(BaseModel):
    date: str                 # UTC YYYY-MM-DD
    source: SentimentSource
    net_score: float
    volume: int
    bull: int
    bear: int
    neutral: int


class SentimentHistoryResponse(BaseModel):
    ticker: str
    points: list[SentimentHistoryPoint]   # oldest-first; gaps simply absent


class OnThisDaySnapshot(BaseModel):
    date: str
    computed_at: str
    source: SentimentSource
    net_score: float
    bull: int
    bear: int
    neutral: int
    volume: int
    mentions_prev: Optional[int] = None
    upvotes: Optional[int] = None
    rank: Optional[int] = None
    top: list[ScoredMention]


class OnThisDayRunupPoint(BaseModel):
    date: str
    source: SentimentSource
    net_score: float
    volume: int


class OnThisDayResponse(BaseModel):
    date: str
    snapshot: Optional[OnThisDaySnapshot] = None   # null = no data captured that day
    runup: list[OnThisDayRunupPoint]               # prior 7 days that exist
