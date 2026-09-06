# Operational Runbook — Insurance Training Bot

> **Repo:** `kylodeng/Insurance-Training-Bot-main`
> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides AI-powered insurance sales training for new agents in a Hong Kong context. It operates in two modes: a **Teacher mode**, where an LLM-backed LangGraph agent coaches agents interactively using a RAG (Retrieval-Augmented Generation) pipeline over ingested insurance product PDFs; and a **Roleplay/Assessment mode**, where the system simulates a realistic Hong Kong customer profile and subsequently scores the trainee's performance across multiple dimensions. The backend exposes a streaming REST API consumed by a Chainlit frontend, both deployed as separate Azure App Service instances. Source documents (Sun Life product brochures, hospital network lists, etc.) are chunked, LLM-annotated, embedded via a vector store (Chroma/FAISS/Pinecone — configurable), and served at `/docs` for inline citation links. A suite of five GitHub Actions AI delivery workflows (code review, tech docs, business docs, auto-testing, UAT) runs against the repo using Anthropic Claude.

---

## 2. Health Checks

Run these checks to confirm the service is operating correctly.

### 2.1 API Service (`training-bot-api`)

```bash
# Liveness — FastAPI root or docs endpoint
curl -s -o /dev/null -w "%{http_code}" https://<api-hostname>/docs
# Expected: 200

# Confirm vector store loaded (check startup log)
# Expected log line: "Vector store loaded (N products)"
```

### 2.2 Frontend Service (`training-bot-frontend`)

```bash
curl -s -o /dev/null -w "%{http_code}" https://<frontend-hostname>/
# Expected: 200
```

### 2.3 Vector Store

```bash
# POST to ingest endpoint returns known products list
curl -s https://<api-hostname>/ingest -X POST | jq .
# If the store is empty, the startup log will warn:
# "No vector store found — run POST /ingest first."
```

### 2.4 LLM Connectivity (OpenRouter / Anthropic)

```bash
# Start a teacher session — if LLM is unreachable the stream will fail
curl -s -N https://<api-hostname>/chat/stream -X POST \
  -H "Content-Type: application/json" \
  -d '{"session_id":"health-check","message":"Hello"}'
# Expected: SSE stream begins within 5 seconds
```

### 2.5 Static Document Serving

```bash
curl -s -o /dev/null -w "%{http_code}" \
  https://<api-hostname>/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
# Expected: 200
```

### 2.6 Session Persistence

```bash
# Check sessions.json exists and is valid JSON
cat data/sessions.json | python3 -m json.tool > /dev/null && echo "OK"
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all `/chat` requests | Vector store not loaded; `data/sessions.json` missing or corrupt | 1. Check startup logs for "No vector store found". 2. Run `POST /ingest`. 3. Verify `data/` directory is mounted and writable. |
| Streaming response hangs / never completes | LLM provider (OpenRouter/Anthropic) timeout or rate limit | 1. Check `OPENAI_URL_BASE` and `API_KEY` env vars. 2. Check provider status page. 3. Inspect logs for `httpx` timeout errors. 4. Retry; consider switching `OPENAI_MODEL`. |
| `SSL: CERTIFICATE_VERIFY_FAILED` in logs | `verify=False` is set in `httpx.Client` — this suppresses SSL errors but may mask real connectivity issues | 1. Confirm the base URL is reachable. 2. Check corporate proxy/firewall rules on App Service. [TODO: confirm whether SSL verification should be re-enabled in production] |
| `401 Unauthorized` from LLM API | `API_KEY` secret missing, expired, or wrong scope | 1. Rotate the key in the provider dashboard. 2. Update `API_KEY` in Azure App Service Application Settings. 3. Restart the app. |
| RAG tools return empty results / no citations appear | Vector store index is empty or corrupt | 1. Run `POST /ingest` to rebuild. 2. Check that PDF files exist under `data/Insurance-product-info/`. 3. Look for embedding errors in logs. |
| `POST /ingest` fails with annotation error | LLM annotation call fails (bad API key, model unavailable, malformed JSON from LLM) | 1. Check `OPENAI_MODEL` and `API_KEY`. 2. The ingester falls back to raw chunking automatically — verify log line "annotation failed … using raw chunker". 3. If all annotation fails, delete `.annot.json` sidecar files and retry. |
| Session state lost after restart | `sessions.json` not persisted (ephemeral filesystem on App Service) | 1. Confirm App Service persistent storage is mounted at `/home`. 2. Verify `_SESSIONS_FILE` path resolves to a persistent location. [TODO: confirm Azure persistent storage mount path] |
| GitHub Actions workflow fails on `tool1`–`tool5` | Missing GitHub Secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) | 1. Navigate to repo → Settings → Secrets. 2. Verify all three secrets exist. 3. Re-run failed workflow. |
| Deployment pipeline fails at `Deploy API to Azure App Service` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret missing or expired | 1. Download a fresh publish profile from Azure Portal → App Service → Get publish profile. 2. Update the secret in GitHub. |
| CORS errors in browser | Origin not whitelisted in `CORSMiddleware` | 1. Add the production frontend origin to `allow_origins` in `api/main.py`. 2. Redeploy API. |
| `ModuleNotFoundError` on startup | `uv sync` not run or `requirements.txt` generated incorrectly | 1. Run `uv sync` locally to reproduce. 2. Check `uv export` step in `deploy.yml`. 3. Verify Python version matches (`3.13` in CI, check App Service runtime). |
| PDF citations return broken `/docs/` links | `_DATA_DIR` path resolution fails on Windows vs Linux, or files not mounted | 1. Confirm `data/` directory is present in App Service deployment. 2. Check `StaticFiles` mount in `api/main.py`. 3. Test `_to_docs_path()` locally with the failing file URL. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- `uv` installed (`pip install uv`)
- GitHub repository secrets configured (see Section 3)
- Python 3.13 locally (matching CI)

### 4.1 Standard Deployment (CI/CD — preferred)

```
Push to main branch
       │
       ▼
  GitHub Actions: "Test & Deploy" (deploy.yml)
       │
       ├─► job: test       → uv sync → pytest tests/ -v
       │
       ├─► job: deploy-api        (on push to main, after test passes)
       │     └─► azure/webapps-deploy → training-bot-api
       │
       └─► job: deploy-frontend   (on push to main, after test passes)
             └─► azure/webapps-deploy → training-bot-frontend
```

**Steps:**

1. Create a feature branch and open a PR against `main`.
2. CI runs tests automatically — fix any failures.
3. Merge PR to `main`.
4. Monitor the `Test & Deploy` workflow in GitHub Actions.
5. Confirm both `deploy-api` and `deploy-frontend` jobs complete with ✅.
6. Run health checks (Section 2).

### 4.2 Manual / Emergency Deployment

```bash
# 1. Clone and set up
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install dependencies
uv sync

# 3. Run tests locally
uv run pytest tests/ -v

# 4. Generate requirements.txt for Azure
uv export --no-dev --format requirements-txt -o requirements.txt

# 5. Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 6. Deploy Frontend manually
az webapp deploy \
  --resource-group <resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### 4.3 Re-ingest Vector Store After Deployment

> Required after new PDF documents are added or on first deploy to a clean environment.

```bash
curl -X POST https://<api-hostname>/ingest
# Monitor logs for: "[ingest] index saved (N chunks)"
```

### 4.4 Rollback Steps

**Option A — Git revert (preferred):**

```bash
# Identify the last good commit
git log --oneline -10

# Revert the bad commit
git revert <bad-commit-sha>
git push origin main
# CI/CD pipeline redeploys automatically
```

**Option B — Azure deployment slot swap:**
[TODO: confirm whether Azure deployment slots are configured for this App Service]

**Option C — Manual redeploy from previous tag:**

```bash
git checkout <last-good-tag>
uv export --no-dev --format requirements-txt -o requirements.txt
# Then repeat manual deployment steps above
```

**Post-rollback validation:**

```bash
# Run all health checks
curl -s -o /dev/null -w "%{http_code}" https://<api-hostname>/docs
curl -s -o /dev/null -w "%{http_code}" https://<frontend-hostname>/
```

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

```bash
# Stream live logs from Azure App Service
az webapp log tail \
  --resource-group <resource-group> \
  --name training-bot-api

az webapp log tail \
  --resource-group <resource-group> \
  --name training-bot-frontend
```

**Key log lines to watch:**

| Log Pattern | Meaning | Action |
|---|---|---|
| `Vector store loaded (N products)` | Startup OK | None |
| `No vector store found — run POST /ingest first` | Index missing | Run `/ingest` immediately |
| `annotation failed for … using raw chunker` | LLM annotation degraded | Check API key / model availability |
| `[ingest] embedding batch N–M / total` | Ingestion in progress | Normal |
| `[ingest] index saved (N chunks)` | Ingestion complete | None |
| `INFO: … 200 OK` | Successful request | None |
| `ERROR` / `Exception` | Application error | Investigate stack trace |

### 5.2 Metrics to Watch

[TODO: confirm whether Azure Application Insights or another APM tool is configured]

| Metric | Threshold | Notes |
|---|---|---|
| HTTP 5xx error rate | > 1% over 5 min | Indicates LLM or vector store failure |
| HTTP 4xx error rate | > 5% over 5 min | Auth issues or bad client requests |
| Response time (P95) | > 30 seconds | LLM streaming is slow; check provider |
| App Service CPU | > 80% sustained | Scale up or out |
| App Service Memory | > 85% | Vector store in-memory (FAISS); may need larger SKU |
| Deployment job duration | > 15 minutes | Investigate stuck deployment |

### 5.3 GitHub Actions Workflow Health

Monitor these scheduled workflows for failures:

| Workflow | Schedule | Alert if |
|---|---|---|
| Tool 1 — Code Review | Every Monday 08:00 UTC | Workflow fails |
| Tool 2 — Tech Documentation | Every Sunday 06:00 UTC + push to main | Workflow fails |
| Tool 4 — Auto Testing | Every Wednesday 07:00 UTC | Workflow fails |
| Test & Deploy | Every push to main | Any job fails |

[TODO: set up GitHub Actions failure notifications — email or Slack webhook]

### 5.4 External Dependency Health

| Dependency | How to Check |
|---|---|
| OpenRouter / LLM provider | [TODO: link to provider status page] |
| Anthropic (Claude, used by AI delivery tools) | https://status.anthropic.com |
| Azure App Service | https://status.azure.com |
| SendGrid (email notifications from AI tools) | https://status.sendgrid.com |

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in] | Service down, health checks failing |
| L2 | Backend/ML lead | [TODO: fill in] | RAG/vector store issues, LLM failures persisting > 30 min |
| L3 | Cloud/infrastructure | [TODO: fill in] | Azure App Service unavailable, secrets rotation needed |
| L4 | Project owner | kylo.deng@capco.com | Data breach, compliance issue, prolonged outage > 2 hours |
| External | OpenRouter support | [TODO: fill in] | LLM provider API degraded |
| External | Azure support | Via Azure Portal | App Service platform failure |

---

## 7. Useful Commands

### Application Management

```bash
# Start the FastAPI server locally
uv run uvicorn api.main:app --reload --port 8000

# Start with explicit host binding
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### Dependency Management

```bash
# Install all dependencies (including dev)
uv sync

# Generate production requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# Add a new dependency
uv add <package-name>
```

### Testing

```bash
# Run full test suite
uv run pytest tests/ -v

# Run with coverage report
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Run a single test file
uv run pytest tests/<test_file>.py -v
```

### Vector Store / Ingestion

```bash
# Ingest PDFs via CLI (local)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Trigger ingestion via API
curl -X POST http://localhost:8000/ingest

# Check known products in the store
curl http://localhost:8000/products | jq .
```

### Session Management

```bash
# List active sessions via API
curl http://localhost:8000/sessions | jq .

# Delete a specific session
curl -X DELETE http://localhost:8000/sessions/<session-id>

# Inspect sessions file directly
cat data/sessions.json | python3 -m json.tool | head -100
```

### Azure App Service

```bash
# View live logs — API
az webapp log tail \
  --resource-group <resource-group> \
  --name training-bot-api

# View live logs — Frontend
az webapp log tail \
  --resource-group <resource-group> \
  --name training-bot-frontend

# Restart the API app
az webapp restart \
  --resource-group <resource-group> \
  --name training-bot-api

# Show current app settings (environment variables)
az webapp config appsettings list \
  --resource-group <resource-group> \
  --name training-bot-api \
  | jq '[.[] | {name, value}]'

# Set/update an environment variable
az webapp config appsettings set \
  --resource-group <resource-group> \
  --name training-bot-api \
  --settings API_KEY="<new-value>"
```

### GitHub Actions — Manual Workflow Triggers

```bash
# Trigger Tech Docs generation manually
gh workflow run tool2_tech_docs.yml

# Trigger Code Review on