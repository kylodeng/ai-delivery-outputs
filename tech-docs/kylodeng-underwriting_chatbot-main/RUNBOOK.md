# Operational Runbook — `underwriting_chatbot`

---

## 1. Service Overview

The `underwriting_chatbot` is an AI-powered life insurance underwriting assistant built on a FastAPI backend and a chat-based frontend. It exposes a streaming Server-Sent Events (SSE) `/chat` endpoint that orchestrates a LangGraph agent backed by Anthropic Claude (default: `claude-haiku-4-5`) and optionally Google Gemini. The agent uses three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — to retrieve customer data from SQLite databases, find similar policyholders, and run a multi-specialist LLM risk assessment that produces a structured `UnderwritingReport` (risk class: Preferred / Standard Plus / Standard / Substandard). Agent conversation state is checkpointed in Redis; a PostgreSQL instance backs the Chainlit-based frontend session store. The system is deployed via Docker Compose and is intended for internal use by insurance underwriters.

---

## 2. Health Checks

Run these checks in order to confirm all components are healthy.

### 2.1 Backend API

```bash
curl -sf http://localhost:8000/health
# Expected: {"status": "ok"}
```

### 2.2 Docker Compose services

```bash
docker compose ps
# All four services (redis, postgres, backend, frontend) should show "running" / "healthy"
```

### 2.3 Redis

```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### 2.4 PostgreSQL

```bash
docker compose exec postgres pg_isready -U chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 2.5 Frontend

```bash
curl -sf http://localhost:8080
# Expected: HTTP 200 with HTML body
```

### 2.6 LLM Connectivity

```bash
# Verify Anthropic key is accepted (no actual token spend)
curl -sf https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.models[0].id'
```

### 2.7 SQLite Database Mounts

```bash
docker compose exec backend ls -lh /data/
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container stuck in `starting` / fails health check | Missing or malformed `.env` file; missing `ANTHROPIC_API_KEY` | 1. Check `docker compose logs backend`. 2. Verify `.env` exists in repo root with all required keys. 3. `docker compose up --build backend`. |
| `{"detail": "Internal Server Error"}` on `/chat` | LLM API key invalid or rate-limited | 1. Check `docker compose logs backend` for `AuthenticationError`. 2. Validate `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`. 3. Check Anthropic dashboard for quota. |
| Chat responses stream but stop mid-sentence | `specialist_max_tokens: 1500` cap hit or network timeout | 1. Increase `specialist_max_tokens` in `backend/config.yml`. 2. Rebuild: `docker compose up --build backend`. |
| `redis.exceptions.ConnectionError` in logs | Redis container not running or wrong `REDIS_HOST` | 1. `docker compose restart redis`. 2. Confirm `REDIS_HOST=redis` is set in backend environment. 3. Check `docker compose logs redis`. |
| Agent repeats tool calls in a loop | LangGraph checkpoint state corrupted in Redis | 1. Flush Redis: `docker compose exec redis redis-cli FLUSHALL`. **Warning: clears all sessions.** 2. Restart backend: `docker compose restart backend`. |
| Frontend shows blank page or 502 | Frontend started before backend was healthy | 1. `docker compose logs frontend`. 2. Verify backend health check passes. 3. `docker compose restart frontend`. |
| `sqlite3.OperationalError: no such table` | Database file not mounted or corrupted | 1. Confirm `.db` files exist under `./database/`. 2. Check volume mounts in `docker-compose.yml`. 3. Restore from backup. [TODO: Where are canonical database backups stored?] |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request does not match `LLMS.model_mapper` keys | 1. Valid values: `anthropic`, `anthropic-fast`, `gemini`. 2. Check request payload. 3. `azure` and `openai` are not yet implemented (return `None`). |
| `POST /chat` returns 200 but no SSE events | `EventSourceResponse` connection dropped by proxy/load balancer | 1. Ensure proxy has SSE timeout ≥ 120s. [TODO: What proxy/LB is used in production?] 2. Check `CORS` config if cross-origin. |
| `ModuleNotFoundError` on backend startup | Python dependencies not installed in image | 1. `docker compose build --no-cache backend`. 2. Verify `Dockerfile` installs all deps. [TODO: Confirm Dockerfile location and contents.] |
| PostgreSQL `FATAL: password authentication failed` | Wrong credentials or init script failed | 1. `docker compose logs postgres`. 2. Delete volume and reinitialise: `docker compose down -v && docker compose up postgres`. **Warning: destroys all Chainlit session data.** |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose v2
- `.env` file in repo root (see §5 Environment Variables)
- SQLite database files present under `./database/`
- [TODO: Is there a container registry? What is the image pull/push workflow?]

---

### 4.1 First-Time Deployment

```bash
# 1. Clone the repo
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file from the template
cp .env.example .env          # [TODO: Confirm .env.example exists]
# Edit .env with real API keys and secrets

# 3. Confirm databases are in place
ls -lh ./database/*.db

# 4. Build and start all services
docker compose up --build -d

# 5. Verify health
docker compose ps
curl -sf http://localhost:8000/health
```

---

### 4.2 Routine Update Deployment

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild changed images (only affected layers rebuild)
docker compose build

# 3. Perform a rolling restart (Redis and Postgres stay up)
docker compose up -d --no-deps backend frontend

# 4. Confirm health
curl -sf http://localhost:8000/health
docker compose ps
```

---

### 4.3 Config-Only Change (e.g. `config.yml` or prompts)

```bash
# config.yml and prompts are baked into the image — rebuild is required
docker compose build backend
docker compose up -d --no-deps backend
curl -sf http://localhost:8000/health
```

---

### 4.4 Rollback Steps

```bash
# 1. Identify the previous working image tag or git commit
git log --oneline -10

# 2. Check out the previous commit
git checkout <previous-commit-sha>

# 3. Rebuild and redeploy
docker compose build backend frontend
docker compose up -d --no-deps backend frontend

# 4. Verify
curl -sf http://localhost:8000/health

# 5. If state is corrupted, flush Redis sessions
docker compose exec redis redis-cli FLUSHALL
docker compose restart backend
```

> [TODO: Are Docker image tags/versions published to a registry to enable image-level rollback without rebuilding?]

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | What to Monitor | Warning Threshold |
|---|---|---|
| Backend container health | `docker compose ps` health status | Any status other than `healthy` |
| `/health` endpoint response time | HTTP latency | > 2s |
| `/chat` SSE stream latency | Time-to-first-token | > 10s |
| Redis memory usage | `redis-cli INFO memory` → `used_memory_human` | > 80% of available RAM |
| Anthropic API error rate | `429` / `401` responses in backend logs | Any `401`; `429` sustained > 60s |
| Specialist LLM call duration | Log line `[SPECIALIST] ... time=Xs` | > 30s per specialist |
| Aggregator LLM call duration | Log line `[AGGREGATOR] ... time=Xs` | > 60s |
| Token usage per request | `in=` / `out=` in specialist/aggregator log lines | Aggregator `out` approaching `8000` |
| PostgreSQL connection count | `SELECT count(*) FROM pg_stat_activity;` | > 80 |

### 5.2 Log Sources

```bash
# All services combined
docker compose logs -f

# Backend only (most diagnostic value)
docker compose logs -f backend

# Key log prefixes to grep:
# [CHAT]        — incoming request details
# [TOOL START]  — tool invocation
# [TOOL END]    — tool completion + elapsed time
# [SPECIALIST]  — per-category LLM call + token counts
# [AGGREGATOR]  — final aggregation step
# [ASSESSMENT]  — overall assessment lifecycle
```

### 5.3 Structured Log Patterns (grep helpers)

```bash
# Find slow specialist calls (> 20s)
docker compose logs backend | grep '\[SPECIALIST\]' | awk '{print $NF}' | sort -t= -k2 -rn | head

# Find errors
docker compose logs backend | grep -iE 'error|exception|traceback'

# Find 5xx from the ASGI layer
docker compose logs backend | grep -E 'HTTP/1.1" 5[0-9]{2}'
```

### 5.4 Alerting

> [TODO: No alerting infrastructure (Prometheus, CloudWatch, Datadog, etc.) was found in the repository. Define alerting stack and configure the following alerts:]
> - Backend health check failing for > 2 consecutive intervals
> - Anthropic API `429` sustained > 5 minutes
> - Redis memory > 80%
> - Any unhandled Python exception (Sentry DSN not found in code)

---

## 6. Escalation Path

| Level | Condition | Contact |
|---|---|---|
| L1 — On-call Engineer | Service unhealthy; container restart resolves | [TODO: On-call engineer name / PagerDuty rotation] |
| L2 — Backend Lead | LLM errors, assessment logic failures, Redis corruption | [TODO: Backend lead name / Slack handle] |
| L3 — Platform / Infra | Docker host down, disk full, network issues | [TODO: Platform team contact] |
| L4 — Vendor Support | Anthropic API outage (check https://status.anthropic.com) | [TODO: Anthropic support contract details] |
| Business Escalation | Incorrect underwriting decision / data integrity concern | [TODO: Underwriting manager / compliance contact] |

**Incident communication channel:** [TODO: Slack channel or Teams channel]
**Runbook owner:** [TODO: Team or individual responsible for this document]

---

## 7. Useful Commands

### Service Lifecycle

```bash
# Start everything
docker compose up -d

# Stop everything (preserves volumes)
docker compose down

# Stop and remove volumes (DESTRUCTIVE — deletes Postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend

# Rebuild and restart backend only
docker compose build backend && docker compose up -d --no-deps backend

# View live logs (all services)
docker compose logs -f

# View backend logs only
docker compose logs -f backend
```

### Health & Debugging

```bash
# Backend health check
curl -sf http://localhost:8000/health | jq .

# Test a chat request (streaming)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'

# Redis ping
docker compose exec redis redis-cli ping

# Redis memory stats
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Flush all Redis keys (clears agent sessions — use with caution)
docker compose exec redis redis-cli FLUSHALL

# List all Redis keys
docker compose exec redis redis-cli KEYS '*'

# PostgreSQL — check connectivity
docker compose exec postgres psql -U chainlit -c "SELECT version();"

# PostgreSQL — active connections
docker compose exec postgres psql -U chainlit -c "SELECT count(*) FROM pg_stat_activity;"

# Check database mounts inside backend container
docker compose exec backend ls -lh /data/

# Inspect SQLite database
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
```

### Environment & Configuration

```bash
# Print resolved environment variables for backend container
docker compose exec backend env | sort

# Validate config.yml is well-formed
docker compose exec backend python -c "import yaml; yaml.safe_load(open('config.yml'))"

# Check which LLM model is active
grep -A2 'default:' backend/config.yml
```

### GitHub Actions (CI/CD Workflows)

```bash
# Manually trigger tech-docs generation (requires gh CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# View recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10
```

### Log Filtering

```bash
# Find all tool calls in last 100 backend log lines
docker compose logs --tail=100 backend | grep -E '\[TOOL (START|END)\]'

# Find all assessment completions with timing
docker compose logs backend | grep '\[AGGREGATOR\]'

# Find Python exceptions
docker compose logs backend | grep -A5 'Traceback'
```

---

*This runbook was generated from source code analysis. Items marked `[TODO]` require a human to supply operational context not present in the repository.*