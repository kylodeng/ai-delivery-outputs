# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based AI coaching platform designed to help new insurance agents in Hong Kong master product knowledge and sales technique. It operates in two modes: **Teacher mode**, which runs an ongoing streamed chat session powered by a LangGraph agent that answers questions about insurance products using a RAG (Retrieval-Augmented Generation) pipeline backed by a vector store of ingested insurance PDFs; and **Roleplay mode**, which simulates a customer interaction using a randomly generated Hong Kong customer profile, followed by an **Assessor mode** that evaluates the agent's performance across five dimensions. The backend is deployed as an Azure App Service (`training-bot-api`), with a separate Azure App Service for the frontend (`training-bot-frontend`), and uses an OpenRouter-proxied LLM (default: `openai/gpt-oss-20b:free`) with optional Claude fallback via Anthropic. PDF knowledge-base documents (Sun Life insurance brochures, hospital network lists) are ingested into a local vector store on first use via `POST /ingest`. Sessions are persisted to `data/sessions.json` so conversation history survives server restarts.

---

## 2. Health Checks

### API Service

| Check | How to verify |
|---|---|
| FastAPI process running | `curl -s https://<api-hostname>/docs` returns Swagger UI (HTTP 200) |
| Lifespan startup completed | App logs show: `Vector store loaded (N products)` |
| Vector store loaded | `GET /ingest` or check startup log; if absent, log shows `No vector store found — run POST /ingest first.` |
| LLM reachability | Send a test teacher-mode message and confirm a streamed response is returned |
| Static file mount | `curl -I https://<api-hostname>/docs/<any-pdf-filename>` returns HTTP 200 |
| Sessions file readable | `data/sessions.json` exists and is valid JSON |
| CORS | Browser DevTools network tab shows no CORS errors from the frontend origin |

### Frontend Service

| Check | How to verify |
|---|---|
| Frontend App Service running | Navigate to `https://<frontend-hostname>` in a browser |
| API connectivity | Open the chat UI, send a message, confirm a response streams back |

### CI/CD

| Check | How to verify |
|---|---|
| GitHub Actions test job passing | Green tick on `Test & Deploy` workflow in the repository Actions tab |
| Azure deploy job passing | `deploy-api` and `deploy-frontend` jobs both show success |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Startup log shows `No vector store found — run POST /ingest first.` | PDF data has never been ingested, or the vector store index file was deleted/not persisted across deploy | SSH into the App Service or trigger `POST /ingest` endpoint; ensure the `data/` directory is on a persistent volume |
| LLM returns 401 / authentication error | `API_KEY` environment variable missing or expired | Verify `API_KEY` is set in Azure App Service application settings; rotate key if expired |
| LLM returns 429 / rate limit exceeded | Too many concurrent requests to OpenRouter or Anthropic | Implement backoff or switch to a higher-tier model/key; reduce concurrent session count |
| `httpx.ConnectError` or SSL verification failure | `verify=False` suppresses SSL errors but underlying network connectivity is broken | Check Azure outbound network rules; confirm OpenRouter/Anthropic endpoints are reachable from the App Service |
| Teacher agent returns wrong product details / hallucinations | Vector store empty or poorly chunked; LLM not calling RAG tools | Re-run ingest pipeline (`POST /ingest`); check `OPENAI_MODEL` env var points to a capable model; inspect tool-call logs with `SHOW_TOOL_CALLS=true` |
| `sessions.json` not found / JSON decode error on startup | File corrupted or permissions issue | Delete `data/sessions.json` and restart (sessions will reset); investigate disk permissions |
| FastAPI returns 500 on `/chat` | Unhandled exception in agent or RAG tool | Check application logs (`logger.error`); enable `SHOW_TOOL_CALLS=true` to trace tool execution |
| Assessment (Assessor agent) never completes | LLM context limit exceeded by long roleplay conversation | Truncate conversation history before passing to assessor; review `max_tokens` settings |
| Frontend CORS errors | `allow_origins` list in `main.py` does not include the deployed frontend hostname | Add the production frontend URL to `allow_origins` in `main.py` and redeploy |
| GitHub Actions deploy fails: `AZURE_WEBAPP_PUBLISH_PROFILE_API` not found | GitHub secret missing or expired | Regenerate publish profile from Azure Portal → App Service → Deployment Center; update GitHub secret |
| `uv sync` fails in CI | `pyproject.toml` or lock file out of sync | Run `uv lock` locally and commit the updated lock file |
| PDF annotation LLM calls fail during ingest | Annotation LLM (`OPENAI_URL_BASE` / `API_KEY`) unreachable or model incompatible | Check environment variables; ingest will fall back to raw heuristic chunker automatically — verify log says `annotation failed … using raw chunker` |
| `POST /ingest` runs but returns 0 products | No PDF files found under `data/` directory | Confirm PDFs are present under `data/Insurance-product-info/`; check file permissions; re-upload if missing |
| Vector store search returns irrelevant results | Embedding model mismatch after a redeploy | Clear the existing vector store index and re-ingest all PDFs from scratch |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- GitHub repository secrets configured:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `API_KEY`, `ANTHROPIC_API_KEY`, `SENDGRID_API_KEY`, `GH_TOKEN`
- `uv` installed locally
- Python 3.13

### Standard Deployment (via CI/CD)

1. Ensure all tests pass locally:
   ```bash
   uv sync
   uv run pytest tests/ -v
   ```

2. Commit and push changes to the `main` branch:
   ```bash
   git add .
   git commit -m "feat: <description>"
   git push origin main
   ```

3. Monitor the `Test & Deploy` workflow in GitHub Actions:
   - `test` job runs `pytest` — must pass before deploy jobs start.
   - `deploy-api` deploys to Azure App Service `training-bot-api`.
   - `deploy-frontend` deploys to Azure App Service `training-bot-frontend`.

4. After deployment completes, run health checks (Section 2).

5. If the vector store is not persisted (first deploy or fresh environment), trigger ingest:
   ```bash
   curl -X POST https://<api-hostname>/ingest
   ```

6. Smoke-test by sending a message in Teacher mode via the frontend UI.

### Manual Deployment (emergency / hotfix)

1. Generate `requirements.txt`:
   ```bash
   uv export --no-dev --format requirements-txt -o requirements.txt
   ```

2. Deploy API manually via Azure CLI:
   ```bash
   az webapp deploy \
     --resource-group <resource-group> \
     --name training-bot-api \
     --src-path . \
     --type zip
   ```

3. Deploy frontend manually:
   ```bash
   az webapp deploy \
     --resource-group <resource-group> \
     --name training-bot-frontend \
     --src-path . \
     --type zip
   ```

### Rollback Steps

1. Identify the last known good commit SHA:
   ```bash
   git log --oneline -10
   ```

2. In Azure Portal → App Service → Deployment Center → Deployment history, select the previous successful deployment and click **Redeploy**.

   _OR_ via Azure CLI:
   ```bash
   az webapp deployment list --name training-bot-api --resource-group <resource-group>
   # Note the deploymentId of the last good deploy, then:
   az webapp deployment show --name training-bot-api \
     --resource-group <resource-group> \
     --deployment-id <deploymentId>
   ```

3. Alternatively, revert the commit and push to `main` to trigger CI/CD rollback:
   ```bash
   git revert HEAD
   git push origin main
   ```

4. Re-run health checks after rollback.

5. If the vector store was corrupted during the failed deploy, re-run ingest:
   ```bash
   curl -X POST https://<api-hostname>/ingest
   ```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Alert Threshold |
|---|---|---|
| App Service HTTP 5xx error rate | Azure Monitor → App Service metrics | > 1% over 5 min |
| App Service HTTP response time (P95) | Azure Monitor → App Service metrics | > 10 seconds |
| App Service CPU usage | Azure Monitor | > 80% sustained for 5 min |
| App Service Memory usage | Azure Monitor | > 85% of quota |
| LLM API error rate (4xx/5xx from OpenRouter) | Application logs | Any sustained errors |
| Vector store load success | Application startup log | `No vector store found` warning on startup |
| GitHub Actions workflow failure | GitHub Actions / email notification | Any failed run on `main` |

### Key Log Signals

| Log Message | Meaning |
|---|---|
| `Vector store loaded (N products)` | Healthy startup — RAG is operational |
| `No vector store found — run POST /ingest first.` | **Warning** — RAG is non-functional; ingest required |
| `[ingest] annotation failed for <file>: <error> — using raw chunker` | Annotation LLM unavailable; fallback mode active |
| `[ingest] index saved (N chunks)` | Ingest completed successfully |
| `SHOW_TOOL_CALLS=true` log lines | Tool invocation trace for debugging agent behaviour |
| Any `ERROR` or `CRITICAL` level log | Investigate immediately |

### Logging Configuration

- Log level is set to `INFO` via `logging.basicConfig(level=logging.INFO)` in `main.py`.
- [TODO: Is Azure Application Insights configured? Where are logs shipped — App Service log stream, Log Analytics, or a third-party tool?]
- [TODO: Are there any alerting rules set up in Azure Monitor or PagerDuty?]

### Recommended Alerts to Create

- HTTP 5xx rate > 1% → notify on-call
- Startup log contains `No vector store found` → notify on-call
- App Service restarts unexpectedly → notify on-call
- GitHub Actions `Test & Deploy` workflow failure on `main` → notify engineering team

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in name and contact] | Service down, health checks failing, LLM errors |
| L2 | Backend/API Lead | [TODO: fill in name and contact] | LLM integration issues, RAG pipeline failures, data corruption |
| L3 | Cloud/Infrastructure Lead | [TODO: fill in name and contact] | Azure App Service outage, networking, secrets/key vault issues |
| L4 | Product Owner | [TODO: fill in name and contact] | Prolonged outage > 2 hours, data loss, regulatory/compliance concern |
| Vendor | OpenRouter / Anthropic Support | https://openrouter.ai | LLM provider outage |
| Vendor | Microsoft Azure Support | https://portal.azure.com | Azure App Service infrastructure issues |

> **Note:** `kylo.deng@capco.com` is currently the configured notification email in all CI/CD workflows. [TODO: Confirm if this is the correct on-call contact or if a team distribution list should be used.]

---

## 7. Useful Commands

### Local Development

```bash
# Install dependencies
uv sync

# Run the FastAPI server locally (adjust port as needed)
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing
```

### Vector Store / Ingest

```bash
# Trigger ingest via API (after server is running)
curl -X POST http://localhost:8000/ingest

# Run ingest pipeline directly (from repo root)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Check known products in the vector store
curl http://localhost:8000/products
```

### Session Management

```bash
# List all sessions
curl http://localhost:8000/sessions

# Delete a session
curl -X DELETE http://localhost:8000/sessions/<session-id>

# Backup sessions file
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%d_%H%M%S)

# Inspect sessions file
python -m json.tool data/sessions.json | head -100
```

### Azure App Service

```bash
# View live logs for API
az webapp log tail \
  --name training-bot-api \
  --resource-group <resource-group>

# View live logs for frontend
az webapp log tail \
  --name training-bot-frontend \
  --resource-group <resource-group>

# Restart API App Service
az webapp restart \
  --name training-bot-api \
  --resource-group <resource-group>

# Restart frontend App Service
az webapp restart \
  --name training-bot-frontend \
  --resource-group <resource-group>

# List App Service application settings
az webapp config appsettings list \
  --name training-bot-api \
  --resource-group <resource-group>

# Set an environment variable on the API App Service
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <resource-group> \
  --settings API_KEY="<new-key>"

# List recent deployments
az webapp deployment list \
  --name training-bot-api \
  --resource-group <resource-group>
```

### CI/CD — GitHub Actions (manual triggers)

```bash
# Manually trigger tech docs generation
gh workflow run tool2_tech_docs.yml

# Manually trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Manually trigger UAT test pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0

# View recent workflow runs
gh run list --limit 10
```

### Dependency Management

```bash
# Export requirements.txt from uv lockfile (for Azure deploy)
uv export --no-dev --format requirements-txt -o requirements.txt

# Add a new dependency
uv add <package-name>

# Update all dependencies
uv lock --upgrade
```

### Debugging LLM/Agent Behaviour

```bash
# Enable tool call logging (set in .env or App Service settings)
echo "SHOW_TOOL_CALLS=true" >> .env

# Check which model is being used
curl http://localhost:8000/info  # [TODO: confirm if this endpoint exists]

# Check OpenRouter connectivity
curl -H "Authorization: Bearer $API_KEY" \
  https://openrouter.ai/api/v1/models | python -m json.tool | head -30
```

---

> **Document status:** Auto-assisted draft — [TODO: review all TODO markers with the engineering team before using in production.]  
> **Last reviewed:** [TODO: add review date and reviewer name]