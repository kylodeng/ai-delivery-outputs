# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides AI-powered insurance sales training for new agents in a Hong Kong market context. It operates in two modes: **Teacher mode**, where the LLM acts as an interactive coach delivering product knowledge, sales technique guidance, and exercises via streaming chat; and **Roleplay mode**, where the LLM simulates realistic Hong Kong customer personas so agents can practise discovery conversations and objection handling. The backend uses a LangGraph agent backed by a RAG (Retrieval-Augmented Generation) pipeline built on a local vector store (FAISS or Chroma) pre-ingested with Sun Life insurance product PDFs. The API is deployed to **Azure App Service** (`training-bot-api`), with a separate frontend App Service (`training-bot-frontend`), both deployed via GitHub Actions CI/CD on every push to `main`.

---

## 2. Health Checks

Perform the following checks in order to confirm the service is running correctly.

### 2.1 API Layer

```
# Check the API process is accepting connections
curl -f http://<api-hostname>/docs
# Expected: HTTP 200, FastAPI Swagger UI HTML
```

```
# Confirm lifespan startup completed (vector store loaded)
# Look for this in application logs on startup:
# INFO: Vector store loaded (N products)
# WARNING: No vector store found — run POST /ingest first.
```

### 2.2 Vector Store

```
# Trigger a re-ingest if the vector store is missing
curl -X POST http://<api-hostname>/ingest
# Expected: HTTP 200, confirmation JSON
```

[TODO: What is the exact path and response schema of the `/ingest` endpoint?]

### 2.3 LLM Connectivity

```
# Send a minimal teacher-mode message and confirm a streaming response
curl -X POST http://<api-hostname>/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "healthcheck", "message": "ping"}'
# Expected: HTTP 200, streaming SSE or JSON response
```

[TODO: What are the exact route paths for `/chat`, `/roleplay`, `/ingest`, and `/sessions`? Not fully visible in the provided code.]

### 2.4 Session Persistence

```
# Confirm sessions file is readable/writable
ls -lh data/sessions.json
# Expected: file exists and is non-empty after at least one session has been created
```

### 2.5 Azure App Service (Portal / CLI)

```bash
# Check App Service status via Azure CLI
az webapp show --name training-bot-api --resource-group <rg-name> --query state
# Expected: "Running"

az webapp show --name training-bot-frontend --resource-group <rg-name> --query state
# Expected: "Running"
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `WARNING: No vector store found — run POST /ingest first` on startup | Vector store index files missing from `data/` (not committed, deleted, or first deploy) | Run `POST /ingest` via curl or the admin UI; ensure PDFs are present under `data/Insurance-product-info/` |
| LLM returns empty or truncated responses | `API_KEY` env var missing, expired, or rate-limited by OpenRouter/Anthropic | Verify `API_KEY` is set in Azure App Service configuration; check upstream API quota/status page |
| `SSL: CERTIFICATE_VERIFY_FAILED` or similar TLS errors | `httpx` clients are configured with `verify=False`; unlikely to block but upstream proxy may require cert pinning | [TODO: Confirm whether the Azure-hosted environment requires SSL verification; consider removing `verify=False` for production] |
| Chat responses are correct but source citations are missing (`[[S1]]` etc.) | RAG tools not being called; sources context var not initialised per request | Check that `reset_sources()` is called at the start of each teacher-mode request; inspect agent tool-call logs |
| Session state lost after restart | `data/sessions.json` was not persisted across deploy (Azure App Service ephemeral storage) | Mount an Azure File Share to the `/data` path, or switch to a durable store (e.g. Azure Blob / CosmosDB); [TODO: confirm current persistence strategy] |
| `KeyError: 'API_KEY'` or similar on startup | Required environment variable not set in Azure App Service Application Settings | Add the missing variable in Azure Portal → App Service → Configuration → Application Settings |
| GitHub Actions deploy fails at `azure/webapps-deploy` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret expired or missing | Regenerate publish profile from Azure Portal and update the GitHub secret |
| `pytest` fails in CI | Test suite broken by a code change | Review the failing test output in the GitHub Actions run; fix or skip the failing test before merging |
| PDF ingestion fails / chunks = 0 | PDF files absent from `data/Insurance-product-info/`, or LLM annotation call failing | Confirm PDFs are committed to the repo or mounted; check `OPENAI_MODEL` and `OPENAI_URL_BASE` env vars; run ingest with `--verbose` |
| Frontend 502 / backend unreachable from frontend | CORS origin mismatch or backend App Service down | Confirm backend URL matches CORS `allow_origins` in `main.py`; check backend App Service health |
| Roleplay persona not generated | `generate_profile` failing, likely LLM or env issue | Check application logs for the session creation call; verify LLM connectivity (see §2.3) |
| Rate limit errors from embedding API | `embed_chunks` batch size too large for free-tier Voyage AI / OpenRouter | Reduce `batch_size` in `embed_chunks` call; increase `batch_delay`; switch to a paid embedding tier |

---

## 4. Deployment Procedure

### Pre-requisites

- Azure CLI installed and authenticated (`az login`)
- GitHub repository secrets set:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `ANTHROPIC_API_KEY` (used by AI tooling workflows)
  - `GH_TOKEN`
  - `SENDGRID_API_KEY`
- Azure App Services `training-bot-api` and `training-bot-frontend` already provisioned
- Python 3.13 and `uv` available locally for manual steps

### Standard Deployment (Automated — preferred)

1. **Merge a PR to `main`** or push directly to `main`.
2. The `Test & Deploy` workflow (`.github/workflows/deploy.yml`) triggers automatically.
3. **`test` job** runs: installs dependencies via `uv sync`, executes `pytest tests/ -v`.
   - If tests fail, deployment jobs are skipped. Fix the failing tests and re-push.
4. **`deploy-api` job** runs (parallel with `deploy-frontend` after tests pass):
   - Generates `requirements.txt` via `uv export`.
   - Deploys to `training-bot-api` Azure App Service using the publish profile.
5. **`deploy-frontend` job** runs:
   - Deploys to `training-bot-frontend` Azure App Service.
6. After both deploy jobs complete, perform health checks (§2).

### Manual Deployment (Break-glass)

```bash
# 1. Clone the repo and install dependencies
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
pip install uv
uv sync

# 2. Export requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# 3. Deploy API manually via Azure CLI
az webapp deploy \
  --name training-bot-api \
  --resource-group <rg-name> \
  --src-path . \
  --type zip

# 4. Deploy frontend manually
az webapp deploy \
  --name training-bot-frontend \
  --resource-group <rg-name> \
  --src-path . \
  --type zip

# 5. Run post-deploy health checks (see §2)
```

### Post-Deployment Verification

```bash
# 1. Confirm API is up
curl -f https://<api-hostname>/docs

# 2. Confirm vector store loaded (check startup logs)
az webapp log tail --name training-bot-api --resource-group <rg-name>
# Look for: "Vector store loaded (N products)"

# 3. Send a test chat message
curl -X POST https://<api-hostname>/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "smoke-test", "message": "What products do you have?"}'
```

### Rollback Steps

```bash
# Option A — Redeploy the previous Git commit via GitHub Actions
# 1. Identify the last good commit SHA
git log --oneline -5

# 2. Create a revert commit and push to main (triggers CI/CD)
git revert <bad-commit-sha>
git push origin main

# Option B — Azure deployment slot swap (if slots are configured)
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group <rg-name> \
  --slot staging \
  --target-slot production

# Option C — Redeploy a specific tag/commit manually
git checkout <last-good-tag-or-sha>
uv export --no-dev --format requirements-txt -o requirements.txt
az webapp deploy \
  --name training-bot-api \
  --resource-group <rg-name> \
  --src-path . \
  --type zip
```

[TODO: Are Azure deployment slots configured for staging/production swap? If not, this is a recommended DR improvement.]

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to Find | Alert Threshold |
|---|---|---|
| App Service HTTP 5xx error rate | Azure Monitor → App Service → Metrics → Http5xx | > 5 errors / 5 min |
| App Service response time (P95) | Azure Monitor → App Service → Metrics → AverageResponseTime | > 10 seconds |
| App Service instance count / CPU | Azure Monitor → App Service Plan → Metrics | CPU > 80% sustained |
| Memory usage | Azure Monitor → App Service Plan → Metrics → MemoryPercentage | > 85% |
| GitHub Actions workflow status | GitHub → Actions tab | Any failed run on `main` |
| LLM API error rate | Azure App Service logs (application-level) | Any repeated `4xx`/`5xx` from LLM provider |
| Vector store load on startup | Application logs at startup | `WARNING: No vector store found` |

### Logs to Watch

```bash
# Stream live application logs from Azure
az webapp log tail \
  --name training-bot-api \
  --resource-group <rg-name>

# Download logs for offline analysis
az webapp log download \
  --name training-bot-api \
  --resource-group <rg-name> \
  --log-file api-logs.zip
```

**Critical log patterns to alert on:**

```
# Vector store missing
WARNING: No vector store found — run POST /ingest first

# LLM connectivity failure
ERROR: ... (any exception during call_claude or ChatOpenAI invocation)

# Session persistence failure
ERROR: ... sessions.json ... (PermissionError, JSONDecodeError)

# Embedding / ingestion failure
[ingest] annotation failed for <file>
```

### Application-Level Logging

The application uses Python `logging` with `basicConfig(level=logging.INFO)`. Structured log lines are prefixed with `[ingest]`, `[rag]`, etc. All logs flow to Azure App Service's built-in log stream.

[TODO: Is Application Insights or any external APM (Datadog, Sentry) configured? If not, this is a gap — recommend adding Azure Application Insights for distributed tracing and error alerting.]

[TODO: Are there any alerting rules configured in Azure Monitor? If not, set up action groups to email/page on the thresholds above.]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: name/email/phone] | Service down, health checks failing, deployment failure |
| L2 | Backend Tech Lead | [TODO: name/email] | LLM integration issues, RAG pipeline failures, data corruption |
| L3 | Cloud/Azure Owner | [TODO: name/email] | Azure App Service outage, subscription/quota issues, secrets rotation |
| L4 | LLM Provider Support | OpenRouter: [TODO: support URL] / Anthropic: support@anthropic.com | LLM API outage, rate limit increase requests |
| — | Repo Owner | kylo.deng@capco.com | Business escalation, access, CI/CD secrets |

[TODO: Fill in L1–L3 team contacts, PagerDuty rotation, and Slack/Teams channel names.]

---

## 7. Useful Commands

### Application Management

```bash
# --- Azure App Service ---

# Check App Service status
az webapp show --name training-bot-api --resource-group <rg-name> --query "{state:state,hostName:defaultHostName}"

# Restart the API service
az webapp restart --name training-bot-api --resource-group <rg-name>

# Restart the frontend service
az webapp restart --name training-bot-frontend --resource-group <rg-name>

# Stream live logs
az webapp log tail --name training-bot-api --resource-group <rg-name>

# List current App Service environment variables
az webapp config appsettings list --name training-bot-api --resource-group <rg-name>

# Set / update an environment variable
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg-name> \
  --settings API_KEY="<new-value>"
```

### Local Development

```bash
# Install all dependencies (including dev)
uv sync

# Run the FastAPI server locally
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### Vector Store / Ingestion

```bash
# Re-ingest all PDFs from the data directory (via CLI entrypoint)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Re-ingest with custom chunk size
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --max-words 280 --verbose

# Trigger ingest via API
curl -X POST http://localhost:8000/ingest

# Check how many products are in the vector store (via API startup log or)
# [TODO: Is there a /products or /status endpoint that lists known products?]
```

### Dependency Management

```bash
# Add a new dependency
uv add <package-name>

# Export requirements for Azure deployment
uv export --no-dev --format requirements-txt -o requirements.txt

# Check for outdated packages
uv pip list --outdated
```

### GitHub Actions (manual triggers)

```bash
# Trigger the deploy workflow manually via GitHub CLI
gh workflow run "Test & Deploy" --ref main

# Trigger a code review on a specific PR
gh workflow run "Tool 1 — Code Review" \
  --field review_mode=pr \
  --field pr_number=42

# Trigger tech doc regeneration
gh workflow run "Tool 2 — Tech Documentation"

# View recent workflow runs
gh run list --workflow="Test & Deploy" --limit 5

# Watch a running workflow
gh run watch
```

### Session Management

```bash
# Inspect sessions file
cat data/sessions.json | python -m json.tool | head -100

# Back up sessions before a risky operation
cp data/sessions.json data/sessions.json.bak

# Clear all sessions (destructive — confirm first)
echo '{}' > data/sessions.json
```

---

*This runbook was auto-generated from repository source code. Items marked `[TODO: ...]` require human verification or completion.*