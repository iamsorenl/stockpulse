import type { Trend } from '../api'

interface TrendBadgeProps {
  pctChange: number
  trend: Trend
  range: string
}

const TREND_META: Record<Trend, { label: string; arrow: string }> = {
  up: { label: 'Uptrend', arrow: '▲' },
  down: { label: 'Downtrend', arrow: '▼' },
  sideways: { label: 'Sideways', arrow: '▬' },
}

export function TrendBadge({ pctChange, trend, range }: TrendBadgeProps) {
  const meta = TREND_META[trend]
  const sign = pctChange > 0 ? '+' : ''
  return (
    <div className={`trend trend--${trend}`}>
      <div className="trend-pct">
        <span className="trend-arrow">{meta.arrow}</span>
        <span className="trend-value">
          {sign}
          {pctChange.toFixed(2)}%
        </span>
      </div>
      <div className="trend-meta">
        <span className="trend-label">{meta.label}</span>
        <span className="trend-range">over {range}</span>
      </div>
    </div>
  )
}
