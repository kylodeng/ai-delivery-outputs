# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is a conversational AI assistant designed to help life insurance underwriters assess customer risk. It exposes a streaming HTTP API (`FastAPI`, port 8000) that accepts chat messages and returns Server-Sent Events (SSE). Each request invokes a LangGraph-based agent that can call three tools: fetching a customer profile from a local SQLite database, running a parallel multi-specialist LLM underwriting risk assessment (using Anthropic Claude or Google Gemini models via a configurable `LLMS` abstraction), and retrieving lookalike customers via a pre-computed similarity dictionary. Assessment results are aggregated into a structured `UnderwritingReport` Pydantic model. The service is containerised with Docker Compose and depends on Redis (for LangGraph conversation checkpointing) and PostgreSQL (for the frontend Chainlit session store). A suite of five GitHub Actions CI workflows automate code review, technical documentation, business documentation, test generation, and UAT facilitation — all powered by the Anthropic Claude API.

---

## 2. Health Checks

### 2.1 Backend API

```bash
# Should return: {"status": "ok"}
curl -f http://localhost:8000/health
```

Docker Compose already runs this check every 10 s with 5 retries and a 15 s start grace period.

### 2.2 Redis

```bash
# Should return: PONG
docker compose exec redis redis-cli ping

# Verify keyspace (LangGraph checkpoints are stored here)
docker compose exec redis redis-cli keys "*"
```

### 2.3 PostgreSQL

```bash
# Should print the chainlit database name
docker compose exec postgres psql -U chainlit -d chainlit -c "\l"
```

### 2.4 Frontend

```bash
# Should return HTTP 200
curl -f http://localhost:8080
```

### 2.5 Full Stack

```bash
docker compose ps
# All four services (redis, postgres, backend, frontend) should show status: running / healthy
```

### 2.6 LLM Connectivity

```bash
# Send a minimal chat message and check for SSE response (non-empty stream)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"ping","temperature":0,"session_id":"healthcheck","model":"anthropic-fast","mode":"fast"}'
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `docker compose up` fails with `port already in use` | Port 8000, 8080, 6379, or 5432 already bound by another process | Run `lsof -i :<port>` to identify the process; stop it or change the port mapping in `docker-compose.yml` |
| Backend container exits immediately / restart loop | Missing or malformed `.env` file; `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` not set | Check `docker compose logs backend`; verify `.env` contains all required variables (see §5 Monitoring) |
| `GET /health` returns 500 or connection refused | Backend crashed; Redis not ready when backend started | Check `docker compose logs backend`; confirm Redis is healthy (`docker compose ps redis`) before restarting backend |
| Chat request hangs indefinitely / no SSE tokens | Anthropic or Google API key invalid or rate-limited; network egress blocked | Check backend logs for `AuthenticationError` or HTTP 429; verify API keys; check Anthropic/Google status pages |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in request body does not match keys in `LLMS.model_mapper` | Use one of: `anthropic`, `anthropic-fast`, `gemini` — see `backend/modules/LLMS.py` |
| LangGraph `AsyncRedisSaver` error / checkpoint not found | Redis connection lost mid-conversation or `REDIS_HOST` env var wrong | Verify `REDIS_HOST=redis` in backend environment; `docker compose restart redis backend` |
| Assessment returns empty or partial `UnderwritingReport` | Aggregator LLM exceeded `aggregator_max_tokens` (8000) or received malformed specialist output | Increase `aggregator_max_tokens` in `backend/config.yml`; check backend logs for `[AGGREGATOR]` token counts |
| Specialist assessment very slow (>30 s) | Too many concurrent specialist LLM calls hitting rate limits; `asyncio.Semaphore(4)` may need tuning | Lower the semaphore value in `assessment.py`; check Anthropic tier rate limits; switch to `anthropic` (Sonnet) model |
| `customer_similarity_dict.json` lookup returns `KeyError` | Customer ID not present in pre-computed similarity file | Confirm `CUST_ID` format is `CUST{8 digits}` (e.g. `CUST00000001`); regenerate the similarity dictionary if new customers were added |
| SQLite database file not found in container | Volume mount path wrong or database files missing on host | Verify files exist at `./database/*.db` on the Docker host; check volume mounts in `docker-compose.yml` |
| PostgreSQL migration/init fails on first start | `postgres/init.sql` missing or malformed | Check `docker compose logs postgres`; manually run `docker compose exec postgres psql -U chainlit -d chainlit -f /docker-entrypoint-initdb.d/init.sql` |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY not found` | Secret not set in repository settings | Go to **Settings → Secrets and variables → Actions**; add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` |
| GitHub Actions `tool2_tech_docs` generates no output | `OUTPUT_REPO` does not exist or `GH_TOKEN` lacks write access | Create `ai-delivery-outputs` repo under the same owner; ensure `GH_TOKEN` has `repo` scope |
| Redis memory exhausted; checkpoints lost | No eviction policy set; long-running sessions accumulate | Set `maxmemory-policy allkeys-lru` in Redis config; [TODO: what is the target Redis memory limit for this deployment?] |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose V2 installed
- `.env` file present at repo root (see §5 for required variables)
- SQLite database files present at `./database/`
- `backend/tmp/customer_similarity_dict.json` present

---

### 4.1 First-Time Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file from the template
cp .env.example .env          # [TODO: confirm .env.example exists or document the template]
# Edit .env with real values (see §5 Environment Variables)

# 3. Build all images
docker compose build --no-cache

# 4. Start infrastructure dependencies first
docker compose up -d redis postgres

# 5. Wait for Postgres to be ready (~5 s), then start the application stack
sleep 5
docker compose up -d backend frontend

# 6. Verify health
docker compose ps
curl -f http://localhost:8000/health
```

---

### 4.2 Updating to a New Version

```bash
# 1. Pull the latest code
git pull origin main

# 2. Rebuild only changed images
docker compose build backend frontend

# 3. Rolling restart (preserves Redis & Postgres state)
docker compose up -d --no-deps backend frontend

# 4. Confirm healthy
docker compose ps
curl -f http://localhost:8000/health
```

---

### 4.3 Configuration-Only Change (e.g. `config.yml` or `prompts/`)

```bash
# No rebuild needed — restart backend to reload config from disk
docker compose restart backend
curl -f http://localhost:8000/health
```

---

### 4.4 Rollback Procedure

```bash
# Option A: roll back to the previous Docker image tag (if images are tagged)
# [TODO: confirm image registry and tagging strategy]
docker compose stop backend frontend
docker tag <registry>/backend:<previous-tag> <registry>/backend:latest
docker compose up -d backend frontend

# Option B: roll back via git (if no image registry)
git log --oneline -10                        # find the last known-good commit SHA
git checkout <sha>
docker compose build backend frontend
docker compose up -d --no-deps backend frontend
curl -f http://localhost:8000/health

# Option C: revert a bad config.yml without rebuild
git checkout HEAD~1 -- backend/config.yml
docker compose restart backend
```

> **Note:** Redis checkpoints are keyed by `session_id`. A rollback does not automatically clear existing session state. If the rollback involves a breaking change to the checkpoint schema, flush Redis:
> ```bash
> docker compose exec redis redis-cli FLUSHALL
> ```
> This will clear all conversation history.

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Source | Alert Threshold |
|---|---|---|
| Backend health endpoint response time | `GET /health` synthetic probe | > 2 s |
| Backend container restart count | Docker engine / container runtime | > 2 restarts in 5 min |
| Redis memory usage | `redis-cli INFO memory` → `used_memory_human` | > 80% of `maxmemory` |
| Postgres connection count | `pg_stat_activity` | > 90% of `max_connections` |
| Anthropic API latency (specialist LLMs) | Backend stdout: `[SPECIALIST]` log lines → `time=` field | > 15 s per specialist call |
| Aggregator LLM output token count | Backend stdout: `[AGGREGATOR]` log lines → `out=` field | Approaching 8000 (config cap) |
| Total assessment wall-clock time | Backend stdout: `[ASSESSMENT]` completion logs | > 60 s |
| GitHub Actions workflow failure rate | GitHub Actions UI / webhook | Any `tool1` through `tool5` failure |

### 5.2 Structured Log Patterns (backend stdout)

```
[CHAT]       session=<id> model=<model> mode=<mode> msg=<first 60 chars>
[TOOL START] <tool_name>
[TOOL END]   <tool_name>  time=<seconds>s
[SPECIALIST] category=<cat>  in=<n> tok  out=<n> tok  time=<s>s
[AGGREGATOR]               in=<n> tok  out=<n> tok  time=<s>s
[ASSESSMENT] Starting — <N> specialist calls (mode=<mode>)
```

Pipe logs to a log aggregator:

```bash
docker compose logs -f backend | grep -E "\[(CHAT|TOOL|SPECIALIST|AGGREGATOR|ASSESSMENT)\]"
```

### 5.3 Environment Variables Required at Runtime

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Anthropic Claude API key |
| `GOOGLE_API_KEY` | ✅ (if using Gemini) | Google Generative AI API key |
| `REDIS_HOST` | ✅ | Redis hostname (default: `redis` in Docker Compose) |
| `DATABASE_URL` | ✅ (frontend) | PostgreSQL URL for Chainlit session store |
| `GH_TOKEN` | ✅ (CI only) | GitHub PAT for Actions workflows |
| `SENDGRID_API_KEY` | ✅ (CI only) | SendGrid API key for email notifications |
| `OUTPUT_REPO` | CI only | Target repo for AI-generated docs (default: `ai-delivery-outputs`) |

### 5.4 Alerting

[TODO: What is the monitoring stack? (e.g. Datadog, Prometheus/Grafana, Azure Monitor, CloudWatch?)]  
[TODO: Are there on-call PagerDuty / OpsGenie rotations configured?]  
[TODO: Should Anthropic API error rate trigger an alert to a Slack channel?]

---

## 6. Escalation Path

| Level | Condition | Contact |
|---|---|---|
| L1 — On-Call Engineer | Service health check failing; container restart loop; Redis/Postgres unavailable | [TODO: on-call rotation contact / Slack channel] |
| L2 — Backend Engineer | LLM assessment errors; `UnderwritingReport` schema failures; LangGraph agent loops | [TODO: backend team lead name and contact] |
| L3 — ML / AI Engineer | Model card accuracy concerns; assessment prompt degradation; token budget issues | [TODO: ML engineer name and contact] |
| L4 — Vendor Support | Anthropic API outage (check https://status.anthropic.com); Google API outage | Anthropic Enterprise support [TODO: account ID]; Google Cloud support [TODO: project ID] |
| Security / Compliance | Any suspected data leak of customer profiles; PII in logs | [TODO: security contact / CISO] |
| Product Owner | Go/no-go decisions on rollback; business impact assessment | [TODO: product owner name — see `model_card.json` stakeholder fields] |

---

## 7. Useful Commands

### Docker Compose Operations

```bash
# Start full stack
docker compose up -d

# Stop full stack (preserves volumes)
docker compose down

# Stop and wipe all data (DESTRUCTIVE — clears Postgres & Redis)
docker compose down -v

# View real-time logs for all services
docker compose logs -f

# View real-time backend logs only
docker compose logs -f backend

# Restart a single service
docker compose restart backend

# Rebuild and redeploy backend only
docker compose build backend && docker compose up -d --no-deps backend

# Check service health status
docker compose ps
```

### Health & Diagnostics

```bash
# Backend health
curl -f http://localhost:8000/health

# Send a test chat request (streaming)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me about customer CUST00000001",
    "temperature": 0,
    "session_id": "runbook-test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Redis: check connection and keyspace
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli INFO memory
docker compose exec redis redis-cli DBSIZE

# Postgres: check sessions table
docker compose exec postgres psql -U chainlit -d chainlit -c "SELECT count(*) FROM pg_stat_activity;"

# Inspect SQLite customer profile DB
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT * FROM customer_profile LIMIT 5;"
```

### Redis Session Management

```bash
# List all LangGraph checkpoint keys
docker compose exec redis redis-cli keys "*"

# Delete a specific session's checkpoint (replace SESSION_ID)
docker compose exec redis redis-cli DEL "SESSION_ID"

# Flush ALL session checkpoints (use only during rollback / major incidents)
docker compose exec redis redis-cli FLUSHALL
```

### LLM Configuration Tuning

```bash
# Edit LLM config (token budgets, temperature, default model)
vi backend/config.yml

# Restart backend to apply (no rebuild needed)
docker compose restart backend

# Verify config is loaded (watch startup logs)
docker compose logs -f backend | head -30
```

### GitHub Actions — Manual Workflow Triggers

```bash
# Trigger code review on a specific PR (requires gh CLI)
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# Trigger tech docs regeneration
gh workflow run tool2_tech_docs.yml

# Trigger business doc for a specific version
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Trigger test generation
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -