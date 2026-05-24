# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a web-based AI coaching and assessment platform designed to help new insurance agents in Hong Kong master sales techniques and product knowledge. The system exposes a **FastAPI backend** (`api/main.py`) that serves two LangGraph agents — a **Teacher agent** for ongoing interactive coaching and an **Assessor agent** for post-roleplay performance scoring — both backed by a **Retrieval-Augmented Generation (RAG)** pipeline built on a vector store populated from Sun Life Hong Kong insurance product PDFs. The frontend is a Chainlit-based chat UI. Both the API (`training-bot-api`) and the frontend (`training-bot-frontend`) are deployed as **Azure App Service** instances via GitHub Actions; the CI/CD pipeline runs tests on every push to `main` and deploys automatically on merge. An LLM router (OpenRouter or direct Anthropic/OpenAI endpoint, configurable via environment variables) powers both the agent reasoning and the PDF annotation/ingestion pipeline.

---

## 2. Health Checks

Perform these checks in order to confirm the service is fully operational.

### 2.1 Azure App Service — API

```bash
# Check HTTP 200 from the API root
curl -o /dev/null -s -w "%{http_code}" https://training-bot-api.azurewebsites.net/

# Check the FastAPI auto-generated docs endpoint
curl -s https://training-bot-api.azurewebsites.net/docs | head -20
```

### 2.2 Vector Store Loaded

On startup, `main.py` logs one of:

```
INFO  Vector store loaded (N products)     ← healthy
WARNING  No vector store found — run POST /ingest first.  ← action needed
```

Check Azure App Service log stream (see §7) for this message after each deployment.

### 2.3 Ingest Endpoint

```bash
# Trigger ingestion and confirm 200 OK
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

### 2.4 Session API

```bash
# List existing sessions — expect JSON array (may be empty)
curl https://training-bot-api.azurewebsites.net/sessions
```

### 2.5 LLM Connectivity

```bash
# A minimal chat request to confirm the LLM router is reachable
curl -X POST https://training-bot-api.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"healthcheck","message":"ping"}'
```

Expected: streamed SSE response with at least one token.

### 2.6 Frontend

```
# Open in browser or curl
curl -o /dev/null -s -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
```

Expected: `200`.

### 2.7 GitHub Actions Pipeline

Navigate to `Actions` tab in the repo. The **Test & Deploy** workflow on the latest `main` commit should show ✅ green for both `deploy-api` and `deploy-frontend` jobs.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all chat requests | `API_KEY` or `OPENAI_URL_BASE` env var missing/wrong in Azure App Service | 1. Open Azure Portal → App Service `training-bot-api` → Configuration → Application Settings. 2. Verify `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`. 3. Save and restart the app. |
| `WARNING: No vector store found — run POST /ingest first` in logs | `data/` directory not deployed, or `/ingest` was never called after a cold start | 1. `POST /ingest` endpoint to rebuild the index. 2. Confirm PDFs exist under `data/Insurance-product-info/`. 3. Check that the `data/` directory is included in the App Service deployment package. |
| LLM responses are empty or truncated | Hitting rate limits on OpenRouter or Anthropic free tier | 1. Check LLM provider dashboard for rate-limit errors. 2. Reduce `batch_size` in `core/ingest.py` (default 126). 3. Increase `batch_delay`. 4. Switch to a paid API tier. |
| `SSL: CERTIFICATE_VERIFY_FAILED` in logs | `verify=False` workaround is suppressed by Azure networking policy | 1. Confirm `httpx.Client(verify=False)` is present in `main.py` (already set). 2. If behind a corporate proxy, install the CA certificate into the container. |
| Chat streams stall mid-response | Upstream LLM timeout; Azure App Service idle timeout (default 230 s) | 1. Set `WEBSITES_CONTAINER_START_TIME_LIMIT=1800` in App Service config. 2. Check `SCM_COMMAND_IDLE_TIMEOUT`. 3. Verify streaming is enabled on the App Service plan (Standard tier or above). |
| Sessions lost after restart | `data/sessions.json` is on ephemeral local storage | 1. Mount an Azure Files share to the App Service and point `_SESSIONS_FILE` to it. 2. [TODO: confirm whether persistent storage is configured] |
| Frontend returns `403` or CORS errors | `allow_origins` list in `main.py` does not include the production frontend URL | 1. Add the production URL to `allow_origins` in `api/main.py`. 2. Redeploy. |
| GitHub Actions `deploy-api` job fails | `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret missing or expired | 1. Download a fresh publish profile from Azure Portal → App Service → Get Publish Profile. 2. Update the GitHub secret `AZURE_WEBAPP_PUBLISH_PROFILE_API`. |
| `uv sync` fails in CI | `pyproject.toml`/`uv.lock` out of sync | 1. Run `uv lock` locally. 2. Commit the updated `uv.lock`. 3. Re-run the workflow. |
| Ingestion produces `0 chunks` | PDFs are corrupt, password-protected, or `pdfplumber` cannot parse them | 1. Open the PDF locally to confirm it is readable. 2. Check annotator logs for `annotation failed` warnings. 3. Re-export the PDF from source without password protection. |
| LLM annotation returns non-JSON | Model wrapped JSON in markdown fences | Already handled by `_call_llm_json` strip logic in `core/annotator.py`. If still failing, check for model output truncation (increase `max_tokens`). |
| Tool 1–5 GitHub Actions workflows fail | Missing secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) | 1. Go to repo Settings → Secrets and variables → Actions. 2. Add or rotate the missing secret. 3. Re-run the failed workflow. |
| `POST /ingest` times out (>30 s) | Large PDF corpus with LLM annotation enabled; synchronous ingestion | 1. Run ingestion offline: `python core/ingest.py --pdf-dir data/Insurance-product-info`. 2. Commit the generated `.annot.json` sidecar files so re-ingestion skips LLM calls. 3. [TODO: implement background task / async ingest endpoint] |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- GitHub repository secrets set: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- `uv` installed locally (`pip install uv`)

### 4.1 Standard Deployment (Automated via CI/CD)

```
1. Create feature branch and open a Pull Request against `main`.
2. GitHub Actions "Test & Deploy" workflow runs `pytest` automatically.
3. All tests must pass (green ✅) before merging.
4. Merge the PR into `main`.
5. GitHub Actions automatically runs:
   a. `test` job  — pytest on ubuntu-latest, Python 3.13
   b. `deploy-api` job — deploys to Azure App Service `training-bot-api`
   c. `deploy-frontend` job — deploys to Azure App Service `training-bot-frontend`
6. Monitor both deploy jobs in the Actions tab.
7. After deploy completes, run health checks (§2).
```

### 4.2 Manual Deployment (Emergency / Hotfix)

```bash
# 1. Install dependencies and generate requirements.txt
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 3. Deploy frontend manually
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip

# 4. Verify deployment
az webapp show --name training-bot-api --resource-group <resource-group> \
  --query state -o tsv
# Expected: "Running"
```

### 4.3 Post-Deployment Steps

```bash
# 1. Trigger vector store rebuild if PDFs changed
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# 2. Confirm vector store loaded in logs
az webapp log tail --name training-bot-api --resource-group <resource-group>
# Look for: "Vector store loaded (N products)"

# 3. Run smoke test
curl -X POST https://training-bot-api.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"smoketest","message":"What products do you have?"}'
```

### 4.4 Rollback Steps

```bash
# Option A — Revert via GitHub (preferred)
# 1. Identify the last known-good commit SHA:
git log --oneline -10

# 2. Create a revert PR:
git revert <bad-commit-sha>
git push origin main
# CI/CD will redeploy the reverted code automatically.

# Option B — Azure deployment slot swap (if slots are configured)
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group <resource-group> \
  --slot staging \
  --target-slot production

# Option C — Redeploy a specific release tag
git checkout tags/v<last-good-version> -b hotfix/rollback-<version>
# Then follow §4.2 manual deployment steps.
```

> [TODO: Confirm whether Azure deployment slots (staging/production) are configured. If not, this should be set up to enable zero-downtime rollback.]

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

```bash
# Stream live logs from the API App Service
az webapp log tail \
  --name training-bot-api \
  --resource-group <resource-group>

# Download historical logs
az webapp log download \
  --name training-bot-api \
  --resource-group <resource-group> \
  --log-file api-logs.zip
```

**Key log patterns to watch:**

| Log Pattern | Meaning | Action |
|---|---|---|
| `Vector store loaded (N products)` | Healthy startup | None |
| `No vector store found` | Missing index | `POST /ingest` |
| `annotation failed for <file>` | LLM annotation error | Check API key; inspect PDF |
| `rate-limit pause` | Embedding rate limiting active | Normal if `batch_delay > 0` |
| `ERROR` / `Exception` | Unhandled error | Inspect full stack trace |
| `SHOW_TOOL_CALLS=true` | Tool call logging active | Expected; reduce verbosity in prod if noisy |

### 5.2 Metrics to Watch (Azure Monitor)

| Metric | Location | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | App Service → Metrics → Http5xx | > 5% over 5 min |
| Average response time | App Service → Metrics → AverageResponseTime | > 10 s |
| CPU percentage | App Service → Metrics → CpuPercentage | > 80% sustained 5 min |
| Memory working set | App Service → Metrics → MemoryWorkingSet | > 80% of plan limit |
| HTTP 4xx rate | App Service → Metrics → Http4xx | > 20% (may indicate auth/config issue) |

### 5.3 GitHub Actions Workflows to Monitor

| Workflow | Trigger | What to Check |
|---|---|---|
| Test & Deploy | Push to `main`, PRs | Both deploy jobs green; test job passes |
| Tool 1 — Code Review | PRs, Mon 08:00 UTC | PR comment posted; no JSON parse errors |
| Tool 2 — Tech Docs | Push to `main`, Sun 06:00 UTC | Docs written to output repo |
| Tool 3 — Business Docs | Version tags `v*` | Business doc + gap questionnaire generated |
| Tool 4 — Auto Testing | PRs on `src/**`, Wed 07:00 UTC | Test files generated; coverage report written |
| Tool 5 — UAT | `release/*` branch creation | UAT test pack or defect report generated |

### 5.4 Vector Store Health

```bash
# Check number of indexed products via API
curl https://training-bot-api.azurewebsites.net/ingest/status
# [TODO: /ingest/status endpoint does not exist in current code — implement or check an alternative]
```

Alternatively, inspect startup logs for `Vector store loaded (N products)` after each restart.

### 5.5 Alerting

> [TODO: No alerting configuration is defined in the codebase. Recommended setup:
> - Azure Monitor alert rule on HTTP 5xx > 5%
> - Azure Monitor alert rule on App Service stopped/crashed state
> - GitHub Actions failure notification (Settings → Notifications)]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 — First Response | On-call DevOps | [TODO: name & contact] | Service down, health checks failing |
| L2 — Backend Engineer | API / Agent developer | [TODO: name & contact] | LangGraph agent errors, RAG failures, session corruption |
| L3 — Cloud/Infra | Azure platform owner | [TODO: name & contact] | App Service plan issues, networking, persistent storage |
| L4 — External | Azure Support | [TODO: support contract tier] | Azure platform outages, App Service quota issues |
| LLM Vendor | Anthropic / OpenRouter support | [TODO: account email & support URL] | API key issues, model deprecation, rate-limit increases |
| Business Owner | Product / Training team | kylo.deng@capco.com | Business continuity, data/content issues |

> [TODO: Fill in all team contacts above before going to production.]

---

## 7. Useful Commands

### Application Management

```bash
# View App Service state
az webapp show --name training-bot-api --resource-group <rg> --query state

# Restart API
az webapp restart --name training-bot-api --resource-group <rg>

# Restart Frontend
az webapp restart --name training-bot-frontend --resource-group <rg>

# Stream live application logs
az webapp log tail --name training-bot-api --resource-group <rg>

# List current App Service configuration settings
az webapp config appsettings list --name training-bot-api --resource-group <rg>

# Set an environment variable (e.g. update API key)
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg> \
  --settings API_KEY="<new-value>"
```

### Vector Store / Ingestion

```bash
# Rebuild the vector store from all PDFs in data/
python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Ingest via the REST API (triggers background ingestion on the server)
curl -X POST https://training-bot-api.