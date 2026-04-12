# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system consists of a FastAPI backend (Python) that orchestrates a LangGraph-based AI agent, a frontend chat UI, a Redis instance for LangGraph conversation state checkpointing, and a PostgreSQL database for session persistence. When an underwriter submits a query, the agent decides which tools to invoke — fetching customer profiles from SQLite databases, running parallel specialist LLM assessments across risk domains (finance, health, life, etc.), and performing customer lookalike comparisons — before aggregating results into a structured `UnderwritingReport`. The backend streams responses to the frontend via Server-Sent Events (SSE). All LLM calls are routed through Anthropic (Claude Haiku as default fast model, Claude Sonnet for deeper assessments) with optional Google Gemini support.

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
# All services should show: Status = Up, health = healthy (backend)
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

### Backend logs (confirm agent is initialising)
```bash
docker compose logs backend --tail=50
# Look for: Uvicorn running on http://0.0.0.0:8000
# No: ConnectionRefusedError, ImportError, missing env var errors
```

### LLM API reachability
```bash
# From backend container — confirm Anthropic API is reachable
docker compose exec backend python -c "import anthropic; print('ok')"
```

### SQLite databases mounted (read-only volumes)
```bash
docker compose exec backend ls -lh /data/
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

### Frontend
```
http://localhost:8080
# Expected: Chat UI loads without errors
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container crashed or failed to start | 1. `docker compose logs backend --tail=100` for errors. 2. Check `.env` file exists with required vars. 3. `docker compose restart backend` |
| `{"detail": "Internal Server Error"}` on `/chat` | Unhandled exception in agent or tool | 1. `docker compose logs backend --tail=200`. 2. Look for Python traceback. 3. Check if LLM API key is valid and has quota. 4. Check Redis is reachable. |
| Agent returns no response / SSE stream hangs | LLM API timeout or Redis checkpoint write failure | 1. Check Anthropic API status at `status.anthropic.com`. 2. `docker compose exec redis redis-cli ping`. 3. Restart backend: `docker compose restart backend`. |
| `ConnectionRefusedError` to Redis on backend start | Redis container not yet ready, or host misconfigured | 1. Ensure `REDIS_HOST=redis` env var is set (via `env_file` or `docker-compose.yml`). 2. `docker compose up redis -d && docker compose restart backend`. |
| `ValueError: Unsupported or unconfigured model provider` | Invalid `model` parameter in chat request, or missing API key | 1. Confirm request sends `model` as one of: `anthropic`, `anthropic-fast`, `gemini`. 2. Check `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` in `.env`. |
| SQLite database not found (`/data/*.db`) | Volume mount missing or incorrect path | 1. Confirm `database/` directory exists at repo root. 2. Check `docker-compose.yml` volume paths. 3. `docker compose down && docker compose up -d`. |
| Frontend shows blank screen or cannot reach backend | `BACKEND_URL` misconfigured, or backend unhealthy | 1. Verify `BACKEND_URL=http://backend:8000` in frontend service env. 2. Confirm backend health check is passing: `docker compose ps`. |
| `POSTGRES_*` auth failure | Wrong credentials or DB not initialised | 1. Check `docker-compose.yml` env matches frontend `DATABASE_URL`. 2. `docker compose down -v && docker compose up -d` to reinitialise (⚠️ destroys data). |
| Specialist LLM assessment times out mid-stream | `specialist_max_tokens` exhausted or API rate limit | 1. Check `config.yml` — `specialist_max_tokens: 1500`. 2. Review Anthropic rate limits for the account. 3. Switch `default` model to `anthropic-fast` in `config.yml`. |
| `ImportError` or `ModuleNotFoundError` on backend start | Missing Python dependency in container | 1. `docker compose build backend --no-cache`. 2. `docker compose up -d backend`. |
| Memory not persisting across backend restarts | Redis is ephemeral (no persistence configured) | 1. See `graph.py` TODO: migrate to Azure Cache for Redis or add Redis AOF persistence. [TODO: confirm whether Redis persistence is required in production] |
| GitHub Action workflow fails with `ANTHROPIC_API_KEY` not set | Secret not configured in repository settings | 1. Go to repo → Settings → Secrets → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker and Docker Compose v2+ installed
- `.env` file present at repo root (see Environment Variables section)
- `database/` directory present with the four SQLite `.db` files
- `postgres/init.sql` present for DB initialisation

### Step-by-step deployment

**Step 1 — Clone the repository**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create the `.env` file**
```bash
cp .env.example .env   # [TODO: confirm if .env.example exists in repo]
# Edit .env and populate all required variables (see Section 5)
```

**Step 3 — Build images**
```bash
docker compose build
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
# Wait ~5s for postgres to initialise
```

**Step 5 — Start backend and confirm health**
```bash
docker compose up -d backend
# Wait for health check to pass (up to 15s start_period + 5 retries)
docker compose ps
# backend should show: healthy
```

**Step 6 — Start frontend**
```bash
docker compose up -d frontend
```

**Step 7 — Verify end-to-end**
```bash
curl -f http://localhost:8000/health
# Open browser: http://localhost:8080
```

---

### Rollback procedure

**Option A — Revert to previous image tag** [TODO: confirm image registry and tagging strategy]
```bash
# Edit docker-compose.yml to pin previous image tag
docker compose pull
docker compose up -d
```

**Option B — Revert to previous Git commit**
```bash
git log --oneline -10          # identify last known good commit
git checkout <commit-sha>
docker compose build
docker compose up -d
```

**Option C — Restart only a failing service without rebuild**
```bash
docker compose restart backend
# or
docker compose up -d --force-recreate backend
```

**Stop all services**
```bash
docker compose down
# To also remove volumes (⚠️ destroys postgres data):
docker compose down -v
```

---

## 5. Monitoring & Alerting

### Key metrics to watch

| Metric | Where | Threshold / Notes |
|---|---|---|
| Backend container health | `docker compose ps` | Must be `healthy`; alert if `unhealthy` for >2 checks |
| `/health` HTTP response code | HTTP monitor | Alert on non-200 |
| `/chat` SSE response latency | Application logs | `[CHAT]` log lines show session/model/mode; `[TOOL END]` lines show per-tool elapsed time — alert if >30s |
| LLM token usage | Backend stdout | `[SPECIALIST]` and `[AGGREGATOR]` log lines emit `in=`, `out=` token counts — watch for quota exhaustion |
| Redis memory usage | `redis-cli INFO memory` | Alert if `used_memory` approaches container limit |
| PostgreSQL connection count | `pg_stat_activity` | [TODO: confirm max_connections setting] |
| Anthropic API error rate | Backend logs | Look for `anthropic.APIError` in logs |

### Log locations

```bash
# Backend application logs (all agent events, tool timings, LLM token usage)
docker compose logs backend -f

# Key log patterns to watch:
# [CHAT]        — new incoming request
# [TOOL START]  — tool being invoked
# [TOOL END]    — tool completed with elapsed time
# [SPECIALIST]  — per-category LLM call with token counts
# [AGGREGATOR]  — final aggregation LLM call

# Redis logs
docker compose logs redis -f

# PostgreSQL logs
docker compose logs postgres -f

# Frontend logs
docker compose logs frontend -f
```

### GitHub Actions workflow monitoring

| Workflow | Trigger | Output location |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC | PR comment + `ai-delivery-outputs` repo |
| Tool 2 — Tech Docs | Push to main, Sunday 06:00 UTC | `ai-delivery-outputs` repo |
| Tool 3 — Business Docs | Version tag push, manual | `ai-delivery-outputs` repo |
| Tool 4 — Auto Testing | PR open/sync on src files, Wednesday 07:00 UTC | `ai-delivery-outputs` repo |
| Tool 5 — UAT | Release branch creation, manual | `ai-delivery-outputs` repo |

Monitor workflow runs at: `https://github.com/kylodeng/underwriting_chatbot-main/actions`

### Alerting [TODO: configure alerting integration]
- [TODO: Is Datadog / Prometheus / CloudWatch / Azure Monitor in use?]
- [TODO: What is the on-call paging tool — PagerDuty, OpsGenie, etc.?]
- Recommended: set up uptime monitor on `http://<host>:8000/health` with 1-minute polling

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in contact] | Service down, health check failing |
| L2 | Backend/ML engineer | [TODO: fill in contact] | LLM assessment errors, Redis/agent failures |
| L3 | Tech lead | [TODO: fill in contact] | Data integrity issues, security incidents |
| Vendor | Anthropic support | https://support.anthropic.com | API outage, quota issues |
| Vendor | Google Cloud support | [TODO: fill in contact] | Gemini API failures |
| Business | Solution owner | [TODO: fill in contact] | Go/no-go decisions, major incidents |

> **Note:** Notification emails are currently configured to `kylo.deng@capco.com` via SendGrid. Update `NOTIFY_EMAIL` in workflow env vars and `.env` for production routing.

---

## 7. Useful Commands

### Service management
```bash
# Start all services
docker compose up -d

# Stop all services (preserve data)
docker compose down

# Restart a single service
docker compose restart backend

# View real-time logs for all services
docker compose logs -f

# View real-time logs for backend only
docker compose logs backend -f --tail=100

# Rebuild and restart backend after code change
docker compose build backend && docker compose up -d backend

# Force recreate (picks up env changes)
docker compose up -d --force-recreate backend
```

### Health & debugging
```bash
# Check all container statuses
docker compose ps

# Backend health endpoint
curl -s http://localhost:8000/health | python3 -m json.tool

# Test chat endpoint (SSE stream)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'

# Redis health
docker compose exec redis redis-cli ping

# Redis memory info
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# List all Redis keys (session checkpoints)
docker compose exec redis redis-cli KEYS "*"

# PostgreSQL connection test
docker compose exec postgres psql -U chainlit -d chainlit -c "\l"

# Check mounted database files
docker compose exec backend ls -lh /data/
```

### LLM & configuration
```bash
# Verify environment variables are loaded in backend
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS"

# View current LLM config
docker compose exec backend cat config.yml

# Validate Python imports (quick smoke test)
docker compose exec backend python -c "from agent.graph import build_agent; print('imports ok')"
```

### GitHub Actions (requires `gh` CLI)
```bash
# List recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# Watch a specific run in real time
gh run watch --repo kylodeng/underwriting_chatbot-main <run-id>

# Manually trigger tech docs generation
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>
```

### Database inspection
```bash
# Inspect customer profile SQLite DB
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT * FROM customer LIMIT 5;"

# [TODO: confirm table names in customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db]
```

---

*Runbook auto-generated · Source: `kylodeng/underwriting_chatbot-main` · Review and complete all `[TODO]` items before production use.*