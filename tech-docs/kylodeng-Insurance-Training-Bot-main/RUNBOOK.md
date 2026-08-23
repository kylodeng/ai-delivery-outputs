# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that helps insurance agents develop their product knowledge and sales skills through two interactive modes: a **Teacher mode** (ongoing streamed chat with a LangGraph agent that answers product questions using a RAG pipeline over Sun Life Hong Kong insurance PDFs) and a **Roleplay/Assessment mode** (a simulated customer conversation followed by an AI-scored performance assessment). The backend connects to an OpenRouter-proxied LLM (default: `openai/gpt-oss-20b:free`, configurable), a local FAISS or ChromaDB vector store built from ingested PDF product brochures and hospital network documents, and a session store persisted to `data/sessions.json`. The application is deployed as two Azure App Service instances (`training-bot-api` and `training-bot-frontend`) via GitHub Actions CI/CD on every push to `main`. A suite of five AI-powered GitHub Actions workflows (Claude-based code review, tech docs, business docs, auto testing, and UAT facilitation) runs against the repository to automate delivery quality checks.

---

## 2. Health Checks

Run these checks in order to confirm the service is operational.

### 2.1 API Service (`training-bot-api`)

```bash
# Basic liveness — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<training-bot-api>.azurewebsites.net/

# FastAPI auto-generated docs endpoint — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<training-bot-api>.azurewebsites.net/docs

# List active sessions — expect HTTP 200 + JSON array
curl -s https://<training-bot-api>.azurewebsites.net/sessions
```

> [TODO: What is the actual Azure App Service hostname for `training-bot-api`?]

### 2.2 Vector Store

```bash
# Trigger ingestion check — confirms vector store is loaded at startup
# Look for this log line on startup:
# INFO: Vector store loaded (N products)
# If missing, the log will show:
# WARNING: No vector store found — run POST /ingest first.

# Manually trigger re-ingest via API (if endpoint exists):
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest
```

> [TODO: Is there a dedicated `/health` or `/readyz` endpoint? None is visible in the code — recommend adding one.]

### 2.3 Frontend Service (`training-bot-frontend`)

```bash
# Expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<training-bot-frontend>.azurewebsites.net/
```

> [TODO: What is the actual Azure App Service hostname for `training-bot-frontend`?]

### 2.4 LLM Connectivity

```bash
# Verify the OpenRouter/LLM endpoint is reachable from the API pod
# Check app logs for errors like "Connection refused" or 401 on first chat message
```

### 2.5 Session Persistence

```bash
# Confirm sessions.json exists and is non-empty
ls -lh data/sessions.json
python -c "import json; d=json.load(open('data/sessions.json')); print(f'{len(d)} sessions')"
```

### 2.6 CI/CD Pipeline Health

- Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`
- Confirm the **Test & Deploy** workflow last run on `main` shows ✅ green.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all chat requests | LLM API key (`API_KEY`) missing or invalid | 1. Check Azure App Service env vars. 2. Rotate key in OpenRouter dashboard. 3. Update `API_KEY` secret. 4. Restart App Service. |
| `WARNING: No vector store found — run POST /ingest first.` in startup logs | `data/` directory missing from deployment or vector store files not persisted | 1. Confirm `data/` is included in the App Service deployment package. 2. Run `POST /ingest`. 3. Check Azure App Service storage mount if using ephemeral filesystem. |
| Chat responses are slow (>30s) or time out | LLM endpoint latency / OpenRouter rate limiting | 1. Check OpenRouter dashboard for rate limit status. 2. Switch to a higher-tier model or paid key via `OPENAI_MODEL` env var. 3. Check `SHOW_TOOL_CALLS` — excessive tool logging can add overhead. |
| `401 Unauthorized` from LLM endpoint | Expired or wrong `API_KEY` | 1. Verify key in Azure App Service Configuration. 2. Generate a new key from OpenRouter. 3. Redeploy or restart. |
| CORS errors in browser console | Frontend origin not in the CORS allow-list | 1. Add the new frontend URL to `allow_origins` in `api/main.py`. 2. Redeploy API. |
| Session data lost after restart | `sessions.json` on ephemeral Azure App Service filesystem | 1. Mount an Azure Files share to the `/data` path. 2. Alternatively, migrate session store to Azure Blob or Azure Table Storage. |
| RAG tools return empty results / wrong product info | Vector store is stale or PDFs were not re-ingested after update | 1. Add new PDFs to `data/Insurance-product-info/`. 2. Run `POST /ingest`. 3. Restart API to reload the store. |
| GitHub Actions workflow fails at `Deploy API to Azure App Service` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret missing or expired | 1. Download a new publish profile from Azure Portal → App Service → Get publish profile. 2. Update the GitHub secret. 3. Re-run the failed workflow. |
| GitHub Actions `Run tests` step fails | Dependency conflict or test regression | 1. Check the failing test output in the Actions log. 2. Run `uv run pytest tests/ -v` locally. 3. Fix the test or the code. 4. Push the fix to unblock `main`. |
| `ssl.SSLError` / `verify=False` warnings | TLS verification is disabled (`httpx.Client(verify=False)`) — existing by design but may surface in logs | 1. Confirm this is intentional for the deployment network. 2. If not, enable verification and supply the correct CA bundle via `HTTPX_CA_BUNDLE` env var. |
| `AttributeError` or `ImportError` on startup | Python version mismatch (code uses Python 3.13 walrus/match syntax, CI uses 3.12 for some tools) | 1. Confirm App Service runtime is Python 3.13. 2. Check `uv sync` completed without errors. 3. Pin the runtime in `pyproject.toml`. |
| Assessor agent produces inaccurate scoring | LLM hallucination — agent not using RAG tools for fact-checking | 1. Review `ASSESSOR_SYSTEM` prompt in `api/agent.py`. 2. Confirm tools are bound to the assessor agent. 3. Check LLM tool-call logs with `SHOW_TOOL_CALLS=true`. |

---

## 4. Deployment Procedure

### Prerequisites

- Access to the GitHub repository with `write` permissions.
- Azure CLI (`az`) installed and authenticated.
- `uv` installed locally (`pip install uv`).
- All required GitHub secrets set (see §5).

### 4.1 Standard Deployment (automated — push to `main`)

```
Developer pushes to main
        │
        ▼
GitHub Actions: "Test & Deploy"
        │
        ├─► Job: test
        │     └─ uv sync → pytest tests/ -v
        │
        ├─► Job: deploy-api      (needs: test)
        │     └─ azure/webapps-deploy → training-bot-api
        │
        └─► Job: deploy-frontend (needs: test)
              └─ azure/webapps-deploy → training-bot-frontend
```

**Steps:**

1. Create a feature branch and open a pull request against `main`.
2. Ensure all GitHub Actions checks pass (including `Tool 1 — Code Review`).
3. Merge the PR to `main` — deployment starts automatically.
4. Monitor the **Test & Deploy** workflow at:
   `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`
5. After deployment (~3–5 min), run the health checks in §2.

### 4.2 Manual Deployment (emergency)

```bash
# 1. Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 3. Deploy Frontend manually
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

> [TODO: What is the Azure resource group name?]

### 4.3 Vector Store Ingest (after adding/updating PDFs)

```bash
# Place new PDFs in:
data/Insurance-product-info/<product-folder>/

# Run ingestion locally
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Or trigger via API endpoint
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest
```

> [TODO: Confirm whether `/ingest` endpoint is authenticated/protected — no auth middleware visible in `main.py`.]

### 4.4 Rollback Steps

**Option A — Revert the Git commit and redeploy:**

```bash
# Identify the last good commit
git log --oneline -10

# Revert the bad commit
git revert <bad-commit-sha>
git push origin main
# GitHub Actions will redeploy automatically
```

**Option B — Roll back via Azure Portal:**

1. Azure Portal → App Services → `training-bot-api` → **Deployment Center**.
2. Select the previous deployment slot or re-deploy the last known-good ZIP.
3. Repeat for `training-bot-frontend`.

**Option C — Azure CLI swap (if slots are configured):**

```bash
az webapp deployment slot swap \
  --resource-group <resource-group> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

> [TODO: Are staging slots configured in Azure? Not evident from the code.]

---

## 5. Monitoring & Alerting

### 5.1 Required GitHub Secrets

| Secret Name | Used By | Description |
|---|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | deploy.yml | Azure publish profile for `training-bot-api` |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | deploy.yml | Azure publish profile for `training-bot-frontend` |
| `API_KEY` | App runtime | OpenRouter / LLM API key |
| `ANTHROPIC_API_KEY` | GitHub Actions tools 1–5 | Claude API key for CI workflows |
| `GH_TOKEN` | GitHub Actions tools 1–5 | GitHub PAT for cross-repo writes |
| `SENDGRID_API_KEY` | GitHub Actions tools 1–5 | Email notification delivery |

### 5.2 Application Logs

```bash
# Stream live logs from Azure App Service
az webapp log tail \
  --resource-group <resource-group> \
  --name training-bot-api

# Key log patterns to alert on:
# ERROR   → Any unhandled exception
# WARNING: No vector store found  → Ingest not run
# WARNING: annotation failed      → PDF annotation LLM call failed (non-fatal)
# Connection refused              → LLM endpoint unreachable
```

### 5.3 Key Metrics to Monitor

| Metric | Tool | Alert Threshold |
|---|---|---|
| API HTTP 5xx rate | Azure App Service Metrics / App Insights | > 1% of requests |
| API response latency (P95) | Azure App Insights | > 30 seconds |
| LLM API error rate | OpenRouter dashboard | > 5% of calls |
| GitHub Actions workflow failure | GitHub Actions / email | Any `main` branch failure |
| `sessions.json` file size | Azure Storage metrics | > 100 MB (session bloat) |
| Vector store chunk count on startup | Application log (`INFO: Vector store loaded`) | 0 chunks = alert |
| Azure App Service CPU/memory | Azure Monitor | CPU > 80% sustained 5 min |

### 5.4 Alerting

> [TODO: Are Azure Monitor alerts or Application Insights configured? Not visible in the repo — recommend setting up at minimum: HTTP 5xx alert, memory pressure alert, and workflow failure notification to the team Slack/email.]

### 5.5 CI/CD Workflow Schedules

| Workflow | Schedule | Purpose |
|---|---|---|
| Tool 1 — Code Review | Every Monday 08:00 UTC + PR open/sync | Automated PR review |
| Tool 2 — Tech Docs | Every Sunday 06:00 UTC + push to `main` | Auto-regenerate docs |
| Tool 4 — Auto Testing | Every Wednesday 07:00 UTC + PR open/sync | Generate/gap-analyse tests |

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: name + contact] | Service down, health checks failing |
| L2 | Backend / ML lead | [TODO: name + contact] | LLM quality issues, RAG accuracy problems, vector store corruption |
| L3 | Azure platform / DevOps | [TODO: name + contact] | App Service outage, deployment pipeline broken, secret rotation needed |
| L4 | Product owner | [TODO: name + contact] | Data breach, major feature regression, go/no-go decision |
| Vendor | OpenRouter support | https://openrouter.ai | LLM API outage or billing issue |
| Vendor | Anthropic support | https://console.anthropic.com | Claude API outage (CI workflows) |
| Vendor | Azure support | Azure Portal → Help + Support | App Service or infrastructure issue |

---

## 7. Useful Commands

### Service Management

```bash
# Restart API App Service
az webapp restart --resource-group <rg> --name training-bot-api

# Restart Frontend App Service
az webapp restart --resource-group <rg> --name training-bot-frontend

# Check App Service status
az webapp show --resource-group <rg> --name training-bot-api --query "state"
```

### Local Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Start the API locally
uv run uvicorn api.main:app --reload --port 8000

# Run with tool-call logging enabled
SHOW_TOOL_CALLS=true uv run uvicorn api.main:app --reload --port 8000
```

### Vector Store Operations

```bash
# Ingest all PDFs (with verbose output)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Ingest with custom chunk size
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --max-words 300

# Force re-annotate a single PDF (delete its .annot.json sidecar first)
rm data/Insurance-product-info/<product>/<file>.pdf.annot.json
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### Session Management

```bash
# View all sessions
python -c "
import json
sessions = json.load(open('data/sessions.json'))
for sid, s in sessions.items():
    print(sid[:8], s.get('mode'), s.get('title',''))
"

# Backup sessions before maintenance
cp data/sessions.json data/sessions.$(date +%Y%m%d%H%M%S).bak

# Clear all sessions (destructive — backup first!)
echo '{}' > data/sessions.json
```