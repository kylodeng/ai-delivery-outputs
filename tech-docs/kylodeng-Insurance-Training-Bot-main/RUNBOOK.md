# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training environment for insurance sales agents. It operates in two modes: **Teacher mode**, which delivers an ongoing interactive coaching experience via a streaming chat interface, and **Roleplay/Assessment mode**, which simulates a customer conversation and evaluates agent performance. The backend uses a LangGraph agent backed by a RAG (Retrieval-Augmented Generation) pipeline: Sun Life insurance product PDFs are ingested, annotated using an LLM, chunked, embedded, and stored in a local FAISS or Chroma vector store. The agent answers product-specific queries by retrieving relevant chunks from this store and citing sources inline. The application is deployed to **Azure App Service** (API: `training-bot-api`, Frontend: `training-bot-frontend`) via GitHub Actions on every push to `main`. Session state is persisted to `data/sessions.json` on disk.

---

## 2. Health Checks

### API Service

| Check | How to verify | Expected result |
|---|---|---|
| API process is up | `curl -s https://training-bot-api.azurewebsites.net/` | HTTP 200 or configured welcome response |
| Lifespan startup completed | Check startup logs for `Vector store loaded` or `No vector store found` | No exception in startup logs |
| Vector store loaded | `curl -s https://training-bot-api.azurewebsites.net/ingest` (or check startup log) | Log line: `Vector store loaded (N products)` |
| Sessions file accessible | Check that `data/sessions.json` exists and is readable at startup | `load_sessions()` completes without error |
| Static files served | `curl -I https://training-bot-api.azurewebsites.net/docs/<any-pdf-filename>` | HTTP 200 |

### Frontend Service

| Check | How to verify | Expected result |
|---|---|---|
| Frontend is reachable | Navigate to `https://training-bot-frontend.azurewebsites.net/` | UI renders without error |
| API connection from frontend | Open browser devtools, initiate a chat, observe network calls | WebSocket or SSE stream connects to API successfully |

### LLM / External API

| Check | How to verify | Expected result |
|---|---|---|
| LLM backend reachable | Trigger a teacher-mode message; check API logs | No `httpx.ConnectError` or 401/403 from `OPENAI_URL_BASE` |
| Vector store has data | Check log at startup | `Vector store loaded (N products)` where N > 0 |

> [TODO: Is there a `/health` or `/readyz` endpoint defined in `api/main.py` beyond what is shown?]

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `No vector store found — run POST /ingest first` at startup | Vector store index files are missing from the deployment artifact or data directory | 1. Confirm `data/` directory is included in the deployment. 2. Call `POST /ingest` to rebuild the index. 3. Verify that PDF files exist under `data/Insurance-product-info/`. 4. Check ingest logs for annotation or embedding errors. |
| LLM returns no response / 500 error on chat | `API_KEY` env var is missing, expired, or `OPENAI_URL_BASE` is misconfigured | 1. Check Azure App Service environment variables for `API_KEY` and `OPENAI_URL_BASE`. 2. Test the key manually: `curl -H "Authorization: Bearer $API_KEY" $OPENAI_URL_BASE/models`. 3. Rotate the key if expired and redeploy. |
| SSL verification errors (`httpx` `verify=False` warnings in logs) | `httpx.Client(verify=False)` is set intentionally but may indicate a proxy or cert issue | 1. Check if a corporate proxy is intercepting TLS. 2. If a proper cert is available, remove `verify=False` and set `SSL_CERT_FILE`. [TODO: Is `verify=False` intentional for production or a dev shortcut?] |
| `sessions.json` write failure / sessions not persisting across restarts | Azure App Service ephemeral filesystem or missing write permissions to `data/` | 1. Check App Service logs for `PermissionError` or `FileNotFoundError`. 2. Confirm `data/` is mounted on persistent storage (Azure Files share). 3. [TODO: Is persistent storage configured for the App Service?] |
| Agent returns stale or incorrect product information | Vector store index is out of date after new PDFs are added | 1. Add new PDFs to `data/Insurance-product-info/`. 2. Call `POST /ingest` endpoint. 3. Restart the API service to reload the store on lifespan startup. |
| `KeyError` on missing environment variable at startup | Required env var (`API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`) not set in App Service config | 1. Navigate to Azure Portal → App Service → Configuration → Application Settings. 2. Add the missing variable. 3. Restart the service. |
| GitHub Actions `Test & Deploy` fails on `deploy-api` or `deploy-frontend` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret is missing or expired | 1. Go to Azure Portal → App Service → Get publish profile. 2. Update the GitHub secret under Settings → Secrets → Actions. 3. Re-run the failed workflow. |
| Frontend CORS error | API CORS `allow_origins` list does not include the production frontend origin | 1. Identify the production frontend URL. 2. Add it to the `allow_origins` list in `api/main.py`. 3. Deploy via `push` to `main`. [TODO: What is the production frontend URL?] |
| Roleplay assessment returns no score / malformed JSON | LLM response cannot be parsed as JSON (model returned markdown fences or partial output) | 1. Check API logs for JSON parse errors. 2. Increase `max_tokens` if response is being truncated. 3. Review the raw LLM response in debug logs. |
| PDF annotation fails during ingest | LLM call times out or returns unexpected format during `annotate_document` | 1. Check ingest logs for `annotation failed for <file>`. 2. The pipeline falls back to raw chunking — verify chunk quality. 3. Re-run `POST /ingest`. 4. If persistent, check LLM quota/rate limits. |
| Azure App Service cold-start delays | App Service is on a low SKU with scale-to-zero enabled | 1. [TODO: What App Service SKU is in use?] 2. Enable Always On setting in App Service configuration if on Basic tier or above. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure App Service instances `training-bot-api` and `training-bot-frontend` are provisioned
- GitHub repository secrets are set:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
- All required application env vars are set in each App Service's Application Settings (see §5)

### Normal Deployment (Push to `main`)

```
1. Create a feature branch and open a Pull Request against main.
   → GitHub Actions: "Test & Deploy" → `test` job runs `pytest tests/ -v`

2. Review test results in the Actions tab. All tests must pass before merging.

3. Merge the PR to main.
   → GitHub Actions: "Test & Deploy" automatically triggers:
     a. `test` job — runs pytest
     b. `deploy-api` job (after test passes):
        - Runs `uv export` to generate requirements.txt
        - Deploys to Azure App Service: training-bot-api
     c. `deploy-frontend` job (after test passes):
        - Runs `uv export` to generate requirements.txt
        - Deploys to Azure App Service: training-bot-frontend

4. Monitor deployment in GitHub Actions tab.
   URL: https://github.com/kylodeng/Insurance-Training-Bot-main/actions

5. After deployment completes, verify health checks (§2).

6. If new PDFs were added to data/:
   POST /ingest    ← trigger re-ingestion via API or run core/ingest.py manually
```

### Manual Deployment

```bash
# Export dependencies
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API manually using Azure CLI
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend manually
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

> [TODO: What resource group are the App Services in?]  
> [TODO: Is the frontend a separate static site or the same FastAPI app serving Chainlit?]

### Rollback Steps

```
Option A — Redeploy previous commit via GitHub Actions:
  1. Go to GitHub Actions → "Test & Deploy" → find the last successful run.
  2. Click "Re-run jobs" on that run.

Option B — Azure portal swap / rollback:
  1. Azure Portal → App Service (training-bot-api) → Deployment Center
  2. Identify the previous deployment slot or package.
  3. Redeploy the previous package.

Option C — Git revert:
  1. git revert HEAD
  2. git push origin main
  → This triggers a new deployment of the reverted code.

After rollback:
  - Verify health checks (§2)
  - If the vector store schema changed, re-run POST /ingest
  - Check sessions.json integrity — a schema change may require clearing old sessions
```

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Where to find it | Alert threshold |
|---|---|---|
| HTTP 5xx error rate | Azure Monitor → App Service → HTTP 5xx | > 1% over 5 min |
| HTTP response time (p95) | Azure Monitor → App Service → Average Response Time | > 10s (streaming endpoints excluded) |
| App Service CPU % | Azure Monitor → App Service → CPU Percentage | > 80% sustained 5 min |
| App Service Memory % | Azure Monitor → App Service → Memory Percentage | > 85% |
| GitHub Actions failure | GitHub Actions tab / email notification | Any failure on `main` branch |
| LLM API error rate | Application logs — search for `httpx` errors or HTTP 4xx/5xx from LLM backend | Any 401/403 from LLM provider |
| Vector store load failure | Application logs at startup — `No vector store found` | Every occurrence |

### Logs to Watch

| Log source | What to look for |
|---|---|
| Azure App Service logs (Log Stream or Log Analytics) | `No vector store found`, `annotation failed`, `KeyError`, `JSONDecodeError`, `httpx.ConnectError`, `PermissionError on sessions.json` |
| GitHub Actions logs | Failed `test` or `deploy-*` steps, uv sync errors, publish profile errors |
| Application stdout | `Vector store loaded (N products)` on startup; tool call traces from LangGraph if `SHOW_TOOL_CALLS=true` |

### Recommended Azure Monitor Alerts

```
1. HTTP Server Errors (5xx) > 5 per minute → notify on-call
2. Average Response Time > 30s → investigate LLM latency or cold start
3. App Service restart detected → investigate crash or OOM
4. Deployment failure in GitHub Actions → notify dev team
```

> [TODO: Are Azure Monitor alerts and Log Analytics workspace configured? What is the alerting email/channel?]  
> [TODO: Is Application Insights enabled on either App Service?]  
> [TODO: What is `SHOW_TOOL_CALLS` set to in production?]

### Environment Variables Required in App Service

| Variable | Required | Description |
|---|---|---|
| `API_KEY` | Yes | API key for the LLM provider |
| `OPENAI_URL_BASE` | Yes | LLM base URL (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Model name (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Set to `true` to log/stream tool call events (default: `true`) |

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: name / PagerDuty / Teams channel] | Service unavailable, 5xx spike, deployment failure |
| L2 | Backend developer | [TODO: name / email] | Persistent LLM errors, vector store corruption, session data loss |
| L3 | Tech lead | [TODO: name / email] | Architecture change needed, data breach concern, LLM provider outage |
| External | Azure Support | [TODO: Azure support plan tier / ticket portal] | App Service platform issues, storage failures |
| External | LLM Provider | [TODO: OpenRouter / Anthropic support URL] | API quota exhaustion, model deprecation, authentication failure |
| Business | Product owner | [TODO: name] | Go/no-go for rollback affecting users, data incident |

---

## 7. Useful Commands

### Check service status (Azure CLI)

```bash
# Check API App Service state
az webapp show \
  --resource-group <rg-name> \
  --name training-bot-api \
  --query "state" -o tsv

# Check Frontend App Service state
az webapp show \
  --resource-group <rg-name> \
  --name training-bot-frontend \
  --query "state" -o tsv
```

### Restart App Service

```bash
az webapp restart \
  --resource-group <rg-name> \
  --name training-bot-api

az webapp restart \
  --resource-group <rg-name> \
  --name training-bot-frontend
```

### Tail live logs

```bash
az webapp log tail \
  --resource-group <rg-name> \
  --name training-bot-api
```

### Trigger PDF ingestion (rebuild vector store)

```bash
# Via API endpoint
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Or locally with uv
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### Run tests locally

```bash
uv sync
uv run pytest tests/ -v
```

### Export dependencies

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Check environment variables on App Service

```bash
az webapp config appsettings list \
  --resource-group <rg-name> \
  --name training-bot-api \
  --output table
```

### Set a missing environment variable

```bash
az webapp config appsettings set \
  --resource-group <rg-name> \
  --name training-bot-api \
  --settings API_KEY="<your-key>"
```

### Inspect sessions file

```bash
# SSH into App Service (if configured) or use Kudu console
# Azure Portal → App Service → Advanced Tools (Kudu) → Debug Console
cat /home/site/wwwroot/data/sessions.json | python3 -m json.tool | head -100
```

### Manually re-run GitHub Actions deployment

```bash
gh workflow run "Test & Deploy" --repo kylodeng/Insurance-Training-Bot-main --ref main
```

### Check GitHub Actions run status

```bash
gh run list --repo kylodeng/Insurance-Training-Bot-main --limit 10
gh run view <run-id> --repo kylodeng/Insurance-Training-Bot-main --log
```

---

> **Outstanding TODOs for this runbook:**
> - [TODO: Confirm the production frontend URL for CORS configuration]
> - [TODO: What Azure resource group are the App Services deployed to?]
> - [TODO: Is `data/` on a persistent Azure Files mount or ephemeral App Service storage?]
> - [TODO: What App Service SKU/pricing tier is in use?]
> - [TODO: Is there a `/health` endpoint defined in the API?]
> - [TODO: Is Application Insights enabled? If so, provide the instrumentation key and workspace ID]
> - [TODO: Is `