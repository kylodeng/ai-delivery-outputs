# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-container AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The backend is a FastAPI application exposing a streaming Server-Sent Events (SSE) `/chat` endpoint and a `/health` endpoint; it orchestrates a LangGraph agent backed by Anthropic Claude models (Haiku for speed, Sonnet for depth) to run parallel specialist risk assessments across finance, health, and life domains, then aggregates results into a structured `UnderwritingReport`. Supporting infrastructure consists of Redis (LangGraph conversation checkpointing), PostgreSQL (Chainlit session persistence), and four SQLite databases (customer profiles, feature importance, model predictions, application profiles) mounted read-only into the backend container. Five GitHub Actions CI/CD tools provide automated code review, technical documentation generation, business documentation, auto-test generation, and UAT facilitation — all powered by the Claude API. A trained CatBoost model card (`model_card.json`) describes the offline risk-classification model whose predictions are served from the SQLite databases.

---

## 2. Health Checks

### Backend API

```bash
# Should return: {"status": "ok"}
curl -f http://localhost:8000/health
```

### Docker Compose Service Status

```bash
docker compose ps
# All services should show "running" / "healthy"
```

### Redis Connectivity

```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

### PostgreSQL Connectivity

```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### Frontend Reachability

```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### Backend Container Health (Docker)

```bash
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{.State.Health.Status}}'
# Expected: healthy
```

### CI/CD Workflow Health

- Navigate to **GitHub Actions** tab in the repository.
- Confirm all five workflow runs (`Tool 1–5`) show green status on their last execution.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container crashed or failed to start | 1. `docker compose logs backend --tail=50` to inspect errors. 2. Check `.env` file is present and all required vars are set. 3. `docker compose restart backend`. |
| `/chat` endpoint hangs or returns no SSE events | Anthropic API key invalid, rate-limited, or model unavailable | 1. Verify `ANTHROPIC_API_KEY` in `.env` is valid. 2. Check Anthropic status page. 3. Switch `default` model in `config.yml` from `anthropic-fast` to `anthropic` or vice versa. 4. `docker compose restart backend`. |
| Agent repeats tool calls infinitely / no `final_answer` | LangGraph state loop not terminating; Claude returning malformed JSON | 1. Check backend logs: `docker compose logs backend -f`. 2. Verify `re.search(r'\{.*\}', content, re.DOTALL)` is finding valid JSON from the LLM. 3. Temporarily lower `temperature` to `0` in request payload. 4. Restart backend to clear any in-memory state. |
| Redis connection refused / `ConnectionError` on startup | Redis container not running or `REDIS_HOST` misconfigured | 1. `docker compose ps redis` — check it is running. 2. `docker compose restart redis`. 3. Confirm `REDIS_HOST=redis` in backend environment. 4. Check port 6379 is not bound by another process on host. |
| PostgreSQL `FATAL: password authentication failed` | Wrong credentials or DB not initialised | 1. Confirm `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`, `POSTGRES_DB=chainlit` in `docker-compose.yml`. 2. Check `postgres/init.sql` executed on first run. 3. If volume is corrupted: `docker compose down -v && docker compose up -d` (**destructive — data lost**). |
| Frontend cannot reach backend (`BACKEND_URL` error) | Network misconfiguration or backend not healthy | 1. Confirm backend is healthy (`docker inspect ...`). 2. Confirm `BACKEND_URL=http://backend:8000` — uses Docker internal DNS, not `localhost`. 3. `docker compose restart frontend`. |
| SQLite database read errors (`unable to open database file`) | Database files not mounted or wrong path | 1. Confirm `./database/*.db` files exist in the host repo root. 2. Check `docker compose.yml` volume mounts point to correct paths. 3. Verify files are not zero-byte. |
| `ValueError: Unsupported or unconfigured model provider` | Model name in request does not match `LLMS.model_mapper` keys | 1. Valid values: `gemini`, `anthropic`, `anthropic-fast`. Azure/OpenAI return `None` and will raise. 2. Check request `model` field. 3. Add/configure provider in `backend/modules/LLMS.py` if needed. |
| GitHub Action fails: `ANTHROPIC_API_KEY` not found | Secret not set in repository settings | 1. Go to **Settings → Secrets and variables → Actions**. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. 3. Re-run failed workflow. |
| Assessment returns `data_gaps` with many `[TODO]` items | Customer profile data is incomplete in the SQLite databases | 1. Verify the correct `.db` files are mounted. 2. Check the `get_customer_profile` tool query returns populated fields. 3. [TODO: confirm expected schema for customer_profile.db] |
| `GOOGLE_API_KEY` missing on Gemini model selection | API key not set; Gemini model selected but key absent | 1. Add `GOOGLE_API_KEY` to `.env`. 2. If Gemini is not needed, do not expose it as a selectable model option. |
| Memory loss between sessions (agent forgets history) | Redis persistence not configured — data lost on container restart | 1. Add Redis volume persistence: `volumes: - redis_data:/data` in `docker-compose.yml`. 2. See TODO note in `graph.py` — consider migrating to Azure Cache for Redis. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker Engine ≥ 24.x and Docker Compose v2 installed on the host.
- `.env` file present in the repo root with all required environment variables (see §5).
- SQLite database files present under `./database/`.
- `postgres/init.sql` present for first-run DB initialisation.

---

### Step-by-Step Deployment

**Step 1 — Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create the environment file**

```bash
cp .env.example .env          # [TODO: confirm .env.example exists or document required vars]
# Edit .env and populate all required variables (see §5)
```

**Step 3 — Build all images**

```bash
docker compose build --no-cache
```

**Step 4 — Start infrastructure services first**

```bash
docker compose up -d redis postgres
# Wait ~5 seconds for PostgreSQL to initialise
sleep 5
docker compose exec postgres pg_isready -U chainlit -d chainlit
```

**Step 5 — Start backend and confirm healthy**

```bash
docker compose up -d backend
# Poll until healthy (up to 60s based on healthcheck config)
for i in {1..12}; do
  STATUS=$(docker inspect underwriting_chatbot-main-backend-1 \
    --format='{{.State.Health.Status}}' 2>/dev/null)
  echo "[$i] Status: $STATUS"
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

**Step 8 — Verify logs**

```bash
docker compose logs --tail=30 backend
docker compose logs --tail=30 frontend
```

---

### Rollback Steps

**Option A — Revert to previous image (if using tagged images)**

```bash
# [TODO: confirm whether images are tagged and pushed to a registry]
docker compose down
# Edit docker-compose.yml image tags to previous known-good version
docker compose up -d
```

**Option B — Git revert and redeploy**

```bash
git log --oneline -10              # identify last known-good commit
git revert HEAD                    # or git checkout <commit-sha>
docker compose build --no-cache
docker compose up -d
```

**Option C — Emergency stop (take service offline)**

```bash
docker compose down
```

**Database rollback note:** The SQLite databases are mounted read-only and are not modified by the application. PostgreSQL state (Chainlit sessions) can be reset with `docker compose down -v` — **this deletes all session history**.

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to Observe | Alert Threshold |
|---|---|---|
| Backend container health | `docker inspect` / `docker compose ps` | Any status other than `healthy` |
| `/health` HTTP response | External uptime monitor [TODO: configure uptime monitor] | Non-200 response |
| Anthropic API latency | Backend stdout: `[SPECIALIST]` and `[AGGREGATOR]` log lines include `time=Xs` | Specialist > 30s, Aggregator > 60s |
| Anthropic token usage | Backend stdout: `in=X tok out=X tok` per specialist call | Specialist output tokens approaching 1500 cap; aggregator approaching 8000 |
| Redis memory | `docker compose exec redis redis-cli info memory` | `used_memory_rss` > [TODO: define limit based on server capacity] |
| PostgreSQL connections | `docker compose exec postgres psql -U chainlit -c "SELECT count(*) FROM pg_stat_activity;"` | > 80% of `max_connections` |
| GitHub Actions failure | GitHub Actions tab / email notifications | Any failed run on `main` branch |

### Key Logs to Watch

```bash
# Backend application logs (tool calls, LLM timings, errors)
docker compose logs -f backend

# Redis logs
docker compose logs -f redis

# PostgreSQL logs
docker compose logs -f postgres

# All services
docker compose logs -f
```

### Log Patterns Indicating Problems

```
# LLM timeout or API error
Error | Exception | Traceback

# Agent loop not resolving
[TOOL START] ... (repeated same tool name without [TOOL END])

# Redis down
ConnectionError | Connection refused | redis

# Assessment token cap hit
out=1500 tok  # specialist at limit — output may be truncated

# JSON parse failure in agent
[DEBUG] JSON parse error
```

### GitHub Actions Monitoring

- **Tool 1 (Code Review):** Triggers on every PR and Monday 08:00 UTC. Watch for failures in `Run Claude code review` step.
- **Tool 2 (Tech Docs):** Triggers on push to `main` and Sunday 06:00 UTC.
- **Tool 3 (Business Docs):** Triggers on version tags (`v*`).
- **Tool 4 (Auto Testing):** Triggers on PRs touching `src/**`, `*.py`, `*.js`, `*.ts` and Wednesday 07:00 UTC.
- **Tool 5 (UAT):** Triggers on `release/*` branch creation.

All tools require `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` secrets to be set.

[TODO: Configure external uptime monitoring (e.g. Datadog, Pingdom, Azure Monitor) for `GET /health`]  
[TODO: Configure alerting for GitHub Actions failures (e.g. Slack webhook, PagerDuty)]  
[TODO: Configure Anthropic API spend alerts in the Anthropic console]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call contact / PagerDuty rotation] | Service down, health check failing |
| L2 | Backend Lead | [TODO: fill in name and contact] | Persistent LLM errors, agent logic failures, data corruption |
| L3 | Platform / DevOps | [TODO: fill in name and contact] | Infrastructure failure (Redis, Postgres, Docker host), secrets rotation needed |
| L4 | Anthropic Support | https://support.anthropic.com | API outage, unexpected model behaviour, billing issues |
| Business | Solution Owner | [TODO: fill in name and contact] | Data breach, compliance issue, go-live decision |

**Notification email (CI/CD tools):** `kylo.deng@capco.com`  
**SendGrid sender:** `noreply@ai-delivery.capco.com`

[TODO: Define SLA / SLO targets (e.g. 99.5% uptime, P95 response < 10s)]  
[TODO: Define on-call schedule and paging policy]

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (data preserved)
docker compose down

# Stop and delete all volumes (DESTRUCTIVE)
docker compose down -v

# Rebuild and restart a single service
docker compose build backend && docker compose up -d --no-deps backend

# Restart a single service without rebuild
docker compose restart backend
```

### Log Inspection

```bash
# Tail all logs
docker compose logs -f

# Tail backend only (last 100 lines)
docker compose logs -f --tail=100 backend

# Search backend logs for errors
docker compose logs backend 2>&1 | grep -i "error\|exception\|traceback"

# Search for LLM timing lines
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]"
```

### Health & Status

```bash
# Check all container statuses
docker compose ps

# Check backend health
curl -s http://localhost:8000/health | python3 -m json.tool

# Backend container health state
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{.State.Health.Status}}'

# Full health check output (last 5 checks)
docker inspect underwriting_chatbot-main-backend-1 \
  --format='{{json .State.Health}}' | python3 -m json.tool
```

### Redis

```bash
# Ping Redis
docker compose exec redis redis-cli ping

# Check memory usage
docker compose exec redis redis-cli info memory | grep used_memory_human

# List all keys (use with caution in production)
docker compose exec redis redis-cli keys '*'

# Flush all Redis data (clears all agent memory — DESTRUCTIVE)
docker compose exec redis redis-cli flushall
```

### PostgreSQL

```bash
# Check DB is ready
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Connect to DB
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit \
  -c "\dt"

# Count active connections
docker compose exec postgres psql -U chainlit -d chainlit \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname='chainlit';"
```

### Test the Chat Endpoint

```bash
# Non-streaming health smoke test
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello",
    "temperature": 0.3,
    "session_id": "test-ops",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### GitHub Actions — Manual Trigger

```bash
# Trigger Tool 2 (tech docs generation) manually via GitHub CLI
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Trigger Tool 1 code review on a specific PR
gh