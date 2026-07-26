# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-powered decision-support tool for insurance underwriters. It exposes a **FastAPI backend** (port 8000) backed by **Redis** (LangGraph conversation checkpointing), **PostgreSQL** (Chainlit session persistence), and **three SQLite databases** (customer profiles, model predictions, feature importance). A **frontend** (port 8080) [TODO: what framework — Chainlit, React, or other?] communicates with the backend via Server-Sent Events (SSE) streaming. When an underwriter asks a question the backend invokes a LangGraph agent that orchestrates up to three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — against specialist and aggregator LLMs (Anthropic Claude Haiku / Sonnet, Google Gemini). Assessment results are structured against a `CatBoostClassifier` model card (v1.0, deployed 2024-06-01) and returned as a typed `UnderwritingReport`. Five GitHub Actions workflows (code review, tech docs, business docs, auto-testing, UAT) provide CI/CD automation using the same Claude API.

---

## 2. Health Checks

### 2.1 Backend API

```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### 2.2 Docker container status

```bash
docker compose ps
# All four services (redis, postgres, backend, frontend) should show "running" / "healthy"
```

### 2.3 Redis connectivity

```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### 2.4 PostgreSQL connectivity

```bash
docker compose exec postgres psql -U chainlit -d chainlit -c "SELECT 1;"
# Expected: ?column? = 1
```

### 2.5 Frontend reachability

```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### 2.6 SQLite database files present

```bash
ls -lh ./database/*.db
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

### 2.7 LLM API keys valid

```bash
# Anthropic
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}'
# Expected: HTTP 200 with content block

# Google (Gemini)
curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY" | head -c 100
# Expected: JSON model list
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `curl /health` returns connection refused | Backend container not running or crashed on startup | `docker compose logs backend --tail 50`; check for missing env vars in `.env`; `docker compose up -d backend` |
| Backend starts then crashes with `KeyError: 'ANTHROPIC_API_KEY'` | Missing required environment variable | Verify `.env` file exists at repo root and contains `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`; re-run `docker compose up` |
| Chat returns no streaming tokens / hangs indefinitely | LLM API unreachable or rate-limited | Check API key validity; inspect `docker compose logs backend` for HTTP 429 or 5xx from Anthropic/Google; implement retry or switch model in `config.yml` |
| `ValueError: Unsupported or unconfigured model provider: <name>` | `model` field in chat request does not match keys in `LLMS.model_mapper` | Accepted values: `gemini`, `anthropic`, `anthropic-fast`; correct request payload or `config.yml` `llm.default` |
| Redis connection refused at startup | Redis container not running; wrong `REDIS_HOST` | `docker compose up -d redis`; confirm `REDIS_HOST=redis` env var is set for backend; `docker compose exec redis redis-cli ping` |
| LangGraph checkpointer error / conversation state lost | Redis restarted without persistence; ephemeral volume | See TODO note in `graph.py` re: migrating to external Redis (Azure Cache for Redis); for now restart backend to clear in-flight state |
| PostgreSQL `FATAL: password authentication failed` | Wrong credentials in frontend `DATABASE_URL` or postgres init | Verify `DATABASE_URL=postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit`; `docker compose down -v && docker compose up -d` to reinitialise volume |
| `FileNotFoundError: config.yml` or `assessment_criterias.json` | Build context missing files; wrong working directory | Ensure `backend/config.yml` and `backend/prompts/assessment_criterias.json` exist; rebuild image: `docker compose build backend` |
| Frontend shows blank page / cannot reach backend | `BACKEND_URL` misconfigured or CORS error | Confirm `BACKEND_URL=http://backend:8000` in frontend env; backend CORS currently allows `*` so this would be a network issue — check Docker network: `docker network ls` |
| Assessment returns empty / truncated report | `aggregator_max_tokens` too low for full `UnderwritingReport` JSON | Increase `aggregator_max_tokens` in `backend/config.yml` (currently 8000); redeploy backend |
| Specialist LLM hits token cap mid-response | `specialist_max_tokens: 1500` too restrictive for category | Increase `specialist_max_tokens` in `backend/config.yml`; monitor cost impact |
| GitHub Action fails with `ModuleNotFoundError: anthropic` | Workflow `pip install` step incomplete | Check workflow YAML `pip install anthropic requests`; confirm Python 3.12 used |
| `customer_similarity_dict.json` lookup KeyError | Customer ID not present in precomputed lookalike dictionary | Verify customer ID format (`CUST########`); regenerate `backend/tmp/customer_similarity_dict.json` [TODO: what script regenerates this file?] |
| `database/*.db` not found inside container | Volume mount path mismatch | Confirm `docker-compose.yml` volume paths match actual file locations; `ls ./database/` on host |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose v2 installed
- `.env` file at repo root with all required variables (see §5)
- SQLite database files present under `./database/`

### 4.1 First-time deployment

```bash
# 1. Clone repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create environment file
cp .env.example .env          # [TODO: confirm .env.example exists or document required vars]
# Edit .env with real credentials

# 3. Verify database files are present
ls ./database/*.db

# 4. Build and start all services
docker compose up -d --build

# 5. Wait for backend health check to pass (up to 15s start_period + 5 retries)
docker compose ps
# All services should show "healthy" or "running"

# 6. Smoke test
curl http://localhost:8000/health
curl http://localhost:8080
```

### 4.2 Routine update deployment

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild changed images (--no-deps prevents unnecessary service restarts)
docker compose build backend frontend

# 3. Rolling restart (redis and postgres volumes are preserved)
docker compose up -d --no-deps backend frontend

# 4. Verify health
docker compose ps
curl -f http://localhost:8000/health

# 5. Tail logs for 60s to confirm no crash loops
docker compose logs -f backend --since 60s
```

### 4.3 Config-only change (e.g. `config.yml` token limits)

```bash
# config.yml is baked into the image at build time
docker compose build backend
docker compose up -d --no-deps backend
curl -f http://localhost:8000/health
```

### 4.4 Rollback procedure

```bash
# Option A — revert to previous image if tagged
docker compose stop backend frontend
docker tag backend:previous backend:latest   # [TODO: confirm image tagging strategy]
docker compose up -d --no-deps backend frontend

# Option B — revert via git and rebuild
git log --oneline -5           # identify last good commit
git checkout <commit-sha>
docker compose build backend frontend
docker compose up -d --no-deps backend frontend
curl -f http://localhost:8000/health

# Option C — full teardown (WARNING: destroys postgres volume / conversation history)
docker compose down -v
git checkout <commit-sha>
docker compose up -d --build
```

> ⚠️ **Redis state**: LangGraph checkpoint state lives in Redis memory only (see `graph.py` TODO). A Redis restart will clear all in-progress conversation threads. This is expected in the current architecture.

---

## 5. Monitoring & Alerting

### 5.1 Key metrics to watch

| Metric | Source | Warning Threshold | Notes |
|---|---|---|---|
| Backend container health | Docker healthcheck (`/health`) | Any non-`healthy` state | Retries every 10s, 5 attempts |
| LLM specialist latency | `backend` stdout `[SPECIALIST]` log lines | > 30s per category | 4 concurrent via `asyncio.Semaphore(4)` |
| LLM aggregator latency | `backend` stdout `[AGGREGATOR]` log lines | > 60s | Large token budget (8000) |
| LLM input/output tokens | `[SPECIALIST]` / `[AGGREGATOR]` log lines | [TODO: set budget alert threshold] | Logged per call |
| Redis memory | `redis-cli info memory` → `used_memory_human` | [TODO: set threshold] | No persistence configured |
| Postgres disk | `df -h` on host for `postgres_data` volume | > 80% | Chainlit session data |
| API error rate | `docker compose logs backend` for HTTP 5xx or `ValueError` | Any `CRITICAL` errors | No external APM configured |
| Chat request throughput | Application logs `[CHAT]` prefix | [TODO: baseline to be established] | Logged per request with session/model/mode |

### 5.2 Logs

```bash
# All services
docker compose logs -f

# Backend only (most relevant)
docker compose logs -f backend

# Filter for errors
docker compose logs backend 2>&1 | grep -E "ERROR|Exception|Traceback|CRITICAL"

# Filter LLM performance lines
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL"

# Filter chat requests
docker compose logs backend 2>&1 | grep "\[CHAT\]"
```

### 5.3 Alerting

[TODO: No alerting infrastructure (PagerDuty, Datadog, Azure Monitor, etc.) is configured in this codebase. Recommend adding: (1) container health → email/Slack alert, (2) Anthropic API 429 rate-limit detection, (3) Redis memory pressure alert.]

### 5.4 GitHub Actions workflows

| Workflow | Trigger | What to watch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Mon 08:00 UTC | Fails if `ANTHROPIC_API_KEY` or `GH_TOKEN` secrets missing |
| Tool 2 — Tech Docs | Push to `main`, Sun 06:00 UTC | Check `ai-delivery-outputs` repo for updated docs |
| Tool 3 — Business Docs | Version tag push, manual | Requires `SENDGRID_API_KEY` for email delivery |
| Tool 4 — Auto Testing | PR on `src/**`, Wed 07:00 UTC | Review generated test files in output repo |
| Tool 5 — UAT | `release/*` branch creation, manual | Check CSV output and defect report |

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in on-call rotation/contact] | Service down, health check failing |
| L2 | Backend / ML engineer | [TODO: fill in team contact] | LLM errors, assessment logic failures, model card issues |
| L3 | Platform / DevOps | [TODO: fill in contact] | Infrastructure, Docker, Redis/Postgres issues |
| L4 | Anthropic support | [TODO: enterprise support URL] | Sustained API outages, billing/rate-limit issues |
| L4 | Google Cloud support | [TODO: GCP support link] | Gemini API outages |
| Product owner | [TODO: fill in name] | [TODO: fill in contact] | Go/no-go decisions, scope changes |

Notify: `kylo.deng@capco.com` for automated workflow failures (configured in all five GitHub Actions workflows).

---

## 7. Useful Commands

### Docker operations

```bash
# Start all services
docker compose up -d

# Start with rebuild
docker compose up -d --build

# Stop all services (preserves volumes)
docker compose down

# Full teardown including volumes (DATA LOSS — postgres history cleared)
docker compose down -v

# View running containers and health
docker compose ps

# Restart a single service
docker compose restart backend

# Follow logs for all services
docker compose logs -f

# Follow backend logs only
docker compose logs -f backend --tail 100
```

### Health & debugging

```bash
# Backend health check
curl -f http://localhost:8000/health && echo "OK" || echo "FAIL"

# Redis ping
docker compose exec redis redis-cli ping

# Redis memory usage
docker compose exec redis redis-cli info memory | grep used_memory_human

# List all Redis keys (conversation threads)
docker compose exec redis redis-cli keys "*"

# Flush all Redis state (clears conversation memory — use with caution)
docker compose exec redis redis-cli flushall

# PostgreSQL — connect and list tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# Check SQLite databases are accessible
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/model_predictions.db ".tables"
```

### Environment & configuration

```bash
# Print resolved environment variables inside backend container
docker compose exec backend env | grep -E "REDIS|ANTHROPIC|GOOGLE|POSTGRES"

# Validate config.yml syntax
docker compose exec backend python -c "import yaml; yaml.safe_load(open('config.yml'))"

# Validate assessment_criterias.json
docker compose exec backend python -c "import json; json.load(open('prompts/assessment_criterias.json')); print('OK')"
```

### Testing a chat request manually

```bash
# Send a test chat message (adjust session_id as needed)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Can you assess customer CUST00000001?",
    "temperature": 0.3,
    "session_id": "test-session-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### Image management

```bash
# List built images
docker images | grep -E "backend|frontend"

# Tag current backend image before upgrade (manual rollback prep)
docker tag underwriting_chatbot-main-backend:latest underwriting_chatbot-main-backend:previous

# View image build history
docker history underwriting_chatbot-main-backend:latest
```

### GitHub Actions (via GitHub CLI)

```bash
# Trigger code review manually on a PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger tech doc regeneration
gh workflow run tool2_tech_docs.yml

# View recent workflow runs
gh run list --workflow tool1_code_review.yml --limit 5

# View logs for a specific run
gh run view <run