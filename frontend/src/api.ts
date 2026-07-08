// Central API client + shared response types.
//
// API base URL resolution:
//   - Default ("" / unset): requests use relative paths (e.g. "/health"),
//     which the Vite dev proxy forwards to the backend. This is the recommended
//     local-dev setup — no CORS needed in the browser.
//   - If VITE_API_BASE_URL is set (e.g. "http://localhost:8000"), requests go
//     directly to that origin, bypassing the proxy. Useful for deployed builds.
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

function apiUrl(path: string): string {
  // path should start with "/". When API_BASE_URL is empty this yields a
  // relative URL handled by the Vite proxy.
  return `${API_BASE_URL}${path}`
}

// ----- Contract types (kept in sync with the backend API contract in README) -----

export interface HealthResponse {
  status: string
}

export interface SearchResult {
  symbol: string
  name: string
}

export interface SearchResponse {
  results: SearchResult[]
}

export type PriceRange = '1mo' | '6mo' | '1y' | '5y'

export interface Candle {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface IndicatorPoint {
  date: string
  value: number
}

export type Trend = 'up' | 'down' | 'sideways'

export interface Indicators {
  sma20: IndicatorPoint[]
  sma50: IndicatorPoint[]
  pctChange: number
  trend: Trend
}

export interface PricesResponse {
  ticker: string
  range: PriceRange
  candles: Candle[]
  indicators: Indicators
}

export type SentimentLabel = 'bullish' | 'bearish' | 'neutral'
export type SentimentKind = 'post' | 'comment'

export interface SentimentTopItem {
  id: string
  kind: SentimentKind
  subreddit: string
  score: number
  permalink: string
  text: string
  sentiment: SentimentLabel
}

// Where the sentiment data came from:
//   'reddit'    => full text sentiment (net_score/bull/bear/neutral/top populated)
//   'apewisdom' => post text unavailable; only real Reddit mention-volume data
//                  (net_score/bull/bear/neutral are 0, top is empty; mentions_prev/
//                  upvotes/rank carry the volume view's numbers)
//   'none'      => no discussion found yet
export type SentimentSource = 'reddit' | 'apewisdom' | 'none'

// How a sentiment snapshot was captured:
//   'live'     => captured that day, as the day happened
//   'backfill' => reconstructed from historical archives after the fact
export type Captured = 'live' | 'backfill'

// ----- Financial news sentiment (separate signal from the Reddit crowd) -----

// One scored headline in the news view. `published_utc` is an epoch-seconds float
// (may be null when the outlet didn't supply a timestamp).
export interface NewsArticle {
  title: string
  outlet: string // e.g. "Yahoo Finance"
  url: string
  published_utc: number | null
  sentiment: SentimentLabel
}

// Aggregated financial-news sentiment. `volume` is 0 when no articles were found.
export interface NewsSentiment {
  net_score: number // -100..100, >0 bullish, <0 bearish
  bull: number
  bear: number
  neutral: number
  volume: number // article count; 0 => no news found
  computed_at: string // ISO timestamp
  top: NewsArticle[]
}

// A blended read across the Reddit crowd and financial news. The has_* flags say
// which sides actually contributed, so the UI can label the blend honestly.
export interface CombinedSentiment {
  net_score: number // -100..100
  has_reddit: boolean
  has_news: boolean
}

export interface SentimentResponse {
  ticker: string
  net_score: number // -100..100, >0 bullish, <0 bearish
  bull: number
  bear: number
  neutral: number
  volume: number // 0 => no discussion found yet
  computed_at: string // ISO timestamp
  top: SentimentTopItem[]
  source: SentimentSource // NEW: which pipeline produced this payload
  mentions_prev: number | null // apewisdom only: mentions 24h ago
  upvotes: number | null // apewisdom only: total upvotes
  rank: number | null // apewisdom only: trending rank
  news: NewsSentiment | null // NEW: financial-news sentiment (null when unavailable)
  combined: CombinedSentiment | null // NEW: blended Reddit + news read
}

// ----- Sentiment timeline (history) -----

// One day of the accumulated sentiment history. `points` is oldest-first and may
// be empty/sparse — history only accrues from today forward.
//   source 'reddit'    => net_score/bull/bear/neutral carry a real signal
//   source 'apewisdom' => volume only (net_score/bull/bear/neutral are 0)
//   source 'none'      => nothing captured that day
export interface SentimentHistoryPoint {
  date: string // YYYY-MM-DD
  source: SentimentSource
  net_score: number
  volume: number
  bull: number
  bear: number
  neutral: number
  captured: Captured // 'live' captured that day vs 'backfill' reconstructed later
}

export interface SentimentHistoryResponse {
  ticker: string
  points: SentimentHistoryPoint[]
}

// ----- Sentiment "on this day" -----

// A prior-day entry in the run-up strip (only days that actually exist).
export interface OnThisDayRunupPoint {
  date: string // YYYY-MM-DD
  source: SentimentSource
  net_score: number
  volume: number
}

// The captured snapshot for a specific day. Shape mirrors SentimentResponse but
// carries the historical `date` it was captured for.
export interface OnThisDaySnapshot {
  date: string // YYYY-MM-DD
  computed_at: string // ISO timestamp
  source: SentimentSource
  net_score: number
  bull: number
  bear: number
  neutral: number
  volume: number
  mentions_prev: number | null
  upvotes: number | null
  rank: number | null
  top: SentimentTopItem[]
  captured: Captured // 'live' captured that day vs 'backfill' reconstructed later
}

export interface OnThisDayResponse {
  date: string // the requested day, YYYY-MM-DD
  snapshot: OnThisDaySnapshot | null // null => nothing captured that day
  runup: OnThisDayRunupPoint[] // prior 7 days that exist, oldest-first
}

// ----- Price/sentiment trend events (markers on the price chart) -----

// A single flagged day where price action and crowd sentiment either confirm or
// diverge from each other. `date` aligns to a candle on the price chart.
//   kind 'confirm' => price and sentiment agree (both bullish or both bearish)
//   kind 'diverge' => price and sentiment disagree (worth a closer look)
export interface TrendEvent {
  date: string // YYYY-MM-DD
  kind: 'confirm' | 'diverge'
  note: string // human-readable explanation
  price_change: number // % change that day
  sentiment: number // -100..100 net score
}

export interface TrendEventsResponse {
  ticker: string
  events: TrendEvent[] // may be empty (history accrues slowly)
}

// Error carrying the HTTP status so callers can distinguish, e.g., a 404
// (unknown ticker) from a network/500 failure and render the right message.
export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// Pull the backend's { "detail": "..." } message off an error response, falling
// back to a generic status string when the body isn't the expected shape.
async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json()
    if (
      body &&
      typeof body === 'object' &&
      'detail' in body &&
      typeof (body as { detail: unknown }).detail === 'string'
    ) {
      return (body as { detail: string }).detail
    }
  } catch {
    // Non-JSON body — fall through to the generic message.
  }
  return `Request failed: HTTP ${res.status}`
}

// ----- Endpoints -----

// GET /health -> { "status": "ok" }
export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(apiUrl('/health'))
  if (!res.ok) {
    throw new Error(`Health check failed: HTTP ${res.status}`)
  }
  return (await res.json()) as HealthResponse
}

// GET /api/search?q=<text> -> { results: [{ symbol, name }, ...] }
// Empty list on no match; never an error from the backend for a valid request.
export async function searchStocks(
  q: string,
  signal?: AbortSignal,
): Promise<SearchResult[]> {
  const res = await fetch(apiUrl(`/api/search?q=${encodeURIComponent(q)}`), {
    signal,
  })
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res))
  }
  const body = (await res.json()) as SearchResponse
  return body.results
}

// GET /api/stocks/{ticker}/prices?range=<1mo|6mo|1y|5y>
// Unknown ticker -> 404 (ApiError with status 404 and the backend's detail).
export async function getPrices(
  ticker: string,
  range: PriceRange,
  signal?: AbortSignal,
): Promise<PricesResponse> {
  const res = await fetch(
    apiUrl(
      `/api/stocks/${encodeURIComponent(ticker)}/prices?range=${encodeURIComponent(range)}`,
    ),
    { signal },
  )
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res))
  }
  return (await res.json()) as PricesResponse
}

// GET /api/stocks/{ticker}/sentiment
// 503 when sentiment isn't configured (no Groq key) — ApiError with status 503
// and the backend's detail. 200 returns the aggregated Reddit sentiment.
export async function fetchSentiment(
  ticker: string,
  signal?: AbortSignal,
): Promise<SentimentResponse> {
  const res = await fetch(
    apiUrl(`/api/stocks/${encodeURIComponent(ticker)}/sentiment`),
    { signal },
  )
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res))
  }
  return (await res.json()) as SentimentResponse
}

// GET /api/stocks/{ticker}/sentiment/history?days=<n>
// Returns the accumulated per-day sentiment timeline (oldest-first, may be empty).
export async function fetchSentimentHistory(
  ticker: string,
  days: number,
  signal?: AbortSignal,
): Promise<SentimentHistoryResponse> {
  const res = await fetch(
    apiUrl(
      `/api/stocks/${encodeURIComponent(ticker)}/sentiment/history?days=${encodeURIComponent(days)}`,
    ),
    { signal },
  )
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res))
  }
  return (await res.json()) as SentimentHistoryResponse
}

// GET /api/stocks/{ticker}/sentiment/on?date=YYYY-MM-DD
// A malformed date returns 400 (ApiError with status 400). A valid day with no
// captured data returns { snapshot: null, runup: [...] }.
export async function fetchOnThisDay(
  ticker: string,
  date: string,
  signal?: AbortSignal,
): Promise<OnThisDayResponse> {
  const res = await fetch(
    apiUrl(
      `/api/stocks/${encodeURIComponent(ticker)}/sentiment/on?date=${encodeURIComponent(date)}`,
    ),
    { signal },
  )
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res))
  }
  return (await res.json()) as OnThisDayResponse
}

// GET /api/stocks/{ticker}/trend-events?range=<1mo|6mo|1y|5y>
// Returns the flagged confirm/diverge days for the range (may be empty). Callers
// treat a failure as "no markers" — there is no error UI for this overlay.
export async function fetchTrendEvents(
  ticker: string,
  range: PriceRange,
  signal?: AbortSignal,
): Promise<TrendEventsResponse> {
  const res = await fetch(
    apiUrl(
      `/api/stocks/${encodeURIComponent(ticker)}/trend-events?range=${encodeURIComponent(range)}`,
    ),
    { signal },
  )
  if (!res.ok) {
    throw new ApiError(res.status, await readErrorDetail(res))
  }
  return (await res.json()) as TrendEventsResponse
}
