import { useEffect, useLayoutEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type MouseEventParams,
  type Time,
} from 'lightweight-charts'
import type { Candle, IndicatorPoint, SentimentHistoryPoint } from '../api'

interface PriceChartProps {
  candles: Candle[]
  sma20: IndicatorPoint[]
  sma50: IndicatorPoint[]
  // Per-day sentiment timeline, rendered as a histogram overlay (may be empty).
  sentiment?: SentimentHistoryPoint[]
  // Fired when a point on the timeline (or any bar) is clicked, with YYYY-MM-DD.
  onSelectDate?: (date: string) => void
}

const CHART_HEIGHT = 420

// SMA overlay colors — kept in sync with the legend swatches in App.css.
const SMA20_COLOR = '#2f9e6b'
const SMA50_COLOR = '#e0952b'

// Sentiment histogram bar colors. Reddit days carry a bull/bear signal; apewisdom
// days are volume-only (net_score 0) and always read as neutral gray.
const SENTIMENT_UP = '#26a875'
const SENTIMENT_DOWN = '#e5484d'
const SENTIMENT_NEUTRAL = '#8b8b96'

// Dedicated price scale for the histogram so it lives in the bottom ~20% band and
// never fights the candlesticks for vertical space.
const SENTIMENT_SCALE_ID = 'sentiment'

function sentimentColor(p: SentimentHistoryPoint): string {
  if (p.source === 'apewisdom') return SENTIMENT_NEUTRAL
  if (p.net_score > 10) return SENTIMENT_UP
  if (p.net_score < -10) return SENTIMENT_DOWN
  return SENTIMENT_NEUTRAL
}

// lightweight-charts hands time back as a business-day string, a {year,month,day}
// object, or a UTC-seconds number depending on how it was set. Normalize to
// YYYY-MM-DD so callers get a stable date key.
function timeToDateStr(time: Time): string {
  if (typeof time === 'string') return time
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${time.year}-${pad(time.month)}-${pad(time.day)}`
}

const SOURCE_LABEL: Record<SentimentHistoryPoint['source'], string> = {
  reddit: 'Reddit',
  apewisdom: 'ApeWisdom',
  none: 'No data',
}

interface ThemeColors {
  background: string
  text: string
  grid: string
  border: string
  up: string
  down: string
}

function readTheme(): ThemeColors {
  const dark =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  return dark
    ? {
        background: 'transparent',
        text: '#9ca3af',
        grid: 'rgba(148, 148, 160, 0.12)',
        border: '#2e303a',
        up: '#26a875',
        down: '#e5484d',
      }
    : {
        background: 'transparent',
        text: '#5b5766',
        grid: 'rgba(120, 120, 140, 0.12)',
        border: '#e5e4e7',
        up: '#159968',
        down: '#d23b3f',
      }
}

function fmtNum(n: number | undefined): string {
  return typeof n === 'number' ? n.toFixed(2) : '—'
}

export function PriceChart({
  candles,
  sma20,
  sma50,
  sentiment,
  onSelectDate,
}: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const sma20SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const sma50SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const sentimentSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  // Date -> sentiment point, so the crosshair handler (wired once) can look up the
  // hovered bar's details without being re-created on every data change.
  const sentimentByDateRef = useRef<Map<string, SentimentHistoryPoint>>(new Map())
  // Latest onSelectDate, held in a ref so the once-only click subscription stays stable.
  const onSelectDateRef = useRef(onSelectDate)
  onSelectDateRef.current = onSelectDate

  // Create the chart once, wire up resize + crosshair tooltip, tear down on unmount.
  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container) return
    const theme = readTheme()

    const chart = createChart(container, {
      width: container.clientWidth,
      height: CHART_HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: theme.background },
        textColor: theme.text,
        fontFamily: 'system-ui, -apple-system, sans-serif',
      },
      grid: {
        vertLines: { color: theme.grid },
        horzLines: { color: theme.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border, timeVisible: false },
      autoSize: false,
    })
    chartRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: theme.up,
      downColor: theme.down,
      borderUpColor: theme.up,
      borderDownColor: theme.down,
      wickUpColor: theme.up,
      wickDownColor: theme.down,
    })
    candleSeriesRef.current = candleSeries

    const sma20Series = chart.addSeries(LineSeries, {
      color: SMA20_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    })
    sma20SeriesRef.current = sma20Series

    const sma50Series = chart.addSeries(LineSeries, {
      color: SMA50_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    })
    sma50SeriesRef.current = sma50Series

    // Sentiment volume histogram on its own price scale, pinned to the bottom
    // ~20% band so it never overlaps the candles.
    const sentimentSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: SENTIMENT_SCALE_ID,
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    })
    chart.priceScale(SENTIMENT_SCALE_ID).applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })
    sentimentSeriesRef.current = sentimentSeries

    // Keep the chart width in sync with its container.
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) chart.applyOptions({ width: Math.floor(w) })
    })
    ro.observe(container)

    // Floating OHLC tooltip driven by crosshair movement.
    function onCrosshairMove(param: MouseEventParams<Time>) {
      const tip = tooltipRef.current
      if (!tip) return
      if (
        !param.point ||
        param.time === undefined ||
        param.point.x < 0 ||
        param.point.y < 0
      ) {
        tip.style.display = 'none'
        return
      }
      const candle = param.seriesData.get(candleSeries) as
        | CandlestickData<Time>
        | undefined
      if (!candle) {
        tip.style.display = 'none'
        return
      }
      const s20 = param.seriesData.get(sma20Series) as
        | LineData<Time>
        | undefined
      const s50 = param.seriesData.get(sma50Series) as
        | LineData<Time>
        | undefined
      const up = candle.close >= candle.open

      // Sentiment for the hovered day, if the timeline has a bar there. net_score
      // is only meaningful for reddit-source days.
      const sPoint = sentimentByDateRef.current.get(timeToDateStr(param.time))
      let sentimentBlock = ''
      if (sPoint) {
        const color = sentimentColor(sPoint)
        const scoreRow =
          sPoint.source === 'reddit'
            ? `<span>Net</span><b>${sPoint.net_score > 0 ? '+' : ''}${sPoint.net_score.toFixed(1)}</b>`
            : ''
        sentimentBlock =
          `<div class="tt-sentiment">` +
          `<span class="tt-dot" style="background:${color}"></span>` +
          `<span class="tt-sentiment-src">${SOURCE_LABEL[sPoint.source]}</span>` +
          `<span class="tt-sentiment-grid">` +
          `<span>Vol</span><b>${sPoint.volume}</b>` +
          scoreRow +
          `</span>` +
          `</div>`
      }

      tip.innerHTML =
        `<div class="tt-date">${String(param.time)}</div>` +
        `<div class="tt-grid">` +
        `<span>O</span><b>${fmtNum(candle.open)}</b>` +
        `<span>H</span><b>${fmtNum(candle.high)}</b>` +
        `<span>L</span><b>${fmtNum(candle.low)}</b>` +
        `<span>C</span><b class="${up ? 'tt-up' : 'tt-down'}">${fmtNum(candle.close)}</b>` +
        `</div>` +
        `<div class="tt-sma">` +
        `<span class="tt-dot" style="background:${SMA20_COLOR}"></span>SMA20 <b>${fmtNum(s20?.value)}</b>` +
        `<span class="tt-dot" style="background:${SMA50_COLOR}"></span>SMA50 <b>${fmtNum(s50?.value)}</b>` +
        `</div>` +
        sentimentBlock

      // Position near the cursor, clamped inside the container.
      const cw = container!.clientWidth
      const tw = 168
      let left = param.point.x + 16
      if (left + tw > cw) left = param.point.x - tw - 16
      if (left < 0) left = 8
      tip.style.display = 'block'
      tip.style.left = `${left}px`
      tip.style.top = `12px`
    }
    chart.subscribeCrosshairMove(onCrosshairMove)

    // Clicking anywhere on the plot opens that day's "on this day" detail. We use
    // the crosshair time (so clicking a sentiment bar or its candle both work).
    function onClick(param: MouseEventParams<Time>) {
      if (!onSelectDateRef.current || param.time === undefined) return
      onSelectDateRef.current(timeToDateStr(param.time))
    }
    chart.subscribeClick(onClick)

    return () => {
      ro.disconnect()
      chart.unsubscribeCrosshairMove(onCrosshairMove)
      chart.unsubscribeClick(onClick)
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      sma20SeriesRef.current = null
      sma50SeriesRef.current = null
      sentimentSeriesRef.current = null
    }
  }, [])

  // Push new data whenever candles / indicators change.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const sma20Series = sma20SeriesRef.current
    const sma50Series = sma50SeriesRef.current
    const chart = chartRef.current
    if (!candleSeries || !sma20Series || !sma50Series || !chart) return

    const candleData: CandlestickData<Time>[] = candles.map((c) => ({
      time: c.date as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))
    const sma20Data: LineData<Time>[] = sma20.map((p) => ({
      time: p.date as Time,
      value: p.value,
    }))
    const sma50Data: LineData<Time>[] = sma50.map((p) => ({
      time: p.date as Time,
      value: p.value,
    }))

    candleSeries.setData(candleData)
    sma20Series.setData(sma20Data)
    sma50Series.setData(sma50Data)
    chart.timeScale().fitContent()
  }, [candles, sma20, sma50])

  // Push sentiment timeline into the histogram overlay. Empty/undefined => clear
  // the series (no bars, no error). Kept separate so it doesn't reset candles.
  useEffect(() => {
    const sentimentSeries = sentimentSeriesRef.current
    if (!sentimentSeries) return

    const points = sentiment ?? []
    const byDate = new Map<string, SentimentHistoryPoint>()
    const data: HistogramData<Time>[] = points.map((p) => {
      byDate.set(p.date, p)
      return {
        time: p.date as Time,
        value: p.volume,
        color: sentimentColor(p),
      }
    })
    sentimentByDateRef.current = byDate
    sentimentSeries.setData(data)
  }, [sentiment])

  return (
    <div className="chart-wrap">
      <div className="chart-legend">
        <span className="legend-item">
          <span className="legend-swatch legend-swatch--candle" />
          Price (OHLC)
        </span>
        <span className="legend-item">
          <span
            className="legend-swatch"
            style={{ background: SMA20_COLOR }}
          />
          SMA 20
        </span>
        <span className="legend-item">
          <span
            className="legend-swatch"
            style={{ background: SMA50_COLOR }}
          />
          SMA 50
        </span>
        {sentiment && sentiment.length > 0 && (
          <span className="legend-item">
            <span
              className="legend-swatch legend-swatch--histogram"
              style={{ background: SENTIMENT_NEUTRAL }}
            />
            Sentiment volume
          </span>
        )}
      </div>
      <div className="chart-container" ref={containerRef}>
        <div className="chart-tooltip" ref={tooltipRef} />
      </div>
    </div>
  )
}
