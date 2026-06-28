# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Document owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training platform for insurance sales agents in a Hong Kong context. It exposes two modes of interaction: a **Teacher mode**, where a LangGraph agent coaches agents on insurance products and sales techniques using a Retrieval-Augmented Generation (RAG) pipeline backed by a vector store of insurance product PDFs, and a **Roleplay/Assessment mode**, where a simulated customer persona interacts with the trainee before an assessor agent scores the session. The backend is served by FastAPI (`api/main.py`), connects to an LLM via OpenRouter (defaulting to `openai/gpt-oss-20b:free` but configurable), uses either Chroma, FAISS, or Pinecone as its vector store, and is deployed to **Azure App Service** (app name: `training-bot-api`) via GitHub Actions. A separate frontend service is deployed to a second Azure App Service (`training-bot-frontend`). PDF insurance product documents are ingested and chunked once into the vector store; they are served statically at `/docs/` by FastAPI for in-app linking.

---

## 2. Health Checks

Perform these checks in order to confirm the service is running correctly.

### 2.1 API Service

```bash
# Basic liveness — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<api-hostname>/

# List active sessions (confirms DB/session store is responsive)
curl -s https://<api-hostname>/sessions | jq .

# Confirm vector store loaded (check startup log for this line)
# Expected: "Vector store loaded (N products)"
az webapp log tail --name training-bot-api --resource-group <rg>
```

> [TODO: What is the production API hostname/URL?]
> [TODO: Is there a dedicated `/health` or `/healthz` endpoint? None is visible in the code — recommend adding one.]

### 2.2 Frontend Service

```bash
# Expect HTTP 200 from the frontend App Service
curl -s -o /dev/null -w "%{http_code}" https://<frontend-hostname>/
```

> [TODO: What is the production frontend hostname/URL?]

### 2.3 Vector Store

```bash
# After startup, confirm products are indexed
curl -s https://<api-hostname>/ingest  # POST to re-trigger if needed

# Locally:
python -c "
from core.vector_store import get_vector_store
s = get_vector_store()
s.load()
print(s.get_known_products())
"
```

### 2.4 LLM Connectivity

```bash
# Confirm the LLM endpoint is reachable (OpenRouter or override)
curl -s -H "Authorization: Bearer $API_KEY" \
  "$OPENAI_URL_BASE/models" | jq .
```

### 2.5 Azure App Service Status

```bash
az webapp show --name training-bot-api --resource-group <rg> \
  --query "state" -o tsv
# Expected: Running

az webapp show --name training-bot-frontend --resource-group <rg> \
  --query "state" -o tsv
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `500 Internal Server Error` on any endpoint at startup | Vector store not loaded — `POST /ingest` was never run or index file is missing | SSH into App Service or run locally: `curl -X POST https://<api-hostname>/ingest`; verify `data/` directory contains PDFs; check startup logs for `No vector store found` warning |
| Agent returns generic/hallucinated product answers with no citations | Vector store empty or search returning zero results | Re-run ingestion: `POST /ingest`; confirm PDF files exist under `data/Insurance-product-info/`; check embedding model config (`VOYAGE_API_KEY` or equivalent) |
| `401 Unauthorized` from LLM calls | `API_KEY` environment variable missing, expired, or wrong | Update `API_KEY` secret in Azure App Service configuration; verify against OpenRouter or Anthropic dashboard |
| `422 Unprocessable Entity` on `/chat` or session endpoints | Pydantic model validation failure — malformed request body | Check client request payload against `api/sessions.py` `CustomerProfile` schema; check logs for field-level validation errors |
| Sessions lost after app restart | `sessions.json` not persisted to durable storage | Mount a persistent Azure File Share to `data/` directory; [TODO: confirm if persistent storage is configured on App Service] |
| SSL verification warnings in logs (`verify=False`) | `httpx` clients are configured with `verify=False` — self-signed cert or proxy in path | This is intentional in the codebase for the LLM HTTP client; if logs are noisy, confirm no MITM proxy is stripping certs in production |
| GitHub Actions deploy fails on `azure/webapps-deploy` step | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret missing or expired | Regenerate publish profile from Azure Portal → App Service → Get Publish Profile; update GitHub secret |
| `ModuleNotFoundError` after deploy | `requirements.txt` generation failed or `uv export` missed a dependency | Re-run `uv export --no-dev --format requirements-txt -o requirements.txt` locally; commit the output if the App Service doesn't run `uv` directly |
| Chainlit UI blank or fails to connect | CORS origin mismatch | Add the production frontend URL to the `allow_origins` list in `api/main.py`; redeploy |
| PDF files return 404 via `/docs/` links | `data/` directory not deployed with the app or path is wrong | Confirm `data/Insurance-product-info/` is included in the deployment artifact; check `StaticFiles` mount path in `api/main.py` |
| Annotation `.annot.json` sidecar files missing | LLM annotation step was skipped during ingestion | Re-run ingestion with `--llm` flag; or manually copy pre-generated `.annot.json` files to `data/` |
| AI Delivery workflow (Tool 1–5) fails | Missing `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` in GitHub secrets | Check Actions run logs; add/rotate the relevant secret in repo Settings → Secrets and variables → Actions |
| Code review or doc generation returns malformed JSON | Claude model response wrapped in markdown fences | Already handled by `extract_json()` / `clean_json()`; if persisting, check `MODEL` value in `shared.py` — `claude-sonnet-4-6` must be accessible |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated: `az login`
- GitHub repo secrets set: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
- Application secrets set in Azure App Service Configuration: `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`
- `uv` installed locally: `pip install uv`

---

### 4.1 Normal Deployment (via CI/CD — Recommended)

```
1. Push commits to a feature branch.
2. Open a Pull Request against `main`.
3. GitHub Actions workflow `Test & Deploy` triggers:
   a. `test` job runs: `uv run pytest tests/ -v`
   b. On merge to `main`, `deploy-api` job deploys to `training-bot-api`.
   c. On merge to `main`, `deploy-frontend` job deploys to `training-bot-frontend`.
4. Monitor the Actions run at:
   https://github.com/kylodeng/Insurance-Training-Bot-main/actions
5. After deploy completes (~3–5 min), run health checks (Section 2).
```

### 4.2 Manual / Emergency Deployment

```bash
# Step 1: Install dependencies and generate requirements.txt
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt

# Step 2: Deploy API manually via Azure CLI
az webapp deployment source config-zip \
  --name training-bot-api \
  --resource-group <rg> \
  --src <path-to-zip>

# Step 3: Deploy frontend manually
az webapp deployment source config-zip \
  --name training-bot-frontend \
  --resource-group <rg> \
  --src <path-to-zip>

# Step 4: Restart the app service to pick up new env vars if changed
az webapp restart --name training-bot-api --resource-group <rg>

# Step 5: Tail logs to confirm clean startup
az webapp log tail --name training-bot-api --resource-group <rg>
# Look for: "Vector store loaded (N products)"
# Look for: "Application startup complete."
```

### 4.3 Vector Store Ingestion (Run After First Deploy or When PDFs Change)

```bash
# Trigger ingestion via the API endpoint
curl -X POST https://<api-hostname>/ingest

# Or run locally:
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### 4.4 Rollback Steps

```bash
# Option A: Revert via GitHub — create a revert PR and merge
git revert HEAD
git push origin main
# CI/CD will redeploy the previous state automatically.

# Option B: Roll back Azure App Service to a previous deployment slot
# [TODO: confirm if deployment slots are configured]
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group <rg> \
  --slot staging \
  --target-slot production

# Option C: Roll back to a specific GitHub Actions run artifact
# Download the artifact from the Actions run, re-deploy using Step 4.2 above.

# Option D: Roll back App Service to previous package directly
az webapp deployment list \
  --name training-bot-api \
  --resource-group <rg>
# Identify the previous deployment ID, then:
az webapp deployment set-active \
  --name training-bot-api \
  --resource-group <rg> \
  --deployment-id <previous-id>
```

> [TODO: Are deployment slots (staging/production) configured on either App Service?]
> [TODO: Is there a container registry or only zip deploy in use?]

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where | Threshold / Alert |
|---|---|---|
| App Service HTTP 5xx error rate | Azure Monitor → App Service → `Http5xx` | Alert if > 5 errors/min |
| App Service response time (P95) | Azure Monitor → App Service → `HttpResponseTime` | Alert if P95 > 10s |
| App Service instance availability | Azure Monitor → App Service → `Availability` | Alert if < 100% |
| App Service CPU % | Azure Monitor → App Service → `CpuPercentage` | Alert if > 80% sustained > 5 min |
| App Service memory % | Azure Monitor → App Service → `MemoryPercentage` | Alert if > 85% |
| GitHub Actions workflow failure | GitHub → Actions tab | Subscribe to workflow failure notifications |
| LLM API latency / errors | Application logs (see below) | Alert on repeated `401`, `429`, or timeout errors |
| Vector store load failure | App startup logs | Alert on `No vector store found` warning |

> [TODO: Is Azure Application Insights configured? If so, provide the workspace/resource name.]
> [TODO: Are Azure Monitor alerts already set up, or do they need to be created?]

### 5.2 Logs to Watch

```bash
# Live application logs (Azure)
az webapp log tail --name training-bot-api --resource-group <rg>

# Download historical logs
az webapp log download --name training-bot-api --resource-group <rg>

# Key log lines to monitor:
# INFO  — "Vector store loaded (N products)"       ← good startup
# WARNING — "No vector store found"                ← ingestion needed
# ERROR — any traceback in /api or /core modules
# INFO  — LangGraph tool call results (search_product, etc.)
```

### 5.3 AI Delivery Workflow Logs

```bash
# All 5 AI tools log to GitHub Actions:
# https://github.com/kylodeng/Insurance-Training-Bot-main/actions

# Audit log entries written to output repo:
# https://github.com/<OUTPUT_REPO_OWNER>/ai-delivery-outputs
```

### 5.4 Log Levels

The application uses Python `logging` with `basicConfig(level=logging.INFO)`. To increase verbosity:

```bash
# Set via Azure App Service config
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg> \
  --settings LOG_LEVEL=DEBUG
```

> [TODO: Is there a log aggregation solution (e.g., Azure Log Analytics, Datadog, Splunk) ingesting these logs?]
> [TODO: Are PagerDuty/OpsGenie/Teams alerting webhooks configured?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: name / email / phone] | Service down, health checks failing, deploy failed |
| L2 | Backend Lead | [TODO: name / email] | Persistent LLM errors, vector store corruption, data loss |
| L3 | Platform / Azure Admin | [TODO: name / email] | Azure App Service quota, networking, SSL certificate issues |
| L4 | Product Owner | [TODO: name / email] | Business-impacting outage > 30 min, data breach concern |
| External | OpenRouter Support | https://openrouter.ai | LLM API outage, rate limit issues |
| External | Azure Support | https://portal.azure.com | Azure infrastructure issues |
| Notifications | AI Delivery Bot outputs | kylo.deng@capco.com | All automated tool outputs (code review, docs, UAT) |

> [TODO: Fill in all team contacts above.]
> [TODO: What is the SLA / RTO / RPO for this service?]
> [TODO: Is there an on-call rota or incident management tool (PagerDuty, etc.)?]

---

## 7. Useful Commands

### Application Management

```bash
# --- Azure App Service ---

# Check app status
az webapp show --name training-bot-api --resource-group <rg> --query "state"

# Restart the API
az webapp restart --name training-bot-api --resource-group <rg>

# Restart the frontend
az webapp restart --name training-bot-frontend --resource-group <rg>

# Stream live logs
az webapp log tail --name training-bot-api --resource-group <rg>

# List all app settings (environment variables)
az webapp config appsettings list --name training-bot-api --resource-group <rg>

# Update a single environment variable
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg> \
  --settings API_KEY="<new-value>"
```

### Local Development

```bash
# Install dependencies
uv sync

# Run the API locally
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Generate requirements.txt (for deployment)
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Vector Store & Ingestion

```bash
# Ingest PDFs (local)
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Trigger ingestion via API endpoint
curl -X POST https://<api-hostname>/ingest

# Verify known products in vector store (local)
python -c "
from dotenv import load_dotenv; load_dotenv()
from core.vector_store import get_vector_store
s = get_vector_store()
s.load()
print('Products:', s.get_known_products())
"
```

### Session Management

```bash
# List all sessions
curl -s https://<api-hostname>/sessions