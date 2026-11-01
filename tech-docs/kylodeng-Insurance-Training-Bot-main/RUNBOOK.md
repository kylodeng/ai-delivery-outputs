# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

> **Version:** 1.0 | **Last updated:** [TODO: insert date] | **Owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training environment for insurance sales agents in a Hong Kong context. It operates in two modes: **Teacher mode**, which offers an interactive, streaming chat experience where an LLM coach guides agents through product knowledge, sales techniques, and discovery questioning; and **Roleplay mode**, where the agent practices client conversations against a randomly generated customer persona drawn from realistic Hong Kong profiles. The system is backed by a Retrieval-Augmented Generation (RAG) pipeline — insurance product PDFs (Sun Life brochures, hospital network lists, etc.) are ingested, chunked, annotated with an LLM, embedded via a vector store (ChromaDB or FAISS, with optional Pinecone), and served to a LangGraph agent via eight specialised tools (product search, hospital network lookup, plan comparison, exclusions lookup, claims procedure lookup, etc.). The backend is deployed to Azure App Service (`training-bot-api`) with a separate Azure App Service for the frontend (`training-bot-frontend`), and CI/CD is managed through GitHub Actions. A suite of five AI delivery tools (code review, tech docs, business docs, auto-testing, UAT facilitation) runs as separate GitHub Actions workflows, all powered by the Anthropic Claude API.

---

## 2. Health Checks

Run these checks in order to confirm the service is operating normally.

### 2.1 API Service

```bash
# Confirm the FastAPI process is responding
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/

# FastAPI auto-generated docs endpoint (returns 200 if app is up)
curl -s https://training-bot-api.azurewebsites.net/docs | grep -c "Insurance Agent Trainer"

# Check lifespan startup — vector store loaded log
# Expected in App Service log stream:
# INFO:api.main:Vector store loaded (N products)
```

### 2.2 Vector Store

```bash
# Trigger an ingest (if store not yet populated) — check for 200
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Confirm known products are returned
curl -s https://training-bot-api.azurewebsites.net/  # [TODO: confirm if a /products or /health endpoint exists]
```

> [TODO: Is there a dedicated `/health` or `/readiness` endpoint? None was found in the code — recommend adding one.]

### 2.3 Frontend

```bash
# Check frontend App Service is responding
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
# Expected: 200
```

### 2.4 Static Document Serving

```bash
# Confirm PDFs are served via /docs mount
curl -s -o /dev/null -w "%{http_code}" \
  https://training-bot-api.azurewebsites.net/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
# Expected: 200
```

### 2.5 Sessions Persistence

```bash
# Check sessions file exists and is valid JSON
# On the App Service instance (Kudu console or SSH):
cat /home/site/wwwroot/data/sessions.json | python3 -m json.tool | head -20
```

### 2.6 GitHub Actions Workflows

- Navigate to **Actions** tab in the repository.
- Confirm `Test & Deploy` workflow last run is green on `main`.
- Check `Tool 2 — Tech Documentation` last Sunday run succeeded.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all routes at startup | Vector store not loaded; `data/sessions.json` missing or corrupt | SSH into App Service; run `POST /ingest` to rebuild vector store; delete and recreate `sessions.json` as `{}` |
| `WARNING: No vector store found — run POST /ingest first` in logs | First deployment or data directory not persisted across restarts | Run `POST /ingest` endpoint; confirm `data/` directory is on a persistent Azure storage mount [TODO: confirm if Azure persistent storage is configured] |
| LLM responses are empty or agent returns no tool results | `API_KEY` or `OPENAI_URL_BASE` env var missing/wrong; OpenRouter quota exceeded | Check App Service Application Settings; verify key at OpenRouter dashboard; check `OPENAI_MODEL` is a valid model name |
| `SSLError` / `httpx.ConnectError` in logs | `verify=False` is set but proxy/firewall blocking outbound HTTPS | Check Azure outbound networking rules; confirm OpenRouter/Anthropic endpoints are whitelisted |
| Sessions not persisting across restarts | `data/sessions.json` stored on ephemeral filesystem | Mount an Azure Files share to `data/`; or migrate session store to Azure Blob / Redis |
| PDF files return `404` from `/docs` endpoint | `data/` directory not present or not mounted at expected path | Confirm `_DATA_DIR` resolves correctly on the App Service; check `StaticFiles` mount in `main.py` |
| GitHub Actions `Test & Deploy` fails at `Run tests` step | Test failures or missing env vars in CI | Check `pytest` output in Actions log; confirm all required secrets are set in repo Settings → Secrets |
| GitHub Actions AI tools (Tool 1–5) fail | `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` secret missing or expired | Rotate and re-add secrets in repo Settings → Secrets → Actions |
| Claude returns non-JSON / JSON parse error in Tool 1 or Tool 5 | Model output formatting variance | Check `[DEBUG]` lines in Actions log; re-run workflow manually; the `extract_json` function has fallback logic but very large diffs may cause truncation |
| Roleplay customer profile generation fails | LLM call fails or returns malformed JSON | Check `generate_profile` function logs; confirm `API_KEY` is valid; retry the session creation |
| Embedding batch fails mid-ingest | Rate limit on embedding provider (Voyage AI free tier: 3 RPM) | Reduce `batch_size` in `embed_chunks` call; increase `batch_delay`; or upgrade to paid tier |
| `ModuleNotFoundError` on startup | Dependency not installed; `uv sync` not run | SSH Kudu console; run `pip install -r requirements.txt`; redeploy via GitHub Actions |
| Annotation LLM calls slow or timing out during ingest | Large PDFs; cold LLM endpoint | Increase HTTP timeout in `_build_ingest_llm`; pre-annotate locally and commit `.annot.json` sidecar files |
| CORS errors in browser | Frontend origin not in `allow_origins` list | Add the production frontend URL to `allow_origins` in `main.py` and redeploy |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- Azure App Service instances `training-bot-api` and `training-bot-frontend` provisioned.
- GitHub repository secrets set:

| Secret Name | Purpose |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Azure App Service publish profile for API |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Azure App Service publish profile for frontend |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key (AI tooling workflows) |
| `GH_TOKEN` | GitHub PAT (AI tooling workflows) |
| `SENDGRID_API_KEY` | SendGrid email (AI tooling workflows) |

- Application environment variables set in Azure App Service → Configuration → Application Settings:

| Variable | Required | Notes |
|---|---|---|
| `API_KEY` | Yes | OpenRouter (or Anthropic) API key |
| `OPENAI_URL_BASE` | Yes | Default: `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | Yes | Default: `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | `true` to stream tool call events |

### 4.2 Standard Deployment (via GitHub Actions)

```
1. Create a feature branch and open a pull request to `main`.
2. The `Test & Deploy` workflow triggers:
   a. `test` job: runs `uv sync` and `pytest tests/ -v`.
   b. On PR: only `test` runs. Do NOT merge if tests fail.
3. Merge the pull request to `main` (squash or merge commit).
4. The `Test & Deploy` workflow triggers again on push to `main`:
   a. `test` job runs again.
   b. `deploy-api` job: exports `requirements.txt` via `uv export`,
      deploys to `training-bot-api` Azure App Service.
   c. `deploy-frontend` job: deploys to `training-bot-frontend` Azure App Service.
5. Monitor the Actions run until all three jobs show green.
6. Run health checks (Section 2) to confirm deployment.
7. If this is a release, create a version tag: git tag v<version> && git push --tags
   This triggers Tool 3 (Business Documentation) workflow automatically.
```

### 4.3 Manual Deployment (emergency / hotfix)

```bash
# 1. Generate requirements.txt locally
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Zip the application
zip -r deploy.zip . -x "*.git*" -x "__pycache__/*" -x ".venv/*"

# 3. Deploy via Azure CLI
az webapp deploy \
  --resource-group <resource-group> \       # [TODO: confirm resource group name]
  --name training-bot-api \
  --src-path deploy.zip \
  --type zip

# 4. Restart the app
az webapp restart --resource-group <resource-group> --name training-bot-api

# 5. Tail logs
az webapp log tail --resource-group <resource-group> --name training-bot-api
```

### 4.4 Post-Deployment: Ingest Documents

After first deployment or if the vector store is missing:

```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
# Monitor App Service log stream for:
# INFO:api.main:Vector store loaded (N products)
```

### 4.5 Rollback Procedure

**Option A — Revert via GitHub (preferred):**

```bash
# 1. Identify the last good commit SHA from Actions history
git revert <bad-commit-sha> --no-edit
git push origin main
# GitHub Actions will automatically redeploy the reverted code.
```

**Option B — Azure deployment slot swap (if slots configured):**

```bash
# [TODO: confirm if deployment slots are configured on the App Service]
az webapp deployment slot swap \
  --resource-group <resource-group> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

**Option C — Redeploy a specific tag:**

```bash
git checkout v<last-good-version>
uv export --no-dev --format requirements-txt -o requirements.txt
zip -r deploy.zip . -x "*.git*" -x "__pycache__/*" -x ".venv/*"
az webapp deploy --resource-group <resource-group> \
  --name training-bot-api --src-path deploy.zip --type zip
az webapp restart --resource-group <resource-group> --name training-bot-api
```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where to Find | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Monitoring → Metrics → Http5xx | > 5 errors/min |
| HTTP 4xx error rate | Azure App Service → Monitoring → Metrics → Http4xx | > 20 errors/min |
| Response time (avg) | Azure App Service → Monitoring → Metrics → AverageResponseTime | > 10 s (streaming expected to be longer) |
| CPU percentage | Azure App Service → Monitoring → Metrics → CpuPercentage | > 80% sustained |
| Memory working set | Azure App Service → Monitoring → Metrics → MemoryWorkingSet | > 80% of plan limit |
| GitHub Actions failure | GitHub Actions → [TODO: configure Slack/email notifications] | Any failed run on `main` |

> [TODO: Are Azure Monitor alerts or Application Insights configured? Recommend enabling Application Insights on both App Services.]

### 5.2 Key Log Entries to Watch

Monitor via **Azure App Service → Log stream** or **Log Analytics** (if configured):

```
# Healthy startup
INFO:api.main:Vector store loaded (N products)

# Ingest success
INFO:core.ingest:index saved (N chunks)

# Embedding rate limit warning
INFO:core.ingest:rate-limit pause Xs ...

# Vector store missing (critical — ingest required)
WARNING:api.main:No vector store found — run POST /ingest first.

# LLM call errors (watch for volume spikes)
ERROR:api.agent:...

# Session load on startup
# [TODO: confirm if session load logs are emitted]
```

### 5.3 GitHub Actions Workflow Health

| Workflow | Schedule | What to Watch |
|---|---|---|
| `Test & Deploy` | Every push/PR to `main` | Must be green before any merge |
| `Tool 1 — Code Review` | Every Monday 08:00 UTC + every PR | Claude JSON parse errors in output |
| `Tool 2 — Tech Documentation` | Every Sunday 06:00 UTC + push to `main` | Output repo file write failures |
| `Tool 4 — Auto Testing` | Every Wednesday 07:00 UTC + PR | Test generation errors |

### 5.4 RAG / Vector Store Health

```bash
# Check number of indexed products after ingest
# Look for this line in logs:
# INFO:api.main:Vector store loaded (N products)
# N should match the number of PDFs in data/Insurance-product-info/

# If N = 0 or the warning appears, re-run ingest:
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

### 5.5 Recommended Alerts (to be configured)

> [TODO: Set up the following in Azure Monitor or GitHub Actions notifications:]

- App Service HTTP 5xx spike → PagerDuty/Slack alert
- App Service restart detected → Slack alert
- GitHub Actions `Test & Deploy` failure → email to team
- `sessions.json` size > 50 MB → investigate session cleanup

---

## 6. Escalation Path

| Level | Condition | Contact |
|---|---|---|
| L1 — On-call engineer | Service down, HTTP 5xx spike, vector store missing | [TODO: on-call rotation contact / Slack channel] |
| L2 — Backend developer | LLM integration failures, RAG quality issues, session corruption | [TODO: backend developer name / email] |
| L3 — Azure infrastructure | App Service outage, networking, persistent storage issues | [TODO: Azure admin / cloud team contact] |
| L3 — External dependencies | OpenRouter API outage, Anthropic API outage, SendGrid outage | Check status pages; failover model in `OPENAI_MODEL` env var |
| Product Owner | Business-impacting issues, data sensitivity concerns | [TODO: product owner name / email] |
| Security | Suspected data leak, API key compromise | [TODO: security team contact]; immediately rotate `API_KEY`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` |

**Status pages to check:**
- Azure: https://status.azure.com
- OpenRouter: https://status.openrouter.ai [TODO: confirm URL]
- Anthropic: https://status.anthropic.com
- SendGrid: https://status.sendgrid.com

---

## 7. Useful Commands

### Application Management

```bash
# --- Azure CLI: App Service operations ---

# Restart API service
az webapp restart --resource-group <rg> --name training-bot-api

# Restart frontend service
az webapp restart --resource-group <rg> --name training-bot-frontend

# Tail live logs
az webapp log tail --resource-group <rg