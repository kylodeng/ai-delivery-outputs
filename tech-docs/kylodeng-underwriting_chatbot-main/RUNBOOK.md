# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is a FastAPI-based AI assistant designed to help insurance underwriters assess customer risk profiles. It exposes a streaming HTTP API (`/chat`) that drives a LangGraph agent backed by Anthropic Claude (claude-sonnet or claude-haiku) and, optionally, Google Gemini. When an underwriter submits a question, the agent orchestrates up to three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — before synthesising a structured `UnderwritingReport` that classifies risk into one of four bands (Preferred, Standard Plus, Standard, Substandard). Conversation memory is persisted to Redis via LangGraph's `AsyncRedisSaver`, and supporting reference data is served from three SQLite databases (customer profiles, feature importance scores, model predictions). A Chainlit-based frontend (port 8080) provides the browser UI, backed by a PostgreSQL database for Chainlit session storage. The full stack is containerised and orchestrated via Docker Compose.

---

## 2. Health Checks

### 2.1 Container-level

```bash
docker compose ps
# All four services (redis, postgres, backend, frontend) should show "running"
```

### 2.2 Backend API

```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

The Docker Compose `healthcheck` polls this endpoint every 10 s with a 5 s timeout (5 retries, 15 s start grace period). The frontend container will not start until this check passes.

### 2.3 Redis

```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### 2.4 PostgreSQL

```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 2.5 Frontend

```bash
curl -f http://localhost:8080
# Expected: HTTP 200 (Chainlit UI)
```

### 2.6 LLM Connectivity

```bash
# Confirm Anthropic API key is reachable (from inside the backend container)
docker compose exec backend python -c "
import os, anthropic
c = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
print(c.models.list())
"
```

### 2.7 GitHub Actions Workflows

Check the Actions tab of the repository for the status of the five automation tools (code review, tech docs, business docs, auto testing, UAT).

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `POST /chat` returns 500 immediately | `ANTHROPIC_API_KEY` missing or invalid | 1. Check `.env` file. 2. `docker compose exec backend env \| grep ANTHROPIC`. 3. Rotate key in Anthropic console and update secret. |
| `POST /chat` hangs indefinitely, no SSE events | Redis unavailable; LangGraph checkpointer cannot save state | 1. `docker compose ps redis`. 2. `docker compose restart redis`. 3. Check `REDIS_HOST` env var in backend container. |
| Frontend shows blank page or cannot connect | Backend not yet healthy; frontend started before `/health` passed | 1. `docker compose logs frontend`. 2. `docker compose restart frontend`. 3. Confirm backend healthcheck is green: `docker compose ps backend`. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request does not match a key in `LLMS.model_mapper` | 1. Valid values: `anthropic`, `anthropic-fast`, `gemini`. 2. Check request payload `model` field. 3. Confirm `GOOGLE_API_KEY` or `ANTHROPIC_API_KEY` env var is set for the chosen provider. |
| `run_underwriting_assessment` tool times out or returns partial data | Specialist LLM calls exceed token limits or Anthropic rate limits | 1. Check `config.yml` `specialist_max_tokens` (currently 1500) and `aggregator_max_tokens` (8000). 2. Review Anthropic usage dashboard for rate-limit errors. 3. Switch `default` in `config.yml` to `anthropic-fast` for lower latency. |
| `get_customer_profile` returns no data | SQLite database file not mounted | 1. `docker compose exec backend ls /data/`. Files `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` must be present. 2. Check volume mounts in `docker-compose.yml`. |
| Postgres connection refused (Chainlit sessions broken) | Postgres container unhealthy or `init.sql` migration failed | 1. `docker compose logs postgres`. 2. `docker compose exec postgres psql -U chainlit -c '\dt'`. 3. `docker compose down postgres && docker compose up -d postgres`. |
| GitHub Actions workflow fails: `ModuleNotFoundError` | Missing Python dependencies in CI (only `anthropic` and `requests` are installed) | 1. Check workflow pip install step. 2. Add missing package to the `pip install` line in the relevant `.yml` workflow file. |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY` or `GH_TOKEN` not found | Secret not configured in repository settings | 1. Go to **Settings → Secrets and variables → Actions**. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| Agent repeats tool calls in a loop | Conversation history not being threaded correctly; `thread_id` not passed | 1. Confirm `session_id` is consistent across turns from the frontend. 2. Check Redis for stale checkpoints: `docker compose exec redis redis-cli keys '*'`. 3. Flush stale session: see Useful Commands. |
| `customer_similarity_dict.json` missing or stale | File not present in `backend/tmp/` | 1. Confirm file exists: `docker compose exec backend ls /app/tmp/`. 2. [TODO: How is `customer_similarity_dict.json` generated or refreshed? Is there a pipeline/script?] |
| Memory not persisting across backend restarts | Redis is running in non-persistent mode (no AOF/RDB configured in this stack) | 1. See TODO in `graph.py` — Redis is ephemeral in the current Compose config. 2. Workaround: do not restart Redis between sessions. 3. Long-term: migrate to Azure Cache for Redis or add a persistent Redis volume. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker and Docker Compose v2 installed on the host
- `.env` file in the repository root with all required secrets (see Section 5)
- SQLite database files present under `./database/`
- `./postgres/init.sql` present for Postgres initialisation

### 4.1 First-time Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file (copy from template and fill in secrets)
cp .env.example .env   # [TODO: confirm .env.example exists or document required variables]
# Edit .env — see Section 5 for required variables

# 3. Confirm database files are present
ls ./database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db

# 4. Build and start all services
docker compose up --build -d

# 5. Confirm all containers are healthy
docker compose ps

# 6. Smoke test
curl http://localhost:8000/health
curl http://localhost:8080
```

### 4.2 Routine Update Deployment

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild only changed services (backend or frontend)
docker compose up --build -d backend frontend

# 3. Verify health
docker compose ps
curl -f http://localhost:8000/health
```

### 4.3 Config-only Change (e.g., `config.yml` or prompt files)

```bash
# config.yml and prompts are baked into the Docker image at build time
docker compose up --build -d backend

# Verify new config loaded (check log output)
docker compose logs --tail=50 backend
```

### 4.4 Rollback Steps

```bash
# Option A — roll back to previous Docker image (if tags exist)
docker compose down backend
docker tag underwriting_chatbot-backend:previous underwriting_chatbot-backend:latest
docker compose up -d backend

# Option B — roll back via Git
git log --oneline -10          # identify last good commit SHA
git checkout <good-sha>
docker compose up --build -d backend frontend
git checkout main              # return to main after rollback is stable
```

> [TODO: Is there a container registry (e.g., ACR, ECR, GHCR) where versioned image tags are pushed? If so, document the tag naming convention here.]

### 4.5 Database Migrations

[TODO: Is there a migration tool (Alembic, Flyway, etc.) for the PostgreSQL Chainlit database? Document migration steps here.]

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Source | Why it Matters |
|---|---|---|
| `/health` endpoint response code | Backend HTTP | Primary liveness signal |
| Container restart count | `docker compose ps` / Docker daemon | Indicates crash-looping |
| Redis memory usage | `redis-cli INFO memory` | High usage may evict session checkpoints |
| Anthropic API latency & token usage | stdout logs (`[SPECIALIST]`, `[AGGREGATOR]` log lines) | Indicates rate limiting or runaway token spend |
| Postgres connection pool | `docker compose logs postgres` | Session persistence for Chainlit |
| `run_underwriting_assessment` tool elapsed time | stdout `[ASSESSMENT]` log lines | >30 s suggests LLM slowness or semaphore starvation |
| GitHub Actions workflow pass rate | GitHub Actions tab | Failed runs mean docs/tests/reviews are not generated |

### 5.2 Log Locations

```bash
# All containers
docker compose logs -f

# Backend only (most verbose — contains [CHAT], [TOOL START/END], [SPECIALIST], [AGGREGATOR])
docker compose logs -f backend

# Redis
docker compose logs -f redis

# Postgres
docker compose logs -f postgres
```

### 5.3 Structured Log Events (Backend stdout)

| Log Prefix | Meaning |
|---|---|
| `[CHAT]` | New chat request received; includes session, model, mode |
| `[TOOL START]` | Agent invoked a tool |
| `[TOOL END]` | Tool returned; includes elapsed time |
| `[SPECIALIST]` | Individual category assessment completed; includes token counts and latency |
| `[AGGREGATOR]` | Final report aggregation; includes token counts and latency |
| `[ASSESSMENT]` | Underwriting assessment started |

### 5.4 Alerting

> [TODO: No alerting infrastructure (Prometheus, Datadog, Azure Monitor, PagerDuty, etc.) is configured in the current codebase. Define alert thresholds and notification channels here.]

Recommended minimum alerts to configure:

- `/health` returns non-200 for > 30 s → **page on-call**
- Any container in `restarting` state → **page on-call**
- Anthropic API 429 (rate limit) errors in logs → **notify engineering**
- Redis `used_memory` > 80% of `maxmemory` → **notify engineering**
- GitHub Actions workflow failure on `main` push → **notify engineering**

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: add PagerDuty / Slack handle] | Service down, `/health` failing, containers crash-looping |
| L2 | Backend Engineer | [TODO: add contact] | LLM integration failures, LangGraph/Redis issues, tool errors |
| L3 | ML/AI Lead | [TODO: add contact] | Model card issues, assessment quality degradation, prompt changes |
| L4 | Anthropic Support | support.anthropic.com | Sustained API outage, billing/quota issues |
| L4 | Infra / Platform | [TODO: add contact] | Host-level, network, or Docker daemon issues |
| Business | Solution Owner | [TODO: add contact] | Data breach, regulatory concern, customer-impacting outage > 1 h |

> **Incident channel:** [TODO: Slack channel / Teams channel]
> **Incident management tool:** [TODO: PagerDuty / OpsGenie / Jira Service Management]

---

## 7. Useful Commands

### Start / Stop

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop and destroy all volumes (destructive — wipes Postgres data)
docker compose down -v
```

### Build

```bash
# Rebuild backend only
docker compose up --build -d backend

# Rebuild all
docker compose up --build -d
```

### Logs

```bash
# Stream all logs
docker compose logs -f

# Backend logs only, last 100 lines
docker compose logs --tail=100 -f backend

# Search for errors
docker compose logs backend 2>&1 | grep -i "error\|exception\|traceback"
```

### Health Check

```bash
curl -f http://localhost:8000/health && echo "OK" || echo "FAIL"
```

### Test the Chat Endpoint

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Assess customer CUST00000001",
    "temperature": 0.3,
    "session_id": "test-session-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### Redis

```bash
# Ping Redis
docker compose exec redis redis-cli ping

# List all session keys
docker compose exec redis redis-cli keys '*'

# Flush a specific session (replace <thread_id> with actual session_id)
docker compose exec redis redis-cli del "<thread_id>"

# Flush ALL keys (destructive — clears all conversation memory)
docker compose exec redis redis-cli flushall

# Check memory usage
docker compose exec redis redis-cli info memory | grep used_memory_human
```

### PostgreSQL

```bash
# Connect to Chainlit database
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit -c '\dt'

# Check active connections
docker compose exec postgres psql -U chainlit -d chainlit \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname='chainlit';"
```

### Inspect Running Containers

```bash
# View all container statuses including health
docker compose ps

# Inspect backend environment variables (redacts nothing — handle with care)
docker compose exec backend env | sort

# Check which database files are mounted
docker compose exec backend ls -lh /data/
```

### GitHub Actions — Manual Workflow Triggers

```bash
# Trigger code review on a PR (replace 42 with actual PR number)
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger tech documentation regeneration
gh workflow run tool2_tech_docs.yml

# Trigger business documentation for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Trigger auto test generation
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Generate UAT test pack
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

### Required Environment Variables Reference

```bash
# Minimum required in .env / runtime environment
ANTHROPIC_API_KEY=<key>          # Required — all LLM calls
GOOGLE_API_KEY=<key>             # Required only if using model=gemini
REDIS_HOST=redis                 # Defaults to localhost; set to service name in Compose
# GitHub Actions only