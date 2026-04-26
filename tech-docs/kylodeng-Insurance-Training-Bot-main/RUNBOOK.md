# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that helps new insurance agents master sales skills and product knowledge through two interactive modes: a **Teacher Mode** (ongoing streamed chat with a LangGraph agent that coaches the trainee using a RAG knowledge base of insurance product PDFs) and a **Roleplay/Assessment Mode** (the agent simulates a Hong Kong customer profile and a separate Assessor agent evaluates the trainee's performance). The backend is written in Python, uses LangChain + LangGraph for agent orchestration, an OpenAI-compatible LLM endpoint (defaulting to OpenRouter or Anthropic via environment configuration), and a local vector store (FAISS or Chroma) built from ingested insurance product PDFs. Sessions are persisted to `data/sessions.json`. The application is deployed to **Azure App Service** (API: `training-bot-api`, Frontend: `training-bot-frontend`) via GitHub Actions CI/CD on every merge to `main`.

---

## 2. Health Checks

### API Service
```bash
# Basic liveness — expect HTTP 200 or FastAPI default root response
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/

# Check docs endpoint (static file mount)
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/docs/
```

### Vector Store
```bash
# POST to ingest endpoint to verify store status
# [TODO: Is there a GET /status or /health endpoint? None found in code — recommend adding one]
curl -X POST https://training-bot-api.azurewebsites.net/ingest
# Expected: vector store loads on startup; logs should show:
# "Vector store loaded (N products)"
# Warning if missing: "No vector store found — run POST /ingest first."
```

### Session Persistence
```bash
# Verify sessions file exists and is readable
ls -lh data/sessions.json
python -c "import json; d=json.load(open('data/sessions.json')); print(f'{len(d)} sessions')"
```

### LLM Connectivity
```bash
# Confirm the configured LLM endpoint is reachable
curl -s -o /dev/null -w "%{http_code}" "$OPENAI_URL_BASE"
# Expected: 200 or 401 (auth required) — anything else indicates connectivity issues
```

### Frontend
```bash
# [TODO: What is the frontend technology? No Vite/React source files were visible — confirm URL]
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
```

### GitHub Actions CI
- Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`
- Confirm the **Test & Deploy** workflow is green on `main`

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `"No vector store found — run POST /ingest first."` in logs on startup | Vector store index files missing from `data/` directory (first deploy, or store was deleted) | Run `POST /ingest` via the API or execute `python core/ingest.py` locally; ensure PDF files exist under `data/Insurance-product-info/` |
| Agent returns wrong or hallucinated product details | RAG tools returning no hits, or vector store is stale/empty | Re-ingest PDFs: `POST /ingest`; verify PDF annotation `.annot.json` files are present; check embedding model connectivity |
| `500 Internal Server Error` on chat endpoints | LLM API key invalid/expired, or OpenRouter/Anthropic endpoint unreachable | Check `API_KEY` env var is set and valid; verify `OPENAI_URL_BASE`; check LLM provider status page; review Azure App Service logs |
| SSL verification errors (`verify=False` workaround active) | Self-signed cert or corporate proxy intercepting HTTPS to LLM endpoint | Confirm network path from Azure App Service to LLM endpoint; add trusted CA bundle if required; note `verify=False` is a security risk [TODO: confirm if this is intentional for Azure egress] |
| Sessions lost after restart | `data/sessions.json` not persisted (ephemeral Azure App Service filesystem) | Mount an Azure Files share to `data/` directory; or migrate session storage to Azure Blob/Cosmos DB |
| CORS errors in browser | Frontend origin not listed in `allow_origins` in `main.py` | Add the production frontend URL to the `CORSMiddleware` `allow_origins` list and redeploy |
| `KeyError: 'ANTHROPIC_API_KEY'` in GitHub Actions | Secret not configured in repository settings | Add `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` to GitHub Actions secrets |
| Deployment fails at `azure/webapps-deploy@v3` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret missing or expired | Regenerate publish profile from Azure Portal → App Service → Get publish profile; update GitHub secret |
| PDF ingestion produces 0 chunks | PDF is image-only/scanned, or `pdfplumber` cannot extract text | Check `.annot.json` sidecar — if `relevant: false` for all pages, the PDF was correctly filtered; for scanned docs, add OCR preprocessing [TODO: OCR is not currently implemented] |
| `pytest` failures in CI blocking deployment | Test regression introduced in PR | Review test output in Actions log; do not merge until green; run `uv run pytest tests/ -v` locally |
| LLM rate limit / quota exceeded | Too many concurrent requests or free-tier model limit hit | Reduce concurrency; switch to a paid model tier; implement request queuing [TODO: no rate limiting middleware found in code] |
| Source citations (`[[S1]]`) missing in teacher responses | `reset_sources()` not called before request, or context var not propagating across async tasks | Check `api/rag_tools.py` — verify `reset_sources()` is called at the start of each streaming request handler |
| `data/sessions.json` corruption / invalid JSON | Concurrent write conflict (no file locking observed in code) | Restore from backup; implement file locking or migrate to a proper database [TODO: no write locking found — race condition risk under concurrent load] |

---

## 4. Deployment Procedure

### Prerequisites
- Azure CLI authenticated with access to the App Service resource group
- GitHub repository secrets configured:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `ANTHROPIC_API_KEY`
  - `GH_TOKEN`
  - `SENDGRID_API_KEY`
- `uv` installed locally for dependency management

### Standard Deployment (Automated via GitHub Actions)

**Step 1:** Merge a pull request to `main` (ensure all PR checks pass).

**Step 2:** The `Test & Deploy` workflow triggers automatically:
1. Runs `uv run pytest tests/ -v` — must be green to proceed
2. On success, exports `requirements.txt` via `uv export`
3. Deploys to `training-bot-api` Azure App Service
4. Deploys to `training-bot-frontend` Azure App Service

**Step 3:** Monitor the Actions run at:
```
https://github.com/kylodeng/Insurance-Training-Bot-main/actions
```

**Step 4:** Verify health checks (Section 2) after deployment completes.

**Step 5:** If the vector store is being deployed fresh (first deploy or new environment):
```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```
Confirm logs show `"Vector store loaded (N products)"`.

---

### Manual / Emergency Deployment

```bash
# 1. Clone and install
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
uv sync

# 2. Export requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# 3. Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 4. Deploy frontend manually
az webapp deploy \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```
> [TODO: What is the Azure resource group name?]
> [TODO: Is there a separate frontend build step (e.g. `npm run build`) before deployment?]

---

### Rollback Procedure

**Option A — Revert via Git (preferred):**
```bash
# 1. Identify the last known-good commit
git log --oneline -10

# 2. Revert the bad commit
git revert <bad-commit-sha>
git push origin main
# GitHub Actions will auto-deploy the reverted commit
```

**Option B — Azure Deployment Slots swap:**
```bash
# [TODO: Confirm if deployment slots are configured on the Azure App Service]
az webapp deployment slot swap \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

**Option B — Azure manual rollback to previous ZIP:**
```bash
# List deployment history
az webapp deployment list \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api

# Redeploy a specific deployment ID
az webapp deployment source config-zip \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api \
  --src <previous-package.zip>
```

**Step after any rollback:** Re-run health checks (Section 2) and confirm agent responses are correct.

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to Monitor | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Monitoring → Metrics → Http5xx | > 1% of requests |
| HTTP response time (P95) | Azure App Service → Monitoring → Metrics → HttpResponseTime | > 10 seconds |
| Instance CPU usage | Azure App Service → Monitoring → Metrics → CpuPercentage | > 80% sustained |
| Memory usage | Azure App Service → Monitoring → Metrics → MemoryWorkingSet | > 80% of plan limit |
| App Service availability | Azure App Service → Monitoring → Availability | < 99% |
| LLM API error rate | Application logs — grep for `LLM` errors or `500` responses | Any sustained errors |

### Log Streams

```bash
# Stream live logs from Azure App Service (API)
az webapp log tail \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api

# Stream live logs from frontend
az webapp log tail \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-frontend
```

### Key Log Patterns to Watch

```
# Healthy startup
"Vector store loaded (N products)"

# Warning — needs action
"No vector store found — run POST /ingest first."

# Error patterns to alert on
ERROR
"KeyError"
"ConnectionError"
"timeout"
"500"
```

### Application Logging

- Logging is configured at `INFO` level via `logging.basicConfig(level=logging.INFO)` in `main.py`
- Logger name: `__name__` (per-module)
- Ingestion pipeline logs prefixed with `[ingest]`
- RAG tool logs via `logger = logging.getLogger(__name__)` in `rag_tools.py`

> [TODO: Is Azure Application Insights configured? No instrumentation key found in the code — strongly recommended for production tracing]
> [TODO: Are there any uptime monitors (e.g. Azure Monitor, Pingdom) configured?]
> [TODO: Are SendGrid email alerts configured for service failures? The shared.py framework has email capability but it's used for AI tool outputs, not ops alerting]

### GitHub Actions Health

- Monitor workflow runs at `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`
- Scheduled workflows that need monitoring:
  - **Tool 1 — Code Review**: Mondays 08:00 UTC
  - **Tool 2 — Tech Docs**: Sundays 06:00 UTC
  - **Tool 4 — Auto Testing**: Wednesdays 07:00 UTC

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in name and contact] | Service down, 5xx spike, failed deployment |
| L2 | Tech Lead | [TODO: fill in name and contact] | L1 cannot resolve within 30 minutes; data loss risk |
| L3 | Platform / Cloud Team | [TODO: fill in Azure team contact] | Azure App Service infrastructure issues, networking, IAM |
| L4 | LLM Provider Support | [TODO: OpenRouter / Anthropic support URL] | LLM API outage, quota exhaustion |
| Business | Solution Owner | kylo.deng@capco.com (inferred from config) | Business impact, compliance concern, data breach |

> [TODO: Define SLA/SLO targets — e.g. P1 response time, RTO, RPO]
> [TODO: Is there an incident management tool (PagerDuty, Opsgenie)?]
> [TODO: Is there a Slack/Teams channel for ops alerts?]

---

## 7. Useful Commands

### Local Development

```bash
# Install dependencies
uv sync

# Run the FastAPI backend locally
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v

# Run with specific test
uv run pytest tests/test_<module>.py -v -k "test_name"
```

### Vector Store / Ingestion

```bash
# Ingest PDFs from the default data directory
python core/ingest.py

# Ingest with verbose output
python core/ingest.py --verbose

# Ingest from a custom directory
python core/ingest.py --pdf-dir /path/to/pdfs

# Trigger ingest via API (production)
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

### Session Management

```bash
# View current sessions
python -c "
import json
sessions = json.load(open('data/sessions.json'))
print(f'Total sessions: {len(sessions)}')
for sid, s in list(sessions.items())[:5]:
    print(f'  {sid}: mode={s.get(\"mode\")}, messages={len(s.get(\"messages\",[]))}')
"

# Backup sessions before risky operations
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%d_%H%M%S)

# Clear all sessions (destructive — backup first)
echo '{}' > data/sessions.json
```

### Azure App Service

```bash
# Check App Service status
az webapp show \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api \
  --query "state"

# Restart App Service
az webapp restart \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api

# Stream live logs
az webapp log tail \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api

# Show environment variables (App Settings)
az webapp config appsettings list \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api

# Update an environment variable
az webapp config appsettings set \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api \
  --settings API_KEY="<new-value>"
```

### GitHub Actions — Manual Triggers

```bash
# Manually trigger code review via GitHub CLI
gh workflow run tool1_code_review.yml \
  --repo kylodeng/Insurance-Training-Bot-main \
  -f review_mode=repo

# Manually trigger tech docs generation
gh workflow run tool2_tech_docs.yml \
  --repo kyl