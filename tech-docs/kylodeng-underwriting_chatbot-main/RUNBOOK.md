# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a containerised, AI-assisted life insurance underwriting platform consisting of a FastAPI backend and a frontend chat interface (technology [TODO: confirm — Chainlit assumed from `DATABASE_URL` environment variable pattern]). Underwriters interact with a LangGraph-powered conversational agent that orchestrates three tools: customer profile lookup, a customer lookalike search, and a parallel multi-specialist underwriting risk assessment. The assessment pipeline fans out across multiple LLM-backed specialist agents (finance, health, life, etc.) and aggregates their outputs into a structured `UnderwritingReport` with a risk classification (`Preferred` → `Substandard`). Supporting infrastructure includes a Redis instance for LangGraph conversation checkpointing, a PostgreSQL database for the frontend session persistence, and three SQLite databases mounted read-only for customer, feature-importance, and model-prediction data. All LLM calls are routed through the Anthropic API (Claude Haiku as the default fast model, Claude Sonnet for deeper analysis) with optional Google Gemini support.

---

## 2. Health Checks

### 2.1 Backend API

```bash
curl -sf http://localhost:8000/health
# Expected: {"status": "ok"}  HTTP 200
```

### 2.2 Docker Compose Service Status

```bash
docker compose ps
# All four services (redis, postgres, backend, frontend) should show status: running (healthy)
```

### 2.3 Redis Connectivity

```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### 2.4 PostgreSQL Connectivity

```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 2.5 Backend Container Health (Docker native)

```bash
docker inspect underwriting_chatbot-backend-1 \
  --format='{{.State.Health.Status}}'
# Expected: healthy
```

### 2.6 Frontend Reachability

```bash
curl -sf http://localhost:8080
# Expected: HTTP 200 with HTML body
```

### 2.7 LLM API Key Validity

```bash
# Quick smoke test — fires one minimal Anthropic API call
docker compose exec backend python - <<'EOF'
import os, anthropic
c = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
r = c.messages.create(model="claude-haiku-4-5-20251001",
    max_tokens=10, messages=[{"role":"user","content":"ping"}])
print("Anthropic OK:", r.content[0].text)
EOF
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container stuck in `starting` / health check failing | Redis not yet ready when backend starts; or `ANTHROPIC_API_KEY` missing from `.env` | 1. Check `docker compose logs backend`. 2. Confirm Redis is `healthy`: `docker compose ps redis`. 3. Verify `.env` contains all required keys. 4. `docker compose restart backend`. |
| `{"detail": "Internal Server Error"}` on `/chat` endpoint | Unhandled exception in agent or LLM call; see backend logs | 1. `docker compose logs --tail 100 backend`. 2. Check for `ANTHROPIC_API_KEY` expiry or rate-limit. 3. Check Redis reachability (`redis-cli ping`). 4. Restart backend: `docker compose restart backend`. |
| Agent returns empty or truncated response | `aggregator_max_tokens` (8000) exceeded or LLM API timeout | 1. Check backend logs for token usage lines (`[AGGREGATOR]`). 2. Increase `aggregator_max_tokens` in `backend/config.yml`. 3. Retry request; intermittent API timeouts will self-resolve. |
| `ValueError: Unsupported or unconfigured model provider` | Frontend passed a `model` value not in `LLMS.model_mapper`; or `GOOGLE_API_KEY` missing for Gemini | 1. Confirm valid values: `anthropic`, `anthropic-fast`, `gemini`. 2. Check `.env` for the relevant API key. 3. If Gemini is unused, remove it from model options in frontend. |
| Redis `WRONGTYPE` or checkpoint errors in logs | Redis key namespace collision; leftover state from previous version | 1. `docker compose exec redis redis-cli FLUSHDB` (**development only — clears all session history**). 2. In production, key-prefix the checkpointer [TODO: confirm Redis key prefix strategy]. |
| Frontend fails to load / shows DB errors | PostgreSQL not initialised or `init.sql` not applied | 1. `docker compose logs postgres`. 2. Check `./postgres/init.sql` exists. 3. Destroy and recreate volume: `docker compose down -v && docker compose up -d`. |
| SQLite `unable to open database file` | Database files not present at expected mount paths | 1. Confirm all three `.db` files exist under `./database/`. 2. Verify volume mounts in `docker-compose.yml`. 3. Check file permissions: `ls -lh ./database/`. |
| `ModuleNotFoundError` on backend startup | Python dependency missing from container image | 1. `docker compose logs backend`. 2. Rebuild image: `docker compose build --no-cache backend`. 3. Restart: `docker compose up -d backend`. |
| GitHub Actions workflow fails — `ANTHROPIC_API_KEY` not set | Secret not configured in repository Settings | 1. Go to repo → Settings → Secrets and variables → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| Assessment returns `data_gaps` for all categories | Customer profile not found or `get_customer_profile` tool returned empty result | 1. Confirm customer ID exists in `customer_profile.db`. 2. Check tool logs: `[TOOL START] get_customer_info`. 3. Query DB directly (see Useful Commands). |
| Streaming chat responses stop mid-sentence | SSE connection dropped; `EventSourceResponse` timeout | 1. Check proxy/load balancer timeout settings [TODO: confirm infrastructure in front of backend]. 2. Check Anthropic API status: https://status.anthropic.com. 3. Retry request. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose v2 installed
- `.env` file populated (see §4.1)
- `./database/*.db` files present
- `./postgres/init.sql` present

### 4.1 Environment Variables

Create `.env` in the repo root:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...           # Only required if using Gemini model
REDIS_HOST=redis             # Default; change for external Redis
# [TODO: document any additional frontend-specific env vars]
```

### 4.2 First-Time Deployment

```bash
# 1. Clone repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create and populate .env
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env with real API keys

# 3. Build images
docker compose build

# 4. Start all services
docker compose up -d

# 5. Wait for healthy status (up to ~30 seconds)
docker compose ps

# 6. Smoke test
curl -sf http://localhost:8000/health
```

### 4.3 Application Update (Rolling)

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild affected services (example: backend changed)
docker compose build backend

# 3. Restart with zero-downtime (one service at a time)
docker compose up -d --no-deps backend
docker compose up -d --no-deps frontend

# 4. Verify health
docker compose ps
curl -sf http://localhost:8000/health
```

### 4.4 Config-Only Change (e.g. `config.yml` token limits)

```bash
# config.yml is baked into the image — rebuild is required
docker compose build backend
docker compose up -d --no-deps backend
```

### 4.5 Rollback Procedure

```bash
# Option A — Roll back to previous Docker image tag (if images are tagged)
# [TODO: confirm image registry and tagging strategy]
docker compose stop backend
docker tag <registry>/underwriting-backend:<previous-tag> underwriting-backend:latest
docker compose up -d --no-deps backend

# Option B — Git revert and rebuild
git log --oneline -10             # identify the last good commit
git revert HEAD                   # or: git checkout <good-commit-sha>
docker compose build backend
docker compose up -d --no-deps backend

# Option C — Full teardown and redeploy (last resort; loses Redis session state)
docker compose down
docker compose up -d
```

> ⚠️ **Data warning:** `docker compose down -v` will destroy the `postgres_data` volume (frontend session history). Do not use `-v` unless you intend to wipe state.

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Source | Alert Threshold |
|---|---|---|
| Backend HTTP 5xx rate | Backend logs / reverse proxy | > 1% of requests |
| `/chat` endpoint latency (P95) | Backend logs (`[CHAT]` lines) | > 30 seconds |
| Specialist LLM call time per category | `[SPECIALIST]` log lines | > 15 seconds per category |
| Aggregator LLM call time | `[AGGREGATOR]` log lines | > 20 seconds |
| Anthropic token usage (output) | `[SPECIALIST]` / `[AGGREGATOR]` log lines | Output tokens approaching `specialist_max_tokens` (1500) or `aggregator_max_tokens` (8000) |
| Redis memory usage | `redis-cli INFO memory` | > 80% of `maxmemory` |
| Docker container health status | `docker compose ps` | Any service not `healthy` |
| GitHub Actions workflow failures | GitHub Actions UI / email | Any failed run |

### 5.2 Log Patterns to Monitor

```
# Backend structured log patterns (stdout of backend container)
[CHAT]        - new chat request (includes session_id, model, mode, message preview)
[TOOL START]  - tool invocation begins
[TOOL END]    - tool invocation complete (includes elapsed time)
[SPECIALIST]  - per-category LLM call metrics (tokens in/out, time)
[AGGREGATOR]  - final aggregation LLM call metrics
[ASSESSMENT]  - full assessment start
```

### 5.3 Log Access Commands

```bash
# Live backend logs
docker compose logs -f backend

# Last 200 lines with timestamps
docker compose logs --tail 200 --timestamps backend

# Filter for errors only
docker compose logs backend 2>&1 | grep -i "error\|exception\|traceback"

# Filter for LLM timing metrics
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL"
```

### 5.4 Alerting

[TODO: No alerting infrastructure is defined in the codebase. Recommend: what is the target platform — Prometheus + Grafana, Datadog, Azure Monitor, or CloudWatch?]

[TODO: Should GitHub Actions failure notifications go beyond the default email to `kylo.deng@capco.com`? Define a team-level alert channel (e.g. Slack webhook).]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: engineer name / PagerDuty rotation] | Service down, health check failing > 5 min |
| L2 | Tech Lead | [TODO: tech lead name / contact] | L1 unable to restore within 15 min; data integrity concerns |
| L3 | Platform / MLOps | [TODO: platform team contact] | LLM provider outage, Redis data loss, database corruption |
| L4 | Anthropic Support | https://support.anthropic.com | API key issues, sustained rate-limiting, model deprecation |
| L4 | Google Cloud Support | [TODO: GCP support link] | Gemini API sustained outage |
| Business | Solution Owner | [TODO: product owner name] | Customer-facing impact > 30 min; data breach suspicion |

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Restart a single service
docker compose restart backend

# View running services and health
docker compose ps

# Rebuild and restart backend after code change
docker compose build backend && docker compose up -d --no-deps backend
```

### Logs

```bash
# Stream all service logs
docker compose logs -f

# Stream backend logs only
docker compose logs -f backend

# Last 500 lines from all services
docker compose logs --tail 500

# Search for Python tracebacks
docker compose logs backend 2>&1 | grep -A 10 "Traceback"
```

### Redis Operations

```bash
# Check Redis is alive
docker compose exec redis redis-cli ping

# List all stored keys (caution: slow on large keyspaces)
docker compose exec redis redis-cli KEYS '*'

# Check memory usage
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Flush all session state (DEVELOPMENT ONLY)
docker compose exec redis redis-cli FLUSHDB
```

### PostgreSQL Operations

```bash
# Connect to chainlit database
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit -c '\dt'

# Check row count in a table (replace <tablename>)
docker compose exec postgres psql -U chainlit -d chainlit \
  -c 'SELECT COUNT(*) FROM <tablename>;'
```

### SQLite Database Queries

```bash
# List available customers (customer_profile.db)
docker compose exec backend sqlite3 /data/customer_profile.db \
  "SELECT customer_id, name FROM customer_profile LIMIT 10;"
# [TODO: confirm actual table/column names in customer_profile.db]

# Check model predictions for a customer
docker compose exec backend sqlite3 /data/model_predictions.db \
  "SELECT * FROM predictions WHERE customer_id = 'CUST00000001';"
# [TODO: confirm actual table/column names in model_predictions.db]
```

### API Smoke Tests

```bash
# Health check
curl -sf http://localhost:8000/health | python3 -m json.tool

# Send a test chat message (streaming — will output SSE events)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get profile for CUST00000001",
    "temperature": 0.3,
    "session_id": "runbook-test",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### GitHub Actions — Manual Workflow Triggers

```bash
# Trigger code review on a PR (requires GitHub CLI)
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# Generate tech documentation
gh workflow run tool2_tech_docs.yml

# Generate UAT test pack
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0
```

### Environment Validation

```bash
# Confirm all required env vars are set inside the backend container
docker compose exec backend env | grep -E \
  "ANTHROPIC_API_KEY|GOOGLE_API_KEY|REDIS_HOST"

# Confirm LLM config is loaded correctly
docker compose exec backend python -c "
import yaml
with open('config.yml') as f:
    cfg = yaml.safe_load(f)
print(cfg)
"
```

---

*Runbook auto-generated — last reviewed: [TODO: insert review date]. Owner: [TODO: insert team name]. Next scheduled review: [TODO].*