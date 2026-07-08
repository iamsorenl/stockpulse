import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchSentiment,
  type SentimentLabel,
  type SentimentResponse,
} from '../api'

interface SentimentPanelProps {
  ticker: string
}

type Load =
  | { state: 'loading' }
  | { state: 'ready'; data: SentimentResponse }
  | { state: 'not-configured'; message: string }
  | { state: 'error'; message: string }

// Bucket the -100..100 net score into a bullish/bearish/neutral tone so the
// gauge and headline label agree.
function toneForScore(net: number): SentimentLabel {
  if (net > 5) return 'bullish'
  if (net < -5) return 'bearish'
  return 'neutral'
}

const TONE_LABEL: Record<SentimentLabel, string> = {
  bullish: 'Bullish',
  bearish: 'Bearish',
  neutral: 'Neutral',
}

// Format an ISO timestamp into a short, human "as of" string; fall back to the
// raw value if parsing ever fails.
function formatComputedAt(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function SentimentPanel({ ticker }: SentimentPanelProps) {
  const [load, setLoad] = useState<Load>({ state: 'loading' })

  // Re-fetch whenever the selected ticker changes; abort stale requests.
  useEffect(() => {
    const controller = new AbortController()
    setLoad({ state: 'loading' })
    fetchSentiment(ticker, controller.signal)
      .then((data) => setLoad({ state: 'ready', data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        const message = err instanceof Error ? err.message : String(err)
        if (err instanceof ApiError && err.status === 503) {
          setLoad({ state: 'not-configured', message })
        } else {
          setLoad({ state: 'error', message })
        }
      })
    return () => controller.abort()
  }, [ticker])

  return (
    <section className="sentiment" aria-label="Reddit sentiment">
      <div className="sentiment-head">
        <h2 className="sentiment-title">Reddit sentiment</h2>
      </div>

      {load.state === 'loading' && (
        <div className="sentiment-status">
          <span className="spinner" aria-hidden="true" />
          <span>Gauging the crowd on r/… this can take a few seconds.</span>
        </div>
      )}

      {load.state === 'not-configured' && (
        <div className="sentiment-status sentiment-status--muted">
          <strong>Reddit sentiment needs a Groq API key</strong>
          <span>
            Add a Groq key to <code>backend/.env</code> to enable this panel.
          </span>
        </div>
      )}

      {load.state === 'error' && (
        <div className="sentiment-status sentiment-status--error">
          <strong>Could not load sentiment</strong>
          <span>{load.message}</span>
        </div>
      )}

      {load.state === 'ready' && load.data.source === 'none' && (
        <div className="sentiment-status sentiment-status--muted">
          <strong>No Reddit discussion found yet</strong>
          <span>
            We haven&apos;t seen posts or comments about {load.data.ticker}{' '}
            recently.
          </span>
          <span className="sentiment-asof">
            as of {formatComputedAt(load.data.computed_at)}
          </span>
        </div>
      )}

      {load.state === 'ready' && load.data.source === 'apewisdom' && (
        <MentionVolumeBody data={load.data} />
      )}

      {load.state === 'ready' && load.data.source === 'reddit' && (
        <SentimentBody data={load.data} />
      )}
    </section>
  )
}

// Fallback view when post text is unavailable (archive source down) but we still
// have real Reddit mention-volume data from ApeWisdom. The sentiment gauge would
// be misleading here (net_score/bull/bear/neutral are 0), so we surface the raw
// volume signal instead.
function MentionVolumeBody({ data }: { data: SentimentResponse }) {
  const delta =
    data.mentions_prev != null ? data.volume - data.mentions_prev : null
  const deltaDir: 'up' | 'down' | 'flat' =
    delta == null || delta === 0 ? 'flat' : delta > 0 ? 'up' : 'down'
  const deltaArrow = deltaDir === 'up' ? '▲' : deltaDir === 'down' ? '▼' : '■'

  return (
    <div className="sentiment-body sentiment-volume">
      <div className="sentiment-volume-headline">
        <span className="sentiment-volume-value">
          {data.volume.toLocaleString()}
        </span>
        <span className="sentiment-volume-unit">
          mention{data.volume === 1 ? '' : 's'}
        </span>
        {delta != null && (
          <span
            className={`sentiment-volume-delta sentiment-volume-delta--${deltaDir}`}
          >
            {deltaArrow} {Math.abs(delta).toLocaleString()} vs yesterday
          </span>
        )}
      </div>

      {(data.upvotes != null || data.rank != null) && (
        <div className="sentiment-chips">
          {data.upvotes != null && (
            <span className="sentiment-chip">
              ▲ {data.upvotes.toLocaleString()} upvotes
            </span>
          )}
          {data.rank != null && (
            <span className="sentiment-chip">#{data.rank} trending</span>
          )}
        </div>
      )}

      <div className="sentiment-meta">
        <span className="sentiment-asof">
          as of {formatComputedAt(data.computed_at)}
        </span>
      </div>

      <p className="sentiment-volume-note">
        Mention volume via ApeWisdom — post-level sentiment resumes when the
        archive source is back.
      </p>
    </div>
  )
}

function SentimentBody({ data }: { data: SentimentResponse }) {
  const tone = toneForScore(data.net_score)
  const total = data.bull + data.bear + data.neutral
  // Gauge fill: map -100..100 onto 0..100% so the marker slides along the bar.
  const gaugePct = Math.min(100, Math.max(0, (data.net_score + 100) / 2))
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0)

  return (
    <div className="sentiment-body">
      <div className="sentiment-overall">
        <div className={`sentiment-score sentiment-score--${tone}`}>
          <span className="sentiment-score-value">
            {data.net_score > 0 ? '+' : ''}
            {data.net_score.toFixed(1)}
          </span>
          <span className="sentiment-score-label">{TONE_LABEL[tone]}</span>
        </div>
        <div className="sentiment-gauge" aria-hidden="true">
          <div className="sentiment-gauge-track" />
          <div
            className={`sentiment-gauge-marker sentiment-gauge-marker--${tone}`}
            style={{ left: `${gaugePct}%` }}
          />
        </div>
        <div className="sentiment-gauge-ends" aria-hidden="true">
          <span>Bearish</span>
          <span>Bullish</span>
        </div>
      </div>

      <div className="sentiment-breakdown">
        <div className="sentiment-bar" role="img" aria-label={`${data.bull} bullish, ${data.bear} bearish, ${data.neutral} neutral`}>
          <span
            className="sentiment-bar-seg sentiment-bar-seg--bull"
            style={{ width: `${pct(data.bull)}%` }}
          />
          <span
            className="sentiment-bar-seg sentiment-bar-seg--bear"
            style={{ width: `${pct(data.bear)}%` }}
          />
          <span
            className="sentiment-bar-seg sentiment-bar-seg--neutral"
            style={{ width: `${pct(data.neutral)}%` }}
          />
        </div>
        <div className="sentiment-chips">
          <span className="sentiment-chip sentiment-chip--bull">
            {data.bull} bullish
          </span>
          <span className="sentiment-chip sentiment-chip--bear">
            {data.bear} bearish
          </span>
          <span className="sentiment-chip sentiment-chip--neutral">
            {data.neutral} neutral
          </span>
        </div>
      </div>

      <div className="sentiment-meta">
        <span>
          {data.volume} mention{data.volume === 1 ? '' : 's'}
        </span>
        <span className="sentiment-asof">
          as of {formatComputedAt(data.computed_at)}
        </span>
      </div>

      {data.top.length > 0 && (
        <ul className="sentiment-top">
          {data.top.map((item) => (
            <li key={item.id} className="sentiment-top-item">
              <a
                href={item.permalink}
                target="_blank"
                rel="noopener noreferrer"
                className="sentiment-top-link"
              >
                <div className="sentiment-top-headline">
                  <span
                    className={`sentiment-tag sentiment-tag--${item.sentiment}`}
                  >
                    {TONE_LABEL[item.sentiment]}
                  </span>
                  <span className="sentiment-top-sub">r/{item.subreddit}</span>
                  <span className="sentiment-top-kind">{item.kind}</span>
                  <span className="sentiment-top-score">▲ {item.score}</span>
                </div>
                <p className="sentiment-top-text">{item.text}</p>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
