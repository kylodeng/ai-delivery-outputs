# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is a conversational AI assistant designed for life insurance underwriters. It exposes a streaming FastAPI backend (`/chat`, `/health`) that orchestrates a LangGraph agent backed by Anthropic Claude (Sonnet/Haiku) and optionally Google Gemini. When a user submits a query, the agent calls one or more tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — to retrieve customer data from SQLite databases and run a parallel multi-specialist LLM risk assessment pipeline. Results are streamed to a frontend (served on port 8080) via Server-Sent Events. Conversation memory is persisted in Redis (via LangGraph's `AsyncRedisSaver`) and a PostgreSQL database backs the Chainlit-based frontend session store. The system is containerised via Docker Compose, with four services: `redis`, `postgres`, `backend`, and `frontend`.

---

## 2. Health Checks

| Check | Command / URL | Expected Result |
|---|---|---|
| Backend HTTP health | `curl -f http://localhost:8000/health` | `{"status": "ok"}` — HTTP 200 |
| Docker Compose all services up | `docker compose ps` | All four services show `running` / `healthy` |
| Redis reachable | `docker compose exec redis redis-cli ping` | `PONG` |
| PostgreSQL reachable | `docker compose exec postgres pg_isready -U chainlit` | `accepting connections` |
| Backend container health (Docker) | `docker inspect underwriting_chatbot-backend-1 --format '{{.State.Health.Status}}'` | `healthy` |
| Frontend serving | `curl -f http://localhost:8080` | HTTP 200 with page content |
| LLM connectivity | Check backend logs for `[SPECIALIST]` / `[AGGREGATOR]` lines after a test chat message | Token counts and timing printed without errors |
| SQLite databases mounted | `docker compose exec backend ls /data/*.db` | Lists `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns 502 or connection refused | Backend container crashed or not yet started | 1. `docker compose ps` to confirm status. 2. `docker compose logs backend --tail=50` for error. 3. `docker compose restart backend`. |
| Backend container stuck in `starting` / health check failing | Redis not ready when backend starts; app fails to connect | 1. `docker compose logs redis`. 2. Ensure Redis is healthy first: `docker compose up redis -d && docker compose up backend -d`. 3. Check `REDIS_HOST` env var is set to `redis`. |
| `ValueError: Unsupported or unconfigured model provider` in logs | Missing or misspelled model name in request, or `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` not set | 1. Confirm `.env` file contains required keys. 2. `docker compose exec backend env | grep API_KEY`. 3. Check request payload `model` field matches `anthropic`, `anthropic-fast`, or `gemini`. |
| `AuthenticationError` / 401 from Anthropic or Google | Expired or invalid API key | 1. Rotate key in provider console. 2. Update `.env`. 3. `docker compose up -d --force-recreate backend`. |
| `RateLimitError` / 429 from LLM provider | Too many concurrent assessments exhausting API quota | 1. Check `asyncio.Semaphore(4)` in `assessment.py` — reduce if needed. 2. Contact LLM provider to increase tier. 3. Switch `llm.default` in `config.yml` to `anthropic-fast` temporarily. |
| Chat responses hang / never complete | SSE stream stalled; LLM timed out; Redis checkpoint write blocked | 1. Check backend logs for `[TOOL START]` without matching `[TOOL END]`. 2. `docker compose restart backend`. 3. Check Redis memory: `docker compose exec redis redis-cli info memory`. |
| `OperationalError` — SQLite database not found | Database files not mounted or path wrong | 1. Confirm `./database/*.db` files exist on host. 2. `docker compose exec backend ls /data/`. 3. Fix volume mounts in `docker-compose.yml` and `docker compose up -d`. |
| PostgreSQL connection refused from frontend | Postgres not started, wrong credentials, or `DATABASE_URL` misconfigured | 1. `docker compose logs postgres`. 2. Verify `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` match `DATABASE_URL`. 3. `docker compose restart postgres frontend`. |
| `customer_similarity_dict.json` lookup returns empty / KeyError | Customer ID not present in `backend/tmp/customer_similarity_dict.json` | 1. Verify the customer ID format matches `CUST00000001` pattern. 2. [TODO: How is `customer_similarity_dict.json` generated and refreshed?] 3. Regenerate the file from source data. |
| GitHub Actions workflow fails — `ModuleNotFoundError` | `pip install anthropic requests` missing packages needed by scripts | 1. Check workflow step logs. 2. Add missing package to the `pip install` step. 3. Re-run the workflow. |
| Frontend shows blank or error page | Backend not yet healthy when frontend started | 1. `docker compose logs frontend`. 2. `docker compose restart frontend`. 3. Confirm backend health check passes first. |
| Memory not persisting across sessions | Redis restarted without persistence; `thread_id` mismatch | 1. [TODO: Is Redis persistence (AOF/RDB) configured?] Currently uses in-memory Redis — memory is lost on restart. 2. Enable Redis persistence or migrate to Azure Cache for Redis (noted as TODO in `graph.py`). |

---

## 4. Deployment Procedure

### Prerequisites

- Docker Engine ≥ 24 and Docker Compose V2 installed on host
- `.env` file present in repo root with all required secrets (see §5)
- `./database/*.db` SQLite files present
- `./postgres/init.sql` present

### Step-by-Step Deployment

```
# 1. Clone / pull latest code
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
git pull origin main

# 2. Create / verify .env file
cp .env.example .env          # if template exists
# [TODO: Is there a .env.example file?]
# Minimum required keys — see §5

# 3. Build images (no cache for clean deploy)
docker compose build --no-cache

# 4. Start infrastructure services first
docker compose up -d redis postgres

# 5. Wait for postgres and redis to be healthy
docker compose ps   # confirm both show 'running'

# 6. Start backend (health check will gate frontend)
docker compose up -d backend

# 7. Wait for backend to be healthy (up to 15s start + 5 retries × 10s = ~65s max)
docker compose ps backend   # wait for 'healthy'

# 8. Start frontend
docker compose up -d frontend

# 9. Verify full stack
curl -f http://localhost:8000/health
curl -f http://localhost:8080
docker compose ps
```

### Rollback Steps

```
# Option A — Roll back to previous Docker image (if images are tagged)
docker compose down
# [TODO: Are Docker images pushed to a registry with versioned tags?]
# Edit docker-compose.yml to pin image: tags to previous version
docker compose up -d

# Option B — Roll back via git
git log --oneline -5          # find last good commit SHA
git checkout <previous-sha>
docker compose build --no-cache
docker compose up -d

# Option C — Emergency: restore from last known-good compose state
docker compose down
git stash                     # revert local changes
git pull origin main          # or specific tag
docker compose up -d
```

> **Note:** Redis memory (conversation history) is lost on `docker compose down` unless persistence is configured. This is a known gap (see `graph.py` TODO).

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where | Warning Threshold |
|---|---|---|
| Backend container health status | `docker compose ps` / Docker daemon | Any status other than `healthy` |
| LLM specialist call latency | Backend stdout: `[SPECIALIST] ... time=` | > 30s per specialist call |
| LLM aggregator call latency | Backend stdout: `[AGGREGATOR] ... time=` | > 60s |
| LLM output token usage | Backend stdout: `out=` values | Near model max_tokens limits (`1500` specialist, `8000` aggregator) |
| Redis memory usage | `redis-cli info memory` → `used_memory_human` | [TODO: What is the Redis container memory limit?] |
| PostgreSQL connectivity | Frontend logs | Any `OperationalError` or connection refused |
| Anthropic / Google API error rate | Backend logs: `RateLimitError`, `AuthenticationError` | Any occurrence |
| `[TOOL START]` without `[TOOL END]` | Backend stdout | Any stuck tool call > 120s |

### Logs to Watch

```bash
# All services combined
docker compose logs -f

# Backend only (most diagnostic value)
docker compose logs -f backend

# Filter for errors only
docker compose logs backend 2>&1 | grep -iE "error|exception|traceback|failed"

# Filter for LLM timing
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[ASSESSMENT\]"

# Filter for tool execution
docker compose logs backend 2>&1 | grep -E "\[TOOL START\]|\[TOOL END\]|\[CHAT\]"
```

### Alerting

> [TODO: Is there a centralised logging platform (e.g. Datadog, Azure Monitor, Grafana Loki) configured for this service?]  
> [TODO: Are there any uptime monitors or PagerDuty/OpsGenie integrations?]  
> [TODO: Should the `/health` endpoint be scraped by a Prometheus exporter?]

Currently, no automated alerting infrastructure is visible in the repository. Recommend:
1. Configure an uptime monitor against `GET /health` (e.g. UptimeRobot, Azure Application Insights availability test)
2. Forward `docker compose logs` to a log aggregator
3. Alert on any 5xx from the backend

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call DevOps / Engineer | [TODO: fill in on-call contact] | Service down > 5 min, health check failing |
| L2 | Backend / ML Engineer | [TODO: fill in team contact] | LLM pipeline errors, assessment logic failures, data quality issues |
| L3 | Repository Owner | kylo.deng@capco.com (from workflow config) | Security incidents, API key compromise, data breach |
| External | Anthropic Support | [TODO: fill in Anthropic support URL/ticket portal] | Persistent 429 / 5xx from Anthropic API |
| External | Google Cloud Support | [TODO: fill in GCP support contact] | Persistent Gemini API failures |

---

## 7. Useful Commands

```bash
# ── Stack Management ─────────────────────────────────────────────────────────

# Start full stack
docker compose up -d

# Stop full stack (preserves volumes)
docker compose down

# Stop and destroy all volumes (DESTRUCTIVE — loses Redis + Postgres data)
docker compose down -v

# Rebuild and restart a single service
docker compose build backend && docker compose up -d --no-deps backend

# View status of all services
docker compose ps

# ── Logs ─────────────────────────────────────────────────────────────────────

# Stream all logs
docker compose logs -f

# Stream backend logs only
docker compose logs -f backend

# Last 100 lines of backend logs
docker compose logs --tail=100 backend

# ── Health & Connectivity ─────────────────────────────────────────────────────

# Backend health check
curl -f http://localhost:8000/health

# Redis ping
docker compose exec redis redis-cli ping

# Redis memory info
docker compose exec redis redis-cli info memory

# Redis list all keys (conversation checkpoints)
docker compose exec redis redis-cli keys '*'

# PostgreSQL connection check
docker compose exec postgres pg_isready -U chainlit

# PostgreSQL list tables
docker compose exec postgres psql -U chainlit -d chainlit -c '\dt'

# ── SQLite Databases ──────────────────────────────────────────────────────────

# List mounted DB files
docker compose exec backend ls -lh /data/

# Query customer profile (example)
sqlite3 ./database/customer_profile.db "SELECT * FROM customer LIMIT 5;"
# [TODO: What is the actual table name in customer_profile.db?]

# ── Backend Runtime ───────────────────────────────────────────────────────────

# Open a shell in the backend container
docker compose exec backend bash

# Check environment variables in backend container
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS|API"

# Run backend locally (outside Docker) for debugging
cd backend
pip install -r requirements.txt   # [TODO: Is there a requirements.txt?]
uvicorn main:app --reload --port 8000

# ── Test Chat via curl (SSE) ──────────────────────────────────────────────────

curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get profile for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-session-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# ── GitHub Actions (CI/CD) ────────────────────────────────────────────────────

# Manually trigger tech docs generation (requires gh CLI)
gh workflow run tool2_tech_docs.yml

# Manually trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# View recent workflow run results
gh run list --limit 10

# View logs of most recent run
gh run view --log
```