# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system is composed of four containerised services: a **FastAPI backend** (Python) that hosts an AI agent powered by Anthropic Claude (via LangChain/LangGraph), a **frontend** chat UI, a **Redis** instance (Redis Stack) used for LangGraph conversation checkpoint persistence, and a **PostgreSQL** database used by the frontend (Chainlit) for session storage. The backend agent orchestrates three tools — customer profile lookup, customer lookalike matching, and a multi-specialist underwriting risk assessment — which run parallel LLM calls across finance, health, life, and other assessment domains before aggregating results into a structured `UnderwritingReport`. All LLM calls are routed through a configurable `LLMS` abstraction supporting Anthropic Claude (Sonnet and Haiku) and Google Gemini.

---

## 2. Health Checks

### Backend API
```bash
curl -sf http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Docker Compose service status
```bash
docker compose ps
# All services should show: Status = running (healthy)
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
curl -sf http://localhost:8080
# Expected: HTTP 200
```

### Backend container health (Docker built-in)
```bash
docker inspect underwriting_chatbot-backend-1 --format='{{.State.Health.Status}}'
# Expected: healthy
```

### End-to-end smoke test
```bash
curl -sf -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
# Expected: SSE stream with at least one "response" event
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container exits immediately on startup | Missing `.env` file or required environment variable (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) not set | 1. Check `docker compose logs backend`. 2. Verify `.env` exists at project root. 3. Confirm all required vars are populated (see §5 env table). 4. `docker compose up --build backend` |
| `GET /health` returns 502 or connection refused | Backend container is not running or crashed | 1. `docker compose ps` to check state. 2. `docker compose logs backend --tail=50`. 3. `docker compose restart backend` |
| Chat requests hang indefinitely / no SSE events | Redis checkpoint save failing; agent cannot persist state | 1. `docker compose logs backend` for Redis connection errors. 2. `docker compose exec redis redis-cli ping`. 3. If Redis is down: `docker compose restart redis`, then `docker compose restart backend`. 4. Check `REDIS_HOST` env var is set to `redis` (not `localhost`) inside container |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request does not match a key in `LLMS.model_mapper` | 1. Check request payload — valid values: `anthropic`, `anthropic-fast`, `gemini`. 2. Verify `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` are set. 3. Note: `azure` and `openai` providers are explicitly `None` and will fail |
| Anthropic API 429 / rate limit errors in logs | Too many concurrent LLM calls; assessment uses up to 4 parallel specialist calls | 1. `docker compose logs backend \| grep -i "429\|rate"`. 2. Reduce `asyncio.Semaphore` value in `assessment.py` (currently `4`) — lower to `2`. 3. Switch to a higher-tier Anthropic API plan. [TODO: What is the current Anthropic tier/quota?] |
| Assessment returns empty or malformed `UnderwritingReport` | Aggregator LLM exceeded `aggregator_max_tokens` (8000) or structured output parsing failed | 1. Check logs for `[AGGREGATOR]` output token count. 2. Increase `aggregator_max_tokens` in `config.yml`. 3. Check `assessment_criterias.json` for prompt length issues |
| Frontend shows blank page or cannot connect to backend | `BACKEND_URL` misconfigured in frontend container, or backend healthcheck not yet passed | 1. `docker compose logs frontend`. 2. Confirm `BACKEND_URL=http://backend:8000` in `docker-compose.yml`. 3. Backend `depends_on: condition: service_healthy` — wait for backend to become healthy before frontend starts: `docker compose up --wait` |
| PostgreSQL init fails; frontend crashes on session save | `postgres/init.sql` missing or permission error on volume | 1. `docker compose logs postgres`. 2. Check `./postgres/init.sql` exists in repo root. 3. Remove stale volume: `docker compose down -v && docker compose up` (**data loss** — confirm with team first) |
| `customer_profile.db` or other SQLite databases not found | Volume mounts in `docker-compose.yml` point to non-existent local paths | 1. Confirm `./database/*.db` files exist in project root. 2. [TODO: Where are the database files sourced from — are they committed to the repo, generated by a setup script, or downloaded from an external source?] |
| Lookalike tool returns empty results | `customer_similarity_dict.json` not loaded or `CUST` ID not found | 1. Check `backend/tmp/customer_similarity_dict.json` is present. 2. Verify the customer ID format matches `CUST########` pattern |
| GitHub Actions workflows fail on secrets | `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` not set as repo secrets | 1. Go to repo → Settings → Secrets and variables → Actions. 2. Add missing secrets. 3. Re-run the failed workflow |

---

## 4. Deployment Procedure

### Prerequisites
- Docker Engine ≥ 24.x and Docker Compose plugin installed
- `.env` file populated (see §5)
- `./database/*.db` SQLite files present
- `./postgres/init.sql` present

### Step-by-step deployment

**Step 1 — Clone the repository**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create and populate `.env`**
```bash
cp .env.example .env   # [TODO: Does a .env.example exist? If not, create one]
# Edit .env and fill in all required variables (see §5)
```

**Step 3 — Build images**
```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
# Wait for postgres to be ready
docker compose exec postgres pg_isready -U chainlit -d chainlit
```

**Step 5 — Start backend and wait for health**
```bash
docker compose up -d backend
# Poll until healthy (up to 75s given start_period=15s + 5 retries × 10s interval)
docker compose exec backend curl -sf http://localhost:8000/health
```

**Step 6 — Start frontend**
```bash
docker compose up -d frontend
```

**Step 7 — Verify all services**
```bash
docker compose ps
curl -sf http://localhost:8000/health
curl -sf http://localhost:8080
```

**Step 8 — Smoke test the chat endpoint**
```bash
curl -sf -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
```

---

### Rollback procedure

**Option A — Revert to previous Docker image tag** (if images are tagged before deployment)
```bash
# [TODO: Is there a container registry? What is the image naming convention?]
docker compose down
# Edit docker-compose.yml to point to previous image tag
docker compose up -d
```

**Option B — Revert via git**
```bash
git log --oneline -10               # identify last known-good commit
git checkout <previous-commit-sha>
docker compose down
docker compose build --no-cache
docker compose up -d
```

**Option C — Emergency rollback of config only** (e.g. `config.yml` change caused LLM failures)
```bash
git diff HEAD~1 backend/config.yml  # review what changed
git checkout HEAD~1 -- backend/config.yml
docker compose restart backend
```

**Data rollback note:** PostgreSQL data is in the `postgres_data` named volume. If schema migration caused issues:
```bash
docker compose down
docker volume rm underwriting_chatbot-main_postgres_data
docker compose up -d postgres
# Re-run any seed/migration scripts
# [TODO: Are there database migration scripts? Is there a backup procedure?]
```

---

## 5. Monitoring & Alerting

### Key metrics to watch

| Metric | What to monitor | Alert threshold |
|---|---|---|
| Backend health endpoint | `GET /health` response code | Non-200 for > 30s |
| Container restart count | `docker compose ps` — `Restarts` column | > 2 restarts in 10 min |
| LLM response latency | `[SPECIALIST]` and `[AGGREGATOR]` log lines — `time=` field | > 30s per specialist call |
| LLM token consumption | `in=` / `out=` token counts in specialist/aggregator logs | Aggregator `out` approaching 8000 |
| Redis memory | `docker compose exec redis redis-cli info memory \| grep used_memory_human` | [TODO: Set threshold based on expected checkpoint volume] |
| Anthropic API errors | Log lines containing `429`, `529`, `AuthenticationError` | Any occurrence |
| Assessment semaphore pressure | Concurrent `[TOOL START] run_underwriting_assessment` log lines | > 4 simultaneous |

### Key log locations

```bash
# All services
docker compose logs -f

# Backend only (most relevant)
docker compose logs -f backend

# Filter for assessment timing
docker compose logs backend | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[ASSESSMENT\]"

# Filter for errors
docker compose logs backend | grep -iE "error|exception|traceback|failed"

# Filter for tool calls
docker compose logs backend | grep -E "\[TOOL START\]|\[TOOL END\]"

# Filter for chat sessions
docker compose logs backend | grep "\[CHAT\]"
```

### Structured log patterns to alert on

```
[CHAT]          # New session start — monitor frequency for load
[TOOL START]    # Tool invocations — monitor for hanging tools
[TOOL END]      # time= field — latency SLI
[SPECIALIST]    # Per-domain LLM call timing and token usage
[AGGREGATOR]    # Final aggregation timing
```

### GitHub Actions workflow monitoring

- Navigate to: `https://github.com/kylodeng/underwriting_chatbot-main/actions`
- Watch for failed runs on: `Tool 1 — Code Review`, `Tool 2 — Tech Documentation`
- Workflow failures will surface missing secrets or broken dependencies early

### Alerting

[TODO: Is there an existing alerting platform (PagerDuty, OpsGenie, Azure Monitor, Datadog)?]  
[TODO: Are container logs shipped to a centralised log aggregator (e.g. Azure Log Analytics, Splunk, ELK)?]  
[TODO: What is the target RTO/RPO for this service?]

---

## 6. Escalation Path

| Level | Role | Contact | Condition to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in on-call rotation] | Service down > 5 min |
| L2 | Backend / ML engineer | [TODO: fill in team contact] | LLM errors, assessment logic failures, model accuracy concerns |
| L3 | Tech lead | [TODO: fill in tech lead contact] | Data breach, persistent outage > 30 min, cost spike on Anthropic API |
| External | Anthropic support | [Anthropic status](https://status.anthropic.com) | Sustained API 5xx errors not caused by local config |
| External | Google Cloud support | [GCP status](https://status.cloud.google.com) | Gemini API failures |

> **Note:** Notification emails are currently configured to `kylo.deng@capco.com` via SendGrid in the GitHub Actions workflows. [TODO: Update `NOTIFY_EMAIL` for production on-call distribution list.]

---

## 7. Useful Commands

### Service management

```bash
# Start all services
docker compose up -d

# Stop all services (preserve data)
docker compose down

# Stop all services and remove volumes (destructive)
docker compose down -v

# Restart a single service
docker compose restart backend

# Rebuild and redeploy backend only
docker compose build backend && docker compose up -d --no-deps backend

# View real-time logs for all services
docker compose logs -f

# View last 100 lines for backend
docker compose logs --tail=100 backend

# Check service health status
docker compose ps
```

### Debugging

```bash
# Open a shell in the backend container
docker compose exec backend bash

# Check environment variables inside backend container
docker compose exec backend env | grep -E "ANTHROPIC|REDIS|GOOGLE|ANTHROPIC"

# Check backend config
docker compose exec backend cat /app/config.yml

# Verify SQLite database mounts
docker compose exec backend ls -la /data/

# Inspect Redis checkpoint keys
docker compose exec redis redis-cli keys "*"

# Check Redis memory usage
docker compose exec redis redis-cli info memory | grep used_memory_human

# Flush Redis (clears all conversation checkpoints — use with caution)
docker compose exec redis redis-cli flushall

# PostgreSQL: list tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"
```

### Application testing

```bash
# Health check
curl -sf http://localhost:8000/health | jq .

# Full chat request (fast mode, Haiku model)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Assess customer CUST00000001",
    "temperature": 0.3,
    "session_id": "test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Full chat request (deep mode, Sonnet model)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run a full assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-001",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### LLM configuration tuning

```bash
# Temporarily reduce specialist parallelism (edit in-container — not persistent)
docker compose exec backend sed -i 's/Semaphore(4)/Semaphore(2)/' /app/modules/assessment.py
docker compose restart backend

# View current LLM config
docker compose exec backend cat /app/config.yml

# Update token limits (edit config.yml, then restart)
# specialist_max_tokens: 1500  → increase if specialist responses are truncated
# aggregator_max_tokens: 8000  → increase if report JSON is incomplete
docker compose restart backend
```

### GitHub Actions (CI/CD tooling)

```bash
# Manually trigger tech docs generation
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger code review on a PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  