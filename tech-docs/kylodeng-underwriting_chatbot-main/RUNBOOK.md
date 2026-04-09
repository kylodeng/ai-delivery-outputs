# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-component AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. A **FastAPI** backend (`backend/main.py`) exposes a streaming `/chat` endpoint backed by a LangGraph agent that orchestrates three tools: customer profile lookup, a customer lookalike finder, and a parallel multi-specialist LLM underwriting risk assessment. The agent supports two LLM providers (Anthropic Claude models and Google Gemini) and two assessment depths ("fast" and "deep"). Conversation state is persisted in **Redis** (via `AsyncRedisSaver`), structured data is stored in **SQLite databases** mounted as read-only volumes, and a **PostgreSQL** instance backs the frontend (Chainlit). The entire stack is containerised and orchestrated with **Docker Compose**. A suite of five GitHub Actions AI workflows (code review, tech docs, business docs, auto-testing, UAT) run against the repo using Claude Sonnet/Haiku via the Anthropic API.

---

## 2. Health Checks

| Component | Check | Expected Result |
|---|---|---|
| Backend API | `GET http://localhost:8000/health` | `{"status": "ok"}` HTTP 200 |
| Docker Compose stack | `docker compose ps` | All services: `Up`, backend shows `healthy` |
| Redis | `redis-cli -h localhost -p 6379 PING` | `PONG` |
| PostgreSQL | `psql -h localhost -U chainlit -d chainlit -c '\l'` | Lists databases without error |
| Frontend | `curl -f http://localhost:8080` | HTTP 200 |
| Backend healthcheck (compose) | `docker inspect --format='{{.State.Health.Status}}' <backend_container>` | `healthy` |
| LLM connectivity | POST `/chat` with a test message | SSE stream begins; first `tool_start` event arrives within 10s |
| Redis persistence | `redis-cli -h localhost -p 6379 KEYS *` | Shows session thread keys if conversations have occurred |
| SQLite databases | `sqlite3 ./database/customer_profile.db "SELECT COUNT(*) FROM sqlite_master;"` | Returns a non-zero count |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Backend container exits immediately / stuck in `starting` | Missing or malformed `.env` file; required env var (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) not set | 1. Check `docker compose logs backend`. 2. Verify `.env` exists at repo root and contains all required keys. 3. `docker compose up --build backend`. |
| `GET /health` returns connection refused | Backend container not running or crashed on startup | 1. `docker compose ps` — confirm backend is up. 2. `docker compose logs backend --tail 50`. 3. Check port 8000 is not in use: `lsof -i :8000`. |
| `GET /health` returns `unhealthy` in compose | Backend starting up slower than `start_period: 15s` or runtime crash | 1. `docker compose logs backend`. 2. Increase `start_period` in `docker-compose.yml`. 3. Re-deploy: `docker compose restart backend`. |
| Chat returns no stream / hangs | Redis unavailable; LangGraph checkpointer cannot connect | 1. `docker compose logs redis`. 2. `redis-cli -h localhost -p 6379 PING`. 3. `docker compose restart redis`. 4. Note: memory will not persist across backend restarts if Redis is down — see TODO in `graph.py`. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in request maps to `None` in `LLMS.model_mapper` (e.g. `azure`, `openai`) | 1. Verify request body sends a supported model: `anthropic`, `anthropic-fast`, or `gemini`. 2. To add Azure/OpenAI, implement the relevant provider in `backend/modules/LLMS.py`. |
| Anthropic API errors (429 / 529) | Rate limit or overload on Anthropic API | 1. Check `docker compose logs backend` for HTTP status in error. 2. Reduce concurrency: lower `asyncio.Semaphore(4)` in `assessment.py`. 3. Switch to `anthropic-fast` (Haiku) model in the request. 4. Implement exponential backoff [TODO: not currently in code]. |
| Google Gemini API errors | Invalid or missing `GOOGLE_API_KEY`; model name changed | 1. Verify `GOOGLE_API_KEY` in `.env`. 2. Check `gemini-3-flash-preview` is still a valid model name in the Google AI SDK. |
| Frontend fails to load / blank page | `BACKEND_URL` misconfigured; backend not healthy when frontend started | 1. `docker compose logs frontend`. 2. Ensure backend is `healthy` before frontend starts (compose `condition: service_healthy` should handle this). 3. `docker compose restart frontend`. |
| PostgreSQL connection error from frontend | Postgres not yet ready; wrong credentials | 1. `docker compose logs postgres`. 2. Verify `DATABASE_URL` matches `POSTGRES_USER/PASSWORD/DB` in compose. 3. `docker compose restart postgres frontend`. |
| Assessment returns empty or truncated JSON | `aggregator_max_tokens: 8000` exceeded or specialist output too large | 1. Check logs for token usage printed by `[AGGREGATOR]`. 2. Increase `aggregator_max_tokens` in `config.yml`. 3. Check specialist output is not exceeding `specialist_max_tokens: 1500`. |
| Customer profile lookup fails | SQLite DB file not present at mount path | 1. Confirm `./database/customer_profile.db` exists. 2. Check volume mount in `docker-compose.yml`. 3. `docker compose exec backend ls /data/`. |
| Session history lost after restart | Redis is ephemeral (no persistence configured) | Expected behaviour per `graph.py` TODO. To fix: configure Redis AOF/RDB persistence or migrate to Azure Cache for Redis. |
| GitHub Actions workflow fails (`ANTHROPIC_API_KEY` error) | Secret not set in repository | 1. Go to **Repo → Settings → Secrets → Actions**. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose ≥ 2.20 installed
- `.env` file at repo root with all required variables (see §5)
- SQLite database files present under `./database/`

### First-Time Deploy

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create .env from template
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate all required keys

# 3. Build and start all services
docker compose up --build -d

# 4. Confirm all services are healthy
docker compose ps

# 5. Verify backend health
curl http://localhost:8000/health

# 6. Tail logs to confirm no startup errors
docker compose logs -f --tail 50
```

### Routine Re-Deploy (code changes)

```bash
# 1. Pull latest changes
git pull origin main

# 2. Rebuild affected services only (backend or frontend)
docker compose up --build -d backend
# or for frontend:
docker compose up --build -d frontend

# 3. Confirm health
curl http://localhost:8000/health
docker compose ps
```

### Configuration Change (config.yml / prompts)

```bash
# config.yml and prompts are loaded at container startup
# No code rebuild needed — restart the backend container

docker compose restart backend
docker compose logs backend --tail 30
```

### Rollback Steps

```bash
# Option A: Rollback to previous Docker image (if images are tagged)
# [TODO: confirm image registry and tagging strategy]
docker compose down
docker tag <registry>/<image>:<previous-tag> backend:latest
docker compose up -d

# Option B: Rollback via git + rebuild
git log --oneline -10            # identify the last good commit
git checkout <commit-sha>
docker compose up --build -d backend

# Option C: If only config.yml changed — restore previous config and restart
git checkout HEAD~1 -- backend/config.yml
docker compose restart backend
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Threshold / Alert |
|---|---|---|
| Backend health endpoint | `GET /health` every 30s | Alert if non-200 for >60s |
| Container health status | `docker compose ps` | Alert if any service not `healthy` |
| Token usage per request | `[SPECIALIST]` / `[AGGREGATOR]` stdout logs | Alert if `out_tokens` approaches model limits |
| Tool execution time | `[TOOL END] <name> time=Xs` stdout | Alert if >30s per tool |
| Total request latency | `[CHAT]` log line timing | [TODO: add timing instrumentation to `/chat` endpoint] |
| Redis memory usage | `redis-cli INFO memory` | Alert if `used_memory` > 80% of available |
| LLM API error rate | Backend logs for HTTP 4xx/5xx from Anthropic/Google | Alert on sustained errors |
| Semaphore concurrency | `asyncio.Semaphore(4)` in `assessment.py` | Observe if assessment queues — consider tuning |

### Logs to Watch

```bash
# All service logs
docker compose logs -f

# Backend only (most relevant)
docker compose logs -f backend

# Filter for errors
docker compose logs backend 2>&1 | grep -i "error\|exception\|traceback"

# Filter for LLM timing
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL"

# Redis logs
docker compose logs redis
```

### Log Patterns — Normal vs. Abnormal

| Log Pattern | Status |
|---|---|
| `[CHAT] session=... msg=...` followed by `[TOOL START]` | Normal — request processing |
| `[TOOL END] run_underwriting_assessment time=Xs` | Normal if X < 30s |
| `[SPECIALIST] category=... in=NNN tok out=NNN tok` | Normal — parallel specialist LLMs |
| `[AGGREGATOR] in=NNN tok out=NNN tok` | Normal — final aggregation |
| `ValueError: Unsupported or unconfigured model` | Error — bad model name in request |
| `Connection refused` on Redis port | Error — Redis down |
| `anthropic._exceptions.OverloadedError` | Error — Anthropic API overloaded |

### Alerting

[TODO: No alerting infrastructure is defined in the codebase. Recommended additions:]
- [TODO: Add Prometheus metrics exporter to FastAPI (`prometheus-fastapi-instrumentator`)]
- [TODO: Configure uptime monitoring on `GET /health` (e.g. UptimeRobot, Datadog Synthetics)]
- [TODO: Configure log-based alerts for error patterns in chosen log aggregation tool]
- [TODO: Set up Redis and PostgreSQL memory/connection alerts]

---

## 6. Escalation Path

| Level | Role | Contact | Escalate When |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: pagerduty/slack handle] | Service down > 5 min; health check failing |
| L2 | Backend Developer | [TODO: name + contact] | LLM integration errors; assessment logic failures |
| L3 | ML / AI Lead | [TODO: name + contact] | Model output quality issues; token budget problems |
| L4 | Infrastructure Lead | [TODO: name + contact] | Redis/PostgreSQL data loss; volume mount issues |
| External | Anthropic Support | https://support.anthropic.com | Sustained Anthropic API 5xx; rate limit issues |
| External | Google AI Support | [TODO: GCP support link] | Sustained Gemini API errors |
| Repo Owner | Kylo Deng | kylo.deng@capco.com | Escalation of last resort; secret rotation |

---

## 7. Useful Commands

```bash
# ── Stack Management ───────────────────────────────────────────────
# Start full stack (detached)
docker compose up -d

# Start with rebuild
docker compose up --build -d

# Stop all services
docker compose down

# Restart a single service
docker compose restart backend
docker compose restart redis

# View running containers + health status
docker compose ps

# ── Logs ───────────────────────────────────────────────────────────
# Tail all logs
docker compose logs -f

# Tail backend logs only
docker compose logs -f backend --tail 100

# Filter for errors
docker compose logs backend 2>&1 | grep -iE "error|exception|traceback"

# Filter LLM timing lines
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL\]|\[CHAT\]"

# ── Health Checks ───────────────────────────────────────────────────
# Backend health
curl -s http://localhost:8000/health | jq .

# Redis ping
docker compose exec redis redis-cli PING

# Redis memory info
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Redis session keys
docker compose exec redis redis-cli KEYS "*"

# PostgreSQL connectivity
docker compose exec postgres psql -U chainlit -d chainlit -c '\dt'

# ── Test Chat Endpoint ──────────────────────────────────────────────
curl -s -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me about customer CUST00000001",
    "temperature": 0.3,
    "session_id": "test-session-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# ── Database Inspection ─────────────────────────────────────────────
# List tables in customer profile DB
sqlite3 ./database/customer_profile.db ".tables"

# Count customers
sqlite3 ./database/customer_profile.db "SELECT COUNT(*) FROM customer_profile;"  # [TODO: confirm table name]

# ── Config Validation ───────────────────────────────────────────────
# Check config.yml is valid YAML
python3 -c "import yaml; yaml.safe_load(open('backend/config.yml'))" && echo "Config OK"

# Check assessment_criterias.json is valid JSON
python3 -m json.tool backend/prompts/assessment_criterias.json > /dev/null && echo "JSON OK"

# ── GitHub Actions (manual triggers) ───────────────────────────────
# Trigger tech docs generation manually (requires GitHub CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# ── Environment ────────────────────────────────────────────────────
# Verify required env vars are set (run from repo root)
for var in ANTHROPIC_API_KEY GOOGLE_API_KEY; do
  [[ -z "${!var}" ]] && echo "MISSING: $var" || echo "OK: $var"
done

# ── Cleanup ────────────────────────────────────────────────────────
# Remove all containers, networks, and volumes (DESTRUCTIVE — loses Postgres data)
docker compose down -v

# Flush Redis session state only (non-destructive to app data)
docker compose exec redis redis-cli FLUSHALL
```

---

> **TODOs requiring human input:**
> - [TODO: What is the container image registry and tagging strategy for production rollbacks?]
> - [TODO: What log aggregation tool is in use (e.g. Datadog, ELK, Azure Monitor)?]
> - [TODO: Confirm exact SQLite table names for `customer_profile.db