# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The backend is a Python FastAPI application that orchestrates a LangGraph agent (backed by Anthropic Claude or Google Gemini LLMs) with three core tools: customer profile lookup, customer lookalike analysis, and a parallel multi-specialist underwriting risk assessment engine. Assessment results are structured against a CatBoostClassifier model card and returned as a typed `UnderwritingReport`. The system is containerised via Docker Compose, with Redis providing LangGraph checkpoint/session persistence, PostgreSQL backing the frontend (Chainlit) session store, and SQLite databases supplying read-only customer, application, and model prediction data. A suite of five GitHub Actions CI/CD workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) automates AI-driven delivery tooling against the repository using Claude via the Anthropic API.

---

## 2. Health Checks

Perform the following checks in order to confirm the service is fully operational.

### 2.1 Container Status
```bash
docker compose ps
```
All four services (`redis`, `postgres`, `backend`, `frontend`) should show status `running (healthy)` or `Up`.

### 2.2 Backend API Health Endpoint
```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### 2.3 Redis Connectivity
```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### 2.4 PostgreSQL Connectivity
```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 2.5 Frontend Availability
```bash
curl -f http://localhost:8080
# Expected: HTTP 200 (Chainlit UI loads)
```

### 2.6 LLM API Reachability
```bash
# Verify Anthropic API key is active
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01"
# Expected: HTTP 200 with model list

# Verify Google API key (if Gemini model in use)
curl "https://generativelanguage.googleapis.com/v1/models?key=$GOOGLE_API_KEY"
# Expected: HTTP 200 with model list
```

### 2.7 SQLite Database Files Present (inside backend container)
```bash
docker compose exec backend ls -lh \
  /data/customer_profile.db \
  /data/feature_importance.db \
  /data/model_predictions.db \
  /data/application_profile.db
# Expected: all four files listed with non-zero size
```

### 2.8 Backend Chat Endpoint Smoke Test
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"healthcheck","model":"anthropic-fast","mode":"fast"}' \
  --max-time 30
# Expected: SSE stream starting with event: tool_start or response data
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container exits immediately on startup | Missing or malformed `.env` file; `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` not set | 1. Check `docker compose logs backend`. 2. Verify `.env` exists in project root. 3. Confirm all required env vars are present (see §5). 4. `docker compose up --build backend`. |
| `GET /health` returns connection refused | Backend container not running or crashed | 1. `docker compose ps`. 2. `docker compose logs backend --tail=50`. 3. Restart: `docker compose restart backend`. |
| `GET /health` returns 200 but `/chat` hangs indefinitely | Anthropic/Gemini API unreachable or quota exhausted | 1. Verify API keys are valid and not rate-limited. 2. Check Anthropic status page: https://status.anthropic.com. 3. Check `ANTHROPIC_API_KEY` environment variable in container: `docker compose exec backend env | grep ANTHROPIC`. 4. Try switching `model` field to `anthropic-fast` in the request. |
| `ValueError: Unsupported or unconfigured model provider: <name>` in logs | Invalid `model` name passed in chat request, or `LLMS` mapper not updated | 1. Confirm valid model names: `anthropic`, `anthropic-fast`, `gemini`. 2. Check `backend/modules/LLMS.py` model_mapper. 3. Ensure `GOOGLE_API_KEY` is set if using Gemini. |
| Redis connection error in backend logs (`ConnectionError`, `redis.exceptions`) | Redis container not running; wrong `REDIS_HOST` | 1. `docker compose ps redis`. 2. `docker compose restart redis`. 3. Verify `REDIS_HOST=redis` is set in backend environment. 4. `docker compose exec backend env | grep REDIS`. |
| LangGraph checkpoint errors / session state lost between requests | Redis flushed or restarted; memory not persisted across serverless restarts (known TODO in `graph.py`) | 1. Restart the session from the UI (new `session_id`). 2. For persistence across restarts, migrate Redis to an external service (Azure Cache for Redis) — see TODO in `graph.py`. |
| PostgreSQL connection refused from frontend | Postgres container not running or init script failed | 1. `docker compose logs postgres --tail=30`. 2. Check `postgres/init.sql` exists. 3. `docker compose restart postgres`. 4. Verify `DATABASE_URL` env var on frontend container. |
| Frontend fails to load / 502 Bad Gateway | Backend health check failing so frontend dependency not satisfied | 1. Fix backend first (see above rows). 2. `docker compose logs frontend`. 3. `docker compose restart frontend` after backend is healthy. |
| SQLite database files not found (`FileNotFoundError`) | Volume mount paths incorrect or database files missing from `./database/` directory | 1. `ls -lh ./database/`. 2. Verify four `.db` files exist. 3. Check volume mounts in `docker-compose.yml` match actual file paths. 4. `docker compose down && docker compose up`. |
| Underwriting assessment returns empty or truncated report | Aggregator LLM hitting `aggregator_max_tokens` (8000) limit; or specialist timeout | 1. Check backend logs for `[AGGREGATOR]` token counts. 2. Increase `aggregator_max_tokens` in `config.yml` if output tokens near limit. 3. Check for `asyncio.Semaphore` bottleneck (limit is 4 concurrent specialist calls). |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY` not found | Repository secret not configured | 1. Go to repo Settings → Secrets and variables → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| GitHub Actions: Claude returns invalid JSON | Claude response wrapped in markdown fences or contains newlines in strings | 1. Check Actions run logs for `[DEBUG] First 500 chars`. 2. The `extract_json` function in `tool1_code_review.py` should handle this; if failing, increase `max_tokens`. 3. Retry the workflow run manually. |
| Assessment produces `[TODO: <question>]` gaps in report | Sparse customer profile — missing fields that specialist LLMs require | 1. Verify customer exists in `customer_profile.db`. 2. Run `get_customer_profile` tool directly and inspect returned fields. 3. Check `assessment_criterias.json` for required fields per category. |
| `model_card.json` not found on backend startup | File missing from `backend/` directory | 1. Verify `backend/model_card.json` exists. 2. Rebuild container: `docker compose build backend`. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker and Docker Compose v2 installed
- `.env` file present in project root (see §5 for required variables)
- Four SQLite databases present under `./database/`
- `postgres/init.sql` present

### 4.1 First-Time Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create .env file from template
cp .env.example .env          # [TODO: confirm .env.example exists or document required vars]
vi .env                       # populate all required secrets

# 3. Verify database files are present
ls ./database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db

# 4. Build and start all services
docker compose up --build -d

# 5. Wait for backend to become healthy (up to 15s start_period + 5 retries × 10s)
docker compose ps   # wait until backend shows (healthy)

# 6. Run smoke test
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

### 4.2 Routine Update Deployment (code changes)

```bash
# 1. Pull latest changes
git pull origin main

# 2. Rebuild only changed services (backend or frontend)
docker compose build backend frontend

# 3. Rolling restart — bring up new containers before removing old
docker compose up -d --no-deps backend frontend

# 4. Verify health
docker compose ps
curl -f http://localhost:8000/health

# 5. Tail logs for 60 seconds to check for errors
docker compose logs -f backend --tail=50
```

### 4.3 Config-Only Change (e.g. `config.yml`, `assessment_criterias.json`)

```bash
# These files are baked into the image; rebuild backend
docker compose build backend
docker compose up -d --no-deps backend
curl -f http://localhost:8000/health
```

### 4.4 Rollback Procedure

```bash
# Option A — Roll back to previous Git commit and rebuild
git log --oneline -10          # identify the last known-good commit SHA
git checkout <previous-sha>
docker compose build backend frontend
docker compose up -d --no-deps backend frontend
curl -f http://localhost:8000/health

# Option B — Roll back using a previously tagged image [TODO: confirm image registry and tagging strategy]
docker compose pull             # if images are pushed to a registry
docker compose up -d

# Verify rollback
curl -f http://localhost:8000/health
docker compose logs backend --tail=30
```

> **Note:** Redis checkpoint data will persist across rollbacks (sessions remain valid). If a rollback requires clearing session state, flush Redis — see §7 Useful Commands.

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Source | Warning Threshold | Critical Threshold |
|---|---|---|---|
| Backend container health | Docker healthcheck | 2 consecutive failures | 5 consecutive failures |
| `/health` endpoint response time | External probe | > 2s | > 5s or non-200 |
| `/chat` SSE stream first-byte latency | Application logs | > 10s | > 30s |
| Anthropic API token usage — specialist | Backend stdout: `[SPECIALIST]` lines | `out > 1200 tok` (approaching 1500 cap) | `out == 1500 tok` (truncated) |
| Anthropic API token usage — aggregator | Backend stdout: `[AGGREGATOR]` lines | `out > 6000 tok` | `out > 7500 tok` (approaching 8000 cap) |
| Redis memory usage | `redis-cli INFO memory` | [TODO: set threshold based on session volume] | [TODO] |
| PostgreSQL connections | `pg_stat_activity` | [TODO] | [TODO] |
| LLM API error rate | Backend logs for `ValueError`, HTTP 429/500 from Anthropic | Any 429 | Sustained 429 or 5xx |
| Tool execution time | Backend stdout: `[TOOL END] <name> time=Xs` | > 30s per tool | > 60s per tool |
| Assessment total time | `[ASSESSMENT]` log lines | > 45s | > 90s |

### 5.2 Log Streams to Watch

```bash
# Real-time backend logs (primary source of truth)
docker compose logs -f backend

# Key log patterns to alert on:
# [TOOL START] / [TOOL END]       — tool invocation lifecycle
# [SPECIALIST] category=...       — per-category LLM call with token counts
# [AGGREGATOR]                    — final report assembly
# [CHAT] session=...              — incoming request trace
# ValueError / Exception          — application errors
# ConnectionError                 — Redis or external API connectivity issues
```

### 5.3 Structured Log Patterns (grep-ready)

```bash
# Filter errors only
docker compose logs backend 2>&1 | grep -E "(ERROR|Exception|ValueError|Traceback)"

# Monitor token consumption
docker compose logs backend 2>&1 | grep -E "\[(SPECIALIST|AGGREGATOR)\]"

# Monitor tool latency
docker compose logs backend 2>&1 | grep "TOOL END"
```

### 5.4 GitHub Actions Workflow Health

- Monitor the **Actions** tab in GitHub for failed runs of all five workflows.
- Workflow failures should trigger email notifications to `kylo.deng@capco.com` via SendGrid (when `send_email` is implemented in `shared.py` — [TODO: confirm `send_email` function is fully implemented; it appears truncated in the provided code]).
- Key workflows to watch:
  - `Tool 1 — Code Review`: runs on every PR open/sync
  - `Tool 2 — Tech Documentation`: runs on every merge to `main`
  - `Tool 4 — Auto Testing`: runs on every PR touching source files

### 5.5 Alerting Setup

[TODO: Is there a PagerDuty, Opsgenie, or Azure Monitor integration? No alerting configuration was found in the repository.]

[TODO: Are Docker healthcheck failures surfaced to any external monitoring system?]

[TODO: Is there a log aggregation platform (e.g. Datadog, Azure Log Analytics, CloudWatch)?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call rotation details] | Service down > 5 minutes; `/health` not recovering |
| L2 | Backend Lead | [TODO: fill in name and contact] | Redis/Postgres data issues; LLM API quota exhaustion; assessment logic errors |
| L3 | ML / AI Engineer | [TODO: fill in name and contact] | Model card or `UnderwritingReport` schema changes; specialist LLM prompt failures; token cap breaches |
| L4 | Platform / DevOps Lead | [TODO: fill in name and contact] | Infrastructure failures; Docker host issues; database corruption |
| Vendor | Anthropic Support | https://support.anthropic.com | Sustained API outages or rate limit increases needed |
| Vendor | Google Cloud Support | [TODO: support tier and contact] | Gemini API outages |
| Owner | Kylo Deng (Capco) | kylo.deng@capco.com | Escalation beyond L2; stakeholder communication |

---

## 7. Useful Commands

### Service Lifecycle
```bash
# Start all services
docker compose up -d

# Stop all services (preserve volumes)
docker compose down

# Stop and remove volumes (DESTRUCTIVE — clears Postgres data)
docker compose down -v

# Rebuild a specific service
docker compose build backend
docker compose build frontend

# Restart a single service
docker compose restart backend
docker compose restart redis

# View running containers and health status
docker compose ps
```

### Log Inspection
```bash
# Tail all logs
docker compose logs -f

# Tail backend only (most useful)
docker compose logs -f backend --tail=100

# Tail frontend
docker compose logs -f frontend --tail=50

# One-shot log dump for a post-mortem
docker compose logs backend > backend_$(date +%Y%m%d_%H%M%S).log
```