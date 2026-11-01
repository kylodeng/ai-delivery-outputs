# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that helps underwriters assess customer risk through a conversational interface. The system exposes a FastAPI backend (port 8000) that streams LLM-generated responses via Server-Sent Events (SSE), orchestrating a LangGraph agent pipeline backed by Anthropic Claude (Haiku for speed, Sonnet for depth). When asked to assess a customer, the agent sequentially calls tools — `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment` — the last of which fans out to parallel specialist LLM calls (finance, health, life, etc.) before aggregating results into a structured `UnderwritingReport`. Conversation state is checkpointed in Redis, customer data is served from SQLite databases mounted read-only, and the Chainlit-based frontend (port 8080) connects to a PostgreSQL database for session persistence. The full stack is containerised and orchestrated with Docker Compose.

---

## 2. Health Checks

### Backend API

```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Docker container status

```bash
docker compose ps
# All four services should show "running" / "healthy"
```

### Redis connectivity

```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### PostgreSQL connectivity

```bash
docker compose exec postgres pg_isready -U chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### Backend container health (Docker native)

```bash
docker inspect underwriting_chatbot-backend-1 \
  --format='{{.State.Health.Status}}'
# Expected: healthy
```

### Frontend reachability

```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### End-to-end smoke test

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}' \
  | head -c 200
# Expected: SSE stream beginning with event: data lines
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container stuck in `starting` / never becomes `healthy` | Missing or invalid `.env` file; `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` not set | 1. Check `docker compose logs backend`. 2. Verify `.env` exists in repo root and contains all required keys. 3. `docker compose up --force-recreate backend`. |
| `curl /health` returns `connection refused` | Backend process crashed at startup (import error, missing dependency, bad config) | 1. `docker compose logs backend --tail 100`. 2. Look for Python traceback. 3. Fix the config/dependency and `docker compose restart backend`. |
| `Redis` connection error in backend logs (`ConnectionRefusedError`) | Redis container not running or `REDIS_HOST` env var not set correctly | 1. `docker compose ps redis`. 2. If stopped: `docker compose start redis`. 3. Confirm `REDIS_HOST=redis` in environment (docker-compose.yml already sets this). |
| Chat responses hang / never stream | Anthropic API key invalid, rate-limited, or network egress blocked | 1. Check backend logs for `AuthenticationError` or `RateLimitError`. 2. Validate key at https://console.anthropic.com. 3. Check network/firewall rules for outbound HTTPS to `api.anthropic.com`. |
| `run_underwriting_assessment` returns empty or truncated report | `aggregator_max_tokens` (8000) exceeded or specialist LLMs hitting `specialist_max_tokens` (1500) cap | 1. Review backend logs for token usage lines `[AGGREGATOR]`. 2. Increase `aggregator_max_tokens` in `backend/config.yml`. 3. `docker compose restart backend`. |
| Frontend shows blank page or fails to load | `BACKEND_URL` misconfigured; backend not yet healthy when frontend started; PostgreSQL unavailable | 1. `docker compose logs frontend`. 2. Confirm backend is healthy first. 3. `docker compose restart frontend`. |
| `customer_lookalike` or `get_customer_profile` tool returns no data | SQLite `.db` files missing from `./database/` or mounted at wrong path | 1. Verify files exist: `ls -lh database/*.db`. 2. Check volume mounts in `docker-compose.yml`. 3. Confirm the container sees the files: `docker compose exec backend ls /data/`. |
| PostgreSQL init fails / `chainlit` database missing | `postgres/init.sql` not found or permissions issue | 1. `docker compose logs postgres`. 2. Verify `./postgres/init.sql` exists. 3. Remove the volume and reinitialise: `docker compose down -v && docker compose up -d`. **Warning: destroys session history.** |
| GitHub Actions workflow fails: `ModuleNotFoundError: anthropic` | Workflow pip install step skipped or cached incorrectly | 1. Re-run the workflow. 2. Check the `pip install anthropic requests` step log. 3. Ensure `ANTHROPIC_API_KEY` secret is set in the repo. |
| GitHub Actions: `KeyError: 'SENDGRID_API_KEY'` | `SENDGRID_API_KEY` secret not configured in repository secrets | 1. Go to Settings → Secrets → Actions. 2. Add `SENDGRID_API_KEY`. 3. Re-run the workflow. |
| Model returns `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request contains an unrecognised string | 1. Valid values: `anthropic`, `anthropic-fast`, `gemini`. 2. Check frontend is sending a supported model name. 3. `azure` and `openai` are defined but not yet configured (`None`). |
| Duplicate chart events streamed to client | `_charts_sent` set is per-process and lost on restart; session reused after restart | 1. This is a known in-memory limitation (noted in `graph.py` TODO). 2. Restart the session (new `session_id`). Permanent fix requires Redis-backed chart tracking. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24 and Docker Compose v2 installed
- `.env` file present in repo root with all required variables (see §5)
- SQLite database files present under `./database/`
- `./postgres/init.sql` present

### Step-by-step Deployment

**Step 1 — Clone / pull latest code**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
# or, for updates:
git pull origin main
```

**Step 2 — Verify environment file**
```bash
cp .env.example .env        # [TODO: confirm .env.example exists or document all required vars]
# Edit .env and populate all values (see §5 for required variables)
```

**Step 3 — Build images**
```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
sleep 5   # allow postgres to initialise
```

**Step 5 — Start backend and wait for healthy**
```bash
docker compose up -d backend
# Poll until healthy (up to 60s)
until [ "$(docker inspect underwriting_chatbot-backend-1 --format='{{.State.Health.Status}}')" = "healthy" ]; do
  echo "Waiting for backend..."; sleep 5
done
echo "Backend healthy"
```

**Step 6 — Start frontend**
```bash
docker compose up -d frontend
```

**Step 7 — Verify deployment**
```bash
docker compose ps
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

**Step 8 — Run smoke test**
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"What can you help me with?","temperature":0.3,"session_id":"deploy-smoke","model":"anthropic-fast","mode":"fast"}' \
  | head -c 500
```

---

### Rollback Steps

**Option A — Revert to previous image (if images are tagged)**
```bash
# [TODO: confirm image registry and tagging strategy]
docker compose down
git checkout <previous-commit-sha>
docker compose build
docker compose up -d
```

**Option B — Quick rollback via git + rebuild**
```bash
docker compose down
git log --oneline -10      # identify last known good commit
git checkout <commit-sha>
docker compose build --no-cache
docker compose up -d redis postgres
sleep 5
docker compose up -d backend frontend
```

**Option C — Rollback config only (no code change)**
```bash
# Edit backend/config.yml to revert LLM/token settings
docker compose restart backend
```

> ⚠️ `docker compose down -v` will **destroy** PostgreSQL session data. Avoid unless reinitialising from scratch.

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Threshold / Action |
|---|---|---|
| Backend health check | `GET /health` every 10s (Docker healthcheck) | If `unhealthy` for >3 retries → restart container |
| LLM token usage — specialist | Backend stdout: `[SPECIALIST] ... in=X tok out=Y tok time=Zs` | `out` consistently near 1500 → hitting cap; increase `specialist_max_tokens` |
| LLM token usage — aggregator | Backend stdout: `[AGGREGATOR] ... in=X tok out=Y tok` | `out` near 8000 → increase `aggregator_max_tokens` |
| Assessment latency | Backend stdout: `[TOOL END] run_underwriting_assessment time=Xs` | >30s → investigate LLM or network latency |
| Redis memory | `docker compose exec redis redis-cli info memory` | Watch `used_memory_human`; [TODO: define threshold] |
| PostgreSQL connections | `docker compose exec postgres psql -U chainlit -c "SELECT count(*) FROM pg_stat_activity;"` | [TODO: define max connections threshold] |

### Logs to Watch

```bash
# Backend application logs (tool calls, LLM usage, errors)
docker compose logs -f backend

# All services combined
docker compose logs -f

# Filter for errors only
docker compose logs backend 2>&1 | grep -i "error\|exception\|traceback"

# GitHub Actions workflow runs
# Navigate to: https://github.com/kylodeng/underwriting_chatbot-main/actions
```

### Log Patterns Indicating Problems

| Log Pattern | Meaning |
|---|---|
| `AuthenticationError` | Invalid Anthropic or Google API key |
| `RateLimitError` | API rate limit hit; may need backoff or quota increase |
| `ConnectionRefusedError` | Redis or Postgres not reachable |
| `KeyError: 'ANTHROPIC_API_KEY'` | Environment variable missing |
| `ValueError: Unsupported or unconfigured model provider` | Bad model name in request |
| `[TOOL START]` without subsequent `[TOOL END]` | Tool call hung; check LLM API |

### Alerting

> [TODO: Is there a PagerDuty, Datadog, or other alerting platform configured? No alerting infrastructure was found in the codebase beyond Docker healthchecks.]

> [TODO: Should the `NOTIFY_EMAIL` (kylo.deng@capco.com) used in GitHub Actions workflows also receive runtime alerts?]

Recommended minimum alerts to configure:
- Backend healthcheck failure (Docker or external probe)
- Anthropic API error rate > 5% over 5 minutes
- Container OOM / crash restart

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: add on-call contact / PagerDuty rotation] | Service down, health check failing |
| L2 | Backend / ML Lead | [TODO: add contact] | LLM quality issues, assessment errors, model config changes |
| L3 | Platform / Infrastructure | [TODO: add contact] | Redis/Postgres data loss, Docker host failure |
| L4 | Anthropic Support | https://support.anthropic.com | API outage, billing, model availability |
| L5 | Security / Compliance | [TODO: add contact] | Any suspected data breach or PII exposure |

> [TODO: Is there a Slack channel for incidents? An incident management tool (e.g. PagerDuty, OpsGenie)?]

---

## 7. Useful Commands

### Service Lifecycle

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Restart a single service
docker compose restart backend

# Rebuild and restart backend after code change
docker compose build backend && docker compose up -d --no-deps backend

# View real-time logs for all services
docker compose logs -f

# View logs for a specific service
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f redis
docker compose logs -f postgres
```

### Health & Status

```bash
# Check container status
docker compose ps

# Backend health endpoint
curl -f http://localhost:8000/health

# Backend health status from Docker
docker inspect underwriting_chatbot-backend-1 \
  --format='{{.State.Health.Status}}'

# Redis ping
docker compose exec redis redis-cli ping

# PostgreSQL readiness
docker compose exec postgres pg_isready -U chainlit

# Redis memory info
docker compose exec redis redis-cli info memory

# Active PostgreSQL connections
docker compose exec postgres psql -U chainlit -c \
  "SELECT count(*) FROM pg_stat_activity;"
```

### Database Inspection

```bash
# List SQLite databases
ls -lh database/*.db

# Inspect customer profiles (read-only)
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db \
  "SELECT * FROM customers LIMIT 5;"   # [TODO: verify actual table name]

# Connect to PostgreSQL (Chainlit sessions)
docker compose exec postgres psql -U chainlit -d chainlit
```

### LLM / API Testing

```bash
# Basic chat smoke test (fast mode)
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me about customer CUST00000001",
    "temperature": 0.3,
    "session_id": "ops-test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Deep assessment mode
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run a full risk assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "ops-test-002",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### GitHub Actions Workflows

```bash
# Trigger code review manually (via GitHub CLI)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Trigger tech docs generation
gh workflow run tool2_tech_docs.yml

# Trigger UAT test pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0

# View recent workflow runs
gh run list --limit 10
```

### Cleanup

```bash
# Remove stopped containers and unused images (safe)
docker system prune -f

# Full reset including volumes (DESTROYS PostgreSQL data)
docker compose down -v
docker system prune -af

# Flush Redis (clears all conversation checkpoints)
docker compose exec redis redis-cli FLUSHALL
```

---

> **Document status:** Auto-assisted draft. Items marked `[TODO:]` require input from the engineering team before this runbook is considered complete for production use.
>
>