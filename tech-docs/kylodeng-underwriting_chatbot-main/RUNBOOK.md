# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The system exposes a FastAPI backend (`/chat`, `/health`) that orchestrates a LangGraph agent powered by Anthropic Claude (Haiku for fast responses, Sonnet for deep analysis) and Google Gemini. The agent can call three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — to retrieve customer data from SQLite databases, find similar customers, and run parallel specialist LLM assessments across domains (finance, health, life, etc.). Conversation state is persisted in Redis (LangGraph checkpointer); a PostgreSQL database backs the Chainlit frontend session store. All services are containerised and orchestrated via Docker Compose. Five GitHub Actions CI workflows (Claude-powered code review, tech docs, business docs, auto-testing, UAT facilitation) run against the repository automatically.

---

## 2. Health Checks

### Backend API
```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Docker Container Status
```bash
docker compose ps
# All four services (redis, postgres, backend, frontend) should show "running (healthy)" or "Up"
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

### LLM API Keys
```bash
# Anthropic
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.models[0].id'

# Google (Gemini)
curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY" | jq '.models[0].name'
```

### Database Files Present
```bash
ls -lh database/*.db
# Expect: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns 5xx or connection refused | Backend container crashed or failed to start | 1. `docker compose logs backend --tail=100` 2. Check for missing `.env` variables 3. `docker compose restart backend` |
| Backend starts but `/chat` returns 500 | Missing or invalid `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | 1. Verify `.env` has all required keys 2. Test keys directly (see Health Checks) 3. `docker compose restart backend` |
| Agent loops or never returns `"action": "done"` | LLM returning malformed JSON; tool call cycle not terminating | 1. Check `docker compose logs backend` for `[DEBUG]` JSON parse errors 2. Reduce `temperature` in request or `config.yml` 3. Restart backend to clear in-flight state |
| `Redis connection refused` / checkpointer errors | Redis container down; `REDIS_HOST` misconfigured | 1. `docker compose up -d redis` 2. Confirm `REDIS_HOST=redis` in backend environment 3. `docker compose exec redis redis-cli ping` |
| Frontend shows blank page or cannot connect to backend | `BACKEND_URL` misconfigured, or backend not healthy yet | 1. Confirm `BACKEND_URL=http://backend:8000` in `docker-compose.yml` 2. Wait for backend healthcheck to pass: `docker compose ps` 3. `docker compose restart frontend` |
| PostgreSQL init fails; frontend session errors | Missing `postgres/init.sql` or volume corruption | 1. `docker compose logs postgres` 2. If corrupted: `docker compose down -v && docker compose up -d` ⚠️ destroys data |
| Assessment tool hangs / very slow response | Specialist LLM calls hitting token limits or API rate limits | 1. Check `specialist_max_tokens` in `config.yml` (default 1500) 2. Check Anthropic API status at status.anthropic.com 3. Switch `model` to `anthropic-fast` in request payload |
| `customer_similarity_dict.json` lookup fails | File missing from `backend/tmp/` | 1. Confirm file present: `ls backend/tmp/customer_similarity_dict.json` 2. Re-run similarity pre-computation [TODO: what script generates this file?] |
| SQLite database not found | Volume mount path incorrect or `.db` files missing | 1. Confirm files exist in `./database/` 2. Check `docker-compose.yml` volume mounts match actual paths 3. [TODO: how are database files provisioned — are they checked into the repo or generated?] |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY not found` | Secret not set in repo settings | 1. Go to repo → Settings → Secrets and variables → Actions 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in request does not match keys in `LLMS.model_mapper` | 1. Valid values: `gemini`, `anthropic`, `anthropic-fast` 2. `azure` and `openai` are stubbed — do not use 3. Correct the request payload or frontend default |
| Underwriting report JSON incomplete / truncated | `aggregator_max_tokens` too low for full `UnderwritingReport` | 1. Increase `aggregator_max_tokens` in `config.yml` (currently 8000) 2. `docker compose restart backend` to reload config |
| Memory not persisting across backend restarts | Redis is ephemeral (no AOF/RDB persistence configured) | 1. This is a known TODO in `graph.py` 2. For production: migrate to Azure Cache for Redis or add Redis persistence flags |

---

## 4. Deployment Procedure

### Prerequisites
- Docker ≥ 24 and Docker Compose v2
- `.env` file in repo root with all required variables (see §5 / Environment Variables)
- SQLite database files present in `./database/`

### Step-by-Step Deployment

**Step 1 — Clone the repository**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create the environment file**
```bash
cp .env.example .env   # [TODO: confirm .env.example exists or document all required keys]
# Edit .env and populate:
#   ANTHROPIC_API_KEY=
#   GOOGLE_API_KEY=
#   REDIS_HOST=redis
#   (any additional secrets)
```

**Step 3 — Build images**
```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
# Wait ~5 seconds for postgres to initialise
sleep 5
docker compose logs postgres | tail -20
```

**Step 5 — Start application services**
```bash
docker compose up -d backend frontend
```

**Step 6 — Verify health**
```bash
docker compose ps
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

**Step 7 — Smoke test the agent**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
```

---

### Rollback Procedure

**Option A — Roll back to previous Docker image (if images are tagged)**
```bash
# [TODO: confirm whether images are pushed to a registry — no registry config found in repo]
docker compose down
# Edit docker-compose.yml to point to previous image tag
docker compose up -d
```

**Option B — Roll back via Git**
```bash
git log --oneline -10          # identify last good commit
git checkout <commit-sha>
docker compose build --no-cache
docker compose up -d
```

**Option C — Restart only the backend (config changes)**
```bash
docker compose restart backend
# Verify:
curl -f http://localhost:8000/health
```

**Data rollback:**
```bash
# Redis state is non-persistent by default — clears on container restart
# PostgreSQL: restore from backup if available
# [TODO: is there a backup/restore procedure for the postgres_data volume?]
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | What to Monitor | Alert Threshold |
|---|---|---|
| Backend health endpoint | `GET /health` response code and latency | Non-200 response |
| Assessment latency | Time logged as `[AGGREGATOR] time=Xs` in backend logs | > 60s |
| Specialist LLM token usage | `in=X tok out=Y tok` per specialist call in logs | Output tokens near 1500 (capped) |
| Redis memory | `docker compose exec redis redis-cli info memory` → `used_memory_human` | [TODO: define threshold] |
| PostgreSQL connections | `SELECT count(*) FROM pg_stat_activity;` | [TODO: define threshold] |
| Container restart count | `docker compose ps` → restart counter | > 3 restarts in 10 min |

### Log Locations and Key Patterns

```bash
# Backend application logs (structured print statements)
docker compose logs backend -f

# Key log patterns to watch:
# [CHAT]        — incoming request received
# [TOOL START]  — agent invoking a tool
# [TOOL END]    — tool completed with elapsed time
# [SPECIALIST]  — individual LLM assessment call with token usage
# [AGGREGATOR]  — final aggregation call with token usage
# [ASSESSMENT]  — full assessment lifecycle
# [DEBUG]       — JSON parse errors from LLM responses (indicates prompt issues)

# Redis logs
docker compose logs redis -f

# PostgreSQL logs
docker compose logs postgres -f

# Frontend logs
docker compose logs frontend -f
```

### GitHub Actions Workflow Monitoring

| Workflow | Schedule | What it alerts on |
|---|---|---|
| Tool 1 — Code Review | On every PR + Monday 08:00 UTC | Security findings, score < threshold, posts PR comment |
| Tool 2 — Tech Docs | On merge to main + Sunday 06:00 UTC | Doc generation failures |
| Tool 3 — Business Docs | On version tag push | Release documentation gaps |
| Tool 4 — Auto Testing | On PR open + Wednesday 07:00 UTC | Coverage gaps |
| Tool 5 — UAT | On `release/*` branch creation | Go/No-Go recommendation |

Monitor at: `https://github.com/kylodeng/underwriting_chatbot-main/actions`

### Recommended Alerting Setup
- [TODO: is there a Datadog / Grafana / CloudWatch / Azure Monitor integration planned?]
- [TODO: are SendGrid email alerts configured for backend failures or only for CI workflow results?]
- Minimum viable: set up an uptime monitor (e.g. UptimeRobot) on `http://<host>:8000/health`

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: name / PagerDuty rotation] | Service down > 5 min; `/health` failing |
| L2 | Backend / ML Lead | [TODO: name / Slack handle] | LLM assessment errors; model quality issues; Redis persistence failure |
| L3 | Platform / DevOps | [TODO: name / email] | Infrastructure failure; data volume corruption; Docker host issues |
| L4 | Anthropic Support | support.anthropic.com | API outage, rate limit increase, model deprecation |
| L4 | Google Cloud Support | [TODO: GCP project support link] | Gemini API outage |
| Product Owner | [TODO: name] | [TODO: email] | Go/No-Go decisions for releases; regulatory/compliance concerns |
| Security | [TODO: name] | [TODO: email] | Any hardcoded secrets found; IAM/auth bypass; data exposure |

> **Current known notification target:** `kylo.deng@capco.com` (set in all GitHub Actions workflows)

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop all services and DELETE volumes (⚠️ destroys postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend
docker compose restart frontend
docker compose restart redis

# View real-time logs for all services
docker compose logs -f

# View logs for a specific service (last 200 lines)
docker compose logs backend --tail=200
docker compose logs redis --tail=50
```

### Debugging

```bash
# Shell into the backend container
docker compose exec backend bash

# Shell into Redis and inspect keys
docker compose exec redis redis-cli
> KEYS *
> DBSIZE

# Flush all Redis state (clears all conversation memory ⚠️)
docker compose exec redis redis-cli FLUSHALL

# Check PostgreSQL sessions
docker compose exec postgres psql -U chainlit -d chainlit -c "SELECT pid, usename, application_name, state FROM pg_stat_activity;"

# Inspect SQLite databases (from host)
sqlite3 database/customer_profile.db ".tables"
sqlite3 database/customer_profile.db "SELECT COUNT(*) FROM customer_profile;"   # [TODO: verify table name]

# Check environment variables in running backend container
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS"
```

### Testing the API

```bash
# Health check
curl -f http://localhost:8000/health

# Chat request — fast mode
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get me a profile for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Chat request — deep assessment mode
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run a full risk assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-002",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### Configuration

```bash
# Edit LLM config (token limits, temperature, default model)
vi backend/config.yml
# Then restart backend to apply:
docker compose restart backend

# Check current config inside container
docker compose exec backend cat /app/config.yml   # [TODO: verify WORKDIR in backend Dockerfile]
```

### GitHub Actions — Manual Triggers

```bash
# Trigger code review on the repo (requires gh CLI)
gh workflow run "Tool 1 — Code Review" \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=repo

# Trigger documentation regeneration
gh workflow run "Tool 2 — Tech Documentation" \
  --repo kylodeng/underwriting_chatbot-main

# Trigger UAT pack generation
gh workflow run "Tool 5 — UAT Facilitation" \
  --repo kylodeng/underwriting_chatbot-main \
  -f uat_mode=generate \
  -f release_version=1.0.0
```

### Image Rebuild

```bash
# Rebuild only the backend image (e.g. after code change)
docker compose build backend
docker compose up -d --no-deps backend

# Rebuild only the frontend
docker compose build frontend
docker compose up -d --no-deps frontend
```

---

> **Known TODOs from codebase:**
> - Redis persistence across backend restarts — migrate to external Redis (noted in `graph.py`)
> - `azure`