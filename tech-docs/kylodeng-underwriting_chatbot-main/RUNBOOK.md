# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a multi-tier AI-assisted life insurance underwriting platform. It exposes a streaming chat API (FastAPI, port 8000) that drives a LangGraph-based agent capable of fetching customer profiles, running lookalike analysis, and executing parallel multi-category underwriting risk assessments powered by Anthropic Claude and Google Gemini LLMs. The backend relies on a Redis instance (port 6379) for LangGraph conversation checkpoint persistence, a PostgreSQL database (port 5432) for the Chainlit frontend session store, and three read-only SQLite databases mounted at `/data/` that hold customer profiles, feature importance scores, and model predictions. The frontend (port 8080) is a Chainlit UI that communicates with the backend over HTTP SSE. Continuous-integration pipelines in `.github/workflows/` automate code review, documentation generation, test generation, and UAT facilitation using the Claude Sonnet/Haiku model family via the `anthropic` API.

---

## 2. Health Checks

| Component | What to check | Expected result |
|---|---|---|
| **Backend API** | `GET http://localhost:8000/health` | `{"status": "ok"}` HTTP 200 |
| **Backend container** | `docker compose ps backend` | State = `running (healthy)` |
| **Redis** | `docker compose exec redis redis-cli ping` | `PONG` |
| **PostgreSQL** | `docker compose exec postgres pg_isready -U chainlit` | `accepting connections` |
| **Frontend** | `curl -sf http://localhost:8080` | HTTP 200 with HTML body |
| **LLM connectivity** | Check backend logs for `[SPECIALIST]` or `[AGGREGATOR]` lines after a test chat | Token counts printed, no `AuthenticationError` |
| **SQLite mounts** | `docker compose exec backend ls -lh /data/*.db` | Three `.db` files present and non-zero size |
| **Redis checkpoint** | `docker compose exec redis redis-cli dbsize` | Non-zero after first conversation |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container not started or crashed on startup | 1. `docker compose logs backend --tail=50` to find the error. 2. Check `.env` file exists and all required vars are set. 3. `docker compose up -d --force-recreate backend`. |
| Backend container stuck in `starting` / health-check loop | Redis not yet ready when backend starts, or Redis auth failure | 1. `docker compose ps redis` — confirm Redis is healthy first. 2. Check `REDIS_HOST` env var equals `redis` (not `localhost`) inside Docker. 3. `docker compose restart backend`. |
| `AuthenticationError` / `401` in backend logs | Missing or invalid `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` | 1. Verify keys in `.env`. 2. Test key manually: `curl https://api.anthropic.com/v1/models -H "x-api-key: $KEY"`. 3. Rotate key in provider console and update `.env`, then `docker compose up -d`. |
| `ValueError: Unsupported or unconfigured model provider` | `model` field in chat request does not match `LLMS.model_mapper` keys | 1. Confirm request sends one of: `anthropic`, `anthropic-fast`, `gemini`. 2. Check `backend/modules/LLMS.py` for available keys. 3. Update `config.yml` `default` if needed. |
| Assessment hangs / times out | LLM API rate limit, network issue, or semaphore deadlock in `_run_underwriting_assessment` | 1. Check backend logs for `[SPECIALIST]` lines — if absent, LLM call is blocked. 2. Check Anthropic/GCP API status pages. 3. Reduce `specialist_max_tokens` in `config.yml` if hitting token limits. 4. Restart backend. |
| Chat responses not streaming / SSE connection drops | CORS or proxy stripping `Content-Type: text/event-stream` | 1. Confirm `allow_origins=["*"]` is in place (already set). 2. Check any reverse proxy / API gateway for SSE pass-through config. 3. Test direct backend SSE: `curl -N http://localhost:8000/chat -X POST -H "Content-Type: application/json" -d '{"message":"hi","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'`. |
| PostgreSQL connection refused or frontend DB errors | PostgreSQL container not running, or wrong `DATABASE_URL` | 1. `docker compose ps postgres`. 2. `docker compose logs postgres --tail=30`. 3. Verify `DATABASE_URL` in frontend env matches `postgres:5432`. 4. If volume corrupted: `docker compose down -v && docker compose up -d` (**data loss — back up first**). |
| SQLite data not found / empty results | Database files not mounted correctly | 1. `docker compose exec backend ls -lh /data/`. 2. Verify `./database/` directory exists on host with the three `.db` files. 3. Check volume mount paths in `docker-compose.yml`. |
| Redis data lost across restarts | Redis running without persistence (default for `redis-stack-server`) | 1. Add `command: redis-server --appendonly yes` to the redis service in `docker-compose.yml`. 2. Mount a volume: `- redis_data:/data`. 3. See TODO in `graph.py` re: migrating to external managed Redis. |
| GitHub Actions workflow failing (`ANTHROPIC_API_KEY` / `GH_TOKEN` / `SENDGRID_API_KEY`) | Repository secrets not set | 1. Go to repo **Settings → Secrets and variables → Actions**. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. 3. Re-run failed workflow. |
| Tool 1/2/3/4/5 workflow: `Could not parse Claude response as JSON` | Claude returned markdown-fenced JSON or malformed output | 1. Check workflow run logs for `[DEBUG] First 500 chars`. 2. Increase `max_tokens` in `call_claude()` if response is truncated. 3. Re-run workflow — transient LLM formatting issue. |

---

## 4. Deployment Procedure

### Prerequisites
- Docker Engine ≥ 24 and Docker Compose v2 installed on the host
- `.env` file at repo root with all required environment variables (see §5)
- SQLite database files present under `./database/`

### Deploy Steps

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create and populate the .env file
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env — see Environment Variables table

# 3. Pull latest images and rebuild application containers
docker compose pull redis postgres
docker compose build backend frontend

# 4. Start infrastructure services first
docker compose up -d redis postgres

# 5. Wait for infrastructure to be healthy (≈10s)
docker compose ps

# 6. Start application services
docker compose up -d backend frontend

# 7. Verify health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 8. Tail logs to confirm no errors
docker compose logs -f backend --tail=50
```

### Rollback Steps

```bash
# Option A — Revert to previous Docker image tag (if images are versioned)
# [TODO: confirm image registry and tagging strategy]

# Option B — Roll back via git and rebuild
git log --oneline -10          # identify the last known-good commit
git checkout <commit-sha>
docker compose build backend frontend
docker compose up -d backend frontend

# Option C — Restore PostgreSQL data from backup (if schema changed)
docker compose exec -T postgres psql -U chainlit chainlit < backup.sql

# Confirm rollback successful
curl http://localhost:8000/health
docker compose ps
```

> **Note:** Redis checkpoints are session-specific and non-critical. Flushing Redis on rollback (`docker compose exec redis redis-cli FLUSHALL`) will clear all active conversation states — users will lose chat history.

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Threshold / Alert |
|---|---|---|
| Backend health endpoint | `GET /health` | Any non-200 → alert |
| Container restart count | `docker compose ps` / `docker stats` | Restart > 2 in 5 min → alert |
| LLM specialist latency | Backend stdout `[SPECIALIST] time=Xs` | > 30s per specialist → investigate |
| LLM aggregator latency | Backend stdout `[AGGREGATOR] time=Xs` | > 60s → investigate |
| LLM token usage | Backend stdout `in=X tok out=X tok` | Approaching provider rate limits |
| Redis memory | `docker compose exec redis redis-cli info memory` | `used_memory_human` trending up without bound |
| PostgreSQL disk | `df -h` on host | > 80% disk usage on postgres volume |
| GitHub Actions | Workflow run status | Any `failure` on `main` branch push |

### Log Locations

```bash
# Backend application logs (LLM calls, tool start/end, errors)
docker compose logs backend -f

# Frontend logs
docker compose logs frontend -f

# Redis logs
docker compose logs redis -f

# PostgreSQL logs
docker compose logs postgres -f

# GitHub Actions workflow logs
# Navigate to: https://github.com/kylodeng/underwriting_chatbot-main/actions
```

### Key Log Patterns to Alert On

| Log Pattern | Meaning |
|---|---|
| `AuthenticationError` | LLM API key invalid/expired |
| `Could not parse Claude response as JSON` | LLM output parsing failure |
| `[TOOL START]` without matching `[TOOL END]` | Tool call hung |
| `Error` / `Exception` / `Traceback` in backend logs | Unhandled exception |
| `ConnectionRefusedError` / `redis.exceptions.ConnectionError` | Redis unreachable |
| `asyncpg.exceptions` | PostgreSQL unreachable |

### Alerting

[TODO: What alerting platform is in use — PagerDuty, OpsGenie, Datadog, CloudWatch?]
[TODO: Are there any existing uptime monitors configured for the backend `/health` endpoint?]
[TODO: Should LLM API cost/token-usage alerts be set up in the Anthropic console?]

---

## 6. Escalation Path

| Level | Who | When to Escalate | Contact |
|---|---|---|---|
| L1 — On-call engineer | [TODO: on-call rotation name] | Service down > 5 minutes, health check failing | [TODO: Slack channel / PagerDuty policy] |
| L2 — Backend developer | [TODO: backend owner name] | LLM integration errors, assessment failures, code bugs | [TODO: email / Slack handle] |
| L3 — Infrastructure / Platform | [TODO: infra team name] | Docker host issues, Redis/PostgreSQL data loss, network | [TODO: contact] |
| L4 — LLM vendor support | Anthropic / Google Cloud | API outage, billing issues, model deprecation | [Anthropic status](https://status.anthropic.com) / [GCP status](https://status.cloud.google.com) |
| Project owner | Kylo Deng (kylo.deng@capco.com) | Business-critical escalations, architectural decisions | kylo.deng@capco.com |

---

## 7. Useful Commands

### Service Management

```bash
# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Restart a single service
docker compose restart backend

# Force rebuild and restart backend
docker compose up -d --build backend

# View running container status
docker compose ps

# View resource usage
docker stats
```

### Health & Debugging

```bash
# Backend health check
curl -s http://localhost:8000/health | python3 -m json.tool

# Test chat endpoint (streaming)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"runbook-test","model":"anthropic-fast","mode":"fast"}'

# Tail backend logs (live)
docker compose logs -f backend

# Tail all logs
docker compose logs -f

# Follow only errors
docker compose logs backend 2>&1 | grep -i "error\|exception\|traceback"
```

### Redis

```bash
# Ping Redis
docker compose exec redis redis-cli ping

# Check memory usage
docker compose exec redis redis-cli info memory

# Count stored keys (conversation checkpoints)
docker compose exec redis redis-cli dbsize

# List all keys
docker compose exec redis redis-cli keys '*'

# Flush all conversation state (WARNING: destroys all sessions)
docker compose exec redis redis-cli FLUSHALL
```

### PostgreSQL

```bash
# Check connection
docker compose exec postgres pg_isready -U chainlit

# Connect to database
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit -c '\dt'

# Backup database
docker compose exec -T postgres pg_dump -U chainlit chainlit > backup_$(date +%Y%m%d).sql

# Restore database
docker compose exec -T postgres psql -U chainlit chainlit < backup_20240601.sql
```

### SQLite Databases

```bash
# List mounted databases
docker compose exec backend ls -lh /data/

# Query customer profile (example)
docker compose exec backend sqlite3 /data/customer_profile.db ".tables"
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT * FROM customer LIMIT 5;"
# [TODO: confirm actual table names in customer_profile.db]
```

### GitHub Actions (CI/CD)

```bash
# Manually trigger tech docs generation (requires GitHub CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger code review on a PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=42

# View recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# Watch a specific run
gh run watch --repo kylodeng/underwriting_chatbot-main
```

### Environment Variable Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic Claude API key |
| `GOOGLE_API_KEY` | ✅ | — | Google Gemini API key |
| `REDIS_HOST` | ✅ | `localhost` | Redis hostname (use `redis` in Docker) |
| `SENDGRID_API_KEY` | ✅ (CI only) | — | SendGrid key for CI workflow email notifications |
| `GH_TOKEN` | ✅ (CI only) | — | GitHub PAT for CI workflow GitHub API calls |
| `NOTIFY_EMAIL` | ❌ | `kylo.deng@capco.com` | Recipient email for CI notifications |
| `OUTPUT_REPO` | ❌ | `ai-delivery-outputs` | GitHub repo where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | ❌ | GitHub repo owner | Owner of the output repo |

[TODO: Are there additional environment variables required for the frontend Chainlit service?]
[TODO: Is there a `.env.example` file or secrets management system (Vault, AWS Secrets Manager) to pull from?]