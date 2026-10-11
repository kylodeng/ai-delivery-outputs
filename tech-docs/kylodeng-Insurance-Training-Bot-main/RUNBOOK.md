# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web service that provides AI-powered insurance sales training for new agents, deployed on **Azure App Service** (two separate apps: `training-bot-api` for the backend and `training-bot-frontend` for the UI). The system operates in two modes: a **Teacher mode** that streams interactive coaching sessions via LangGraph agents using RAG (Retrieval-Augmented Generation) over a local vector store of insurance product PDFs, and a **Roleplay/Assessment mode** that simulates customer interactions and grades agent performance across five dimensions. The knowledge base consists of Sun Life Hong Kong insurance product brochures and hospital network documents, chunked and embedded into either a local FAISS or Chroma vector store at ingest time. The LLM backend is configurable via environment variables and defaults to OpenRouter; annotation/ingestion also uses an OpenAI-compatible endpoint. Five GitHub Actions CI/CD tools (code review, tech docs, business docs, auto-testing, UAT facilitation) run autonomously on schedule or PR triggers using Claude (`claude-sonnet-4-6`) via the Anthropic API.

---

## 2. Health Checks

### API (FastAPI Backend — `training-bot-api`)

| Check | Command / URL | Expected Result |
|---|---|---|
| HTTP liveness | `curl https://training-bot-api.azurewebsites.net/` | HTTP 200 or FastAPI default response |
| Docs UI | `https://training-bot-api.azurewebsites.net/docs` | Swagger UI loads |
| Vector store loaded | Check startup log for line | `Vector store loaded (N products)` where N > 0 |
| Static files (PDFs) | `curl https://training-bot-api.azurewebsites.net/docs/` | HTTP 200, directory listing or first PDF accessible |
| Sessions file | Check `data/sessions.json` exists and is valid JSON | File present, parseable |

### Frontend (`training-bot-frontend`)

| Check | Command / URL | Expected Result |
|---|---|---|
| HTTP liveness | `curl https://training-bot-frontend.azurewebsites.net/` | HTTP 200 |
| UI loads | Open in browser | [TODO: What UI framework — Chainlit or Vite SPA? Confirm expected landing page] |

### LLM/Vector Store

| Check | How | Expected |
|---|---|---|
| OpenRouter/LLM reachable | Check application logs for LLM call errors | No `401`, `429`, or connection errors |
| Vector store non-empty | `POST /ingest` response or startup log | `> 0 products` reported |
| Embedding model reachable | Run ingest and check logs | No embedding API errors |

### GitHub Actions Workflows

| Check | Where | Expected |
|---|---|---|
| CI pipeline passing | GitHub → Actions tab | `Test & Deploy` workflow green |
| AI tool workflows | GitHub → Actions tab | Tool 1–5 workflows show green on last run |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log shows `No vector store found — run POST /ingest first` | Vector store not initialised; `data/` directory missing index files | 1. SSH into the App Service or run locally. 2. `POST /ingest` endpoint or `python -m core.ingest --pdf-dir data/Insurance-product-info`. 3. Verify `data/` persisted (Azure App Service ephemeral storage — see note below). |
| LLM calls return `401 Unauthorized` | `API_KEY` env var missing or expired | 1. Check Azure App Service → Configuration → Application settings. 2. Rotate key at OpenRouter/provider. 3. Update `API_KEY` value and restart the app. |
| LLM calls return `429 Too Many Requests` | Rate limit hit on LLM provider or embedding API | 1. Check provider dashboard for quota. 2. Increase `batch_delay` in `core/ingest.py` for ingestion. 3. For chat, implement exponential backoff [TODO: backoff not yet implemented in `api/agent.py`]. |
| RAG tools return empty results or wrong products | Vector store stale or PDF annotations corrupt | 1. Check `.annot.json` sidecar files in `data/Insurance-product-info/`. 2. Delete corrupt `.annot.json` files. 3. Re-run ingestion: `POST /ingest`. |
| `StreamingResponse` stops mid-stream to the client | LLM provider timeout or agent tool-call loop | 1. Check application logs for `asyncio` timeout or `httpx` errors. 2. Note: `verify=False` is set on httpx clients — check for TLS/network issues. 3. Restart the App Service. |
| Sessions not persisting across restarts | `data/sessions.json` on ephemeral Azure App Service storage | 1. [TODO: Is Azure persistent storage (Azure Files mount) configured for `/data`?]. 2. Configure a persistent mount or migrate sessions to Azure Blob / Cosmos DB. |
| `POST /ingest` fails with LLM annotation error | Annotation LLM endpoint unreachable or returns non-JSON | 1. Check `OPENAI_URL_BASE` env var points to a valid endpoint. 2. Raw chunker fallback activates automatically — check logs for `annotation failed for X — using raw chunker`. 3. Verify `API_KEY` is valid for the annotation model. |
| CI `Test & Deploy` workflow fails on `pytest` | Test failures or missing env vars in CI | 1. Check GitHub Actions run logs. 2. Verify all required secrets set in GitHub repo settings (see §5). 3. Run `uv run pytest tests/ -v` locally to reproduce. |
| Azure deployment step fails (`azure/webapps-deploy`) | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `_FRONTEND` secret missing/expired | 1. Download fresh publish profile from Azure Portal → App Service → Get publish profile. 2. Update GitHub secret. 3. Re-run the deployment workflow. |
| Tool 1–5 GitHub Action workflows fail | Missing `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` secrets | 1. Go to GitHub → Settings → Secrets. 2. Add/rotate the missing secret. 3. Re-run the failed workflow. |
| Claude returns non-JSON or malformed response in Tool 1 | Model output formatting issue | 1. Check workflow logs for `[DEBUG] First 500 chars of raw response`. 2. Retry the workflow — transient LLM formatting issues usually self-correct. 3. If persistent, check `claude-sonnet-4-6` model availability on Anthropic API. |
| CORS errors in browser | Frontend origin not in allow-list | 1. Check `api/main.py` `allow_origins` list. 2. Add the production frontend URL. 3. Redeploy API. |
| `httpx` SSL errors (certificate verify failed) | `verify=False` bypasses SSL — but if the target endpoint has broken TLS | 1. Confirm `verify=False` is intentional for the target environment. 2. [TODO: Is this a corporate proxy/self-signed cert environment? If so, configure `REQUESTS_CA_BUNDLE`.] |

---

## 4. Deployment Procedure

### Prerequisites

- Python 3.13 (local) / 3.12 (CI)
- `uv` package manager installed: `pip install uv` or `curl -Lsf https://astral.sh/uv/install.sh | sh`
- Azure CLI authenticated: `az login`
- All environment variables configured (see §5)

### Standard Deployment (via GitHub Actions — recommended)

```
1. Merge feature branch to `main` after PR approval.
2. GitHub Actions automatically runs the `Test & Deploy` workflow:
   a. `test` job: runs `uv run pytest tests/ -v`
   b. `deploy-api` job (on test pass): deploys to Azure App Service `training-bot-api`
   c. `deploy-frontend` job (on test pass): deploys to Azure App Service `training-bot-frontend`
3. Monitor the Actions run at:
   https://github.com/kylodeng/Insurance-Training-Bot-main/actions
4. Confirm deployment succeeded:
   - Check Azure App Service logs
   - Run health checks from §2
```

### Manual Deployment (break-glass)

```bash
# 1. Clone and set up
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install dependencies
uv sync

# 3. Export requirements for Azure
uv export --no-dev --format requirements-txt -o requirements.txt

# 4. Deploy API manually via Azure CLI
az webapp deployment source config-zip \
  --resource-group [TODO: resource group name] \
  --name training-bot-api \
  --src [TODO: zip package path]

# 5. Deploy Frontend manually
az webapp deployment source config-zip \
  --resource-group [TODO: resource group name] \
  --name training-bot-frontend \
  --src [TODO: zip package path]

# 6. Run post-deployment health checks (see §2)
```

### First-Time Ingestion (after fresh deployment)

```bash
# Must be run once after deployment or whenever PDFs are updated
# [TODO: Is there a POST /ingest endpoint? Confirm the exact route and auth requirements.]
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Or run locally against the data directory:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

### Rollback Steps

```bash
# Option A — Revert via GitHub (recommended)
# 1. Identify the last good commit SHA
git log --oneline main

# 2. Revert the bad commit
git revert <bad-commit-sha>
git push origin main
# GitHub Actions will automatically deploy the reverted version

# Option B — Azure App Service deployment slots / swap
# [TODO: Are staging slots configured on Azure App Service?]
az webapp deployment slot swap \
  --resource-group [TODO] \
  --name training-bot-api \
  --slot staging \
  --target-slot production

# Option C — Azure rollback to previous deployment
az webapp deployment list \
  --resource-group [TODO] \
  --name training-bot-api
az webapp deployment rollback \
  --resource-group [TODO] \
  --name training-bot-api \
  --deployment-id [previous-deployment-id]
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to Monitor | Alert Threshold |
|---|---|---|
| App Service HTTP 5xx errors | Azure Monitor → App Service → HTTP Server Errors | > 5 in 5 min |
| App Service HTTP response time | Azure Monitor → App Service → Average Response Time | > 10s average |
| App Service CPU % | Azure Monitor → App Service → CPU Percentage | > 80% sustained |
| App Service Memory % | Azure Monitor → App Service → Memory Working Set | > 85% |
| App Service restarts | Azure Monitor → App Service → Restarts | > 1 per hour |
| LLM API error rate | Application logs — filter for `401`, `429`, `500` from LLM provider | Any 401; > 5 × 429 per hour |
| Vector store not loaded | Application startup logs — filter for `No vector store found` | Any occurrence |
| GitHub Actions workflow failures | GitHub Actions → Notifications | Any failure on `main` |

### Log Locations

| Log Source | Location | Key Patterns to Filter |
|---|---|---|
| FastAPI application logs | Azure App Service → Log stream, or Kudu console | `ERROR`, `WARNING`, `Vector store`, `LLM`, `ingest` |
| GitHub Actions CI/CD logs | `https://github.com/kylodeng/Insurance-Training-Bot-main/actions` | `FAIL`, `Error`, `exit code 1` |
| AI Tool workflow logs | Same GitHub Actions URL | `[DEBUG]`, `Could not parse Claude response`, `JSON parse error` |
| Azure deployment logs | Azure Portal → App Service → Deployment Center | `Failed`, `Error` |

### Log Setup

```bash
# Stream live logs from Azure App Service
az webapp log tail \
  --resource-group [TODO: resource group] \
  --name training-bot-api

# Download logs
az webapp log download \
  --resource-group [TODO: resource group] \
  --name training-bot-api \
  --log-file ./api-logs.zip
```

### Alerting Configuration

[TODO: Are Azure Monitor alerts or Application Insights configured? If not, set up alerts for HTTP 5xx > threshold and App Service restart events.]

[TODO: Is there a PagerDuty / OpsGenie / Teams webhook configured for GitHub Actions failures?]

### Required GitHub Secrets

Confirm all the following secrets are set under **GitHub → Settings → Secrets and variables → Actions**:

| Secret Name | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API (all 5 AI tools) |
| `GH_TOKEN` | GitHub API access (AI tools write to output repo) |
| `SENDGRID_API_KEY` | Email notifications from AI tools |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Deploy `training-bot-api` to Azure |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Deploy `training-bot-frontend` to Azure |

### Required Application Environment Variables (Azure App Service → Configuration)

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | _(none)_ | LLM provider API key (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | LLM API base URL |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name |
| `SHOW_TOOL_CALLS` | No | `true` | Stream tool call events to UI |
| [TODO: embedding model API key] | [TODO] | — | Key for Voyage AI or other embedding provider |

---

## 6. Escalation Path

### Tier 1 — Self-service (On-call Engineer)

1. Check health checks (§2).
2. Review Azure App Service log stream.
3. Consult common failure scenarios (§3).
4. Attempt resolution steps in §3 and §4.

### Tier 2 — Engineering Team

[TODO: Fill in — who is the primary on-call engineer? Name and contact.]

[TODO: Fill in — what is the team Slack/Teams channel for incidents?]

[TODO: Fill in — what is the incident ticketing system (Jira project key, ServiceNow queue)?]

### Tier 3 — External Dependencies

| Dependency | Escalation Contact |
|---|---|
| Azure App Service outage | Azure Support — [TODO: support plan tier and portal link] |
| OpenRouter / LLM provider outage | [TODO: Check provider status page; file support ticket] |
| Anthropic API (Claude) outage | https://status.anthropic.com |
| SendGrid email failures | https://status.sendgrid.com |
| GitHub Actions outage | https://www.githubstatus.com |

### Escalation Owner

[TODO: Who is the solution owner / business sponsor accountable for this service?]

[TODO: What is the SLA / RTO / RPO for this service?]

---

## 7. Useful Commands

### Local Development

```bash
# Clone repo
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# Install all dependencies
uv sync

# Copy and populate environment variables
cp .env.example .env    # [TODO: confirm .env.example exists]
# Edit .env with: API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, SHOW_TOOL_CALLS

# Start FastAPI backend
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### Ingestion

```bash
# Ingest all PDFs in the default data directory
uv run python