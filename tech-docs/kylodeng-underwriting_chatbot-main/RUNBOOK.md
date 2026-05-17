# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that helps underwriters assess customer risk profiles through a conversational interface. The system comprises a **FastAPI backend** that orchestrates a LangGraph-based AI agent (powered by Anthropic Claude and optionally Google Gemini), a **Chainlit frontend** served on port 8080, and supporting infrastructure including **Redis** (for LangGraph conversation state checkpointing) and **PostgreSQL** (for Chainlit session/user data). On receiving a chat message, the agent selects from three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — running parallel specialist LLM calls across assessment categories (finance, health, life, etc.) before aggregating results into a structured `UnderwritingReport` with a risk classification of Preferred, Standard Plus, Standard, or Substandard. Outputs are streamed to the frontend via Server-Sent Events (SSE).

---

## 2. Health Checks

### Backend API

```bash
# Should return {"status": "ok"}
curl -f http://localhost:8000/health
```

### Docker Compose service status

```bash
docker compose ps
# All services should show "running (healthy)" for backend; "running" for others
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

### Frontend availability

```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### End-to-end smoke test

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
# Expected: SSE stream with at least one "response" event
```

### SQLite database files present (read-only mounts)

```bash
ls -lh database/customer_profile.db database/feature_importance.db \
        database/model_predictions.db database/application_profile.db
# All four files must exist and be non-zero
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container exits immediately or stays unhealthy | Missing or invalid `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` in `.env` | 1. Check `docker compose logs backend`. 2. Verify `.env` contains all required keys. 3. `docker compose up -d backend` |
| `/chat` returns `500` or SSE stream is empty | LLM API key expired or rate-limited | 1. Check `docker compose logs backend` for `AuthenticationError` or `429`. 2. Rotate key in `.env`. 3. Restart backend: `docker compose restart backend` |
| `redis` container not reachable; agent throws checkpoint error | Redis not started or `REDIS_HOST` env var wrong | 1. `docker compose ps redis` — confirm running. 2. Confirm backend env has `REDIS_HOST=redis`. 3. `docker compose restart redis backend` |
| Conversation state lost between messages / session resets | Redis container restarted without persistent volume | 1. Note: Redis is currently **in-memory only** (no volume mounted). 2. [TODO: Is Redis persistence (AOF/RDB) required for production?] 3. Add a named volume for Redis data in `docker-compose.yml` as a workaround. |
| Frontend shows blank page or cannot connect to backend | `BACKEND_URL` env var wrong, or backend unhealthy | 1. Confirm `BACKEND_URL=http://backend:8000` in frontend service. 2. `curl -f http://backend:8000/health` from within frontend container. 3. Check backend health check passes: `docker compose ps`. |
| PostgreSQL init fails; frontend errors on login/session | `init.sql` missing or schema mismatch | 1. `docker compose logs postgres`. 2. Confirm `./postgres/init.sql` exists. 3. `docker compose down -v && docker compose up -d` to reinitialise (⚠ destroys data). |
| `get_customer_profile` tool returns empty or errors | SQLite DB files not mounted or path wrong | 1. Check volume mounts in `docker-compose.yml`. 2. Confirm DB files exist: `ls -lh database/`. 3. `docker compose exec backend ls /data/` to verify mount. |
| `run_underwriting_assessment` times out | LLM provider latency spike; semaphore (4 concurrent calls) overwhelmed | 1. Check backend logs for `[SPECIALIST]` timing lines. 2. Reduce `specialist_max_tokens` in `config.yml` temporarily. 3. Switch `default` model to `anthropic-fast` in `config.yml`. |
| Model `anthropic-fast` or `anthropic` returns `ValueError: Unsupported or unconfigured model provider` | Model name in request doesn't match `LLMS.model_mapper` keys | 1. Valid values: `gemini`, `anthropic`, `anthropic-fast`. 2. Check `backend/modules/LLMS.py`. 3. Correct the `model` field in the frontend or API call. |
| GitHub Actions workflow fails (`ANTHROPIC_API_KEY` not found) | Secret not set in repository settings | 1. Go to repo → Settings → Secrets and variables → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| `customer_similarity_dict.json` lookup returns no results | File missing from `backend/tmp/` | 1. Confirm `backend/tmp/customer_similarity_dict.json` exists. 2. [TODO: How is this file regenerated if missing? Is there a training pipeline?] |

---

## 4. Deployment Procedure

### Prerequisites

- Docker Engine ≥ 24 and Docker Compose v2
- `.env` file at repo root with all required secrets (see Section 5)
- SQLite database files present in `./database/`

### Step-by-step deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file from template
cp .env.example .env          # [TODO: Does an .env.example exist?]
# Edit .env and populate all required variables (see Section 5)

# 3. Build all images
docker compose build

# 4. Start infrastructure services first
docker compose up -d redis postgres

# 5. Wait for postgres to be ready
docker compose exec postgres pg_isready -U chainlit -d chainlit

# 6. Start backend and wait for health check to pass
docker compose up -d backend
docker compose ps   # wait until backend shows "(healthy)"

# 7. Start frontend
docker compose up -d frontend

# 8. Verify all services
docker compose ps
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

### Updating to a new version

```bash
# 1. Pull latest changes
git pull origin main

# 2. Rebuild changed images (--no-cache if dependency changes)
docker compose build backend frontend

# 3. Rolling restart (keeps redis/postgres running)
docker compose up -d --no-deps backend frontend

# 4. Confirm health
docker compose ps
curl -f http://localhost:8000/health
```

### Rollback steps

```bash
# 1. Identify the previous image tag or commit
git log --oneline -5

# 2. Check out the previous commit
git checkout <previous-commit-sha>

# 3. Rebuild from the previous commit
docker compose build backend frontend

# 4. Restart services
docker compose up -d --no-deps backend frontend

# 5. Verify health
curl -f http://localhost:8000/health
```

> ⚠ **Note:** There is no image registry or versioned image tagging observed in this repo. [TODO: Is there a container registry (e.g. Docker Hub, ACR, ECR) where images are pushed before deployment?]

---

## 5. Monitoring & Alerting

### Key metrics to watch

| Metric | What to monitor | Threshold / Action |
|---|---|---|
| Backend health endpoint | `GET /health` → `{"status":"ok"}` | Alert if non-200 for >30s |
| Container restart count | `docker compose ps` restart count | Alert if any service restarts >3 times in 10 min |
| LLM API latency | `[SPECIALIST]` and `[AGGREGATOR]` log lines (printed to stdout) | Alert if assessment takes >30s end-to-end |
| LLM token usage | `in=` / `out=` token counts in backend logs | Alert if aggregator output tokens approach 8000 (`aggregator_max_tokens`) |
| Redis memory | `docker compose exec redis redis-cli info memory` → `used_memory_human` | Alert if >80% of available RAM |
| PostgreSQL connections | `SELECT count(*) FROM pg_stat_activity;` | Alert if approaching max connections |
| SSE stream errors | `[CHAT]` log lines with exceptions | Any exception in chat handler |

### Log locations

```bash
# Backend application logs (includes [CHAT], [TOOL START/END], [SPECIALIST], [AGGREGATOR])
docker compose logs -f backend

# Frontend logs
docker compose logs -f frontend

# Redis logs
docker compose logs -f redis

# PostgreSQL logs
docker compose logs -f postgres
```

### Important log patterns

```
# Successful assessment cycle
[CHAT] session=... model=anthropic-fast mode=fast msg=...
[TOOL START] get_customer_profile
[TOOL END]   get_customer_profile  time=x.xxs
[TOOL START] run_underwriting_assessment
[ASSESSMENT] Starting — N specialist calls (mode='fast')
[SPECIALIST] category=... in=XXXX tok  out=XXXX tok  time=x.xxs
[AGGREGATOR]  in=XXXX tok  out=XXXX tok  time=x.xxs
[TOOL END]   run_underwriting_assessment  time=x.xxs

# Error patterns to alert on
AuthenticationError        # Invalid API key
RateLimitError             # LLM provider throttling
ConnectionRefusedError     # Redis or Postgres unreachable
ValueError: Unsupported    # Invalid model name
```

### GitHub Actions workflow monitoring

| Workflow | Schedule | What to check |
|---|---|---|
| Tool 1 — Code Review | On PRs + every Monday 08:00 UTC | Fails = Claude API or GH token issue |
| Tool 2 — Tech Docs | On merge to main + every Sunday 06:00 UTC | Fails = output repo write permission |
| Tool 4 — Auto Testing | On PRs to `src/**`, `*.py`, `*.js`, `*.ts` + every Wednesday 07:00 UTC | Fails = Claude API issue |

[TODO: Is there a centralised monitoring platform (Datadog, Grafana, Azure Monitor, etc.) where these metrics should be shipped?]

[TODO: Are there existing PagerDuty/OpsGenie/Slack alert channels for this service?]

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in contact] | Service down, health check failing, all containers unhealthy |
| L2 | Backend/ML engineer | [TODO: fill in contact] | LLM assessment errors, model accuracy concerns, Redis checkpoint issues |
| L3 | Tech lead / architect | [TODO: fill in contact] | Data breach, SQLite DB corruption, LLM API contract/billing issue |
| Vendor | Anthropic support | https://support.anthropic.com | Persistent API errors, rate limit increases needed |
| Vendor | Google Cloud support | [TODO: fill in contact] | Gemini API outages |

> Contact for this repo: `kylo.deng@capco.com` (inferred from workflow configurations)

---

## 7. Useful Commands

### Service management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop and wipe all data (⚠ destructive)
docker compose down -v

# Restart a single service
docker compose restart backend

# View real-time logs for all services
docker compose logs -f

# View logs for a specific service
docker compose logs -f backend
```

### Health and diagnostics

```bash
# Check all container statuses
docker compose ps

# Backend health check
curl -f http://localhost:8000/health

# Redis ping
docker compose exec redis redis-cli ping

# Redis memory stats
docker compose exec redis redis-cli info memory

# PostgreSQL health
docker compose exec postgres pg_isready -U chainlit -d chainlit

# List active PostgreSQL sessions
docker compose exec postgres psql -U chainlit -c "SELECT pid, usename, application_name, state FROM pg_stat_activity;"

# Verify SQLite DB mounts inside backend container
docker compose exec backend ls -lh /data/
```

### LLM configuration

```bash
# View current LLM config
cat backend/config.yml

# Temporarily switch to faster model (edit in place)
# Change 'default: anthropic' to 'default: anthropic-fast' in config.yml
# Then restart backend:
docker compose restart backend
```

### Manual API test

```bash
# Test chat endpoint with anthropic-fast model
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get the profile for customer CUST00000001",
    "temperature": 0.3,
    "session_id": "test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Test with deep assessment mode
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run a full risk assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-002",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### GitHub Actions — manual workflow triggers

```bash
# Trigger code review on a specific PR (requires GitHub CLI)
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger tech docs regeneration
gh workflow run tool2_tech_docs.yml

# Trigger UAT test pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0
```

### Environment variable reference

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Anthropic Claude API key |
| `GOOGLE_API_KEY` | ⚠ Required for Gemini | Google Generative AI API key |
| `REDIS_HOST` | ✅ | Redis hostname (default: `redis` in Docker network) |
| `GH_TOKEN` | ✅ (CI only) | GitHub PAT for workflow scripts |
| `SENDGRID_API_KEY` | ✅ (CI only) | SendGrid key for email notifications |
| `OUTPUT_REPO` | CI only | Target repo for AI tool outputs (default: `ai-delivery-outputs`) |
| `NOTIFY_EMAIL` | CI only | Email for AI tool notifications (default: `kylo.deng@capco.com`) |

[TODO: Are `DATABASE_URL` and the SQLite DB file paths configurable via env vars, or are they hardcoded mount paths?]

[TODO: What is the production deployment target — bare VM, Kubernetes, Azure App Service, or other?]