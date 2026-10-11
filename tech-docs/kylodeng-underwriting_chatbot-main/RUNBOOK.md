# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-tier, AI-powered life insurance underwriting assistant designed for use by underwriters. It exposes a streaming chat API (FastAPI, port 8000) backed by a LangGraph-orchestrated agent that coordinates multiple LLM calls (Anthropic Claude Haiku/Sonnet and optionally Google Gemini) to assess customer risk profiles. The agent performs parallel specialist assessments across finance, health, and life risk domains, aggregates them into a structured `UnderwritingReport`, and streams results to a frontend (port 8080) via Server-Sent Events (SSE). Conversation state is persisted in Redis (LangGraph checkpointing), customer profile data is served from SQLite databases mounted read-only into the backend container, and a PostgreSQL instance backs the frontend (Chainlit) session store. Five GitHub Actions workflows provide automated code review, documentation generation, test generation, and UAT facilitation via Claude AI.

---

## 2. Health Checks

### Application-Level

| Check | How to verify | Expected result |
|---|---|---|
| Backend API liveness | `GET http://localhost:8000/health` | `{"status": "ok"}` HTTP 200 |
| Backend container status | `docker compose ps backend` | `Up (healthy)` |
| Redis connectivity | `docker compose exec redis redis-cli ping` | `PONG` |
| PostgreSQL connectivity | `docker compose exec postgres pg_isready -U chainlit` | `accepting connections` |
| Frontend container | `docker compose ps frontend` | `Up` |
| Agent responds to chat | `POST http://localhost:8000/chat` with valid payload | SSE stream begins, no immediate 5xx |

### Infrastructure-Level

| Check | Command |
|---|---|
| All containers running | `docker compose ps` |
| Backend health probe (as Docker sees it) | `docker inspect underwriting_chatbot-backend --format='{{.State.Health.Status}}'` |
| Redis port reachable | `nc -zv localhost 6379` |
| PostgreSQL port reachable | `nc -zv localhost 5432` |
| Backend logs (last 50 lines) | `docker compose logs --tail=50 backend` |

### External Dependency Checks

| Dependency | Check |
|---|---|
| Anthropic API reachable | `curl https://api.anthropic.com` (expect 4xx, not timeout) |
| Google Gemini API reachable | `curl https://generativelanguage.googleapis.com` |
| `ANTHROPIC_API_KEY` set | `docker compose exec backend env \| grep ANTHROPIC` |
| `GOOGLE_API_KEY` set | `docker compose exec backend env \| grep GOOGLE` |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container stuck in `starting` / unhealthy | Redis not ready when backend starts; `.env` missing | 1. `docker compose logs redis` — confirm Redis is up. 2. `docker compose restart backend`. 3. Verify `.env` file exists at repo root with all required keys. |
| `POST /chat` returns HTTP 500 immediately | Missing `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` environment variable | 1. `docker compose exec backend env \| grep API_KEY`. 2. Add missing keys to `.env`. 3. `docker compose up -d --force-recreate backend`. |
| Chat hangs indefinitely / SSE stream never closes | LLM API timeout or rate-limit; Redis checkpoint write failure | 1. Check `docker compose logs backend` for `anthropic` or `rate_limit` errors. 2. Check Redis: `docker compose exec redis redis-cli info clients`. 3. Retry request; if persistent, check Anthropic status page. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request has invalid value (e.g., `"azure"` or `"openai"` — currently `None` in `LLMS.py`) | 1. Only use `anthropic`, `anthropic-fast`, or `gemini` as model values. 2. If `azure`/`openai` needed, implement in `backend/modules/LLMS.py`. |
| Assessment returns empty or truncated report | `aggregator_max_tokens` (8000) exceeded or specialist output too verbose | 1. Check `docker compose logs backend` for `[AGGREGATOR]` lines and token counts. 2. Reduce `specialist_max_tokens` in `config.yml`. 3. `docker compose restart backend`. |
| Redis `AsyncRedisSaver` connection refused | Redis container not running or wrong `REDIS_HOST` | 1. `docker compose up -d redis`. 2. Confirm `REDIS_HOST=redis` is set in backend environment. 3. `docker compose restart backend`. |
| Frontend cannot reach backend | `BACKEND_URL` misconfigured or backend unhealthy | 1. Confirm `BACKEND_URL=http://backend:8000` in frontend environment. 2. `docker compose logs frontend`. 3. Ensure backend passes health check before frontend starts. |
| PostgreSQL init fails | `postgres/init.sql` missing or malformed | 1. `docker compose logs postgres`. 2. Check `./postgres/init.sql` exists. 3. `docker compose down -v && docker compose up -d postgres` to reinitialise volume. |
| SQLite databases not found inside backend | Volume mount path mismatch | 1. Confirm files exist: `ls ./database/*.db`. 2. Check `docker-compose.yml` volume mounts point to correct host paths. 3. `docker compose restart backend`. |
| GitHub Actions workflow fails at `pip install` | Python version mismatch or missing dependency | 1. Check workflow logs in GitHub Actions tab. 2. Confirm `python-version: "3.12"`. 3. Add missing packages to the `pip install` step. |
| `tool1_code_review.py` fails to parse Claude response | Claude returned malformed JSON | 1. Check workflow artifact `code-review-<run_id>` for raw response. 2. Re-run workflow (`workflow_dispatch`). Transient issue. |
| `on_chat_model_stream` emits no text | Agent tag filtering issue; model returning tool-call blocks only | 1. `docker compose logs backend` — look for `[TOOL START]` / `[TOOL END]` pairs. 2. Confirm `agent` tag is applied in `agent_with_skills.py`. |
| Customer similarity lookup returns empty | `customer_similarity_dict.json` missing from `backend/tmp/` | 1. Confirm file exists: `docker compose exec backend ls /app/tmp/`. 2. Rebuild or restore from source. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker and Docker Compose installed (Docker Engine ≥ 24)
- `.env` file at repo root containing all required secrets (see §5)
- `./database/*.db` SQLite files present
- `./postgres/init.sql` present

### Step-by-Step Deployment

```bash
# Step 1 — Clone / pull latest code
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
# or, if already cloned:
git pull origin main

# Step 2 — Verify .env is populated
cat .env   # must contain ANTHROPIC_API_KEY, GOOGLE_API_KEY, etc.

# Step 3 — Pull/build images
docker compose pull          # pull Redis, PostgreSQL base images
docker compose build         # build backend and frontend images

# Step 4 — Start infrastructure services first
docker compose up -d redis postgres

# Step 5 — Wait for Redis and Postgres to be ready (~5s)
sleep 5
docker compose exec postgres pg_isready -U chainlit

# Step 6 — Start backend and wait for healthy status
docker compose up -d backend
docker compose exec backend sh -c 'until curl -sf http://localhost:8000/health; do sleep 2; done'
echo "Backend healthy"

# Step 7 — Start frontend
docker compose up -d frontend

# Step 8 — Verify all containers
docker compose ps

# Step 9 — Smoke test
curl http://localhost:8000/health
```

### Rollback Steps

```bash
# Option A — Roll back to previous Docker image (if tagged)
docker compose down backend frontend
docker tag underwriting_chatbot-backend:previous underwriting_chatbot-backend:latest
docker compose up -d backend frontend

# Option B — Roll back to previous git commit
git log --oneline -5          # identify target commit SHA
git checkout <previous-sha>
docker compose build backend frontend
docker compose up -d --force-recreate backend frontend

# Option C — Full environment reset (DESTRUCTIVE — loses Redis state and Postgres data)
docker compose down -v        # removes named volumes
git checkout main
docker compose up -d --build
```

> **Note:** Rolling back the backend while keeping Redis state may cause `AsyncRedisSaver` schema mismatches. If sessions are corrupt post-rollback, flush Redis: `docker compose exec redis redis-cli FLUSHALL`

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Threshold / Alert |
|---|---|---|
| Backend health check pass/fail | Docker health probe (`/health`, every 10s) | Alert if `unhealthy` for > 3 consecutive checks |
| LLM specialist call duration | Backend stdout `[SPECIALIST] time=Xs` | Alert if any specialist > 30s |
| LLM aggregator call duration | Backend stdout `[AGGREGATOR] time=Xs` | Alert if > 60s |
| LLM token usage (specialist) | `in=X tok out=Y tok` log lines | Alert if `out` repeatedly hits `specialist_max_tokens` (1500) |
| LLM token usage (aggregator) | `[AGGREGATOR]` log lines | Alert if `out` approaches `aggregator_max_tokens` (8000) |
| Redis memory | `docker compose exec redis redis-cli info memory` → `used_memory_human` | [TODO: What is the memory ceiling for this deployment?] |
| PostgreSQL connections | `docker compose exec postgres psql -U chainlit -c "SELECT count(*) FROM pg_stat_activity;"` | Alert if > [TODO: define max connection threshold] |
| Container restart count | `docker compose ps` — `Restart` column | Alert on any container restarting > 3 times/hour |

### Log Sources

| Component | How to access | What to watch for |
|---|---|---|
| Backend application | `docker compose logs -f backend` | `[TOOL START/END]`, `[SPECIALIST]`, `[AGGREGATOR]`, `[ASSESSMENT]`, `ERROR`, `Exception`, HTTP 5xx |
| Frontend | `docker compose logs -f frontend` | Connection errors to backend, unhandled exceptions |
| Redis | `docker compose logs -f redis` | `OOM`, connection refused, `LOADING` state |
| PostgreSQL | `docker compose logs -f postgres` | `FATAL`, `ERROR`, connection limit warnings |
| GitHub Actions | GitHub repository → Actions tab | Workflow failures for tools 1–5 |

### Structured Log Patterns (Backend stdout)

```
[CHAT] session=<id> model=<model> mode=<mode> msg=<truncated>
[TOOL START] <tool_name>
[TOOL END]   <tool_name>  time=Xs
[SPECIALIST] category=<cat>  in=X tok  out=Y tok  time=Xs
[AGGREGATOR]                 in=X tok  out=Y tok  time=Xs
[ASSESSMENT] Starting — N specialist calls (mode=<mode>)
```

### Alerting

[TODO: Are there any existing alerting channels (PagerDuty, Slack, email) configured for this deployment?]  
[TODO: Is there a centralised log aggregation platform (e.g. Datadog, Azure Monitor, ELK)?]  
[TODO: What is the acceptable response time SLA for `/chat` endpoint?]

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: name/Slack/PagerDuty handle] | Service down, health check failing, containers not starting |
| L2 | Backend/ML Engineer | [TODO: name/Slack] | LLM errors, assessment logic failures, Redis/LangGraph issues |
| L3 | Tech Lead | [TODO: name/email] | Data loss, security incident, prolonged outage (> 1 hr) |
| L4 | Anthropic Support | support.anthropic.com | API outage, rate limit increases, billing issues |
| L4 | Google Cloud Support | [TODO: GCP support tier] | Gemini API outage |
| Business | Solution Owner | [TODO: name/email] | Go/no-go decisions, business-impacting incidents |

**Incident Communication Channel:** [TODO: Slack channel / Teams channel / PagerDuty policy name]  
**Incident Tracking:** [TODO: Jira project / ServiceNow queue]

---

## 7. Useful Commands

### Container Management

```bash
# Start all services
docker compose up -d

# Start with rebuild (after code changes)
docker compose up -d --build

# Stop all services (preserve volumes)
docker compose down

# Stop and remove all volumes (DESTRUCTIVE)
docker compose down -v

# Restart a single service
docker compose restart backend

# View all container statuses
docker compose ps

# Follow all logs
docker compose logs -f

# Follow backend logs only
docker compose logs -f backend

# View last 100 lines of backend logs
docker compose logs --tail=100 backend
```

### Health & Diagnostics

```bash
# Backend health check
curl -s http://localhost:8000/health | python3 -m json.tool

# Test chat endpoint (streaming)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "temperature": 0.3, "session_id": "test-001", "model": "anthropic-fast", "mode": "fast"}'

# Redis ping
docker compose exec redis redis-cli ping

# Redis info (memory, clients, etc.)
docker compose exec redis redis-cli info

# Flush all Redis sessions (DESTRUCTIVE — clears all conversation history)
docker compose exec redis redis-cli FLUSHALL

# PostgreSQL connection test
docker compose exec postgres psql -U chainlit -d chainlit -c "\conninfo"

# List active PostgreSQL connections
docker compose exec postgres psql -U chainlit -c "SELECT pid, usename, application_name, state FROM pg_stat_activity;"

# Check backend environment variables
docker compose exec backend env | sort

# Inspect backend container health
docker inspect underwriting_chatbot-main-backend-1 --format='{{json .State.Health}}' | python3 -m json.tool
```

### Database

```bash
# List mounted SQLite databases
docker compose exec backend ls -lh /data/

# Query customer profile (read-only)
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT * FROM customer LIMIT 5;"

# Check customer similarity file
docker compose exec backend cat /app/tmp/customer_similarity_dict.json | python3 -m json.tool | head -30
```

### Configuration

```bash
# View current LLM config
docker compose exec backend cat /app/config.yml

# Validate .env file has required keys
for key in ANTHROPIC_API_KEY GOOGLE_API_KEY; do
  grep -q "^${key}=" .env && echo "✓ ${key} present" || echo "✗ ${key} MISSING"
done
```

### GitHub Actions (Manual Triggers)

```bash
# Trigger code review via GitHub CLI
gh workflow run tool1_code_review.yml \
  -f review_mode=repo \
  --repo kylodeng/underwriting_chatbot-main

# Trigger documentation generation
gh workflow run tool2_tech_docs.yml \
  --repo kylodeng/underwriting_chatbot-main

# Trigger test generation
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate \
  --repo kylodeng/underwriting_chatbot-main

# View latest workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# View a specific run's logs
gh run view <run-