# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based AI coaching platform designed to help new insurance agents in Hong Kong master product knowledge and sales technique. It exposes two modes: a **Teacher mode** — an ongoing streamed chat session powered by a LangGraph agent with eight RAG tools that query a local vector store of insurance product PDFs — and an **Assessment mode** — a one-shot evaluator that scores a completed roleplay session across five dimensions. The backend is built in Python 3.13 with LangChain/LangGraph, connects to an LLM via OpenRouter (or a compatible endpoint), and uses a vector store (ChromaDB, FAISS, or Pinecone depending on configuration) for retrieval-augmented generation. A companion frontend (served separately) communicates with the API over HTTP. Both the API and frontend are deployed to Azure App Service via GitHub Actions on every push to `main`.

---

## 2. Health Checks

Run the following checks to confirm the service is healthy:

### 2.1 API Process

```bash
# Confirm the FastAPI process is listening
curl -s http://localhost:8000/docs | grep -i "Insurance Agent Trainer"
# Expected: HTML containing the app title
```

### 2.2 Application Endpoints

```bash
# Root / liveness
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/
# Expected: 200

# OpenAPI schema (confirms app boot)
curl -s http://localhost:8000/openapi.json | python3 -m json.tool | head -5
# Expected: valid JSON with "Insurance Agent Trainer" title

# Static file mount (confirms /data directory is accessible)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs/
# Expected: 200 or 404 (directory listing disabled) — NOT 500
```

### 2.3 Vector Store

```bash
# Trigger ingest check via the API (confirms vector store loaded at startup)
# Check logs for the startup message:
# "Vector store loaded (N products)"   ← healthy
# "No vector store found — run POST /ingest first."  ← needs ingest
```

### 2.4 LLM Connectivity

```bash
# Check the API can reach the LLM endpoint (OpenRouter or configured base URL)
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $API_KEY" \
  "${OPENAI_URL_BASE:-https://openrouter.ai/api/v1}/models"
# Expected: 200
```

### 2.5 Azure App Service (Production)

```bash
# API service
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/
# Expected: 200

# Frontend service
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
# Expected: 200
```

### 2.6 Sessions File

```bash
# Confirm sessions persist correctly
ls -lh data/sessions.json
# Expected: file exists, modified timestamp is recent
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log: `"No vector store found — run POST /ingest first."` | Vector store has never been built or the `data/` directory is missing | Run `POST /ingest` via the API, or execute `python -m core.ingest` manually against the PDF directory; confirm `data/` is present in the deployment |
| `500 Internal Server Error` on chat endpoints | LLM API key missing or invalid (`API_KEY` env var) | Verify `API_KEY` is set in Azure App Service → Configuration → Application Settings; rotate the key if expired |
| `500` errors with `SSLError` or `CERTIFICATE_VERIFY_FAILED` | `verify=False` httpx clients are bypassing SSL; an upstream proxy is intercepting TLS | This is intentional in code (`verify=False`) but may fail in restricted environments; ensure outbound HTTPS to `openrouter.ai` (or configured base URL) is whitelisted |
| Chat responses are very slow or time out | LLM endpoint rate limit hit, or cold-start on Azure App Service free/basic tier | Switch to a paid LLM tier; scale up Azure App Service plan; enable Always On in Azure portal |
| `KeyError: 'content'` or empty tool results | Vector store is empty or corrupted; retrieval returning zero chunks | Re-run ingest (`POST /ingest`); check that PDF files exist under `data/Insurance-product-info/`; verify embedding model credentials |
| `sessions.json` not found or `PermissionError` on sessions file | Deployment did not include the `data/` directory, or the app has no write permission to it | Ensure `data/` is writable by the app process; on Azure App Service, use `/home/` or a mounted storage volume for persistent state |
| Frontend cannot reach API (`CORS` error in browser console) | The frontend origin is not in the `allow_origins` list in `main.py` | Add the production frontend URL to the `CORSMiddleware` configuration and redeploy |
| GitHub Actions deploy fails at `azure/webapps-deploy@v3` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret is missing, expired, or misnamed | Re-download the publish profile from Azure Portal → App Service → Get publish profile; update the GitHub repository secret |
| `uv sync` fails in CI | `pyproject.toml` / `uv.lock` is out of sync, or Python 3.13 is unavailable on the runner | Run `uv lock` locally and commit the updated lockfile; confirm `python-version: '3.13'` is supported by `actions/setup-python` |
| Pytest fails in CI blocking deployment | Test regressions introduced in the PR | Review failing test output in the Actions run; fix the code or tests before merging to `main` |
| LLM annotation fails during ingest with `json.JSONDecodeError` | The LLM returned markdown-fenced JSON or malformed output | The annotator has a fence-stripping fallback; if it still fails, check the LLM model name (`OPENAI_MODEL`) and ensure the model supports structured output; retry ingest |
| `ModuleNotFoundError` for `langchain`, `langchain_openai`, etc. | Dependencies not installed; `uv export` did not produce a complete `requirements.txt` | Run `uv sync` locally; verify `requirements.txt` is generated correctly by `uv export --no-dev`; check that Azure App Service is running the startup command against the correct Python environment |
| `AttributeError` or `ImportError` in `api/agent.py` (`create_agent`) | The file imports `from langchain.agents import create_agent` but the actual agent factory functions (`make_teacher_agent`, `make_assessor_agent`) are not shown in the provided code | [TODO: Is the agent factory fully implemented in api/agent.py? The file appears truncated — verify complete implementation is committed] |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI installed and authenticated (`az login`)
- GitHub repository secrets set:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
- `uv` installed locally (`pip install uv` or `brew install uv`)
- Python 3.13 available locally

### 4.1 Standard Deployment (Push to `main`)

Deployment is automated via GitHub Actions (`.github/workflows/deploy.yml`). The pipeline runs on every push to `main`:

1. **CI runs tests** (`uv run pytest tests/ -v`) — deployment is blocked if tests fail.
2. On test pass, two parallel jobs deploy:
   - `deploy-api` → Azure App Service `training-bot-api`
   - `deploy-frontend` → Azure App Service `training-bot-frontend`

```bash
# To trigger: simply merge a PR or push directly to main
git checkout main
git pull origin main
# make your changes
git add .
git commit -m "feat: your change"
git push origin main
# Monitor: https://github.com/kylodeng/Insurance-Training-Bot-main/actions
```

### 4.2 Manual Deployment (Emergency / Hotfix)

```bash
# Step 1 — Install dependencies and generate requirements.txt
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt

# Step 2 — Run tests locally before deploying
uv run pytest tests/ -v

# Step 3 — Deploy API manually via Azure CLI
az webapp up \
  --name training-bot-api \
  --resource-group [TODO: Azure resource group name] \
  --runtime "PYTHON:3.13"

# Step 4 — Deploy Frontend manually
az webapp up \
  --name training-bot-frontend \
  --resource-group [TODO: Azure resource group name] \
  --runtime "PYTHON:3.13"

# Step 5 — Verify health (see Section 2)
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/
```

### 4.3 Post-Deployment Verification

```bash
# 1. Confirm API is up
curl https://training-bot-api.azurewebsites.net/openapi.json | python3 -m json.tool | head -10

# 2. Confirm vector store loaded (check App Service log stream)
az webapp log tail --name training-bot-api --resource-group [TODO: resource group]
# Look for: "Vector store loaded (N products)"

# 3. Send a test chat message
curl -s -X POST https://training-bot-api.azurewebsites.net/[TODO: chat endpoint path] \
  -H "Content-Type: application/json" \
  -d '{"message": "What products do you have?", "session_id": "test-001"}'
```

### 4.4 Rollback Procedure

#### Option A — Revert via Git (preferred)

```bash
# Find the last good commit
git log --oneline -10

# Revert the bad commit (creates a new revert commit — keeps history clean)
git revert <bad-commit-sha>
git push origin main
# CI/CD pipeline will automatically redeploy the reverted code
```

#### Option B — Azure Deployment Slot Swap (if slots are configured)

```bash
# [TODO: Confirm whether deployment slots are configured in Azure]
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group [TODO: resource group] \
  --slot staging \
  --target-slot production
```

#### Option C — Redeploy a specific Git tag

```bash
git checkout <last-known-good-tag>
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt
az webapp up --name training-bot-api --resource-group [TODO: resource group]
```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where to Find It | Threshold / Alert Condition |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Monitoring → Metrics → `Http5xx` | Alert if > 5 errors/min for 5 min |
| HTTP response time (P95) | Azure App Service → Metrics → `AverageResponseTime` | Alert if P95 > 30s (LLM streaming can be slow) |
| CPU usage | Azure App Service → Metrics → `CpuPercentage` | Alert if > 80% sustained for 5 min |
| Memory usage | Azure App Service → Metrics → `MemoryWorkingSet` | Alert if > 85% of plan limit |
| Requests per minute | Azure App Service → Metrics → `Requests` | Baseline and alert on sudden drops (may indicate crash) |
| LLM API errors | Application logs — search for `ERROR` + `openrouter` / `anthropic` | Any sustained LLM error rate |
| Vector store load failure | Application startup logs — `"No vector store found"` | Alert on any occurrence — service is degraded without RAG |
| Session file write errors | Application logs — `PermissionError` or `sessions.json` | Alert on any occurrence |

### 5.2 Log Locations

```bash
# Azure App Service — stream logs live
az webapp log tail \
  --name training-bot-api \
  --resource-group [TODO: resource group]

# Azure App Service — download logs
az webapp log download \
  --name training-bot-api \
  --resource-group [TODO: resource group] \
  --log-file api-logs.zip

# GitHub Actions — workflow run logs
# https://github.com/kylodeng/Insurance-Training-Bot-main/actions
```

### 5.3 Important Log Patterns

```
# Healthy startup
INFO:     Vector store loaded (N products)
INFO:     Application startup complete.

# Degraded startup (RAG unavailable)
WARNING:  No vector store found — run POST /ingest first.

# LLM errors
ERROR:    ... openrouter ... 429 Too Many Requests
ERROR:    ... APIConnectionError ...

# Session errors
ERROR:    ... PermissionError: [Errno 13] ... sessions.json

# Tool call logging (controlled by SHOW_TOOL_CALLS env var)
# Set SHOW_TOOL_CALLS=true to see all RAG tool invocations in logs
```

### 5.4 Recommended Alerts (Azure Monitor)

```bash
# [TODO: Configure these in Azure Monitor / Application Insights]
# 1. HTTP 5xx spike alert
# 2. App Service restart alert (indicates crash loop)
# 3. CPU > 80% for 5 minutes
# 4. No requests received for 30 minutes during business hours (dead service)
```

### 5.5 GitHub Actions Monitoring

- **Code Review (Tool 1):** Runs on every PR open/sync and Mondays 08:00 UTC
- **Tech Docs (Tool 2):** Runs on every push to `main` and Sundays 06:00 UTC
- **Business Docs (Tool 3):** Runs on version tags (`v*`) or manual dispatch
- **Auto Testing (Tool 4):** Runs on PRs touching `src/**`, `*.py`, `*.js`, `*.ts` and Wednesdays 07:00 UTC
- **UAT (Tool 5):** Runs on `release/*` branch creation or manual dispatch

Monitor scheduled workflow failures at: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: name / Slack handle / phone] | Service down, health checks failing, deployment failure |
| L2 | Tech Lead | [TODO: name / Slack handle / phone] | L1 cannot resolve within 30 min; data loss suspected; security incident |
| L3 | Platform / Azure Owner | [TODO: name / Slack handle / phone] | Azure App Service infrastructure issues, billing/quota problems |
| L4 | LLM Provider Support | OpenRouter: https://openrouter.ai/docs#support / Anthropic: https://support.anthropic.com | LLM API outage, model deprecation, billing issues |
| Business | Solution Owner | [TODO: name / email] | User-facing issues affecting training sessions; compliance or data concerns |
| Notify | kylo.deng@capco.com | kylo.deng@capco.com | Automated AI workflow outputs (code review, docs) are sent here |

**Incident channel:** [TODO: Slack channel / Teams channel name]
**Runbook review cadence:** [TODO: e.g. quarterly]

---

## 7. Useful Commands

### Application — Local Development

```bash
# Clone and set up
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env with your API_KEY, OPENAI_URL_BASE, OPENAI_MODEL etc.

# Install dependencies