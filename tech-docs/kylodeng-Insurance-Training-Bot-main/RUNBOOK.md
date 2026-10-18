# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based conversational AI application designed to help new insurance agents in Hong Kong master sales techniques, product knowledge, and customer handling. It operates in two modes: **Teacher mode**, which delivers an ongoing coaching experience via a streamed LangGraph agent, and **Roleplay/Assessment mode**, which simulates a customer interaction and then runs a one-shot assessor agent to evaluate the trainee's performance across multiple dimensions. The backend is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life insurance product PDFs into a vector store (ChromaDB or FAISS, with optional Pinecone); agent tool calls retrieve grounded, citation-linked answers from that store. The application is deployed on **Azure App Service** (API on `training-bot-api`, frontend on `training-bot-frontend`) via GitHub Actions CI/CD, using an OpenRouter-compatible LLM endpoint (defaulting to GPT or Claude Sonnet). Sessions are persisted to a local `data/sessions.json` file.

---

## 2. Health Checks

Perform the following checks to confirm the service is fully operational:

### 2.1 API Service

```bash
# Basic liveness — expects HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<training-bot-api>.azurewebsites.net/

# FastAPI auto-docs (confirms app started and routes registered)
curl -s https://<training-bot-api>.azurewebsites.net/docs | grep "Insurance Agent Trainer"
```

> [TODO: What is the exact Azure App Service hostname for the API?]

### 2.2 Vector Store

```bash
# Trigger ingestion status check — should return known products list
curl -s -X GET https://<training-bot-api>.azurewebsites.net/ingest
```

At startup, the app logs:
- `Vector store loaded (N products)` — healthy
- `No vector store found — run POST /ingest first` — **action required**

Check Azure App Service log stream for this message within 30 seconds of a cold start.

### 2.3 LLM Connectivity

```bash
# Send a minimal teacher-mode message; expect a streamed response (HTTP 200 with content)
curl -s -X POST https://<training-bot-api>.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"health-check","message":"Hello"}'
```

> [TODO: Confirm the exact chat endpoint path and request schema — `/chat` is inferred from the FastAPI + Chainlit pattern but not fully visible in the provided code.]

### 2.4 Frontend

```bash
# Expect HTTP 200 from the Chainlit or Vite frontend
curl -s -o /dev/null -w "%{http_code}" https://<training-bot-frontend>.azurewebsites.net/
```

### 2.5 Sessions Persistence

```bash
# On the App Service Kudu console or SSH:
cat /home/site/wwwroot/data/sessions.json | python3 -m json.tool | head -20
```

Expect a valid JSON array/object. An empty `{}` or `[]` is acceptable on a fresh instance.

### 2.6 Static Document Serving

```bash
# PDF files served at /docs — test one known document
curl -s -o /dev/null -w "%{http_code}" \
  https://<training-bot-api>.azurewebsites.net/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
```

Expect HTTP 200. HTTP 404 indicates the `data/` directory is not mounted or deployed correctly.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `No vector store found — run POST /ingest first` in logs on startup | Vector store index files (`chroma/` or `faiss_index/`) are absent from the deployment package or were wiped by an App Service restart | SSH into Kudu console; verify `data/` directory exists. Re-trigger ingestion: `POST /ingest`. For persistent storage, mount an Azure File Share to the App Service. |
| LLM calls return `401 Unauthorized` or `403 Forbidden` | `API_KEY` environment variable is missing, expired, or the OpenRouter/Anthropic key has been revoked | Check App Service → Configuration → Application Settings for `API_KEY`. Rotate the key in OpenRouter/Anthropic dashboard and update the secret. |
| Agent hangs / no streamed tokens returned | LLM provider timeout, SSL verification disabled (`verify=False`) causing proxy issues, or model name `OPENAI_MODEL` pointing to a non-existent model | Check App Service log stream for HTTP errors from `httpx`. Verify `OPENAI_URL_BASE` and `OPENAI_MODEL` env vars. Test LLM endpoint directly with `curl`. |
| `SSL: CERTIFICATE_VERIFY_FAILED` or similar httpx errors | `verify=False` is set but a corporate proxy is intercepting TLS; or a certificate bundle is missing | Set `REQUESTS_CA_BUNDLE` or inject the correct CA bundle. Review whether the proxy should be bypassed for API endpoints. |
| RAG tools return empty results / agent answers without citations | Vector store exists but is empty, or embedding model endpoint is unavailable during ingestion | Re-run ingestion (`POST /ingest`). Check embedding provider availability. Inspect `data/*.annot.json` sidecar files — missing sidecars cause re-annotation on next ingest. |
| Sessions lost after App Service restart | `data/sessions.json` stored on ephemeral local disk (App Service restarts wipe `/home` unless using persistent storage) | Mount an Azure File Share to `/home/site/wwwroot/data`. Alternatively, migrate session persistence to Azure Blob Storage or a database. |
| GitHub Actions deployment fails at `azure/webapps-deploy` step | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret is expired or incorrect | Regenerate the publish profile from Azure Portal → App Service → Overview → Download publish profile. Update the GitHub secret. |
| `uv sync` fails in CI | `pyproject.toml` or `uv.lock` is out of sync, or a dependency requires a build tool not present on the runner | Run `uv lock` locally, commit the updated `uv.lock`. Check if any native extension packages need build dependencies. |
| Pytest tests fail in CI (`test` job) | Code regression or environment variable not available during test run | Review the failing test output in the Actions log. Ensure test fixtures mock external calls (LLM, vector store). Do not merge until green. |
| PDF documents not accessible via `/docs` endpoint | `StaticFiles` mount path is wrong, or the `data/` directory is not included in the deployment zip | Verify the `data/Insurance-product-info/` directory is committed (or copied) into the deployment. Check `app.mount("/docs", StaticFiles(directory=str(_DATA_DIR)))` path resolution in logs. |
| CORS errors in browser console | Frontend origin not listed in `allow_origins` in `main.py`, or the App Service URL changed | Add the new frontend URL to the `allow_origins` list in `api/main.py` and redeploy. |
| Annotation LLM calls fail during ingestion | `OPENAI_MODEL` or `OPENAI_URL_BASE` not set for the ingest worker, or rate limits hit | Set correct env vars. For Anthropic: use `claude-sonnet-4-6` at `https://api.anthropic.com/v1`. Implement retry logic or reduce `batch_size`. |
| `tool1_code_review` / `tool2_tech_docs` GitHub Actions fail | `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` secrets missing from repo | Settings → Secrets → Actions: add the missing secrets. Verify the `OUTPUT_REPO` (`ai-delivery-outputs`) exists and the `GH_TOKEN` has write access. |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- Azure App Service instances `training-bot-api` and `training-bot-frontend` provisioned
- GitHub repository secrets set:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `API_KEY` (for LLM)
  - `ANTHROPIC_API_KEY` (for AI tools workflows)
  - `GH_TOKEN`
  - `SENDGRID_API_KEY`
- `uv` installed locally (`pip install uv` or `brew install uv`)

### 4.2 Standard Deployment (via CI/CD — preferred)

1. **Merge a PR to `main`** — the `Test & Deploy` workflow (`.github/workflows/deploy.yml`) triggers automatically.
2. **`test` job** runs: `uv sync` → `uv run pytest tests/ -v`. If any test fails, deployment is blocked.
3. **`deploy-api` job** runs (only on push to `main`, after `test` passes):
   - Checks out code
   - Runs `uv export --no-dev --format requirements-txt -o requirements.txt`
   - Deploys to Azure App Service `training-bot-api` using the publish profile
4. **`deploy-frontend` job** runs in parallel with `deploy-api`:
   - Same steps, deploys to `training-bot-frontend`
5. **Verify deployment** — wait ~2 minutes, then run health checks in Section 2.

### 4.3 Manual Deployment (emergency / local override)

```bash
# Step 1: Install dependencies and export requirements
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt

# Step 2: Zip the application
zip -r deployment.zip . \
  --exclude ".git/*" \
  --exclude ".github/*" \
  --exclude "__pycache__/*" \
  --exclude "*.pyc" \
  --exclude ".venv/*"

# Step 3: Deploy via Azure CLI
az webapp deployment source config-zip \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src deployment.zip

az webapp deployment source config-zip \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src deployment.zip
```

> [TODO: What is the Azure resource group name?]

### 4.4 Ingest PDFs into Vector Store (post-deployment or on new documents)

```bash
# Via API endpoint (recommended for production)
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest

# Or locally before deployment
cd Insurance-Training-Bot-main
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### 4.5 Rollback Procedure

**Option A — Revert via GitHub (recommended):**

```bash
# Identify the last good commit SHA
git log --oneline -10

# Revert the bad commit and push
git revert <bad-commit-sha>
git push origin main
# CI/CD will redeploy the reverted code automatically
```

**Option B — Azure deployment slot swap (if slots are configured):**

```bash
# Swap staging slot back to production
az webapp deployment slot swap \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

> [TODO: Are Azure deployment slots (staging/production) configured? If not, Option A is the only rollback path.]

**Option C — Re-deploy a specific commit manually:**

```bash
git checkout <last-good-commit-sha>
# Follow manual deployment steps in 4.3
```

**Post-rollback verification:** Re-run all health checks in Section 2. Confirm the LLM responds and the vector store is loaded.

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

| Log Source | What to Watch | How to Access |
|---|---|---|
| Azure App Service log stream | Startup messages (`Vector store loaded`, `No vector store found`), unhandled exceptions, httpx errors | Azure Portal → `training-bot-api` → Log Stream |
| GitHub Actions run logs | CI/CD step failures (`test`, `deploy-api`, `deploy-frontend`) | GitHub → Actions tab |
| AI Tools workflow logs | `tool1` through `tool5` execution errors, Claude API failures | GitHub → Actions tab → individual workflow runs |
| Python `logging` output (INFO) | Ingestion progress (`[ingest] N chunks from file.pdf`), embedding batch progress, vector store save | App Service log stream |

### 5.2 Key Metrics to Monitor

| Metric | Target / Threshold | Alert Condition |
|---|---|---|
| Azure App Service HTTP 5xx errors | < 1% of requests | > 5% over 5 minutes → investigate |
| Azure App Service response time (P95) | < 5 s for non-streaming; streaming starts within 3 s | > 10 s P95 → investigate LLM latency |
| App Service CPU | < 70% sustained | > 90% for 5 min → scale up or investigate runaway process |
| App Service memory | [TODO: baseline after first week of load] | > 85% → possible memory leak in vector store or session accumulation |
| LLM API error rate | 0% | Any `401`, `429`, `5xx` from OpenRouter/Anthropic → alert |
| GitHub Actions `Test & Deploy` workflow | All jobs green | Any job failure on `main` branch → block and investigate |

> [TODO: Are Azure Monitor alerts, Application Insights, or any third-party APM (Datadog, New Relic) configured? If not, set up at minimum: HTTP 5xx alert and CPU alert on both App Services.]

### 5.3 Logs to Watch in Detail

```
# Healthy startup sequence
INFO:     Vector store loaded (N products)
INFO:     Application startup complete.

# Unhealthy startup
WARNING:  No vector store found — run POST /ingest first.

# Ingestion progress (normal)
[ingest] processing: data/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
[ingest] 47 chunks from Generations-II_PB_EN.pdf
[ingest] embedding batch 1–126 / 312 …
[ingest] index saved (312 chunks)

# LLM errors to alert on
httpx.HTTPStatusError: 401
httpx.HTTPStatusError: 429   # rate limit
httpx.ConnectError           # LLM endpoint unreachable
```

### 5.4 Session File Health

```bash
# Check sessions file size growth (large file = many sessions accumulated)
ls -lh /home/site/wwwroot/data/sessions.json
```

> [TODO: Implement a session TTL/cleanup policy. Sessions are currently never deleted automatically, which will cause `sessions.json` to grow unboundedly.]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: name / Slack handle / phone] | Service down, health checks failing, deployment failures |
| L2 | Backend Lead | [TODO: name / contact] | LLM integration issues, RAG pipeline failures, data loss |
| L3 | Platform / DevOps | [TODO: name / contact] | Azure infrastructure issues, scaling, persistent storage |
| L4 | Product Owner | [TODO: name / contact] | Business-impacting outage > 1 hour, data breach concern |
| External | Azure Support | [TODO: support tier / case URL] | Azure App Service / infrastructure P1 issues |
| External | OpenRouter / Anthropic Support | [TODO: support contact] | LLM API outage confirmed on provider side |

> [TODO: Fill in all team contacts, Slack channels (#insurance-bot-ops?), PagerDuty rotation, and SLA targets.]

---

## 7. Useful Commands

### Application Management

```bash
# Restart the API App Service
az webapp restart --resource-group <RG> --name training-bot-api

# Restart the frontend App Service
az webapp restart --resource-group <RG> --name training-bot-frontend

# Tail live logs from Azure
az webapp log tail --resource-group <RG> --name training-bot-api

# Download recent logs
az webapp log download --resource-