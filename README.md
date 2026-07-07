# StockPulse

A stock charting web app: search tickers, view candlestick price history with
SMA overlays and a simple trend read. This repository is the **Stage 0 scaffold**
— wiring, conventions, and the API contract are in place; search, price, and
chart features are built by later stages against the contract below.

## Layout

```
stockpulse/
├── backend/            FastAPI app (Python)
│   ├── app/
│   │   ├── main.py     FastAPI entrypoint, /health, CORS, startup DB init
│   │   ├── config.py   Env-configurable settings (single source of truth)
│   │   └── db.py       SQLite cache helper (stdlib sqlite3) + `cache` table
│   ├── requirements.txt
│   └── .env.example
├── frontend/           Vite + React + TypeScript app
│   ├── src/
│   │   ├── api.ts      API client + shared contract types + base-URL resolution
│   │   ├── App.tsx     Home page; calls GET /health to prove wiring/CORS
│   │   └── vite-env.d.ts
│   ├── vite.config.ts  Dev server (:5173) + proxy for /api and /health
│   └── .env.example
└── README.md
```

## Prerequisites

- **Python** 3.11+ (developed on 3.12)
- **Node.js** 20+ (developed on 24) and npm

## Ports

| Service  | URL                     |
|----------|-------------------------|
| Backend  | http://localhost:8000   |
| Frontend | http://localhost:5173   |

## Run the backend (one command)

First-time setup (create venv + install deps):

```bash
cd backend && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

Then, to start it (the one command):

```bash
cd backend && ./.venv/bin/uvicorn app.main:app --reload
```

Serves on http://localhost:8000. `GET /health` → `{"status":"ok"}`.
On startup the app creates `backend/stockpulse.db` (gitignored) with a `cache`
table that later stages use to memoize price responses.

> If you prefer to activate the venv first (`source backend/.venv/bin/activate`),
> the equivalent command is `uvicorn app.main:app --reload` from `backend/`.

## Run the frontend (one command)

First-time setup (install deps):

```bash
cd frontend && npm install
```

Then, to start it (the one command):

```bash
cd frontend && npm run dev
```

Open http://localhost:5173. The home page calls the backend `GET /health` and
shows the result, proving the frontend ↔ backend wiring works.

## How the frontend reaches the backend

Two supported approaches; **the default is the Vite dev proxy**:

1. **Dev proxy (default, recommended for local dev).** `vite.config.ts` proxies
   requests to `/api/*` and `/health` to `http://localhost:8000`. The browser
   only ever talks to the Vite origin, so no CORS is involved. `src/api.ts`
   builds relative URLs when no base URL is configured.
2. **Env-configurable base URL.** Set `VITE_API_BASE_URL` in `frontend/.env`
   (see `.env.example`) to call the backend directly, bypassing the proxy — e.g.
   `VITE_API_BASE_URL=http://localhost:8000`. Useful for deployed builds. The
   backend enables CORS for `http://localhost:5173` (configurable via
   `STOCKPULSE_CORS_ORIGINS`) to support this path.

## Configuration conventions

- **Backend**: all settings live in `backend/app/config.py` and are read from
  environment variables (see `backend/.env.example`). Read config from there,
  never `os.environ` directly, in later stages.
  - `STOCKPULSE_DB_PATH` — SQLite cache file (default `backend/stockpulse.db`)
  - `STOCKPULSE_CORS_ORIGINS` — comma-separated allowed origins (default the Vite dev server)
- **Frontend**: only `VITE_`-prefixed vars reach the browser (see
  `frontend/.env.example`). API access goes through `src/api.ts`.

## API contract

Both backend and frontend agree on this. Later stages implement the search and
price endpoints against it; the TypeScript types already live in
`frontend/src/api.ts`.

### `GET /health`
```json
{ "status": "ok" }
```

### `GET /api/search?q=<text>`
Symbol OR company-name match. Empty list on no match — never an error.
```json
{ "results": [ { "symbol": "AAPL", "name": "Apple Inc." } ] }
```

### `GET /api/stocks/{ticker}/prices?range=<1mo|6mo|1y|5y>`
```json
{
  "ticker": "AAPL",
  "range": "6mo",
  "candles": [
    { "date": "2026-01-02", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0 }
  ],
  "indicators": {
    "sma20": [ { "date": "2026-01-02", "value": 0 } ],
    "sma50": [ { "date": "2026-01-02", "value": 0 } ],
    "pctChange": 0,
    "trend": "up"
  }
}
```
- `pctChange` — percent change over the selected range.
- `trend` — one of `"up"`, `"down"`, `"sideways"`.
- Unknown ticker → **HTTP 404** with `{ "detail": "..." }` (clear message).

## Charting

`lightweight-charts` is installed as a frontend dependency for later stages
(candlesticks + line overlays for the SMAs). No charts are built yet.
