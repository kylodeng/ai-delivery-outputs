# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that helps new insurance agents master product knowledge and sales technique through two interactive modes: a **Teacher mode** (ongoing streamed chat with an AI coach powered by LangGraph) and a **Roleplay/Assessment mode** (simulated customer conversations with automated performance scoring). The backend exposes a REST API served from Azure App Service (`training-bot-api`), while the frontend (Chainlit UI) is deployed as a separate Azure App Service (`training-bot-frontend`). Product knowledge is stored in a local vector store (FAISS or Chroma) built from PDF brochures and supplementary documents under `data/Insurance-product-info/`. Ingestion is triggered manually via `POST /ingest`; all LLM calls route through an OpenRouter-compatible endpoint (defaulting to `openai/gpt-oss-20b:free`) configured via environment variables. Sessions are persisted to `data/sessions.json` and survive server restarts.

---

## 2. Health Checks

Run these checks in order to confirm the service is fully operational.

### 2.1 API Service
```bash
# Basic liveness — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<api-hostname>/docs

# List active sessions — expect HTTP 200 + JSON array
curl https://<api-hostname>/sessions
```
> [TODO: What is the production hostname for `training-bot-api` on Azure?]

### 2.2 Frontend (Chainlit)
- Navigate to `https://<frontend-hostname>` in a browser.
- Confirm the chat interface loads and a new session can be started.

> [TODO: What is the production hostname for `training-bot-frontend` on Azure?]

### 2.3 Vector Store
```bash
# POST /ingest status check — confirm store is loaded, not empty
curl -s https://<api-hostname>/ingest   # expect 200 or a "already loaded" message
```
In the API startup logs, look for:
```
Vector store loaded (N products)
```
If you see `No vector store found — run POST /ingest first`, the store needs rebuilding (see §3).

### 2.4 LLM Connectivity
Send a minimal teacher-mode message and confirm a streamed response is returned within ~10 seconds.
```bash
curl -s -N -X POST https://<api-hostname>/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","message":"Hello"}'
```
> [TODO: Confirm exact chat endpoint path from `api/main.py` route definitions not shown in truncated file.]

### 2.5 GitHub Actions Pipelines
In the repo's **Actions** tab, verify:
- `Test & Deploy` workflow is green on `main`.
- No failed runs for `Tool 1–5` workflows within the last 24 hours.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all requests | Vector store not loaded at startup — log shows `No vector store found` | Run `POST /ingest` endpoint to rebuild the store; confirm `data/sessions.json` and `data/` directory are accessible to the app |
| Chat responses are empty or stream hangs indefinitely | LLM API key invalid, rate-limited, or OpenRouter endpoint unreachable | Check `API_KEY` and `OPENAI_URL_BASE` env vars on Azure; test connectivity to `https://openrouter.ai/api/v1`; check OpenRouter dashboard for quota |
| `SSL: CERTIFICATE_VERIFY_FAILED` errors in logs | `verify=False` is set in `httpx` clients but the Azure outbound proxy may intercept TLS | Confirm `verify=False` is propagated to both sync and async `httpx` clients in `api/main.py`; check Azure App Service networking/proxy settings |
| Sessions lost after deployment / restart | `data/sessions.json` is on ephemeral storage | Ensure `data/` is mapped to persistent Azure storage (e.g. Azure Files mount); confirm `load_sessions()` runs successfully at lifespan startup |
| `KeyError: ANTHROPIC_API_KEY` in GitHub Actions | Secret not set in repository settings | Add `ANTHROPIC_API_KEY` (and `GH_TOKEN`, `SENDGRID_API_KEY`) to GitHub repository secrets under **Settings → Secrets and variables → Actions** |
| RAG tools return no results / agent says "I don't know" | Vector store index stale or empty after PDF additions | Re-run ingestion: `POST /ingest`; check `data/Insurance-product-info/` for new PDFs; verify annotation `.annot.json` sidecar files are present |
| `deploy-api` or `deploy-frontend` job fails in CI | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret missing or expired | Download fresh publish profiles from Azure Portal → App Service → **Get publish profile**; update the corresponding GitHub secret |
| Pytest fails in `test` job | Dependency mismatch or missing test fixtures | Run `uv sync` locally and reproduce; check Python version is 3.13 (as pinned in workflow) vs 3.12 used in other jobs — [TODO: confirm intended Python version consistency] |
| `Tool 2 — Tech Documentation` workflow fails on Sunday | `GH_TOKEN` lacks write access to `ai-delivery-outputs` repo | Ensure the PAT stored as `GH_TOKEN` has `repo` scope and the owner matches `OUTPUT_REPO_OWNER` |
| Ingestion produces zero chunks for a PDF | PDF is image-only (no extractable text) or all pages marked `relevant: false` in annotation | Open the PDF manually; if scanned, run OCR pre-processing; inspect `.annot.json` sidecar and flip `relevant` to `true` for affected pages |
| Age/premium calculation wrong in Teacher mode | Agent did not call `get_current_date` tool before quoting premium | This is a prompt adherence issue — review the `TEACHER_SYSTEM` prompt; confirm the tool is registered in `make_teacher_agent()`; [TODO: add an automated test for this tool-call ordering] |
| CORS errors in browser console | Frontend origin not in `allow_origins` list | Add the production frontend URL to `CORSMiddleware` in `api/main.py` and redeploy |

---

## 4. Deployment Procedure

Deployment is automated via `.github/workflows/deploy.yml` and triggers on every push to `main` after tests pass. The steps below cover both the automated flow and manual deployment.

### 4.1 Pre-deployment Checklist
- [ ] All tests pass locally: `uv run pytest tests/ -v`
- [ ] Environment variables confirmed in Azure App Service configuration (see §5)
- [ ] Vector store data (`data/` directory) is on persistent storage
- [ ] Publish profiles for both App Services are valid

### 4.2 Automated Deployment (normal path)

```
push to main
    │
    ├─► job: test
    │     uv sync
    │     uv run pytest tests/ -v
    │
    ├─► job: deploy-api  (needs: test)
    │     uv export --no-dev --format requirements-txt -o requirements.txt
    │     azure/webapps-deploy → app-name: training-bot-api
    │
    └─► job: deploy-frontend  (needs: test)
          uv export --no-dev --format requirements-txt -o requirements.txt
          azure/webapps-deploy → app-name: training-bot-frontend
```

### 4.3 Manual Deployment

```bash
# 1. Clone and install
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
uv sync

# 2. Set required environment variables (see §5)
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API keys

# 3. Run tests
uv run pytest tests/ -v

# 4. Generate requirements.txt for Azure
uv export --no-dev --format requirements-txt -o requirements.txt

# 5. Deploy API to Azure (requires Azure CLI logged in)
az webapp deploy --resource-group <rg> --name training-bot-api \
  --src-path . --type zip

# 6. Deploy Frontend to Azure
az webapp deploy --resource-group <rg> --name training-bot-frontend \
  --src-path . --type zip

# 7. Ingest knowledge base (first deploy or after PDF changes)
curl -X POST https://<api-hostname>/ingest
```

> [TODO: Is the frontend a separate Chainlit process within the same repo, or a distinct sub-directory? Confirm entry points for both App Services.]

### 4.4 Rollback Steps

```bash
# Option A — Revert via git and re-trigger CI
git revert HEAD --no-edit
git push origin main
# CI will run tests → deploy on passing

# Option B — Azure Portal rollback
# Azure Portal → App Service (training-bot-api / training-bot-frontend)
# → Deployment Center → Deployments → select previous deployment → Redeploy

# Option C — Azure CLI rollback to previous slot (if deployment slots configured)
az webapp deployment slot swap \
  --resource-group <rg> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

> [TODO: Are staging slots configured on either Azure App Service?]

After rollback, verify health checks in §2 pass before marking incident resolved.

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Where to find it | Alert threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Monitoring → Metrics → Http5xx | > 1% over 5 min |
| HTTP response time (P95) | Azure App Service → Metrics → Response Time | > 10 s |
| CPU percentage | Azure App Service → Metrics → CpuPercentage | > 80% sustained 10 min |
| Memory percentage | Azure App Service → Metrics → MemoryPercentage | > 85% |
| LLM API latency | Application logs — search `call_claude` or `ChatOpenAI` duration | > 30 s per call |
| Vector store load success | App startup log — `Vector store loaded (N products)` | Alert if N = 0 or log absent |
| Sessions file size | `data/sessions.json` file size growth | [TODO: define max acceptable size] |
| GitHub Actions failure | GitHub → Actions tab | Any red run on `main` |

### 5.2 Log Locations

| Log type | Location |
|---|---|
| API application logs | Azure Portal → `training-bot-api` → Log stream, or `az webapp log tail` |
| Frontend logs | Azure Portal → `training-bot-frontend` → Log stream |
| Ingestion logs | Application logs — prefixed `[ingest]` |
| GitHub Actions CI/CD logs | `https://github.com/kylodeng/Insurance-Training-Bot-main/actions` |
| AI delivery tool audit logs | `ai-delivery-outputs` repo (written by `shared.py` `write_audit_entry`) |

### 5.3 Structured Log Patterns to Monitor

```
# Startup — vector store healthy
INFO:     Vector store loaded (N products)

# Startup — vector store missing (ALERT)
WARNING:  No vector store found — run POST /ingest first.

# LLM errors to watch
ERROR:    ... ChatOpenAI ... 401 Unauthorized
ERROR:    ... ChatOpenAI ... 429 Too Many Requests
ERROR:    ... ChatOpenAI ... 503 Service Unavailable

# Annotation failure (non-fatal but degrades RAG quality)
WARNING:  [ingest] annotation failed for <filename>.pdf: <reason> — using raw chunker
```

### 5.4 Alerting Setup
> [TODO: Are Azure Monitor alerts configured? If not, create alerts for HTTP 5xx > 1% and response time P95 > 10 s on both App Services.]
> [TODO: Is there a PagerDuty / OpsGenie / Teams webhook for incident notification?]

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: name / email / Teams handle] | Service health check fails; deployment stuck |
| L2 | Backend lead | [TODO: name / email] | LLM integration errors; vector store corruption; data loss |
| L3 | Platform / DevOps | [TODO: name / email] | Azure infrastructure issues; secrets rotation; cost spike |
| L4 | Product owner | [TODO: Kylo Deng — `kylo.deng@capco.com`] | Business-critical outage > 1 h; security incident; data breach |
| External | OpenRouter / Anthropic support | [TODO: support URLs] | LLM API sustained outage not caused by our configuration |
| External | Azure Support | [TODO: support ticket URL] | Azure App Service platform failure |

---

## 7. Useful Commands

### Start the API locally
```bash
# Install dependencies
uv sync

# Run FastAPI development server
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### Run tests
```bash
uv run pytest tests/ -v

# With coverage report
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### Ingest knowledge base
```bash
# Via HTTP (server must be running)
curl -X POST http://localhost:8000/ingest

# Directly via CLI
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### Rebuild vector store from scratch
```bash
# Delete existing index and re-ingest
rm -rf data/chroma_db/   # or data/faiss_index/ depending on VECTOR_STORE env var
curl -X POST http://localhost:8000/ingest
```

> [TODO: Confirm the exact vector store directory name — `chroma_db`, `faiss_index`, or other — from `core/vector_store.py`.]

### Tail Azure App Service logs
```bash
# API service
az webapp log tail --resource-group <rg> --name training-bot-api

# Frontend service
az webapp log tail --resource-group <rg> --name training-bot-frontend
```

### Check and update App Service environment variables
```bash
# List current settings
az webapp config appsettings list \
  --resource-group <rg> --name training-bot-api --output table

# Set / update a variable
az webapp config appsettings set \
  --resource-group <rg> --name training-bot-api \
  --settings API_KEY="<new-value>"
```

### Restart App Services
```bash
az webapp restart --resource-group <rg> --name training-bot-api
az webapp restart --resource-group <rg> --name training-bot-frontend
```

### Generate requirements.txt from uv lockfile
```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Trigger AI delivery tools manually (GitHub CLI)
```bash
# Tool 2 — regenerate tech docs
gh workflow run tool2_tech_docs.yml --repo kylodeng/Insurance-Training-Bot-main

# Tool 1 — code review a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/Insurance-Training-Bot-main \
  -f review_mode=pr \
  -f pr_number=42
```

### Check sessions file
```bash
# Count active sessions
python -c "import json; d=json.load(open('data/sessions.json')); print(f'{len(d)} sessions')"

# Pretty-print first session
python -c "import json; d=json.load(open('data/sessions.json')); print(json.dumps(list(d.values())[0], indent=2))"
```

### Required environment variables reference

| Variable | Required | Description |
|---|---|---|
| `API_KEY` | Yes | LLM provider API key (OpenRouter or Anthropic) |
| `OPENAI_URL