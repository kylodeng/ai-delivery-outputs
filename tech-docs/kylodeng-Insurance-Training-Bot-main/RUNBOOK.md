# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training environment for insurance sales agents, deployed to Azure App Service. It operates in two modes: a **Teacher mode**, where a LangGraph agent engages trainees in interactive coaching sessions using Retrieval-Augmented Generation (RAG) over a corpus of insurance product PDFs (Sun Life Hong Kong products), and a **Roleplay/Assessor mode**, where the system simulates realistic Hong Kong customer personas and evaluates the trainee's sales performance across multiple dimensions. The backend connects to an LLM provider via OpenRouter (default) or a configurable OpenAI-compatible endpoint, maintains per-session conversation state in a JSON file on disk, and serves static PDF documents over HTTP. A companion CI/CD pipeline on GitHub Actions runs tests on every PR and deploys both the API and a separate frontend to Azure App Service on merge to `main`.

---

## 2. Health Checks

Perform the following checks to confirm the service is running correctly:

### 2.1 API Liveness

```bash
# Replace with your actual Azure App Service hostname
curl -f https://training-bot-api.azurewebsites.net/docs
# Expected: HTTP 200, FastAPI Swagger UI HTML
```

### 2.2 Vector Store Loaded

Check the application startup logs for the following line:

```
INFO  Vector store loaded (N products)
```

If you see instead:

```
WARNING  No vector store found — run POST /ingest first.
```

the RAG knowledge base has not been initialised. See [Section 4 — Deployment Procedure](#4-deployment-procedure).

### 2.3 Sessions File Exists

```bash
# On the Azure App Service instance (via Kudu/SSH console)
ls -lh /home/site/wwwroot/data/sessions.json
# Expected: file present, non-zero size
```

### 2.4 Static Docs Endpoint

```bash
curl -I https://training-bot-api.azurewebsites.net/docs/
# Expected: HTTP 200 or 301
```

### 2.5 LLM Connectivity

```bash
# Trigger a minimal chat request and confirm a streamed response
curl -X POST https://training-bot-api.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test", "message": "hello"}'
# Expected: HTTP 200 with streamed text tokens
```

[TODO: What is the exact `/chat` endpoint path and request schema? It is not fully shown in the provided files.]

### 2.6 GitHub Actions CI Health

Navigate to:
`https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

All workflow runs on `main` should show green ✅. A red ❌ on `Test & Deploy` means the last deployment may have failed.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `WARNING: No vector store found` in startup logs; RAG tools return empty results | `/ingest` endpoint was never called, or the vector store index file is missing/corrupt after a deployment | SSH into the App Service (Kudu console) and run `POST /ingest` or execute `python -m core.ingest --pdf-dir data/Insurance-product-info`. Verify the index file is written to the expected path. |
| `500 Internal Server Error` on all chat requests | `API_KEY` environment variable missing or revoked; LLM provider is unreachable | Check Azure App Service → Configuration → Application Settings for `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`. Test LLM connectivity directly. Rotate the key if revoked. |
| HTTP `SSL: CERTIFICATE_VERIFY_FAILED` or similar TLS errors | `httpx.Client(verify=False)` is set but the upstream endpoint is enforcing mutual TLS, or a corporate proxy is stripping certificates | Confirm `OPENAI_URL_BASE` is correct. If behind a corporate proxy, add the proxy CA cert to the container trust store. [TODO: Is there a known proxy configuration for this environment?] |
| Sessions lost after redeployment | `data/sessions.json` lives on the ephemeral filesystem and is wiped on deploy | Mount an Azure Files share to `/home/site/wwwroot/data/` in the App Service configuration, or enable App Service persistent storage. |
| `KeyError: 'ANTHROPIC_API_KEY'` in GitHub Actions logs | Secret not set in GitHub repository secrets | Go to **Settings → Secrets and variables → Actions** and add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`. |
| Deployment workflow fails at `azure/webapps-deploy` step | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret is missing or has expired | Regenerate the publish profile in Azure Portal → App Service → Get publish profile, then update the GitHub secret. |
| RAG search returns stale or incorrect product info | PDFs were updated but `/ingest` was not re-run | Re-run the ingest pipeline after uploading new PDFs to `data/Insurance-product-info/`. Delete stale `.annot.json` sidecar files if the document structure changed. |
| `json.JSONDecodeError` in logs when loading sessions | `sessions.json` is corrupt (e.g., partial write during crash) | Back up the corrupt file, then delete it. The app will create a new empty sessions file on next startup (`load_sessions()` is called in `lifespan`). |
| Frontend not loading / CORS errors in browser console | CORS `allow_origins` list in `main.py` does not include the production frontend URL | Add the production frontend hostname to the `allow_origins` list and redeploy. [TODO: What is the production frontend URL?] |
| LLM response latency > 30s; users report timeouts | Model throughput limits on OpenRouter free tier; or model changed to a slower one | Check `OPENAI_MODEL` env var. Switch to a faster/paid model. Consider adding a request timeout to the `httpx` clients. |
| Tool calls not appearing in UI when expected | `SHOW_TOOL_CALLS` env var set to `false`, or Chainlit session toggle is off | Set `SHOW_TOOL_CALLS=true` in App Service configuration, or instruct users to enable the toggle in the Chainlit UI. |
| `uv sync` fails in CI | `pyproject.toml` or `uv.lock` is inconsistent | Run `uv lock` locally and commit the updated lock file. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- Access to the GitHub repository with write permissions
- `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secrets configured in GitHub
- Python 3.13 and `uv` installed locally

### 4.1 Standard Deployment (Automated via GitHub Actions)

1. **Merge a PR to `main`** (or push directly to `main`).
2. GitHub Actions automatically triggers the `Test & Deploy` workflow (`.github/workflows/deploy.yml`).
3. The `test` job runs:
   ```bash
   uv sync
   uv run pytest tests/ -v
   ```
4. If tests pass, `deploy-api` and `deploy-frontend` jobs run in parallel:
   - `uv export --no-dev --format requirements-txt -o requirements.txt`
   - Azure Web Apps Deploy action pushes to `training-bot-api` and `training-bot-frontend`.
5. **Verify deployment** using the health checks in [Section 2](#2-health-checks).
6. **Trigger ingest** if PDF content has changed:
   ```bash
   curl -X POST https://training-bot-api.azurewebsites.net/ingest
   ```
   [TODO: Confirm exact `/ingest` endpoint path and authentication requirements.]

### 4.2 Manual Deployment (Emergency / Hotfix)

```bash
# 1. Build requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API manually via Azure CLI
az webapp deployment source config-zip \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src <path-to-zip>

# 3. Deploy frontend manually
az webapp deployment source config-zip \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src <path-to-zip>
```

[TODO: What is the Azure resource group name?]
[TODO: Is zip-deploy the correct method, or is SCM/Kudu used?]

### 4.3 Post-Deployment Checklist

- [ ] API health check returns HTTP 200
- [ ] Vector store loaded log line present
- [ ] `sessions.json` exists and is accessible
- [ ] Test a Teacher mode chat session end-to-end
- [ ] Test a Roleplay mode session end-to-end
- [ ] Confirm tool calls (product search, hospital lookup) return results

### 4.4 Rollback Steps

1. **Identify the last known-good deployment** in the GitHub Actions run history.
2. **Via Azure Portal:**
   - Navigate to App Service → `training-bot-api` → Deployment Center → Deployment History.
   - Select the previous successful deployment and click **Redeploy**.
   - Repeat for `training-bot-frontend`.
3. **Via GitHub Actions (re-run):**
   - Find the last successful `Test & Deploy` run.
   - Click **Re-run jobs** → **Re-run all jobs**.
4. **Via Git revert (if a bad commit caused the issue):**
   ```bash
   git revert <bad-commit-sha>
   git push origin main
   # CI/CD will redeploy automatically
   ```
5. **Verify rollback** using the health checks in [Section 2](#2-health-checks).

> ⚠️ **Note:** If `sessions.json` was on ephemeral storage, rollback will not restore lost sessions. If the vector store index was overwritten, re-run `/ingest` after rollback.

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

The application uses Python's standard `logging` module at `INFO` level.

```bash
# Stream live logs from Azure App Service
az webapp log tail \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP>
```

**Key log patterns to watch:**

| Log Pattern | Meaning |
|---|---|
| `Vector store loaded (N products)` | Healthy startup — RAG is available |
| `No vector store found — run POST /ingest first` | RAG unavailable — action required |
| `[ingest] index saved (N chunks)` | Ingest completed successfully |
| `[ingest] annotation failed for X` | PDF annotation failed; raw chunker used as fallback |
| `[ingest] rate-limit pause Xs` | Embedding batching in progress (normal) |
| `ERROR` / `CRITICAL` at any path | Escalate immediately |

### 5.2 Metrics to Monitor

| Metric | Source | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service metrics | > 1% over 5 min |
| HTTP response time (P95) | Azure App Service metrics | > 10s |
| CPU usage | Azure App Service metrics | > 80% sustained for 10 min |
| Memory usage | Azure App Service metrics | > 85% |
| GitHub Actions `Test & Deploy` — failure | GitHub Actions notifications | Any failure on `main` |
| LLM API error rate | Application logs (grep `ERROR`) | Any sustained errors |

[TODO: Are Azure Monitor alerts or Application Insights configured for this service?]
[TODO: Is there a Slack/Teams/PagerDuty integration for alerts?]

### 5.3 GitHub Actions Workflow Health

Monitor these workflows for failures:

| Workflow | Trigger | What to check |
|---|---|---|
| `Test & Deploy` | Push/PR to `main` | Test failures, deploy failures |
| `Tool 1 — Code Review` | PR open/sync, Monday 08:00 UTC | Claude API errors, output repo write failures |
| `Tool 2 — Tech Documentation` | Merge to `main`, Sunday 06:00 UTC | Doc generation failures |
| `Tool 3 — Business Documentation` | Version tags (`v*`), manual | Business doc generation failures |
| `Tool 4 — Auto Testing` | PR open/sync on `src/**`, Wednesday 07:00 UTC | Test generation failures |
| `Tool 5 — UAT Facilitation` | Release branch creation, manual | UAT pack generation failures |

### 5.4 Data / Vector Store Health

```bash
# Check vector store index size and modification time
ls -lh data/  # look for chroma/ or faiss index files
# [TODO: What is the exact vector store directory/file name for the deployed environment?]
```

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in contact] | Service down, health checks failing |
| L2 | Backend Lead | [TODO: fill in contact] | LLM integration failures, RAG issues, data loss |
| L3 | Platform/DevOps Lead | [TODO: fill in contact] | Azure infrastructure issues, CI/CD pipeline failures |
| L4 | Project Owner | kylo.deng@capco.com | Business-impacting outage > 1 hour, data breach |
| External | Azure Support | [TODO: Azure support ticket URL / phone] | Azure App Service platform failures |
| External | OpenRouter / LLM Provider | [TODO: provider support URL] | LLM API outage or key revocation |

---

## 7. Useful Commands

> All commands assume you are in the repository root unless otherwise noted.

### 7.1 Local Development

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Run the API server
uv run uvicorn api.main:app --reload --port 8000

# Ingest PDFs into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info

# Run all tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_chunker.py -v
```

### 7.2 Azure App Service Operations

```bash
# Stream live API logs
az webapp log tail \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP>

# Restart the API app
az webapp restart \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP>

# Restart the frontend app
az webapp restart \
  --name training-bot-frontend \
  --resource-group <RESOURCE_GROUP>

# List current application settings
az webapp config appsettings list \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --output table

# Update an environment variable
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --settings API_KEY="<new-value>"

# Open SSH/Kudu console (browser)
az webapp ssh \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP>
```

### 7.3 Vector Store & Data

```bash
# Re-run ingestion (all PDFs)
uv run python -m core.ingest \
  --pdf-dir data/Insurance-product-info \
  --verbose

# Delete stale annotation sidecars to force re-annotation
find data/Insurance-product-info -name "*.annot.json" -delete

# Trigger ingest via API (if endpoint is available)
curl -X POST https://training-bot-api.azurewebsites.net/ingest \
  -H "Content-Type: application/json"
```

### 7.4 Sessions Management

```bash
# Backup sessions before destructive operations
cp data/sessions.json data/sessions.json.bak.$(date +%