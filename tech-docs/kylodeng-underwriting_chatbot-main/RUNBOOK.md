# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform built for underwriters. It exposes a streaming chat API (FastAPI, port 8000) backed by a LangGraph agent that orchestrates calls to specialist LLM assessors (Claude Haiku/Sonnet via Anthropic, Gemini via Google). When an underwriter submits a query about a customer, the agent retrieves the customer profile from a SQLite database, optionally identifies lookalike customers via a pre-computed similarity dictionary, and runs a parallel multi-domain underwriting risk assessment (finance, health, life, etc.) that produces a structured `UnderwritingReport` with a risk classification (Preferred / Standard Plus / Standard / Substandard). Conversation state is persisted in Redis; a PostgreSQL database backs the Chainlit frontend session store. The full stack is defined in `docker-compose.yml` and runs as four containers: `redis`, `postgres`, `backend`, and `frontend`.

---

## 2. Health Checks

Run these checks to confirm the service is operational:

| Component | Check | Expected Result |
|---|---|---|
| Backend API | `GET http://localhost:8000/health` | `{"status": "ok"}` with HTTP 200 |
| Backend container | `docker compose ps backend` | State = `running (healthy)` |
| Redis | `docker compose exec redis redis-cli ping` | `PONG` |
| PostgreSQL | `docker compose exec postgres pg_isready -U chainlit` | `accepting connections` |
| Frontend | `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080` | HTTP 200 |
| LLM connectivity | Check backend logs for `[SPECIALIST]` or `[AGGREGATOR]` lines after a test chat | Token counts and timing appear without errors |
| Database files | `docker compose exec backend ls /data/*.db` | All four `.db` files present and non-zero |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container crashed or not yet started | 1. `docker compose ps` to check state. 2. `docker compose logs backend --tail=100` for traceback. 3. `docker compose restart backend`. |
| `ValueError: Unsupported or unconfigured model provider` | Missing or incorrect `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` in `.env` | 1. Verify `.env` exists in project root. 2. Confirm `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` are set and valid. 3. `docker compose up -d --force-recreate backend`. |
| Chat requests hang indefinitely | Redis connection failure (LangGraph checkpointer blocked) | 1. `docker compose ps redis` — confirm running. 2. `docker compose exec redis redis-cli ping`. 3. Confirm `REDIS_HOST=redis` is set in `docker-compose.yml` env. 4. `docker compose restart redis backend`. |
| `[TOOL START]` appears in logs but no `[TOOL END]` / request times out | Anthropic or Google API rate limit / timeout during LLM call | 1. Check Anthropic/Google API status pages. 2. `docker compose logs backend --tail=50` for HTTP 429 or 529 errors. 3. Retry request; consider switching `model` to `anthropic-fast` in chat request payload. |
| Frontend shows blank page or cannot connect to backend | `BACKEND_URL` misconfigured, or backend not healthy | 1. Confirm backend healthcheck passes: `docker compose ps`. 2. Check `BACKEND_URL=http://backend:8000` in `docker-compose.yml`. 3. `docker compose restart frontend`. |
| `UnderwritingReport` JSON parse error / empty assessment | Aggregator LLM exceeded `aggregator_max_tokens` (8000) or returned malformed output | 1. `docker compose logs backend` for `[AGGREGATOR]` token counts. 2. If output tokens near 8000, increase `aggregator_max_tokens` in `config.yml` and redeploy. 3. Check Anthropic model availability. |
| PostgreSQL connection error on frontend startup | Postgres container not ready or init script failed | 1. `docker compose logs postgres --tail=50`. 2. Confirm `postgres_data` volume is intact. 3. `docker compose restart postgres frontend`. |
| `FileNotFoundError` for `/data/*.db` | SQLite database files not mounted | 1. Confirm `./database/` directory exists and contains `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db`. 2. Check volume mounts in `docker-compose.yml`. 3. `docker compose down && docker compose up -d`. |
| Customer profile lookup returns empty / no lookalike results | `customer_similarity_dict.json` or SQLite DB missing/corrupt | 1. Verify `backend/tmp/customer_similarity_dict.json` is present. 2. `docker compose exec backend ls /data/` to confirm DB files. 3. Check `[TOOL START] get_customer_info` / `customer_lookalike` in logs for errors. |
| GitHub Actions workflows fail (`ANTHROPIC_API_KEY` missing) | Repository secrets not configured | 1. Go to repo **Settings → Secrets and variables → Actions**. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. 3. Re-run failed workflow. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker and Docker Compose installed (Docker Desktop ≥ 4.x or Docker Engine ≥ 24.x)
- `.env` file present in project root with all required variables (see §5)
- `./database/` directory populated with all four SQLite `.db` files
- `backend/tmp/customer_similarity_dict.json` present

### First-Time Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create and populate .env (copy from template if available)
cp .env.example .env          # [TODO: does a .env.example exist in the repo?]
# Edit .env and set all required variables (see §5 Environment Variables)

# 3. Build all images
docker compose build

# 4. Start infrastructure first (Redis + Postgres)
docker compose up -d redis postgres

# 5. Wait for postgres to be ready (~5s), then start backend
sleep 5
docker compose up -d backend

# 6. Wait for backend healthcheck to pass, then start frontend
docker compose up -d frontend

# 7. Verify all containers are healthy
docker compose ps

# 8. Confirm health endpoint
curl http://localhost:8000/health
```

### Routine Update Deployment

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild affected images (only backend/frontend if source changed)
docker compose build backend frontend

# 3. Rolling restart — start new containers before stopping old
docker compose up -d --no-deps backend
# Wait for healthcheck to pass
docker compose ps backend
docker compose up -d --no-deps frontend

# 4. Verify
curl http://localhost:8000/health
```

### Configuration Change Only (no code change)

```bash
# Edit config.yml or .env as needed, then:
docker compose up -d --force-recreate backend
```

### Rollback Steps

```bash
# 1. Identify the previous working image tag or git commit
git log --oneline -10

# 2. Check out the previous commit
git checkout <previous-commit-sha>

# 3. Rebuild and redeploy
docker compose build backend frontend
docker compose up -d --no-deps backend frontend

# 4. If the issue is a config change only (e.g. bad config.yml):
git checkout <previous-commit-sha> -- backend/config.yml
docker compose up -d --force-recreate backend

# 5. Verify rollback succeeded
curl http://localhost:8000/health
docker compose logs backend --tail=50
```

> **Note:** Redis conversation history will persist across restarts unless the Redis volume is removed. If corrupted state is suspected, flush Redis: `docker compose exec redis redis-cli FLUSHALL` (this clears all session memory).

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | How to Observe | Alert Threshold |
|---|---|---|
| Backend health | `GET /health` response | Any non-200 → page on-call |
| LLM response latency | `[SPECIALIST]` and `[AGGREGATOR]` time= fields in backend logs | Specialist > 30s or Aggregator > 60s → investigate |
| LLM token consumption | `in=` / `out=` tok fields in logs per request | Aggregator out tokens approaching 8000 → raise `aggregator_max_tokens` |
| Tool call duration | `[TOOL END] <name> time=` in backend logs | Any tool > 10s → check downstream API |
| Container health | `docker compose ps` | Any container not `healthy` → alert |
| Redis memory | `docker compose exec redis redis-cli INFO memory` | `used_memory_human` growing unbounded → configure `maxmemory` policy |
| Error rate | `docker compose logs backend` filtered for `ERROR` or `Exception` | Any unhandled exception → investigate |

### Log Locations

```bash
# Backend application logs (most diagnostic value)
docker compose logs backend -f

# Frontend logs
docker compose logs frontend -f

# Redis logs
docker compose logs redis -f

# Postgres logs
docker compose logs postgres -f

# Filter for LLM timing data
docker compose logs backend | grep -E '\[(SPECIALIST|AGGREGATOR|TOOL)\]'

# Filter for errors only
docker compose logs backend | grep -iE '(error|exception|traceback)'
```

### GitHub Actions Monitoring

- Navigate to **Actions** tab in the repository to monitor CI/CD workflow runs.
- Workflow artifacts (code review JSON, test reports) are uploaded on every run and retained per GitHub's default retention policy.
- [TODO: Should workflow failure notifications be sent to a Slack channel or email distribution list beyond `kylo.deng@capco.com`?]

### What to Alert On

- `/health` endpoint returning non-200 for > 1 minute
- Any container exiting with a non-zero code (`docker compose ps` shows `Exit 1`)
- Anthropic API returning HTTP 429 (rate limit) or 529 (overloaded) repeatedly
- Redis not responding to `PING`
- Backend logs containing `Traceback` during active business hours

> [TODO: No APM, distributed tracing, or centralised logging (e.g. Datadog, Azure Monitor, CloudWatch) is configured in the repo. Recommend instrumenting before production use.]

---

## 6. Escalation Path

| Level | Who | When to Escalate | Contact |
|---|---|---|---|
| L1 — First Response | On-call Engineer | Service down, health check failing, container crash | [TODO: fill in on-call rotation contact] |
| L2 — Backend/ML | Backend Engineer / ML Engineer | LLM errors, assessment logic failures, model output issues | [TODO: fill in backend team contact] |
| L3 — Infrastructure | DevOps / Platform Engineer | Redis/Postgres data loss, Docker host issues, networking | [TODO: fill in infrastructure team contact] |
| L4 — Vendor | Anthropic Support | Persistent API 5xx errors from Anthropic | [TODO: Anthropic support contract / portal URL] |
| L4 — Vendor | Google Cloud Support | Persistent Gemini API failures | [TODO: GCP support case URL] |
| Product Owner | [TODO: fill in name] | Business-impacting decisions, major feature failures | [TODO: fill in PO contact] |
| Security | [TODO: fill in name] | API key exposure, data breach, unauthorised access | [TODO: fill in security team contact] |

---

## 7. Useful Commands

### Container Management

```bash
# Start all services
docker compose up -d

# Stop all services (data preserved)
docker compose down

# Stop all services and remove volumes (DATA LOSS — use with caution)
docker compose down -v

# Restart a single service
docker compose restart backend

# Force recreate a service (picks up env changes)
docker compose up -d --force-recreate backend

# View status of all containers
docker compose ps

# Follow live logs for all services
docker compose logs -f

# Follow backend logs only
docker compose logs backend -f --tail=100
```

### Health & Diagnostics

```bash
# Check backend health
curl -s http://localhost:8000/health | python3 -m json.tool

# Send a test chat message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Give me a risk assessment for customer CUST00000001", "temperature": 0.3, "session_id": "test-001", "model": "anthropic-fast", "mode": "fast"}'

# Check Redis connectivity and memory
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Flush all Redis session state (CAUTION: clears all conversation history)
docker compose exec redis redis-cli FLUSHALL

# Check Postgres connectivity
docker compose exec postgres psql -U chainlit -c "\dt"

# Verify database files are mounted and accessible
docker compose exec backend ls -lh /data/
```

### Log Filtering

```bash
# Show only LLM timing lines
docker compose logs backend | grep -E '\[(SPECIALIST|AGGREGATOR)\]'

# Show only tool start/end events
docker compose logs backend | grep -E '\[TOOL (START|END)\]'

# Show errors and exceptions
docker compose logs backend 2>&1 | grep -iE '(error|exception|traceback|critical)'

# Show all chat session starts
docker compose logs backend | grep '\[CHAT\]'
```

### Image & Build

```bash
# Rebuild backend image from scratch (no cache)
docker compose build --no-cache backend

# Rebuild all images
docker compose build --no-cache

# Check image sizes
docker images | grep underwriting
```

### Config & Environment

```bash
# Validate docker-compose syntax
docker compose config

# Check which env vars are loaded in backend container
docker compose exec backend env | grep -E '(ANTHROPIC|GOOGLE|REDIS|OPENAI)'

# View current LLM config
docker compose exec backend cat /app/config.yml
# [TODO: confirm the working directory path inside the backend container]
```