# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The system is composed of four containerised services: a **FastAPI backend** that orchestrates LangGraph-based AI agents (backed by Anthropic Claude and Google Gemini LLMs), a **frontend** chat UI, a **Redis** instance (redis-stack) used for LangGraph agent checkpoint/conversation memory, and a **PostgreSQL** database used by the frontend (Chainlit-compatible schema). The backend agents invoke specialist LLM sub-chains to perform multi-dimensional underwriting assessments (finance, health, life, etc.) against customer profile data stored in SQLite databases, producing structured `UnderwritingReport` outputs. A suite of five GitHub Actions workflows provides AI-generated code review, technical documentation, business documentation, automated test generation, and UAT facilitation for the repository itself.

---

## 2. Health Checks

### 2.1 Backend API

```bash
curl -sf http://localhost:8000/health
# Expected: {"status":"ok"}  HTTP 200
```

### 2.2 Docker Service Status

```bash
docker compose ps
# All four services (redis, postgres, backend, frontend) should show status: running (healthy)
```

### 2.3 Redis Connectivity

```bash
docker compose exec redis redis-cli PING
# Expected: PONG
```

### 2.4 PostgreSQL Connectivity

```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 2.5 Backend → Redis Reachability

```bash
docker compose exec backend python -c "import redis; r = redis.Redis(host='redis', port=6379); print(r.ping())"
# Expected: True
```

### 2.6 Frontend Reachability

```bash
curl -sf http://localhost:8080
# Expected: HTTP 200 with HTML body
```

### 2.7 SQLite Database Files (Read-Only Mounts)

```bash
docker compose exec backend ls -lh /data/
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

### 2.8 LLM API Keys

```bash
docker compose exec backend python -c "import os; print('ANTHROPIC_API_KEY set:', bool(os.environ.get('ANTHROPIC_API_KEY'))); print('GOOGLE_API_KEY set:', bool(os.environ.get('GOOGLE_API_KEY')))"
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container exits immediately or fails health check | Missing or invalid environment variables (e.g. `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) | 1. Check `docker compose logs backend`. 2. Verify `.env` file exists at project root with all required keys. 3. Restart: `docker compose up -d backend`. |
| `GET /health` returns HTTP 500 or connection refused | Backend process crashed (unhandled exception at startup) | 1. `docker compose logs --tail=100 backend`. 2. Check for import errors or missing `.env`. 3. `docker compose restart backend`. |
| Chat returns `"Unsupported or unconfigured model provider"` | `model` field in request maps to a provider with `None` in `LLMS.model_mapper` (e.g. `azure`, `openai`) | 1. Use a supported model: `anthropic`, `anthropic-fast`, or `gemini`. 2. [TODO: Are Azure/OpenAI providers planned? Add keys and model instances to `LLMS.model_mapper` when ready.] |
| LangGraph agent fails with Redis connection error | Redis container not started, or `REDIS_HOST` env var wrong | 1. `docker compose up -d redis`. 2. Confirm `REDIS_HOST=redis` is set in backend environment. 3. Check `docker compose logs redis`. |
| Agent produces no response / hangs indefinitely | LLM API rate limit, network timeout, or semaphore deadlock in `_run_underwriting_assessment` | 1. Check Anthropic/Google API status pages. 2. `docker compose logs backend` for timeout errors. 3. Restart backend: `docker compose restart backend`. 4. Reduce concurrency by lowering the semaphore value in `assessment.py` (`asyncio.Semaphore(4)`). |
| `UnderwritingReport` JSON parse failure / aggregator error | Aggregator LLM exceeded `aggregator_max_tokens` (8000) or returned malformed JSON | 1. Check logs for `[AGGREGATOR]` output token count. 2. Increase `aggregator_max_tokens` in `config.yml` and rebuild. 3. Switch `default` model to `anthropic` (Sonnet) instead of `anthropic-fast` (Haiku). |
| Frontend shows blank page or cannot reach backend | `BACKEND_URL` env var misconfigured, or backend health check failing | 1. Confirm `BACKEND_URL=http://backend:8000` in frontend environment. 2. Confirm backend passes health check: `docker compose ps`. 3. `docker compose logs frontend`. |
| PostgreSQL init fails | `./postgres/init.sql` missing or malformed, or `postgres_data` volume corrupted | 1. `docker compose logs postgres`. 2. Remove volume and reinitialise: `docker compose down -v && docker compose up -d postgres`. **Warning: destroys all Postgres data.** |
| Customer profile not found during assessment | SQLite `.db` files not mounted, missing, or empty | 1. Confirm files exist on host: `ls -lh ./database/`. 2. Check volume mount in `docker-compose.yml`. 3. `docker compose exec backend sqlite3 /data/customer_profile.db ".tables"`. |
| GitHub Actions workflow fails — `ANTHROPIC_API_KEY` not set | Repository secret missing or expired | 1. Go to repo → Settings → Secrets and Variables → Actions. 2. Verify `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are set. 3. Re-run failed workflow. |
| Conversation history lost between sessions | Redis data was flushed or container restarted without persistence | 1. Verify Redis is running: `docker compose ps redis`. 2. Note: the current Redis setup has **no AOF/RDB persistence** configured — all state is lost on container restart. [TODO: Add persistence config or migrate to an external Redis service as noted in `graph.py`.] |
| Specialist LLM output truncated | `specialist_max_tokens: 1500` cap reached | 1. Increase `specialist_max_tokens` in `config.yml`. 2. Rebuild backend: `docker compose up -d --build backend`. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker and Docker Compose v2+ installed
- `.env` file at project root populated with all required secrets (see Section 5)
- SQLite database files present in `./database/`
- `./postgres/init.sql` present

### 4.1 First-Time Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create and populate the .env file
cp .env.example .env          # [TODO: confirm .env.example exists or document required vars]
# Edit .env — minimum required keys:
#   ANTHROPIC_API_KEY=<key>
#   GOOGLE_API_KEY=<key>
#   REDIS_HOST=redis           # set automatically via docker-compose.yml

# 3. Build and start all services
docker compose up -d --build

# 4. Verify all services are healthy
docker compose ps
curl -sf http://localhost:8000/health

# 5. Check logs for startup errors
docker compose logs --tail=50 backend
```

### 4.2 Deploying an Update (Rolling Update)

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild only changed service images
docker compose build backend frontend

# 3. Restart services with zero-downtime approach
#    (Redis and Postgres do not need to restart for code-only changes)
docker compose up -d --no-deps backend
sleep 15   # allow start_period from healthcheck
docker compose up -d --no-deps frontend

# 4. Verify health
docker compose ps
curl -sf http://localhost:8000/health

# 5. Tail logs to confirm no errors
docker compose logs -f --tail=100 backend
```

### 4.3 Config-Only Change (e.g. `config.yml`, `prompts/`)

```bash
# Config files are baked into the image — rebuild required
docker compose build backend
docker compose up -d --no-deps backend
curl -sf http://localhost:8000/health
```

### 4.4 Rollback Steps

```bash
# Option A: Roll back to previous Docker image tag (if images are tagged in CI)
# [TODO: Define image tagging strategy and registry location]
docker compose stop backend
docker tag <registry>/underwriting-backend:<previous-tag> underwriting-backend:latest
docker compose up -d --no-deps backend

# Option B: Roll back via git and rebuild
git log --oneline -10           # identify last good commit
git checkout <commit-sha>
docker compose build backend frontend
docker compose up -d --no-deps backend frontend

# Option C: Full teardown and redeploy (last resort — WARNING: Postgres volume survives, Redis data lost)
docker compose down
git checkout <commit-sha>
docker compose up -d --build
```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | What to Watch | Warning Threshold |
|---|---|---|
| Backend container health | `docker compose ps` health status | Any status other than `(healthy)` |
| LLM response latency | `[SPECIALIST]` and `[AGGREGATOR]` log lines show per-call time | Specialist > 30s; Aggregator > 60s |
| LLM token consumption | `in=` and `out=` values in `[SPECIALIST]` / `[AGGREGATOR]` log lines | `out` approaching `specialist_max_tokens` (1500) or `aggregator_max_tokens` (8000) |
| Redis memory | `docker compose exec redis redis-cli INFO memory` → `used_memory_human` | [TODO: Set threshold based on expected session volume] |
| Postgres disk usage | `docker system df` → postgres_data volume | [TODO: Set threshold] |
| API error rate | HTTP 5xx responses in backend logs | Any sustained 5xx |
| GitHub Actions success rate | Actions tab in GitHub | Any workflow failure on `main` branch push |

### 5.2 Log Locations & Key Log Patterns

```bash
# Backend application logs (most useful)
docker compose logs -f backend

# Key log prefixes to monitor:
# [CHAT]          — incoming request details
# [TOOL START]    — agent tool invocations
# [TOOL END]      — tool completion + elapsed time
# [SPECIALIST]    — per-category LLM call stats
# [AGGREGATOR]    — final report generation stats
# [ASSESSMENT]    — assessment start with category count

# Redis logs
docker compose logs -f redis

# Postgres logs
docker compose logs -f postgres
```

### 5.3 GitHub Actions Workflow Health

Monitor the following workflows in the Actions tab:

| Workflow | Trigger | What to watch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Mon 08:00 UTC, manual | Claude JSON parse errors, SendGrid failures |
| Tool 2 — Tech Documentation | Push to `main`, Sun 06:00 UTC | File write failures to output repo |
| Tool 3 — Business Documentation | Version tags `v*`, manual | Missing `---GAPS---` delimiter in Claude response |
| Tool 4 — Auto Testing | PR open/sync on `.py`/`.js`/`.ts`, Wed 07:00 UTC | Coverage gap analysis JSON parse errors |
| Tool 5 — UAT Facilitation | `release/*` branch creation, manual | CSV parse failures in analyse mode |

### 5.4 Alerting

> [TODO: No alerting infrastructure is currently configured in the codebase (no Prometheus, Datadog, CloudWatch, or PagerDuty integration found). Recommend instrumenting the FastAPI backend with Prometheus metrics middleware and setting up alerts on: 5xx rate, LLM latency p99, container health check failures.]

SendGrid email notifications are sent by the GitHub Actions workflows to `kylo.deng@capco.com` — verify `SENDGRID_API_KEY` is valid to ensure delivery.

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call contact] | Service down > 5 min, health check failing |
| L2 | Backend / ML Lead | [TODO: fill in contact] | LLM failures, assessment accuracy issues, token budget issues |
| L3 | Platform / Infra Lead | [TODO: fill in contact] | Redis data loss, Postgres corruption, Docker host failure |
| L4 | Anthropic Support | [TODO: Anthropic enterprise support URL] | Sustained Anthropic API outage |
| L4 | Google Cloud Support | [TODO: GCP support contact] | Sustained Gemini API outage |
| Business | Solution Owner | [TODO: fill in contact] | Data breach, compliance incident, go/no-go decision |

> **Incident Tracking:** [TODO: Define incident tracking tool — Jira, PagerDuty, or similar.]

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (data preserved)
docker compose down

# Stop and remove all volumes (DESTRUCTIVE — wipes Postgres and Redis data)
docker compose down -v

# Rebuild a single service
docker compose build backend
docker compose up -d --no-deps --build backend

# Restart a single service
docker compose restart backend

# View real-time logs for all services
docker compose logs -f

# View logs for one service, last 200 lines
docker compose logs --tail=200 backend
```

### Health & Diagnostics

```bash
# Check all container statuses
docker compose ps

# Backend health endpoint
curl -sf http://localhost:8000/health | python3 -m json.tool

# Test a chat request end-to-end
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'

# Redis ping
docker compose exec redis redis-cli PING

# Redis memory info
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Redis — list all keys (use with care in production)
docker compose exec redis redis-cli KEYS '*'

# Postgres connectivity check
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Postgres — list tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# SQLite — check customer profiles
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT COUNT(*) FROM customer_profile;"
```

### Configuration

```bash
# View current config
docker compose exec backend cat config.yml

# Edit config and apply (rebuild required)
vi backend/config.yml
docker compose build backend && docker compose up -d --no-deps backend

# Check environment variables loaded in backend
docker compose exec backend env | grep -E 'ANTHROPIC|GOOGLE|REDIS|BACKEND'
```

### GitHub Actions (manual triggers via GitHub CLI)

```bash
# Install GitHub CLI if not present: https://cli.github.com/

# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger tech documentation generation
gh workflow run tool2_tech_docs.yml

# Trigger business documentation for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Trigger test generation
gh workflow run tool