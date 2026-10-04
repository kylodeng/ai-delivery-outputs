# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides AI-powered insurance sales training for new agents operating in the Hong Kong market. It consists of two core modes: a **Teacher mode** — an ongoing, streamed conversational agent that teaches insurance concepts, product knowledge, and sales techniques using a RAG (Retrieval-Augmented Generation) pipeline backed by a vector store of Sun Life insurance product PDFs — and a **Roleplay/Assessor mode** — a one-shot session where a trainee practices sales conversations against a simulated customer persona, followed by an LLM-graded accuracy assessment. The backend is built with Python/FastAPI, uses LangChain + LangGraph agents, and is connected to an LLM via OpenRouter (defaulting to `openai/gpt-oss-20b:free`) or directly to Anthropic (Claude). A separate Chainlit frontend is served alongside the API. Both the API (`training-bot-api`) and frontend (`training-bot-frontend`) are deployed as Azure App Service instances via GitHub Actions on every push to `main`.

---

## 2. Health Checks

Perform the following checks in order to confirm the service is fully operational:

### 2.1 API Liveness

```bash
# Replace with your actual Azure App Service hostname
curl -f https://training-bot-api.azurewebsites.net/docs
# Expected: HTTP 200, FastAPI Swagger UI HTML
```

### 2.2 Vector Store Loaded

Check the application startup log for this line:

```
Vector store loaded (N products)
```

If instead you see:

```
No vector store found — run POST /ingest first.
```

The RAG pipeline is not operational. Trigger ingestion (see §7).

### 2.3 LLM Connectivity

```bash
# Send a minimal teacher-mode message and confirm a streamed response
curl -X POST https://training-bot-api.azurewebsites.net/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "healthcheck", "message": "Hello"}'
# Expected: HTTP 200, streaming text response
```

### 2.4 Frontend Liveness

```bash
curl -f https://training-bot-frontend.azurewebsites.net/
# Expected: HTTP 200, Chainlit UI HTML
```

### 2.5 Sessions Persistence

```bash
curl https://training-bot-api.azurewebsites.net/sessions
# Expected: HTTP 200, JSON array (may be empty on first boot)
```

### 2.6 Static PDF Serving

```bash
curl -I https://training-bot-api.azurewebsites.net/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
# Expected: HTTP 200 with Content-Type: application/pdf
```

### 2.7 GitHub Actions Pipelines

Navigate to: `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

Confirm the **Test & Deploy** workflow last run is green.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API returns `500` on all `/chat` requests | LLM API key missing or expired (`API_KEY` env var) | 1. Check Azure App Service → Configuration → `API_KEY`. 2. Rotate key at OpenRouter / Anthropic. 3. Redeploy or restart the App Service. |
| Startup log: `No vector store found — run POST /ingest first` | Vector store index was never built or was deleted | 1. SSH into the App Service or run locally: `POST /ingest`. 2. Confirm PDFs exist under `data/Insurance-product-info/`. 3. Check embedding API key (`API_KEY`) is valid. |
| RAG tools return no results / empty search results | Vector store is stale or embeddings are corrupted | 1. Delete existing index files. 2. Re-run ingestion pipeline (`POST /ingest`). 3. Verify PDF `.annot.json` sidecar files are present in `data/`. |
| `SSL: CERTIFICATE_VERIFY_FAILED` or TLS errors in logs | `verify=False` is set in `httpx.Client` — this suppresses SSL errors. However, upstream proxy/firewall may be blocking the LLM endpoint. | 1. Confirm `OPENAI_URL_BASE` points to the correct endpoint. 2. Check Azure outbound networking / firewall rules allow the LLM host. |
| Sessions not persisting after restart | `data/sessions.json` is on ephemeral App Service local disk | 1. Mount an Azure File Share to `data/` in App Service. 2. Alternatively, migrate sessions to Azure Blob Storage or Azure Cosmos DB. [TODO: Is persistent session storage required in production?] |
| Frontend shows blank page / cannot connect to API | CORS misconfiguration or App Service URL mismatch | 1. Check `allow_origins` list in `api/main.py` includes the frontend URL. 2. Verify both App Services are running. 3. Check frontend environment variable pointing to API base URL. [TODO: What env var does the frontend use for the API URL?] |
| GitHub Actions deploy job fails: `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret missing | Secret not configured in repo settings | 1. Download publish profile from Azure Portal → App Service → Overview → Get publish profile. 2. Add as GitHub secret `AZURE_WEBAPP_PUBLISH_PROFILE_API` / `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`. |
| LLM returns malformed JSON (annotation/assessment failures) | Model response does not match expected schema | 1. Check logs for `[DEBUG] JSON parse error`. 2. Switch to a more capable model via `OPENAI_MODEL` env var. 3. The `extract_json` function in `tool1_code_review.py` has fallback parsing — confirm it is being invoked. |
| `POST /ingest` is very slow or times out | Large PDF set + slow embedding API rate limits | 1. Reduce `batch_size` in `embed_chunks()` call. 2. Increase `batch_delay`. 3. Run ingestion offline and upload the resulting index files. [TODO: What is the embedding provider in production — Voyage AI, OpenAI, or other?] |
| Chainlit tool calls not showing in UI | `SHOW_TOOL_CALLS` env var not set to `true` | 1. Set `SHOW_TOOL_CALLS=true` in App Service Configuration. 2. Restart the service. |
| Age / premium calculations incorrect in agent responses | Agent not calling `get_current_date` tool first | 1. Review TEACHER_SYSTEM prompt — it instructs the agent to call `get_current_date` first. 2. If using a different model, test whether it follows tool-call ordering instructions. |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- GitHub Actions secrets configured:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
  - `ANTHROPIC_API_KEY` (for AI automation tools)
  - `GH_TOKEN`
  - `SENDGRID_API_KEY`
- Azure App Services created:
  - `training-bot-api`
  - `training-bot-frontend`
- [TODO: What Azure region and resource group are these App Services in?]
- [TODO: What Python runtime version is configured on the App Services?]

### 4.1 Standard Deployment (Automated via GitHub Actions)

1. **Ensure all tests pass locally:**

   ```bash
   uv sync
   uv run pytest tests/ -v
   ```

2. **Merge your branch to `main`** (via Pull Request — this also triggers the AI code review workflow automatically).

3. **GitHub Actions triggers automatically:**
   - **`test` job** runs `pytest` on Python 3.13.
   - **`deploy-api` job** (on `test` pass):
     - Runs `uv export` to generate `requirements.txt`.
     - Deploys to `training-bot-api` Azure App Service.
   - **`deploy-frontend` job** (on `test` pass):
     - Deploys to `training-bot-frontend` Azure App Service.

4. **Monitor the Actions run:**
   `https://github.com/kylodeng/Insurance-Training-Bot-main/actions`

5. **Perform health checks** (see §2) after deployment completes (~5–10 min).

6. **Verify the vector store** is still loaded after deployment (App Service restarts on deploy):

   ```bash
   curl -X POST https://training-bot-api.azurewebsites.net/ingest
   ```

   [TODO: Confirm the exact `/ingest` endpoint signature and whether it is idempotent]

### 4.2 Manual Deployment (Emergency)

```bash
# Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API manually
az webapp up \
  --name training-bot-api \
  --resource-group <RESOURCE_GROUP> \
  --runtime "PYTHON:3.13"

# Deploy Frontend manually
az webapp up \
  --name training-bot-frontend \
  --resource-group <RESOURCE_GROUP> \
  --runtime "PYTHON:3.13"
```

### 4.3 Rollback Procedure

**Option A — GitHub Actions (preferred):**

1. Identify the last known-good commit SHA:

   ```bash
   git log --oneline -10
   ```

2. Revert on `main`:

   ```bash
   git revert <bad-commit-sha>
   git push origin main
   ```

   This triggers the deploy workflow automatically.

**Option B — Azure Portal:**

1. Navigate to **Azure Portal → App Service (`training-bot-api`) → Deployment Center → Deployment logs**.
2. Identify the previous successful deployment.
3. Click **Redeploy** on that deployment slot.
4. Repeat for `training-bot-frontend`.

**Option C — Azure CLI:**

```bash
# List deployment history
az webapp deployment list --name training-bot-api --resource-group <RESOURCE_GROUP>

# Redeploy a specific deployment ID
az webapp deployment source sync --name training-bot-api --resource-group <RESOURCE_GROUP>
```

**Post-rollback:** Re-run health checks (§2). If the vector store was modified, re-run ingestion.

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

The application uses Python's standard `logging` module at `INFO` level.

**Azure App Service Log Stream:**

```bash
az webapp log tail --name training-bot-api --resource-group <RESOURCE_GROUP>
```

**Key log patterns to watch:**

| Log Pattern | Meaning | Action |
|---|---|---|
| `Vector store loaded (N products)` | Normal startup | None |
| `No vector store found — run POST /ingest first` | RAG pipeline down | Trigger ingestion |
| `[ingest] processing: <file>` | Ingestion in progress | None (informational) |
| `[ingest] annotation failed for <file>` | LLM annotation error | Check API key, inspect file |
| `[DEBUG] JSON parse error:` | LLM returned malformed JSON | Check model / prompt |
| `WARNING — No vector store found` | Post-restart store missing | Re-ingest |
| HTTP `500` errors | Unhandled exception | Check full traceback in logs |

### 5.2 Metrics to Monitor (Azure Monitor / Application Insights)

[TODO: Is Azure Application Insights configured for this App Service?]

| Metric | Warning Threshold | Critical Threshold | Notes |
|---|---|---|---|
| HTTP 5xx error rate | > 1% | > 5% | LLM or vector store failures |
| HTTP response time (p95) | > 10s | > 30s | Streaming responses are expected to be slow; set generous thresholds |
| App Service CPU % | > 70% | > 90% | Embedding/inference is CPU-intensive |
| App Service Memory % | > 80% | > 95% | FAISS index held in memory |
| `sessions.json` file size | > 50 MB | > 100 MB | Unbounded session growth |
| GitHub Actions workflow failure | Any | — | Deployment or test regression |

### 5.3 GitHub Actions Workflow Monitoring

Monitor these automated workflows for failures:

| Workflow | Trigger | What to Alert On |
|---|---|---|
| Test & Deploy | Push to `main` | Any job failure → deployment blocked |
| Tool 1 — Code Review | PR open / Monday 08:00 UTC | Failure silently skips review |
| Tool 2 — Tech Docs | Push to `main` / Sunday 06:00 UTC | Stale documentation |
| Tool 4 — Auto Testing | PR open / Wednesday 07:00 UTC | Missing test coverage |

### 5.4 LLM API Usage

[TODO: Is there a budget alert set on OpenRouter or Anthropic for this project?]

Monitor:
- **OpenRouter / Anthropic dashboard** for token usage and rate limit errors.
- The model `openai/gpt-oss-20b:free` is a free tier model — **it may be rate-limited or deprecated** without notice. Have a fallback model configured via `OPENAI_MODEL`.

### 5.5 PDF Knowledge Base Integrity

```bash
# Count annotation sidecar files — should match number of PDFs
find data/Insurance-product-info -name "*.annot.json" | wc -l
find data/Insurance-product-info -name "*.pdf" | wc -l
```

If counts differ, some PDFs have not been annotated — re-run ingestion.

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call contact / PagerDuty rotation] | Service down, health checks failing |
| L2 | Platform / DevOps Lead | [TODO: fill in name and contact] | Azure App Service issues, deployment failures, persistent LLM errors |
| L3 | Application Owner | [TODO: fill in name — likely kylo.deng@capco.com] | Data integrity issues, security incidents, budget overruns |
| External | Azure Support | Via Azure Portal — Support + troubleshoot | Azure infrastructure failures |
| External | OpenRouter Support | https://openrouter.ai | LLM API outages |
| External | Anthropic Support | https://support.anthropic.com | Claude API outages (if used directly) |

**Notification email configured:** `kylo.deng@capco.com` (via SendGrid in AI automation tools)

---

## 7. Useful Commands

### Service Management

```bash
# Restart API App Service
az webapp restart --name training-bot-api --resource-group <RESOURCE_GROUP>

# Restart Frontend App Service
az webapp restart --name training-bot-frontend --resource-group <RESOURCE_GROUP>

# Stream live logs — API
az webapp log tail --name training-bot-api --resource-group <RESOURCE_GROUP>

# Stream live logs — Frontend
az webapp log tail --name training-bot-frontend --resource-group <RESOURCE_GROUP>
```

### Local Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Start the FastAPI server locally
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Copy and configure environment variables
cp .env.example .env   # [TODO: Does a .env.example exist?]
# Edit .env with: API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, SHOW_TOOL_CALLS
```

### Vector Store / Ingestion

```bash
# Run ingestion pipeline locally (from repo root)
uv run python core/ingest.py --verbose --pdf-dir data/Insurance-product-info

# Trigger ingestion via API endpoint
curl -X POST http://localhost:8000/ingest

# Check how many products are in the vector store
curl http://localhost:8000/products   # [TODO: Confirm this endpoint exists]

# Count PDF files and annotation sidecars
find data/Insurance-product-info -name "*.pdf" |