# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that helps new insurance agents in Hong Kong learn product knowledge and sales techniques through two modes: a **Teacher mode** (ongoing streamed chat backed by a LangGraph agent with RAG tools) and a **Roleplay/Assessment mode** (one-shot simulated customer conversations followed by automated performance assessment). The backend serves a REST API at `http://localhost:8000`, exposes static insurance PDF documents under `/docs`, and relies on a vector store (Chroma or FAISS) pre-populated by an ingestion pipeline that annotates and chunks PDFs from the `data/Insurance-product-info/` directory. The LLM gateway is configured via environment variables and currently points to OpenRouter or a compatible OpenAI-API endpoint. CI/CD is handled by GitHub Actions, deploying both API and frontend to **Azure App Service** (`training-bot-api` and `training-bot-frontend`) on every push to `main`.

---

## 2. Health Checks

### API Process

```bash
# Is the FastAPI process running?
curl -s http://localhost:8000/docs | grep -q "Insurance Agent Trainer" && echo "UP" || echo "DOWN"

# Azure App Service health (replace with actual URL)
curl -s https://training-bot-api.azurewebsites.net/docs | grep -q "Insurance Agent Trainer" && echo "UP" || echo "DOWN"
```

### Vector Store

```bash
# POST /ingest status — vector store must be pre-loaded
curl -s http://localhost:8000/ingest -X POST | jq .
# Expected: 200 OK with chunk count, or message "Vector store already loaded"
```

### Session State

```bash
# Confirm sessions endpoint is accessible
curl -s http://localhost:8000/sessions | jq .
# Expected: JSON array (may be empty [])
```

### LLM Connectivity

```bash
# Verify the LLM backend is reachable (replace URL with actual OPENAI_URL_BASE)
curl -s -H "Authorization: Bearer $API_KEY" \
  "${OPENAI_URL_BASE}/models" | jq '.data[0].id'
# Expected: a non-empty model ID string
```

### Static File Serving

```bash
# Verify PDF docs are accessible
curl -o /dev/null -s -w "%{http_code}" \
  http://localhost:8000/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
# Expected: 200
```

### GitHub Actions (CI/CD pipeline)

- Navigate to `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`
- Confirm **Test & Deploy** workflow shows green on `main`.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `500 Internal Server Error` on any chat endpoint | LLM API key invalid or quota exceeded | 1. Check `API_KEY` env var is set and not expired. 2. Check OpenRouter/LLM provider dashboard for quota. 3. Rotate key and restart app service. |
| `"No vector store found — run POST /ingest first"` in startup logs | Vector store not built or `data/` directory missing | 1. Ensure PDFs are present in `data/Insurance-product-info/`. 2. Run `POST /ingest` via curl or Swagger UI. 3. Confirm `chroma_db/` or FAISS index directory is written. |
| Sessions lost after restart | `data/sessions.json` missing or unwritable | 1. Check file permissions on `data/sessions.json`. 2. Verify the `data/` directory is persisted (not ephemeral in Azure App Service). 3. Enable persistent storage on Azure App Service. |
| Streaming response hangs / never completes | LLM provider timeout or SSL verification disabled (`verify=False`) causing silent failure | 1. Check `OPENAI_URL_BASE` is reachable from the App Service. 2. Check LLM provider status page. 3. Review `httpx` client logs for SSL/TLS errors. 4. [TODO: confirm whether SSL is intentionally disabled in production] |
| `KeyError: 'ANTHROPIC_API_KEY'` in GitHub Actions | Missing secret in repo settings | 1. Go to repo **Settings → Secrets and variables → Actions**. 2. Add `ANTHROPIC_API_KEY`. 3. Re-run failed workflow. |
| PDF ingestion produces 0 chunks | PDFs unreadable by `pdfplumber`, or all pages marked `relevant: false` by LLM annotator | 1. Run `python -m core.ingest --verbose --pdf-dir data/Insurance-product-info` locally. 2. Check `.annot.json` sidecar files for `"relevant": false` on all pages. 3. Delete stale `.annot.json` files and re-ingest if annotation logic changed. |
| Azure deployment fails at `webapps-deploy` step | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret expired or missing | 1. Download fresh publish profile from Azure Portal → App Service → Get publish profile. 2. Update GitHub secret. 3. Re-run deployment workflow. |
| RAG tools return empty results | Embedding model unreachable, or vector store index corrupted | 1. Check embedding model env vars (`OPENAI_MODEL`, `OPENAI_URL_BASE`). 2. Delete and rebuild the vector store index. 3. Re-run `/ingest`. |
| `TypeError` on LLM annotation during ingest | `OPENAI_MODEL` set to a model that does not support JSON-structured responses | 1. Set `OPENAI_MODEL` to a capable model (e.g. `claude-sonnet-4-6` or `gpt-4o`). 2. Re-run ingest. |
| CORS errors in browser | Frontend origin not in `allow_origins` list in `main.py` | 1. Add the frontend's deployed Azure URL to `allow_origins` in `api/main.py`. 2. Redeploy API. |
| `tests/` fail in CI | Dependency mismatch or missing env vars for tests | 1. Run `uv sync` locally and re-run `pytest tests/ -v`. 2. [TODO: confirm whether tests require env vars to be set in CI] |

---

## 4. Deployment Procedure

### Prerequisites

- `uv` installed (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Azure CLI authenticated (`az login`) with contributor access to the App Service
- All required secrets set in GitHub repo (see §5 for list)
- PDFs present in `data/Insurance-product-info/`

---

### Standard Deployment (via GitHub Actions — preferred)

**Step 1 — Merge to `main`**
```bash
git checkout main
git merge --no-ff feature/your-branch
git push origin main
```

**Step 2 — Monitor the workflow**
- Navigate to **Actions → Test & Deploy**
- Confirm `test` job passes (pytest)
- Confirm `deploy-api` and `deploy-frontend` jobs complete green

**Step 3 — Smoke-test production**
```bash
curl -s https://training-bot-api.azurewebsites.net/sessions | jq .
# Expected: []  or existing sessions list
```

**Step 4 — Re-ingest if PDFs changed**
```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
# Monitor logs in Azure Portal → App Service → Log stream
```

---

### Manual Deployment (emergency / hotfix)

**Step 1 — Install dependencies and generate requirements**
```bash
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt
```

**Step 2 — Deploy API**
```bash
az webapp deploy \
  --resource-group <YOUR_RG> \
  --name training-bot-api \
  --src-path . \
  --type zip
```

**Step 3 — Deploy Frontend**
```bash
az webapp deploy \
  --resource-group <YOUR_RG> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

**Step 4 — Verify**
```bash
az webapp show --name training-bot-api --resource-group <YOUR_RG> \
  --query "state" -o tsv
# Expected: Running
```

---

### Rollback Procedure

**Option A — Revert commit and redeploy via CI**
```bash
git revert HEAD --no-edit
git push origin main
# GitHub Actions will redeploy previous state automatically
```

**Option B — Azure deployment slot swap (if slots are configured)**
```bash
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group <YOUR_RG> \
  --slot staging \
  --target-slot production
```

**Option C — Redeploy a specific Git commit**
```bash
git checkout <PREVIOUS_COMMIT_SHA>
uv export --no-dev --format requirements-txt -o requirements.txt
az webapp deploy \
  --resource-group <YOUR_RG> \
  --name training-bot-api \
  --src-path . \
  --type zip
```

> [TODO: Confirm resource group name and whether deployment slots are provisioned on Azure]

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to Check | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure Monitor → App Service → HTTP Server Errors | > 5 errors/min |
| Response latency (P95) | Azure Monitor → App Service → Response Time | > 30 s (streaming expected to be long) |
| CPU usage | Azure Monitor → App Service → CPU Percentage | > 80% sustained |
| Memory usage | Azure Monitor → App Service → Memory Working Set | > 80% of plan limit |
| LLM API errors | Application logs (`logger.warning` / `logger.error`) | Any `CRITICAL` log line |
| Vector store load failure | App startup log: `"No vector store found"` | Any occurrence |
| GitHub Actions failure | GitHub Actions status / email notification | Any failed `Test & Deploy` run |

### Log Locations

**Azure App Service logs (live stream):**
```bash
az webapp log tail --name training-bot-api --resource-group <YOUR_RG>
```

**Application-level structured logs (Python `logging` module):**
- Log level: `INFO` by default (`logging.basicConfig(level=logging.INFO)` in `main.py`)
- Key log patterns to watch:
  - `[ingest] processing:` — PDF ingestion progress
  - `[ingest] index saved` — confirms successful vector store build
  - `No vector store found` — **critical**: RAG tools will fail
  - `Vector store loaded` — healthy startup
  - `annotation failed for` — soft failure, falls back to raw chunker

**GitHub Actions audit:**
- All 5 AI tools log run URLs to the output repo (`ai-delivery-outputs`)
- Review `code-review-*.json` artifacts on the `Tool 1 — Code Review` workflow

### Recommended Azure Alerts

- Set up **Azure Monitor Alert** on HTTP 5xx > threshold → notify via email / Teams
- Set up **Application Insights** (not currently wired in — [TODO: confirm whether App Insights is enabled on the Azure App Service])
- Set up **Log Analytics Workspace** query for `"No vector store found"` pattern

---

## 6. Escalation Path

| Level | Role | Contact | Condition to Escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in on-call rotation / PagerDuty] | Service down > 5 min, deployment failure |
| L2 | Tech lead | [TODO: fill in tech lead name and contact] | L1 cannot restore within 30 min, data loss, security incident |
| L3 | Solution owner / Platform team | [TODO: fill in owner contact] | SLA breach, LLM provider outage, Azure subscription issue |
| External | Azure Support | https://portal.azure.com → Help + Support | Azure infrastructure failure (App Service, networking) |
| External | OpenRouter / LLM Provider | [TODO: confirm LLM provider support URL] | LLM API quota exhausted or provider outage |

> [TODO: Does the team use PagerDuty, OpsGenie, or another incident management tool?]
> [TODO: Is there a Slack/Teams channel for production incidents?]

---

## 7. Useful Commands

### Application Startup (local)

```bash
# Install dependencies
uv sync

# Copy and populate .env
cp .env.example .env   # [TODO: confirm .env.example exists]
# Required vars: API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Start FastAPI server
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Or directly with Python
uv run python -m uvicorn api.main:app --reload
```

### Vector Store Ingestion

```bash
# Ingest all PDFs via API endpoint
curl -X POST http://localhost:8000/ingest

# Ingest directly via CLI (bypasses API)
uv run python -m core.ingest \
  --pdf-dir data/Insurance-product-info \
  --verbose

# Delete and rebuild vector store from scratch
rm -rf chroma_db/    # [TODO: confirm actual vector store directory name]
curl -X POST http://localhost:8000/ingest
```

### Session Management

```bash
# List all active sessions
curl -s http://localhost:8000/sessions | jq .

# Delete a specific session
curl -X DELETE http://localhost:8000/sessions/<SESSION_ID>

# Backup sessions file
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%d)
```

### Run Tests

```bash
# Run full test suite
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Run a single test file
uv run pytest tests/test_agent.py -v   # [TODO: confirm test file names]
```

### Azure App Service Operations

```bash
# View live logs
az webapp log tail \
  --name training-bot-api \
  --resource-group <YOUR_RG>

# Restart the API app service
az webapp restart \
  --name training-bot-api \
  --resource-group <YOUR_RG>

# Restart the frontend app service
az webapp restart \
  --name training-bot-frontend \
  --resource-group <YOUR_RG>

# Check app service status
az webapp show \
  --name training-bot-api \
  --resource-group <YOUR_RG> \
  --query "{state:state, hostName:defaultHostName}" \
  -o table

# List app settings (env vars) — does NOT show secret values
az webapp config appsettings list \
  --name training-bot-api \
  --resource-group <YOUR_RG> \
  -o table
```

### GitHub Actions — Manual Triggers

```bash
# Trigger code review manually on a PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger tech docs regeneration
gh workflow run tool2_tech_docs.yml

# Trigger UAT test pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0
```

### Check Required Secrets Are Set

```bash
# List secret names (values are masked)
gh secret list --repo kylodeng/Insurance-Training-Bot-main
# Expected secrets:
#   ANTHROPIC_API_KEY
#   GH_TOKEN
#   SENDGRID_API_KEY
#   AZURE_WEBAPP_PUBLISH_PROFILE_API
#   AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND
#   API_KEY  (for LLM gateway)
# [TODO: confirm complete list of required secrets]
```

### Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | — | LLM provider API key (OpenRouter or Anthropic) |
|