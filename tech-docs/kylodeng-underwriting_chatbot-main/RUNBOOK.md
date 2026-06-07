# Operational Runbook — Underwriting Chatbot (`kylodeng/underwriting_chatbot-main`)

---

## 1. Service Overview

The Underwriting Chatbot is a multi-container AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The backend is a Python FastAPI application that orchestrates a LangGraph-based agent; the agent calls specialist LLMs (Anthropic Claude, Google Gemini) in parallel to produce a structured `UnderwritingReport` covering finance, health, life, and other risk domains. Customer profile data is served from SQLite databases, conversation state is persisted in Redis (via `AsyncRedisSaver`), and the frontend is a separate web application (Chainlit) backed by PostgreSQL for session/user data. The system is deployed locally via Docker Compose, with CI/CD automation pipelines in GitHub Actions for code review, documentation generation, automated testing, and UAT facilitation.

---

## 2. Health Checks

### Docker Compose Stack

```bash
docker compose ps                       # All containers should show "running (healthy)"
```

### Backend API

```bash
curl -f http://localhost:8000/health    # Expected: {"status": "ok"}
```

### Redis

```bash
docker compose exec redis redis-cli ping   # Expected: PONG
```

### PostgreSQL

```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### Frontend

```
http://localhost:8080                   # Should render the Chainlit UI
```

### End-to-End Smoke Test

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
# Expected: SSE stream with at least one "response" event
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container unhealthy / exits immediately | Missing `.env` file or unset `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | 1. Check `docker compose logs backend`. 2. Verify `.env` exists in project root with all required variables. 3. `docker compose up --build backend`. |
| `curl /health` returns connection refused | Backend not started or crashed after startup | 1. `docker compose ps` — check backend state. 2. `docker compose logs backend --tail 50`. 3. Check port 8000 not bound by another process: `lsof -i :8000`. |
| `redis` container fails to start | Port 6379 already in use, or image pull failure | 1. `docker compose logs redis`. 2. Check for port conflict: `lsof -i :6379`. 3. Stop conflicting process and `docker compose restart redis`. |
| Redis connection error in backend logs (`ConnectionRefusedError`) | Backend started before Redis was ready; or `REDIS_HOST` env var incorrect | 1. `docker compose restart backend` (depends_on ordering should handle this). 2. Verify `REDIS_HOST=redis` in compose env. 3. `docker compose exec backend env \| grep REDIS`. |
| Chat response hangs / never completes | LLM API timeout, rate limit, or Anthropic/Google API outage | 1. Check backend logs for HTTP 429 or 503 from API calls. 2. Verify API keys are valid and have quota. 3. Switch `model` param to `anthropic-fast` for lower latency. 4. Check [https://status.anthropic.com](https://status.anthropic.com) and [https://status.cloud.google.com](https://status.cloud.google.com). |
| `ValueError: Unsupported or unconfigured model provider` | Model name in request does not match keys in `LLMS.model_mapper` | 1. Check request `model` field — valid values: `anthropic`, `anthropic-fast`, `gemini`. 2. `azure` and `openai` are explicitly `None` (not implemented). |
| Frontend shows blank page or fails to connect | Backend not healthy when frontend started, or `BACKEND_URL` misconfigured | 1. Confirm backend health check passes. 2. `docker compose logs frontend`. 3. Verify `BACKEND_URL=http://backend:8000` in compose env. 4. `docker compose restart frontend`. |
| PostgreSQL init fails | `./postgres/init.sql` missing or syntax error; volume conflict | 1. `docker compose logs postgres`. 2. Verify `./postgres/init.sql` exists. 3. To reset: `docker compose down -v && docker compose up`. **Warning: destroys postgres_data volume.** |
| `UnderwritingReport` JSON parse error in logs | Aggregator LLM response exceeded `aggregator_max_tokens` (8000) or returned malformed output | 1. Check backend logs for `[AGGREGATOR]` output token count. 2. Increase `aggregator_max_tokens` in `backend/config.yml`. 3. Retry the request; LLM output is non-deterministic. |
| SQLite database read error | Database file not mounted or path mismatch | 1. Check volume mounts in `docker-compose.yml` — `.db` files must exist under `./database/`. 2. `docker compose exec backend ls /data/` to verify files are present. |
| GitHub Actions workflow fails (`ANTHROPIC_API_KEY` not found) | Repository secret not set | 1. Go to repo **Settings → Secrets and variables → Actions**. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| Agent loops / repeats tool calls | LangGraph state not persisted (Redis lost); or agent not reading conversation history correctly | 1. Verify Redis is healthy. 2. Check `session_id` is consistent across requests for the same conversation. 3. Restart backend and Redis: `docker compose restart redis backend`. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker Engine ≥ 24.x and Docker Compose v2
- `.env` file in project root (see Environment Variables table in Section 5)
- Database files present under `./database/`
- `./postgres/init.sql` present

### Deploy (First Time or Full Rebuild)

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create and populate .env
cp .env.example .env          # [TODO: does .env.example exist? If not, create .env manually]
# Edit .env with required values (see Section 5)

# 3. Build and start all services
docker compose up --build -d

# 4. Verify all containers are healthy
docker compose ps

# 5. Run the health check
curl -f http://localhost:8000/health

# 6. Access the frontend
open http://localhost:8080
```

### Deploy (Code Update — No Schema Changes)

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild and restart only the changed service(s)
docker compose up --build -d backend
# Or frontend if only frontend changed:
docker compose up --build -d frontend

# 3. Verify health
curl -f http://localhost:8000/health
docker compose ps
```

### Deploy (Config Change — `backend/config.yml`)

```bash
# config.yml is baked into the image — rebuild is required
docker compose up --build -d backend
curl -f http://localhost:8000/health
```

### Rollback Procedure

```bash
# Option A: Roll back to a previous Docker image tag
# [TODO: are images pushed to a container registry? If yes, specify registry URL and tagging convention]

# Option B: Roll back via git and rebuild
git log --oneline -10           # identify the last known-good commit
git checkout <commit-sha>
docker compose up --build -d backend
curl -f http://localhost:8000/health

# Option C: If a bad database migration caused the issue — restore postgres volume
docker compose down
docker volume rm underwriting_chatbot-main_postgres_data
# Restore from backup [TODO: define backup/restore procedure for postgres_data volume]
docker compose up -d
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | How to Observe | Alert Threshold |
|---|---|---|
| Backend container health | `docker compose ps` / Docker health check | Any non-`healthy` state |
| API response time (chat endpoint) | Backend stdout: `[CHAT]` log lines with timestamps | [TODO: define SLA — suggested >30s is degraded] |
| Specialist LLM token usage | `[SPECIALIST]` log lines: `in=`, `out=` tok counts | `out` tokens approaching 1500 (specialist cap) |
| Aggregator LLM token usage | `[AGGREGATOR]` log lines | `out` tokens approaching 8000 (aggregator cap) |
| Tool call duration | `[TOOL END]` log lines: `time=` field | [TODO: define threshold — suggested >20s] |
| Redis connectivity | Backend log errors on startup | Any `ConnectionRefusedError` to Redis |
| LLM API errors (429/503) | Backend logs from `httpx` / `langchain_anthropic` | Any 429 rate-limit or 5xx error |

### Log Locations

```bash
# All service logs (follow mode)
docker compose logs -f

# Backend only (most relevant for debugging)
docker compose logs -f backend

# Structured log patterns to grep for:
docker compose logs backend | grep "\[CHAT\]"         # incoming requests
docker compose logs backend | grep "\[TOOL"           # tool start/end timings
docker compose logs backend | grep "\[SPECIALIST\]"   # per-category LLM calls
docker compose logs backend | grep "\[AGGREGATOR\]"   # final report generation
docker compose logs backend | grep "ERROR\|Exception\|Traceback"  # errors
```

### Alerting

[TODO: Is there a monitoring stack (Prometheus, Datadog, Azure Monitor)? Currently no instrumentation is visible in the code. Recommend adding OpenTelemetry or at minimum structured JSON logging.]

[TODO: Are there PagerDuty/OpsGenie integrations for on-call alerting?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: on-call rotation contact] | Service down, health check failing |
| L2 | Backend / ML Engineer | [TODO: backend team contact] | LLM errors, assessment logic failures, Redis/LangGraph issues |
| L3 | Tech Lead | [TODO: tech lead name and contact] | Data integrity issues, security incidents, extended outage >1h |
| L4 | Solution Owner / Business Sponsor | [TODO: business owner contact] | Regulatory or compliance impact, customer-facing data errors |
| External | Anthropic Support | [https://support.anthropic.com](https://support.anthropic.com) | API outage or billing issues with Claude |
| External | Google Cloud Support | [TODO: GCP support tier and contact] | Gemini API outage |

---

## 7. Useful Commands

### Stack Management

```bash
# Start all services (detached)
docker compose up -d

# Start with rebuild
docker compose up --build -d

# Stop all services (preserve volumes)
docker compose down

# Stop and remove volumes (DESTRUCTIVE — loses postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend
docker compose restart redis

# View real-time logs for all services
docker compose logs -f

# View logs for a specific service (last 100 lines)
docker compose logs --tail 100 backend
```

### Health & Status

```bash
# Check all container statuses
docker compose ps

# Backend health endpoint
curl -f http://localhost:8000/health

# Redis ping
docker compose exec redis redis-cli ping

# PostgreSQL readiness
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Check Redis keys (conversation state)
docker compose exec redis redis-cli keys "*"

# Inspect Redis memory usage
docker compose exec redis redis-cli info memory | grep used_memory_human
```

### Debugging

```bash
# Open a shell in the backend container
docker compose exec backend bash

# Check environment variables in backend container
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS|OPENAI"

# Check mounted database files
docker compose exec backend ls -lh /data/

# Inspect a SQLite database
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"

# Test a chat request manually (SSE stream)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get profile for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### GitHub Actions (CI/CD)

```bash
# Manually trigger tech documentation generation (requires gh CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=42

# View recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# Watch a running workflow
gh run watch --repo kylodeng/underwriting_chatbot-main
```

### Configuration

```bash
# Edit LLM configuration (token limits, temperature, default model)
vim backend/config.yml
# Then rebuild: docker compose up --build -d backend

# View current model card
cat backend/model_card.json

# Validate docker-compose syntax
docker compose config
```

---

*Runbook generated from repository `kylodeng/underwriting_chatbot-main`. Items marked `[TODO]` require manual input from the owning team.*