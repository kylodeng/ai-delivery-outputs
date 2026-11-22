# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-container AI-assisted life insurance underwriting platform. Underwriters interact with a chat-based frontend that routes requests to a FastAPI backend (`/chat`, `/health`). The backend orchestrates a LangGraph agent that calls three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — against SQLite customer databases and in-memory similarity indices. Risk assessment is performed in parallel by specialist LLM agents (Anthropic Claude or Google Gemini, configured via `config.yml`) whose outputs are aggregated into a structured `UnderwritingReport`. Conversation state is persisted in Redis (via LangGraph checkpointing), and the frontend uses a PostgreSQL database (Chainlit session storage). Five GitHub Actions workflows provide automated code review, documentation generation, test generation, business documentation, and UAT facilitation — all powered by the Anthropic Claude API.

---

## 2. Health Checks

### Backend API
```bash
# Should return {"status": "ok"}
curl -f http://localhost:8000/health
```

### Docker Compose Service Status
```bash
docker compose ps
# All services should show "running" and backend should show "(healthy)"
```

### Redis Connectivity
```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### PostgreSQL Connectivity
```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### Frontend Reachability
```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### LLM API Reachability
```bash
# Anthropic
curl -s https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.models[0].id'

# Google (Gemini)
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY" \
  | jq '.models[0].name'
```

### Database Files Present
```bash
ls -lh database/*.db
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container exits immediately on startup | Missing `.env` file or missing required env vars (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) | 1. Confirm `.env` exists in repo root. 2. Check `docker compose logs backend`. 3. Verify all required vars are set: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `REDIS_HOST`. |
| `GET /health` returns non-200 or connection refused | Backend crashed or not yet started | 1. `docker compose logs backend`. 2. Check for Python import errors. 3. Confirm port 8000 is not already in use (`lsof -i :8000`). 4. Restart: `docker compose restart backend`. |
| Chat returns 500 or empty SSE stream | LLM API key invalid/rate-limited, or Redis unreachable | 1. Check `docker compose logs backend` for traceback. 2. Test Redis: `docker compose exec redis redis-cli ping`. 3. Verify `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` are valid and have quota. 4. Retry with `model=anthropic-fast` as fallback. |
| `ValueError: Unsupported or unconfigured model provider` | Invalid `model` field sent in request, or missing LLM config in `config.yml` | 1. Confirm request `model` field is one of: `gemini`, `anthropic`, `anthropic-fast`. 2. Check `backend/modules/LLMS.py` model mapper. 3. Ensure corresponding API key env var is set. |
| Redis connection refused / LangGraph checkpoint error | Redis container not running; `REDIS_HOST` env var wrong | 1. `docker compose up -d redis`. 2. Confirm `REDIS_HOST=redis` in `.env` (not `localhost` when inside Docker). 3. Check `graph.py` TODO: Redis must be an external persistent service for multi-instance deployments. |
| Assessment returns partial or empty `UnderwritingReport` | Aggregator LLM hit token limit or structured output failed | 1. Check `docker compose logs backend` for `[AGGREGATOR]` lines. 2. Increase `aggregator_max_tokens` in `backend/config.yml` (currently 8000). 3. Verify model supports structured output. |
| Frontend cannot connect to backend | `BACKEND_URL` env var misconfigured, or backend unhealthy | 1. Confirm `BACKEND_URL=http://backend:8000` in `docker-compose.yml`. 2. Check backend healthcheck: `docker compose ps backend`. 3. Confirm frontend `depends_on: backend: condition: service_healthy`. |
| PostgreSQL init fails / frontend DB errors | `postgres/init.sql` missing or schema mismatch | 1. `docker compose logs postgres`. 2. Confirm `./postgres/init.sql` exists. 3. If schema changed: `docker compose down -v && docker compose up -d`. |
| `.db` file not found / customer profile lookup fails | SQLite database files not mounted or missing from `database/` dir | 1. `ls -lh database/*.db`. 2. Confirm volume mounts in `docker-compose.yml` point to correct paths. 3. [TODO: Where do the initial SQLite databases come from — are they committed to the repo, generated by a seed script, or downloaded from a data store?] |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY not set` | Secret not configured in repository Settings | 1. Go to repo → Settings → Secrets and variables → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. 3. Re-run failed workflow. |
| `customer_similarity_dict.json` stale or missing | Pre-computed similarity index not regenerated after data update | 1. [TODO: Is there a script to regenerate `backend/tmp/customer_similarity_dict.json`? This file appears to be pre-computed offline.] 2. Restart backend after updating the file. |
| Specialist LLM output truncated | `specialist_max_tokens: 1500` cap too low for complex profiles | 1. Increase `specialist_max_tokens` in `backend/config.yml`. 2. Redeploy backend: `docker compose up -d --build backend`. |
| SSE stream silently stops mid-response | Network timeout, `asyncio` exception swallowed in generator | 1. Check `docker compose logs backend` for unhandled exceptions. 2. Check client-side SSE reconnect logic. 3. [TODO: Is there a configured request timeout on the frontend or reverse proxy?] |

---

## 4. Deployment Procedure

### Prerequisites
- Docker and Docker Compose installed
- `.env` file present in repo root with all required variables
- `database/*.db` files present
- `postgres/init.sql` present

### Environment Variables Required

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic Claude API key |
| `GOOGLE_API_KEY` | Yes | Google Gemini API key |
| `REDIS_HOST` | Yes | Redis hostname (use `redis` inside Docker) |
| [TODO: Are there any other required env vars not visible in the code, e.g. a database connection string for the SQLite files?] | | |

---

### Standard Deployment (Docker Compose)

```bash
# Step 1: Clone repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Step 2: Create .env file
cp .env.example .env     # [TODO: does .env.example exist? Create from template if not]
# Edit .env and populate: ANTHROPIC_API_KEY, GOOGLE_API_KEY

# Step 3: Confirm database files are present
ls database/*.db

# Step 4: Pull / build images
docker compose pull
docker compose build

# Step 5: Start infrastructure services first
docker compose up -d redis postgres

# Step 6: Wait for postgres to be ready (~5s), then start backend
sleep 5
docker compose up -d backend

# Step 7: Wait for backend healthcheck to pass
docker compose ps backend   # wait for "(healthy)"

# Step 8: Start frontend
docker compose up -d frontend

# Step 9: Verify all services
docker compose ps
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

### Updating an Existing Deployment

```bash
# Step 1: Pull latest code
git pull origin main

# Step 2: Rebuild changed images
docker compose build backend frontend

# Step 3: Rolling restart (backend first, then frontend)
docker compose up -d --no-deps backend
# Wait for healthy
docker compose ps backend
docker compose up -d --no-deps frontend
```

### Configuration Change Only (no code change)

```bash
# Edit backend/config.yml, then:
docker compose restart backend
```

---

### Rollback Procedure

```bash
# Step 1: Identify the previous image tag or git commit
git log --oneline -5

# Step 2: Check out the previous commit
git checkout <previous-commit-sha>

# Step 3: Rebuild and restart
docker compose build backend frontend
docker compose up -d --no-deps backend
docker compose ps backend   # wait for healthy
docker compose up -d --no-deps frontend

# Step 4: Verify health
curl -f http://localhost:8000/health
```

> **Note:** Redis conversation state will persist across rollbacks. If a rollback is due to a breaking state schema change, flush Redis: see Useful Commands below.

> **Note:** PostgreSQL data persists in the `postgres_data` Docker volume. A rollback does **not** automatically roll back the database schema. [TODO: Are there database migrations? If so, what is the down-migration procedure?]

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | What to Watch | Warning Threshold |
|---|---|---|
| Backend `/health` response | Must return 200 `{"status":"ok"}` | Any non-200 |
| Container restart count | `docker stats` or orchestrator metrics | > 1 restart in 10 min |
| LLM token usage (stdout) | `[SPECIALIST]` and `[AGGREGATOR]` log lines show `in=` / `out=` token counts | Aggregator output tokens approaching 8000 |
| Assessment latency | `[TOOL END] run_underwriting_assessment time=Xs` in backend logs | > 30s per request |
| Redis memory usage | `redis-cli info memory` → `used_memory_human` | [TODO: Set threshold based on expected session volume] |
| PostgreSQL connection count | `SELECT count(*) FROM pg_stat_activity;` | [TODO: Set threshold] |

### Key Log Patterns

```bash
# Follow all service logs
docker compose logs -f

# Backend only
docker compose logs -f backend

# Watch for assessment performance
docker compose logs -f backend | grep -E '\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL'

# Watch for errors
docker compose logs -f backend | grep -iE 'error|exception|traceback|failed'

# Watch for chat requests
docker compose logs -f backend | grep '\[CHAT\]'
```

### Critical Log Lines to Alert On

| Log Pattern | Meaning | Action |
|---|---|---|
| `ValueError: Unsupported or unconfigured model provider` | Bad model name in request | Check client config |
| `Could not parse Claude response as JSON` | LLM returned malformed JSON | Check prompt/model version |
| `ConnectionRefusedError` (Redis) | Redis down | Restart Redis |
| `Connection refused` on port 5432 | Postgres down | Restart Postgres |
| Any Python `Traceback` in backend logs | Unhandled exception | Investigate immediately |

### GitHub Actions Monitoring

- Monitor the **Actions** tab at `https://github.com/kylodeng/underwriting_chatbot-main/actions`
- Workflows that should be green: `Tool 1 — Code Review` (on PR), `Tool 2 — Tech Documentation` (on merge to main)
- Failed workflows send email to `kylo.deng@capco.com` via SendGrid

### Alerting

[TODO: Is there a Datadog, Prometheus, Grafana, or cloud-native monitoring stack configured? No monitoring/alerting infrastructure is visible in the codebase.]

[TODO: Is there a PagerDuty, Opsgenie, or Slack webhook for alert routing?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in contact] | Service down, health check failing, containers restarting |
| L2 | Backend / ML Lead | [TODO: fill in contact] | LLM errors, assessment logic failures, Redis/DB corruption |
| L3 | Platform / DevOps Lead | [TODO: fill in contact] | Infrastructure outage, secrets rotation, Docker host issues |
| L4 | Solution Owner | [TODO: fill in contact — referenced as kylo.deng@capco.com in workflows] | Major data incident, regulatory concern, prolonged outage |
| External | Anthropic Support | https://support.anthropic.com | Claude API outage, quota exhaustion |
| External | Google Cloud Support | [TODO: fill in GCP support contact] | Gemini API outage |

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop all services and DELETE volumes (destructive — wipes Postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend
docker compose restart frontend
docker compose restart redis

# Rebuild and restart backend only
docker compose up -d --build --no-deps backend

# View running containers and health status
docker compose ps
```

### Logs

```bash
# Tail all logs
docker compose logs -f

# Backend logs only
docker compose logs -f backend

# Last 100 lines from backend
docker compose logs --tail=100 backend

# Filter for assessment timings
docker compose logs backend | grep -E '\[SPECIALIST\]|\[AGGREGATOR\]'

# Filter for errors
docker compose logs backend | grep -iE 'error|exception|traceback'
```

### Health & Diagnostics

```bash
# Backend health check
curl -f http://localhost:8000/health && echo "OK" || echo "FAIL"

# Test chat endpoint (SSE)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'

# Redis ping
docker compose exec redis redis-cli ping

# Redis info (memory, connections)
docker compose exec redis redis-cli info server | grep -E 'redis_version|uptime'
docker compose exec redis redis-cli info memory | grep used_memory_human

# Flush all Redis keys (WARNING: destroys all conversation state)
docker compose exec redis redis-cli FLUSHALL

# List Redis keys (inspect conversation state)
docker compose exec redis redis-cli KEYS '*'

# PostgreSQL connectivity check
docker compose exec postgres pg_isready -U chainlit -d chainlit

# PostgreSQL — list tables
docker compose exec postgres psql -U chainlit -d chainlit -c '\dt'

# PostgreSQL — active connections
docker compose exec postgres psql -U chainlit -d chainlit \
  -c "SELECT count(*) FROM pg_stat_activity;"
```

### Database Files

```bash
# Check SQLite database files are present and non-empty
ls -lh database/*.db

# Quick sanity check on customer profile DB
sqlite3 database/customer_profile.db ".tables"
sqlite3 database/customer_profile.db "SELECT count(*) FROM sqlite_master WHERE type='table';"

# [TODO: What is the main table name in customer_profile.db?]
```

### GitHub Actions (manual triggers)

```bash
# Trigger code review workflow manually (requires gh CLI)
gh workflow run tool1