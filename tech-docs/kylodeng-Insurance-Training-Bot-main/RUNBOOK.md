# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training environment for insurance sales agents in the Hong Kong market. It operates in two modes: a **Teacher mode**, which conducts interactive, multi-turn coaching sessions using a LangGraph agent backed by a RAG (Retrieval-Augmented Generation) pipeline over ingested insurance product PDFs, and a **Roleplay/Assessment mode**, which simulates realistic customer interactions with randomised Hong Kong customer profiles and subsequently scores the agent's performance across multiple dimensions. The backend is deployed as an Azure App Service (`training-bot-api`) and a separate frontend App Service (`training-bot-frontend`), with CI/CD managed through GitHub Actions. The vector store (Chroma or FAISS) is built from insurance product PDFs stored under `data/Insurance-product-info/` and must be ingested via `POST /ingest` before RAG tools are functional. The system also includes five AI-powered DevOps automation tools (code review, tech docs, business docs, auto-testing, UAT) that run as separate GitHub Actions workflows against the Anthropic Claude API.

---

## 2. Health Checks

Run these checks in order to confirm the service is fully operational.

### 2.1 API Backend (`training-bot-api`)

```bash
# Basic liveness — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/

# FastAPI auto-generated docs page — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/docs
```

### 2.2 Vector Store (RAG readiness)

```bash
# Confirm products are loaded — expect a non-empty JSON list
curl -s https://training-bot-api.azurewebsites.net/products
# If empty or 500, the vector store is not loaded → run POST /ingest
```

### 2.3 Frontend (`training-bot-frontend`)

```bash
# Expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
```

### 2.4 Sessions Persistence

```bash
# List active sessions — expect HTTP 200 and a JSON array
curl -s https://training-bot-api.azurewebsites.net/sessions
```

### 2.5 Static Assets (PDF serving)

```bash
# Confirm a known PDF is reachable over /docs/
curl -s -o /dev/null -w "%{http_code}" \
  https://training-bot-api.azurewebsites.net/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
# Expect 200
```

### 2.6 LLM Connectivity

```bash
# Trigger a minimal teacher-mode chat and confirm a streamed response
curl -s -X POST https://training-bot-api.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"healthcheck","message":"hello"}' | head -c 200
```

### 2.7 GitHub Actions Workflows

In the GitHub repository, navigate to **Actions** and confirm:
- `Test & Deploy` workflow last run: **green**
- No workflows stuck in `queued` state for > 10 minutes

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `POST /ingest` returns 500 or vector store shows 0 products | PDF files missing from `data/Insurance-product-info/`, annotation LLM call failed, or `API_KEY`/`OPENAI_URL_BASE` env var wrong | 1. Confirm PDFs exist under `data/`. 2. Check `API_KEY` and `OPENAI_URL_BASE` env vars on the App Service. 3. Re-run `POST /ingest`. 4. Check Azure App Service logs for traceback. |
| Chat endpoint returns `500` / agent produces no tool calls | `API_KEY` invalid or OpenRouter/Anthropic API unreachable | 1. Test the LLM API key directly (see Useful Commands). 2. Check `OPENAI_URL_BASE` points to correct endpoint. 3. Check Azure outbound networking/firewall rules. |
| RAG tools return empty results (`[]`) for product queries | Vector store not loaded at startup (missing `data/chroma/` or `data/faiss/` index) | 1. `GET /products` to confirm. 2. `POST /ingest` to rebuild. 3. Confirm `data/` directory is mounted/persisted on the App Service (not ephemeral). |
| `sessions.json` is empty after server restart | App Service ephemeral filesystem — `data/sessions.json` is not on persistent storage | 1. Confirm `data/` is mounted to an Azure Files share or Persistent Storage. [TODO: Is Azure Files mount configured for App Service?] 2. If not, sessions will be lost on every restart — configure persistent storage immediately. |
| SSL verification errors in logs (`InsecureRequestWarning`) | `verify=False` is set in `httpx.Client` (intentional in code, but may indicate proxy/cert issue in prod) | 1. Confirm this is intentional for the target environment. 2. [TODO: Should TLS verification be enabled in production? Provide a valid CA bundle if so.] |
| Frontend cannot reach backend (`CORS 403`) | `allow_origins` list in `main.py` does not include the production frontend URL | 1. Add the production frontend URL to `allow_origins` in `api/main.py`. 2. Redeploy via `git push origin main`. |
| GitHub Actions `Test & Deploy` fails on `pytest` step | Broken import, missing dependency, or test fixture failure | 1. Check the Actions log for the failing test name. 2. Run `uv run pytest tests/ -v` locally to reproduce. 3. Fix the failing test and push. |
| GitHub Actions workflow stuck / never starts | Secrets (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`) expired or missing | 1. In GitHub repo → Settings → Secrets, verify both secrets exist. 2. Re-download publish profiles from Azure Portal and update secrets. |
| AI DevOps tools (Tool 1–5) fail | `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` missing or revoked | 1. Check Actions env vars in workflow logs. 2. Rotate/update secrets in GitHub repo settings. |
| `POST /ingest` annotation step fails with LLM JSON parse error | LLM returned markdown-fenced JSON or unexpected format | 1. Check `data/*.annot.json` sidecar files for corruption. 2. Delete the relevant `.annot.json` sidecar file to force re-annotation. 3. Re-run ingest. |
| Roleplay customer profile generation fails | `generate_profile()` depends on the LLM; if LLM is down, profile generation fails silently | 1. Check `/sessions` for sessions with missing profile data. 2. Test LLM connectivity. 3. Retry profile generation via `POST /sessions`. |
| App Service shows HTTP 503 | App Service plan scaled down or instance crashed | 1. Check Azure Portal → App Service → Overview for instance health. 2. Restart the App Service. 3. Check if plan has sufficient compute. [TODO: What is the App Service plan SKU?] |
| `uv sync` fails in CI | `pyproject.toml` or `uv.lock` out of sync | 1. Run `uv lock` locally and commit updated `uv.lock`. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- GitHub repo write access
- Secrets set in GitHub: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- Python 3.13 and `uv` installed locally

### 4.1 Standard Deployment (Automated via GitHub Actions)

```
1. Commit and push changes to a feature branch.
2. Open a Pull Request against `main`.
   → GitHub Actions: "Tool 1 — Code Review" triggers automatically.
   → Review the Claude code review comment on the PR.
3. Merge the PR into `main` after approval.
   → GitHub Actions: "Test & Deploy" triggers automatically.
   → Step 1 (test job): runs `uv run pytest tests/ -v`.
   → Step 2 (deploy-api job): deploys to Azure App Service `training-bot-api`.
   → Step 3 (deploy-frontend job): deploys to Azure App Service `training-bot-frontend`.
4. Monitor the Actions run at:
   https://github.com/kylodeng/Insurance-Training-Bot-main/actions
5. Once green, run health checks (Section 2) to confirm.
```

### 4.2 Manual / Emergency Deployment

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

### 4.3 Post-Deployment Steps

```bash
# 1. Confirm the API is up
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/docs

# 2. If this is a fresh deployment or PDFs have changed, re-ingest the vector store:
curl -s -X POST https://training-bot-api.azurewebsites.net/ingest

# 3. Confirm products loaded:
curl -s https://training-bot-api.azurewebsites.net/products

# 4. Run a smoke test chat:
curl -s -X POST https://training-bot-api.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"smoke","message":"list all products"}'
```

### 4.4 Rollback Procedure

```
Option A — GitHub Actions re-deploy previous commit:
1. In GitHub → Actions → "Test & Deploy" → find the last successful run.
2. Click "Re-run jobs" on the last known-good run.
   Note: This will re-deploy the last successful build artifact.

Option B — Azure Portal rollback:
1. Go to Azure Portal → App Service (training-bot-api) → Deployment Center.
2. Select a previous deployment slot or swap to a staging slot if configured.
   [TODO: Is a staging/swap slot configured on the App Service?]

Option C — Git revert:
```

```bash
# Identify the last good commit
git log --oneline -10

# Revert to last known-good commit
git revert <bad-commit-sha>
git push origin main
# CI/CD will redeploy automatically
```

```
3. After rollback, run health checks (Section 2) and re-ingest if needed.
```

---

## 5. Monitoring & Alerting

### 5.1 Key Metrics to Watch

| Metric | Source | Warning Threshold | Critical Threshold |
|---|---|---|---|
| API HTTP 5xx error rate | Azure App Service → Metrics → HTTP 5xx | > 1% over 5 min | > 5% over 5 min |
| API response time (P95) | Azure App Service → Metrics → Response Time | > 3 s | > 10 s |
| App Service instance availability | Azure App Service → Metrics → Availability | < 99% | < 95% |
| Vector store product count | `GET /products` response length | 0 products | N/A — page alert |
| GitHub Actions failure rate | GitHub Actions summary | Any failed `main` branch run | Two consecutive failures |
| LLM API latency | [TODO: No custom metrics instrumentation found — add Prometheus/Application Insights] | — | — |
| `sessions.json` file size growth | Azure Storage / App Service file system | [TODO: Set a threshold] | [TODO: Set a threshold] |

### 5.2 Logs to Watch

```bash
# Azure App Service live log stream (CLI)
az webapp log tail \
  --resource-group <resource-group> \
  --name training-bot-api

# Key log patterns to alert on:
grep -E "ERROR|WARNING|No vector store found|annotation failed|Could not parse" app.log
```

**Critical log strings:**

| Log Message | Meaning |
|---|---|
| `No vector store found — run POST /ingest first.` | RAG is non-functional; agents will hallucinate |
| `annotation failed for <file>` | A PDF was not annotated; chunks may be lower quality |
| `Could not parse Claude response as JSON` | AI tool returned unparseable output |
| `InsecureRequestWarning` | TLS verification disabled — review in production |
| `[ingest] index saved` | Successful ingest — note chunk count |

### 5.3 Recommended Alerting Setup

```
[TODO: Configure the following in Azure Monitor / Application Insights:]
- Alert: HTTP 5xx > 5 per minute on training-bot-api → notify on-call
- Alert: App Service restarts > 2 in 10 minutes → notify on-call
- Alert: GitHub Actions workflow failure on main branch → notify team lead
- Log query alert: "No vector store found" in App Service logs → P1 alert
```

### 5.4 GitHub Actions Workflow Health

Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

Monitor weekly scheduled workflows:
- `Tool 1 — Code Review`: every Monday 08:00 UTC
- `Tool 2 — Tech Documentation`: every Sunday 06:00 UTC
- `Tool 4 — Auto Testing`: every Wednesday 07:00 UTC

---

## 6. Escalation Path

```
P1 — Service down / vector store empty / all users impacted:
  1. On-call engineer: [TODO: fill in name and contact]
  2. Tech Lead:        [TODO: fill in name and contact]
  3. Azure Support:    https://portal.azure.com → Help + Support

P2 — Degraded performance / single workflow failing:
  1. Primary engineer: [TODO: fill in name and contact]
  2. Notify:           kylo.deng@capco.com

P3 — Non-urgent / documentation / AI tool output quality:
  1. Raise a GitHub Issue in kylodeng/Insurance-Training-Bot-main
  2. Notify:           kylo.deng@capco.com

External dependencies:
  - OpenRouter / Anthropic API issues: https://status.anthropic.com
  - Azure App Service issues:          https://status.azure.com
  - SendGrid email delivery issues:    https://status.sendgrid.com
```

---

## 7. Useful Commands

### 7.1 Local Development

```bash
# Install dependencies
uv sync

# Copy and configure environment variables
cp .env.example .env
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Start the API server
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Ingest PDFs into vector store (run once after first setup or when PDFs change)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Or via the API endpoint:
curl -X POST http://localhost:8000/ingest
```

### 7.2 Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage report
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Run a specific test file
uv run pytest tests/test_chunker.py -v
```

### 7.3 Vector Store Operations

```bash
# Check what products are in the vector store
curl -s http://localhost:8