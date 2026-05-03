# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that allows underwriters to assess customer risk profiles through a conversational interface. The system consists of a FastAPI backend that orchestrates a LangGraph-based agent, which calls specialist LLM assessment pipelines (using Anthropic Claude and Google Gemini) across multiple risk domains (finance, health, life, etc.), a Redis instance for agent conversation checkpointing, a PostgreSQL database for the Chainlit frontend session state, and several SQLite databases holding customer profile, model prediction, and feature importance data. The frontend is a Chainlit-based chat UI served on port 8080, while the backend API runs on port 8000. A pre-trained CatBoost classifier (`model_card.json`) informs risk classification labels (Preferred, Standard Plus, Standard, Substandard), and a customer lookalike service provides peer comparison. The entire stack is containerised via Docker Compose.

---

## 2. Health Checks

### Backend API

```bash
# Confirm backend is alive
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Docker Container Status

```bash
docker compose ps
# All services should show: "Up" and backend should show "(healthy)"
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
# Expected: HTTP 200 with HTML body
```

### LLM API Key Validity

```bash
# Check Anthropic key is set and non-empty
docker compose exec backend printenv ANTHROPIC_API_KEY | wc -c
# Expected: > 1

# Check Google key (for Gemini fallback)
docker compose exec backend printenv GOOGLE_API_KEY | wc -c
# Expected: > 1
```

### SQLite Database Files

```bash
# Confirm customer profile DB is mounted and readable
docker compose exec backend ls -lh /data/customer_profile.db
docker compose exec backend ls -lh /data/model_predictions.db
docker compose exec backend ls -lh /data/feature_importance.db
docker compose exec backend ls -lh /data/application_profile.db
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container shows `unhealthy` or keeps restarting | Missing `.env` file, bad API key, or port conflict on 8000 | 1. Check `docker compose logs backend`. 2. Verify `.env` exists in project root with all required vars. 3. Confirm port 8000 is free: `lsof -i :8000`. 4. Restart: `docker compose restart backend`. |
| `/health` returns connection refused | Backend process crashed or failed to start | 1. `docker compose logs backend --tail 50`. 2. Check for import errors or missing dependencies. 3. `docker compose up --build backend`. |
| Agent returns no response / hangs indefinitely | LLM API timeout, rate-limit, or invalid API key | 1. Confirm `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` are valid in `.env`. 2. Check Anthropic/Google status pages. 3. Review backend logs for `4xx`/`5xx` from LLM APIs. 4. Reduce `specialist_max_tokens` in `config.yml` if context window is being exceeded. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request does not match a key in `LLMS.model_mapper` | 1. Valid values are `gemini`, `anthropic`, `anthropic-fast`. 2. Check request payload `model` field. 3. `azure` and `openai` are defined but return `None` — do not use them. |
| Redis connection error on backend start | Redis container not running or `REDIS_HOST` env var wrong | 1. `docker compose ps redis`. 2. If down: `docker compose up -d redis`. 3. Confirm `REDIS_HOST=redis` is set in backend environment (set in `docker-compose.yml`). |
| Agent conversation loses context between messages | Redis checkpoint missing or Redis restarted without persistence | 1. Check Redis is running. 2. Note: current Redis image (`redis-stack-server`) does **not** have persistence configured — data is lost on restart. See TODO in `graph.py`. 3. Restart conversation with a new `session_id`. |
| Frontend fails to start | Backend not healthy (frontend depends on `service_healthy`) or `DATABASE_URL` misconfigured | 1. Ensure backend passes health check first: `docker compose ps`. 2. Confirm PostgreSQL is running: `docker compose logs postgres`. 3. `docker compose restart frontend`. |
| PostgreSQL init fails | `postgres/init.sql` missing or malformed | 1. `docker compose logs postgres`. 2. Confirm `./postgres/init.sql` exists in project root. 3. Wipe volume and reinitialise: `docker compose down -v && docker compose up -d postgres`. |
| Customer profile not found in tool call | Customer ID not present in SQLite DB or DB file not mounted | 1. Confirm `/data/customer_profile.db` is mounted: `docker compose exec backend ls /data/`. 2. Check the customer ID format matches `CUST0000XXXX` pattern. 3. [TODO: Is there a seeding script to populate the databases?] |
| Assessment returns empty or malformed JSON | Aggregator LLM exceeded `aggregator_max_tokens` or returned non-structured output | 1. Increase `aggregator_max_tokens` in `config.yml` (currently 8000). 2. Check backend logs for `[AGGREGATOR]` line and token counts. 3. Switch to `anthropic` (Sonnet) instead of `anthropic-fast` (Haiku) for more reliable structured output. |
| GitHub Actions workflow fails | Missing secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) | 1. Navigate to repo **Settings → Secrets → Actions**. 2. Confirm all three secrets are present and non-expired. 3. Re-run the failed workflow. |
| `run_underwriting_assessment` tool called before `get_customer_profile` | Agent skipped required pre-condition tool call | 1. This is a prompt-level constraint. Review agent logs for the tool call sequence. 2. Rephrase user message to request customer info explicitly first. 3. [TODO: Add guard-rail in tool implementation to reject calls without a populated profile.] |

---

## 4. Deployment Procedure

### Prerequisites

- Docker and Docker Compose v2+ installed
- `.env` file in project root (see required variables below)
- SQLite database files present under `./database/`
- `./postgres/init.sql` present

### Required `.env` Variables

```dotenv
ANTHROPIC_API_KEY=<your-anthropic-key>
GOOGLE_API_KEY=<your-google-api-key>
# TODO: Are SENDGRID_API_KEY, GH_TOKEN needed at runtime, or only in CI?
# TODO: What additional env vars does the frontend require beyond BACKEND_URL and DATABASE_URL?
```

### Step-by-Step Deployment

**Step 1 — Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**Step 2 — Create the `.env` file**

```bash
cp .env.example .env   # TODO: confirm .env.example exists; if not, create manually
# Edit .env and populate all required variables
```

**Step 3 — Build and start all services**

```bash
docker compose up --build -d
```

**Step 4 — Verify services are healthy**

```bash
docker compose ps
# Wait up to 60 seconds for backend to become healthy
# Expected: redis Up, postgres Up, backend Up (healthy), frontend Up
```

**Step 5 — Confirm backend health endpoint**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

**Step 6 — Access the frontend**

Open `http://localhost:8080` in a browser.

---

### Updating to a New Version

**Step 1 — Pull latest code**

```bash
git pull origin main
```

**Step 2 — Rebuild changed services**

```bash
docker compose up --build -d --no-deps backend frontend
```

**Step 3 — Verify health**

```bash
docker compose ps
curl http://localhost:8000/health
```

---

### Rollback Steps

**Option A — Roll back to previous Docker image (if images are tagged)**

```bash
# TODO: Confirm whether images are pushed to a registry with version tags.
# If so:
docker compose stop backend frontend
# Edit docker-compose.yml to pin the previous image tag, then:
docker compose up -d backend frontend
```

**Option B — Roll back via Git**

```bash
git log --oneline -5             # identify last known-good commit
git checkout <previous-commit>   # or git revert HEAD
docker compose up --build -d --no-deps backend frontend
docker compose ps
curl http://localhost:8000/health
```

**Option C — Full teardown and redeploy**

```bash
# WARNING: -v removes postgres_data volume — confirm DB is backed up first
docker compose down
git checkout <previous-commit>
docker compose up --build -d
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to Find | Alert Threshold |
|---|---|---|
| Backend health status | `GET /health` response | Any non-200 response |
| Container restart count | `docker compose ps` / `docker stats` | > 2 restarts in 5 min |
| LLM response latency | Backend stdout: `[SPECIALIST]` and `[AGGREGATOR]` log lines (`time=Xs`) | > 30s for specialist, > 60s for aggregator |
| LLM token consumption | Backend stdout: `in=X tok out=X tok` per assessment call | Approaching model context window limits |
| Redis memory usage | `docker compose exec redis redis-cli info memory` | [TODO: Set threshold based on expected session volume] |
| PostgreSQL connections | `docker compose exec postgres psql -U chainlit -c "SELECT count(*) FROM pg_stat_activity;"` | [TODO: Define max connection limit] |
| Tool call sequence errors | Backend logs for `ValueError` or JSON parse errors | Any occurrence |

### Log Locations

```bash
# All services
docker compose logs -f

# Backend only (most verbose — LLM calls, tool events)
docker compose logs -f backend

# Structured log patterns to grep
docker compose logs backend | grep "\[TOOL START\]"
docker compose logs backend | grep "\[TOOL END\]"
docker compose logs backend | grep "\[ASSESSMENT\]"
docker compose logs backend | grep "\[SPECIALIST\]"
docker compose logs backend | grep "\[AGGREGATOR\]"
docker compose logs backend | grep "\[CHAT\]"
```

### GitHub Actions CI Monitoring

- Navigate to **Actions** tab in the repository to monitor workflow runs for:
  - Tool 1 — Code Review (triggers on PR open/sync and Mondays 08:00 UTC)
  - Tool 2 — Tech Documentation (triggers on push to `main` and Sundays 06:00 UTC)
  - Tool 4 — Auto Testing (triggers on PR open/sync and Wednesdays 07:00 UTC)
- Failed runs indicate missing secrets or broken scripts — check the Actions run log.

### What to Alert On

- `backend` container health check fails (3 consecutive failures = container marked unhealthy)
- Any `CRITICAL` or `500` error in backend logs
- LLM API returning `429 Too Many Requests` (rate limit hit)
- Redis unavailable (agent will fail to checkpoint conversation state)
- [TODO: Is there a centralised logging or APM platform (e.g. Datadog, Azure Monitor, CloudWatch) configured for this service?]
- [TODO: Are there any existing PagerDuty/Opsgenie/Alertmanager integrations?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call rotation contact] | Service down, health check failing, containers not starting |
| L2 | Backend/ML Lead | [TODO: fill in team lead contact] | LLM assessment errors, model output quality issues, Redis persistence failures |
| L3 | Platform / Infra | [TODO: fill in infra team contact] | Database corruption, Docker host failures, network/port issues |
| L4 | LLM Vendor Support | Anthropic: console.anthropic.com/support | API outage, unexpected billing spikes, model behaviour changes |
| L4 | LLM Vendor Support | Google Cloud Support | Gemini API outage |
| Product Owner | [TODO: fill in product owner contact] | Decisions required on go/no-go, data issues affecting underwriting decisions |

> **Note:** This service processes insurance underwriting decisions. Any production incident that may have resulted in incorrect risk classifications must be escalated to the Product Owner and [TODO: compliance/risk team contact] immediately.

---

## 7. Useful Commands

### Start / Stop / Restart

```bash
# Start all services (build if needed)
docker compose up --build -d

# Stop all services (preserve volumes)
docker compose down

# Stop and remove volumes (DESTRUCTIVE — deletes postgres_data)
docker compose down -v

# Restart a single service
docker compose restart backend
docker compose restart frontend
docker compose restart redis

# View running containers and health status
docker compose ps
```

### Logs

```bash
# Tail all logs
docker compose logs -f

# Tail backend logs only
docker compose logs -f backend --tail 100

# Filter for LLM assessment events
docker compose logs backend | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[ASSESSMENT\]"

# Filter for tool calls
docker compose logs backend | grep -E "\[TOOL START\]|\[TOOL END\]"

# Filter for errors
docker compose logs backend | grep -iE "error|exception|traceback"
```

### Health Checks

```bash
# Backend health
curl -s http://localhost:8000/health | python3 -m json.tool

# Redis ping
docker compose exec redis redis-cli ping

# PostgreSQL readiness
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Redis memory info
docker compose exec redis redis-cli info memory | grep used_memory_human

# PostgreSQL active connections
docker compose exec postgres psql -U chainlit -c "SELECT count(*) FROM pg_stat_activity;"
```

### Test the Chat API Directly

```bash
# Send a test message to the backend
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get customer profile for CUST00000001",
    "temperature": 0.3,
    "session_id": "test-session-001",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### Database Inspection

```bash
# List SQLite DB files mounted into backend
docker compose exec backend ls -lh /data/

# Query customer profile DB (if sqlite3 available in container)
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT * FROM customer_profile LIMIT 5;"

# PostgreSQL — list tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"
```

### Redis Inspection

```bash
# List all Redis keys (use with caution in production — can be large)
docker compose exec redis redis-cli keys "*"

# Count keys
docker compose exec redis redis-cli dbsize

# Flush all Redis data (DESTRUCTIVE — clears all conversation checkpoints)
docker compose exec redis redis-cli flushall
```

### Configuration

```bash
# View current LLM config
cat backend/config.yml

# Increase aggregator token budget (edit in place)
# aggregator_max_tokens: 8000  →  increase if structured output is being truncated
vim backend/config.yml
docker compose restart backend
```

### GitHub Actions — Manual