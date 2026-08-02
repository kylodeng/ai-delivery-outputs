# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-service AI-powered decision-support tool designed to assist insurance underwriters in assessing customer risk. The system exposes a streaming chat API (FastAPI backend on port 8000) backed by LangGraph agents that orchestrate calls to specialist LLM assessors (Anthropic Claude and Google Gemini). When an underwriter submits a customer query, the agent retrieves the customer profile from a SQLite database, runs parallel specialist assessments across risk domains (finance, health, life, etc.), and aggregates them into a structured `UnderwritingReport`. A Redis instance provides LangGraph conversation-state checkpointing, PostgreSQL backs the Chainlit frontend session store, and a React/Chainlit frontend (port 8080) provides the chat UI. Five GitHub Actions CI workflows handle automated code review, documentation generation, test generation, business documentation, and UAT facilitation via Claude (`claude-sonnet-4-6`/`claude-haiku-4-5`).

---

## 2. Health Checks

### Backend API
```bash
# Should return {"status": "ok"}
curl -f http://localhost:8000/health
```

### Docker Compose Services
```bash
# Check all containers are Up and healthy
docker compose ps

# Expected: backend healthy, redis running, postgres running, frontend running
```

### Redis (LangGraph Checkpointer)
```bash
docker compose exec redis redis-cli ping
# Expected: PONG

docker compose exec redis redis-cli info replication
# Confirm connected_slaves and role:master
```

### PostgreSQL (Chainlit Session Store)
```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### Frontend
```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### LLM API Connectivity
```bash
# Verify Anthropic key is valid
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01"
# Expected: 200 with model list

# Verify Google API key
curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY"
# Expected: 200 with model list
```

### Database Files
```bash
# All three SQLite DBs must be present and non-empty
ls -lh database/*.db
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container exits immediately or fails healthcheck | Missing or invalid `.env` file; `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` not set | 1. Check `docker compose logs backend`. 2. Verify `.env` exists in repo root with all required vars. 3. Run `docker compose up --build backend`. |
| `/health` returns 500 or connection refused | FastAPI failed to start; Python import error on startup | 1. `docker compose logs backend --tail=50`. 2. Check for missing Python dependencies or bad imports. 3. Rebuild: `docker compose build backend && docker compose up backend`. |
| Chat request hangs indefinitely / no SSE events | Redis unavailable; LangGraph checkpointer cannot connect | 1. `docker compose ps redis` — confirm running. 2. `docker compose exec redis redis-cli ping`. 3. Check `REDIS_HOST` env var is set to `redis` (not `localhost`). 4. `docker compose restart redis backend`. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request is invalid or LLM not configured | 1. Check request payload `model` field — must be one of: `anthropic`, `anthropic-fast`, `gemini`. 2. Check `GOOGLE_API_KEY` is set if using Gemini. 3. Review `backend/modules/LLMS.py` for supported names. |
| `AuthenticationError` or 401 from Anthropic/Google | API key expired, revoked, or not set | 1. Validate keys: see health check curl commands above. 2. Rotate keys in `.env` and in GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`). 3. `docker compose up -d` to reload env. |
| Assessment returns empty or malformed `UnderwritingReport` | Aggregator LLM hit `aggregator_max_tokens` (8000) limit or returned unparseable JSON | 1. Check `docker compose logs backend` for `[AGGREGATOR]` log lines and token counts. 2. Increase `aggregator_max_tokens` in `backend/config.yml` if output tokens are at ceiling. 3. Redeploy backend. |
| Frontend cannot reach backend (`502` / `ERR_CONNECTION_REFUSED`) | `BACKEND_URL` misconfigured, or backend healthcheck failing so frontend didn't start | 1. `docker compose ps` — confirm backend is `healthy`. 2. Verify `BACKEND_URL=http://backend:8000` in frontend environment. 3. `docker compose restart frontend`. |
| PostgreSQL init fails; frontend crashes on session writes | `init.sql` missing or incompatible schema | 1. `docker compose logs postgres`. 2. Confirm `./postgres/init.sql` exists. 3. Destroy volume and reinit: `docker compose down -v && docker compose up -d postgres`. |
| Customer profile not found in chat | SQLite DB files not mounted or empty | 1. `ls -lh database/*.db` — confirm files exist. 2. Check `docker-compose.yml` volume mounts for `customer_profile.db`. 3. If DB missing, [TODO: what is the process to restore or regenerate the SQLite databases?] |
| GitHub Actions workflow fails (`tool1`–`tool5`) | Missing GitHub secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) | 1. Navigate to repo → Settings → Secrets. 2. Verify all three secrets are present. 3. Re-run failed workflow. |
| CI tool writes to output repo fail with 404/403 | `GH_TOKEN` lacks write access to `ai-delivery-outputs` repo | 1. Verify token scopes include `repo`. 2. Confirm `OUTPUT_REPO_OWNER` matches the org/user owning `ai-delivery-outputs`. 3. Rotate token if expired. |
| Redis memory exhaustion — LangGraph state lost | No eviction policy set; Redis fills up over time | 1. `docker compose exec redis redis-cli info memory`. 2. Set eviction: `docker compose exec redis redis-cli config set maxmemory-policy allkeys-lru`. 3. [TODO: configure `maxmemory` limit appropriate for your server RAM]. |
| Specialist LLM calls timeout during assessment | Rate limiting from Anthropic/Google; or `specialist_max_tokens=1500` too low for response | 1. Check logs for HTTP 429 responses. 2. Implement retry logic [TODO: no retry logic currently present in `assessment.py`]. 3. Temporarily reduce concurrent specialists by lowering `asyncio.Semaphore(4)`. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker ≥ 24.x and Docker Compose ≥ 2.x installed
- `.env` file present in repo root (see Environment Variables section)
- SQLite database files present in `./database/`
- `./postgres/init.sql` present

### Step-by-Step Deployment

**Step 1 — Clone repository**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Configure environment**
```bash
cp .env.example .env   # [TODO: confirm .env.example exists or document required vars]
# Edit .env and populate all required variables (see Section 5)
nano .env
```

**Step 3 — Build images**
```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
# Wait for postgres to be ready
sleep 5
docker compose exec postgres pg_isready -U chainlit
```

**Step 5 — Start backend and verify health**
```bash
docker compose up -d backend
# Poll health endpoint until healthy (up to 15s start_period + 5 retries × 10s = 65s max)
for i in {1..10}; do
  curl -sf http://localhost:8000/health && echo " HEALTHY" && break
  echo "Waiting... ($i)"
  sleep 8
done
```

**Step 6 — Start frontend**
```bash
docker compose up -d frontend
```

**Step 7 — Verify all services**
```bash
docker compose ps
# All services should show: Up (healthy) or Up
curl -f http://localhost:8080    # Frontend reachable
curl -f http://localhost:8000/health  # Backend healthy
```

**Step 8 — Smoke test**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
# Expected: SSE stream with at least one event
```

---

### Rollback Steps

**Option A — Roll back to previous Docker image tag**
```bash
# [TODO: confirm image registry and tagging strategy — no registry config found in repo]
docker compose down
# Edit docker-compose.yml image tags to previous known-good version
docker compose up -d
```

**Option B — Roll back via Git**
```bash
git log --oneline -10                  # Identify last good commit
git checkout <previous-good-commit>
docker compose build --no-cache
docker compose up -d
```

**Option C — Roll back config only (e.g. LLM model change)**
```bash
# Edit backend/config.yml to restore previous values
docker compose restart backend
# Verify health
curl -f http://localhost:8000/health
```

**Database rollback**
```bash
# SQLite DBs are mounted read-only (:ro) — no write risk to production data
# PostgreSQL (session state only — safe to wipe):
docker compose down
docker volume rm underwriting_chatbot-main_postgres_data
docker compose up -d postgres
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | How to Observe | Alert Threshold |
|---|---|---|
| Backend healthcheck status | `docker compose ps` / Docker healthcheck | Any non-healthy state |
| `[SPECIALIST]` LLM call duration | `docker compose logs backend \| grep SPECIALIST` | > 30s per category |
| `[AGGREGATOR]` output token count | `docker compose logs backend \| grep AGGREGATOR` | Approaching 8000 tokens |
| Redis memory usage | `redis-cli info memory \| grep used_memory_human` | > 80% of available |
| LLM API error rate | Backend logs for `AuthenticationError`, HTTP 429, 500 | Any occurrence |
| Chat request latency | Backend stdout `[CHAT]` log lines | [TODO: define SLA threshold] |
| Tool call counts per session | `[TOOL START]` / `[TOOL END]` log lines | Loops (same tool > 3× per session) |

### Log Locations

```bash
# Live backend logs (most operational signal here)
docker compose logs -f backend

# Filter for assessment performance
docker compose logs backend | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[ASSESSMENT\]"

# Filter for tool calls
docker compose logs backend | grep -E "\[TOOL START\]|\[TOOL END\]"

# Filter for chat sessions
docker compose logs backend | grep "\[CHAT\]"

# Redis logs
docker compose logs redis

# Postgres logs
docker compose logs postgres
```

### GitHub Actions CI Monitoring

- Navigate to: `https://github.com/kylodeng/underwriting_chatbot-main/actions`
- Watch for failures on:
  - **Tool 1 (Code Review)** — triggers on every PR
  - **Tool 2 (Tech Docs)** — triggers on every merge to `main`
  - **Tool 4 (Auto Testing)** — triggers on PRs touching `src/**` or `*.py`

### Alerting

[TODO: No alerting infrastructure (Prometheus, Datadog, CloudWatch, PagerDuty, etc.) is configured in this repo. Recommend instrumenting the `/health` endpoint with an external uptime monitor and setting up log-based alerts for ERROR-level log lines from the backend container.]

[TODO: No structured logging (JSON) is used — all logs are unstructured print() statements. Consider adding structlog or similar for easier alerting integration.]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in PagerDuty/Slack handle] | Service down > 5 minutes; healthcheck failing |
| L2 | Backend Lead | [TODO: fill in contact] | LLM integration failures; data integrity issues; Redis state loss |
| L3 | AI/ML Lead | [TODO: fill in contact] | UnderwritingReport quality degradation; model card discrepancies; prompt injection concerns |
| L4 | Anthropic Support | https://support.anthropic.com | API outages; persistent 5xx from Claude API |
| L4 | Google Cloud Support | [TODO: fill in GCP support link] | Google Gemini API outages |
| Product Owner | [TODO: fill in contact] | [TODO: fill in contact] | Business-critical outage during underwriter working hours |
| Security | [TODO: fill in CISO/security team] | [TODO: fill in contact] | Suspected API key leak; unauthorised data access |

**Incident Communication Channel:** [TODO: Slack channel / Teams channel name]

**Incident Ticketing:** [TODO: Jira project key or ServiceNow queue]

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Rebuild and restart everything
docker compose down && docker compose build --no-cache && docker compose up -d

# Restart a single service
docker compose restart backend

# View real-time logs for all services
docker compose logs -f

# View real-time logs for backend only
docker compose logs -f backend

# Check service health and ports
docker compose ps
```

### Debugging

```bash
# Shell into backend container
docker compose exec backend bash

# Shell into Redis
docker compose exec redis redis-cli

# List all Redis keys (LangGraph thread state)
docker compose exec redis redis-cli keys "*"

# Delete a specific session's checkpoint (replace SESSION_ID)
docker compose exec redis redis-cli del "SESSION_ID"

# Flush ALL Redis state (WARNING: clears all conversation history)
docker compose exec redis redis-cli flushall

# Check PostgreSQL tables (Chainlit sessions)
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# Verify SQLite DB files inside backend container
docker compose exec backend ls -lh /data/
```

### Environment & Config

```bash
# Verify environment variables are loaded in backend
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS|OPENAI"

# Hot-reload config changes (config.yml — requires container restart)
docker compose restart backend

# Print current LLM config
docker compose exec backend cat /app/config.yml  # [TODO: confirm /app path matches Dockerfile WORKDIR]
```

### API Testing

```bash
# Health check
curl -f http://localhost:8000/health

# Send a chat message (streaming)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Assess customer CUST00000001",
    "temperature": 0.3,
    "session_id": "test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Send with deep analysis mode
curl -N -X POST http://localhost:8000/chat \
  -H "