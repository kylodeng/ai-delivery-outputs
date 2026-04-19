# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application designed to train insurance sales agents in Hong Kong. It exposes two AI-powered modes: a **Teacher mode**, which provides an interactive chat experience where a LangGraph agent coaches agents on insurance products, discovery techniques, and sales skills using a RAG (Retrieval-Augmented Generation) pipeline backed by a vector store of insurance product PDFs; and a **Roleplay/Assessment mode**, in which a simulated customer (driven by a randomly generated Hong Kong customer profile) engages the trainee, followed by an automated performance assessment. The backend is built with FastAPI and LangChain/LangGraph, uses an OpenAI-compatible LLM endpoint (defaulting to OpenRouter), and is deployed to **Azure App Service** (`training-bot-api` and `training-bot-frontend`) via GitHub Actions CI/CD on every push to `main`. A PDF ingestion pipeline pre-processes insurance product brochures into a local vector store (FAISS or Chroma) which must be populated before the service can answer product questions.

---

## 2. Health Checks

### API Service

| Check | How to verify | Expected result |
|---|---|---|
| App Service reachability | `GET https://training-bot-api.azurewebsites.net/` | HTTP 200 or redirect |
| FastAPI docs endpoint | `GET https://training-bot-api.azurewebsites.net/docs` | Swagger UI renders |
| Vector store loaded | Check startup log for `Vector store loaded (N products)` | N > 0 |
| LLM connectivity | Send a test chat message via the `/chat` or streaming endpoint | Non-empty streamed response |
| PDF data mounted | `GET https://training-bot-api.azurewebsites.net/docs/` (static file mount) | HTTP 200, lists data files |
| Sessions persistence | `GET /sessions` (or equivalent) | Returns existing session list |

### Frontend Service

| Check | How to verify | Expected result |
|---|---|---|
| App Service reachability | `GET https://training-bot-frontend.azurewebsites.net/` | HTTP 200, UI renders |
| CORS connectivity | Browser network tab — chat request to API | No CORS error |

### Vector Store

```bash
# Check vector store file exists on the server
ls -lh data/vector_store/   # [TODO: confirm exact path used by FAISS/Chroma store on Azure]

# Trigger re-ingest if missing
POST /ingest   # see §7 Useful Commands
```

> **Warning:** If the startup log shows `No vector store found — run POST /ingest first`, the service is running but **all RAG tool calls will fail silently**. Re-ingest immediately.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log: `No vector store found — run POST /ingest first` | Vector store file missing (first deploy, storage wiped, or cold container) | 1. Verify PDF files exist under `data/Insurance-product-info/`. 2. `POST /ingest` to rebuild. 3. Confirm log shows `index saved (N chunks)`. |
| LLM returns empty or error responses | `API_KEY` env var missing/invalid, or OpenRouter/upstream LLM is down | 1. Check `API_KEY` in Azure App Service Configuration. 2. Verify `OPENAI_URL_BASE` points to correct endpoint. 3. Check upstream LLM provider status page. |
| `SSL: CERTIFICATE_VERIFY_FAILED` in logs | `httpx.Client(verify=False)` is set but a proxy or Azure middleware is intercepting | Expected — SSL verification is disabled by design. If errors still appear, check Azure outbound proxy settings. |
| Chat responses contain no product citations / wrong product details | Vector store stale or embeddings out of date | 1. Re-run `POST /ingest` after adding new PDFs. 2. Check `data/Insurance-product-info/` contains all expected PDFs. |
| `KeyError: 'ANTHROPIC_API_KEY'` in GitHub Actions | Secret not set in repo/org secrets | Add `ANTHROPIC_API_KEY` in GitHub → Settings → Secrets. |
| GitHub Actions workflow fails at `pip install anthropic requests` | Network issue or PyPI outage | Retry the workflow; if persistent, pin package versions in the install step. |
| Sessions lost after restart | `sessions.json` is on ephemeral container storage | 1. Mount a persistent Azure File Share to `data/` directory. [TODO: confirm if persistent storage is configured on Azure App Service] |
| `POST /ingest` times out on Azure | PDF annotation LLM calls take too long; App Service request timeout exceeded | 1. Run ingest locally and upload the resulting vector store artifact. 2. Or increase Azure App Service request timeout. [TODO: what is the current timeout setting?] |
| CORS errors in browser | Frontend origin not in `allow_origins` list in `main.py` | Add the production frontend URL to `allow_origins` in `api/main.py` and redeploy. |
| Roleplay profile generation fails | LLM call failure or malformed JSON response from LLM | 1. Check application logs for the exact exception. 2. Verify LLM endpoint is reachable. 3. Retry — the profile generator uses random data from static lists and should not fail unless the LLM is down. |
| Assessment/scoring returns garbage | LLM returned non-JSON for the assessor structured output | 1. Check logs for `JSON parse error`. 2. Retry the assessment. 3. If persistent, check if the LLM model (`OPENAI_MODEL`) supports structured output reliably. |
| `train-bot-api` App Service shows 503 | Cold start or app crashed | 1. Check Azure App Service logs (Log Stream). 2. Restart the App Service. 3. Check for OOM — vector store + LLM clients can be memory-heavy. [TODO: what is the App Service plan/SKU?] |
| `deploy-api` GitHub Actions job fails | Invalid or expired `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret | Regenerate publish profile from Azure Portal → App Service → Get publish profile, update GitHub secret. |

---

## 4. Deployment Procedure

### Prerequisites

- Access to the GitHub repository `kylodeng/Insurance-Training-Bot-main`
- Azure CLI or Azure Portal access to `training-bot-api` and `training-bot-frontend` App Services
- GitHub repository secrets configured (see §5)

### Normal Deployment (CI/CD — Recommended)

```
1. Merge feature branch → `main` via Pull Request.
2. GitHub Actions automatically runs the `Test & Deploy` workflow (.github/workflows/deploy.yml):
   a. Job `test`:
      - Installs Python 3.13 + uv
      - Runs `uv run pytest tests/ -v`
      - Must pass before deploy jobs run
   b. Job `deploy-api` (on test pass):
      - Generates requirements.txt via `uv export`
      - Deploys to Azure App Service `training-bot-api`
   c. Job `deploy-frontend` (on test pass):
      - Generates requirements.txt via `uv export`
      - Deploys to Azure App Service `training-bot-frontend`
3. Monitor the Actions run at:
   https://github.com/kylodeng/Insurance-Training-Bot-main/actions
4. Verify health checks (§2) pass on the production URL.
5. If the vector store is not persisted, run POST /ingest on the new deployment.
```

### Manual / Emergency Deployment

```bash
# 1. Install Azure CLI (if not already)
az login

# 2. Package the application
uv export --no-dev --format requirements-txt -o requirements.txt
zip -r deploy.zip . -x "*.git*" -x "__pycache__/*" -x "*.pyc"

# 3. Deploy API
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path deploy.zip \
  --type zip

# 4. Deploy Frontend [TODO: confirm if frontend is also Python/FastAPI or a separate static/Vite build]
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path deploy.zip \
  --type zip

# 5. Verify
curl https://training-bot-api.azurewebsites.net/docs
```

### Rollback Steps

```bash
# Option A — Revert via Git (triggers CI/CD redeploy)
git revert HEAD           # creates a revert commit
git push origin main      # triggers the workflow

# Option B — Azure deployment slot swap (if slots are configured)
# [TODO: confirm if staging/production slots are in use]
az webapp deployment slot swap \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --slot staging \
  --target-slot production

# Option C — Redeploy a specific Git SHA manually
git checkout <previous-sha>
# Follow manual deployment steps above

# Option D — Azure App Service deployment history
az webapp deployment list \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api
# Then restore from a previous deployment in the Azure Portal:
# App Service → Deployment Center → Deployment History → Redeploy
```

> **After any rollback:** Verify the vector store is intact. If the rollback changes the chunker or embedding model version, a full re-ingest (`POST /ingest`) may be required to avoid embedding dimension mismatches.

---

## 5. Monitoring & Alerting

### Required GitHub Secrets

Ensure these are set before any workflow runs:

| Secret | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | AI delivery workflow scripts (tools 1–5) |
| `GH_TOKEN` | GitHub API operations in delivery scripts |
| `SENDGRID_API_KEY` | Email notifications from delivery scripts |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | `deploy-api` job |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | `deploy-frontend` job |

### Key Metrics to Watch

| Metric | Where | Alert Threshold |
|---|---|---|
| App Service HTTP 5xx rate | Azure Monitor → App Service → Metrics | > 5 errors/min |
| App Service response time (p95) | Azure Monitor | > 30s (LLM streaming expected to be slow) |
| App Service memory usage | Azure Monitor | > 80% of plan limit |
| App Service CPU | Azure Monitor | > 90% sustained > 5 min |
| App Service instance restarts | Azure Monitor / Activity Log | Any unexpected restart |
| GitHub Actions workflow failures | GitHub → Actions tab | Any `test` or `deploy` job failure |
| Vector store chunk count at startup | App startup log | 0 chunks = critical |

### Key Log Statements to Monitor

```
# Healthy startup
INFO:     Vector store loaded (N products)
INFO:     Application startup complete.

# Warnings requiring attention
WARNING:  No vector store found — run POST /ingest first.
WARNING:  annotation failed for <file> — using raw chunker

# Errors requiring immediate action
ERROR:    [any LLM API error / connection refused]
ERROR:    [any unhandled exception in /chat or /ingest endpoint]
```

### Where to Find Logs

```bash
# Azure App Service live log stream
az webapp log tail \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api

# GitHub Actions run logs
# https://github.com/kylodeng/Insurance-Training-Bot-main/actions

# Download historical logs from Azure
az webapp log download \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --log-file logs.zip
```

### Alerting Configuration

[TODO: Are Azure Monitor alert rules configured? If not, create alerts for 5xx rate and memory pressure on both App Services.]

[TODO: Is there an on-call rotation or PagerDuty/Opsgenie integration?]

---

## 6. Escalation Path

| Level | Who | When to escalate | Contact |
|---|---|---|---|
| L1 — First responder | On-call engineer | Service unreachable, 5xx spike, vector store missing | [TODO: fill in on-call contact / Slack channel] |
| L2 — Application owner | [TODO: fill in] | LLM API issues, data quality problems, assessment scoring failures | [TODO: fill in] |
| L3 — Platform / Azure | [TODO: fill in] | Azure App Service outage, deployment slot issues, storage failures | [TODO: fill in Azure subscription owner contact] |
| LLM Provider | OpenRouter / Anthropic support | API key issues, model deprecation, quota exceeded | https://openrouter.ai — [TODO: confirm primary LLM provider for production] |
| Repository owner | kylo.deng@capco.com | GitHub Actions secrets, output repo permissions, AI delivery tooling | kylo.deng@capco.com |

---

## 7. Useful Commands

### Health & Status

```bash
# Check API is up
curl -s https://training-bot-api.azurewebsites.net/docs | head -20

# List sessions
curl https://training-bot-api.azurewebsites.net/sessions

# Check App Service state
az webapp show \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --query "state"
```

### Vector Store / Ingestion

```bash
# Trigger PDF ingestion (rebuilds vector store from data/Insurance-product-info/)
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Run ingestion locally (useful for large PDF sets that time out on Azure)
cd Insurance-Training-Bot-main
cp .env.example .env   # fill in API_KEY etc.
uv run python core/ingest.py --pdf-dir data/Insurance-product-info/ --verbose

# Check ingest log output locally
uv run python -c "
import logging; logging.basicConfig(level=logging.INFO)
from core.ingest import ingest_directory
from core.vector_store import get_vector_store
from pathlib import Path
store = get_vector_store()
chunks = ingest_directory(Path('data/Insurance-product-info/'), verbose=True)
print(f'Total chunks: {len(chunks)}')
"
```

### Local Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Start API server locally
uv run uvicorn api.main:app --reload --port 8000

# Check environment variables loaded
uv run python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(os.getenv('OPENAI_MODEL'))"
```

### Azure Operations

```bash
# Restart App Service (clears in-memory state — vector store will reload from disk)
az webapp restart \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api

# Stream live logs
az webapp log tail \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api

# View App Service configuration (env vars)
az webapp config appsettings list \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --output table

# Set an environment variable on Azure
az webapp config appsettings set \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --settings API_KEY="<new-key>"

# Scale up App Service plan (if OOM)
az appservice plan update \
  --resource-group <RESOURCE_GROUP> \
  --name <APP_SERVICE_PLAN_NAME> \
  --sku P2V3   # [TODO: confirm current SKU and available options]
```

### GitHub Actions — Manual Workflow Triggers

```bash
# Trigger code review on the repo (Tool 1)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Trigger tech documentation regeneration (Tool 2)
gh workflow run tool2_tech_docs.yml

# Trigger UAT pack generation (Tool 5)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0

# Check last workflow run status
gh run list --workflow=deploy.yml --limit=5