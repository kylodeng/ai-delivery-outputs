# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training environment for insurance sales agents in a Hong Kong context. It exposes two modes: a **Teacher mode**, in which a LangGraph agent coaches trainees on insurance concepts, product knowledge, and sales techniques using a Retrieval-Augmented Generation (RAG) pipeline backed by a vector store (Chroma/FAISS/Pinecone) loaded from insurance product PDFs; and a **Roleplay/Assessment mode**, in which the system simulates a realistic customer profile and subsequently scores the trainee's performance. The backend is deployed as an Azure App Service (`training-bot-api`), with a separate frontend service (`training-bot-frontend`), both deployed automatically on push to `main` via GitHub Actions. A secondary suite of GitHub Actions workflows (`tool1`–`tool5`) performs CI/CD automation including AI-assisted code review, documentation generation, auto-testing, and UAT facilitation using the Anthropic Claude API.

---

## 2. Health Checks

### 2.1 API Service

| Check | Command / URL | Expected Result |
|---|---|---|
| HTTP liveness | `GET https://training-bot-api.azurewebsites.net/` | HTTP 200 or configured root response |
| FastAPI docs endpoint | `GET https://training-bot-api.azurewebsites.net/docs` | Swagger UI loads |
| Ingest status | `GET https://training-bot-api.azurewebsites.net/ingest` | [TODO: confirm if a status endpoint exists; not found in code] |
| Vector store loaded | Check startup log for: `Vector store loaded (N products)` | N > 0 |
| Static file serving | `GET https://training-bot-api.azurewebsites.net/docs/<any-pdf-filename>` | PDF bytes returned |
| Sessions persistence | `GET https://training-bot-api.azurewebsites.net/sessions` | Returns JSON array of sessions |

### 2.2 Frontend Service

| Check | URL | Expected Result |
|---|---|---|
| Frontend loads | `https://training-bot-frontend.azurewebsites.net/` | UI renders in browser |

### 2.3 Dependency Checks

| Dependency | Check |
|---|---|
| OpenRouter / LLM API | Startup log: no `ChatOpenAI` init errors; test with a `/chat` request |
| Vector store (FAISS/Chroma/Pinecone) | Startup log: `Vector store loaded` — if absent, run `POST /ingest` |
| PDF data directory | `ls data/Insurance-product-info/` — should contain `.pdf` files |
| `sessions.json` | `data/sessions.json` exists and is valid JSON after first run |
| GitHub Actions secrets | All five secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`) must be set in repository Settings → Secrets |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log shows `No vector store found — run POST /ingest first` | Vector store index file missing or not committed; first deployment | `POST /ingest` endpoint (or run `python core/ingest.py` locally); verify `data/` directory contains PDFs; re-deploy |
| `KeyError: 'API_KEY'` or LLM auth error at startup / first request | `API_KEY` env var not set in Azure App Service configuration | Azure Portal → App Service → Configuration → Application Settings → add `API_KEY` |
| `500 Internal Server Error` on chat endpoints | LLM API unreachable, wrong `OPENAI_URL_BASE`, or model name invalid | Check `OPENAI_URL_BASE` and `OPENAI_MODEL` env vars; verify OpenRouter account credits/quota; check Azure App Service logs |
| SSL verification errors (`verify=False` in code suggests self-signed cert environment) | `httpx.Client(verify=False)` is hardcoded — likely intentional for corporate proxy but may cause warnings | [TODO: confirm if a corporate proxy or self-signed cert is in use; consider setting `verify` to a CA bundle path instead] |
| Sessions lost after restart | `data/sessions.json` not on a persistent storage mount | Azure App Service: mount a persistent storage volume to `/home/data` or equivalent; update `_SESSIONS_FILE` path via env var |
| GitHub Actions deploy job fails at `azure/webapps-deploy` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `_FRONTEND` secret expired or missing | Re-download publish profile from Azure Portal → App Service → Overview → Get publish profile; update GitHub secret |
| `tool1`–`tool5` workflows fail with `anthropic.AuthenticationError` | `ANTHROPIC_API_KEY` secret missing or rotated | Rotate key in Anthropic console; update GitHub repository secret |
| `tool1`–`tool5` fail with `SENDGRID` errors | `SENDGRID_API_KEY` missing or domain not verified | Verify SendGrid API key and sender domain (`noreply@ai-delivery.capco.com`) in SendGrid dashboard |
| Vector store returns zero results for product queries | PDFs not ingested, or ingestion ran with wrong `pdf_dir` | Re-run ingestion: `POST /ingest` or `python core/ingest.py --pdf-dir data/Insurance-product-info` |
| `list_products` tool returns empty list | Vector store loaded but no documents indexed (empty store) | Re-ingest PDFs; check logs for annotation LLM errors during ingestion |
| LLM annotation fails during ingestion (`annotation failed for X — using raw chunker`) | `OPENAI_URL_BASE` or `OPENAI_MODEL` misconfigured for annotation LLM | Check `core/ingest.py` `_build_ingest_llm()` — uses `API_KEY` and `OPENAI_URL_BASE`; ensure these are set |
| High latency or timeouts on chat endpoints | LLM API slow; large context window being sent | Check OpenRouter status page; review token limits; consider reducing `max_files` in `shared.py` |
| `pytest` tests failing in CI | Dependency mismatch or missing test fixtures | Run `uv sync` locally; check `tests/` directory for required fixtures; review test output in Actions logs |
| CORS errors in browser | Frontend origin not in `allow_origins` list in `main.py` | Add the production frontend URL to the `CORSMiddleware` `allow_origins` list and redeploy |
| `sessions.json` parse error on startup | File corrupted (e.g. partial write during crash) | Delete or back up `sessions.json`; restart service — sessions will be lost but service will recover |

---

## 4. Deployment Procedure

### Prerequisites
- Azure CLI authenticated (`az login`)
- GitHub repository secrets configured (see §2.3)
- `uv` package manager installed locally
- Python 3.13 available

### 4.1 Standard Deployment (via GitHub Actions — preferred)

1. **Ensure all tests pass locally before pushing:**
   ```bash
   uv sync
   uv run pytest tests/ -v
   ```

2. **Commit and push to `main`:**
   ```bash
   git add .
   git commit -m "feat: <description>"
   git push origin main
   ```

3. **Monitor the workflow in GitHub Actions:**
   - Navigate to `Actions` → `Test & Deploy` workflow
   - Confirm `test` job passes (green)
   - Confirm `deploy-api` and `deploy-frontend` jobs complete

4. **Verify deployment:**
   ```bash
   curl https://training-bot-api.azurewebsites.net/docs
   ```

5. **Check startup logs in Azure Portal:**
   - Portal → `training-bot-api` → Log stream
   - Confirm: `Vector store loaded (N products)`

---

### 4.2 Manual Deployment (emergency / bypass CI)

```bash
# Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

> [TODO: confirm resource group name and whether zip deploy or run-from-package is used]

---

### 4.3 First-time Ingestion

After deploying to a new environment with no existing vector store:

```bash
# Option A: via API endpoint
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Option B: locally (then commit the resulting index files)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info
```

> [TODO: confirm whether the vector store index (Chroma/FAISS) is committed to the repo or generated at runtime; if runtime, a persistent storage mount is required]

---

### 4.4 Rollback Steps

#### Rollback via GitHub Actions (recommended):
1. Identify the last known-good commit SHA from `Actions` history
2. In GitHub UI: `Actions` → `Test & Deploy` → find the last passing run → click `Re-run all jobs`

   **OR** revert the bad commit:
   ```bash
   git revert <bad-commit-sha>
   git push origin main
   ```
   This triggers the deploy workflow automatically.

#### Rollback via Azure Portal (immediate):
1. Azure Portal → `training-bot-api` → **Deployment Center** → **Deployment logs**
2. Select the previous successful deployment
3. Click **Redeploy**

   **OR** via CLI:
   ```bash
   az webapp deployment list --name training-bot-api \
     --resource-group <RESOURCE_GROUP> \
     --output table
   # Note the deploymentId of last good deploy
   az webapp deployment source sync \
     --name training-bot-api \
     --resource-group <RESOURCE_GROUP>
   ```

#### Post-rollback verification:
```bash
curl -s https://training-bot-api.azurewebsites.net/docs | grep "Insurance Agent Trainer"
```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Source | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Monitoring → Metrics | > 1% over 5 min |
| HTTP response time (P95) | Azure App Service → Metrics | > 10s (streaming endpoints may be higher) |
| CPU usage | Azure App Service → Metrics | > 80% sustained |
| Memory usage | Azure App Service → Metrics | > 85% |
| Instance count / restarts | Azure App Service → Metrics | Any unplanned restart |
| GitHub Actions workflow failure | GitHub → Actions | Any `deploy-api` or `deploy-frontend` failure |
| LLM API quota / rate limit errors | Application logs | Any `429` or `RateLimitError` in logs |

> [TODO: confirm whether Azure Application Insights is enabled on the App Service; if so, use it for distributed tracing and custom metrics]

### 5.2 Log Locations

| Log | Location |
|---|---|
| Application startup logs | Azure Portal → `training-bot-api` → Log stream |
| Application stdout/stderr | Azure Portal → `training-bot-api` → Advanced Tools (Kudu) → LogFiles |
| GitHub Actions run logs | `github.com/kylodeng/Insurance-Training-Bot-main/actions` |
| Vector store load status | Startup log: `INFO — Vector store loaded (N products)` or `WARNING — No vector store found` |
| Ingestion errors | Log lines prefixed `[ingest]` — watch for `annotation failed` warnings |
| Session persistence | `data/sessions.json` — check file size and last-modified timestamp |
| RAG tool usage | Log lines from `api/rag_tools.py` logger at `DEBUG`/`INFO` level |

### 5.3 Key Log Messages to Monitor

```
# Healthy startup
INFO:     Vector store loaded (N products)

# Problem: no data ingested
WARNING:  No vector store found — run POST /ingest first.

# LLM annotation degradation (non-fatal, uses fallback)
WARNING:  [ingest] annotation failed for <file> — using raw chunker

# Rate limiting from LLM provider
ERROR:    RateLimitError / 429

# Session file corruption
ERROR:    JSONDecodeError loading sessions.json
```

### 5.4 Alerting Configuration

> [TODO: confirm whether Azure Monitor alerts or a third-party tool (PagerDuty, OpsGenie) is used for alerting]
> [TODO: confirm whether SENDGRID email alerts from the `tool1`–`tool5` workflows are routed to an ops mailbox or only to `kylo.deng@capco.com`]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: add on-call rotation contact] | Service down, deploy failed, 5xx spike |
| L2 | Tech Lead | [TODO: add tech lead name/contact] | L1 cannot resolve within 30 min; data loss suspected |
| L3 | Cloud/Platform Owner | [TODO: add Azure subscription owner contact] | Azure service incident; quota exhaustion; billing issue |
| L4 | Security | [TODO: add security team contact] | Suspected API key leak; unauthorised access; data breach |
| External | OpenRouter / Anthropic Support | support@openrouter.ai / support@anthropic.com | LLM API outage; persistent rate limiting |
| External | Azure Support | Azure Portal → Support + troubleshooting | App Service platform issues |
| External | SendGrid Support | sendgrid.com/support | Email delivery failures |

> **Note:** Current NOTIFY_EMAIL is `kylo.deng@capco.com`. Ensure this is monitored outside business hours or set up forwarding to an ops distribution list.

---

## 7. Useful Commands

### Application Management

```bash
# Check Azure App Service status
az webapp show \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --query "state" -o tsv

# Stream live logs from Azure App Service
az webapp log tail \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP>

# Restart the API service
az webapp restart \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP>

# Restart the frontend service
az webapp restart \
  --name training-bot-frontend \
  --resource-group <RESOURCE_GROUP>
```

### Local Development

```bash
# Install dependencies
uv sync

# Run the FastAPI backend locally
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### Data Ingestion

```bash
# Ingest all PDFs into vector store (local)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info

# Ingest with verbose chunk output
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Trigger ingestion via API (production)
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Check which products are in the vector store
curl https://training-bot-api.azurewebsites.net/ingest   # [TODO: confirm status endpoint path]
```

### Session Management

```bash
# List all sessions
curl https://training-bot-api.azurewebsites.net/sessions

# Delete a specific session
curl -X DELETE https://training-bot-api.azurewebsites.net/sessions/<session_id>

# Backup sessions file (run on server or via Kudu)
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%d%H%M%S)

# Inspect sessions file locally
python -m json.tool data/sessions.json | head -100
```

### GitHub Actions —