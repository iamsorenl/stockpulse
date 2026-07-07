import { useEffect, useLayoutEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type MouseEventParams,
  type Time,
} from 'lightweight-charts'
import type { Candle, IndicatorPoint } from '../api'

interface PriceChartProps {
  candles: Candle[]
  sma20: IndicatorPoint[]
  sma50: IndicatorPoint[]
}

const CHART_HEIGHT = 420

// SMA overlay colors — kept in sync with the legend swatches in App.css.
const SMA20_COLOR = '#2f9e6b'
const SMA50_COLOR = '#e0952b'

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

export function PriceChart({ candles, sma20, sma50 }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const sma20SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const sma50SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)

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
        `</div>`

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

    return () => {
      ro.disconnect()
      chart.unsubscribeCrosshairMove(onCrosshairMove)
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      sma20SeriesRef.current = null
      sma50SeriesRef.current = null
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
      </div>
      <div className="chart-container" ref={containerRef}>
        <div className="chart-tooltip" ref={tooltipRef} />
      </div>
    </div>
  )
}
