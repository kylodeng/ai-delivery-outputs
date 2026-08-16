# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-service AI-assisted platform designed to help insurance underwriters assess customer risk profiles. The system exposes a streaming chat API (FastAPI, port 8000) backed by a LangGraph agent that orchestrates calls to specialist LLM assessors (Anthropic Claude and Google Gemini). When an underwriter submits a customer query, the agent calls a suite of tools — customer profile lookup, customer lookalike search, and a parallel multi-category underwriting risk assessment — before returning a structured `UnderwritingReport` with risk classification, findings, and follow-up items. Conversation state is persisted in Redis (LangGraph checkpointing), a PostgreSQL database backs the Chainlit frontend session layer, and three SQLite databases (customer profile, feature importance, model predictions, application profile) are mounted read-only into the backend container. A separate CI/CD pipeline of five GitHub Actions workflows (code review, tech docs, business docs, auto testing, UAT facilitation) automates AI-generated documentation and QA artefacts.

---

## 2. Health Checks

### 2.1 Backend API

```bash
# Should return: {"status": "ok"}
curl -f http://localhost:8000/health
```

### 2.2 Docker Compose service status

```bash
docker compose ps
# All services should show "running (healthy)" or "running"
```

Expected output:

| Service | Expected State |
|---|---|
| `backend` | `running (healthy)` |
| `frontend` | `running` |
| `redis` | `running` |
| `postgres` | `running` |

### 2.3 Redis connectivity

```bash
docker compose exec redis redis-cli PING
# Expected: PONG
```

### 2.4 PostgreSQL connectivity

```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 2.5 Backend container health (Docker)

```bash
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{.State.Health.Status}}'
# Expected: healthy
```

### 2.6 LLM API reachability

```bash
# Anthropic
curl -s -o /dev/null -w "%{http_code}" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  https://api.anthropic.com/v1/models

# Google (Gemini)
curl -s -o /dev/null -w "%{http_code}" \
  "https://generativelanguage.googleapis.com/v1/models?key=$GOOGLE_API_KEY"
```

### 2.7 Frontend reachability

```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns 503 or connection refused | Backend container crashed or failed health check | 1. `docker compose logs backend --tail=50` to identify error. 2. `docker compose restart backend`. 3. If recurring, check for missing env vars in `.env`. |
| `KeyError: 'ANTHROPIC_API_KEY'` in backend logs | Required environment variable not set | 1. Verify `.env` file exists at repo root. 2. Confirm `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` are populated. 3. `docker compose down && docker compose up -d` to reload env. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in request maps to `None` in `LLMS.model_mapper` | 1. Confirm request sends `model` as one of: `anthropic`, `anthropic-fast`, `gemini`. 2. Check `backend/modules/LLMS.py` — `azure` and `openai` are stubbed as `None`. 3. [TODO: Are Azure/OpenAI providers expected to be enabled?] |
| Agent loops or never returns `final_answer` | LLM not emitting `{"action": "done", ...}` JSON; history context overflow | 1. Check backend logs for repeated `[TOOL START]` without `[TOOL END]`. 2. Reduce `temperature` in request. 3. Check `SPECIALIST_MAX_TOKENS` / `AGGREGATOR_MAX_TOKENS` in `config.yml` are not too low. 4. Restart backend to clear in-memory state. |
| Redis connection refused / `ConnectionRefusedError` | Redis container not running or `REDIS_HOST` misconfigured | 1. `docker compose ps redis` — confirm running. 2. `docker compose restart redis`. 3. Verify `REDIS_HOST=redis` in backend environment. 4. Note: TODO in `graph.py` — Redis is not persistent across serverless restarts; conversation history lost on restart. |
| `psycopg2.OperationalError` or frontend DB errors | PostgreSQL not ready or missing init SQL | 1. `docker compose logs postgres --tail=30`. 2. `docker compose restart postgres`. 3. Verify `./postgres/init.sql` exists. 4. Check `postgres_data` volume is not corrupted: `docker volume inspect underwriting_chatbot-main_postgres_data`. |
| SQLite database not found (`/data/customer_profile.db`) | Database files not present at expected mount path | 1. Confirm `./database/` directory exists with all four `.db` files. 2. Check `docker compose.yml` volume mounts. 3. [TODO: Where are the canonical database files sourced/generated from?] |
| Streaming chat hangs mid-response | SSE connection dropped; LLM API rate limit or timeout | 1. Check backend logs for `[TOOL END]` never appearing. 2. Check Anthropic/Google API status pages. 3. Retry request. 4. If rate-limited, reduce concurrent users or add request queuing. |
| `UnderwritingReport` JSON parse failure (aggregator) | Aggregator LLM output exceeded `aggregator_max_tokens: 8000` or malformed JSON | 1. `docker compose logs backend` for `[AGGREGATOR]` token counts. 2. Increase `aggregator_max_tokens` in `config.yml`. 3. Retry with `mode=fast` instead of `mode=deep`. |
| GitHub Actions workflow failing (`tool1`–`tool5`) | Missing repository secrets | 1. Verify `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are set in GitHub repo Settings → Secrets. 2. Check workflow run logs for the failing step. |
| Frontend cannot reach backend | `BACKEND_URL` misconfigured or backend not healthy | 1. Confirm `BACKEND_URL=http://backend:8000` in `docker-compose.yml`. 2. Verify backend healthcheck passes before frontend starts (`depends_on: condition: service_healthy`). 3. `docker compose logs frontend`. |
| `chart` events not emitting to frontend | `_charts_sent` set already contains `(session_id, field)` key | 1. This is by design — charts are deduplicated per session. 2. Use a new `session_id` to force re-emission. 3. Restart backend to clear in-memory `_charts_sent`. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose v2 installed
- `.env` file at repo root with all required environment variables (see Section 5)
- SQLite database files present in `./database/`
- `./postgres/init.sql` present

### 4.1 Initial / Fresh Deployment

```bash
# 1. Clone repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create and populate .env (see Environment Variables in README)
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with real values

# 3. Build all images
docker compose build --no-cache

# 4. Start infrastructure first, then application
docker compose up -d redis postgres

# 5. Wait for postgres to be ready
sleep 10
docker compose exec postgres pg_isready -U chainlit -d chainlit

# 6. Start backend and frontend
docker compose up -d backend frontend

# 7. Verify all services healthy
docker compose ps
curl -f http://localhost:8000/health
```

### 4.2 Code Update / Redeployment

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild only changed services (Docker layer cache applies)
docker compose build backend frontend

# 3. Rolling restart — infrastructure services (redis, postgres) stay up
docker compose up -d --no-deps backend
# Wait for health check to pass before restarting frontend
sleep 20
docker compose up -d --no-deps frontend

# 4. Confirm healthy
docker compose ps
curl -f http://localhost:8000/health
```

### 4.3 Config-only Change (`config.yml`)

```bash
# config.yml is baked into the image at build time — must rebuild backend
docker compose build backend
docker compose up -d --no-deps backend
```

### 4.4 Rollback Steps

```bash
# Option A: Roll back to a previous Docker image tag (if images are tagged)
# [TODO: Confirm image registry and tagging strategy]
docker compose stop backend frontend
docker tag <registry>/backend:<previous-tag> <registry>/backend:latest
docker compose up -d --no-deps backend frontend

# Option B: Roll back via git and rebuild
git log --oneline -10          # identify target commit
git checkout <commit-sha>
docker compose build backend frontend
docker compose up -d --no-deps backend frontend

# Option C: Emergency — restart current containers
docker compose restart backend frontend

# Verify rollback
curl -f http://localhost:8000/health
docker compose logs backend --tail=30
```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Source | Warning Threshold | Notes |
|---|---|---|---|
| `/health` endpoint response | Backend HTTP | Non-200 response | Docker healthcheck polls every 10s |
| Backend container restarts | Docker | > 1 restart in 10 min | `docker compose ps` restart count |
| LLM token usage (specialist) | Backend logs `[SPECIALIST]` | `out` > 1400 tokens | Cap is 1500; runaway output risk |
| LLM token usage (aggregator) | Backend logs `[AGGREGATOR]` | `out` > 7500 tokens | Cap is 8000 |
| Tool execution time | Backend logs `[TOOL END]` | > 30s per tool | Indicates LLM or downstream timeout |
| Redis memory | `docker stats` / Redis INFO | > 80% memory | Conversation checkpoint growth |
| PostgreSQL disk | Docker volume | [TODO: set threshold] | `postgres_data` volume growth |
| GitHub Actions workflow failures | GitHub Actions UI / email | Any failure | Secrets expiry, API quota |

### 5.2 Logs to Watch

```bash
# Backend application logs (all tool calls, LLM timings, errors)
docker compose logs -f backend

# Filter for errors only
docker compose logs backend 2>&1 | grep -iE "error|exception|traceback|failed"

# LLM performance summary
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL"

# Redis logs
docker compose logs -f redis

# PostgreSQL logs
docker compose logs -f postgres
```

### 5.3 Backend Log Patterns

| Log Pattern | Meaning |
|---|---|
| `[CHAT] session=... msg=...` | New chat request received |
| `[TOOL START] <name>` | Agent called a tool |
| `[TOOL END] <name> time=Xs` | Tool completed; note elapsed time |
| `[SPECIALIST] category=... in=X out=Y time=Zs` | Individual LLM specialist call metrics |
| `[AGGREGATOR] in=X out=Y time=Zs` | Final aggregation LLM call metrics |
| `[ASSESSMENT] Starting — N specialist calls` | Underwriting assessment initiated |

### 5.4 Alerting

> [TODO: No alerting infrastructure (PagerDuty, Datadog, Prometheus, CloudWatch, etc.) is configured in this codebase. Instrument the `/health` endpoint with an external uptime monitor and set up log-based alerts for `ERROR` and `CRITICAL` patterns.]

**Recommended immediate actions:**
- Set up an uptime monitor (e.g. UptimeRobot, Datadog Synthetics) on `GET /health`
- Configure Docker log driver to ship to a central log platform (e.g. ELK, Splunk, Azure Monitor)
- Alert on container restart events

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call contact] | Backend down > 5 min; all restart attempts failed |
| L2 | Backend Lead | [TODO: fill in backend lead contact] | LLM integration failures; data model errors; Redis persistence issues |
| L3 | Platform/Infra Lead | [TODO: fill in infra contact] | Docker host issues; database corruption; volume loss |
| L4 | Anthropic Support | [Anthropic Support Portal](https://support.anthropic.com) | Claude API outage or billing/quota issues |
| L4 | Google Cloud Support | [TODO: GCP support link/account] | Gemini API outage |
| Product Owner | [TODO: fill in PO contact] | [TODO] | Business-critical outage affecting underwriters |

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop and remove volumes (DESTRUCTIVE — loses postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend

# Rebuild and restart backend only
docker compose build backend && docker compose up -d --no-deps backend

# View all service status
docker compose ps

# Follow all logs
docker compose logs -f

# Follow backend logs only
docker compose logs -f backend

# View last 100 lines of backend logs
docker compose logs --tail=100 backend
```

### Health & Debugging

```bash
# API health check
curl -f http://localhost:8000/health

# Test chat endpoint (streaming)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Tell me about customer CUST00000001","temperature":0.3,"session_id":"test-001","model":"anthropic-fast","mode":"fast"}'

# Inspect backend container environment
docker compose exec backend env | grep -E "REDIS|ANTHROPIC|GOOGLE|POSTGRES"

# Open a shell in the backend container
docker compose exec backend bash

# Check Python dependencies installed in container
docker compose exec backend pip list | grep -E "langchain|anthropic|fastapi|redis"
```

### Redis

```bash
# Ping Redis
docker compose exec redis redis-cli PING

# List all LangGraph checkpoint keys (conversation state)
docker compose exec redis redis-cli KEYS "*"

# Check Redis memory usage
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Flush all Redis data (DESTRUCTIVE — clears all conversation checkpoints)
docker compose exec redis redis-cli FLUSHALL
```

### PostgreSQL

```bash
# Check PostgreSQL readiness
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Connect to chainlit DB
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# Check disk usage of postgres volume
docker system df -v | grep postgres
```

### SQLite Databases (mounted read-only)

```bash
# List mounted databases inside backend container
docker compose exec backend ls -lh /data/

# Query customer profile (example)
docker compose exec backend sqlite3 /data/customer_profile.db \
  "SELECT * FROM sqlite_master WHERE type='table';"
# [TODO: confirm table names in each SQLite database]
```

### GitHub Actions (CI/CD)

```bash
# Manually trigger tech docs generation (requires GitHub CLI)
gh workflow run tool2_