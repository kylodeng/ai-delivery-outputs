# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based backend service that provides an AI-powered training platform for insurance sales agents in a Hong Kong context. It operates in two modes: **Teacher mode**, which delivers an ongoing interactive coaching experience via a streaming LangGraph agent backed by a RAG (Retrieval-Augmented Generation) pipeline over Sun Life insurance product PDFs; and **Roleplay/Assessment mode**, which simulates customer interactions using randomised Hong Kong customer profiles and evaluates trainee performance across multiple dimensions. The system ingests insurance product brochures and supplementary documents (hospital networks, exclusion lists, claim procedures) into a vector store (Chroma or FAISS), which is queried at runtime by eight LangChain tools exposed to the LangGraph agents. The API backend is deployed to **Azure App Service** (`training-bot-api`), with a separate frontend deployment (`training-bot-frontend`), via a GitHub Actions CI/CD pipeline that runs tests before every push to `main`.

---

## 2. Health Checks

Perform these checks in order to confirm the service is fully operational.

### 2.1 API Layer

```bash
# Basic liveness — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/

# [TODO: Is there a /health or /readiness endpoint? Add one if not.]
```

### 2.2 Vector Store

```bash
# Trigger re-ingest / confirm store is loaded (expect 200 + product count in logs)
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Check known products are available
curl https://training-bot-api.azurewebsites.net/products
# [TODO: Confirm /products endpoint exists and correct path]
```

### 2.3 LLM Connectivity

```bash
# [TODO: What OpenRouter/model endpoint is used in production? Confirm _BASE_URL value in App Service config]
# Confirm API_KEY is set:
az webapp config appsettings list \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --query "[?name=='API_KEY']"
```

### 2.4 Session Persistence

```bash
# Sessions file should exist and be non-empty after at least one session
# On the App Service container:
cat /home/site/wwwroot/data/sessions.json | python3 -m json.tool | head -20
# [TODO: Confirm persistent storage is mounted for /data — Azure App Service ephemeral disk will lose sessions.json on restart without a mount]
```

### 2.5 Static Document Serving

```bash
# PDF files served under /docs — spot-check one file returns 200
curl -I https://training-bot-api.azurewebsites.net/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
```

### 2.6 Frontend

```bash
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
# Expect 200
```

### 2.7 CI/CD Pipeline

- Navigate to **GitHub → Actions → Test & Deploy** and confirm the latest `main` push run is green.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on any chat request | Vector store not loaded — `No vector store found` in logs | POST `/ingest` to rebuild the index; check that PDF files exist under `data/Insurance-product-info/`; verify `API_KEY` and `OPENAI_URL_BASE` env vars are set correctly |
| `KeyError: 'API_KEY'` or `401 Unauthorized` from LLM | Missing or invalid `API_KEY` / `OPENAI_URL_BASE` environment variable in App Service | `az webapp config appsettings set --name training-bot-api --resource-group <RG> --settings API_KEY=<key>`; redeploy |
| Streaming responses stop mid-sentence or connection drops | Azure App Service idle timeout (default 230 s) cutting long LangGraph streams | Increase `WEBSITE_HTTPLOGGING_RETENTION_DAYS`; set App Service `connectionTimeout` / `requestTimeout`; consider chunked keep-alive pings; [TODO: confirm timeout config] |
| `sessions.json` reset to empty after App Service restart | Ephemeral local disk — no persistent mount configured | Mount an Azure Files share to `/home/site/wwwroot/data` via App Service storage config, or move session persistence to Azure Blob / Cosmos DB |
| RAG tools return empty results / wrong product | Vector store stale or ingest failed partway | Re-run `/ingest`; check `data/Insurance-product-info/` for new/renamed PDFs; inspect annotator output in `*.annot.json` sidecar files |
| `SSLError: certificate verify failed` | `httpx.Client(verify=False)` hardcoded — this suppresses errors but may mask proxy/cert issues in some environments | [TODO: Determine if TLS verification should be re-enabled in production; update `_get_llm()` accordingly] |
| LLM annotation fails during ingest | LLM API rate limit or connectivity issue | Ingest retries are not automatic — re-run `/ingest`; check `OPENAI_URL_BASE` and `API_KEY`; raw heuristic chunker fallback activates automatically per `ingest.py` |
| GitHub Actions `Test & Deploy` fails at test step | Python dependency mismatch or test regression | Check Actions logs; run `uv run pytest tests/ -v` locally; fix failing tests before merging to `main` |
| Deploy step succeeds but service returns old code | Azure App Service slot not swapped / deploy artefact cached | Force restart: `az webapp restart --name training-bot-api --resource-group <RG>`; [TODO: Confirm if staging slots are used] |
| Frontend loads but API calls fail (CORS) | Origin not in `allow_origins` list in `main.py` | Add the production frontend URL to `CORSMiddleware` `allow_origins` list and redeploy; [TODO: Production frontend URL is not in the current CORS list] |
| `ModuleNotFoundError` on startup | `requirements.txt` generated from `uv export` missing a dependency | Run `uv sync` locally; verify `uv export --no-dev` captures all runtime deps; redeploy |
| Citation links (`/docs/...`) return 404 | PDF file missing from `data/` directory or path mismatch | Confirm file exists; check `_to_docs_path()` path conversion; ensure `StaticFiles` mount covers the correct directory |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI installed and authenticated (`az login`)
- GitHub repository secrets configured:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL` set in App Service config
- `uv` installed locally
- Python 3.13 (matches CI)

---

### 4.1 Standard Deployment (via CI/CD — preferred)

```
1. Create a feature branch and open a pull request to `main`.
2. GitHub Actions runs the `test` job automatically.
   - All pytest tests in tests/ must pass.
3. On PR approval and merge to `main`:
   - `deploy-api` job runs: generates requirements.txt via `uv export` and deploys
     to Azure App Service `training-bot-api`.
   - `deploy-frontend` job runs: deploys to `training-bot-frontend`.
4. Confirm deployment success in GitHub Actions run logs.
5. Run health checks (Section 2) against production.
```

### 4.2 Manual / Emergency Deployment

```bash
# Step 1 — Checkout and install
git checkout main
git pull origin main
uv sync

# Step 2 — Run tests locally
uv run pytest tests/ -v

# Step 3 — Generate requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# Step 4 — Deploy API manually via Azure CLI
az webapp deployment source config-zip \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --src <path-to-zip>
# [TODO: Confirm whether zip deploy or publish-profile deploy is used for manual path]

# Step 5 — Deploy Frontend manually
az webapp deployment source config-zip \
  --name training-bot-frontend \
  --resource-group <RESOURCE_GROUP> \
  --src <path-to-zip>

# Step 6 — Restart services
az webapp restart --name training-bot-api --resource-group <RESOURCE_GROUP>
az webapp restart --name training-bot-frontend --resource-group <RESOURCE_GROUP>

# Step 7 — Run health checks
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/
```

### 4.3 Vector Store Re-ingestion (run after deploying new PDFs)

```bash
# Trigger ingest via API
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Monitor logs during ingest
az webapp log tail --name training-bot-api --resource-group <RESOURCE_GROUP>
```

### 4.4 Rollback Steps

```bash
# Option A — Revert via Git and re-run CI
git revert <commit-hash>
git push origin main
# Wait for CI/CD to complete, then re-run health checks

# Option B — Roll back to a previous Azure deployment slot
# [TODO: Confirm whether deployment slots (staging/production swap) are configured]
az webapp deployment list --name training-bot-api --resource-group <RESOURCE_GROUP>
az webapp deployment source config-zip \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --src <previous-known-good-zip>

# Option C — Restart with previous image if container-based
# [TODO: Is this deployed as a container or as a code deployment?]
az webapp restart --name training-bot-api --resource-group <RESOURCE_GROUP>
```

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

```bash
# Stream live logs from API
az webapp log tail --name training-bot-api --resource-group <RESOURCE_GROUP>

# Stream live logs from frontend
az webapp log tail --name training-bot-frontend --resource-group <RESOURCE_GROUP>
```

**Key log patterns to watch:**

| Log Pattern | Meaning | Action |
|---|---|---|
| `Vector store loaded (N products)` | Successful startup | Confirm N matches expected product count |
| `No vector store found — run POST /ingest first` | Missing index on startup | POST `/ingest` immediately |
| `[ingest] annotation failed for <file>` | LLM annotation error during ingest | Check API key / LLM connectivity; raw chunker fallback activates |
| `[ingest] index saved (N chunks)` | Successful ingest completion | Verify N is reasonable (thousands of chunks expected) |
| `WARNING` or `ERROR` from FastAPI/uvicorn | Runtime errors | Investigate stack trace in full log |

### 5.2 Metrics to Monitor

| Metric | Tool | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure Monitor / App Insights | > 1% of requests over 5 min |
| HTTP response time (p95) | Azure Monitor | > 30 s (streaming expected to be long; tune threshold) |
| App Service memory usage | Azure Monitor | > 80% of plan limit |
| App Service CPU usage | Azure Monitor | > 85% sustained for 5 min |
| LLM API error rate | Azure Monitor (custom log metric) | Any sustained errors |
| `/ingest` last run time | [TODO: No scheduled ingest trigger found — add monitoring for stale vector store] | > 7 days since last ingest if PDFs are updated regularly |
| GitHub Actions workflow failures | GitHub → Actions | Any `Test & Deploy` failure on `main` |

### 5.3 Recommended Azure Application Insights Setup

```bash
# Enable Application Insights on API App Service
az monitor app-insights component create \
  --app insurance-training-bot-insights \
  --location <REGION> \
  --resource-group <RESOURCE_GROUP>

# [TODO: Application Insights is not currently configured in the codebase — add instrumentation key to App Service settings]
```

### 5.4 GitHub Actions Monitoring

- Monitor the **Test & Deploy**, **Tool 1–5** workflow runs in GitHub Actions.
- Set up GitHub notifications or Slack/Teams webhook on workflow failures.
- [TODO: No alerting webhook configured in current workflows — add `on: failure` notification step]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call contact / PagerDuty rotation] | Service unavailable, 5xx error rate spike, ingest failure |
| L2 | Backend / ML Lead | [TODO: fill in contact] | RAG quality issues, LangGraph agent errors, LLM API outage |
| L3 | Azure Platform / Cloud Ops | [TODO: fill in Azure subscription owner contact] | App Service outage, storage failure, networking issues |
| L4 | LLM Provider Support | OpenRouter support / Anthropic support (depending on `OPENAI_URL_BASE` config) | LLM API sustained outage or degraded performance |
| Business Owner | [TODO: fill in — kylo.deng@capco.com is the notification email but confirm accountability] | [TODO] | Data breach, compliance issue, sustained service outage > 1 hour |

**Notify:** `kylo.deng@capco.com` (current `NOTIFY_EMAIL` in workflows) for all L2+ incidents.

---

## 7. Useful Commands

### Service Management

```bash
# Restart API
az webapp restart --name training-bot-api --resource-group <RESOURCE_GROUP>

# Restart Frontend
az webapp restart --name training-bot-frontend --resource-group <RESOURCE_GROUP>

# Stop API (emergency)
az webapp stop --name training-bot-api --resource-group <RESOURCE_GROUP>

# Start API
az webapp start --name training-bot-api --resource-group <RESOURCE_GROUP>
```

### Logs

```bash
# Stream live logs
az webapp log tail --name training-bot-api --resource-group <RESOURCE_GROUP>

# Download log archive
az webapp log download \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --log-file training-bot-api-logs.zip
```

### Environment Variables

```bash
# List all App Service settings
az webapp config appsettings list \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP>

# Set / update a setting
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --settings API_KEY=<new_key>

# Delete a setting
az webapp config appsettings delete \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --setting-names API_KEY
```

### Vector Store / Ingest

```bash
# Trigger full re-ingest via API
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Run ingest locally (requires .env with API_KEY, OPENAI_URL_BASE)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Check annotation sidecar files exist
ls data/Insurance-product-info/**/*.annot.json
```

### Local Development

```bash
# Install dependencies
uv sync

# Run locally
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_rag_tools.py -v

# Generate requirements for deployment
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Session Management

```bash
# Inspect current sessions (on server or locally)
cat data/sessions.json | python3 -m json.tool