# Operational Runbook — Insurance Training Bot

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides AI-powered insurance sales training for agents operating in the Hong Kong market. It combines a Retrieval-Augmented Generation (RAG) pipeline — backed by a vector store (ChromaDB or FAISS, optionally Pinecone) — with a LangGraph agent framework to deliver two modes: a **Teacher mode** (interactive, streamed coaching sessions) and an **Assessor mode** (one-shot post-roleplay performance evaluation). Insurance product knowledge is ingested from PDF brochures, supplementary documents, and hospital network lists stored under `data/Insurance-product-info/`, annotated via an LLM, chunked, embedded (via Voyage AI or equivalent), and indexed at startup. The backend is deployed to **Azure App Service** (`training-bot-api`) and a separate frontend service (`training-bot-frontend`), with CI/CD orchestrated through GitHub Actions. Five supplementary AI delivery workflows (code review, tech docs, business docs, auto testing, UAT facilitation) run alongside the core application using Claude (Anthropic) and write outputs to a separate `ai-delivery-outputs` GitHub repository.

---

## 2. Health Checks

### Application Health

| Check | Command / URL | Expected Result |
|---|---|---|
| API process running | `curl -s http://localhost:8000/docs` | Returns FastAPI Swagger UI HTML |
| Azure App Service status | Azure Portal → App Service `training-bot-api` → Overview | Status: **Running** |
| Frontend App Service status | Azure Portal → App Service `training-bot-frontend` → Overview | Status: **Running** |
| Vector store loaded | Check startup logs for: | `Vector store loaded (N products)` |
| Sessions file accessible | `ls -lh data/sessions.json` | File exists and is non-zero |
| CORS / API reachable from frontend | Browser DevTools → Network tab on frontend URL | No CORS errors; 200 responses |
| PDF data directory | `ls data/Insurance-product-info/` | PDFs and `.annot.json` sidecar files present |

### Confirming RAG Pipeline is Operational

```bash
# Hit ingest endpoint (only if vector store is missing/stale)
curl -X POST http://localhost:8000/ingest

# Check known products are indexed (add this endpoint if not already present)
# [TODO: Is there a /health or /products endpoint exposed by the API?]
```

### GitHub Actions Workflow Health

Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

All five tool workflows (`tool1` through `tool5`) and the `deploy.yml` workflow should show green on `main`.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns 500 on all requests at startup | Vector store not found — `No vector store found` warning in logs | Run `POST /ingest` or execute `python core/ingest.py` manually; ensure `data/` PDFs are present |
| `KeyError: 'API_KEY'` or similar env var error on startup | Missing environment variable(s) in App Service config | Check Azure App Service → Configuration → Application Settings; add missing vars (`API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`); restart app |
| LLM responses are empty or garbled | OpenRouter / LLM provider outage, or wrong `OPENAI_URL_BASE` / model name | Verify `OPENAI_URL_BASE` and `OPENAI_MODEL` env vars; test model endpoint directly; check OpenRouter status page |
| `SSL: CERTIFICATE_VERIFY_FAILED` in logs | `verify=False` is set but underlying TLS issue at network level | Confirm proxy/firewall settings on Azure; `verify=False` is hard-coded in `main.py` — [TODO: should this be configurable per environment?] |
| Sessions lost after restart | `data/sessions.json` missing or unwritable | Confirm `_SESSIONS_FILE` path is on persistent storage (Azure App Service file share, not ephemeral `/tmp`); check write permissions |
| RAG returns irrelevant results | Stale or corrupt vector index | Delete existing vector store files and re-run ingest: `python core/ingest.py --pdf-dir data/Insurance-product-info` |
| Annotation LLM calls failing during ingest | LLM quota exceeded or bad API key | Check `API_KEY` env var; check provider rate limits; re-run ingest — existing `.annot.json` sidecar files are cached and won't be re-called |
| CORS errors in browser | Frontend origin not in allow-list | Add the frontend Azure URL to `allow_origins` in `api/main.py` and redeploy |
| GitHub Actions `deploy-api` job fails | Invalid or expired `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret | Regenerate publish profile from Azure Portal → App Service → Get publish profile; update GitHub secret |
| GitHub Actions `tool1`–`tool5` workflows fail | Missing secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) | Verify all three secrets exist under GitHub repo Settings → Secrets and variables → Actions |
| Streaming responses cut off mid-sentence | Network timeout or Azure App Service idle timeout | Increase Azure App Service request timeout; check `httpx.AsyncClient` timeout settings; [TODO: are explicit timeouts configured?] |
| Teacher agent not citing sources | `reset_sources()` not called before request, or context var not initialised | Ensure `reset_sources()` is called at the top of each `/chat` handler before streaming begins |
| Frontend 404 on PDF links (`/docs/...`) | `data/` directory not mounted or `StaticFiles` misconfigured | Verify `_DATA_DIR` path resolves correctly on Azure; check App Service file system; confirm `/docs` static mount in `main.py` |
| Wrong premium band quoted | `get_current_date` tool not called first / age calculation error | Check agent system prompt; ensure `get_current_date` is invoked before any premium-band lookup; review agent tool-call logs |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- GitHub repo secrets set: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- `uv` installed locally for local builds

### Standard Deployment (via CI/CD — Preferred)

```
1. Merge a PR into `main`.
2. GitHub Actions triggers `.github/workflows/deploy.yml` automatically.
3. The `test` job runs: `uv run pytest tests/ -v`
   - If tests fail → deployment is blocked. Fix tests before merging.
4. On test success, two parallel jobs run:
   a. `deploy-api`   → deploys to Azure App Service `training-bot-api`
   b. `deploy-frontend` → deploys to Azure App Service `training-bot-frontend`
5. Monitor job progress at:
   https://github.com/kylodeng/Insurance-Training-Bot-main/actions
6. Confirm deployment:
   - Check Azure Portal: both App Services show last deployment timestamp.
   - Smoke test: curl https://<training-bot-api-url>/docs
   - Smoke test: load https://<training-bot-frontend-url> in browser.
7. Check application logs for vector store load confirmation:
   "Vector store loaded (N products)"
```

### Manual Deployment (Emergency / Hotfix)

```bash
# 1. Install uv if not already installed
pip install uv

# 2. Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# 3. Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 4. Deploy frontend manually
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip

# 5. Restart App Services to pick up new code
az webapp restart --resource-group <RESOURCE_GROUP> --name training-bot-api
az webapp restart --resource-group <RESOURCE_GROUP> --name training-bot-frontend
```

### Post-Deployment: Vector Store Ingest (if PDFs updated)

```bash
# Run from the API machine or trigger via endpoint
curl -X POST https://<training-bot-api-url>/ingest

# Or run locally against the data directory
python core/ingest.py --pdf-dir data/Insurance-product-info
```

### Rollback Steps

```
Option A — Revert via GitHub (preferred):
1. Identify the last known-good commit SHA on main:
   git log --oneline main
2. Create a revert commit:
   git revert <bad-commit-sha>
   git push origin main
3. CI/CD pipeline redeploys automatically.

Option B — Azure deployment slot swap (if slots are configured):
[TODO: Are staging/production slots configured on these App Services?]
az webapp deployment slot swap \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --slot staging \
  --target-slot production

Option C — Manual rollback to previous zip artifact:
1. Download the previous deployment artifact from GitHub Actions.
2. Re-run the deploy step manually using the old requirements.txt + source.

Option D — Azure portal rollback:
Azure Portal → App Service → Deployment Center → Deployment history
→ Select previous deployment → Redeploy
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to Find | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure Monitor → App Service → HTTP Server Errors | > 1% of requests |
| HTTP 4xx error rate | Azure Monitor → App Service → HTTP 4xx Errors | > 5% sustained |
| Response time (P95) | Azure Monitor → App Service → Average Response Time | > 10s (streaming endpoint) |
| CPU usage | Azure Monitor → App Service → CPU Percentage | > 80% sustained 5 min |
| Memory usage | Azure Monitor → App Service → Memory Working Set | > 85% of plan limit |
| GitHub Actions failure | GitHub → Actions tab | Any `deploy.yml` failure on `main` |
| Vector store ingest success | Application logs | Absence of `"Vector store loaded"` at startup |

### Logs to Watch

```bash
# Azure App Service live log stream
az webapp log tail \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api

# Key log patterns to alert on:
# ERROR  — any unhandled exception
# "No vector store found"  — RAG not operational
# "annotation failed"      — PDF ingest degraded (using raw chunker fallback)
# "rate-limit pause"       — embedding batch delays (normal during ingest, flag if in prod)
# SSL / certificate errors — networking issue
```

### Log Retention

[TODO: What is the Azure Log Analytics workspace retention policy?]

### Alerting Setup

[TODO: Are Azure Monitor Alert Rules configured? If not, create alerts for 5xx rate and App Service restart events.]

### LLM Provider Monitoring

- **OpenRouter / LLM**: Check [https://openrouter.ai/activity](https://openrouter.ai/activity) for quota usage.
- **Anthropic (Claude)**: Used only by GitHub Actions workflows (`tool1`–`tool5`), not the live API. Monitor at [https://console.anthropic.com](https://console.anthropic.com).
- **Voyage AI** (embeddings): [TODO: Which embedding provider is in use? Confirm from `OPENAI_URL_BASE` env var.]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call DevOps / Engineer | [TODO: name & contact] | API down, deployment failure, vector store failure |
| L2 | Tech Lead | [TODO: name & contact] | Persistent LLM errors, data corruption, security incident |
| L3 | Solution Owner | [TODO: name & contact] | Extended outage > 1 hour, compliance/data incident |
| External | Azure Support | [Azure Support Portal](https://portal.azure.com/#blade/Microsoft_Azure_Support/HelpAndSupportBlade) | Azure infrastructure issues |
| External | OpenRouter Support | [TODO: support URL] | LLM provider outage |
| External | Anthropic Support | [https://support.anthropic.com](https://support.anthropic.com) | Claude API outage affecting CI/CD tools |

**Primary notification email (from code):** `kylo.deng@capco.com`

---

## 7. Useful Commands

### Local Development

```bash
# Install dependencies
pip install uv
uv sync

# Copy and configure environment variables
cp .env.example .env   # [TODO: does .env.example exist?]
# Edit .env with: API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, SHOW_TOOL_CALLS

# Start the API server
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### Vector Store & Ingest

```bash
# Ingest all PDFs into vector store
python core/ingest.py --pdf-dir data/Insurance-product-info

# Ingest with verbose chunk output
python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Trigger ingest via API endpoint
curl -X POST http://localhost:8000/ingest

# Check annotation sidecar files (LLM cache)
ls data/Insurance-product-info/**/*.annot.json

# Delete vector store to force full re-ingest
rm -rf data/chroma/        # if using ChromaDB
rm -f data/*.faiss data/*.pkl  # if using FAISS
```

### Azure App Service

```bash
# Check App Service status
az webapp show \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --query "state" -o tsv

# Stream live logs
az webapp log tail \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api

# Restart App Service
az webapp restart \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api

# Get current app settings
az webapp config appsettings list \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --output table

# Set/update an environment variable
az webapp config appsettings set \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --settings API_KEY="<new-value>"

# Generate requirements.txt from uv lockfile
uv export --no-dev --format requirements-txt -o requirements.txt
```

### GitHub Actions — Manual Workflow Triggers

```bash
# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger tech doc generation
gh workflow run tool2_tech_docs.yml

# Trigger business docs for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Insurance Training Bot" \
  -f release_version="1.0.0"

# Trigger auto test generation
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

### Session Management

```bash
# Inspect current sessions file
cat data/sessions.json | python -m json.tool | head -100

# Backup sessions before deployment
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%d%H%M%S)

# Clear all sessions (destructive — confirm first)
echo '{}' > data/sessions.json
```

### Smoke Tests

```bash
# Check API is up
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs