import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchSentiment,
  fetchOnThisDay,
  type CombinedSentiment,
  type NewsSentiment,
  type OnThisDayResponse,
  type OnThisDayRunupPoint,
  type SentimentLabel,
  type SentimentResponse,
  type SentimentTopItem,
  type TrendEvent,
} from '../api'

interface SentimentPanelProps {
  ticker: string
  // When set (YYYY-MM-DD) the panel shows that day's captured snapshot instead of
  // the live view. Driven from App so a timeline bar-click and the date input
  // both feed the same state.
  date: string | null
  onDateChange: (date: string | null) => void
  // Trend events flagged on the price chart; used for a compact "Signals" line in
  // the live view (may be empty).
  events?: TrendEvent[]
}

type Load =
  | { state: 'loading' }
  | { state: 'ready'; data: SentimentResponse }
  | { state: 'not-configured'; message: string }
  | { state: 'error'; message: string }

type OnLoad =
  | { state: 'loading' }
  | { state: 'ready'; data: OnThisDayResponse }
  | { state: 'error'; message: string }

// Permalinks originate from a third-party archive; only ever put http(s) URLs in
// an href so a tampered `javascript:`/`data:` value can't execute on click.
function safeHref(url: string | undefined | null): string {
  return url && /^https?:\/\//i.test(url) ? url : '#'
}

// The subset of fields the body renderers use — satisfied by both the live
// SentimentResponse and a historical OnThisDaySnapshot.
interface SentimentBodyData {
  net_score: number
  bull: number
  bear: number
  neutral: number
  volume: number
  computed_at: string
  top: SentimentTopItem[]
  mentions_prev: number | null
  upvotes: number | null
  rank: number | null
}

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

// A YYYY-MM-DD day rendered as a short "Jul 2" (parsed as UTC to avoid tz drift).
function formatDay(ymd: string): string {
  const d = new Date(`${ymd}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return ymd
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}

function todayYmd(): string {
  return new Date().toISOString().slice(0, 10)
}

export function SentimentPanel({
  ticker,
  date,
  onDateChange,
  events,
}: SentimentPanelProps) {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [onLoad, setOnLoad] = useState<OnLoad>({ state: 'loading' })

  // Live sentiment: re-fetch whenever the ticker changes; abort stale requests.
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

  // "On this day": fetch only when a specific day is selected; abort stale requests.
  useEffect(() => {
    if (!date) return
    const controller = new AbortController()
    setOnLoad({ state: 'loading' })
    fetchOnThisDay(ticker, date, controller.signal)
      .then((data) => setOnLoad({ state: 'ready', data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        const message = err instanceof Error ? err.message : String(err)
        setOnLoad({ state: 'error', message })
      })
    return () => controller.abort()
  }, [ticker, date])

  return (
    <section className="sentiment" aria-label="Reddit sentiment">
      <div className="sentiment-head">
        <h2 className="sentiment-title">
          {date ? `Sentiment · ${formatDay(date)}` : 'Reddit sentiment'}
        </h2>
        <div className="sentiment-daypick">
          <label className="sentiment-daypick-label">
            <span>On this day</span>
            <input
              type="date"
              className="sentiment-date-input"
              value={date ?? ''}
              max={todayYmd()}
              onChange={(e) =>
                onDateChange(e.target.value ? e.target.value : null)
              }
            />
          </label>
          {date && (
            <button
              type="button"
              className="sentiment-back-btn"
              onClick={() => onDateChange(null)}
            >
              Back to live
            </button>
          )}
        </div>
      </div>

      {date ? (
        <OnThisDayView load={onLoad} date={date} />
      ) : (
        <LiveView load={load} events={events} />
      )}
    </section>
  )
}

// ---- Signals note ----

// One compact line summarizing the chart's flagged trend events, drawing the eye
// to the markers. Renders nothing when there are no events.
function SignalsNote({ events }: { events?: TrendEvent[] }) {
  if (!events || events.length === 0) return null
  const diverge = events.filter((e) => e.kind === 'diverge').length
  const confirm = events.length - diverge
  const parts: string[] = []
  if (diverge > 0) {
    parts.push(`${diverge} divergence${diverge === 1 ? '' : 's'} flagged`)
  }
  if (confirm > 0) {
    parts.push(`${confirm} confirmation${confirm === 1 ? '' : 's'}`)
  }
  return (
    <div className="sentiment-signals">
      <span className="sentiment-signals-icon" aria-hidden="true">
        ⚠
      </span>
      <span>
        {parts.join(' · ')} on the chart — see the markers above.
      </span>
    </div>
  )
}

// ---- Live view (current sentiment) ----

function LiveView({ load, events }: { load: Load; events?: TrendEvent[] }) {
  return (
    <>
      <SignalsNote events={events} />

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

      {load.state === 'ready' &&
        load.data.news != null &&
        load.data.news.volume > 0 && <NewsBody news={load.data.news} />}

      {load.state === 'ready' &&
        load.data.combined != null &&
        (load.data.combined.has_reddit || load.data.combined.has_news) && (
          <CombinedRead combined={load.data.combined} />
        )}
    </>
  )
}

// ---- On this day view (historical snapshot for a chosen date) ----

function OnThisDayView({ load, date }: { load: OnLoad; date: string }) {
  return (
    <>
      {load.state === 'loading' && (
        <div className="sentiment-status">
          <span className="spinner" aria-hidden="true" />
          <span>Loading sentiment for {formatDay(date)}…</span>
        </div>
      )}

      {load.state === 'error' && (
        <div className="sentiment-status sentiment-status--error">
          <strong>Could not load this day</strong>
          <span>{load.message}</span>
        </div>
      )}

      {load.state === 'ready' && load.data.snapshot === null && (
        <div className="sentiment-status sentiment-status--muted">
          <strong>No sentiment captured for {formatDay(date)}</strong>
          <span>
            History only accumulates from today forward, so this day has no
            snapshot.
          </span>
          <RunupStrip runup={load.data.runup} />
        </div>
      )}

      {load.state === 'ready' && load.data.snapshot !== null && (
        <div className="sentiment-body">
          {load.data.snapshot.captured === 'backfill' && <BackfilledBadge />}
          {load.data.snapshot.source === 'none' ? (
            <div className="sentiment-status sentiment-status--muted">
              <strong>No Reddit discussion that day</strong>
              <span className="sentiment-asof">
                as of {formatComputedAt(load.data.snapshot.computed_at)}
              </span>
            </div>
          ) : load.data.snapshot.source === 'apewisdom' ? (
            <MentionVolumeBody data={load.data.snapshot} />
          ) : (
            <SentimentBody data={load.data.snapshot} />
          )}
          <RunupStrip runup={load.data.runup} />
        </div>
      )}
    </>
  )
}

// Small muted provenance tag shown on the "on this day" view when the snapshot was
// reconstructed from historical archives rather than captured live that day. Live
// snapshots (the default) render nothing.
function BackfilledBadge() {
  return (
    <span
      className="sentiment-backfill-badge"
      title="Reconstructed from historical archives after the fact — not captured live that day."
    >
      backfilled
    </span>
  )
}

// Compact colored-bar strip of the prior days that exist (net_score/volume).
function runupTone(p: OnThisDayRunupPoint): 'up' | 'down' | 'neutral' {
  if (p.source === 'apewisdom') return 'neutral'
  if (p.net_score > 10) return 'up'
  if (p.net_score < -10) return 'down'
  return 'neutral'
}

function RunupStrip({ runup }: { runup: OnThisDayRunupPoint[] }) {
  if (runup.length === 0) return null
  const maxVol = Math.max(1, ...runup.map((r) => r.volume))
  return (
    <div className="runup">
      <div className="runup-label">Run-up · prior {runup.length} days</div>
      <div className="runup-bars">
        {runup.map((r) => {
          const heightPct = Math.max(10, Math.round((r.volume / maxVol) * 100))
          const tone = runupTone(r)
          const scorePart =
            r.source === 'reddit'
              ? ` · net ${r.net_score > 0 ? '+' : ''}${r.net_score.toFixed(1)}`
              : ''
          return (
            <div
              key={r.date}
              className="runup-col"
              title={`${formatDay(r.date)} · ${r.volume} mention${r.volume === 1 ? '' : 's'}${scorePart}`}
            >
              <div className="runup-track">
                <div
                  className={`runup-bar runup-bar--${tone}`}
                  style={{ height: `${heightPct}%` }}
                />
              </div>
              <span className="runup-date">{formatDay(r.date)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// Fallback view when post text is unavailable (archive source down) but we still
// have real Reddit mention-volume data from ApeWisdom. The sentiment gauge would
// be misleading here (net_score/bull/bear/neutral are 0), so we surface the raw
// volume signal instead.
function MentionVolumeBody({ data }: { data: SentimentBodyData }) {
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

// Overall net-score badge + sliding gauge. Shared by the crowd and news views so
// both read identically.
function ScoreGauge({ net_score }: { net_score: number }) {
  const tone = toneForScore(net_score)
  // Gauge fill: map -100..100 onto 0..100% so the marker slides along the bar.
  const gaugePct = Math.min(100, Math.max(0, (net_score + 100) / 2))
  return (
    <div className="sentiment-overall">
      <div className={`sentiment-score sentiment-score--${tone}`}>
        <span className="sentiment-score-value">
          {net_score > 0 ? '+' : ''}
          {net_score.toFixed(1)}
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
  )
}

// Bull / bear / neutral proportion bar + chips. Shared by the crowd and news views.
function Breakdown({
  bull,
  bear,
  neutral,
}: {
  bull: number
  bear: number
  neutral: number
}) {
  const total = bull + bear + neutral
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0)
  return (
    <div className="sentiment-breakdown">
      <div
        className="sentiment-bar"
        role="img"
        aria-label={`${bull} bullish, ${bear} bearish, ${neutral} neutral`}
      >
        <span
          className="sentiment-bar-seg sentiment-bar-seg--bull"
          style={{ width: `${pct(bull)}%` }}
        />
        <span
          className="sentiment-bar-seg sentiment-bar-seg--bear"
          style={{ width: `${pct(bear)}%` }}
        />
        <span
          className="sentiment-bar-seg sentiment-bar-seg--neutral"
          style={{ width: `${pct(neutral)}%` }}
        />
      </div>
      <div className="sentiment-chips">
        <span className="sentiment-chip sentiment-chip--bull">{bull} bullish</span>
        <span className="sentiment-chip sentiment-chip--bear">{bear} bearish</span>
        <span className="sentiment-chip sentiment-chip--neutral">
          {neutral} neutral
        </span>
      </div>
    </div>
  )
}

function SentimentBody({ data }: { data: SentimentBodyData }) {
  return (
    <div className="sentiment-body">
      <ScoreGauge net_score={data.net_score} />
      <Breakdown bull={data.bull} bear={data.bear} neutral={data.neutral} />

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
                href={safeHref(item.permalink)}
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

// Format an epoch-seconds timestamp into a short "Jul 2" day; null/invalid => ''.
function formatPublished(epochSeconds: number | null): string {
  if (epochSeconds == null) return ''
  const d = new Date(epochSeconds * 1000)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// Financial-news sentiment sub-section. Mirrors the crowd block's gauge +
// breakdown, then lists scored headlines linking out to each outlet.
function NewsBody({ news }: { news: NewsSentiment }) {
  return (
    <div className="sentiment-body sentiment-subsection">
      <div className="sentiment-subhead">
        <h3 className="sentiment-subhead-title">News</h3>
        <span className="sentiment-subhead-src">from financial news</span>
      </div>

      <ScoreGauge net_score={news.net_score} />
      <Breakdown bull={news.bull} bear={news.bear} neutral={news.neutral} />

      <div className="sentiment-meta">
        <span>
          {news.volume} article{news.volume === 1 ? '' : 's'}
        </span>
        <span className="sentiment-asof">
          as of {formatComputedAt(news.computed_at)}
        </span>
      </div>

      {news.top.length > 0 && (
        <ul className="sentiment-top">
          {news.top.map((item, i) => {
            const published = formatPublished(item.published_utc)
            return (
              <li key={`${item.url}-${i}`} className="sentiment-top-item">
                <a
                  href={safeHref(item.url)}
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
                    <span className="sentiment-top-sub">{item.outlet}</span>
                    {published && (
                      <span className="sentiment-top-kind">{published}</span>
                    )}
                  </div>
                  <p className="sentiment-top-text">{item.title}</p>
                </a>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// Describe how the combined score was blended, staying honest when only one side
// contributed so we never imply a Reddit read that doesn't exist.
function combinedCaption(c: CombinedSentiment): string {
  if (c.has_reddit && c.has_news) return 'Reddit + News, 50/50'
  if (c.has_news) return 'News only — Reddit archive unavailable'
  if (c.has_reddit) return 'Reddit only — no financial news found'
  return ''
}

// Compact blended read across the crowd and news signals.
function CombinedRead({ combined }: { combined: CombinedSentiment }) {
  const tone = toneForScore(combined.net_score)
  return (
    <div className="sentiment-combined">
      <div className="sentiment-subhead">
        <h3 className="sentiment-subhead-title">Combined</h3>
      </div>
      <div className="sentiment-combined-row">
        <span
          className={`sentiment-combined-score sentiment-combined-score--${tone}`}
        >
          <span className="sentiment-combined-value">
            {combined.net_score > 0 ? '+' : ''}
            {combined.net_score.toFixed(1)}
          </span>
          <span className="sentiment-combined-label">{TONE_LABEL[tone]}</span>
        </span>
        <span className="sentiment-combined-caption">
          {combinedCaption(combined)}
        </span>
      </div>
    </div>
  )
}
