import { useEffect, useState } from 'react'
import {
  ApiError,
  getPrices,
  type PriceRange,
  type PricesResponse,
} from './api'
import { SearchBox } from './components/SearchBox'
import { RangeSelector } from './components/RangeSelector'
import { PriceChart } from './components/PriceChart'
import { TrendBadge } from './components/TrendBadge'
import { SentimentPanel } from './components/SentimentPanel'
import './App.css'

const RANGE_LABELS: Record<PriceRange, string> = {
  '1mo': 'the past month',
  '6mo': 'the past 6 months',
  '1y': 'the past year',
  '5y': 'the past 5 years',
}

type Load =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ready'; data: PricesResponse }
  | { state: 'error'; message: string; notFound: boolean }

function App() {
  const [ticker, setTicker] = useState<string | null>(null)
  const [range, setRange] = useState<PriceRange>('6mo')
  const [load, setLoad] = useState<Load>({ state: 'idle' })

  // Fetch prices whenever the selected ticker or range changes.
  useEffect(() => {
    if (!ticker) return
    const controller = new AbortController()
    setLoad({ state: 'loading' })
    getPrices(ticker, range, controller.signal)
      .then((data) => setLoad({ state: 'ready', data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        const notFound = err instanceof ApiError && err.status === 404
        const message =
          err instanceof Error ? err.message : String(err)
        setLoad({ state: 'error', message, notFound })
      })
    return () => controller.abort()
  }, [ticker, range])

  const last =
    load.state === 'ready' && load.data.candles.length > 0
      ? load.data.candles[load.data.candles.length - 1]
      : null

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◈</span>
          <span className="brand-name">StockPulse</span>
        </div>
        <SearchBox onSelect={setTicker} selected={ticker} />
      </header>

      <main className="content">
        {!ticker && (
          <div className="empty-hero">
            <h1>Search a ticker to begin</h1>
            <p>
              Type a symbol or company name above — try{' '}
              <code>app</code> for Apple — then pick a suggestion to
              see its price chart, moving averages, and trend.
            </p>
          </div>
        )}

        {ticker && (
          <section className="ticker-panel">
            <div className="panel-head">
              <div className="panel-title">
                <h1 className="ticker-symbol">{ticker}</h1>
                {last && (
                  <span className="ticker-price">
                    {last.close.toFixed(2)}
                    <span className="ticker-price-label">last close</span>
                  </span>
                )}
              </div>
              <div className="panel-controls">
                {load.state === 'ready' && (
                  <TrendBadge
                    pctChange={load.data.indicators.pctChange}
                    trend={load.data.indicators.trend}
                    range={RANGE_LABELS[range]}
                  />
                )}
                <RangeSelector
                  value={range}
                  onChange={setRange}
                  disabled={load.state === 'loading'}
                />
              </div>
            </div>

            <div className="panel-body">
              {load.state === 'loading' && (
                <div className="chart-status">
                  <span className="spinner" aria-hidden="true" />
                  Loading {ticker} · {RANGE_LABELS[range]}…
                </div>
              )}

              {load.state === 'error' && (
                <div className="chart-status chart-status--error">
                  <strong>
                    {load.notFound
                      ? `No data for “${ticker}”`
                      : 'Could not load price data'}
                  </strong>
                  <span>{load.message}</span>
                  {load.notFound && (
                    <span className="chart-status-hint">
                      Check the symbol and try another search.
                    </span>
                  )}
                </div>
              )}

              {load.state === 'ready' && (
                <PriceChart
                  candles={load.data.candles}
                  sma20={load.data.indicators.sma20}
                  sma50={load.data.indicators.sma50}
                />
              )}
            </div>
          </section>
        )}

        {ticker && <SentimentPanel key={ticker} ticker={ticker} />}
      </main>
    </div>
  )
}

export default App
