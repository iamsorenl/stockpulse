import type { PriceRange } from '../api'

const RANGES: { value: PriceRange; label: string }[] = [
  { value: '1mo', label: '1M' },
  { value: '6mo', label: '6M' },
  { value: '1y', label: '1Y' },
  { value: '5y', label: '5Y' },
]

interface RangeSelectorProps {
  value: PriceRange
  onChange: (range: PriceRange) => void
  disabled?: boolean
}

export function RangeSelector({ value, onChange, disabled }: RangeSelectorProps) {
  return (
    <div className="range-selector" role="group" aria-label="Time range">
      {RANGES.map((r) => (
        <button
          key={r.value}
          type="button"
          className={
            'range-btn' + (r.value === value ? ' range-btn--active' : '')
          }
          aria-pressed={r.value === value}
          disabled={disabled}
          onClick={() => onChange(r.value)}
        >
          {r.label}
        </button>
      ))}
    </div>
  )
}
