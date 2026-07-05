# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

> **Last updated:** [TODO: insert date]
> **Maintained by:** [TODO: fill in team contacts]
> **Severity classification:** [TODO: define P1–P4 thresholds for this service]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training environment for insurance sales agents operating in the Hong Kong market. It exposes two primary modes: a **Teacher mode**, in which a LangGraph agent conducts interactive, RAG-backed coaching sessions using streamed LLM responses, and a **Roleplay/Assessment mode**, in which the agent simulates realistic customer personas drawn from a randomised Hong Kong customer profile generator and then produces a structured performance assessment of the trainee. The backend is built with FastAPI (Python 3.13), uses LangChain + LangGraph for agent orchestration, and retrieves insurance product knowledge from a vector store (supporting ChromaDB, local FAISS, or Pinecone) populated by ingesting PDF product brochures. An OpenRouter-compatible LLM endpoint (defaulting to `openai/gpt-oss-20b:free` or a Claude model) is used for inference. A Chainlit or Vite-based frontend communicates with the backend over HTTP. The service is deployed to **Azure App Service** as two separate apps (`training-bot-api` and `training-bot-frontend`) via GitHub Actions on every push to `main`.

---

## 2. Health Checks

Perform the following checks to confirm the service is operating normally.

### 2.1 API Service

```bash
# Basic HTTP liveness — expect HTTP 200
curl -I https://<training-bot-api>.azurewebsites.net/

# FastAPI auto-generated docs page (confirms app started correctly)
curl https://<training-bot-api>.azurewebsites.net/docs
```

[TODO: Is there a dedicated `/health` or `/ping` endpoint? None found in the code — recommend adding one.]

### 2.2 Vector Store

```bash
# Ingest check — POST to /ingest confirms vector store is accessible and writable
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest

# List sessions — confirms session state is loading correctly from sessions.json
curl https://<training-bot-api>.azurewebsites.net/sessions
```

On startup, the application logs one of:
- `Vector store loaded (N products)` — **healthy**
- `No vector store found — run POST /ingest first` — **degraded: RAG unavailable**

### 2.3 LLM Connectivity

```bash
# Send a minimal teacher-mode message and confirm a streamed response
curl -X POST https://<training-bot-api>.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test", "message": "Hello"}'
```

[TODO: Confirm the exact chat endpoint path — `main.py` was truncated before route definitions were visible.]

### 2.4 Frontend

```bash
# Confirm the frontend app is serving
curl -I https://<training-bot-frontend>.azurewebsites.net/
```

### 2.5 Static Document Serving

```bash
# Confirm PDFs are accessible via the /docs mount
curl -I https://<training-bot-api>.azurewebsites.net/docs/Insurance-product-info/
```

### 2.6 GitHub Actions (CI/CD Pipeline Health)

Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

All workflows (`Test & Deploy`, `Tool 1–5`) should show green on `main`.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500 Internal Server Error` on all routes | Application failed to start; missing environment variable or vector store crash | Check Azure App Service logs. Verify all required env vars are set (see §5). Run `POST /ingest` if vector store is absent. |
| `No vector store found — run POST /ingest first` in logs | Vector store was never built, or `data/` directory is missing/empty | SSH into the App Service or trigger `POST /ingest` endpoint. Ensure PDF files exist under `data/Insurance-product-info/`. |
| LLM responses are empty or return `401 Unauthorized` | `API_KEY` env var missing or revoked; OpenRouter/Anthropic quota exhausted | Rotate the API key in Azure App Service Configuration. Check provider dashboard for quota/billing status. |
| LLM responses are extremely slow or time out | LLM provider rate limiting; free-tier model throttling (`openai/gpt-oss-20b:free`) | Switch `OPENAI_MODEL` to a paid model. Check OpenRouter status page. Add retry logic [TODO: not currently present in code]. |
| SSL verification errors in logs (`verify=False` suppressing them) | The `httpx` client is configured with `verify=False` — self-signed cert or proxy in path | This is intentional in the current code but is a security risk. Investigate network path and install proper certs; remove `verify=False` for production. |
| Chat sessions lost after app restart | `sessions.json` stored on ephemeral local disk in Azure App Service | Mount an Azure Files share to persist `data/sessions.json`, or switch to a database-backed session store. |
| `POST /ingest` takes very long or times out | Large number of PDFs; embedding API rate limiting; `batch_delay` set too high | Monitor ingest logs. Reduce batch size or increase timeout. With a paid Voyage AI account, set `batch_delay=0`. |
| GitHub Actions deploy job fails | Invalid or expired `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret | Rotate publish profiles in Azure Portal → App Service → "Get publish profile". Update secrets in GitHub repository settings. |
| GitHub Actions test job fails | Pytest test failures after a code change | Review test output in Actions logs. Fix failing tests before merging. Run locally with `uv run pytest tests/ -v`. |
| Tool 1–5 AI workflow fails | Missing `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` secrets | Add/rotate the relevant secret in GitHub repository → Settings → Secrets. |
| Roleplay persona generation returns malformed profile | LLM JSON output parsing failure in `sessions.py` `generate_profile()` | Check logs for raw LLM output. The profile generator uses random selection from hardcoded lists — verify `generate_profile()` fallback logic. |
| RAG tool returns stale or incorrect product information | `.annot.json` annotation cache is outdated; PDF re-ingested but vector store not rebuilt | Delete `.annot.json` sidecar files and re-run `POST /ingest`. |
| Citation markers (`[[S1]]`) missing from teacher responses | Sources context variable (`_sources_ctx`) not initialised; `reset_sources()` not called at request start | Check `api/rag_tools.py` — ensure `reset_sources()` is called at the start of each teacher-mode request handler. |
| CORS errors in browser | Frontend origin not listed in `allow_origins` | Add the frontend URL to `allow_origins` in `api/main.py` and redeploy. |
| `uv sync` or `uv export` fails in CI | `pyproject.toml` / `uv.lock` out of sync | Run `uv lock` locally, commit the updated `uv.lock`, and push. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI installed and authenticated (`az login`)
- `uv` installed (`pip install uv` or via `astral-sh/setup-uv`)
- GitHub repository secrets set (see §5.2)
- Azure App Services `training-bot-api` and `training-bot-frontend` already provisioned

[TODO: What Azure region is used? What App Service plan tier?]
[TODO: Is there a staging slot before production?]

---

### 4.1 Standard Deployment (Automated via GitHub Actions)

```text
1. Create a feature branch and open a pull request against `main`.
2. GitHub Actions automatically runs the `Test & Deploy` workflow:
   a. Job `test`: runs `uv run pytest tests/ -v` on ubuntu-latest / Python 3.13.
   b. If tests pass and the event is a push to `main`:
      - Job `deploy-api`:   deploys to Azure App Service `training-bot-api`
      - Job `deploy-frontend`: deploys to Azure App Service `training-bot-frontend`
3. Merge the PR only after the test job passes.
4. Monitor the Actions run at:
   https://github.com/kylodeng/Insurance-Training-Bot-main/actions
5. After deploy jobs complete, run health checks (§2) against the production URLs.
6. Verify vector store is intact:
   - Check startup log: "Vector store loaded (N products)"
   - If missing, call POST /ingest (see §7).
```

---

### 4.2 Manual Deployment

```bash
# Step 1: Export dependencies
uv export --no-dev --format requirements-txt -o requirements.txt

# Step 2: Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <YOUR_RG> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Step 3: Deploy frontend manually
az webapp deploy \
  --resource-group <YOUR_RG> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

[TODO: Is the frontend a separate Node/Vite build, or does the Python app serve the frontend too? `main.py` mounts `StaticFiles` for `/docs` but the Vite dev-server reference suggests a separate build step.]

---

### 4.3 Rollback Steps

```bash
# Option A — Revert via Git and push to main (triggers redeploy)
git revert <bad-commit-sha>
git push origin main

# Option B — Roll back to a previous deployment slot (if deployment slots configured)
az webapp deployment slot swap \
  --resource-group <YOUR_RG> \
  --name training-bot-api \
  --slot staging \
  --target-slot production

# Option C — Roll back to a specific zip deploy via Azure CLI
az webapp deployment list \
  --resource-group <YOUR_RG> \
  --name training-bot-api
# Identify the previous successful deployment ID, then:
az webapp deployment show \
  --resource-group <YOUR_RG> \
  --name training-bot-api \
  --deployment-id <previous-id>
```

> ⚠️ **Vector store note:** The vector store (`data/` directory) is stored on the App Service local filesystem. A redeployment may overwrite it. Always verify after rollback with `POST /ingest` if product data is missing.

[TODO: Is the vector store persisted to an external store (Pinecone / Azure Blob) or only local disk? This is a critical DR gap — local FAISS/Chroma will be lost on redeployment.]

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where to Find | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Monitoring → Metrics → Http5xx | > 1% over 5 min |
| HTTP response time (P95) | Azure App Service → Monitoring → Metrics → HttpResponseTime | > 10s (LLM streaming expected to be slow) |
| CPU usage | Azure App Service → Metrics → CpuPercentage | > 80% sustained for 5 min |
| Memory usage | Azure App Service → Metrics → MemoryWorkingSet | > 80% of plan limit |
| LLM token consumption | OpenRouter / Anthropic dashboard | [TODO: set budget alert] |
| Vector store chunk count | Logged at startup: `Vector store loaded (N products)` | Alert if N drops vs baseline |
| GitHub Actions workflow failures | GitHub → Actions tab | Any failure on `main` branch |
| Session file size (`sessions.json`) | App Service filesystem or mounted share | [TODO: define upper bound] |

### 5.2 Required Environment Variables / Secrets

| Variable | Where Set | Notes |
|---|---|---|
| `API_KEY` | Azure App Service Configuration | OpenRouter or Anthropic API key |
| `OPENAI_URL_BASE` | Azure App Service Configuration | Default: `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | Azure App Service Configuration | Default: `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | Azure App Service Configuration | `true`/`false` — toggles tool call logging |
| `ANTHROPIC_API_KEY` | GitHub Secrets | Used by AI workflow tools 1–5 |
| `GH_TOKEN` | GitHub Secrets | Used by AI workflow tools 1–5 |
| `SENDGRID_API_KEY` | GitHub Secrets | Used by AI workflow tools 1–5 |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | GitHub Secrets | Azure deploy credential for API |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | GitHub Secrets | Azure deploy credential for frontend |

### 5.3 Log Locations

```text
# Azure App Service live log stream
az webapp log tail \
  --resource-group <YOUR_RG> \
  --name training-bot-api

# Application Insights (if configured)
[TODO: Is Application Insights set up? No telemetry SDK found in the code.]

# GitHub Actions logs
https://github.com/kylodeng/Insurance-Training-Bot-main/actions
```

### 5.4 Key Log Messages

| Log Message | Meaning |
|---|---|
| `Vector store loaded (N products)` | Startup success — RAG is ready |
| `No vector store found — run POST /ingest first` | RAG unavailable — ingestion needed |
| `[ingest] processing: <file>` | Ingestion pipeline is running |
| `[ingest] index saved (N chunks)` | Ingestion complete |
| `[ingest] annotation failed for <file>` | PDF annotation error — chunk still ingested without LLM metadata |
| `[ingest] rate-limit pause Ns` | Normal rate-limiting pause during embedding |
| `SHOW_TOOL_CALLS=true` | Tool call events will be logged and streamed |

### 5.5 Alerting

[TODO: No alerting infrastructure (PagerDuty, Opsgenie, Azure Monitor alerts) is configured in the code. Recommend setting up Azure Monitor alert rules for HTTP 5xx and response time against both App Services.]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in] | Service unavailable, health check failing |
| L2 | Backend Lead | [TODO: fill in] | LLM integration failure, vector store corruption, data loss |
| L3 | Platform / Azure Admin | [TODO: fill in] | Azure App Service outage, publish profile rotation |
| L4 | LLM Provider Support | OpenRouter: https://openrouter.ai/docs · Anthropic: support@anthropic.com | API quota exhaustion, model deprecation |
| Business | Product Owner | `kylo.deng@capco.com` (from workflow config) | Training data gap, regulatory content issue |

> ⚠️ **Note:** SSL verification is disabled (`verify=False`) in all `httpx` clients. Escalate to security team if this is a compliance concern.

---

## 7. Useful Commands

### Start the API locally

```bash
# Install dependencies
uv sync

# Start FastAPI dev server (hot reload)
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### Run tests

```bash
uv run pytest tests/ -v
```

### Ingest PDFs into the vector store

```bash
# Via API endpoint (production/staging)
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest

# Via CLI (local)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### Export dependencies for deployment

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```