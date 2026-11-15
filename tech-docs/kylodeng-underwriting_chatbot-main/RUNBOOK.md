# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that exposes a streaming chat API (FastAPI, port 8000) backed by a multi-agent LangGraph pipeline. When an underwriter submits a question, an orchestrating LLM agent (Anthropic Claude Haiku or Sonnet via LangChain) calls a set of tools — customer profile lookup, lookalike matching, and a parallel multi-specialist underwriting risk assessment — before returning a structured `UnderwritingReport` with risk classification, findings, and follow-up items. Session memory is persisted in Redis (via `AsyncRedisSaver`) and customer data is served from three read-only SQLite databases (customer profile, feature importance, model predictions) and one application profile database, all mounted into the backend container. A PostgreSQL database backs the Chainlit frontend session store. The system is delivered as a four-container Docker Compose stack: `redis`, `postgres`, `backend`, and `frontend`.

---

## 2. Health Checks

### Backend API
```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Docker container states
```bash
docker compose ps
# All four services should show status: Up (healthy) for backend
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

### Backend logs (live)
```bash
docker compose logs -f backend
# Look for absence of ERROR/EXCEPTION and presence of [CHAT] log lines on requests
```

### Database file mounts
```bash
docker compose exec backend ls -lh /data/
# Expected: customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db
```

### End-to-end smoke test
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","temperature":0,"session_id":"smoke-test","model":"anthropic-fast","mode":"fast"}'
# Expected: SSE stream beginning with event: tool_start or event: response data
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container crashed or failed startup | 1. `docker compose logs backend` to identify error. 2. Check `.env` for missing secrets. 3. `docker compose restart backend`. |
| `{"detail": "..."}` 500 on `/chat` | Missing or invalid `ANTHROPIC_API_KEY` | 1. Verify secret in `.env`. 2. Test key with a direct Anthropic API call. 3. Restart backend. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in request does not match keys in `LLMS.model_mapper` | 1. Check valid values: `gemini`, `anthropic`, `anthropic-fast`. 2. Verify `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY` are set for the requested provider. |
| Redis connection error on startup / `ConnectionRefusedError` | Redis container not ready before backend starts | 1. `docker compose up -d redis && sleep 5 && docker compose up -d backend`. 2. Check `REDIS_HOST` env var is set to `redis` (not `localhost`). |
| Session memory not persisted across backend restarts | Redis is ephemeral (in-memory, not persisted to disk) | 1. Add `redis-stack-server` persistence config (AOF/RDB). 2. See TODO in `graph.py` re: external Redis service. |
| Assessment returns empty or truncated `UnderwritingReport` | Aggregator LLM hits `aggregator_max_tokens: 8000` limit | 1. Inspect `[AGGREGATOR]` log line for token counts. 2. Increase `aggregator_max_tokens` in `config.yml`. 3. Rebuild backend container. |
| `[SPECIALIST]` calls time out or hang | Anthropic API rate limit or network issue | 1. Check Anthropic API status page. 2. Review `[SPECIALIST]` log lines for elapsed times. 3. Reduce `asyncio.Semaphore(4)` in `assessment.py` to lower concurrency. |
| Frontend fails to load or shows blank page | Backend health check failing, so frontend container won't start | 1. Fix backend first (see row 1). 2. `docker compose up -d frontend` once backend is healthy. |
| PostgreSQL init fails | `postgres/init.sql` missing or syntax error | 1. `docker compose logs postgres`. 2. Verify `./postgres/init.sql` exists. 3. `docker compose down -v && docker compose up -d` to reinitialise. |
| SQLite database not found in `/data/` | Volume mount path incorrect or database files missing from host | 1. Verify files exist: `ls -lh ./database/*.db`. 2. Check `docker-compose.yml` volume mount paths. 3. Rebuild/restart backend. |
| `customer_similarity_dict.json` stale / missing lookalike results | File in `backend/tmp/` not updated after model retrain | 1. Re-run the similarity generation pipeline. 2. Rebuild the backend image or restart with updated file. [TODO: What process regenerates this file?] |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY not set` | Repository secret missing | 1. Go to repo → Settings → Secrets → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker Engine ≥ 24 and Docker Compose v2
- `.env` file at repo root (see Environment Variables table below)
- SQLite database files present under `./database/`

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic Claude API key |
| `GOOGLE_API_KEY` | Only if using `gemini` model | — | Google Generative AI key |
| `REDIS_HOST` | No | `localhost` | Redis hostname (should be `redis` in Docker Compose) |
| `DATABASE_URL` | Yes (frontend) | — | PostgreSQL connection string for Chainlit |
| [TODO: Are there any other required env vars for the frontend?] | | | |

### Deployment Steps

**1. Clone the repository**
```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

**2. Create the `.env` file**
```bash
cp .env.example .env   # [TODO: Does an .env.example exist?]
# Edit .env and populate all required secrets
```

**3. Confirm database files are present**
```bash
ls ./database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db
```

**4. Build and start all services**
```bash
docker compose up --build -d
```

**5. Confirm healthy state**
```bash
docker compose ps
curl -f http://localhost:8000/health
```

**6. Tail logs to verify normal startup**
```bash
docker compose logs -f backend
# Wait for uvicorn startup message; no CRITICAL/ERROR lines
```

**7. Access the frontend**
```
http://localhost:8080
```

---

### Rollback Steps

**Option A — Revert to previous image tag** (if images are tagged in a registry)
```bash
# [TODO: What container registry is used? ECR, ACR, Docker Hub?]
docker compose down
# Edit docker-compose.yml to pin image tag to previous version
docker compose up -d
```

**Option B — Revert via Git**
```bash
git log --oneline -10          # identify last known good commit
git checkout <commit-sha>
docker compose down
docker compose up --build -d
```

**Option C — Partial service rollback (backend only)**
```bash
docker compose stop backend
docker compose rm -f backend
# Rebuild from previous commit or pinned image
docker compose up --build -d backend
```

> ⚠️ **Data note:** Redis session state will be lost on `docker compose down` unless persistence is configured. PostgreSQL data persists in the `postgres_data` named volume.

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | What to watch | Threshold / Action |
|---|---|---|
| `/health` endpoint response | HTTP 200 | Alert if non-200 for > 30s |
| Backend container status | `docker compose ps` shows `Up (healthy)` | Alert if `unhealthy` or `Exit` |
| `[SPECIALIST]` LLM call duration | Log line: `time=X.XXs` | Investigate if consistently > 15s |
| `[AGGREGATOR]` token usage | `out=XXXX tok` in logs | Alert if approaching 8000 (config limit) |
| Redis memory usage | `redis-cli info memory` → `used_memory_human` | Alert if > 80% of available RAM |
| Anthropic API errors | `[CHAT]` log lines with exceptions | Any rate limit (429) or server error (5xx) |
| Docker container restarts | `docker compose ps` restart count | Alert on any unexpected restart |

### Logs to Watch

| Log source | Location | What to look for |
|---|---|---|
| Backend application | `docker compose logs backend` | `ERROR`, `EXCEPTION`, `ConnectionRefused`, `ValueError` |
| Redis | `docker compose logs redis` | OOM errors, connection refused |
| PostgreSQL | `docker compose logs postgres` | FATAL, connection errors |
| GitHub Actions | GitHub → Actions tab | Failed workflow runs for Tools 1–5 |

### Structured Log Patterns (backend)
```
[CHAT] session=<id> model=<model> mode=<mode> msg=<first 60 chars>
[TOOL START] <tool_name>
[TOOL END]   <tool_name>  time=<s>
[SPECIALIST] category=<cat>  in=<n> tok  out=<n> tok  time=<s>
[AGGREGATOR]                 in=<n> tok  out=<n> tok  time=<s>
[ASSESSMENT] Starting — <n> specialist calls (mode=<mode>)
```

### Alerting

[TODO: What observability stack is in use — Datadog, Prometheus/Grafana, Azure Monitor, CloudWatch?]  
[TODO: Are there any existing PagerDuty / OpsGenie integrations?]

Recommended alerting rules to configure:
1. `/health` returns non-200 for > 1 minute → page on-call
2. Backend container exits → page on-call
3. Redis container exits → page on-call
4. Anthropic API 429 rate-limit errors (3+ in 5 min) → Slack alert
5. GitHub Actions workflow failure on `main` branch → Slack alert

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: add PagerDuty/Slack handle] | Service down, health check failing |
| L2 | Backend Tech Lead | [TODO: add contact] | Persistent LLM errors, data corruption, Redis memory issues |
| L3 | ML / AI Engineer | [TODO: add contact] | Assessment quality issues, model card changes, `customer_similarity_dict.json` regeneration |
| L3 | Infrastructure / DevOps Lead | [TODO: add contact] | Docker host issues, database volume corruption, secrets rotation |
| External | Anthropic Support | https://support.anthropic.com | API outages, persistent 5xx errors from Claude |
| External | Google Cloud Support | [TODO: if Gemini is in production use] | Gemini API outages |

> Notify: `kylo.deng@capco.com` for AI tooling workflow failures (Tools 1–5 in GitHub Actions).

---

## 7. Useful Commands

### Service lifecycle
```bash
# Start all services
docker compose up -d

# Start with fresh build
docker compose up --build -d

# Stop all services (preserves volumes)
docker compose down

# Stop and destroy all volumes (destructive!)
docker compose down -v

# Restart a single service
docker compose restart backend

# View running containers and health
docker compose ps
```

### Logs
```bash
# Tail all services
docker compose logs -f

# Backend only
docker compose logs -f backend

# Last 100 lines of backend
docker compose logs --tail=100 backend

# Filter for errors
docker compose logs backend 2>&1 | grep -i "error\|exception\|critical"
```

### Health & connectivity checks
```bash
# API health
curl -f http://localhost:8000/health

# Redis ping
docker compose exec redis redis-cli ping

# Redis memory info
docker compose exec redis redis-cli info memory

# Flush Redis session store (WARNING: clears all sessions)
docker compose exec redis redis-cli flushall

# PostgreSQL connectivity
docker compose exec postgres pg_isready -U chainlit -d chainlit

# PostgreSQL interactive shell
docker compose exec postgres psql -U chainlit -d chainlit
```

### Database inspection
```bash
# List tables in customer profile DB
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"

# Row count check
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT COUNT(*) FROM customer_profile;"
# [TODO: Verify actual table name in customer_profile.db]
```

### Manual chat API test
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get me the profile for CUST00000001",
    "temperature": 0,
    "session_id": "runbook-test",
    "model": "anthropic-fast",
    "mode": "fast"
  }'
```

### Configuration changes (no image rebuild needed if mounted)
```bash
# Edit LLM config (token limits, temperature, default model)
nano backend/config.yml
docker compose restart backend
```

### GitHub Actions — trigger docs/review manually
```bash
# Trigger Tech Docs generation (requires gh CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Trigger Code Review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=42

# View recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10
```

### Image cleanup
```bash
# Remove unused images after deploy
docker image prune -f

# Full system prune (use with caution)
docker system prune -f
```