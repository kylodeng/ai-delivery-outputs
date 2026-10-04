# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-powered assistant designed to support insurance underwriters in assessing customer risk profiles. It exposes a FastAPI backend (port 8000) that accepts chat requests via Server-Sent Events (SSE) and orchestrates a LangGraph agent backed by Anthropic Claude (primary) or Google Gemini LLMs. The agent can retrieve customer profiles, run a parallel multi-specialist underwriting risk assessment across categories such as finance, health, and life, and identify lookalike customers from pre-computed similarity data. Conversation state is persisted in Redis (via LangGraph's `AsyncRedisSaver` checkpointer), customer data is served from read-only SQLite databases, and a Chainlit-compatible frontend (port 8080) sits in front of the backend. A suite of five GitHub Actions AI-delivery workflows (code review, tech docs, business docs, auto-testing, UAT) are also included and run against the repository itself using Claude via the Anthropic API.

---

## 2. Health Checks

### Primary Health Endpoint

```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Container-Level Health (Docker Compose)

```bash
docker compose ps
# All services should show: "healthy" or "running"
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

### Backend Application Logs (startup confirmation)

```bash
docker compose logs backend --tail=50
# Look for: Uvicorn running on http://0.0.0.0:8000
```

### Frontend Reachability

```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### LLM API Reachability

```bash
# Anthropic
curl -s -o /dev/null -w "%{http_code}" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  https://api.anthropic.com/v1/models
# Expected: 200

# Google Gemini (if enabled)
curl -s -o /dev/null -w "%{http_code}" \
  "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY"
# Expected: 200
```

### SQLite Database Files Present

```bash
docker compose exec backend ls -lh /data/
# Expected: customer_profile.db, feature_importance.db,
#           model_predictions.db, application_profile.db
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container crashed or failed to start | `docker compose logs backend --tail=100`; check for import errors or missing env vars; `docker compose restart backend` |
| Chat request hangs indefinitely / SSE never closes | Redis unavailable; LangGraph checkpointer cannot persist state | Verify `docker compose exec redis redis-cli ping` returns `PONG`; check `REDIS_HOST` env var equals `redis`; restart Redis: `docker compose restart redis` |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request does not match a key in `LLMS.model_mapper` | Check request payload `model` field; valid values: `anthropic`, `anthropic-fast`, `gemini`; verify `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` are set in `.env` |
| Anthropic API `401 Unauthorized` | `ANTHROPIC_API_KEY` missing or expired | Rotate key in Anthropic console; update `.env` and `docker compose up -d --no-deps backend` |
| `FileNotFoundError` for `config.yml` or `assessment_criterias.json` | Container build did not copy `prompts/` directory, or volume mount is wrong | Rebuild image: `docker compose build backend`; verify `Dockerfile` copies `prompts/` and `config.yml` into image |
| `FileNotFoundError` for SQLite `.db` files | Volume mounts in `docker-compose.yml` point to non-existent host paths | Confirm `./database/*.db` files exist on host; check `docker compose config` for correct volume paths |
| Assessment returns empty or garbled JSON | Aggregator LLM hit token limit (`aggregator_max_tokens: 8000`); response truncated | Increase `aggregator_max_tokens` in `config.yml`; check `[AGGREGATOR]` log lines for token counts |
| Frontend shows blank page / cannot reach backend | `BACKEND_URL` env var incorrect, or backend healthcheck failing so frontend never starts | Check `docker compose ps` — backend must be `healthy` before frontend starts; verify `BACKEND_URL=http://backend:8000` |
| PostgreSQL connection refused | Postgres container not started or init failed | `docker compose logs postgres --tail=50`; check `init.sql` syntax; `docker compose restart postgres` |
| GitHub Actions workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set in repo | Add `ANTHROPIC_API_KEY` in GitHub repo → Settings → Secrets and variables → Actions |
| Redis data lost after restart | Redis is using the in-memory-only default config; no persistence configured | [TODO: Is Redis persistence (AOF/RDB) required? Currently `redis-stack-server` is used without a persistence config — conversation history will be lost on Redis restart] |
| `customer_similarity_dict.json` not found | File expected at `backend/tmp/customer_similarity_dict.json` but missing | Ensure the file is committed to the repo and present in the Docker build context; rebuild backend image |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose v2 installed
- `.env` file present at repository root with all required variables (see §5)
- SQLite database files present under `./database/`
- `postgres/init.sql` present

### Step-by-Step Deployment

**Step 1 — Clone / pull latest code**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
git pull origin main
```

**Step 2 — Populate environment variables**

```bash
cp .env.example .env   # [TODO: Does a .env.example exist? If not, create one]
# Edit .env and set:
#   ANTHROPIC_API_KEY=...
#   GOOGLE_API_KEY=...      (if using Gemini)
#   REDIS_HOST=redis        (keep as-is for Docker Compose)
```

**Step 3 — Build images**

```bash
docker compose build
```

**Step 4 — Start infrastructure services first**

```bash
docker compose up -d redis postgres
# Wait ~5 seconds for Postgres to initialise
sleep 5
```

**Step 5 — Start backend and wait for healthy**

```bash
docker compose up -d backend
# Poll health until ready (up to 60 s)
until curl -sf http://localhost:8000/health; do echo "waiting..."; sleep 3; done
```

**Step 6 — Start frontend**

```bash
docker compose up -d frontend
```

**Step 7 — Verify all containers are healthy**

```bash
docker compose ps
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

**Step 8 — Smoke test the chat endpoint**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
```

---

### Rollback Steps

**Option A — Roll back to previous Docker image tag** *(if images are tagged and pushed to a registry)*

```bash
# [TODO: Is there a container registry? If yes, what is the registry URL and tagging convention?]
docker compose down
# Edit docker-compose.yml image tags to previous version
docker compose up -d
```

**Option B — Roll back via Git**

```bash
git log --oneline -10          # identify last known-good commit
git checkout <commit-sha>
docker compose build
docker compose up -d
```

**Option C — Hotfix only the backend without downtime**

```bash
docker compose build backend
docker compose up -d --no-deps backend
# Frontend continues serving; backend restarts in ~15 s
```

**Verify rollback**

```bash
curl -f http://localhost:8000/health
docker compose logs backend --tail=30
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Warning Threshold | Notes |
|---|---|---|---|
| Backend response time per chat request | Application logs `[CHAT]` lines | > 30 s end-to-end | Includes specialist LLM calls |
| Specialist LLM call duration | `[SPECIALIST]` log lines | > 15 s per category | `specialist_max_tokens: 1500` |
| Aggregator LLM call duration | `[AGGREGATOR]` log lines | > 20 s | `aggregator_max_tokens: 8000` |
| Tool call duration | `[TOOL START]` / `[TOOL END]` log lines | > 10 s | |
| Redis memory usage | `redis-cli INFO memory` | > 80% `maxmemory` | [TODO: Is `maxmemory` configured?] |
| Docker container restart count | `docker compose ps` or container runtime | Any restart | Indicates crash loop |
| Anthropic API error rate | Application logs / Anthropic console | Any 4xx/5xx | Check API quota |
| Input/output token counts | `[SPECIALIST]` and `[AGGREGATOR]` log lines | Aggregator output tokens approaching 8000 | Risk of truncated reports |

### Log Locations

```bash
# All services
docker compose logs -f

# Backend only (most relevant)
docker compose logs -f backend

# Key log prefixes to grep:
#   [CHAT]        — incoming request
#   [TOOL START]  — agent calling a tool
#   [TOOL END]    — tool completed (includes elapsed time)
#   [SPECIALIST]  — per-category LLM call (tokens + time)
#   [AGGREGATOR]  — final aggregation LLM call
#   [ASSESSMENT]  — overall assessment timing
```

### Alerting

> [TODO: Is there a monitoring stack (Prometheus, Datadog, Azure Monitor, etc.)? No monitoring agent or exporter is configured in the current codebase.]

Recommended alerts to configure:

- **Backend container unhealthy** for > 1 minute → page on-call
- **`/health` endpoint non-200** for > 2 consecutive checks → alert
- **Anthropic API 429 (rate limit)** in logs → alert + check quota
- **Redis container down** → alert (conversation state will be lost)
- **Any `[AGGREGATOR]` output tokens ≥ 7500** → warning (approaching truncation limit)

### GitHub Actions Workflow Health

Monitor in GitHub → Actions tab:

- `Tool 1 — Code Review`: runs on every PR; failures indicate Claude API or GitHub token issues
- `Tool 2 — Tech Documentation`: runs on push to `main` and weekly Sunday 06:00 UTC
- `Tool 3 — Business Documentation`: runs on version tags
- `Tool 4 — Auto Testing`: runs on PRs touching source files and weekly Wednesday 07:00 UTC
- `Tool 5 — UAT Facilitation`: runs on `release/*` branch creation

---

## 6. Escalation Path

| Level | Condition | Contact | Channel |
|---|---|---|---|
| L1 — On-call Engineer | Service unhealthy, restart doesn't resolve within 15 min | [TODO: on-call engineer name/rotation] | [TODO: PagerDuty / Slack channel] |
| L2 — Backend Engineer | LangGraph agent errors, Redis state corruption, assessment failures | [TODO: backend team contact] | [TODO: Slack #underwriting-chatbot-dev] |
| L3 — ML/AI Engineer | LLM output quality issues, token budget problems, model card questions | [TODO: ML engineer contact] | [TODO: channel] |
| L4 — Infrastructure / DevOps | Docker host down, database file corruption, network issues | [TODO: infra team contact] | [TODO: channel] |
| Vendor — Anthropic | API outage or sustained 5xx from `api.anthropic.com` | status.anthropic.com | [Anthropic support portal](https://support.anthropic.com) |
| Vendor — Google | Gemini API outage | cloud.google.com/support | GCP console |
| Business Owner | Regulatory / compliance concern with underwriting outputs | [TODO: business owner name] | [TODO: contact] |

---

## 7. Useful Commands

### Service Lifecycle

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Full teardown including volumes (DATA LOSS on postgres_data)
docker compose down -v

# Restart a single service
docker compose restart backend
docker compose restart redis
docker compose restart frontend

# Rebuild and redeploy backend only
docker compose build backend && docker compose up -d --no-deps backend

# View real-time logs for all services
docker compose logs -f

# View last 100 lines from backend
docker compose logs backend --tail=100
```

### Health & Diagnostics

```bash
# Backend health check
curl -f http://localhost:8000/health

# Redis ping
docker compose exec redis redis-cli ping

# Redis info (memory, clients, persistence)
docker compose exec redis redis-cli INFO

# Flush all Redis keys (clears all conversation sessions — use with caution)
docker compose exec redis redis-cli FLUSHALL

# Postgres connectivity
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Postgres — list tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# List mounted SQLite databases in backend container
docker compose exec backend ls -lh /data/

# Check container resource usage
docker stats
```

### Chat API — Manual Test

```bash
# Fast mode (Claude Haiku)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get the profile for customer CUST00000001",
    "temperature": 0.3,
    "session_id": "test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Deep mode (Claude Sonnet)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run a full underwriting assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-002",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### GitHub Actions — Manual Trigger

```bash
# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger tech docs generation
gh workflow run tool2_tech_docs.yml

# Trigger business docs for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Trigger test generation
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

### Environment Variable Verification

```bash
# Check required env vars are set in the backend container
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS|OPENAI"

# Check .env file is present
ls -la .env
```

### Database — Quick Record Lookup (SQLite)

```bash
# [TODO: What is the schema of customer_profile.db? Confirm table and column names]
docker compose exec backend sqlite3 /