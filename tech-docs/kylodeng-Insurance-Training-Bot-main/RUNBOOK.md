# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered insurance sales training platform targeting the Hong Kong market. It operates in two primary modes: a **Teacher mode**, where a LangGraph agent conducts interactive coaching sessions with trainee agents using a RAG (Retrieval-Augmented Generation) pipeline over a corpus of insurance product PDFs (Sun Life Hong Kong products, hospital network lists, etc.); and a **Roleplay/Assessment mode**, where a simulated customer persona challenges the trainee and an Assessor agent grades the session across five dimensions. The backend is deployed as an Azure App Service (`training-bot-api`) with a separate Azure App Service for the frontend (`training-bot-frontend`), both deployed automatically via GitHub Actions on push to `main`. The vector store (ChromaDB or FAISS, with optional Pinecone) is built from ingested PDFs stored under `data/Insurance-product-info/` and loaded at startup.

---

## 2. Health Checks

### API Service

| Check | How to verify | Expected result |
|---|---|---|
| API process up | `curl -s https://<api-host>/docs` | FastAPI Swagger UI HTML returned |
| Lifespan startup | App logs at startup | Log line: `Vector store loaded (N products)` |
| Vector store loaded | `GET /` or startup log | No `WARNING: No vector store found` in logs |
| LLM connectivity | Send a test chat message via UI or API | Non-error streaming response received |
| Sessions persistence | `GET /sessions` (or equivalent endpoint) | Returns existing sessions from `sessions.json` |
| Static file serving | `GET /docs/<filename>.pdf` | PDF bytes returned (HTTP 200) |

### Frontend Service

| Check | How to verify | Expected result |
|---|---|---|
| Frontend reachable | `curl -s https://<frontend-host>/` | HTML page returned (HTTP 200) |
| CORS connectivity | Browser console on frontend | No CORS errors when calling API |

### GitHub Actions Pipelines

| Check | How to verify | Expected result |
|---|---|---|
| CI passing | GitHub Actions → `Test & Deploy` workflow | All jobs green |
| Secrets present | Repository Settings → Secrets | `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`, `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` exist |

> [TODO: What is the actual Azure App Service hostname for the API and frontend?]
> [TODO: Is there a dedicated `/health` or `/ping` endpoint implemented in `api/main.py` beyond what is shown?]

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log shows `WARNING: No vector store found — run POST /ingest first.` | Vector store index file missing or not built | 1. Run `POST /ingest` via API or execute `python core/ingest.py` with correct `--pdf-dir`. 2. Confirm `data/` directory contains PDFs. 3. Restart the app service and check logs for `Vector store loaded`. |
| All RAG tool calls return empty results / agent says it cannot find product info | Vector store empty, wrong `VECTOR_STORE_TYPE` env var, or index file not present at expected path | 1. Check `VECTOR_STORE_TYPE` env var matches the stored index type (chroma/faiss/pinecone). 2. Re-run ingestion. 3. Verify index files are present in the expected directory. [TODO: What is the exact index file path / directory?] |
| LLM calls fail with 401/403 | `API_KEY` secret missing, expired, or wrong | 1. Verify `API_KEY` env var / Azure App Service Application Setting. 2. Confirm `OPENAI_URL_BASE` points to correct endpoint (OpenRouter or Anthropic). 3. Rotate key if compromised. |
| LLM calls fail with 429 (rate limit) | Too many concurrent requests or free-tier limit hit | 1. Reduce concurrent users. 2. Check `SHOW_TOOL_CALLS` — high tool-call volume increases token usage. 3. Upgrade API tier or switch model via `OPENAI_MODEL` env var. |
| `ssl.SSLError` or `httpx` SSL warnings in logs | `verify=False` is set in `httpx.Client` — this suppresses SSL errors but logs warnings | This is by design (note: security risk). [TODO: Should SSL verification be re-enabled for production? Current code explicitly disables it.] |
| Streaming response hangs or cuts off mid-message | LLM API timeout, network interruption, or large response exceeding token limit | 1. Check LLM provider status page. 2. Reduce `max_tokens` if applicable. 3. Check Azure App Service idle timeout settings (default 230s). 4. Review application logs for upstream errors. |
| Sessions lost after restart | `sessions.json` not persisted (ephemeral filesystem on Azure App Service) | 1. Check if Azure App Service has persistent storage configured. 2. [TODO: Is sessions.json stored on a mounted Azure Files share or ephemeral local disk?] 3. Consider migrating session storage to Azure Blob Storage or a database. |
| PDF documents not loading in UI (`/docs/<file>` returns 404) | `data/` directory not deployed or `StaticFiles` mount path incorrect | 1. Verify `data/Insurance-product-info/` is included in the deployment package. 2. Check `app.mount("/docs", StaticFiles(directory=...))` path resolves correctly in Azure. 3. [TODO: Is the data directory deployed with the app or mounted separately?] |
| CI/CD pipeline fails on `deploy-api` job | `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret missing/expired or `training-bot-api` app name wrong | 1. Re-download publish profile from Azure portal. 2. Update the GitHub secret. 3. Confirm `app-name: training-bot-api` matches the Azure resource name. |
| Agent returns incorrect product facts / hallucinations | RAG retrieval miss — chunk not indexed, annotation marked page as irrelevant incorrectly | 1. Re-check `.annot.json` sidecar files for pages marked `"relevant": false` incorrectly. 2. Re-run ingestion with `--verbose`. 3. Check chunk count in startup logs vs expected. |
| `AttributeError` or `ImportError` on startup | Dependency mismatch, Python version issue, or `uv sync` not run | 1. Run `uv sync` locally to reproduce. 2. Check Python version is 3.13 (as per CI). 3. Review `requirements.txt` generated by `uv export`. |
| GitHub Actions AI tools (Tool 1–5) fail | Missing secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) or output repo not found | 1. Verify all required secrets in repo settings. 2. Confirm `ai-delivery-outputs` repo exists under `OUTPUT_REPO_OWNER`. 3. Check workflow run logs for specific HTTP error codes. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated to the correct subscription
- GitHub repository secrets configured (see Environment Variables section)
- `uv` installed locally (`pip install uv`)
- Access to Azure App Service `training-bot-api` and `training-bot-frontend`

### Standard Deployment (Automated — push to `main`)

```
1. Merge a pull request or push a commit to the `main` branch.

2. GitHub Actions workflow `.github/workflows/deploy.yml` triggers automatically.

3. Job: `test`
   - Checks out repo
   - Sets up Python 3.13
   - Installs `uv`
   - Runs: uv sync
   - Runs: uv run pytest tests/ -v
   - Pipeline STOPS here if any test fails.

4. Job: `deploy-api` (only if tests pass AND push to main)
   - Generates requirements.txt: uv export --no-dev --format requirements-txt -o requirements.txt
   - Deploys to Azure App Service `training-bot-api` using publish profile secret.

5. Job: `deploy-frontend` (only if tests pass AND push to main)
   - Generates requirements.txt: uv export --no-dev --format requirements-txt -o requirements.txt
   - Deploys to Azure App Service `training-bot-frontend` using publish profile secret.

6. Verify deployment:
   - Check Azure App Service → Deployment Center for status.
   - Curl the API health check endpoint.
   - Check startup logs for "Vector store loaded".
```

### Manual Deployment

```bash
# 1. Install dependencies
uv sync

# 2. Export requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# 3. Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 4. Deploy Frontend manually via Azure CLI
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

> [TODO: What Azure resource group contains these App Services?]
> [TODO: Is the data/ directory and vector store index deployed with the app package, or ingested post-deploy?]

### First-Time Ingestion (after fresh deploy)

```bash
# Run ingestion to build the vector store from PDFs
python core/ingest.py --pdf-dir data/Insurance-product-info/

# Or via API endpoint (if exposed)
curl -X POST https://<api-host>/ingest
```

### Rollback Steps

```
Option A — Revert via GitHub (preferred):
1. Identify the last known-good commit SHA from git log or GitHub Actions history.
2. git revert HEAD  (or git revert <bad-commit-sha>)
3. Push the revert commit to main → triggers automated deployment of previous code.

Option B — Azure App Service deployment slots (if configured):
[TODO: Are deployment slots configured on the Azure App Services?]
1. az webapp deployment slot swap --name training-bot-api \
     --resource-group <rg> --slot staging

Option C — Azure App Service manual rollback:
1. Azure Portal → App Service: training-bot-api
2. Deployment Center → Deployment History
3. Select previous successful deployment → Redeploy

Option D — Emergency: stop the service
az webapp stop --name training-bot-api --resource-group <rg>
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to find | Alert threshold |
|---|---|---|
| App Service HTTP 5xx error rate | Azure Monitor → App Service → HTTP 5xx | > 1% over 5 min |
| App Service response time (P95) | Azure Monitor → App Service → Response Time | > 10s P95 |
| App Service instance CPU | Azure Monitor → App Service → CPU Percentage | > 80% sustained 10 min |
| App Service memory | Azure Monitor → App Service → Memory | > 85% |
| App Service availability | Azure Monitor uptime check | < 99% |
| LLM API error rate | Application logs — search for `ERROR` + status codes 429/401/500 | Any 401/403; sustained 429 |
| Vector store load failure | App startup logs | Any occurrence of `No vector store found` |

### Log Streams to Watch

```bash
# Stream live logs from Azure App Service
az webapp log tail --name training-bot-api --resource-group <rg>

# Key log patterns to alert on:
# ERROR      → Any unhandled exception
# WARNING: No vector store found  → Ingestion required
# 429        → Rate limit on LLM API
# SSLError   → Network/SSL misconfiguration
# JSONDecodeError → LLM returning malformed responses
```

### GitHub Actions Monitoring

- Watch `.github/workflows/deploy.yml` — failed runs mean code is not deployed
- Watch `tool1_code_review.yml` through `tool5_uat.yml` for AI pipeline failures

### Structured Logging

```
The application uses Python's standard `logging` module with `basicConfig(level=logging.INFO)`.
Key logger: `api.main` — logs all startup events, ingestion status, and request errors.
```

> [TODO: Is Azure Application Insights configured for the App Services? If so, the instrumentation key/connection string should be added as an env var.]
> [TODO: Are there any existing Azure Monitor alert rules or action groups configured?]

---

## 6. Escalation Path

| Level | Role | Contact | Trigger |
|---|---|---|---|
| L1 | On-call DevOps / Engineer | [TODO: Name, email, phone/Teams handle] | Service down > 5 min, deployment failure |
| L2 | Tech Lead | [TODO: Name, email] | L1 unresolved > 30 min, data loss, security incident |
| L3 | Solution Owner / Business Sponsor | [TODO: Name, email] | Customer-facing outage > 1 hour, compliance issue |
| External | Azure Support | [TODO: Azure support ticket URL / support tier] | Azure platform failure, App Service issues |
| External | LLM Provider (OpenRouter / Anthropic) | [TODO: Provider status page and support URL] | LLM API outage |
| Notifications | Email alerts currently configured to | `kylo.deng@capco.com` | AI tool pipeline completions and failures |

> [TODO: Fill in all team contacts above. Current code only references `kylo.deng@capco.com` as the notify email.]

---

## 7. Useful Commands

### Local Development

```bash
# Install all dependencies
uv sync

# Start the API server (infers uvicorn from FastAPI convention)
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run all tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_<name>.py -v
```

### Ingestion

```bash
# Full ingestion with LLM annotation
python core/ingest.py --pdf-dir data/Insurance-product-info/ --verbose

# Export requirements before deployment
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Azure App Service

```bash
# View live logs
az webapp log tail --name training-bot-api --resource-group <rg>
az webapp log tail --name training-bot-frontend --resource-group <rg>

# Restart the API service
az webapp restart --name training-bot-api --resource-group <rg>

# Restart the frontend service
az webapp restart --name training-bot-frontend --resource-group <rg>

# Stop the API service (emergency)
az webapp stop --name training-bot-api --resource-group <rg>

# Start the API service
az webapp start --name training-bot-api --resource-group <rg>

# Check App Service status
az webapp show --name training-bot-api --resource-group <rg> \
  --query "state" -o tsv

# List recent deployments
az webapp deployment list-publishing-credentials \
  --name training-bot-api --resource-group <rg>

# SSH into the App Service container
az webapp ssh --name training-bot-api --resource-group <rg>
```

### GitHub Actions — Manual Triggers

```bash
# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# Trigger tech docs generation
gh workflow run tool2_tech_docs.yml

# Trigger business docs for a version
gh workflow run tool3_business_docs.yml \
  -f project_name="Insurance Training Bot" \
  -f release_version="1.0.0"

# Trigger auto test generation
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"

# View recent workflow runs
gh run list --workflow=deploy.yml

# View logs for a specific run
gh run view <run-id> --log
```

### Debugging Vector Store

```python
# Quick Python check — run inside az webapp ssh or locally
from core.vector_store import get_vector_store
store = get_vector_store()
loaded = store