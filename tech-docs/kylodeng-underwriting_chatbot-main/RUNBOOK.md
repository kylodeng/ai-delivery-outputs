# Operational Runbook — `kylodeng/underwriting_chatbot`

---

## 1. Service Overview

The Underwriting Chatbot is an AI-assisted life insurance underwriting platform that enables underwriters to assess customer risk profiles via a conversational interface. The system comprises a **FastAPI backend** (port 8000) that orchestrates a multi-agent LangGraph pipeline, a **frontend chat UI** (port 8080), a **Redis** instance (port 6379) used for LangGraph conversation checkpointing, and a **PostgreSQL** database (port 5432) used by the frontend (Chainlit). The backend agents call Anthropic Claude models (`claude-sonnet-4-20250514` and `claude-haiku-4-5-20251001`) and optionally Google Gemini, performing parallel specialist assessments across domains (finance, health, life, etc.) before aggregating results into a structured `UnderwritingReport`. Customer profile data is served from read-only SQLite databases mounted into the backend container. A suite of five GitHub Actions CI workflows provide automated code review, documentation generation, test generation, and UAT facilitation via Claude.

---

## 2. Health Checks

| Check | How to verify | Expected result |
|---|---|---|
| Backend API liveness | `curl http://localhost:8000/health` | `{"status": "ok"}` |
| Backend container running | `docker compose ps backend` | `Up` / `healthy` |
| Frontend reachable | `curl -o /dev/null -s -w "%{http_code}" http://localhost:8080` | `200` |
| Redis reachable | `docker compose exec redis redis-cli ping` | `PONG` |
| PostgreSQL reachable | `docker compose exec postgres pg_isready -U chainlit` | `accepting connections` |
| Docker healthcheck | `docker inspect underwriting_chatbot-backend-1 --format='{{.State.Health.Status}}'` | `healthy` |
| Anthropic API key valid | Check backend startup logs for model init errors | No `AuthenticationError` |
| SQLite databases mounted | `docker compose exec backend ls /data/` | All four `.db` files present |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container crashed or not started | 1. `docker compose ps` to check status. 2. `docker compose logs backend --tail=50`. 3. `docker compose restart backend`. |
| Frontend shows blank page or "backend unreachable" | Backend unhealthy; frontend started before backend was ready | 1. Check backend health: `curl http://localhost:8000/health`. 2. `docker compose restart frontend`. 3. Confirm `BACKEND_URL` env var is set correctly. |
| `ChatAnthropic` / `ChatGoogleGenerativeAI` errors on startup or during chat | Missing or invalid `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` in `.env` | 1. Verify `.env` file exists at repo root. 2. Check key values: `docker compose exec backend env | grep API_KEY`. 3. Rotate keys and restart: `docker compose restart backend`. |
| Redis connection error / LangGraph checkpoint failure | Redis container not running or `REDIS_HOST` misconfigured | 1. `docker compose ps redis`. 2. `docker compose restart redis`. 3. Confirm `REDIS_HOST=redis` in backend environment. 4. See note in `graph.py` — Redis memory does not persist across serverless restarts by design. |
| Assessment returns empty or truncated `UnderwritingReport` | Aggregator LLM hit token limit (`aggregator_max_tokens: 8000`) or API timeout | 1. Check backend logs for `[AGGREGATOR]` output token count. 2. Increase `aggregator_max_tokens` in `backend/config.yml` if output tokens are at limit. 3. Retry request. |
| Slow assessment response (>30s) | Parallel specialist LLM calls hitting API rate limits or `specialist_max_tokens` being consumed | 1. Check logs for each `[SPECIALIST]` timing line. 2. Check Anthropic API rate limit dashboard. 3. Reduce `default` model to `anthropic-fast` in `config.yml` for faster responses. |
| `get_customer_profile` tool returns no data | SQLite database file missing or not mounted correctly | 1. `docker compose exec backend ls -lh /data/`. 2. Check `docker-compose.yml` volume mounts. 3. Ensure database files exist at `./database/*.db` on host. |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY` not found | GitHub secret not set on the repository | 1. Go to repo → Settings → Secrets and variables → Actions. 2. Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| GitHub Actions `tool1_code_review` fails with JSON parse error | Claude returned malformed JSON (intermittent) | 1. Re-run the workflow manually. 2. Check workflow logs for `[DEBUG] First 500 chars` output. 3. If persistent, check Anthropic API status. |
| PostgreSQL init fails / frontend DB errors | `postgres/init.sql` missing or schema mismatch | 1. `docker compose logs postgres`. 2. Confirm `./postgres/init.sql` exists. 3. Destroy and re-create volume: `docker compose down -v && docker compose up -d`. **Warning: destroys chat history.** |
| `ValueError: Unsupported or unconfigured model provider` | Invalid `model` value passed in chat request, or `azure`/`openai` selected (not yet implemented) | 1. Valid values are: `gemini`, `anthropic`, `anthropic-fast`. 2. Check frontend model selector. 3. Do not select `azure` or `openai` — [TODO: are these planned for implementation?]. |

---

## 4. Deployment Procedure

### Prerequisites

- Docker and Docker Compose installed
- `.env` file at repo root with required secrets (see §5)
- `./database/*.db` SQLite files present
- `./postgres/init.sql` present

### Step-by-step Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Copy and populate the environment file
cp .env.example .env          # [TODO: confirm .env.example exists or document required vars]
# Edit .env and set: ANTHROPIC_API_KEY, GOOGLE_API_KEY, and any other required values

# 3. Build all images
docker compose build

# 4. Start infrastructure services first
docker compose up -d redis postgres

# 5. Wait for postgres to be ready (typically 5-10s)
docker compose exec postgres pg_isready -U chainlit

# 6. Start backend (healthcheck will gate frontend startup)
docker compose up -d backend

# 7. Wait for backend to become healthy (up to 65s per healthcheck config)
watch docker compose ps backend

# 8. Start frontend
docker compose up -d frontend

# 9. Confirm all services healthy
docker compose ps

# 10. Smoke test
curl http://localhost:8000/health
curl -o /dev/null -s -w "%{http_code}" http://localhost:8080
```

### Updating to a New Version

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild changed images only
docker compose build backend frontend

# 3. Rolling restart (redis and postgres do not need restart for code changes)
docker compose up -d --no-deps backend
# Wait for healthy
watch docker compose ps backend
docker compose up -d --no-deps frontend
```

### Rollback Steps

```bash
# 1. Identify the previous working image tag or commit
git log --oneline -10

# 2. Check out the previous commit
git checkout <previous-commit-sha>

# 3. Rebuild and redeploy
docker compose build backend frontend
docker compose up -d --no-deps backend frontend

# 4. If database schema changed and rollback is needed:
# WARNING: This destroys all PostgreSQL data (chat history)
docker compose down -v
git checkout <previous-commit-sha>
docker compose up -d
```

> [TODO: Is there a container registry (ECR, ACR, Docker Hub) with tagged image versions that should be used for rollback instead of rebuilding from source?]

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Alert Threshold |
|---|---|---|
| Backend health endpoint | `GET /health` | Any non-200 response |
| Docker container restarts | `docker compose ps` / Docker daemon | > 2 restarts in 5 min |
| LLM specialist call duration | Backend stdout `[SPECIALIST] time=` | > 15s per specialist call |
| LLM aggregator call duration | Backend stdout `[AGGREGATOR] time=` | > 30s |
| LLM output token count (specialist) | Backend stdout `out=` token count | Approaching `specialist_max_tokens: 1500` |
| LLM output token count (aggregator) | Backend stdout `out=` token count | Approaching `aggregator_max_tokens: 8000` |
| Redis memory usage | `docker compose exec redis redis-cli info memory` | > 80% `maxmemory` |
| PostgreSQL connectivity | `pg_isready` | Not accepting connections |

### Log Locations

| Service | How to access |
|---|---|
| Backend application logs | `docker compose logs backend -f` |
| Frontend logs | `docker compose logs frontend -f` |
| Redis logs | `docker compose logs redis -f` |
| PostgreSQL logs | `docker compose logs postgres -f` |
| GitHub Actions workflows | `https://github.com/kylodeng/underwriting_chatbot-main/actions` |

### Key Log Patterns to Watch

```
# Successful assessment pipeline
[TOOL START] run_underwriting_assessment
[ASSESSMENT] Starting — N specialist calls (mode='fast')
[SPECIALIST] category=...  time=Xs
[AGGREGATOR] in=X tok  out=X tok  time=Xs
[TOOL END]   run_underwriting_assessment  time=Xs

# Error patterns to alert on
AuthenticationError          # Invalid API key
ConnectionRefusedError       # Redis/Postgres down
ValueError: Unsupported      # Bad model name
JSONDecodeError              # Claude response parsing failure
```

### Alerting

> [TODO: No alerting infrastructure is defined in this repo. Recommended: add Uptime Kuma, Prometheus + Grafana, or cloud-native monitoring (e.g. Azure Monitor, AWS CloudWatch) to monitor the `/health` endpoint and container health.]

> [TODO: Are Anthropic API spend alerts configured on the Anthropic console?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in contact] | Service down, health check failing |
| L2 | Backend Tech Lead | [TODO: fill in contact] | LLM pipeline failures, assessment errors, data issues |
| L3 | Solution Owner / Architect | [TODO: fill in contact] | Data breach, model card compliance issues, major outage |
| External | Anthropic Support | https://support.anthropic.com | API outage, rate limit issues not resolvable by retry |
| External | Google Cloud Support | [TODO: fill in if Gemini in production use] | Gemini API issues |

> Primary contact from codebase: `kylo.deng@capco.com` (identified in workflow env vars — [TODO: confirm this is the correct escalation contact]).

---

## 7. Useful Commands

```bash
# ── Service Management ──────────────────────────────────────────────────────

# Start all services
docker compose up -d

# Stop all services (preserves volumes)
docker compose down

# Stop all services AND destroy volumes (destructive — wipes postgres data)
docker compose down -v

# Restart a single service
docker compose restart backend
docker compose restart frontend
docker compose restart redis

# Rebuild and restart backend after code change
docker compose build backend && docker compose up -d --no-deps backend

# ── Health & Status ─────────────────────────────────────────────────────────

# Check all container statuses
docker compose ps

# Backend health endpoint
curl http://localhost:8000/health

# Frontend HTTP check
curl -o /dev/null -s -w "HTTP %{http_code}\n" http://localhost:8080

# Redis ping
docker compose exec redis redis-cli ping

# PostgreSQL ready check
docker compose exec postgres pg_isready -U chainlit

# ── Logs ────────────────────────────────────────────────────────────────────

# Follow all service logs
docker compose logs -f

# Follow backend logs only
docker compose logs backend -f

# Last 100 lines of backend logs
docker compose logs backend --tail=100

# ── Redis ────────────────────────────────────────────────────────────────────

# Check Redis memory usage
docker compose exec redis redis-cli info memory | grep used_memory_human

# List all Redis keys (LangGraph checkpoints)
docker compose exec redis redis-cli keys '*'

# Flush all Redis data (clears all conversation history/checkpoints)
docker compose exec redis redis-cli flushall

# ── PostgreSQL ───────────────────────────────────────────────────────────────

# Connect to PostgreSQL
docker compose exec postgres psql -U chainlit -d chainlit

# List tables
docker compose exec postgres psql -U chainlit -d chainlit -c '\dt'

# ── SQLite Databases ─────────────────────────────────────────────────────────

# Verify database files are mounted and accessible
docker compose exec backend ls -lh /data/

# Quick row count check on customer profile DB
docker compose exec backend sqlite3 /data/customer_profile.db "SELECT COUNT(*) FROM customer_profile;" 2>/dev/null || echo "[TODO: confirm table name]"

# ── Environment Variables ────────────────────────────────────────────────────

# Check backend sees required env vars (never log values in production)
docker compose exec backend env | grep -E 'ANTHROPIC|GOOGLE|REDIS|MODEL' | sed 's/=.*/=***/'

# ── GitHub Actions (requires gh CLI) ────────────────────────────────────────

# List recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main

# Manually trigger tech docs workflow
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger code review
gh workflow run tool1_code_review.yml --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=repo

# View a specific run's logs
gh run view <run-id> --log --repo kylodeng/underwriting_chatbot-main

# ── Config Tuning ────────────────────────────────────────────────────────────

# Edit LLM config (token limits, model selection, temperature)
# File: backend/config.yml
# After editing, rebuild backend:
docker compose build backend && docker compose up -d --no-deps backend
```

---

> **Document status:** Draft — auto-supplemented from source code analysis.
> Fields marked `[TODO]` require human input before this runbook is production-ready.
> Last reviewed: [TODO: insert date]