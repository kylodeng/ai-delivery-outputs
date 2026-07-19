# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-tier, AI-assisted life insurance underwriting platform designed for use by professional underwriters. It exposes a streaming chat API (FastAPI, port 8000) backed by a LangGraph agent that orchestrates calls to two LLM providers (Anthropic Claude and Google Gemini) via configurable model aliases. When asked to assess a customer, the agent retrieves a customer profile from a SQLite database, optionally finds lookalike customers via a pre-computed similarity dictionary, and invokes a parallel multi-specialist underwriting assessment pipeline. Each specialist LLM evaluates a domain category (finance, health, life, etc.) concurrently; an aggregator LLM then consolidates findings into a structured `UnderwritingReport`. Conversation state is checkpointed in Redis (via LangGraph's `AsyncRedisSaver`), and the frontend (port 8080) communicates with the backend over Server-Sent Events (SSE). The stack is orchestrated locally with Docker Compose, and CI/CD automation runs five GitHub Actions workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) powered by Anthropic's Claude Sonnet.

---

## 2. Health Checks

### 2.1 Backend API

```bash
curl -sf http://localhost:8000/health
# Expected: {"status": "ok"}
```

### 2.2 Docker Compose Service Status

```bash
docker compose ps
# All services should show status: running (healthy) for backend
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

### 2.5 Frontend Reachability

```bash
curl -sf -o /dev/null -w "%{http_code}" http://localhost:8080
# Expected: 200
```

### 2.6 LLM Provider Reachability

```bash
# Verify Anthropic API key is valid
curl -sf https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.models[0].id'

# Verify Google API key is valid
curl -sf "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY" \
  | jq '.models[0].name'
```

### 2.7 SQLite Database Files Present

```bash
ls -lh database/customer_profile.db \
       database/feature_importance.db \
       database/model_predictions.db \
       database/application_profile.db
# All four files must be present and non-zero
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `backend` container exits at startup with `KeyError: 'ANTHROPIC_API_KEY'` | Missing environment variable in `.env` file | 1. Check `.env` exists in repo root. 2. Ensure `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` are set. 3. `docker compose down && docker compose up -d` |
| `backend` health check fails; container restarts in a loop | Redis not yet accepting connections when backend starts, or port 8000 already in use | 1. `docker compose logs backend` for traceback. 2. Confirm Redis is healthy: `docker compose ps redis`. 3. Increase `start_period` in healthcheck if timing issue. 4. `lsof -i :8000` to check port conflict |
| Chat returns `500` / SSE stream closes immediately | LangGraph agent fails to connect to Redis checkpointer | 1. `docker compose logs backend` — look for `redis.exceptions.ConnectionError`. 2. Verify `REDIS_HOST=redis` is set in environment. 3. Restart Redis: `docker compose restart redis` |
| Chat hangs indefinitely with no SSE events | LLM provider rate limit or timeout | 1. Check Anthropic/Google console for quota errors. 2. `docker compose logs backend` — look for `RateLimitError` or `timeout`. 3. Switch model alias to `anthropic-fast` (Haiku) in request payload |
| Assessment returns incomplete `UnderwritingReport` / JSON parse error | Aggregator LLM exceeded `aggregator_max_tokens: 8000` or returned malformed JSON | 1. `docker compose logs backend` — look for `[AGGREGATOR]` output token count. 2. Increase `aggregator_max_tokens` in `backend/config.yml`. 3. Retry request |
| `ValueError: Unsupported or unconfigured model provider: <name>` | Unknown model alias passed in chat request | 1. Valid aliases: `anthropic`, `anthropic-fast`, `gemini`. 2. Check frontend is sending a supported `model` field. 3. See `backend/modules/LLMS.py` for full list |
| `customer_similarity_dict.json` lookup returns empty / KeyError | Customer ID not found in pre-computed similarity file | 1. Verify `backend/tmp/customer_similarity_dict.json` contains the queried customer ID. 2. Re-run similarity computation if a new customer dataset has been loaded |
| Database mount returns `Read-only file system` error | SQLite `.db` files missing or path mismatch in `docker-compose.yml` | 1. Confirm all four `.db` files exist in `./database/`. 2. Check volume mounts in `docker-compose.yml` match actual file paths. 3. `docker compose down -v && docker compose up -d` |
| Frontend shows blank screen / cannot connect to backend | `BACKEND_URL` env var incorrect, or backend not healthy before frontend starts | 1. Confirm `BACKEND_URL=http://backend:8000` in frontend environment. 2. Check `depends_on: backend: condition: service_healthy` — backend must pass health check first. 3. `docker compose restart frontend` |
| GitHub Actions workflow fails with `403` on output repo write | `GH_TOKEN` lacks write permission to `ai-delivery-outputs` repo | 1. Regenerate PAT with `repo` scope. 2. Update `GH_TOKEN` secret in GitHub repository settings |
| GitHub Actions `tool1_code_review` posts no PR comment | `SENDGRID_API_KEY` or `GH_TOKEN` secret missing from Actions secrets | 1. Go to **Settings → Secrets → Actions**. 2. Verify `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are all set |
| Redis memory exhausted; checkpoint writes fail | Conversation history growing unboundedly in Redis | 1. `docker compose exec redis redis-cli INFO memory`. 2. Flush stale sessions: `docker compose exec redis redis-cli FLUSHDB` (⚠️ clears all sessions). 3. [TODO: implement Redis TTL policy for checkpoints] |
| PostgreSQL init fails; frontend cannot persist chat history | `./postgres/init.sql` missing or syntax error | 1. `docker compose logs postgres`. 2. Confirm `postgres/init.sql` exists. 3. `docker compose down -v && docker compose up -d` to re-run init |

---

## 4. Deployment Procedure

### Prerequisites

- Docker ≥ 24.x and Docker Compose v2 installed
- `.env` file in repo root with all required variables (see §5)
- All four SQLite database files present in `./database/`
- Access to `kylodeng/underwriting_chatbot-main` repository

---

### 4.1 Initial Deploy

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create and populate .env
cp .env.example .env          # [TODO: confirm .env.example exists or create manually]
# Edit .env — see §5 for required variables

# 3. Confirm database files are in place
ls database/*.db

# 4. Build and start all services
docker compose build --no-cache
docker compose up -d

# 5. Verify all services healthy
docker compose ps
curl -sf http://localhost:8000/health
```

---

### 4.2 Routine Update Deploy (rolling)

```bash
# 1. Pull latest changes
git pull origin main

# 2. Rebuild only changed images
docker compose build backend frontend

# 3. Restart services with zero-downtime for stateless frontend
docker compose up -d --no-deps --force-recreate frontend
docker compose up -d --no-deps --force-recreate backend

# 4. Verify health
sleep 15
curl -sf http://localhost:8000/health
docker compose ps
```

---

### 4.3 Configuration-Only Change (no code change)

```bash
# 1. Edit backend/config.yml (e.g. adjust max_tokens)
# 2. Restart only backend
docker compose restart backend

# 3. Verify
curl -sf http://localhost:8000/health
```

---

### 4.4 Rollback Steps

```bash
# Option A — roll back to previous Git commit
git log --oneline -5          # identify previous good commit SHA
git checkout <previous-sha>

docker compose build backend frontend
docker compose up -d --no-deps --force-recreate backend frontend

curl -sf http://localhost:8000/health

# Option B — roll back using Docker image tag (if images are tagged)
# [TODO: confirm whether images are pushed to a registry and tagged by version]
# docker compose pull             # pull previous pinned tag
# docker compose up -d

# Option C — emergency stop
docker compose down
```

> ⚠️ **Redis state**: Rolling back does NOT clear Redis checkpoint data. If the rollback is due to a breaking schema change in `AgentState`, flush Redis first:
> ```bash
> docker compose exec redis redis-cli FLUSHDB
> ```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where to observe | Alert threshold |
|---|---|---|
| Backend health endpoint | `GET /health` → `{"status":"ok"}` | Any non-200 response |
| Backend container restart count | `docker compose ps` / Docker daemon | > 2 restarts in 5 min |
| Redis memory usage | `redis-cli INFO memory` → `used_memory_human` | [TODO: set threshold based on deployment RAM] |
| LLM token consumption (specialist) | stdout log `[SPECIALIST]` lines | `out=` tokens consistently near 1500 (cap) |
| LLM token consumption (aggregator) | stdout log `[AGGREGATOR]` lines | `out=` tokens > 7500 (near 8000 cap) |
| Tool execution time | stdout log `[TOOL END] ... time=Xs` | > 30s per tool call |
| Total request latency | stdout log `[CHAT]` + SSE stream close | [TODO: define SLA] |
| PostgreSQL disk usage | `docker compose exec postgres psql -U chainlit -c "SELECT pg_size_pretty(pg_database_size('chainlit'));"` | [TODO: set threshold] |

### 5.2 Log Locations

```bash
# All service logs (follow mode)
docker compose logs -f

# Backend only (most verbose)
docker compose logs -f backend

# Filter for errors only
docker compose logs backend 2>&1 | grep -i "error\|exception\|traceback"

# Filter for LLM token usage summary
docker compose logs backend 2>&1 | grep -E "\[SPECIALIST\]|\[AGGREGATOR\]|\[ASSESSMENT\]"

# Filter for tool timing
docker compose logs backend 2>&1 | grep -E "\[TOOL START\]|\[TOOL END\]"
```

### 5.3 GitHub Actions Workflow Monitoring

| Workflow | Schedule | What to check |
|---|---|---|
| Tool 1 — Code Review | On PR open/sync; Mon 08:00 UTC | PR comment posted; no `JSON parse error` in logs |
| Tool 2 — Tech Docs | On push to `main`; Sun 06:00 UTC | Commit to `ai-delivery-outputs` repo |
| Tool 3 — Business Docs | On `v*` tag push | Output file present in output repo |
| Tool 4 — Auto Testing | On PR open; Wed 07:00 UTC | Test files written; no `ValueError` |
| Tool 5 — UAT | On `release/*` branch create | Test pack CSV written to output repo |

```bash
# View workflow run status via GitHub CLI
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10
gh run view <run-id> --log
```

### 5.4 Alerting

[TODO: No alerting infrastructure (PagerDuty, Datadog, Prometheus, etc.) is evident in the codebase — define alerting stack and thresholds]

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call rotation / Slack channel] | Service down > 5 min, health check failing |
| L2 | Backend Tech Lead | [TODO: fill in name and contact] | LLM errors, Redis data loss, database corruption |
| L3 | Platform / DevOps Lead | [TODO: fill in name and contact] | Infrastructure failure, secrets rotation needed |
| L4 | Vendor Support | Anthropic: [support.anthropic.com](https://support.anthropic.com) / Google Cloud Support | LLM API outage, quota breach, billing issue |
| Business Owner | Solution Owner | [TODO: fill in name and contact] | Data breach, compliance issue, production outage > 1 hr |

> **Notification email** (from CI/CD workflows): `kylo.deng@capco.com`

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop and remove all volumes (DESTRUCTIVE — wipes PostgreSQL and Redis data)
docker compose down -v

# Restart a single service
docker compose restart backend
docker compose restart redis
docker compose restart frontend

# Rebuild and restart backend after code change
docker compose build backend && docker compose up -d --no-deps --force-recreate backend

# View real-time logs for all services
docker compose logs -f

# View last 100 lines from backend
docker compose logs --tail=100 backend
```

### Health & Diagnostics

```bash
# Full health check
curl -sf http://localhost:8000/health | jq .

# Test a chat request (streaming)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'

# Redis health
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli INFO server | grep redis_version
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Redis — list all keys (session checkpoints)
docker compose exec redis redis-cli KEYS "*"

# Redis — flush all session data (use with caution)
docker compose exec redis redis-cli FLUSHDB

# PostgreSQL health
docker compose exec postgres pg_isready -U chainlit
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# Check all database file sizes
ls -lh database/*.db
```

### Configuration

```bash
# View current LLM config
cat backend/config.yml

# Edit LLM token limits (e.g. raise aggregator cap)
# Edit backend/config.yml: aggregator_max_tokens: <value>
# Then: docker compose restart backend

# View model card (feature importances)
cat backend/model_card.json | jq '.global_feature_importance'
```

### GitHub Actions (requires `gh` CLI)

```bash
# List recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# Watch a running workflow
gh run watch --repo kylodeng/underwriting_chatbot-main

# Manually trigger tech docs generation
gh workflow run tool2_tech_docs.yml