import { useEffect, useState } from 'react'
import { getHealth, API_BASE_URL } from './api'
import './App.css'

type Status =
  | { state: 'loading' }
  | { state: 'ok'; payload: string }
  | { state: 'error'; message: string }

function App() {
  const [status, setStatus] = useState<Status>({ state: 'loading' })

  useEffect(() => {
    let cancelled = false
    getHealth()
      .then((res) => {
        if (!cancelled) {
          setStatus({ state: 'ok', payload: JSON.stringify(res) })
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err)
          setStatus({ state: 'error', message })
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const target = API_BASE_URL || '(Vite dev proxy → http://localhost:8000)'

  return (
    <main className="app">
      <h1>StockPulse</h1>
      <p className="tagline">Stage 0 scaffold &mdash; verifying frontend &harr; backend wiring.</p>

      <section className="health-card">
        <h2>Backend health</h2>
        {status.state === 'loading' && <p className="pending">Checking&hellip;</p>}
        {status.state === 'ok' && (
          <p className="ok">
            OK &mdash; backend responded: <code>{status.payload}</code>
          </p>
        )}
        {status.state === 'error' && (
          <p className="error">
            Could not reach backend: {status.message}
            <br />
            Is it running on <code>http://localhost:8000</code>? See the README.
          </p>
        )}
        <p className="meta">
          API base: <code>{target}</code>
        </p>
      </section>
    </main>
  )
}

export default App
