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

// ----- Endpoints -----

// GET /health -> { "status": "ok" }
export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(apiUrl('/health'))
  if (!res.ok) {
    throw new Error(`Health check failed: HTTP ${res.status}`)
  }
  return (await res.json()) as HealthResponse
}
