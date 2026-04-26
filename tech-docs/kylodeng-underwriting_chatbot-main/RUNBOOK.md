# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system consists of a **FastAPI backend** (port 8000) that orchestrates a LangGraph-powered AI agent using Anthropic Claude (Haiku for fast responses, Sonnet for deep analysis) and Google Gemini models; a **frontend UI** (port 8080); a **Redis** instance (port 6379) used as a LangGraph checkpoint store for conversation memory; and a **PostgreSQL** database (port 5432) used by the frontend (Chainlit). The agent calls three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — against SQLite databases containing customer profiles, model predictions, and feature importance data. Assessment results are structured using a `CatBoostClassifier`-derived model card (v1.0, deployed 2024-06-01) and returned as a validated `UnderwritingReport` Pydantic model. Five GitHub Actions CI/CD workflows provide automated code review, documentation generation, business documentation, test generation, and UAT facilitation, all powered by Claude via the Anthropic API.

---

## 2. Health Checks

### Backend API
```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Docker Compose service status
```bash
docker compose ps
# All services should show status: running (healthy)
```

### Redis connectivity
```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### PostgreSQL connectivity
```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### Frontend reachability
```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### Backend container health (via Docker)
```bash
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{.State.Health.Status}}'
# Expected: healthy
```

### Check backend logs for startup errors
```bash
docker compose logs backend --tail=50
# Should NOT contain: ERROR, Traceback, Connection refused
```

### Verify SQLite databases are mounted
```bash
docker compose exec backend ls -lh /data/
# Expected: customer_profile.db, feature_importance.db,
#           model_predictions.db, application_profile.db
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or times out | Backend container crashed or failed to start | 1. `docker compose logs backend --tail=100` to inspect error. 2. Check env vars in `.env` are present. 3. `docker compose restart backend`. |
| Backend health check shows `unhealthy` | App started but `/health` endpoint not responding within 5s × 5 retries | 1. Check if port 8000 is already in use: `lsof -i :8000`. 2. Inspect logs: `docker compose logs backend`. 3. `docker compose down && docker compose up -d`. |
| Frontend fails to load / returns 502 | Backend not yet healthy when frontend started | 1. Confirm backend is healthy: `docker compose ps`. 2. `docker compose restart frontend`. The `depends_on: condition: service_healthy` should prevent this but can race on cold starts. |
| `Redis connection refused` in backend logs | Redis container not running or REDIS_HOST env var incorrect | 1. `docker compose ps redis` — check it is running. 2. Confirm `REDIS_HOST=redis` is set in docker-compose environment. 3. `docker compose restart redis backend`. |
| `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` errors in logs | Missing or invalid API key in `.env` | 1. Verify `.env` file exists in project root and contains valid keys. 2. `docker compose down && docker compose up -d` to reload env. |
| LangGraph agent returns no response / hangs | Redis checkpoint store unavailable or model API rate limit hit | 1. Check Redis: `docker compose exec redis redis-cli ping`. 2. Check Anthropic/Google API status pages. 3. Reduce concurrency or retry request. |
| `ValueError: Unsupported or unconfigured model provider` | Invalid `model` field passed in chat request, or Azure/OpenAI selected (not configured) | 1. Use only `anthropic`, `anthropic-fast`, or `gemini` as model values. 2. Check `backend/modules/LLMS.py` for supported providers. |
| SQLite database not found (`/data/*.db`) | Volume mount missing or `.db` files not present in `./database/` | 1. Confirm `./database/` directory contains all four `.db` files. 2. Check `docker-compose.yml` volume mounts. 3. Re-provision database files. [TODO: Where are the canonical database files sourced from?] |
| PostgreSQL init fails | `./postgres/init.sql` missing or invalid | 1. Check `./postgres/init.sql` exists. 2. `docker compose logs postgres`. 3. Remove stale volume: `docker compose down -v && docker compose up -d`. |
| GitHub Actions workflow fails on `ANTHROPIC_API_KEY` | Secret not set in repository settings | 1. Go to repo → Settings → Secrets and variables → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| Streaming chat response stops mid-token | SSE connection dropped or model `max_tokens` cap hit (`specialist_max_tokens: 1500`) | 1. Check browser network tab for SSE stream errors. 2. Adjust `specialist_max_tokens` in `backend/config.yml` if legitimate truncation. 3. Restart backend. |
| `run_underwriting_assessment` returns incomplete JSON | Aggregator LLM hit `aggregator_max_tokens: 8000` limit | 1. Increase `aggregator_max_tokens` in `backend/config.yml`. 2. Switch to Sonnet model for deeper assessments. |
| Redis memory not persisting across restarts | Redis is in-memory only; no AOF/RDB persistence configured | This is a known limitation (noted in `graph.py` TODO). [TODO: Migrate to Azure Cache for Redis or add Redis persistence config.] |
| `on_chat_model_stream` events not reaching frontend | `in_tool=True` flag suppressing stream during tool execution | Expected behaviour — streaming resumes after tool completes. If it never resumes, check `on_tool_end` event firing in backend logs. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker and Docker Compose installed
- `.env` file populated with required secrets (see Section 5)
- `./database/*.db` SQLite files present
- `./postgres/init.sql` present

### Step-by-Step Deployment

**Step 1 — Clone the repository**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create and populate the `.env` file**
```bash
cp .env.example .env   # [TODO: confirm .env.example exists or document required vars]
# Edit .env and fill in all required variables (see Section 5)
```

**Step 3 — Build Docker images**
```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
# Wait for postgres to be ready
sleep 10
docker compose logs postgres | grep "database system is ready"
```

**Step 5 — Start backend and verify health**
```bash
docker compose up -d backend
# Wait for health checks to pass (up to 15s start_period + 5×10s = 65s max)
docker compose ps backend
# Confirm: (healthy)
```

**Step 6 — Start frontend**
```bash
docker compose up -d frontend
```

**Step 7 — Verify all services are running**
```bash
docker compose ps
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

**Step 8 — Smoke test the chat endpoint**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'
```

---

### Rollback Steps

**Option A — Roll back to previous Docker image**
```bash
# Tag current image before deployment for easy rollback
docker tag underwriting_chatbot-main-backend:latest \
  underwriting_chatbot-main-backend:rollback-$(date +%Y%m%d%H%M)

# To rollback:
docker compose down
docker tag underwriting_chatbot-main-backend:rollback-<TIMESTAMP> \
  underwriting_chatbot-main-backend:latest
docker compose up -d
```

**Option B — Roll back via Git**
```bash
git log --oneline -10          # find last good commit
git checkout <COMMIT_SHA>
docker compose build --no-cache
docker compose up -d
```

**Option C — Emergency stop**
```bash
docker compose down
# Services will be unavailable until restarted
```

> [TODO: Is there a container registry (ECR, ACR, Docker Hub) where tagged images are pushed? Define the versioned image tag strategy.]

> [TODO: Is there a Kubernetes/cloud deployment target beyond local Docker Compose? No IaC (Terraform/Bicep) was found in the repo.]

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Threshold / Alert |
|---|---|---|
| Backend health endpoint | `GET /health` | Alert if non-200 for > 30s |
| Docker container status | `docker compose ps` | Alert if any container exits or is unhealthy |
| Redis memory usage | `redis-cli INFO memory` → `used_memory_human` | [TODO: Set threshold based on expected session volume] |
| PostgreSQL connections | `pg_stat_activity` | Alert if connections approach `max_connections` (default 100) |
| LLM API latency | Backend stdout `[SPECIALIST]` / `[AGGREGATOR]` log lines | Alert if `time=` exceeds 30s per specialist call |
| LLM token usage | Backend stdout `in=` / `out=` token counts | Monitor for unexpected spikes (cost control) |
| GitHub Actions workflow failures | GitHub Actions UI / email notifications | Alert on any failed run |

### Log Locations

| Service | How to access |
|---|---|
| Backend (FastAPI + agent) | `docker compose logs backend -f` |
| Redis | `docker compose logs redis -f` |
| PostgreSQL | `docker compose logs postgres -f` |
| Frontend | `docker compose logs frontend -f` |

### Key Log Patterns to Monitor

```
# Successful assessment
[ASSESSMENT] Starting — N specialist calls (mode='fast')
[SPECIALIST] category=...  in=X tok  out=Y tok  time=Z.XXs
[AGGREGATOR]               in=X tok  out=Y tok  time=Z.XXs

# Tool execution
[TOOL START] <tool_name>
[TOOL END]   <tool_name>  time=X.XXs

# New chat request
[CHAT] session=<id> model=<model> mode=<mode> msg='...'
```

### Alerting

> [TODO: No monitoring stack (Prometheus, Datadog, CloudWatch, etc.) is configured in this repo. Recommend adding structured JSON logging and a metrics exporter.]

> [TODO: No alerting rules or on-call rotation defined. Integrate with PagerDuty/OpsGenie or configure GitHub Actions failure notifications.]

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in contact] | Service down, health check failing, unable to restart containers |
| L2 | Backend/ML Engineer | [TODO: fill in contact] | LLM assessment errors, model output quality issues, Redis/DB failures |
| L3 | Tech Lead | [TODO: fill in contact] | Data breach, API key compromise, architecture-level failures |
| External | Anthropic Support | support.anthropic.com | Claude API outage, unexpected billing spikes, model deprecation |
| External | Google Cloud Support | [TODO: fill in contract details] | Gemini API outage |
| External | Redis Support | redis.io/support | Redis Stack issues |

> Repository owner visible in GitHub: `kylodeng`. Notification email seen in workflows: `kylo.deng@capco.com`.

---

## 7. Useful Commands

### Service Lifecycle

```bash
# Start all services
docker compose up -d

# Stop all services (preserve volumes)
docker compose down

# Stop and remove volumes (DESTRUCTIVE — wipes postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend

# Rebuild and redeploy backend only
docker compose build backend && docker compose up -d backend

# View real-time logs for all services
docker compose logs -f

# View real-time logs for backend only
docker compose logs backend -f --tail=100
```

### Health & Diagnostics

```bash
# Check all container statuses
docker compose ps

# Backend health check
curl -f http://localhost:8000/health && echo "OK"

# Redis ping
docker compose exec redis redis-cli ping

# Redis session key inspection
docker compose exec redis redis-cli KEYS "*"

# Postgres connectivity
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Postgres: list active connections
docker compose exec postgres psql -U chainlit -d chainlit \
  -c "SELECT pid, usename, application_name, state FROM pg_stat_activity;"

# Check SQLite database files are mounted
docker compose exec backend ls -lh /data/
```

### Test Chat Endpoint

```bash
# Fast mode (Haiku)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get profile for customer CUST00000001",
    "temperature": 0.3,
    "session_id": "ops-test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Deep mode (Sonnet)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run full risk assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "ops-test-002",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### Redis — Clear a Specific Session

```bash
# List all checkpoint keys for a session
docker compose exec redis redis-cli KEYS "*default*"

# Delete a session checkpoint (replace KEY with actual key)
docker compose exec redis redis-cli DEL <KEY>

# Flush ALL Redis data (DESTRUCTIVE — clears all conversation history)
docker compose exec redis redis-cli FLUSHALL
```

### Config & Secrets Verification

```bash
# Verify required env vars are loaded in backend container
docker compose exec backend env | grep -E \
  "ANTHROPIC_API_KEY|GOOGLE_API_KEY|REDIS_HOST"

# Check config.yml values
docker compose exec backend cat /app/config.yml
```

### GitHub Actions — Trigger Workflows Manually

```bash
# Requires GitHub CLI (gh) installed and authenticated

# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr -f pr_number=42

# Trigger tech documentation generation
gh workflow run tool2_tech_docs.yml

# Trigger test generation
gh workflow run tool4_auto_testing.yml -f test_mode=generate
```

### Database Inspection (SQLite)

```bash
# Inspect customer profile database
docker compose exec backend sqlite3 /data/customer_profile.db \
  ".tables"

# Query a specific customer
docker compose exec backend sqlite3 /data/customer_profile.db \
  "SELECT * FROM customer_profile WHERE customer_id='CUST00000001';"
```

> [TODO: What is the table schema for each SQLite database? No schema files were found in the repo.]

> [TODO: Are the