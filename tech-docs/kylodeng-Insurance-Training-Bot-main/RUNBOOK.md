# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application designed to train insurance sales agents in a Hong Kong context. It provides two core interaction modes: a **Teacher mode**, where a LangGraph agent coaches agents interactively on products, discovery questioning, and sales technique using a RAG (Retrieval-Augmented Generation) pipeline backed by a vector store of insurance product PDFs; and a **Roleplay/Assessor mode**, where the agent simulates a realistic Hong Kong customer profile and subsequently scores the trainee's performance across five dimensions. The backend is deployed to **Azure App Service** (`training-bot-api`), with a separate frontend deployment (`training-bot-frontend`). PDF knowledge base documents (product brochures, hospital network lists, etc.) are ingested into a local vector store at startup. An LLM is accessed via OpenRouter (or a compatible endpoint) using the `ChatOpenAI` LangChain adapter.

---

## 2. Health Checks

### 2.1 API Service

```bash
# Check FastAPI root is responsive
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/

# Check FastAPI docs endpoint
curl -s https://training-bot-api.azurewebsites.net/docs | head -20
```

Expected: HTTP `200` on both.

### 2.2 Vector Store

```bash
# POST /ingest to verify store is loaded (do NOT re-ingest in prod unless needed)
# Instead, check the startup log for this line:
# INFO: Vector store loaded (N products)
# If you see:
# WARNING: No vector store found — run POST /ingest first.
# → the vector store is missing or corrupt
```

```bash
# Trigger a test product listing via the API (once session/tool endpoints are exposed)
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

> [TODO: Confirm the exact path of the `/ingest` endpoint and whether it is protected by auth]

### 2.3 LLM Connectivity

```bash
# Verify environment variable is set and reachable
curl -s -H "Authorization: Bearer $API_KEY" \
  $OPENAI_URL_BASE/models | jq '.data[].id' | head -5
```

Expected: List of available models returned without auth errors.

### 2.4 Sessions Persistence

```bash
# Check sessions file exists and is valid JSON
python3 -c "import json; print(json.load(open('data/sessions.json')))"
```

### 2.5 Azure App Service Status

```bash
az webapp show --name training-bot-api --resource-group <rg-name> \
  --query "state" -o tsv
# Expected output: Running
```

> [TODO: What is the Azure resource group name?]

### 2.6 GitHub Actions (CI/CD Pipeline Health)

- Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`
- Confirm the **Test & Deploy** workflow last run shows ✅ on `main`.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all requests | LLM API key missing or invalid (`API_KEY` env var) | 1. Check Azure App Service → Configuration → `API_KEY`. 2. Rotate key at OpenRouter. 3. Redeploy or restart the app. |
| `WARNING: No vector store found — run POST /ingest first` in logs | Vector store file absent or not persisted across deployment | 1. Call `POST /ingest` via API. 2. Confirm `data/` directory is mounted/persisted on Azure (not ephemeral). 3. See [TODO: confirm Azure persistent storage config]. |
| RAG tools return empty results / agent says "I don't know" | PDF not ingested, or embeddings corrupted | 1. Check `data/` for `.annot.json` sidecars. 2. Re-run ingest: `POST /ingest`. 3. Check embedding model connectivity (Voyage AI / OpenRouter). |
| SSL verification errors in logs (`verify=False` workaround active) | Self-signed cert or corporate proxy intercepting TLS | 1. Confirm if behind a proxy. 2. Supply correct CA bundle via `REQUESTS_CA_BUNDLE` env var. 3. Replace `verify=False` with proper cert path (security risk — see Note below). |
| `sessions.json` not found or `JSONDecodeError` on startup | File corrupt or missing after deployment | 1. `rm data/sessions.json` and restart — sessions will reinitialise empty. 2. Restore from backup if session history is needed. |
| GitHub Actions `Test & Deploy` workflow fails on `test` job | pytest failures or missing test dependencies | 1. Check Actions log for failing test. 2. Run `uv run pytest tests/ -v` locally. 3. Fix failing tests and push fix to `main`. |
| Deployment job fails: `azure/webapps-deploy` error | Expired or incorrect publish profile secret | 1. Download new publish profile from Azure portal. 2. Update `AZURE_WEBAPP_PUBLISH_PROFILE_API` / `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secrets in GitHub. |
| LLM returns malformed JSON (agent tool parsing error) | Model hallucinating non-JSON or markdown fences in tool responses | 1. Check `OPENAI_MODEL` env var — ensure it points to a capable model. 2. Inspect logs for `[DEBUG] First 500 chars` output. 3. Switch to a more reliable model temporarily. |
| Frontend unreachable / CORS errors | CORS middleware missing the deployed frontend origin | 1. Add the production frontend URL to `allow_origins` in `main.py`. 2. Redeploy. |
| Streaming responses stop mid-sentence | Timeout on Azure App Service (default 230s) or OpenRouter rate limit | 1. Increase Azure idle timeout: `az webapp config set --idle-timeout 300`. 2. Check OpenRouter rate limit dashboard. |
| Annotation LLM calls fail during ingest | `API_KEY` or `OPENAI_URL_BASE` wrong for annotation LLM in `ingest.py` | 1. Verify env vars. 2. Check `core/ingest.py` `_build_ingest_llm()` — it defaults to Anthropic base URL, which may differ from runtime LLM. |
| `Tool 1–5` GitHub Action workflows fail | Missing `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` secrets | 1. Go to repo Settings → Secrets. 2. Add/rotate missing secrets. 3. Re-run workflow. |

> ⚠️ **Security Note:** `httpx.Client(verify=False)` is used in production code. This disables TLS verification and is a security risk. [TODO: Track and remediate this — supply correct CA bundle instead.]

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI installed and logged in (`az login`)
- `uv` installed locally
- GitHub repository secrets configured:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `API_KEY` (set in Azure App Service Configuration, not GitHub secrets)
  - `OPENAI_URL_BASE`, `OPENAI_MODEL` (Azure App Service Configuration)

### 4.1 Normal Deployment (Automated via GitHub Actions)

```
Push to main branch → GitHub Actions "Test & Deploy" runs automatically
```

1. **Commit and push** your changes to the `main` branch:
   ```bash
   git checkout main
   git pull origin main
   git add .
   git commit -m "feat: <description>"
   git push origin main
   ```

2. **Monitor the workflow** at:
   ```
   https://github.com/kylodeng/Insurance-Training-Bot-main/actions
   ```

3. **Confirm jobs complete**:
   - `test` → all pytest tests pass
   - `deploy-api` → `training-bot-api` app service updated
   - `deploy-frontend` → `training-bot-frontend` app service updated

4. **Post-deployment health check**:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/
   # Expected: 200
   ```

5. **Re-ingest vector store** if PDF knowledge base changed:
   ```bash
   curl -X POST https://training-bot-api.azurewebsites.net/ingest
   # Monitor logs for: INFO: index saved (N chunks)
   ```

---

### 4.2 Manual Deployment (Emergency / Out-of-band)

```bash
# 1. Install dependencies and generate requirements.txt
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Zip and deploy to Azure manually (API)
az webapp deploy \
  --name training-bot-api \
  --resource-group <rg-name> \
  --src-path . \
  --type zip

# 3. Zip and deploy to Azure manually (Frontend)
az webapp deploy \
  --name training-bot-frontend \
  --resource-group <rg-name> \
  --src-path . \
  --type zip
```

> [TODO: What is the Azure resource group name?]  
> [TODO: Is the frontend a separate Vite build step required before deploy?]

---

### 4.3 Rollback Procedure

#### Option A — Revert via Git (Preferred)

```bash
# Find the last known-good commit
git log --oneline -10

# Revert to previous commit
git revert HEAD --no-edit
git push origin main
# This triggers the CI/CD pipeline automatically
```

#### Option B — Azure Deployment Slot Swap

```bash
# If deployment slots are configured:
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group <rg-name> \
  --slot staging \
  --target-slot production
```

> [TODO: Confirm whether Azure deployment slots are configured for staging/production swap]

#### Option C — Redeploy Previous GitHub Actions Run

1. Go to Actions → select the last successful **Test & Deploy** run.
2. Click **Re-run jobs** → **Re-run all jobs**.

#### Rollback Checklist

- [ ] API returns HTTP 200
- [ ] Vector store loaded message in logs (or re-ingest if needed)
- [ ] Teacher mode responds correctly in UI
- [ ] Roleplay mode generates a valid customer profile
- [ ] Assessor produces a score report

---

## 5. Monitoring & Alerting

### 5.1 Key Logs to Watch

| Log Location | What to Look For |
|---|---|
| Azure App Service → Log Stream | `WARNING: No vector store found`, `ERROR`, `500` HTTP responses |
| Azure App Service → Log Stream | `INFO: Vector store loaded (N products)` on startup (confirm N > 0) |
| Azure App Service → Log Stream | `[DEBUG] First 500 chars of raw response` — indicates LLM JSON parse failure |
| GitHub Actions logs | Test failures, deployment errors, secret missing errors |
| Application logs | `[ingest] annotation failed for <file>` — PDF annotation LLM errors |

### 5.2 Metrics to Monitor (Azure Portal)

| Metric | Location | Alert Threshold |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Metrics → Http Server Errors | > 5 errors/min |
| Response time (P95) | Azure App Service → Metrics → Average Response Time | > 30s |
| CPU usage | Azure App Service → Metrics → CPU Percentage | > 80% sustained 5min |
| Memory usage | Azure App Service → Metrics → Memory Working Set | > 80% of plan limit |
| Availability | Azure App Service → Availability | < 99% |

### 5.3 Alerting

> [TODO: Are Azure Monitor alerts configured? If not, set up the following:]

Recommended alerts to create in Azure Monitor:
- HTTP 5xx rate > 5/min → PagerDuty / email
- App Service restart detected → email
- Deployment failure in GitHub Actions → GitHub email notification (already on by default)

### 5.4 LLM Usage Monitoring

> [TODO: Is OpenRouter usage dashboard being monitored for rate limits and cost?]

- Monitor OpenRouter dashboard at: `https://openrouter.ai/`
- Watch for `429 Too Many Requests` errors in App Service logs
- Monitor Voyage AI (embedding model) usage if used for ingestion

### 5.5 GitHub Actions Workflow Health

Workflows to monitor weekly:
| Workflow | Schedule | Purpose |
|---|---|---|
| Test & Deploy | On push to `main` | CI/CD |
| Tool 1 — Code Review | Every Monday 08:00 UTC | Automated PR review |
| Tool 2 — Tech Documentation | Every Sunday 06:00 UTC | Auto doc generation |
| Tool 4 — Auto Testing | Every Wednesday 07:00 UTC | Test generation |

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call contact] | Service down, 5xx rate spike, vector store missing |
| L2 | Tech Lead / Backend Owner | [TODO: fill in tech lead contact] | LangGraph agent logic failure, RAG quality issues, data ingestion failures |
| L3 | Platform / Azure Admin | [TODO: fill in Azure admin contact] | Azure App Service unavailable, deployment pipeline broken, resource limits hit |
| L4 | LLM Provider Support | OpenRouter support / Anthropic support | API key issues, model deprecation, rate limit increases |
| Business | Product Owner | kylo.deng@capco.com | Business logic questions, insurance content accuracy |

> [TODO: Fill in all team contacts, PagerDuty escalation policy, and on-call rotation]

---

## 7. Useful Commands

### 7.1 Local Development

```bash
# Clone and set up
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# Install dependencies
uv sync

# Copy and configure environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env:
#   API_KEY=<your-openrouter-or-anthropic-key>
#   OPENAI_URL_BASE=https://openrouter.ai/api/v1
#   OPENAI_MODEL=openai/gpt-oss-20b:free
#   SHOW_TOOL_CALLS=true

# Start the FastAPI server
uv run uvicorn api.main:app --reload --port 8000

# Check it's running
curl http://localhost:8000/docs
```

### 7.2 Ingest PDFs into Vector Store

```bash
# Ingest all PDFs from ./data directory (local)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Or via API endpoint (once server is running)
curl -X POST http://localhost:8000/ingest
```

### 7.3 Run Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Run a single test file
uv run pytest tests/test_sessions.py -v
```

### 7.4 Azure Operations

```bash
# View live logs from API app service
az webapp log tail \
  --name training-bot-api \
  --resource-group <rg-name>

# Restart the API app service
az webapp restart \
  --name training-bot-api \
  --resource-group <rg-name>

# Restart the frontend app service
az webapp restart \
  --name training-bot-frontend \
  --resource-group <rg-name>

# Check app service status
az webapp show \
  --name training-bot-api \
  --resource-group <rg-name> \
  --query "{state:state, defaultHostName:defaultHostName}" -o table

# View app settings (env vars)
az webapp config appsettings list \
  --name training-bot-api \
  --resource-