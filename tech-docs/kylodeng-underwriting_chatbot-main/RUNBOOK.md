# Operational Runbook — `kylodeng/underwriting_chatbot-main`

---

## 1. Service Overview

The Underwriting Chatbot is a life insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational AI interface. The system is composed of a FastAPI backend that orchestrates a LangGraph-based agent, a frontend chat UI (Chainlit), a Redis instance for conversation checkpointing, and a PostgreSQL database for Chainlit session state. When an underwriter submits a query, the agent routes tool calls — fetching customer profiles from SQLite databases, finding lookalike customers, and running a parallel multi-specialist LLM underwriting assessment (covering finance, health, life, and other risk domains). Assessments are performed by a fleet of Claude (Anthropic) and optionally Gemini LLM calls, aggregated into a structured `UnderwritingReport`. The CI/CD pipeline includes five GitHub Actions–driven AI tools: automated code review, technical documentation generation, business documentation, test generation, and UAT facilitation — all powered by Claude via the Anthropic API.

---

## 2. Health Checks

### 2.1 Backend API

```bash
curl -f http://localhost:8000/health
# Expected: {"status": "ok"}  HTTP 200
```

### 2.2 Docker Compose Service Status

```bash
docker compose ps
# All services should show: Status = "running (healthy)"
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
curl -f http://localhost:8080
# Expected: HTTP 200 with HTML body
```

### 2.6 LLM API Reachability

```bash
# Anthropic
curl -s https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.data[0].id'

# Google (if Gemini enabled)
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY" \
  | jq '.models[0].name'
```

### 2.7 SQLite Database Files Present

```bash
ls -lh ./database/*.db
# Expected: customer_profile.db, feature_importance.db,
#           model_predictions.db, application_profile.db all present and non-zero
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `GET /health` returns non-200 or connection refused | Backend container crashed or failed to start | 1. `docker compose logs backend --tail=50` 2. Check for missing `.env` variables 3. `docker compose restart backend` |
| Frontend shows blank page or cannot reach backend | `BACKEND_URL` misconfigured, or backend not yet healthy | 1. Confirm backend health check passes 2. Check `BACKEND_URL=http://backend:8000` in frontend env 3. Verify `depends_on: backend: condition: service_healthy` is respected |
| Chat responses hang indefinitely / no SSE events | Redis connection lost mid-session (LangGraph checkpointer failure) | 1. `docker compose logs backend \| grep -i redis` 2. `docker compose restart redis` 3. Restart backend after Redis recovers 4. See TODO: Redis persistence note in `graph.py` |
| `ValueError: Unsupported or unconfigured model provider` | `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` missing or model name typo | 1. Verify `.env` has all required keys 2. Check `config.yml` `llm.default` matches a key in `LLMS.model_mapper` 3. `docker compose down && docker compose up` to reload env |
| `401 Unauthorized` from Anthropic API | Invalid or expired `ANTHROPIC_API_KEY` | 1. Rotate key in Anthropic console 2. Update `.env` and GitHub Actions secret `ANTHROPIC_API_KEY` 3. Restart backend |
| `422 Unprocessable Entity` on `/chat` | Malformed request body (missing `message`, `temperature`, etc.) | 1. Inspect frontend request payload 2. Verify `ChatRequest` Pydantic schema matches frontend contract 3. Check frontend build for stale JS bundle |
| Assessment returns incomplete/garbled JSON | Aggregator LLM exceeded `aggregator_max_tokens` (8000) or structured output failed | 1. Check backend logs for `[AGGREGATOR]` line — inspect `out=` token count 2. Increase `aggregator_max_tokens` in `config.yml` if consistently near limit 3. Reduce number of specialist categories in `config.yml` if needed |
| `customer_profile.db` not found / no profile returned | Database file not mounted correctly | 1. `docker compose exec backend ls /data/` 2. Verify volume mounts in `docker-compose.yml` point to correct host paths 3. Ensure `.db` files are present on the host |
| Redis data lost on restart (conversation history reset) | Redis running without persistence (in-memory only) | 1. This is a known TODO in `graph.py` 2. Short-term: accept stateless sessions 3. Long-term: migrate to Azure Cache for Redis or add `redis.conf` with AOF/RDB persistence |
| GitHub Actions workflow fails at Claude API call | `ANTHROPIC_API_KEY` secret not set in repository | 1. Go to Repo → Settings → Secrets → Actions 2. Add/update `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` 3. Re-run failed workflow |
| Tool 1 (Code Review) fails to post PR comment | `GH_TOKEN` lacks `pull-requests: write` permission | 1. Confirm token scopes in GitHub settings 2. Re-generate PAT with `repo` and `pull_requests` scopes 3. Update `GH_TOKEN` secret |
| Specialist LLM calls timing out under load | Semaphore limit (4 concurrent) too low, or LLM API rate limit hit | 1. Check `[SPECIALIST]` log lines for time values 2. If rate-limited, reduce `asyncio.Semaphore(4)` to `2` in `assessment.py` 3. [TODO: What are the Anthropic API rate limits for this account?] |
| PostgreSQL init fails on first start | `./postgres/init.sql` missing or malformed | 1. `docker compose logs postgres` 2. Verify `init.sql` exists at `./postgres/init.sql` 3. `docker compose down -v && docker compose up` to reinitialise volume |

---

## 4. Deployment Procedure

### Prerequisites

- Docker & Docker Compose v2 installed
- `.env` file populated (see §5 for required variables)
- SQLite database files present in `./database/`
- Sufficient host memory for concurrent LLM calls (recommend ≥ 4 GB RAM)

---

### 4.1 First-Time Deployment

```bash
# 1. Clone repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create .env from template
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env — fill in all required values (see §5)

# 3. Verify database files are present
ls ./database/*.db

# 4. Build and start all services
docker compose up --build -d

# 5. Wait for backend health check to pass (up to 90s)
docker compose ps

# 6. Confirm health
curl http://localhost:8000/health
# {"status": "ok"}

# 7. Open frontend
open http://localhost:8080
```

---

### 4.2 Routine Update Deployment

```bash
# 1. Pull latest changes
git pull origin main

# 2. Rebuild affected images only
docker compose build backend frontend

# 3. Rolling restart (Redis and Postgres preserve state)
docker compose up -d --no-deps backend frontend

# 4. Verify health
curl http://localhost:8000/health
docker compose ps
```

---

### 4.3 Configuration-Only Change (e.g., `config.yml` or `prompts/`)

```bash
# config.yml and prompt files are baked into the image at build time
docker compose build backend
docker compose up -d --no-deps backend
curl http://localhost:8000/health
```

---

### 4.4 Rollback Procedure

```bash
# Option A: Roll back to previous Git commit
git log --oneline -10          # identify target commit SHA
git checkout <previous-sha>

docker compose build backend frontend
docker compose up -d --no-deps backend frontend
curl http://localhost:8000/health

# Option B: Roll back to a tagged release
git checkout v<previous-version>
docker compose build backend frontend
docker compose up -d --no-deps backend frontend

# Option C: If Docker images are tagged and pushed to a registry
# [TODO: Is there a container registry (ACR, ECR, GCR) in use?]
docker compose pull
docker compose up -d
```

---

### 4.5 Full Teardown

```bash
# Stops containers, removes containers and networks — PRESERVES volumes
docker compose down

# DESTRUCTIVE: also removes postgres_data volume (loses all Chainlit session history)
docker compose down -v
```

---

## 5. Monitoring & Alerting

### 5.1 Key Log Patterns to Watch

```bash
# Stream all backend logs
docker compose logs -f backend

# Key log markers emitted by the application:
# [CHAT]       - incoming request details (session, model, mode, message preview)
# [TOOL START] - agent tool invocation begins
# [TOOL END]   - tool completed with elapsed time
# [SPECIALIST] - per-category LLM call: tokens in/out, elapsed time
# [AGGREGATOR] - final aggregation LLM call: tokens in/out, elapsed time
# [ASSESSMENT] - overall assessment start with category count
```

### 5.2 Metrics to Monitor

| Metric | Source | Alert Threshold |
|---|---|---|
| Backend `/health` response | Docker healthcheck (10s interval) | Any non-200 → page on-call |
| Specialist LLM call duration (`time=` in logs) | Backend stdout | > 30s per category |
| Aggregator token output (`out=` in logs) | Backend stdout | Approaching 8000 tokens |
| Redis connection errors | Backend logs (`redis`, `connection refused`) | Any occurrence |
| Anthropic API errors (4xx/5xx) | Backend logs | Any 5xx or repeated 429 |
| Docker container restarts | `docker compose ps` / Docker daemon | > 2 restarts in 10 min |
| Disk usage (SQLite DBs + postgres volume) | Host `df -h` | > 80% disk utilisation |
| [TODO: Are there any APM tools (Datadog, Grafana, CloudWatch) configured?] | — | — |

### 5.3 GitHub Actions Workflow Monitoring

- Navigate to **Repo → Actions** to view run status for all 5 tools
- Tool 1 (Code Review) posts results directly to PRs — check PR comments for failures
- Artifacts from Tool 1 (`code-review-<run_id>`) are uploaded on every run, including failures
- [TODO: Is there a Slack/Teams webhook configured to receive GitHub Actions failure notifications?]

### 5.4 LLM Cost Monitoring

- Monitor Anthropic API dashboard for token consumption: `specialist_max_tokens=1500`, `aggregator_max_tokens=8000` per assessment
- Monitor Google Cloud Console if Gemini (`GOOGLE_API_KEY`) is in active use
- [TODO: Are there spend alerts configured on the Anthropic or Google API accounts?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-Call Engineer | [TODO: fill in on-call rotation/PagerDuty link] | Service down, health check failing |
| L2 | Backend Tech Lead | [TODO: fill in name and contact] | Redis/DB data loss, LLM API outage, auth failures |
| L3 | Platform / DevOps Lead | [TODO: fill in name and contact] | Infrastructure failure, secrets rotation, DR invocation |
| L4 | Anthropic Support | https://support.anthropic.com | Anthropic API persistent 5xx, model unavailability |
| L4 | Google Cloud Support | [TODO: GCP support tier link] | Gemini API outage |
| Business | Product Owner | [TODO: fill in name and contact] | Data breach, incorrect underwriting outputs, compliance concern |
| Compliance | [TODO: Risk/Compliance contact] | [TODO: fill in] | PII exposure, model fairness concerns, regulatory query |

> ⚠️ **Note:** This system processes insurance underwriting data which may include personal and financial information. Any suspected data breach must be escalated immediately to the compliance contact regardless of severity level.

---

## 7. Useful Commands

### Service Management

```bash
# Start all services (detached)
docker compose up -d

# Start with rebuild
docker compose up --build -d

# Stop all services (preserve data)
docker compose down

# Restart a single service
docker compose restart backend

# View live logs for all services
docker compose logs -f

# View live logs for backend only
docker compose logs -f backend

# Check service health and port status
docker compose ps
```

### Debugging

```bash
# Shell into backend container
docker compose exec backend bash

# Shell into Redis container
docker compose exec redis bash

# Check Redis keys (conversation checkpoints)
docker compose exec redis redis-cli KEYS "*"

# Count Redis keys
docker compose exec redis redis-cli DBSIZE

# Flush all Redis data (WARNING: clears all conversation history)
docker compose exec redis redis-cli FLUSHALL

# Check PostgreSQL tables
docker compose exec postgres psql -U chainlit -d chainlit -c "\dt"

# Inspect backend environment variables
docker compose exec backend env | sort
```

### Health & Connectivity

```bash
# Backend health check
curl -f http://localhost:8000/health && echo "OK" || echo "FAILED"

# Test chat endpoint (basic smoke test)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","temperature":0.3,"session_id":"test","model":"anthropic-fast","mode":"fast"}'

# Redis ping
docker compose exec redis redis-cli ping

# Postgres ready check
docker compose exec postgres pg_isready -U chainlit
```

### Environment & Configuration

```bash
# View resolved config
docker compose exec backend cat config.yml

# Check .env is loaded (look for ANTHROPIC_API_KEY)
docker compose exec backend env | grep -E "ANTHROPIC|GOOGLE|REDIS"

# View model card
docker compose exec backend cat model_card.json | python3 -m json.tool
```

### GitHub Actions (run from your local machine)

```bash
# Manually trigger Tech Docs generation (requires gh CLI)
gh workflow run tool2_tech_docs.yml --repo kylodeng/underwriting_chatbot-main

# Manually trigger Code Review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/underwriting_chatbot-main \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# View recent workflow runs
gh run list --repo kylodeng/underwriting_chatbot-main --limit 10

# Watch a specific run
gh run watch <RUN_ID> --repo kylodeng/underwriting_chatbot-main
```

### Log Analysis

```bash
# Find all slow specialist calls (> 10s)
docker compose logs backend 2>&1 | grep '\[SPECIALIST\]' | awk -F'time=' '{if ($2+0 > 10) print $0}'

# Find all errors in backend logs
docker compose logs backend 2>&1 | grep -iE 'error|exception|traceback|failed'

# Find all tool calls in current session
docker compose logs backend 2>&1 | grep -E '\[TOOL (START|END)\