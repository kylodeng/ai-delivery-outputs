# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training environment for insurance sales agents in a Hong Kong context. It operates in two modes: a **Teacher mode**, where a LangGraph agent engages trainees in interactive coaching sessions using a RAG (Retrieval-Augmented Generation) pipeline backed by a vector store of insurance product PDFs, and a **Roleplay/Assessment mode**, where the system simulates a customer persona and subsequently scores the agent's performance across multiple dimensions. The backend is deployed to Azure App Service (`training-bot-api`) alongside a separate frontend deployment (`training-bot-frontend`), with CI/CD managed through GitHub Actions. The service additionally runs five AI-powered DevOps automation tools (code review, tech docs, business docs, auto-testing, and UAT facilitation), all powered by the Anthropic Claude API.

---

## 2. Health Checks

Perform the following checks to confirm the service is running correctly:

### API Server
```bash
# Check the FastAPI root or docs endpoint
curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/docs
# Expected: 200
```

### Vector Store Loaded
```bash
# Check ingest status — if the store is empty, the bot cannot answer product questions
curl -X POST https://training-bot-api.azurewebsites.net/ingest
# On startup, logs should contain:
# INFO: Vector store loaded (N products)
# WARNING: No vector store found — run POST /ingest first.   ← failure indicator
```

### LLM Connectivity
```bash
# Start a teacher session and send a test message; expect a streamed response
curl -X POST https://training-bot-api.azurewebsites.net/sessions \
  -H "Content-Type: application/json" \
  -d '{"mode":"teacher"}'
# Follow up with a chat message on the returned session_id
```

### Azure App Service Status
- Navigate to the Azure Portal → App Services → `training-bot-api` → **Overview** → confirm Status = **Running**.
- Navigate to `training-bot-frontend` → confirm Status = **Running**.

### GitHub Actions Pipelines
- Check [Actions tab](https://github.com/kylodeng/Insurance-Training-Bot-main/actions) — confirm the `Test & Deploy` workflow is green on `main`.

### Session Persistence
```bash
# Confirm sessions.json is accessible and non-empty
ls -lh data/sessions.json
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `WARNING: No vector store found` on startup | Vector store was never built, or `data/` directory is missing/empty after deployment | SSH into App Service or run locally: `POST /ingest`; confirm PDFs exist under `data/Insurance-product-info/` |
| LLM returns empty or garbled responses | `API_KEY` or `OPENAI_URL_BASE` env var not set, or OpenRouter/Anthropic API is down | Verify env vars in Azure App Service Configuration; check [OpenRouter status](https://openrouter.ai) or Anthropic status page |
| `SSL: CERTIFICATE_VERIFY_FAILED` errors in logs | `httpx.Client(verify=False)` is intentional but the upstream endpoint's cert may have expired | Confirm connectivity to `OPENAI_URL_BASE`; rotate to a different model endpoint if needed |
| FastAPI returns 500 on chat endpoints | LangGraph agent failed — often a tool call exception or malformed LLM JSON | Check Azure App Service log stream; look for `[DEBUG]` JSON parse errors; confirm all 8 RAG tools are registered |
| GitHub Actions `Test & Deploy` fails at `Run tests` | Pytest failures or missing test dependencies | Check Actions run log; run `uv run pytest tests/ -v` locally to reproduce |
| GitHub Actions deploy step fails with `publish-profile` error | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `_FRONTEND` secret is expired or missing | Regenerate publish profile from Azure Portal → App Service → **Get publish profile**; update GitHub secret |
| Claude AI tools (Tools 1–5) fail with `ANTHROPIC_API_KEY` error | Secret not set or rotated in GitHub repository secrets | Go to repo → Settings → Secrets → update `ANTHROPIC_API_KEY` |
| SendGrid email notifications not sent | `SENDGRID_API_KEY` missing or sender not verified | Verify secret; confirm `kylo.deng@capco.com` is a verified sender in SendGrid |
| Sessions lost after App Service restart | `sessions.json` is stored on ephemeral local disk, not persistent storage | Mount an Azure File Share to `data/` directory; or migrate session state to Azure Cosmos DB / Redis |
| Vector store data lost after restart | Chroma/FAISS index stored on local ephemeral disk | [TODO: Is the vector store persisted to Azure Blob Storage or only local disk? If local, mount persistent storage] |
| RAG tools return stale or wrong product data | PDF annotations (`.annot.json`) cached from a previous ingestion; PDFs updated without re-ingestion | Delete `.annot.json` sidecar files and re-run `POST /ingest` |
| `No module named 'langchain'` or import errors on startup | `uv sync` not run; requirements not installed on App Service | Confirm `requirements.txt` was generated by `uv export` and uploaded; check App Service startup command |
| CORS errors in browser | Allowed origins list in `main.py` does not include the frontend's deployed URL | Add the Azure App Service frontend URL to `allow_origins` in `main.py` and redeploy |
| `500` on `/ingest` — annotation LLM fails | `OPENAI_MODEL` or `OPENAI_URL_BASE` not set; annotation LLM falls back to raw chunker | Check env vars; annotation failure is non-fatal (falls back gracefully), but product metadata will be lower quality |

---

## 4. Deployment Procedure

### Prerequisites
- Azure CLI installed and authenticated (`az login`)
- `uv` installed (`pip install uv`)
- GitHub repository secrets configured (see Section 5)
- Python 3.13+

### Standard Deployment (via GitHub Actions — preferred)

1. **Ensure tests pass locally before pushing:**
   ```bash
   uv sync
   uv run pytest tests/ -v
   ```

2. **Merge changes to `main` branch** (via PR or direct push):
   ```bash
   git checkout main
   git merge feature/your-branch
   git push origin main
   ```

3. **Monitor the GitHub Actions workflow:**
   - Navigate to Actions → `Test & Deploy`
   - Confirm `test` job passes
   - Confirm `deploy-api` and `deploy-frontend` jobs complete successfully

4. **Verify deployment:**
   ```bash
   curl -s -o /dev/null -w "%{http_code}" https://training-bot-api.azurewebsites.net/docs
   # Expected: 200
   ```

5. **Re-ingest PDFs if product data changed:**
   ```bash
   curl -X POST https://training-bot-api.azurewebsites.net/ingest
   ```

6. **Smoke-test a teacher session** via the frontend UI or API.

---

### Manual Deployment (emergency / bypass CI)

```bash
# 1. Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API manually via Azure CLI
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 3. Deploy frontend manually
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

---

### Rollback Steps

**Option A — GitHub Actions rollback (preferred):**

1. Identify the last known-good commit SHA:
   ```bash
   git log --oneline main | head -10
   ```
2. Revert the bad commit:
   ```bash
   git revert <bad-commit-sha>
   git push origin main
   ```
3. The `Test & Deploy` workflow will trigger automatically and redeploy the reverted code.

**Option B — Azure App Service deployment slot swap:**

> [TODO: Are deployment slots configured on the Azure App Service? If so, document the swap command here.]

```bash
# If slots are configured:
az webapp deployment slot swap \
  --resource-group <your-resource-group> \
  --name training-bot-api \
  --slot staging \
  --target-slot production
```

**Option C — Manual redeploy from previous tag:**
```bash
git checkout v<last-good-version>
uv export --no-dev --format requirements-txt -o requirements.txt
az webapp deploy --resource-group <rg> --name training-bot-api --src-path . --type zip
```

---

## 5. Monitoring & Alerting

### Required GitHub Secrets

| Secret | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API access for all 5 AI tools |
| `GH_TOKEN` | GitHub API access (PR comments, file writes) |
| `SENDGRID_API_KEY` | Email notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Azure deploy — API |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Azure deploy — Frontend |
| `API_KEY` | LLM provider (OpenRouter or direct) API key |

### Key Metrics to Watch

| Metric | Where to Check | Alert Threshold |
|---|---|---|
| App Service HTTP 5xx errors | Azure Portal → App Service → Metrics → HTTP Server Errors | > 5 errors / 5 min |
| App Service response time | Azure Portal → App Service → Metrics → Response Time | > 10 seconds average |
| App Service availability | Azure Portal → App Service → Health Check | < 100% |
| GitHub Actions workflow failure rate | GitHub Actions tab | Any failure on `main` |
| LLM API latency | App Service log stream — look for slow `call_claude` returns | [TODO: Define SLA threshold] |
| Vector store chunk count | Startup log line: `Vector store loaded (N products)` | N = 0 is a critical failure |
| Session file size | `data/sessions.json` | [TODO: Define max size before migration to DB is required] |

### Log Locations

| Log | Location |
|---|---|
| FastAPI application logs | Azure Portal → `training-bot-api` → Log stream |
| GitHub Actions run logs | GitHub → Actions tab → individual workflow run |
| AI tool audit logs | [TODO: Is `write_audit_entry` writing to a file or external service? Confirm location.] |
| Ingestion logs | App Service log stream — prefixed with `[ingest]` |
| Tool call debug output | App Service log stream — prefixed with `[DEBUG]` |

### Log Lines to Alert On

```
WARNING: No vector store found          # Critical — RAG is broken
Could not parse Claude response as JSON # LLM output parsing failure
No JSON object found in Claude response # LLM output parsing failure
annotation failed for                   # Non-critical, but degrades metadata quality
```

### Alerting Setup

> [TODO: Are Azure Monitor alerts or Application Insights configured? If not, recommended setup:]

```bash
# Create an alert rule for HTTP 5xx errors
az monitor metrics alert create \
  --name "API 5xx Alert" \
  --resource-group <rg> \
  --scopes /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Web/sites/training-bot-api \
  --condition "total Http5xx > 5" \
  --window-size 5m \
  --evaluation-frequency 1m \
  --action <action-group-id>
```

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: Name & contact] | Service down, 5xx rate spike, LLM API outage |
| L2 | Tech Lead | [TODO: Name & contact] | Data loss, vector store corruption, secrets compromised |
| L3 | Solution Owner | [TODO: Name & contact] | Prolonged outage > 1 hour, security incident |
| External | Azure Support | [TODO: Azure support contract tier & portal link] | Azure infrastructure failures |
| External | Anthropic / OpenRouter Support | [TODO: Support URL] | LLM API outage or key revocation |
| Notifications | Default notify email | `kylo.deng@capco.com` | All automated AI tool completions and failures |

---

## 7. Useful Commands

### Local Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Start the FastAPI server locally
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Ingest PDFs into vector store (run once, or after PDF updates)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# View API docs
open http://localhost:8000/docs
```

### Ingestion & Vector Store

```bash
# Trigger ingestion via API
curl -X POST http://localhost:8000/ingest

# Delete annotation sidecar files to force re-annotation
find data/ -name "*.annot.json" -delete

# Check how many product PDFs exist
find data/Insurance-product-info -name "*.pdf" | wc -l
```

### Sessions

```bash
# List active sessions
curl http://localhost:8000/sessions

# Delete a specific session
curl -X DELETE http://localhost:8000/sessions/<session_id>

# Backup sessions before restart
cp data/sessions.json data/sessions.json.bak
```

### Azure App Service

```bash
# Stream live logs
az webapp log tail --name training-bot-api --resource-group <rg>

# Restart the API service
az webapp restart --name training-bot-api --resource-group <rg>

# Restart the frontend service
az webapp restart --name training-bot-frontend --resource-group <rg>

# Check App Service status
az webapp show --name training-bot-api --resource-group <rg> \
  --query "{state:state, defaultHostName:defaultHostName}" -o table

# View environment variables set on App Service
az webapp config appsettings list --name training-bot-api --resource-group <rg> -o table

# Set or update an environment variable
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg> \
  --settings API_KEY="<new-key>"
```

### GitHub Actions — Manual Triggers

```bash
# Manually trigger Tech Docs generation
gh workflow run tool2_tech_docs.yml --repo kylodeng/Insurance-Training-Bot-main

# Manually trigger Code Review on a PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/Insurance-Training-Bot-main \
  -f review_mode=pr \
  -f pr_number=42

# Manually trigger UAT test pack generation
gh workflow run tool5_uat.yml \
  --repo kylodeng/Insurance-Training-Bot-main \
  -f uat_mode=generate \
  -f release_version=1.0.0

# View recent workflow runs
gh run list --repo kylodeng/Insurance-Training-Bot-main --limit 10
```

### Requirements Generation

```bash
# Regenerate requirements.txt from uv lockfile (needed for Azure deployment)
uv export --no-dev --format requirements-txt -o requirements.txt
```

---

*This runbook was generated from source analysis of `kylodeng/Insurance-Training-Bot-main`. Items marked `[TODO: ...]` require confirmation from the owning team.*