# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based AI application that helps insurance agents in Hong Kong master product knowledge and sales techniques through two interaction modes: a **Teacher mode** (ongoing streamed chat with a LangGraph agent) and a **Roleplay/Assessment mode** (one-shot simulation against a generated customer profile followed by automated scoring). The backend exposes a REST API served on Azure App Service (`training-bot-api`), backed by a vector store (ChromaDB or FAISS, with optional Pinecone) that is populated by ingesting insurance product PDF brochures from the `data/Insurance-product-info/` directory. A separate frontend app service (`training-bot-frontend`) hosts the Chainlit UI. LLM inference is routed through OpenRouter (or an Anthropic-compatible endpoint) using a configured model (default: `openai/gpt-oss-20b:free`). Session state is persisted to `data/sessions.json` and survives restarts. CI/CD runs via GitHub Actions on Python 3.13, using `uv` for dependency management, and deploys to Azure on every push to `main`.

---

## 2. Health Checks

Run these checks in order to confirm the service is fully operational.

### 2.1 Azure App Service — API

```bash
# Basic HTTP probe — expect 200 and JSON body
curl -s https://training-bot-api.azurewebsites.net/

# Readiness — vector store must be loaded
curl -s https://training-bot-api.azurewebsites.net/health
# [TODO: does a /health endpoint exist? Add one if not.]
```

| Check | Expected result |
|---|---|
| HTTP GET `/` | `200 OK`, JSON response |
| Azure Portal → App Service → Overview | Status = **Running** |
| Azure Portal → App Service → Log stream | No `ERROR` or `CRITICAL` entries at startup |
| Vector store loaded | Log line: `Vector store loaded (N products)` at startup |
| Sessions file readable | Log line: sessions loaded (no `FileNotFoundError`) |

### 2.2 Azure App Service — Frontend

```bash
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
# Expect: 200
```

### 2.3 Ingest endpoint (after PDF changes)

```bash
curl -s -X POST https://training-bot-api.azurewebsites.net/ingest
# Expect: 200 and a chunk count in response
# [TODO: confirm exact /ingest endpoint path and payload]
```

### 2.4 LLM connectivity

```bash
# Create a session and send a teacher message — expect a streamed response
curl -s -X POST https://training-bot-api.azurewebsites.net/sessions \
  -H "Content-Type: application/json" \
  -d '{"mode": "teacher"}'
# [TODO: confirm exact session creation endpoint and payload schema]
```

### 2.5 Local smoke test

```bash
cd Insurance-Training-Bot-main
uv run uvicorn api.main:app --reload --port 8000
curl http://localhost:8000/
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all requests; startup log shows `No vector store found` | Vector store was never built or the `data/` directory is missing/empty | 1. Confirm PDFs exist in `data/Insurance-product-info/`. 2. POST to `/ingest` or run `python core/ingest.py --pdf-dir data/Insurance-product-info`. 3. Restart the app service. |
| Streaming chat hangs or returns empty response | `API_KEY` / `OPENAI_URL_BASE` env var missing, wrong, or quota exhausted | 1. Check App Service → Configuration → Application Settings for `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`. 2. Verify key validity against OpenRouter dashboard. 3. Rotate key and redeploy if exhausted. |
| LLM returns generic/wrong product details; citations missing | Embeddings in vector store are stale or the PDF was not re-ingested after update | 1. Add/replace PDF in `data/Insurance-product-info/`. 2. Delete the `.annot.json` sidecar if re-annotation is needed. 3. POST to `/ingest`. 4. Confirm chunk count increased in logs. |
| `sessions.json` corruption — app fails to load sessions on restart | Concurrent writes or incomplete write during shutdown | 1. SSH into App Service or use Kudu console. 2. Inspect `data/sessions.json` for invalid JSON. 3. If corrupt, rename to `sessions.json.bak` and restart (sessions lost but service recovers). 4. [TODO: implement atomic write / backup rotation] |
| GitHub Actions deploy job fails (`AZURE_WEBAPP_PUBLISH_PROFILE_API` secret missing) | Secret not set or expired in GitHub repo settings | 1. Go to GitHub repo → Settings → Secrets → Actions. 2. Download fresh publish profile from Azure Portal → App Service → Get publish profile. 3. Update the `AZURE_WEBAPP_PUBLISH_PROFILE_API` (and `_FRONTEND`) secrets. 4. Re-run the workflow. |
| `pytest` failing in CI — `ModuleNotFoundError` | Dependency not in `pyproject.toml` or `uv.lock` out of sync | 1. Run `uv sync` locally. 2. Commit updated `uv.lock`. 3. Re-run CI. |
| TLS/SSL errors in logs (`verify=False` suppresses but underlying connectivity fails) | Network egress blocked from App Service to OpenRouter or Anthropic API | 1. Check App Service outbound IP allowlist on the LLM provider side. 2. Check Azure VNET/NSG rules if applicable. 3. [TODO: remove `verify=False` and install proper certs] |
| Roleplay assessment returns empty or malformed JSON | LLM model context window exceeded during long roleplay sessions | 1. Check session message count — trim old messages if > ~20 turns. 2. Switch to a higher-context model via `OPENAI_MODEL` env var. 3. [TODO: implement message truncation strategy in agent.py] |
| Annotation (`.annot.json`) missing for new PDF | `llm` not passed to `ingest_directory`, or annotation LLM call timed out | 1. Check ingest logs for `annotation failed for <file>`. 2. Re-run ingest with `llm` configured. 3. Manually delete partial `.annot.json` and retry. |
| Frontend returns `502 Bad Gateway` | API App Service is down or cold-starting | 1. Check API app service status in Azure Portal. 2. Trigger a warm-up GET request. 3. Check deployment logs for startup errors. |
| `CORS` errors in browser console | Frontend origin not in the `allow_origins` list in `main.py` | 1. Identify the production frontend URL. 2. Add it to `allow_origins` in `api/main.py`. 3. Redeploy. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- GitHub repo secrets set: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
- `uv` installed locally (`pip install uv`)

---

### 4.1 Standard Deployment (via CI/CD — preferred)

```
Push to main branch → GitHub Actions "Test & Deploy" workflow triggers automatically
```

**Steps:**

1. Create a feature branch and open a PR against `main`.
2. Verify the `test` job passes (all `pytest` tests green).
3. Merge the PR to `main`.
4. Monitor the `deploy-api` and `deploy-frontend` jobs in GitHub Actions.
5. Confirm both jobs show ✅ green.
6. Run health checks from Section 2.

---

### 4.2 Manual Deployment (emergency / hotfix)

```bash
# 1. Clone and install
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
uv sync

# 2. Run tests locally
uv run pytest tests/ -v

# 3. Generate requirements.txt (Azure App Service needs this)
uv export --no-dev --format requirements-txt -o requirements.txt

# 4. Deploy API
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 5. Deploy Frontend
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip

# 6. Set environment variables (if not already configured)
az webapp config appsettings set \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --settings \
    API_KEY="<your-llm-api-key>" \
    OPENAI_URL_BASE="https://openrouter.ai/api/v1" \
    OPENAI_MODEL="openai/gpt-oss-20b:free" \
    SHOW_TOOL_CALLS="true"
```

---

### 4.3 Post-Deployment Verification

```bash
# 1. Check app is running
az webapp show --name training-bot-api --resource-group <RG> --query state

# 2. Tail live logs
az webapp log tail --name training-bot-api --resource-group <RG>

# 3. Run health checks from Section 2
```

---

### 4.4 Rollback Steps

**Option A — Revert via Git (preferred)**

```bash
# Identify the last good commit
git log --oneline -10

# Revert (creates a new commit, safe for main branch)
git revert <bad-commit-sha>
git push origin main
# CI/CD will automatically redeploy the reverted code
```

**Option B — Azure deployment slot swap**

```bash
# [TODO: confirm whether staging slots are configured on these App Services]
az webapp deployment slot swap \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

**Option C — Redeploy a specific git tag/release**

```bash
git checkout v<last-good-version>
uv export --no-dev --format requirements-txt -o requirements.txt
# Then follow manual deployment steps above
```

**Vector Store Rollback:**

```bash
# The vector store index is stored in data/ — back it up before ingesting
cp -r data/chroma_db data/chroma_db.bak   # [TODO: confirm actual store path]
# To rollback, restore the backup
cp -r data/chroma_db.bak data/chroma_db
# Restart the app service
az webapp restart --name training-bot-api --resource-group <RG>
```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where to find it | Alert threshold |
|---|---|---|
| HTTP 5xx error rate | Azure Portal → App Service → Metrics → HTTP Server Errors | > 5 errors / 5 min |
| Response time (P95) | Azure Portal → App Service → Metrics → Response Time | > 30 s (streaming) |
| App Service CPU % | Azure Portal → App Service → Metrics → CPU Percentage | > 80% sustained |
| App Service Memory % | Azure Portal → App Service → Metrics → Memory Working Set | > 85% |
| App Service availability | Azure Portal → App Service → Metrics → Availability | < 99% |
| GitHub Actions workflow failures | GitHub → Actions tab; or configure webhook notification | Any failure on `main` |
| LLM API quota / rate limit errors | App Service log stream; search for `429` or `RateLimitError` | Any occurrence |

### 5.2 Logs to Monitor

```bash
# Live log streaming (Azure CLI)
az webapp log tail --name training-bot-api --resource-group <RG>

# Download logs
az webapp log download --name training-bot-api --resource-group <RG> --log-file api-logs.zip
```

**Key log patterns to alert on:**

| Log pattern | Meaning |
|---|---|
| `No vector store found` | Ingest has not been run; RAG tools will return empty results |
| `annotation failed for` | A PDF was not annotated; chunks may be lower quality |
| `Vector store loaded (0 products)` | Empty index — ingest failed silently |
| `ERROR` / `CRITICAL` | Any unhandled exception |
| `rate-limit pause` | Embedding batch throttled — ingest is running slowly |
| `Could not parse Claude response as JSON` | Tool 1/2 LLM response parsing failure |

### 5.3 Alerting Setup

[TODO: Are Azure Monitor alerts configured? Set up the following if not:]

- Azure Monitor alert rule: HTTP 5xx > threshold → email/Teams notification
- Azure Monitor alert rule: App Service stopped → immediate page
- GitHub Actions: enable email notifications for failed workflows (Settings → Notifications)
- [TODO: Is Application Insights connected to the App Service? Enable it for distributed tracing.]

---

## 6. Escalation Path

| Level | Who | When to escalate | Contact |
|---|---|---|---|
| L1 — First responder | On-call engineer | Service down, health checks failing | [TODO: team Slack channel / PagerDuty rotation] |
| L2 — Application owner | Backend developer | Code-level bug, data corruption, failed deploy | [TODO: name and contact] |
| L3 — Platform / Infra | Azure admin / DevOps | App Service outage, networking, quota, billing | [TODO: name and contact] |
| L4 — LLM vendor | OpenRouter / Anthropic support | API key suspended, model unavailable, billing issue | https://openrouter.ai/docs · support@openrouter.ai |
| L5 — Business owner | Product owner | Go/no-go on rollback, data loss decisions | [TODO: name and contact] |

**Notification email (configured in workflows):** `kylo.deng@capco.com`

---

## 7. Useful Commands

### Service Management

```bash
# Restart API app service
az webapp restart --name training-bot-api --resource-group <RG>

# Restart frontend app service
az webapp restart --name training-bot-frontend --resource-group <RG>

# Check app service status
az webapp show --name training-bot-api --resource-group <RG> --query "state" -o tsv

# Stream live logs
az webapp log tail --name training-bot-api --resource-group <RG>

# List all app settings (env vars)
az webapp config appsettings list --name training-bot-api --resource-group <RG> -o table
```

### Local Development

```bash
# Install dependencies
uv sync

# Run API locally with hot-reload
uv run uvicorn api.main:app --reload --port 8000

# Run all tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Generate requirements.txt from lockfile
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Vector Store / Ingestion

```bash
# Ingest all PDFs (from repo root)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info

# Ingest with verbose chunk output
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Trigger ingest via API (POST endpoint)
curl -X POST http://localhost:8000/ingest
# [TODO: confirm endpoint exists and payload]

# Back up vector store before re-ingesting
cp -r data/chroma_db data/chroma_db.bak_$(date +%Y%m%d)
# [TODO: confirm actual vector store directory name]
```

### Session Management

```bash
# View sessions file
cat data/sessions.json | python -m json.tool | head -100