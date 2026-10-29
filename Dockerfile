# StockPulse as one container image for AWS Lambda.
#
# Stage 1 builds the React frontend. Stage 2 is the Python app that serves the
# API and the built frontend, same as on Render. The Lambda Web Adapter lets
# Lambda run a normal web server (uvicorn) instead of a Lambda-specific handler.

# --- Stage 1: frontend -------------------------------------------------------
# Full node image (not slim) because it ships git, which npm needs for the
# chat-ui dependency pulled from GitHub.
FROM node:22 AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend --------------------------------------------------------
FROM python:3.12-slim

# The adapter runs as a Lambda extension: it receives Lambda events and turns
# them into plain HTTP requests to uvicorn on port 8080.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter

# git is needed to pip-install llm-kit from GitHub.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# main.py looks for the built frontend at ../frontend/dist relative to backend/.
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# Lambda's filesystem is read-only except /tmp, so SQLite lives there.
ENV STOCKPULSE_DB_PATH=/tmp/stockpulse.db \
    AWS_LWA_PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
