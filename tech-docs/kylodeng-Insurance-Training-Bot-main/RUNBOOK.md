# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training platform for insurance sales agents, deployed on **Azure App Service** (two separate slots: `training-bot-api` and `training-bot-frontend`). The backend exposes a REST/streaming API that drives two modes of interaction: a **Teacher mode** (ongoing streamed chat using a LangGraph agent backed by RAG tools over a local vector store of insurance product PDFs) and an **Assessor mode** (one-shot evaluation of a completed roleplay session). Insurance product knowledge is ingested from PDF brochures and supplementary documents stored under `data/Insurance-product-info/`, chunked, embedded via a configurable embedding model, and persisted in a local vector store (FAISS or Chroma, configured via environment). The LLM backend is accessed via an OpenRouter-compatible endpoint (defaulting to `openrouter.ai/api/v1`) using a `ChatOpenAI`-compatible client. CI/CD is handled by GitHub Actions: tests run on every PR and push, and deployments to Azure fire automatically on merge to `main`.

---

## 2. Health Checks

Run these checks in order to confirm the service is fully operational.

### 2.1 API Process

```bash
# Check the Azure App Service is running (replace with your resource group)
az webapp show \
  --name training-bot-api \
  --resource-group <resource-group> \
  --query "state" -o tsv
# Expected output: Running
```

### 2.2 HTTP Liveness

```bash
# Root / docs endpoint — FastAPI auto-generated OpenAPI UI
curl -sf https://<training-bot-api>.azurewebsites.net/docs | head -5
# Expected: HTML content (200 OK)

# Direct health probe (no dedicated /health endpoint found — use OpenAPI JSON)
curl -sf https://<training-bot-api>.azurewebsites.net/openapi.json | python3 -m json.tool | head -10
# Expected: valid JSON with "title": "Insurance Agent Trainer"
```

> [TODO: Is there a dedicated `/health` or `/ping` endpoint? If not, recommend adding one.]

### 2.3 Vector Store

```bash
# POST /ingest should return 200 if vector store loads correctly.
# On startup the app logs one of:
#   INFO  Vector store loaded (N products)
#   WARNING  No vector store found — run POST /ingest first.
# Check the App Service log stream for this message:
az webapp log tail --name training-bot-api --resource-group <resource-group>
```

### 2.4 LLM Connectivity

```bash
# Send a minimal chat request; a non-5xx response confirms LLM reachability
curl -X POST https://<training-bot-api>.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"healthcheck","message":"ping"}' \
  --max-time 30
# Expected: streaming SSE response or JSON, HTTP 200
```

> [TODO: Confirm exact chat endpoint path — not fully visible in provided files.]

### 2.5 Frontend

```bash
curl -sf https://<training-bot-frontend>.azurewebsites.net/ | head -5
# Expected: HTML 200 OK
```

### 2.6 GitHub Actions

- Navigate to **Actions** tab → `Test & Deploy` workflow.
- Last run on `main` should show ✅ green.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `503` or Azure portal shows `Stopped` | App Service plan exhausted, crash loop, or failed deployment | 1. `az webapp restart --name training-bot-api --resource-group <rg>` 2. Check log stream for Python traceback 3. If OOM, scale up App Service plan tier |
| Startup log: `WARNING No vector store found` | `data/` directory missing from deployment package or `POST /ingest` never run | 1. SSH into App Service: `az webapp ssh` 2. Confirm `data/` exists 3. `curl -X POST .../ingest` to rebuild index |
| All chat responses are empty or `500` | `API_KEY` / `OPENAI_URL_BASE` env vars missing or invalid | 1. `az webapp config appsettings list --name training-bot-api` 2. Verify `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL` are set 3. Test LLM endpoint directly with `curl` |
| Chat responses stall / timeout after ~30 s | LLM provider rate limit or network timeout to OpenRouter | 1. Check OpenRouter status page 2. Review App Service logs for `httpx` timeout errors 3. Retry; if persistent, switch `OPENAI_URL_BASE` to fallback provider |
| `SSL: CERTIFICATE_VERIFY_FAILED` in logs | `verify=False` suppressed but upstream proxy intercepts TLS | Expected: `verify=False` is hard-coded. If errors appear, check proxy configuration on Azure outbound networking |
| Sessions lost after restart | `sessions.json` stored on ephemeral local disk | 1. Confirm `data/sessions.json` is in a persistent volume or Azure Files mount 2. [TODO: Is Azure Files mounted for the App Service?] |
| GitHub Actions deploy fails: `AZURE_WEBAPP_PUBLISH_PROFILE_*` secret error | Publish profile secret expired or missing | 1. Download fresh publish profile from Azure portal 2. Update `AZURE_WEBAPP_PUBLISH_PROFILE_API` / `_FRONTEND` secrets in GitHub repo settings |
| Tests fail in CI (`uv run pytest`) | Dependency conflict or missing env vars in CI | 1. Check Actions log for specific test failure 2. `uv sync` locally to reproduce 3. Pin conflicting dependency in `pyproject.toml` |
| PDF ingestion produces 0 chunks | PDF is image-only / corrupted, or `pdfplumber` extraction returns empty text | 1. Open PDF manually to confirm it has selectable text 2. Check `[ingest]` log lines for the specific file 3. Remove or replace the corrupted PDF and re-run `/ingest` |
| LLM annotation fails during ingest | LLM API unreachable or returns non-JSON for a document | Annotator falls back to raw heuristic chunker automatically (see `ingest.py`). Verify log: `annotation failed for X — using raw chunker`. If persistent, check LLM connectivity. |
| `KeyError: ANTHROPIC_API_KEY` in GitHub Actions | Secret not set in repo | Add `ANTHROPIC_API_KEY` under **Settings → Secrets → Actions** |
| Citation markers `[[Sn]]` not appearing in teacher responses | `reset_sources()` not called before the request, or contextvar scope issue | 1. Check `rag_tools.py` `_sources_ctx` initialisation 2. Ensure `reset_sources()` is called at the start of each teacher-mode request handler |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated: `az login`
- GitHub repository secrets set: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`
- `uv` installed locally: `pip install uv`

---

### 4.1 Standard Deployment (via GitHub Actions — recommended)

```
1. Merge your feature branch PR into `main`.
   → GitHub Actions automatically runs the "Test & Deploy" workflow.

2. Monitor the workflow:
   GitHub UI → Actions → "Test & Deploy" → latest run

3. The pipeline runs in this order:
   a. job: test        — pytest suite on Python 3.13
   b. job: deploy-api  — deploys to Azure App Service `training-bot-api`
   c. job: deploy-frontend — deploys to Azure App Service `training-bot-frontend`

4. Confirm both deploy jobs show ✅.

5. Run health checks (Section 2) against production URL.
```

### 4.2 Manual / Emergency Deployment

```bash
# Step 1 — Install dependencies and generate requirements.txt
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt

# Step 2 — Deploy API manually
az webapp deploy \
  --name training-bot-api \
  --resource-group <resource-group> \
  --src-path . \
  --type zip

# Step 3 — Deploy Frontend manually
az webapp deploy \
  --name training-bot-frontend \
  --resource-group <resource-group> \
  --src-path . \
  --type zip

# Step 4 — Verify
az webapp show --name training-bot-api --resource-group <rg> --query state
```

### 4.3 First-Time Ingest (after fresh deployment)

```bash
# Trigger PDF ingestion to build the vector store
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest
# Wait for completion — may take several minutes depending on PDF count
# Confirm log: "index saved (N chunks)"
```

> [TODO: Confirm the `/ingest` endpoint path and whether it requires authentication.]

---

### 4.4 Rollback Steps

```bash
# Option A — Revert via GitHub (preferred)
# 1. Identify the last good commit SHA
git log --oneline -10

# 2. Create a revert commit
git revert <bad-commit-sha>
git push origin main
# → Triggers CI/CD pipeline automatically

# Option B — Azure deployment slot swap (if slots are configured)
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group <resource-group> \
  --slot staging \
  --target-slot production

# Option C — Re-deploy a specific Git tag directly
git checkout <last-known-good-tag>
uv export --no-dev --format requirements-txt -o requirements.txt
az webapp deploy --name training-bot-api --resource-group <rg> --src-path . --type zip
```

> [TODO: Are staging/deployment slots configured on the Azure App Services?]

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Monitoring → Metrics → `Http5xx` | > 5 errors/min |
| HTTP response time (P95) | Azure App Service → `HttpResponseTime` | > 10 s (streaming) |
| CPU percentage | App Service Plan → `CpuPercentage` | > 80% sustained 5 min |
| Memory percentage | App Service Plan → `MemoryPercentage` | > 85% |
| Instance availability | App Service → `Availability` | < 99% |
| LLM API error rate | App Service log stream — search `httpx` / `ChatOpenAI` errors | Any sustained errors |
| Vector store load | App Service log stream — `WARNING No vector store` | Any occurrence |
| GitHub Actions pipeline | GitHub Actions → workflow run status | Any failure on `main` |

### 5.2 Log Locations

```bash
# Live log stream from Azure App Service
az webapp log tail \
  --name training-bot-api \
  --resource-group <resource-group>

# Download recent logs
az webapp log download \
  --name training-bot-api \
  --resource-group <resource-group> \
  --log-file ./api-logs.zip

# Application-level logs — Python logging at INFO level
# Key log prefixes to filter:
#   [ingest]   — PDF ingestion pipeline
#   WARNING    — Vector store missing, annotation failures
#   ERROR      — Unhandled exceptions
```

### 5.3 Alerting Setup

> [TODO: Are Azure Monitor Alert Rules configured? Recommend setting up alerts for `Http5xx > 5` and `Availability < 99%`.]

> [TODO: Is there an on-call PagerDuty/Opsgenie integration?]

### 5.4 Session Persistence Monitoring

```bash
# Check sessions.json exists and is non-empty
az webapp ssh --name training-bot-api --resource-group <rg>
# Inside the SSH session:
ls -lh /home/site/wwwroot/data/sessions.json
wc -l /home/site/wwwroot/data/sessions.json
```

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: name / Slack handle / phone] | Service down, health checks failing |
| L2 | Backend lead | [TODO: name / contact] | LLM integration failures, data ingestion issues, code bugs |
| L3 | Cloud/infra lead | [TODO: name / contact] | Azure App Service platform issues, networking, secrets |
| L4 | Product owner | [TODO: name / contact] | Business impact, data loss, security incident |
| External | Azure Support | [TODO: Azure support plan tier and case portal URL] | Azure platform outages |
| External | OpenRouter / LLM provider | [TODO: provider support URL] | LLM API outages |

---

## 7. Useful Commands

### Azure App Service

```bash
# Check app status
az webapp show --name training-bot-api --resource-group <rg> --query "{state:state,hostName:defaultHostName}" -o table

# Restart the API
az webapp restart --name training-bot-api --resource-group <rg>

# Restart the frontend
az webapp restart --name training-bot-frontend --resource-group <rg>

# Stream live logs
az webapp log tail --name training-bot-api --resource-group <rg>

# List all app settings (env vars)
az webapp config appsettings list --name training-bot-api --resource-group <rg> -o table

# Set / update an environment variable
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg> \
  --settings API_KEY="<new-value>"

# SSH into the running container
az webapp ssh --name training-bot-api --resource-group <rg>

# Scale up App Service plan
az appservice plan update --name <plan-name> --resource-group <rg> --sku P2V3
```

### Local Development

```bash
# Install all dependencies
uv sync

# Run the FastAPI server locally (default port 8000)
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_rag_tools.py -v

# Ingest PDFs into local vector store
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Export requirements (for Azure deploy)
uv export --no-dev --format requirements-txt -o requirements.txt

# Check installed packages
uv pip list
```

### Vector Store & Ingestion

```bash
# Trigger ingestion via API (production)
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest \
  -H "Content-Type: application/json"

# Check known products in vector store (teacher sanity check)
curl https://<training-bot-api>.azurewebsites.net/products
# [TODO: confirm this endpoint exists]

# Run ingestion locally with verbose chunking output
uv run python core/ingest.py --verbose --pdf-dir data/Insurance-product-info
```

### GitHub Actions

```bash
# Manually trigger tech-docs generation
gh workflow run tool2_tech_docs.yml --ref main

# Manually trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  --ref main \
  -f review_mode=pr \
  -f pr_number=42

# Manually trigger UAT test pack generation
gh workflow run tool5_uat.yml \
  --ref main \
  -f uat_mode=generate \
  -f release_version=1.0.0

# List recent workflow runs
gh run list --workflow=deploy.yml --limit 10

# Watch a specific run
gh run watch <run-id>