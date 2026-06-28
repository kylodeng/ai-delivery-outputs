# Operational Runbook — Underwriting Chatbot (`kylodeng/underwriting_chatbot-main`)

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The system is composed of four containerised services (frontend, backend, Redis, PostgreSQL) orchestrated via Docker Compose. The FastAPI backend hosts a LangGraph-powered agentic loop that orchestrates calls to Anthropic Claude LLMs (claude-sonnet and claude-haiku variants), a suite of underwriting specialist sub-agents, and customer profile tooling backed by SQLite databases. The frontend (Chainlit-based) streams responses to the user via Server-Sent Events. Conversation memory is persisted in Redis via `AsyncRedisSaver`, and session/user state is stored in PostgreSQL. The model card references a CatBoost classifier (`Underwriting Risk Classification`) trained on merged customer datasets; the primary risk signals are medical conditions (18.9%), age (34.6%), and smoker status (14.6%).

---

## 2. Health Checks

### 2.1 Backend API Health

```bash
curl -sf http://localhost:8000/health
# Expected: {"status": "ok"}
```

The Docker Compose `healthcheck` polls this endpoint every 10 seconds; the frontend will not start until it passes.

### 2.2 Redis

```bash
docker compose exec redis redis-cli ping
# Expected: PONG
```

Check Redis is accepting connections and that the LangGraph checkpointer can persist state:

```bash
docker compose exec redis redis-cli info server | grep redis_version
```

### 2.3 PostgreSQL

```bash
docker compose exec postgres pg_isready -U chainlit -d chainlit
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 2.4 Frontend

```bash
curl -sf http://localhost:8080
# Expected: HTTP 200
```

### 2.5 Container Status (all services)

```bash
docker compose ps
# All four services should show: Up (healthy) or Up
```

### 2.6 SQLite Databases (read-only mounts)

```bash
ls -lh ./database/*.db
# Confirm files are present and non-zero size
```

### 2.7 LLM Connectivity

```bash
# Confirm Anthropic API key is present and reachable
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq .
```

[TODO: Is there a Google Gemini key also required in production? `GOOGLE_API_KEY` is referenced in `LLMS.py`.]

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `POST /chat` returns 500 immediately | Missing or invalid `ANTHROPIC_API_KEY` env var | 1. Check `.env` file for `ANTHROPIC_API_KEY`. 2. Verify key validity: `curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"`. 3. Restart backend: `docker compose restart backend`. |
| Chat hangs indefinitely / SSE stream never closes | Redis connection failure causing LangGraph checkpointer to block | 1. `docker compose exec redis redis-cli ping`. 2. Check `REDIS_HOST` env var is set to `redis` (not `localhost`) inside the backend container. 3. Restart Redis then backend: `docker compose restart redis backend`. |
| Frontend fails to start / stays in "waiting" state | Backend healthcheck failing; frontend depends on `service_healthy` | 1. `docker compose logs backend --tail=50`. 2. Manually hit `http://localhost:8000/health`. 3. Fix root cause (LLM key, DB mount, port conflict) then `docker compose up -d`. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request maps to `None` in `LLMS.model_mapper` (e.g. `azure`, `openai`) | 1. Confirm the request body `model` field is one of: `anthropic`, `anthropic-fast`, `gemini`. 2. Do not use `azure` or `openai` — they are not yet implemented. |
| `KeyError: GOOGLE_API_KEY` on startup | `gemini` model selected but `GOOGLE_API_KEY` not set in `.env` | 1. Add `GOOGLE_API_KEY=<your-key>` to `.env`. 2. Or avoid selecting the `gemini` model from the frontend until the key is configured. |
| Assessment returns incomplete/garbled JSON | Aggregator LLM token budget exceeded or specialist output truncated | 1. Check `config.yml`: `aggregator_max_tokens` should be ≥ 8000. 2. Check `specialist_max_tokens` — currently 1500; increase cautiously. 3. Review backend logs for `[AGGREGATOR]` output token counts. |
| `customer_similarity_dict.json` lookup returns empty | File missing from `backend/tmp/` or customer ID not present in the dict | 1. Confirm file exists: `ls -lh backend/tmp/customer_similarity_dict.json`. 2. Verify the customer ID format matches `CUST########`. 3. Rebuild/re-populate the similarity dict if stale. |
| SQLite DB data not found (`customer_profile.db` etc.) | Volume mount path mismatch or file absent on host | 1. Confirm files exist on host: `ls ./database/`. 2. Check `docker-compose.yml` volume paths. 3. `docker compose down && docker compose up -d` after restoring DB files. |
| PostgreSQL connection refused from frontend | Postgres container not ready, or `DATABASE_URL` env var misconfigured | 1. `docker compose logs postgres --tail=30`. 2. Confirm `POSTGRES_USER/PASSWORD/DB` match the `DATABASE_URL` in the frontend env. 3. `docker compose restart postgres frontend`. |
| Redis memory not persisting across restarts | Redis is run without AOF/RDB persistence; state is ephemeral | 1. This is a **known architectural gap** (noted in `graph.py` TODO). 2. Mitigate by using `redis/redis-stack-server` with persistence flags or migrate to Azure Cache for Redis. See §5. |
| GitHub Actions workflow fails (`tool1`–`tool5`) | Missing GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) | 1. Navigate to repo → Settings → Secrets and variables → Actions. 2. Confirm all three secrets are present. 3. Re-run the failed workflow. |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- Docker ≥ 24 and Docker Compose v2 installed on the host
- `.env` file present at repo root with all required secrets (see §5)
- SQLite database files present in `./database/`
- [TODO: Is there a container registry? Are images pre-built or built locally on every deploy?]
- [TODO: What is the target deployment environment — bare metal, VM, Kubernetes, Azure Container Apps?]

### 4.2 First-Time Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create .env from template
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate all required secrets

# 3. Build and start all services
docker compose up --build -d

# 4. Confirm health
docker compose ps
curl -sf http://localhost:8000/health
```

### 4.3 Standard Deployment (code update)

```bash
# 1. Pull latest changes
git pull origin main

# 2. Rebuild affected images (backend or frontend)
docker compose build backend frontend

# 3. Rolling restart — bring up new containers
docker compose up -d --no-deps backend frontend

# 4. Verify health
docker compose ps
curl -sf http://localhost:8000/health

# 5. Smoke-test a chat request
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
```

### 4.4 Config-Only Change (`config.yml`)

```bash
# config.yml is read at process startup — a restart is required
docker compose restart backend
```

### 4.5 Rollback Procedure

```bash
# 1. Identify the previous working image tag or git commit
git log --oneline -5

# 2. Check out the last known-good commit
git checkout <previous-commit-sha>

# 3. Rebuild and redeploy
docker compose build backend frontend
docker compose up -d --no-deps backend frontend

# 4. Verify health
docker compose ps
curl -sf http://localhost:8000/health
```

> **Note:** If Redis state was modified by a broken deployment, flush sessions with caution:
> ```bash
> docker compose exec redis redis-cli FLUSHDB   # ⚠️ deletes all session checkpoints
> ```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where | Alert Threshold |
|---|---|---|
| Backend HTTP 5xx rate | Container logs / reverse proxy access log | > 1% of requests over 5 min |
| `/chat` response latency (time-to-first-token) | Backend stdout `[CHAT]` log lines | > 15 s |
| Specialist LLM call duration | Backend stdout `[SPECIALIST]` log lines | > 10 s per category |
| Aggregator LLM output tokens | Backend stdout `[AGGREGATOR]` log lines | Approaching `aggregator_max_tokens` (8000) |
| Redis memory usage | `docker compose exec redis redis-cli info memory` | > 80% `maxmemory` |
| PostgreSQL connection pool | `docker compose logs postgres` | Any connection refused errors |
| Anthropic API rate limit errors | Backend stderr | Any `429` responses |
| Container restart count | `docker compose ps` or Docker daemon | Any container restarting in a loop |

### 5.2 Log Locations & Patterns

```bash
# All services (follow)
docker compose logs -f

# Backend only — look for LLM timing and errors
docker compose logs -f backend

# Key log patterns to grep for:
docker compose logs backend | grep "\[TOOL START\]\|\[TOOL END\]\|\[SPECIALIST\]\|\[AGGREGATOR\]\|\[ASSESSMENT\]"
docker compose logs backend | grep -i "error\|exception\|traceback"
```

### 5.3 Important Log Prefixes (backend stdout)

| Prefix | Meaning |
|---|---|
| `[CHAT]` | New request received — session, model, mode, message |
| `[TOOL START] / [TOOL END]` | LangGraph tool invocation start and finish with elapsed time |
| `[SPECIALIST]` | Per-category LLM call with token counts and latency |
| `[AGGREGATOR]` | Final aggregation call with token counts and latency |
| `[ASSESSMENT]` | Assessment workflow start |

### 5.4 GitHub Actions Workflow Monitoring

- Navigate to **Actions** tab in the repository to view status of all five AI delivery tools.
- Email notifications are sent via SendGrid to `kylo.deng@capco.com` on workflow completion.
- Audit logs are written to the `ai-delivery-outputs` repository.

[TODO: Is there a centralised logging platform (e.g., Azure Monitor, Datadog, ELK)? Currently all logging is stdout-only.]

[TODO: Are there any uptime monitors (e.g. Pingdom, UptimeRobot) configured for `/health`?]

[TODO: Are Anthropic API spend limits or quota alerts configured in the Anthropic console?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: on-call engineer name/email/Slack] | Service down, health check failing, containers not starting |
| L2 | Backend / AI Engineer | [TODO: backend engineer name/email] | LLM errors, agent loop failures, assessment quality issues |
| L3 | Platform / DevOps Lead | [TODO: DevOps lead name/email] | Infrastructure failure, data loss, Redis/Postgres corruption |
| L4 | Anthropic Support | https://support.anthropic.com | Sustained API outage, unexpected model behaviour, billing issues |
| Notify | Product Owner | [TODO: PO name/email] | Any P1 incident affecting underwriter workflows in production |

[TODO: What is the SLA for this service? Is there an on-call rotation?]

[TODO: Is there a PagerDuty / OpsGenie integration?]

---

## 7. Useful Commands

### Container Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop and remove volumes (⚠️ destroys Postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend

# Rebuild a single service image
docker compose build backend

# View real-time logs for all services
docker compose logs -f

# View logs for a single service
docker compose logs -f backend --tail=100
```

### Health & Status

```bash
# Check container status
docker compose ps

# Backend health endpoint
curl -sf http://localhost:8000/health | jq .

# Redis ping
docker compose exec redis redis-cli ping

# PostgreSQL readiness
docker compose exec postgres pg_isready -U chainlit -d chainlit

# Redis memory info
docker compose exec redis redis-cli info memory

# Redis key count (session checkpoints)
docker compose exec redis redis-cli dbsize
```

### LLM Connectivity

```bash
# Test Anthropic API key
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.data[].id'
```

### Smoke Test Chat Endpoint

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me about customer CUST00000001",
    "temperature": 0.3,
    "session_id": "runbook-smoke-test",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### Session / Memory Management

```bash
# List all Redis keys (session checkpoints)
docker compose exec redis redis-cli keys '*'

# Flush all session state (⚠️ logs all users out)
docker compose exec redis redis-cli FLUSHDB

# Delete a specific session
docker compose exec redis redis-cli DEL <session_key>
```

### Database Inspection

```bash
# Check SQLite database sizes
ls -lh ./database/*.db

# Query customer profile database
sqlite3 ./database/customer_profile.db "SELECT COUNT(*) FROM sqlite_master;"

# Connect to PostgreSQL
docker compose exec postgres psql -U chainlit -d chainlit

# List PostgreSQL tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"
```

### Config Validation

```bash
# View current LLM config
cat backend/config.yml

# View model card
cat backend/model_card.json | jq '{model_name,version,deployment_date,top_features: .global_feature_importance}'
```

### GitHub Actions (AI Delivery Tools)

```bash
# Trigger tech docs generation manually (requires GitHub CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# View recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# View logs for a specific run
gh run view <run-id> --log
```

---

*Runbook generated from source: `kylodeng/underwriting_chatbot-main`. Mark all `[TODO:]` items for review before production use.*