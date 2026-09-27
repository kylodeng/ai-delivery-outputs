# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based AI training platform designed to help new insurance agents in the Hong Kong market master sales techniques, product knowledge, and customer handling. The system operates in two modes: **Teacher mode**, which provides an ongoing interactive coaching session powered by a LangGraph agent backed by a RAG (Retrieval-Augmented Generation) vector store of insurance product PDFs; and **Roleplay mode**, which simulates a realistic Hong Kong customer profile for the agent to practise on. A separate **Assessor agent** evaluates completed roleplay sessions across five dimensions including product knowledge accuracy. The backend (FastAPI + LangChain/LangGraph) is deployed to **Azure App Service** (`training-bot-api`), a frontend is deployed to a second Azure App Service (`training-bot-frontend`), and the underlying LLM is accessed via OpenRouter (default) or a compatible endpoint. CI/CD is managed via GitHub Actions, which also runs five AI-powered auxiliary workflows (code review, tech docs, business docs, auto testing, UAT facilitation) using Claude via Anthropic's API.

---

## 2. Health Checks

### 2.1 API Service

| Check | How to verify |
|---|---|
| API process is up | `curl -f https://training-bot-api.azurewebsites.net/docs` → expect HTTP 200 and FastAPI Swagger UI |
| Vector store loaded | Check startup logs for `Vector store loaded (N products)` — if absent, see **Failure Scenarios** below |
| LLM reachability | POST a minimal chat message and confirm a streamed response is returned |
| Sessions file present | Confirm `data/sessions.json` exists and is valid JSON |
| Static file serving | `curl -I https://training-bot-api.azurewebsites.net/docs/` → expect HTTP 200 |
| Frontend up | `curl -f https://training-bot-frontend.azurewebsites.net/` → expect HTTP 200 |

### 2.2 Azure Portal Checks

- **App Service → Overview**: Status = `Running`, no recent restarts
- **App Service → Log stream**: No unhandled exception tracebacks at startup
- **App Service → Health check**: [TODO: Is a `/health` endpoint configured in Azure App Service health check settings?]

### 2.3 GitHub Actions Checks

- Navigate to **Actions** tab → confirm the `Test & Deploy` workflow last run is green on `main`
- Confirm `deploy-api` and `deploy-frontend` jobs completed without error

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log: `No vector store found — run POST /ingest first.` | Vector store index file missing or not persisted to App Service disk | 1. SSH/Kudu into App Service. 2. Confirm `data/` directory exists. 3. Call `POST /ingest` endpoint (or run `python core/ingest.py` locally and redeploy). 4. Verify log now shows chunk count. |
| LLM returns 401 / 403 | `API_KEY` environment variable missing or expired on App Service | 1. Azure Portal → App Service → Configuration → Application Settings. 2. Verify `API_KEY` is set and matches the active OpenRouter/Anthropic key. 3. Restart the App Service. |
| LLM returns 429 Too Many Requests | Rate limit exceeded on OpenRouter or Anthropic free tier | 1. Check `OPENAI_URL_BASE` and `OPENAI_MODEL` settings. 2. Switch to a paid tier or a less-loaded model. 3. Reduce concurrent users or add request queuing. [TODO: Is there a retry/back-off strategy implemented?] |
| SSL verification errors in logs (`verify=False` warnings) | `httpx.Client(verify=False)` is set globally — certificate issue with upstream LLM endpoint | Confirm the `OPENAI_URL_BASE` host certificate is valid; if using a corporate proxy, add CA bundle via `HTTPX_SSL_CA_BUNDLE` env var. |
| `sessions.json` corrupt / parse error | Concurrent writes or incomplete shutdown | 1. SSH into App Service. 2. Back up then delete `data/sessions.json`. 3. Restart the service (sessions will be lost — this is expected in a stateless recovery). [TODO: Is there a backup/restore procedure for sessions?] |
| GitHub Actions `Test & Deploy` fails on `pytest` | Broken code pushed to `main`, or missing test dependencies | 1. Review the failing test output in the Actions log. 2. Fix the code, push a new commit. 3. Do NOT manually re-run the deploy jobs — fix the root cause first. |
| GitHub Actions deploy step fails with `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret error | Secret missing or expired in repository settings | 1. Azure Portal → App Service → Get publish profile. 2. GitHub → Settings → Secrets → update `AZURE_WEBAPP_PUBLISH_PROFILE_API` (or `_FRONTEND`). 3. Re-run the workflow. |
| Claude/AI tool workflows fail with `ANTHROPIC_API_KEY` error | Secret not set in GitHub repo | GitHub → Settings → Secrets → add `ANTHROPIC_API_KEY`. |
| RAG search returns irrelevant results | Vector store built from wrong or incomplete PDF set; chunk quality issues | 1. Verify PDFs are in `data/Insurance-product-info/`. 2. Check `.annot.json` sidecar files are current. 3. Delete vector store index and re-run `POST /ingest` with `llm` annotation enabled. |
| CORS errors in browser | Frontend origin not in the `allow_origins` list | Add the new origin to the `CORSMiddleware` list in `api/main.py`, redeploy. |
| Annotation sidecar `.annot.json` stale after PDF update | LLM annotation is cached — stale cache not invalidated | Delete the corresponding `.annot.json` file from `data/` and trigger re-ingestion. |
| `KeyError` on `ANTHROPIC_API_KEY` / `GH_TOKEN` / `SENDGRID_API_KEY` in CI scripts | Environment variable not injected into the workflow | Check the `env:` block in the relevant `tool*.yml` workflow file; add the secret in GitHub Settings. |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- Access to the Azure Portal for the subscription hosting `training-bot-api` and `training-bot-frontend`
- GitHub repository write access
- Python 3.13 + `uv` installed locally for local testing
- [TODO: Is there a staging/UAT App Service environment, or is `main` deployed directly to production?]

### 4.2 Standard Deployment (Automated via GitHub Actions)

```
Step 1 — Develop on a feature branch
  git checkout -b feature/my-change
  # make changes
  git push origin feature/my-change

Step 2 — Open a Pull Request to main
  - Tool 1 (Code Review) triggers automatically
  - Tool 4 (Auto Testing) triggers automatically
  - Review Claude's PR comment and address any CRITICAL/HIGH findings

Step 3 — Merge PR to main
  - GitHub Actions "Test & Deploy" workflow triggers
  - Job: test      → runs pytest (must pass)
  - Job: deploy-api      → deploys to training-bot-api Azure App Service
  - Job: deploy-frontend → deploys to training-bot-frontend Azure App Service

Step 4 — Verify deployment
  - Check Actions tab: all three jobs green
  - Run health checks (Section 2)
  - Confirm LLM responds in the UI
```

### 4.3 Manual Deployment (Break-Glass)

```bash
# Generate requirements.txt locally
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend manually
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

[TODO: What is the Azure Resource Group name?]

### 4.4 Vector Store Re-ingestion (after PDF updates)

```bash
# Locally
cd Insurance-Training-Bot-main
uv sync
uv run python core/ingest.py --pdf-dir data/Insurance-product-info/

# Or via API endpoint (if exposed)
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

### 4.5 Rollback Procedure

```
Option A — GitHub Actions rollback (preferred)
  Step 1: Identify the last good commit SHA from the Actions run history
  Step 2: git revert <bad-commit-sha> --no-edit
  Step 3: git push origin main
  Step 4: GitHub Actions redeploys automatically
  Step 5: Verify health checks pass

Option B — Azure App Service deployment slots (if configured)
  Step 1: Azure Portal → App Service → Deployment slots
  Step 2: Swap active slot back to previous slot
  [TODO: Are deployment slots configured for zero-downtime swap?]

Option C — Azure Portal manual rollback
  Step 1: Azure Portal → App Service → Deployment Center → Deployments
  Step 2: Select a previous successful deployment
  Step 3: Click "Redeploy"
  Step 4: Verify health checks pass
```

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

| Log location | What to watch |
|---|---|
| Azure App Service → Log Stream (live) | Startup errors, unhandled exceptions, `WARNING` level messages |
| Azure App Service → Diagnose and solve problems | Crash analysis, memory/CPU spikes |
| GitHub Actions log | CI/CD failures, test failures, secret errors |

**Key log messages to alert on:**

```
# Vector store missing at startup (ingestion needed)
"No vector store found — run POST /ingest first."

# LLM annotation failure (non-fatal, fallback used)
"[ingest] annotation failed for <file>: <error> — using raw chunker"

# Embedding batch progress
"[ingest] embedding batch N–M / total …"

# Successful load
"Vector store loaded (N products)"
```

### 5.2 Azure Monitor Metrics

| Metric | Threshold to alert | Action |
|---|---|---|
| HTTP 5xx response rate | > 1% over 5 min | Investigate logs; check LLM API key validity |
| HTTP 4xx response rate | > 5% over 5 min | Check CORS settings; verify client requests |
| Average response time | > 30 s | LLM latency issue; check OpenRouter status |
| CPU percentage | > 80% sustained | Scale up App Service plan |
| Memory working set | > 80% | Check for session/vector store memory leak; restart |
| App Service restarts | Any unexpected restart | Check crash logs immediately |

[TODO: Are Azure Monitor alerts configured? If not, set them up in Azure Portal → App Service → Alerts.]

### 5.3 LLM / External API Monitoring

| Service | How to check status |
|---|---|
| OpenRouter | https://status.openrouter.ai |
| Anthropic (Claude) | https://status.anthropic.com |
| SendGrid | https://status.sendgrid.com |

### 5.4 Key Metrics to Track

- **Session count**: Monitor growth of `data/sessions.json` — large file can indicate memory pressure
- **Vector store chunk count**: Logged at startup — regression means re-ingestion needed
- **LLM token usage**: [TODO: Is token usage tracked? Consider adding logging in `call_claude()` and `_llm` calls]
- **RAG source hit rate**: Evaluate if the agent is returning `[S1]`, `[S2]` citations — absence may indicate poor retrieval

### 5.5 Alerting Configuration

[TODO: Configure Azure Monitor action groups with email/Teams/PagerDuty alerts to the on-call engineer for 5xx spikes and app restarts.]

---

## 6. Escalation Path

```
Level 1 — On-call Engineer
  Contact: [TODO: name, email, Teams/Slack handle, phone]
  Responsibilities: Health checks, restart service, check logs, basic triage
  Response time: [TODO: define SLA]

Level 2 — Backend Developer / Tech Lead
  Contact: [TODO: name, email]
  Responsibilities: Code-level issues, LLM prompt failures, RAG retrieval problems
  Response time: [TODO: define SLA]

Level 3 — DevOps / Platform Engineer
  Contact: [TODO: name, email]
  Responsibilities: Azure infrastructure, App Service configuration, deployment pipeline
  Response time: [TODO: define SLA]

Level 4 — Product Owner / Stakeholder
  Contact: kylo.deng@capco.com (inferred from workflow config)
  Responsibilities: Business decision on rollback, comms to affected users
  Escalate when: Data loss risk, extended outage > [TODO: define threshold]

External Support:
  Azure Support: https://portal.azure.com/#blade/Microsoft_Azure_Support/HelpAndSupportBlade
  OpenRouter Support: https://openrouter.ai/docs
  Anthropic Support: https://console.anthropic.com/support
  SendGrid Support: https://support.sendgrid.com
```

---

## 7. Useful Commands

### 7.1 Local Development

```bash
# Clone and install
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
uv sync

# Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Required vars — edit .env:
# API_KEY=<openrouter-or-anthropic-key>
# OPENAI_URL_BASE=https://openrouter.ai/api/v1
# OPENAI_MODEL=openai/gpt-oss-20b:free
# SHOW_TOOL_CALLS=true

# Run the API server
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### 7.2 Ingestion

```bash
# Ingest all PDFs from the default directory
uv run python core/ingest.py --pdf-dir data/Insurance-product-info/ --verbose

# Ingest with custom max word size per chunk
uv run python core/ingest.py --pdf-dir data/Insurance-product-info/ --max-words 300

# Trigger via API
curl -X POST http://localhost:8000/ingest
```

### 7.3 Health & Diagnostics

```bash
# Check API is up (local)
curl -f http://localhost:8000/docs

# Check API is up (production)
curl -f https://training-bot-api.azurewebsites.net/docs

# Check frontend (production)
curl -f https://training-bot-frontend.azurewebsites.net/

# View Azure App Service logs (requires Azure CLI)
az webapp log tail \
  --resource-group <rg-name> \
  --name training-bot-api

# List sessions (if endpoint is exposed)
curl http://localhost:8000/sessions
```

### 7.4 Azure App Service Management

```bash
# Restart the API app service
az webapp restart --resource-group <rg-name> --name training-bot-api

# Restart the frontend app service
az webapp restart --resource-group <rg-name> --name training-bot-frontend

# View application settings
az webapp config appsettings list \
  --resource-group <rg-name> \
  --name training-bot-api \
  --output table

# Set / update an environment variable
az webapp config appsettings set \
  --resource-group <rg-name> \
  --name training-bot-api \
  --settings API_KEY="new-key-value"

# SSH into App Service (Kudu)
az webapp ssh --resource-group <rg-name> --name training-bot-api
```

### 7.5 Dependency Management

```bash
# Export pinned requirements (used by deploy workflow)
uv export --no-dev --format requirements-txt -