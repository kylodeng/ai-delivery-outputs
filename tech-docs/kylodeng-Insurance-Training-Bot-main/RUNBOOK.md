# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that helps new insurance agents in a Hong Kong context master product knowledge and sales techniques. It exposes two AI-powered interaction modes: a **Teacher Agent** (ongoing streamed chat using LangGraph, backed by a RAG vector store of insurance product PDFs) and an **Assessor Agent** (one-shot post-roleplay performance evaluator). The backend serves both a Chainlit UI and a Vite frontend, with PDF documents mounted at `/docs`. On startup, the application loads a persisted vector store (Chroma, FAISS, or Pinecone — configurable) pre-indexed from Sun Life Hong Kong insurance product brochures and supplementary documents. Deployment targets two Azure App Service instances (`training-bot-api` and `training-bot-frontend`), triggered automatically via GitHub Actions on merge to `main`, after tests pass.

---

## 2. Health Checks

| Check | How to verify | Expected result |
|---|---|---|
| API process is running | `curl -f http://localhost:8000/docs` | FastAPI Swagger UI HTML returned (HTTP 200) |
| Vector store loaded | Check startup logs for `Vector store loaded (N products)` | Log line present; N > 0 |
| LLM connectivity | Send a test chat message via UI or API | Streamed response begins within 10 s |
| Static docs endpoint | `curl -I http://localhost:8000/docs/Insurance-product-info/` | HTTP 200 or 404 (directory listing disabled is OK; 500 is not) |
| Sessions persistence | `cat data/sessions.json` | Valid JSON, not empty if sessions exist |
| Frontend reachable | `curl -f http://localhost:5173` (dev) or Azure frontend URL | HTML page returned |
| Azure App Service — API | Azure Portal → `training-bot-api` → Overview → Status | **Running** |
| Azure App Service — Frontend | Azure Portal → `training-bot-frontend` → Overview → Status | **Running** |
| GitHub Actions CI | Repo → Actions tab → latest `Test & Deploy` workflow | Green / succeeded |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log: `No vector store found — run POST /ingest first.` | Vector store index files missing from `data/` | 1. SSH / Kudu into App Service. 2. Run `uv run python -m core.ingest --pdf-dir data/Insurance-product-info`. 3. Verify `data/chroma/` or `data/faiss_index/` exists. 4. Restart app. |
| LLM calls returning 401 / 403 | `API_KEY` env var missing or rotated | 1. Check Azure App Service → Configuration → Application Settings. 2. Confirm `API_KEY` is set and matches the OpenRouter / Anthropic key. 3. Rotate if compromised. 4. Restart app. |
| All chat responses time out or hang | LLM provider outage or SSL verification disabled causing connection failure | 1. Check LLM provider status page [TODO: add OpenRouter/Anthropic status URL]. 2. Confirm `OPENAI_URL_BASE` is correct. 3. Note: `httpx.Client(verify=False)` is set — ensure firewall/proxy is not stripping requests silently. 4. Restart app. |
| `Vector store loaded (0 products)` at startup | PDF ingestion ran but produced no chunks (annotation LLM failed, all pages skipped) | 1. Re-run ingest with `--verbose` to inspect per-page decisions. 2. Check `.annot.json` sidecar files for `"relevant": false` on all pages. 3. Verify annotation LLM key is working. |
| Session data lost after restart | `data/sessions.json` missing or corrupt | 1. Check `data/sessions.json` exists and is valid JSON: `python -c "import json; json.load(open('data/sessions.json'))"`. 2. If corrupt, remove file (sessions reset) or restore from backup. 3. [TODO: Is sessions.json backed up anywhere?] |
| GitHub Actions deploy job fails | Azure publish profile secret expired or mis-scoped | 1. Go to Azure Portal → App Service → `Get publish profile`. 2. Update `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` in repo Secrets. 3. Re-run workflow. |
| Tests fail in CI blocking deploy | Code regression or broken dependency | 1. Check `pytest` output in Actions log. 2. Fix failing test or revert the offending commit. 3. Push fix to `main`. |
| CORS errors in browser | Frontend origin not in allowed list | 1. Check `allow_origins` in `api/main.py`. 2. Add the production frontend URL to the list and redeploy. |
| `POST /ingest` returns 500 | Annotation LLM unreachable or PDF unreadable | 1. Check `OPENAI_URL_BASE` and `OPENAI_MODEL` env vars. 2. Inspect app logs for `annotation failed for … using raw chunker`. 3. Run ingest without LLM annotation as fallback (set `llm=None` in call). |
| RAG tools return empty / irrelevant results | Vector store stale after new PDFs added | 1. Add new PDFs to `data/Insurance-product-info/`. 2. Re-run ingest: `POST /ingest` or CLI. 3. Restart app to reload store. |
| `SHOW_TOOL_CALLS` syntax error at startup (`print(f"SHOW_TOOL_CALLS=...")`) | Python f-string with nested same quotes (known code defect in `main.py`) | Patch line in `main.py`: change inner `" "` to `' '` or use a temporary variable. Redeploy. |

---

## 4. Deployment Procedure

### Prerequisites
- Azure CLI installed and logged in
- GitHub repo secrets configured:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` (for AI tooling workflows)
- Python 3.13 and `uv` installed locally for manual steps

### Standard Deployment (Automated — Preferred)

```
1. Merge feature branch PR into `main` on GitHub.
2. GitHub Actions: `Test & Deploy` workflow triggers automatically.
3. Job `test` runs: `uv run pytest tests/ -v`
   - If tests fail → deployment is blocked. Fix and re-push.
4. Job `deploy-api` runs on success:
   - Generates requirements.txt via `uv export`
   - Deploys to Azure App Service `training-bot-api`
5. Job `deploy-frontend` runs on success:
   - Deploys to Azure App Service `training-bot-frontend`
6. Verify health checks (Section 2) post-deploy.
```

### Manual Deployment (Break-Glass)

```bash
# 1. Clone repo and install deps
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
uv sync

# 2. Export requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# 3. Set required environment variables (see Section 5 env table)
export API_KEY="<your-llm-api-key>"
export OPENAI_URL_BASE="https://openrouter.ai/api/v1"
export OPENAI_MODEL="openai/gpt-oss-20b:free"

# 4. (If vector store missing) Run ingestion
uv run python -m core.ingest --pdf-dir data/Insurance-product-info

# 5. Start the API locally to verify
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000

# 6. Deploy to Azure manually (API)
az webapp deployment source config-zip \
  --resource-group <rg-name> \
  --name training-bot-api \
  --src <zip-of-repo>
```

[TODO: What is the Azure Resource Group name?]  
[TODO: Is there a startup command configured in Azure App Service (e.g. `uvicorn api.main:app`)?]  
[TODO: Is the frontend a Chainlit app or a separate Vite build? The deploy workflow uses `azure/webapps-deploy` for both — confirm frontend build step.]

### Rollback Steps

```
Option A — GitHub Actions (preferred):
1. Identify the last known good commit SHA in git log.
2. git revert <bad-commit-sha> and push to main, OR
3. In GitHub Actions: navigate to the last successful deploy run →
   click "Re-run jobs" to redeploy the previous artifact.
   [TODO: Confirm whether Azure App Service deployment slots are configured for swap-based rollback]

Option B — Azure Portal:
1. Azure Portal → App Service → training-bot-api → Deployment Center
2. Select previous successful deployment from deployment history
3. Click Redeploy
4. Repeat for training-bot-frontend

Option C — Manual zip deploy of previous release:
1. git checkout <last-good-tag-or-sha>
2. Follow Manual Deployment steps above
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to find it | Alert threshold |
|---|---|---|
| App Service HTTP 5xx error rate | Azure Portal → App Service → Metrics → Http 5xx | > 5% over 5 min |
| App Service response time (P95) | Azure Portal → App Service → Metrics → Response Time | > 30 s (LLM streaming can be slow; tune threshold) |
| App Service instance CPU | Azure Portal → App Service → Metrics → CPU Percentage | > 80% sustained 5 min |
| Memory usage | Azure Portal → App Service → Metrics → Memory Working Set | > 80% of plan limit |
| GitHub Actions workflow status | Repo → Actions tab | Any `Test & Deploy` run in failed/cancelled state |
| LLM API quota / rate limit errors | App logs — search for `429` or `RateLimitError` | Any occurrence |
| Vector store chunk count | Startup log: `Vector store loaded (N products)` | N = 0 is a critical failure |
| Session file size | `data/sessions.json` file size on disk | [TODO: Define retention/rotation policy] |

### Key Log Patterns

```
# Startup success
INFO: Vector store loaded (N products)

# Startup failure — no index
WARNING: No vector store found — run POST /ingest first.

# Annotation failure (non-fatal, falls back to raw chunker)
WARNING: [ingest] annotation failed for <file>: <error> — using raw chunker

# Rate limit hit on LLM
[ingest] rate-limit pause Xs …

# Request-level tool call activity
# (Enable SHOW_TOOL_CALLS=true in env for verbose tool logging)
```

### Where to Find Logs

```bash
# Azure App Service live log stream
az webapp log tail --name training-bot-api --resource-group <rg-name>

# Download logs
az webapp log download --name training-bot-api --resource-group <rg-name>
```

[TODO: Is Azure Application Insights or Log Analytics Workspace connected to these App Services?]  
[TODO: Are any uptime monitoring tools (e.g. Azure Monitor alerts, Pingdom, Better Uptime) configured?]  
[TODO: Are SendGrid delivery failures monitored? The shared.py wires up email notifications but no bounce handling is visible.]

---

## 6. Escalation Path

| Level | Who | Contact | When to escalate |
|---|---|---|---|
| L1 — On-call engineer | [TODO: Team on-call rota] | [TODO: Slack channel / PagerDuty] | Service down > 5 min; deploy failed |
| L2 — Backend owner | [TODO: Name] | [TODO: Email / Slack handle] | LLM integration broken; vector store corrupt; data loss |
| L3 — Platform / Azure admin | [TODO: Name] | [TODO: Email / Slack handle] | App Service tier limits; infrastructure outage |
| L4 — Product owner | Kylo Deng | kylo.deng@capco.com | Business-critical failure; data breach; production rollback needed |
| External — LLM provider | OpenRouter / Anthropic support | [TODO: Support URLs and SLA tier] | Persistent LLM API outage |
| External — Azure support | Microsoft Azure support | [TODO: Support plan tier and ticket URL] | App Service / networking outage |

---

## 7. Useful Commands

```bash
# ── Local dev ──────────────────────────────────────────────────────────────

# Install dependencies
uv sync

# Run API server locally
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core

# ── Ingestion ──────────────────────────────────────────────────────────────

# Ingest all PDFs from default data directory
uv run python -m core.ingest --pdf-dir data/Insurance-product-info

# Ingest with verbose chunk output
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Trigger ingest via API endpoint (if implemented)
curl -X POST http://localhost:8000/ingest

# ── Sessions ───────────────────────────────────────────────────────────────

# Inspect sessions file
python -c "import json; sessions=json.load(open('data/sessions.json')); print(f'{len(sessions)} sessions')"

# Validate sessions file JSON
python -c "import json; json.load(open('data/sessions.json')); print('OK')"

# ── Azure App Service ──────────────────────────────────────────────────────

# View live API logs
az webapp log tail --name training-bot-api --resource-group <rg-name>

# View live frontend logs
az webapp log tail --name training-bot-frontend --resource-group <rg-name>

# Restart API app service
az webapp restart --name training-bot-api --resource-group <rg-name>

# Restart frontend app service
az webapp restart --name training-bot-frontend --resource-group <rg-name>

# List current app settings (env vars)
az webapp config appsettings list --name training-bot-api --resource-group <rg-name>

# Set a single env var
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg-name> \
  --settings API_KEY="<new-value>"

# Download deployment logs
az webapp log download --name training-bot-api --resource-group <rg-name>

# ── GitHub Actions ─────────────────────────────────────────────────────────

# Manually trigger Tech Docs generation
gh workflow run tool2_tech_docs.yml

# Manually trigger code review on a PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# Manually trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0

# List recent workflow runs
gh run list --workflow=deploy.yml --limit=10

# ── Vector store inspection ────────────────────────────────────────────────

# Check known products in store (Python)
python -c "
from core.vector_store import get_vector_store
store = get_vector_store()
store.load()
print('Products:', store.get_known_products())
"

# ── Environment variable reference ────────────────────────────────────────
# Required for API:
#   API_KEY            — LLM provider API key (OpenRouter or Anthropic)
#   OPENAI_URL_BASE    — LLM base URL (default: https://openrouter.ai/api/v1)
#   OPENAI_MODEL       — Model name (default: openai/gpt-oss-20b:free)
#   SHOW_