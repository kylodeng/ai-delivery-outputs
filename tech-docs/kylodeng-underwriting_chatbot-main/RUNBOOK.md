# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-container AI-assisted life insurance underwriting platform. Underwriters interact with a chat interface (frontend) that routes requests to a FastAPI backend. The backend hosts a LangGraph agent powered by Anthropic Claude (claude-haiku or claude-sonnet) and optionally Google Gemini. On request, the agent calls three tools: `get_customer_profile` (fetches structured customer data from SQLite databases), `customer_lookalike` (identifies similar customers from a pre-computed similarity dictionary), and `run_underwriting_assessment` (runs parallel specialist LLM sub-agents across assessment categories — finance, health, life, etc. — before aggregating into a structured `UnderwritingReport`). Session memory is persisted via Redis (LangGraph checkpointer), and the Chainlit frontend uses PostgreSQL for its own persistence. The system is deployed via Docker Compose and exposes the backend on port `8000` and the frontend on port `8080`.

---

## 2. Health Checks

### Backend API
```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Docker container status
```bash
docker compose ps
# All four services should show status: running (healthy)
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
# Expected: HTTP 200 (Chainlit UI)
```

### Docker health check (backend container)
```bash
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{.State.Health.Status}}'
# Expected: healthy
```

### LLM API key validity
```bash
# Confirm ANTHROPIC_API_KEY is set and non-empty
docker compose exec backend printenv ANTHROPIC_API_KEY | wc -c
# Should return > 1
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container stays `unhealthy`; `/health` returns connection refused | Backend failed to start (import error, missing env var, port conflict) | 1. `docker compose logs backend --tail=50` 2. Check for missing env vars in `.env` 3. Confirm port 8000 is free: `lsof -i :8000` 4. Rebuild: `docker compose up --build backend` |
| Chat returns `"Unsupported or unconfigured model provider"` error | Invalid `model` value in request or missing API key for that provider | 1. Confirm `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` is set in `.env` 2. Check the `model` field in the request matches keys in `LLMS.model_mapper` (`anthropic`, `anthropic-fast`, `gemini`) |
| Agent loops indefinitely or never calls `done` | LLM returned malformed JSON; regex `re.search(r'\{.*\}')` failed to parse | 1. Check backend logs for `[TOOL START]`/`[TOOL END]` with no completion 2. Increase timeout on frontend 3. Retry with lower temperature (`temperature=0`) 4. Check Anthropic API status at status.anthropic.com |
| `run_underwriting_assessment` tool hangs or times out | One or more specialist LLM calls stalled; `asyncio.Semaphore(4)` slot not released | 1. `docker compose logs backend | grep SPECIALIST` to identify which category is stalling 2. Check Anthropic API rate limits / quotas 3. Reduce `specialist_max_tokens` in `config.yml` and restart backend |
| Redis connection error: `ConnectionRefusedError` on `localhost:6379` | Redis container not running, or backend started before Redis was ready | 1. `docker compose ps redis` — restart if not running 2. Verify `REDIS_HOST=redis` is set (not `localhost`) in backend env 3. `docker compose restart backend` after Redis is healthy |
| Frontend fails to load or shows database errors | PostgreSQL not initialised or `init.sql` not applied | 1. `docker compose logs postgres --tail=30` 2. If first run, confirm `./postgres/init.sql` exists 3. Destroy and recreate volume: `docker compose down -v && docker compose up` |
| SQLite database files not found; `get_customer_profile` returns errors | Volume mount missing or database files absent from `./database/` | 1. Confirm `./database/*.db` files exist on the host 2. Check volume mounts in `docker-compose.yml` are correct 3. `docker compose exec backend ls /data/` |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY` not found | Secret not set in repository settings | 1. Navigate to repo → Settings → Secrets and variables → Actions 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` |
| Assessment report returns empty `areas_of_interest` | Aggregator LLM output exceeded `aggregator_max_tokens` (8000) or structured output parsing failed | 1. Check backend logs for `[AGGREGATOR]` token counts 2. If `out` tokens near 8000, increase `aggregator_max_tokens` in `config.yml` 3. Restart backend |
| Memory not persisted across sessions | Redis is ephemeral (no persistence configured); data lost on container restart | 1. [TODO: Is Redis persistence (AOF/RDB) required? If so, add `command: redis-server --appendonly yes` to docker-compose] 2. See TODO in `graph.py` re: migrating to Azure Cache for Redis |

---

## 4. Deployment Procedure

### Prerequisites
- Docker ≥ 24.x and Docker Compose ≥ 2.x installed
- `.env` file present in repo root with all required variables (see §5)
- `./database/*.db` SQLite files present
- `./postgres/init.sql` present

### Step-by-step deployment

**Step 1 — Clone the repository**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create the environment file**
```bash
cp .env.example .env      # [TODO: does .env.example exist? If not, create from variables listed in §5]
# Edit .env and fill in all required values
```

**Step 3 — Build all images**
```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
# Wait for postgres to be healthy
docker compose ps
```

**Step 5 — Start application services**
```bash
docker compose up -d backend frontend
```

**Step 6 — Verify deployment**
```bash
docker compose ps
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

**Step 7 — Smoke test**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
# Expect SSE stream with response events
```

---

### Rollback steps

**Option A — Roll back to previous image (if images are tagged)**
```bash
# [TODO: confirm image tagging strategy — are images pushed to a registry?]
docker compose down
# Edit docker-compose.yml to pin previous image tag
docker compose up -d
```

**Option B — Roll back via git**
```bash
git log --oneline -5           # identify the last good commit
git checkout <commit-sha>
docker compose build --no-cache
docker compose up -d
```

**Option C — Restart a single failing service without full rebuild**
```bash
docker compose restart backend
# or
docker compose up -d --force-recreate backend
```

**Preserve data before rollback**
```bash
docker compose exec postgres pg_dump -U chainlit chainlit > backup_$(date +%Y%m%d).sql
# SQLite files are host-mounted (read-only) — no action needed
```

---

## 5. Monitoring & Alerting

### Key metrics to watch

| Metric | Source | Warning threshold |
|---|---|---|
| Backend health check | `GET /health` | Any non-200 response |
| Container restart count | `docker compose ps` / Docker daemon | > 2 restarts in 10 min |
| Redis memory usage | `redis-cli INFO memory` | > 80% `maxmemory` |
| LLM token counts (specialist) | Backend stdout: `[SPECIALIST]` lines | `out` tokens consistently near 1500 |
| LLM token counts (aggregator) | Backend stdout: `[AGGREGATOR]` lines | `out` tokens near 8000 |
| Tool execution time | Backend stdout: `[TOOL END] name time=Xs` | > 30s for any tool |
| End-to-end request latency | Backend stdout: `[CHAT]` session logs | [TODO: define SLA threshold] |
| Anthropic API errors | Backend stderr / logs | Any 4xx/5xx from Anthropic |

### Log sources

```bash
# Backend application logs (includes SPECIALIST, AGGREGATOR, TOOL timing)
docker compose logs -f backend

# Frontend logs
docker compose logs -f frontend

# Redis logs
docker compose logs -f redis

# PostgreSQL logs
docker compose logs -f postgres

# All services combined
docker compose logs -f --tail=100
```

### Log patterns to alert on

```
# LLM API failures
grep -i "error\|exception\|traceback" <(docker compose logs backend 2>&1)

# Tool failures
grep "TOOL END" <(docker compose logs backend 2>&1) | grep -v "time=[0-2][0-9]\."

# Health check failures
grep "unhealthy\|failed" <(docker compose logs backend 2>&1)
```

### GitHub Actions workflow monitoring

- Navigate to: `https://github.com/kylodeng/underwriting_chatbot-main/actions`
- Watch for failed runs on: `tool1_code_review.yml`, `tool2_tech_docs.yml`
- Scheduled runs: Code review every Monday 08:00 UTC; Tech docs every Sunday 06:00 UTC

### Alerting

[TODO: Is there a PagerDuty, OpsGenie, or Slack webhook configured for container health alerts?]  
[TODO: Is there a CloudWatch, Datadog, or Prometheus instance scraping these containers?]  
[TODO: Are Anthropic API quota alerts configured in the Anthropic console?]

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in contact] | Service down > 5 min, health check failing |
| L2 | Backend/ML Lead | [TODO: fill in contact] | LLM quality issues, assessment logic failures, token budget exceeded |
| L3 | Platform/Infra Lead | [TODO: fill in contact] | Redis/Postgres data loss, Docker host failures, network issues |
| L4 | Anthropic Support | https://support.anthropic.com | API outage, model deprecation, quota exhaustion |
| L4 | Google Cloud Support | [TODO: fill in contact] | Gemini API failures |
| Business Owner | [TODO: fill in name] | [TODO: fill in contact] | Data breach, regulatory incident, prolonged outage > 1 hour |

**Incident communication channel:** [TODO: Slack channel / Teams channel?]  
**Incident log / ticketing system:** [TODO: Jira / ServiceNow project?]

---

## 7. Useful Commands

### Start / stop services
```bash
# Start all services
docker compose up -d

# Stop all services (preserve volumes)
docker compose down

# Stop and destroy all volumes (DESTRUCTIVE)
docker compose down -v

# Restart a single service
docker compose restart backend

# Rebuild and restart backend only
docker compose up -d --build --force-recreate backend
```

### View logs
```bash
# Follow all logs
docker compose logs -f

# Backend only, last 100 lines
docker compose logs -f --tail=100 backend

# Filter for assessment timing
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL"

# Filter for errors
docker compose logs backend 2>&1 | grep -iE "error|exception|traceback"
```

### Health & status
```bash
# Check all container states
docker compose ps

# Backend health endpoint
curl -s http://localhost:8000/health | python3 -m json.tool

# Redis ping
docker compose exec redis redis-cli ping

# Redis memory info
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# PostgreSQL connectivity
docker compose exec postgres pg_isready -U chainlit -d chainlit

# List PostgreSQL tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"
```

### Test the chat endpoint
```bash
# Fast mode, anthropic-fast model
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get profile for CUST00000001",
    "temperature": 0,
    "session_id": "test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Deep assessment mode
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run a full assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-002",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### Database inspection
```bash
# List SQLite databases mounted into backend
docker compose exec backend ls -lh /data/

# Query customer profile database
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db \
  "SELECT * FROM customer_profile WHERE customer_id='CUST00000001' LIMIT 1;"
```

### Environment variable check
```bash
# Confirm critical env vars are loaded in backend container
docker compose exec backend printenv | grep -E "ANTHROPIC|GOOGLE|REDIS|OPENAI" | sed 's/=.*/=***REDACTED***/'
```

### GitHub Actions — manual trigger
```bash
# Trigger tech docs generation manually (requires gh CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=42
```

### Emergency: flush Redis session memory
```bash
# WARNING: this clears ALL LangGraph checkpoints (all session memory)
docker compose exec redis redis-cli FLUSHALL
```

---

*Runbook auto-generated · Source: `kylodeng/underwriting_chatbot-main` · [TODO: add version and last-reviewed date]*