# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that exposes a streaming chat API backed by a LangGraph agent. An underwriter submits natural-language questions about a customer; the agent orchestrates tool calls (customer profile lookup, customer lookalike matching, and a multi-specialist LLM risk assessment pipeline) and streams a structured `UnderwritingReport` back to the frontend. The system is composed of four Docker containers — a **FastAPI backend** (port 8000), a **Chainlit frontend** (port 8080), a **Redis** instance used for LangGraph conversation checkpointing (port 6379), and a **PostgreSQL** database used by Chainlit for session/user data (port 5432). Customer profiles and model predictions are served from read-only SQLite databases mounted into the backend container. The backend connects to external LLM providers (Anthropic Claude, Google Gemini) and a set of GitHub Actions CI workflows provide automated code review, documentation generation, test generation, and UAT facilitation.

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
# All four services should show: Status = Up, health = healthy (backend)
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

### Frontend reachability
```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### Backend container health (Docker-native)
Docker performs its own health check every 10 s with a 5-retry, 15 s start-period grace:
```bash
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{.State.Health.Status}}'
# Expected: healthy
```

### LLM provider reachability
```bash
# Anthropic
curl -s -o /dev/null -w "%{http_code}" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  https://api.anthropic.com/v1/models
# Expected: 200

# Google Gemini
curl -s -o /dev/null -w "%{http_code}" \
  "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY"
# Expected: 200
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `curl /health` returns non-200 or connection refused | Backend container crashed or failed its health check | 1. `docker compose logs backend --tail 100` to identify exception. 2. `docker compose restart backend`. 3. If persists, check `.env` for missing required secrets. |
| Chat returns `ValueError: Unsupported or unconfigured model provider` | `model` parameter in request does not match a key in `LLMS.model_mapper`; or API key env var is missing | 1. Verify the request sends one of: `anthropic`, `anthropic-fast`, `gemini`. 2. Confirm `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` are set in `.env`. 3. `docker compose exec backend env \| grep API_KEY` to verify injection. |
| LLM calls return 401 / AuthenticationError | Invalid or expired API key for Anthropic or Google | 1. Rotate the relevant API key at the provider console. 2. Update `.env`. 3. `docker compose up -d --force-recreate backend`. |
| LLM calls return 429 / RateLimitError | Token-per-minute or request-per-minute quota exceeded | 1. Check provider usage dashboard. 2. Reduce `specialist_max_tokens` / `aggregator_max_tokens` in `config.yml` temporarily. 3. Consider switching `default` model in `config.yml` to `anthropic-fast` for lower-cost tier. |
| Agent hangs and never returns a final answer | `pending_call` loop not resolving; tool raised unhandled exception | 1. `docker compose logs backend -f` to find the exception. 2. Check tool function logs for `[TOOL START]` without a matching `[TOOL END]`. 3. Restart backend: `docker compose restart backend`. |
| Redis `ECONNREFUSED` / `ConnectionError` in backend logs | Redis container not running, or `REDIS_HOST` env var wrong | 1. `docker compose up -d redis`. 2. Confirm `REDIS_HOST=redis` in `docker-compose.yml` environment block. 3. `docker compose restart backend` after Redis is healthy. |
| LangGraph checkpoint errors / session state lost | Redis lost data (container restart without persistent volume) | 1. Redis is currently ephemeral (no volume defined — see TODO in `graph.py`). Session history will be lost on Redis restart — this is a known limitation. 2. For immediate workaround: instruct users to start a new `session_id`. 3. Long-term: add Redis volume or migrate to Azure Cache for Redis (see `graph.py` TODO). |
| PostgreSQL `connection refused` | Postgres container not running or init failed | 1. `docker compose logs postgres --tail 50`. 2. `docker compose up -d postgres`. 3. If init failed: `docker compose down -v && docker compose up -d` (⚠️ destroys Chainlit data). |
| Frontend returns 502 / blank screen | Backend unhealthy at frontend startup (frontend `depends_on: backend: condition: service_healthy`) | 1. Fix backend first. 2. `docker compose up -d frontend`. |
| SQLite database not found in backend | Volume mount path mismatch between host and container | 1. Confirm files exist: `ls ./database/*.db`. 2. Check `docker-compose.yml` volume mounts match actual file paths. 3. `docker compose restart backend`. |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY not set` | Secret missing from repository settings | 1. Go to repo → Settings → Secrets and variables → Actions. 2. Add/update `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. 3. Re-run failed workflow. |
| Assessment returns empty or malformed `UnderwritingReport` | Aggregator LLM exceeded `aggregator_max_tokens` (8000) or returned non-structured output | 1. Check backend logs for `[AGGREGATOR]` token counts. 2. Increase `aggregator_max_tokens` in `config.yml` if output tokens are being truncated. 3. `docker compose restart backend` after config change. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker Engine ≥ 24 and Docker Compose V2 installed
- `.env` file present in repo root with all required variables (see Section 5)
- SQLite database files present under `./database/`

### Step-by-step deployment

**Step 1 — Clone or pull latest code**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
# OR, for updates:
git pull origin main
```

**Step 2 — Create / verify `.env`**
```bash
cp .env.example .env   # [TODO: confirm .env.example exists in repo]
# Edit .env and populate all required variables (see Section 5)
```

**Step 3 — Build images**
```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**
```bash
docker compose up -d redis postgres
# Wait for postgres to be ready
sleep 10
docker compose exec postgres pg_isready -U chainlit
```

**Step 5 — Start backend and verify healthy**
```bash
docker compose up -d backend
# Poll health until healthy (up to ~60 s)
for i in $(seq 1 12); do
  STATUS=$(docker inspect underwriting_chatbot-main-backend-1 \
    --format='{{.State.Health.Status}}' 2>/dev/null)
  echo "[$i] backend health: $STATUS"
  [ "$STATUS" = "healthy" ] && break
  sleep 5
done
```

**Step 6 — Start frontend**
```bash
docker compose up -d frontend
```

**Step 7 — Smoke test**
```bash
curl -f http://localhost:8000/health
curl -f http://localhost:8080
```

**Step 8 — Confirm all containers running**
```bash
docker compose ps
```

---

### Rollback procedure

**Option A — Roll back to previous Docker image (if tagged)**
```bash
# Stop current deployment
docker compose down

# Edit docker-compose.yml to pin backend/frontend image to previous tag
# [TODO: confirm whether images are tagged and pushed to a registry]

docker compose up -d
```

**Option B — Roll back via Git**
```bash
# Identify last known good commit
git log --oneline -10

# Check out previous commit
git checkout <previous-commit-sha>

# Rebuild and redeploy
docker compose build --no-cache
docker compose up -d
```

**Option C — Restart without rebuild (config-only change)**
```bash
docker compose restart backend
```

> ⚠️ **Data note:** Rolling back does not affect PostgreSQL data (persisted in `postgres_data` volume). Redis data is ephemeral and will be lost on any `docker compose down`.

---

## 5. Monitoring & Alerting

### Key metrics to watch

| Metric | Where to find it | Alert threshold |
|---|---|---|
| Backend HTTP health | `GET /health` → `{"status":"ok"}` | Any non-200 response |
| Backend container health | `docker inspect` → `Health.Status` | Not `healthy` for > 60 s |
| LLM response latency | Backend stdout: `[AGGREGATOR] … time=Xs` | > 60 s for full assessment |
| LLM token consumption | Backend stdout: `in=X tok  out=X tok` | Output tokens approaching `aggregator_max_tokens` (8000) |
| Redis memory | `docker compose exec redis redis-cli info memory` | [TODO: set threshold based on expected session volume] |
| Postgres disk usage | `docker compose exec postgres psql -U chainlit -c "SELECT pg_database_size('chainlit');"` | [TODO: set threshold] |
| Tool execution time | Backend stdout: `[TOOL END] <name>  time=Xs` | > 30 s per tool call |

### Logs

```bash
# All services
docker compose logs -f

# Backend only (most relevant)
docker compose logs -f backend

# Filter for errors
docker compose logs backend 2>&1 | grep -iE "error|exception|traceback|critical"

# Filter for LLM usage/timing
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL"

# Filter for assessment starts
docker compose logs backend 2>&1 | grep "\[ASSESSMENT\]"

# Filter for chat sessions
docker compose logs backend 2>&1 | grep "\[CHAT\]"
```

### GitHub Actions workflow health
- Monitor workflow runs at: `https://github.com/kylodeng/underwriting_chatbot-main/actions`
- Key workflows to watch:
  - `Tool 1 — Code Review`: triggers on every PR
  - `Tool 2 — Tech Documentation`: triggers on merge to main and weekly Sunday 06:00 UTC
  - `Tool 4 — Auto Testing`: triggers on PRs touching source files and weekly Wednesday 07:00 UTC

### Alerting
[TODO: Is there a PagerDuty/OpsGenie/Slack integration configured? If so, which webhook URL and which events should trigger pages?]

[TODO: Are there any cloud-native monitoring dashboards (e.g. Azure Monitor, Datadog) connected to this service?]

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in on-call rotation / Slack handle] | Service down, health check failing |
| L2 | Backend/AI engineer | [TODO: fill in] | LLM errors, agent logic failures, assessment pipeline issues |
| L3 | Tech lead | [TODO: fill in] | Data integrity issues, security incidents, Redis/Postgres data loss |
| L4 | Solution owner | [TODO: fill in] | Prolonged outage (> 1 h), vendor API outages, compliance concerns |
| External | Anthropic support | https://support.anthropic.com | Sustained 5xx from Claude API, billing issues |
| External | Google Cloud support | [TODO: fill in GCP support channel] | Sustained Gemini API failures |
| Notifications | Default email | kylo.deng@capco.com | Automated CI/CD workflow notifications (currently hardcoded) |

---

## 7. Useful Commands

### Start / stop the full stack
```bash
# Start everything
docker compose up -d

# Stop everything (preserves volumes)
docker compose down

# Stop and destroy ALL data including postgres volume
docker compose down -v
```

### Rebuild a single service after code change
```bash
docker compose build backend
docker compose up -d --no-deps backend
```

### Tail logs
```bash
# All services
docker compose logs -f

# Single service
docker compose logs -f backend
docker compose logs -f redis
docker compose logs -f postgres
docker compose logs -f frontend
```

### Health check — backend
```bash
curl -sf http://localhost:8000/health | python3 -m json.tool
```

### Send a test chat request
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the risk profile for customer CUST00000001?",
    "temperature": 0.3,
    "session_id": "ops-test-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### Redis — inspect conversation state
```bash
# Connect to Redis CLI
docker compose exec redis redis-cli

# List all keys (LangGraph checkpoint keys)
KEYS *

# Flush all sessions (⚠️ destroys all conversation history)
FLUSHALL
```

### PostgreSQL — basic checks
```bash
# Connect
docker compose exec postgres psql -U chainlit -d chainlit

# Check Chainlit tables
\dt

# Check DB size
SELECT pg_size_pretty(pg_database_size('chainlit'));

# Exit
\q
```

### Inspect environment variables injected into backend
```bash
docker compose exec backend env | sort | grep -v PASSWORD | grep -v API_KEY
# Note: omits secrets from output for safety
```

### Restart individual services
```bash
docker compose restart backend
docker compose restart redis
docker compose restart postgres
docker compose restart frontend
```

### View backend container health check history
```bash
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{range .State.Health.Log}}{{.Start}} {{.ExitCode}} {{.Output}}{{end}}'
```

### Pull latest config and hot-restart backend
```bash
git pull origin main
docker compose restart backend
# Config changes in config.yml take effect on restart (no rebuild needed)
```

### Trigger GitHub Actions workflows manually
```bash
# Requires GitHub CLI (gh)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main
gh workflow run tool4_auto_testing.yml --repo kylodeng/underwriting_chatbot-main \
  -f test_mode=generate
```

---

> **Known TODOs extracted from code:**
> - `backend/agent/graph.py`: Redis has no persistent volume — conversation history is lost on Redis container restart. Migrate to Azure Cache for Redis or add a named Docker volume.
> - `backend/modules/LLMS.py`: Azure and OpenAI providers are stubbed (`None`) — calling them will raise `ValueError`.
> - `docker-compose.yml`: No resource limits (CPU/memory) defined for any container.
> - No centralised alerting or metrics exporter (e.g. Prometheus) is present in the compose file.
> - [TODO: What is the process for rotating Anthropic / Google API keys in production?]
> - [TODO: Is there a staging