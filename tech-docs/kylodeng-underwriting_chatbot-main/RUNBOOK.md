# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational chat interface. The backend is a Python FastAPI application that orchestrates a LangGraph agent backed by Anthropic Claude (claude-haiku for fast responses, claude-sonnet for deep analysis). When prompted, the agent calls specialist sub-LLMs in parallel to evaluate customer risk across domains (finance, health, life, etc.), aggregates their outputs into a structured `UnderwritingReport`, and streams results back to the frontend via Server-Sent Events (SSE). Conversation state is persisted in Redis (LangGraph checkpointing) and customer profile data is served from SQLite databases. A PostgreSQL instance backs the frontend Chainlit session store. The full stack is containerised and managed via Docker Compose.

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
# All services should show: Status = running, health = healthy
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

### End-to-end smoke test

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
# Expected: SSE stream with at least one "response" event
```

### Frontend availability

```bash
curl -f http://localhost:8080
# Expected: HTTP 200
```

### Database file presence

```bash
ls -lh ./database/*.db
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db all present and non-zero size
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or times out | Backend container crashed or not started | 1. `docker compose logs backend --tail=50` 2. Check for import errors or missing env vars 3. `docker compose restart backend` |
| Chat endpoint returns 500 with `ValueError: Unsupported or unconfigured model provider` | Invalid `model` value in request, or LLM provider not configured | 1. Verify `model` field is one of: `anthropic`, `anthropic-fast`, `gemini` 2. Confirm `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` are set in `.env` |
| Chat streams no tokens / hangs indefinitely | Anthropic API key missing, rate-limited, or network timeout | 1. Check `.env` for `ANTHROPIC_API_KEY` 2. `docker compose logs backend` for API errors 3. Test key directly: `curl https://api.anthropic.com/v1/messages -H "x-api-key: $ANTHROPIC_API_KEY"` |
| `ConnectionRefusedError` on Redis host | Redis container not running or `REDIS_HOST` misconfigured | 1. `docker compose ps redis` 2. `docker compose restart redis` 3. Confirm `REDIS_HOST=redis` is set in backend environment |
| Agent repeats tool calls infinitely | LangGraph state not persisting; Redis checkpoint failure | 1. `docker compose logs redis` for OOM or connection errors 2. Restart Redis: `docker compose restart redis` 3. Clear session state (see Useful Commands) |
| `FileNotFoundError` for `.db` files | SQLite database volume not mounted or files missing | 1. Confirm `./database/` directory exists and contains all four `.db` files 2. Check `docker-compose.yml` volume mounts are correct 3. Re-run any data provisioning scripts [TODO: what script generates the SQLite databases?] |
| Frontend fails to load or shows 502 | Backend not healthy when frontend started; backend health check failing | 1. `docker compose logs frontend` 2. `docker compose logs backend` 3. `docker compose restart backend` then `docker compose restart frontend` |
| PostgreSQL `FATAL: role "chainlit" does not exist` | Init SQL not executed; fresh volume with missing init | 1. `docker compose down -v` (destroys data — use with caution) 2. `docker compose up -d` to reinitialise 3. Or manually run: `docker compose exec postgres psql -U chainlit -f /docker-entrypoint-initdb.d/init.sql` |
| Assessment returns empty `areas_of_interest` | `get_customer_profile` tool not called before `run_underwriting_assessment`; customer ID not found | 1. Check agent logs for tool call order 2. Verify customer ID exists: see Useful Commands 3. Check `assessment_criterias.json` is present in `backend/prompts/` |
| GitHub Actions tool workflows fail with `KeyError: 'ANTHROPIC_API_KEY'` | Repository secret not set | 1. Go to repo Settings → Secrets and variables → Actions 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` |
| Aggregator LLM output truncated / malformed JSON | `aggregator_max_tokens` too low for large profiles | 1. Increase `aggregator_max_tokens` in `backend/config.yml` (currently 8000) 2. Redeploy backend |
| `gemini` model returns errors | Google API key missing or `gemini-3-flash-preview` model name invalid | 1. Confirm `GOOGLE_API_KEY` in `.env` 2. [TODO: verify correct Gemini model name — `gemini-3-flash-preview` may be a placeholder] |

---

## 4. Deployment Procedure

### Prerequisites

- Docker and Docker Compose installed
- `.env` file present in repo root with required secrets (see Section 5)
- SQLite database files present under `./database/`
- `./postgres/init.sql` present for DB initialisation

### Step-by-step deployment

```bash
# Step 1: Clone repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Step 2: Create .env file from template
cp .env.example .env          # [TODO: does an .env.example exist?]
# Edit .env and populate all required variables (see Section 5)

# Step 3: Ensure database files are in place
ls ./database/*.db            # Must list all four .db files

# Step 4: Build images
docker compose build

# Step 5: Start infrastructure services first
docker compose up -d redis postgres

# Step 6: Wait for postgres to be ready
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Step 7: Start backend
docker compose up -d backend

# Step 8: Confirm backend is healthy
docker compose ps backend     # health = healthy
curl -f http://localhost:8000/health

# Step 9: Start frontend
docker compose up -d frontend

# Step 10: Run smoke test
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"smoke","model":"anthropic-fast","mode":"fast"}'
```

### Config changes (no image rebuild required)

```bash
# Edit backend/config.yml or .env, then:
docker compose restart backend
```

### Code changes (rebuild required)

```bash
git pull origin main
docker compose build backend   # or frontend if frontend changed
docker compose up -d --no-deps backend
```

### Rollback procedure

```bash
# Option A: Roll back to previous Docker image (if tagged)
docker compose down backend
docker tag underwriting_chatbot-main-backend:previous underwriting_chatbot-main-backend:latest
docker compose up -d backend

# Option B: Roll back to previous git commit
git log --oneline -10          # identify target commit hash
git checkout <commit-hash>
docker compose build backend
docker compose up -d --no-deps backend

# Option C: Full stack rollback
docker compose down
git checkout <commit-hash>
docker compose build
docker compose up -d
```

> **Note:** Redis conversation state will survive a backend rollback. If a bad deployment corrupted Redis state, flush it selectively (see Useful Commands).

---

## 5. Monitoring & Alerting

### Required environment variables / secrets to monitor

| Variable | Where Set | Sensitivity |
|---|---|---|
| `ANTHROPIC_API_KEY` | `.env` / GitHub Secrets | High — rotates with Anthropic account |
| `GOOGLE_API_KEY` | `.env` | High |
| `SENDGRID_API_KEY` | GitHub Secrets | High |
| `GH_TOKEN` | GitHub Secrets | High |
| `REDIS_HOST` | `docker-compose.yml` | Low |

### Key metrics to watch

| Metric | Source | Alert Threshold |
|---|---|---|
| `/health` endpoint response | Backend HTTP | Non-200 for >30s |
| Backend container health | Docker Compose | `unhealthy` state |
| Redis memory usage | `redis-cli INFO memory` | >80% `maxmemory` |
| LLM specialist call latency | Backend stdout logs `[SPECIALIST]` | [TODO: define SLA threshold] |
| Aggregator call latency | Backend stdout logs `[AGGREGATOR]` | [TODO: define SLA threshold] |
| Anthropic API token usage | Backend stdout logs `in=X tok out=Y tok` | [TODO: set token budget alert] |
| Tool call duration | Backend stdout logs `[TOOL END] name time=Xs` | >30s per tool call |
| GitHub Actions workflow failures | GitHub Actions UI | Any failure on `main` branch |

### Log streams

```bash
# All services
docker compose logs -f

# Backend only (most relevant for LLM/agent issues)
docker compose logs -f backend

# Look for specialist/aggregator timings
docker compose logs backend | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[TOOL"

# Look for errors
docker compose logs backend | grep -iE "error|exception|traceback"
```

### Log patterns to alert on

| Log Pattern | Meaning |
|---|---|
| `AuthenticationError` | Anthropic/Google API key invalid or expired |
| `RateLimitError` | LLM API quota exceeded |
| `ConnectionRefusedError.*6379` | Redis unreachable |
| `FileNotFoundError.*\.db` | SQLite database file missing |
| `ValueError: Unsupported or unconfigured model` | Bad model name in request |
| `[ASSESSMENT] Starting` | Assessment tool invoked — expect 4–6 parallel specialist calls |

### GitHub Actions monitoring

- Navigate to: `https://github.com/kylodeng/underwriting_chatbot-main/actions`
- Tool 1 (Code Review): triggers on every PR
- Tool 2 (Tech Docs): triggers on merge to `main` and Sundays 06:00 UTC
- Tool 4 (Auto Testing): triggers on PRs touching `src/**`, `*.py`, `*.js`, `*.ts`
- Check the `ai-delivery-outputs` repo for generated artefacts

> [TODO: Is there a centralised log aggregator (e.g. Azure Monitor, Datadog, CloudWatch) or is logging currently stdout-only?]

> [TODO: Are there any uptime monitoring services (e.g. Pingdom, UptimeRobot) configured against the `/health` endpoint?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: add on-call rotation contact] | Service down, health check failing |
| L2 | Backend lead | [TODO: add backend lead contact] | LLM integration failures, data corruption |
| L3 | Platform / DevOps | [TODO: add platform team contact] | Infrastructure failures, Docker/Redis/Postgres issues |
| L4 | Anthropic support | [TODO: add Anthropic support link] | API outage, billing/quota issues |
| Business | Solution owner | [TODO: add solution owner contact] | Data quality issues affecting underwriting decisions |

> [TODO: Is there a PagerDuty / OpsGenie / Slack alerting channel configured?]

> [TODO: What is the RTO/RPO for this service?]

---

## 7. Useful Commands

### Start / stop services

```bash
# Start full stack
docker compose up -d

# Stop all (preserve volumes)
docker compose down

# Stop all and destroy volumes (WARNING: deletes postgres data)
docker compose down -v

# Restart single service
docker compose restart backend
docker compose restart redis
docker compose restart frontend
```

### View logs

```bash
# Tail all logs
docker compose logs -f

# Tail backend only
docker compose logs -f backend --tail=100

# Grep for errors
docker compose logs backend 2>&1 | grep -iE "error|exception|traceback"

# Grep for LLM timing stats
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]"
```

### Redis operations

```bash
# Check Redis is alive
docker compose exec redis redis-cli ping

# List all conversation thread keys
docker compose exec redis redis-cli KEYS "*"

# Delete a specific session's LangGraph checkpoint (replace SESSION_ID)
docker compose exec redis redis-cli DEL "SESSION_ID"

# Flush all Redis state (WARNING: clears all conversation history)
docker compose exec redis redis-cli FLUSHALL

# Check Redis memory
docker compose exec redis redis-cli INFO memory | grep used_memory_human
```

### PostgreSQL operations

```bash
# Connect to chainlit DB
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# Check row counts
docker compose exec postgres psql -U chainlit -d chainlit -c "SELECT count(*) FROM users;"
```

### SQLite database inspection

```bash
# List customers (adjust path if running outside Docker)
sqlite3 ./database/customer_profile.db "SELECT customer_id FROM customer_profile LIMIT 10;"

# Check if a specific customer exists
sqlite3 ./database/customer_profile.db \
  "SELECT * FROM customer_profile WHERE customer_id='CUST00000001';"
```

### Backend API calls

```bash
# Health check
curl -s http://localhost:8000/health | python3 -m json.tool

# Fast chat request (streaming — will print SSE events)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get profile for CUST00000001 and run a fast risk assessment",
    "temperature": 0.3,
    "session_id": "test-session-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'

# Deep assessment request
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Run a deep assessment for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-deep-001",
    "model": "anthropic",
    "mode": "deep"
  }'
```

### GitHub Actions — trigger workflows manually

```bash
# Trigger tech docs generation (requires gh CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Trigger business docs for a release
gh workflow run tool3_business_docs.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# View recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10
```

### Docker image management

```bash
# Rebuild without cache
docker compose build --no-cache backend

# Check image sizes
docker images | grep underwriting

# Force recreate containers after config change
docker compose up -d --force-recreate backend
```