# StockPulse

A live stock app that puts price trends and crowd sentiment on the same chart.

**Live:** https://t4brsvyq6qmnknw4lwquukjhhe0rsdqa.lambda-url.us-west-2.on.aws/
(AWS Lambda, inside the free tier; the first load after idle takes a few seconds)

- Search by symbol or company name, then view candlesticks with 20 and 50 day
  moving averages and an up / down / sideways trend read
- Sentiment scored by gpt-oss 20B on Groq from Reddit, ApeWisdom and news
  feeds, snapshotted daily so a timeline builds up per ticker
- Chart markers on the days where sentiment and price agree or split
- A per-ticker chat that answers from the loaded price and sentiment data
- Per-IP rate limits and a global daily cap on model calls, since anyone can
  use it

## Layout

```
stockpulse/
├── backend/            FastAPI app (Python)
│   ├── app/
│   │   ├── main.py     FastAPI entrypoint, /health, CORS, serves the built frontend
│   │   ├── api.py      /api routes: search, prices, sentiment, trend events, chat
│   │   ├── config.py   Env-configurable settings (single source of truth)
│   │   ├── db.py       SQLite cache + sentiment snapshot tables
│   │   ├── dynamo_store.py  DynamoDB version of db.py, plus the free-tier meter (AWS)
│   │   ├── providers.py  yfinance prices with a Stooq fallback
│   │   ├── sentiment.py  Groq scoring over Reddit, ApeWisdom and news
│   │   └── ratelimit.py  per-IP limits and the daily model-call cap
│   ├── scripts/        snapshot_tickers.py for scheduled sentiment capture
│   ├── tests/          pytest suite (offline)
│   ├── requirements.txt
│   └── .env.example
├── frontend/           Vite + React + TypeScript app
│   ├── src/
│   │   ├── api.ts      API client + shared contract types + base-URL resolution
│   │   ├── App.tsx     Search, chart, sentiment panel and chat
│   │   └── components/ PriceChart (lightweight-charts), SentimentPanel, TickerChat, ...
│   ├── vite.config.ts  Dev server (:5173) + proxy for /api and /health
│   └── .env.example
├── infra/              Terraform for the AWS deploy
├── Dockerfile          Lambda container image (frontend build + backend)
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
On startup the app creates `backend/stockpulse.db` (gitignored), which caches
price and sentiment responses and holds the daily sentiment snapshots.

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

Open http://localhost:5173. Sentiment and chat need `GROQ_API_KEY` in
`backend/.env`; prices and charts work without it.

## Tests

```bash
cd backend && ./.venv/bin/pip install pytest "moto[dynamodb]" && ./.venv/bin/pytest
```

## Sentiment snapshots (daily timeline)

Sentiment is snapshotted per ticker per UTC day into the `sentiment_snapshots`
table, building the timeline over time. Snapshots are captured automatically
whenever a ticker is viewed. To also capture tickers nobody happened to open,
run the refresh script:

```bash
cd backend && ./.venv/bin/python -m scripts.snapshot_tickers --tickers AAPL,NVDA,TSLA
# or, using the env var:
STOCKPULSE_TRACKED_TICKERS=AAPL,NVDA,TSLA ./.venv/bin/python -m scripts.snapshot_tickers
```

Optional daily capture via cron (install yourself; the app never touches your
crontab):

```cron
0 23 * * *  cd /path/to/stockpulse/backend && STOCKPULSE_TRACKED_TICKERS=AAPL,NVDA,TSLA ./.venv/bin/python -m scripts.snapshot_tickers >> snapshots.log 2>&1
```

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
  never `os.environ` directly.
  - `STOCKPULSE_DB_PATH` — SQLite cache file (default `backend/stockpulse.db`)
  - `STOCKPULSE_CORS_ORIGINS` — comma-separated allowed origins (default the Vite dev server)
- **Frontend**: only `VITE_`-prefixed vars reach the browser (see
  `frontend/.env.example`). API access goes through `src/api.ts`.

## API contract

The core endpoints. The TypeScript types live in `frontend/src/api.ts`; the
sentiment, trend-event and chat routes are in `backend/app/api.py`.

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

## Deploy (AWS)

One AWS Lambda function serves both the API and the built frontend at one URL
(`backend/app/main.py` mounts `frontend/dist` at `/` after `/api` and
`/health`). The app runs as a container image with the
[Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter), so it
is the same FastAPI app as local dev, unchanged.

```
browser -> Lambda Function URL -> container (FastAPI + built frontend)
                                    |-- DynamoDB: cache, sentiment history, Groq cap, usage meter
                                    '-- SSM Parameter Store: Groq key, read at startup
```

All infrastructure is Terraform in `infra/`. Pushes to `main` run the tests,
then build, push and deploy a new image (`.github/workflows/deploy.yml`). The
workflow logs in to AWS with GitHub OIDC, so GitHub stores no AWS keys.

### First-time setup

Needs the AWS CLI (configured), Terraform and Docker.

```bash
# 1. Store the Groq key (kept out of Terraform state and the repo)
aws ssm put-parameter --name /stockpulse/groq-api-key --type SecureString --value <key>

# 2. Create the image registry first; the function needs an image to exist
cd infra && terraform init && terraform apply -target=aws_ecr_repository.app

# 3. Build and push the first image, tagged "bootstrap"
ECR=$(terraform output -raw ecr_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${ECR%%/*}"
cd .. && docker build --platform linux/arm64 --provenance=false -t "$ECR:bootstrap" . && docker push "$ECR:bootstrap"

# 4. Create everything else, then print the site URL
cd infra && terraform apply && terraform output url
```

Then click the confirmation link in the AWS email to get alerts. To deploy
from a fork, change `github_repo` in `infra/github.tf` and `AWS_ROLE_ARN` in
the workflow.

### Staying free

- **Self-metering.** Each request adds its count and duration to a monthly
  total in DynamoDB. At 80% of Lambda's free allowance (800k requests or 320k
  GB-seconds) the app sets its own concurrency to 0, which turns the site off
  until you turn it back on:
  `aws lambda delete-function-concurrency --function-name stockpulse`
- **DynamoDB** uses fixed capacity (10 reads/10 writes per second), which the
  always-free tier covers. Bursts get slowed down, not billed.
- **Backups:** email alarms on function errors and throttles, and an AWS
  budget that emails at $0.50 and $1. Budgets only notify; they don't stop
  spending.
- ECR keeps the newest 3 images, which costs a few cents a month.

Notes:
- **Rate limits are per instance.** The per-IP limiter keeps its counts in
  memory, so a client spread across several warm instances can get a multiple
  of the limit (up to the account's concurrency limit, 10). The daily Groq cap
  is shared through DynamoDB, so the model budget holds exactly.
- **Yahoo often blocks datacenter IPs**, which can make `yfinance` fail from
  AWS even though it works locally. Price fetching falls back to Stooq's free
  daily CSV endpoint (`backend/app/providers.py`), so prices still load.
- Local dev and tests still use SQLite; the app only switches to DynamoDB when
  `STOCKPULSE_STORE=dynamodb` (set by Terraform on the function).
