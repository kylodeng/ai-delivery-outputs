# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that helps insurance agents in Hong Kong develop their sales and product knowledge skills. It provides two core modes: a **Teacher Mode** (ongoing streamed chat where an LLM-powered agent coaches the agent-trainee using a RAG knowledge base of insurance product PDFs) and a **Roleplay/Assessment Mode** (where the trainee practises sales conversations against an AI-simulated customer, followed by an automated accuracy assessment). The backend is built in Python using FastAPI, LangChain/LangGraph agents, and a vector store (Chroma/FAISS/Pinecone — selectable via environment) populated from insurance product PDFs stored under `data/Insurance-product-info/`. The application is deployed to **Azure App Service** (`training-bot-api` and `training-bot-frontend`) via GitHub Actions CI/CD on every push to `main`. A Chainlit UI serves as the primary frontend interface.

---

## 2. Health Checks

Run these checks in order to confirm the service is operating normally.

### 2.1 Azure App Service — API

```bash
# Check HTTP 200 from the FastAPI root
curl -f https://training-bot-api.azurewebsites.net/

# Check the docs endpoint (FastAPI auto-docs)
curl -f https://training-bot-api.azurewebsites.net/docs
```

### 2.2 Vector Store Loaded

On startup, the application logs one of two messages. Check App Service logs:

```
Vector store loaded (N products)    ← HEALTHY
No vector store found               ← ACTION REQUIRED: run POST /ingest
```

### 2.3 Ingest Endpoint

```bash
# Trigger a fresh ingest (idempotent — safe to re-run)
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

Expected: HTTP 200 with chunk count in response body.

### 2.4 Session List

```bash
curl https://training-bot-api.azurewebsites.net/sessions
# Expected: JSON array (may be empty [] on first boot — that is normal)
```

### 2.5 Frontend

```bash
curl -f https://training-bot-frontend.azurewebsites.net/
# Expected: HTTP 200 — Chainlit UI HTML
```

### 2.6 GitHub Actions

Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

- `Test & Deploy` workflow should show green on `main`.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all chat requests | Vector store not loaded at startup — `No vector store found` in logs | `POST /ingest` to rebuild the vector store; verify `data/` directory is populated with PDFs |
| `POST /ingest` fails with embedding errors | `API_KEY` or `OPENAI_URL_BASE` env var missing/wrong; OpenRouter/Anthropic quota exceeded | Check App Service application settings for `API_KEY` and `OPENAI_URL_BASE`; verify API key validity and quota |
| Agent gives wrong or hallucinated product facts | RAG retrieval returning poor chunks; PDF annotation cache stale | Delete `.annot.json` sidecar files and re-run ingest; verify PDF content is readable (`pdfplumber` extraction) |
| Chat streaming hangs / never completes | LLM provider timeout; `httpx` SSL verification disabled (`verify=False`) causing proxy issues | Check upstream LLM provider status; review App Service outbound network rules; check `OPENAI_URL_BASE` value |
| Sessions lost after restart | `data/sessions.json` not persisted — Azure App Service ephemeral filesystem | Mount an Azure Files share to the `data/` path; or configure persistent storage on the App Service plan |
| GitHub Actions deploy fails: `publish-profile` error | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret expired or missing | Re-download publish profiles from Azure Portal → App Service → Deployment Center; update GitHub secrets |
| GitHub Actions `test` job fails | Python 3.13 dependency incompatibility; missing test fixtures | Check `pytest` output in Actions log; run `uv run pytest tests/ -v` locally; verify `uv.lock` is committed |
| `uv sync` fails in CI | `uv.lock` out of sync with `pyproject.toml` | Run `uv lock` locally, commit updated `uv.lock` |
| CORS errors in browser | Frontend origin not in the `allow_origins` list in `api/main.py` | Add the production frontend URL to the `CORSMiddleware` `allow_origins` list and redeploy |
| Tool call events not visible in UI | `SHOW_TOOL_CALLS` env var not set | Set `SHOW_TOOL_CALLS=true` in App Service application settings |
| `lookup_hospital_network` returns no results | Hospital name spelling mismatch; mainland China hospital list PDF not ingested | Check PDF annotation JSON files exist under `data/`; re-run ingest; try alternate hospital name spellings |
| Assessment mode produces empty score | `ASSESSOR_SYSTEM` prompt truncated; conversation too long for context window | Trim conversation history before invoking assessor; check token usage in LLM provider dashboard |
| PDF chunking produces zero chunks | `pdfplumber` cannot extract text (scanned/image PDF) | Convert PDF to text-searchable format using OCR; replace source PDF |
| SendGrid email not delivered (CI tools) | `SENDGRID_API_KEY` secret missing or sender domain not verified | Verify secret in GitHub repository settings; check SendGrid sender authentication |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI installed and authenticated
- `uv` installed locally (`pip install uv`)
- GitHub repository secrets configured (see §5)

### 4.1 Normal Deployment (Automated)

Deployment is fully automated on push to `main` via `.github/workflows/deploy.yml`.

```
1. Developer merges PR to main
2. GitHub Actions triggers "Test & Deploy" workflow
3. test job: Python 3.13, uv sync, pytest tests/ -v
4. deploy-api job (only if tests pass):
   a. uv export → requirements.txt
   b. azure/webapps-deploy → training-bot-api
5. deploy-frontend job (only if tests pass):
   a. uv export → requirements.txt
   b. azure/webapps-deploy → training-bot-frontend
6. Verify health checks (§2) pass after deploy
```

### 4.2 Manual Deployment (Emergency / Hotfix)

```bash
# 1. Clone and install
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
uv sync

# 2. Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# 3. Deploy API via Azure CLI
az webapp deploy \
  --resource-group <resource-group-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 4. Deploy Frontend via Azure CLI
az webapp deploy \
  --resource-group <resource-group-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### 4.3 First-Time Ingest (after fresh deployment)

```bash
# Trigger document ingestion to build the vector store
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Confirm vector store loaded
# Look for: "Vector store loaded (N products)" in App Service logs
```

### 4.4 Rollback Procedure

#### Option A — GitHub Actions (preferred)

```bash
# 1. Identify the last known-good commit SHA
git log --oneline -10

# 2. Revert on main (creates a revert commit, re-triggers CI/CD)
git revert <bad-commit-sha>
git push origin main

# 3. Monitor Actions → "Test & Deploy" for green status
# 4. Run health checks (§2)
```

#### Option B — Azure Portal Deployment Slots

[TODO: Are deployment slots configured on the App Service plan? If yes, document swap procedure here.]

```bash
# If deployment slots exist:
az webapp deployment slot swap \
  --resource-group <resource-group-name> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

#### Option C — Re-deploy previous container / zip

```bash
# Find previous deployment in Azure Portal:
# App Service → Deployment Center → Logs → select previous deployment → Redeploy
```

---

## 5. Monitoring & Alerting

### 5.1 Required GitHub Secrets

Ensure all of the following are set in `Settings → Secrets and variables → Actions`:

| Secret | Purpose |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Azure App Service deploy for API |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Azure App Service deploy for frontend |
| `API_KEY` | LLM provider API key (OpenRouter or Anthropic) |
| `ANTHROPIC_API_KEY` | Claude API key (used by CI tools 1–5) |
| `GH_TOKEN` | GitHub PAT for cross-repo output writes |
| `SENDGRID_API_KEY` | Email notifications from CI tools |

### 5.2 Application Logs

```bash
# Stream live logs from Azure App Service
az webapp log tail \
  --resource-group <resource-group-name> \
  --name training-bot-api

# Key log lines to watch:
# INFO  Vector store loaded (N products)   ← startup OK
# WARNING No vector store found             ← action required
# INFO  [ingest] N chunks from <file>       ← ingest progress
# ERROR                                     ← any error — investigate immediately
```

### 5.3 Key Metrics to Watch

| Metric | Where | Threshold / Alert |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Metrics → Http 5xx | Alert if > 1% over 5 min |
| HTTP response time (P95) | Azure App Service → Metrics → Response Time | Alert if > 10s |
| CPU percentage | Azure App Service → Metrics → CPU Percentage | Alert if > 80% sustained |
| Memory usage | Azure App Service → Metrics → Memory Working Set | Alert if > 85% |
| GitHub Actions workflow status | GitHub → Actions tab | Alert on any failed run on `main` |
| LLM API quota / rate limits | OpenRouter / Anthropic dashboard | Alert at 80% quota consumed |
| Vector store chunk count | Startup log: `Vector store loaded (N products)` | Alert if N = 0 or significantly lower than baseline |
| `sessions.json` file size | App Service file system / Azure Files | Alert if file missing after restart (data loss) |

### 5.4 Alerting Setup

[TODO: Are Azure Monitor alerts configured? If not, set up action groups for the HTTP 5xx and CPU thresholds above.]

[TODO: Is there a status page or on-call rotation for this service?]

### 5.5 Log Levels

Set `LOG_LEVEL` environment variable to control verbosity:

- `INFO` — default; startup, ingest progress, request routing
- `DEBUG` — full LangChain tool call traces, chunk details

---

## 6. Escalation Path

```
Level 1 — On-call engineer
  [TODO: Name, contact (Slack/Teams/phone)]
  Handles: health check failures, restart, re-ingest

Level 2 — Backend developer / Tech lead
  [TODO: Name, contact]
  Handles: code bugs, LangChain/LangGraph issues, vector store corruption

Level 3 — Cloud/infrastructure owner
  [TODO: Name, contact]
  Handles: Azure App Service outages, networking, secrets rotation

LLM Provider (external)
  OpenRouter: https://openrouter.ai/docs#errors  /  status.openrouter.ai
  Anthropic:  https://status.anthropic.com

Azure Support
  [TODO: Azure support tier and ticket portal URL]

Escalation SLA
  [TODO: Define P1/P2/P3 response times]
```

---

## 7. Useful Commands

### Service Management

```bash
# Restart API App Service
az webapp restart \
  --resource-group <resource-group-name> \
  --name training-bot-api

# Restart Frontend App Service
az webapp restart \
  --resource-group <resource-group-name> \
  --name training-bot-frontend

# Stream live logs
az webapp log tail \
  --resource-group <resource-group-name> \
  --name training-bot-api
```

### Vector Store / Ingest

```bash
# Trigger ingest via API
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Run ingest locally (rebuilds vector store from data/ PDFs)
cd Insurance-Training-Bot-main
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Clear all annotation cache (forces re-annotation on next ingest)
find data/ -name "*.annot.json" -delete
```

### Local Development

```bash
# Install dependencies
uv sync

# Start the FastAPI server locally
uv run uvicorn api.main:app --reload --port 8000

# Start with debug logging
LOG_LEVEL=DEBUG uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### Session Management

```bash
# List all sessions
curl https://training-bot-api.azurewebsites.net/sessions

# Delete a specific session
curl -X DELETE https://training-bot-api.azurewebsites.net/sessions/<session-id>

# Backup sessions file (before any risky operation)
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%d_%H%M%S)
```

### GitHub Actions — Manual Triggers

```bash
# Manually trigger code review (Tool 1)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Manually regenerate tech docs (Tool 2)
gh workflow run tool2_tech_docs.yml

# Manually generate business docs (Tool 3)
gh workflow run tool3_business_docs.yml \
  -f project_name="Insurance Training Bot" \
  -f release_version="1.0.0"

# Manually generate test suite (Tool 4)
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Manually generate UAT test pack (Tool 5)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

### Azure App Service — Configuration

```bash
# View current app settings
az webapp config appsettings list \
  --resource-group <resource-group-name> \
  --name training-bot-api \
  --output table

# Set/update an environment variable
az webapp config appsettings set \
  --resource-group <resource-group-name> \
  --name training-bot-api \
  --settings SHOW_TOOL_CALLS=true

# View deployment history
az webapp deployment list \
  --resource-group <resource-group-name> \
  --name training-bot-api \
  --output table
```

---

> **TODOs Summary**
> - [ ] Fill in escalation contacts (Level 1, 2, 3)
> - [ ] Confirm Azure resource group name
> - [ ] Confirm whether Azure Files mount is configured for `data/` persistence
> - [ ] Confirm whether deployment slots (staging/production) are enabled
> - [ ] Confirm Azure Monitor alerts are configured and action groups set
> - [ ] Confirm `/ingest` endpoint URL and auth requirements (is it open or protected?)
> - [ ] Confirm which vector store backend is in use (Chroma / FAISS / Pinec