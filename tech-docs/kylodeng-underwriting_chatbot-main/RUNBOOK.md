# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that helps underwriters assess customer risk profiles through a conversational AI interface. The system consists of a **FastAPI backend** that orchestrates a LangGraph-based agent (backed by Anthropic Claude or Google Gemini LLMs), a **frontend chat UI** (Chainlit-based), a **Redis** instance for LangGraph session/checkpoint persistence, and a **PostgreSQL** database for chat history. The backend agent can invoke three tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — which draw on pre-built SQLite databases of customer profiles, ML model predictions, and feature importance data. A parallel set of five GitHub Actions workflows provide AI-assisted code review, documentation generation, test generation, and UAT facilitation using the Anthropic Claude API. The underwriting risk classification is underpinned by a CatBoostClassifier model (v1.0, trained on merged customer/KYC/financial datasets) with `Medical_Conditions` and `Smoker_Status` as the dominant risk drivers.

---

## 2. Health Checks

### Service-Level Health Checks

| Service | How to Check | Expected Result |
|---|---|---|
| **Backend API** | `GET http://localhost:8000/health` | `{"status": "ok"}` with HTTP 200 |
| **Backend container** | `docker compose ps backend` | State: `running (healthy)` |
| **Redis** | `docker compose exec redis redis-cli ping` | `PONG` |
| **PostgreSQL** | `docker compose exec postgres pg_isready -U chainlit` | `accepting connections` |
| **Frontend** | `curl -f http://localhost:8080` | HTTP 200 |
| **LLM connectivity** | Check backend logs for `[SPECIALIST]` or `[AGGREGATOR]` lines on a test chat message | Token counts and latencies printed; no `AuthenticationError` |
| **SQLite databases** | `docker compose exec backend ls /data/*.db` | All four `.db` files present |

### Composite Health (Docker)

```bash
docker compose ps
# All services should show "running (healthy)" or "running"
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or times out | Backend container crashed or not started | 1. `docker compose logs backend --tail=50` 2. Check for Python import errors on startup 3. `docker compose restart backend` |
| Backend starts but crashes immediately | Missing env var (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) | 1. Check `.env` file exists in repo root 2. Verify all required vars are set (see §5) 3. `docker compose up --env-file .env backend` |
| Chat returns no response / hangs indefinitely | LLM API unreachable or rate-limited | 1. Check backend logs for `anthropic` or `google` API errors 2. Verify API keys are valid and have quota 3. Switch model via `model` field in request to a fallback provider |
| `redis.exceptions.ConnectionError` in logs | Redis container down or `REDIS_HOST` misconfigured | 1. `docker compose restart redis` 2. Confirm `REDIS_HOST=redis` is set in backend env 3. `docker compose exec redis redis-cli ping` |
| `asyncpg` or PostgreSQL connection error | Postgres container down or wrong credentials | 1. `docker compose restart postgres` 2. Verify `DATABASE_URL` in frontend env matches postgres credentials (`chainlit`/`chainlit`) 3. Check `postgres_data` volume is intact |
| Frontend shows blank page or `502 Bad Gateway` | Backend not yet healthy when frontend started, or `BACKEND_URL` misconfigured | 1. `docker compose logs frontend --tail=30` 2. Confirm backend healthcheck passes before frontend starts (`depends_on: condition: service_healthy`) 3. `docker compose restart frontend` |
| Underwriting assessment returns partial/empty report | LLM hit `specialist_max_tokens` (1500) cap or aggregator hit 8000 cap | 1. Check backend logs for `[SPECIALIST]` token counts near 1500 2. Increase `specialist_max_tokens` in `backend/config.yml` 3. Redeploy backend |
| `ValueError: Unsupported or unconfigured model provider` | `model` name in request doesn't match `LLMS.model_mapper` keys | 1. Valid values: `gemini`, `anthropic`, `anthropic-fast` 2. Check request payload `model` field 3. [TODO: Are `azure` and `openai` providers ever expected to be enabled?] |
| SQLite database file not found at `/data/*.db` | Volume mount missing or DB files not committed to repo | 1. Confirm `./database/*.db` files exist on host 2. Check `docker-compose.yml` volume mounts 3. [TODO: How are the SQLite databases initially populated / seeded?] |
| GitHub Actions workflow fails with `KeyError: ANTHROPIC_API_KEY` | Secret not set in repository settings | 1. Go to repo Settings → Secrets and variables → Actions 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` |
| Redis session state lost between deployments | Redis is ephemeral (no persistence configured); noted as TODO in `graph.py` | 1. Known limitation — see TODO in `backend/agent/graph.py` 2. Workaround: users must start a new session after redeployment 3. Long-term fix: migrate to Azure Cache for Redis with AOF persistence |
| `[AGGREGATOR]` log shows `?` for token counts | `usage_metadata` not returned by LLM provider | Informational only; does not affect output. Check if provider supports usage reporting. |
| Agent repeats tool calls in a loop | LangGraph state/history not correctly propagating previous tool results | 1. Check `history` field in `AgentState` is accumulating 2. Restart the session with a new `session_id` 3. Check Redis for corrupted checkpoint: `docker compose exec redis redis-cli KEYS "*"` then `DEL <key>` |

---

## 4. Deployment Procedure

### Prerequisites

- Docker and Docker Compose installed on the target host
- `.env` file present in repo root with all required environment variables
- `./database/*.db` SQLite files present on host
- [TODO: Is there a container registry? Or is the image built locally from source every time?]
- [TODO: Is there a Kubernetes/cloud deployment target beyond local Docker Compose?]

### Step-by-Step Deployment

```bash
# 1. Pull latest code
git pull origin main

# 2. Verify .env file is present and populated
cat .env | grep -E "ANTHROPIC_API_KEY|GOOGLE_API_KEY|REDIS_HOST"

# 3. Build updated images (backend and frontend only; Redis/Postgres use pre-built images)
docker compose build backend frontend

# 4. Bring up infrastructure services first
docker compose up -d redis postgres

# 5. Wait for postgres to be ready
docker compose exec postgres pg_isready -U chainlit
# Retry until output is: localhost:5432 - accepting connections

# 6. Start backend (healthcheck gate prevents frontend from starting prematurely)
docker compose up -d backend

# 7. Poll backend health (should become healthy within 15s start_period + 5 retries × 10s)
until docker compose exec backend curl -sf http://localhost:8000/health; do
  echo "Waiting for backend..."; sleep 5
done

# 8. Start frontend
docker compose up -d frontend

# 9. Verify all services are healthy
docker compose ps

# 10. Smoke test — send a test chat message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
```

### Rollback Steps

```bash
# Option A: Roll back to a previous Docker image (if using a registry)
# [TODO: Specify image registry and tagging strategy]
docker compose pull backend  # pull previous tag
docker compose up -d backend

# Option B: Roll back via git (local build)
git log --oneline -10          # identify last known-good commit
git checkout <commit-hash>
docker compose build backend frontend
docker compose up -d backend frontend

# Verify rollback
curl -f http://localhost:8000/health

# If Redis state is corrupted, flush it (WARNING: clears all sessions)
docker compose exec redis redis-cli FLUSHALL
docker compose restart backend
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | What to Monitor | Alert Threshold |
|---|---|---|
| Backend healthcheck | Docker healthcheck status | Any transition away from `healthy` |
| LLM latency | `[SPECIALIST]` and `[AGGREGATOR]` `time=` fields in backend logs | [TODO: Define SLO — e.g. >30s per specialist call?] |
| LLM token usage | `in=` / `out=` token counts per call | `out` approaching `specialist_max_tokens` (1500) or `aggregator_max_tokens` (8000) |
| Redis memory | `docker compose exec redis redis-cli INFO memory` → `used_memory_human` | [TODO: Set threshold based on expected session volume] |
| PostgreSQL connections | Active connections vs `max_connections` | [TODO: Define threshold] |
| Container restarts | `docker compose ps` — Restart count | Any container restarting unexpectedly |
| GitHub Actions | Workflow run status in Actions tab | Any workflow with status `failure` |

### Log Locations & Key Patterns

```bash
# Backend structured logs (most operational value)
docker compose logs backend -f

# Key log patterns to watch for:
# [CHAT]        — incoming request with session/model/mode
# [TOOL START]  — tool invocation begin
# [TOOL END]    — tool invocation complete with timing
# [SPECIALIST]  — per-category LLM call with token counts and latency
# [AGGREGATOR]  — final aggregation LLM call
# [ASSESSMENT]  — full assessment lifecycle

# Redis logs
docker compose logs redis -f

# PostgreSQL logs
docker compose logs postgres -f

# All services combined
docker compose logs -f --tail=100
```

### Alerting

[TODO: No alerting infrastructure (PagerDuty, CloudWatch, Datadog, etc.) is evident in the codebase. Recommend instrumenting the `/health` endpoint and setting up an external uptime monitor.]

[TODO: Should GitHub Actions failures trigger email/Slack alerts? `NOTIFY_EMAIL` is set to `kylo.deng@capco.com` in workflow files — confirm whether SendGrid is active in production.]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: Add on-call contact] | Service down, healthcheck failing, cannot restart container |
| L2 | Backend/ML Engineer | [TODO: Add backend engineer contact] | LLM assessment producing incorrect results, SQLite DB issues, Redis checkpoint corruption |
| L3 | Solution Owner | [TODO: Add solution owner] | LLM API key expired/quota exhausted, data breach concern, regulatory/compliance issue |
| External | Anthropic Support | https://console.anthropic.com/support | `claude-sonnet-4` / `claude-haiku-4` API outages |
| External | Google Cloud Support | [TODO: GCP support link] | Gemini API outages |
| Repo Owner | Kylo Deng | kylo.deng@capco.com | Escalation for CI/CD pipeline issues, secrets rotation |

---

## 7. Useful Commands

```bash
# ── Docker Compose ──────────────────────────────────────────────────────────

# Start all services
docker compose up -d

# Stop all services
docker compose down

# Restart a single service
docker compose restart backend

# Rebuild and restart backend after code change
docker compose build backend && docker compose up -d backend

# View live logs for all services
docker compose logs -f

# View live backend logs only
docker compose logs backend -f --tail=100

# Check service health status
docker compose ps

# ── Health & Smoke Tests ────────────────────────────────────────────────────

# Backend health check
curl -f http://localhost:8000/health

# Send a test chat message (fast mode)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the risk profile for CUST00000001?",
    "temperature": 0.3,
    "session_id": "ops-test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# ── Redis ───────────────────────────────────────────────────────────────────

# Ping Redis
docker compose exec redis redis-cli ping

# List all session checkpoint keys
docker compose exec redis redis-cli KEYS "*"

# Inspect memory usage
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Delete a specific session checkpoint (replace <key> with actual key)
docker compose exec redis redis-cli DEL <key>

# Flush ALL sessions (WARNING: destructive — clears all conversation state)
docker compose exec redis redis-cli FLUSHALL

# ── PostgreSQL ──────────────────────────────────────────────────────────────

# Check postgres is accepting connections
docker compose exec postgres pg_isready -U chainlit

# Connect to the chainlit database
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# ── SQLite Databases ────────────────────────────────────────────────────────

# List mounted database files in backend container
docker compose exec backend ls -lh /data/

# Query customer profile DB (example)
docker compose exec backend sqlite3 /data/customer_profile.db \
  "SELECT * FROM customer_profile WHERE customer_id='CUST00000001' LIMIT 1;"

# ── GitHub Actions (requires gh CLI) ───────────────────────────────────────

# List recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# View logs for a specific run
gh run view <run-id> --log --repo kylodeng/underwriting_chatbot-main

# Manually trigger tech docs generation
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger code review (repo-wide)
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=repo

# ── Environment Variable Verification ──────────────────────────────────────

# Check which env vars are set inside the backend container
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS|SENDGRID"

# ── Config ──────────────────────────────────────────────────────────────────

# View current LLM config without restarting
docker compose exec backend cat /app/config.yml
```

---

*Runbook generated from source: `kylodeng/underwriting_chatbot-main`. Items marked `[TODO]` require human input — see §6 for owner.*