# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that helps new insurance agents in Hong Kong master product knowledge and sales techniques. It provides two operational modes: a **Teacher mode** for ongoing interactive coaching (streamed via LangGraph agents), and an **Assessor mode** for one-shot evaluation of roleplay sessions. The backend uses a RAG (Retrieval-Augmented Generation) pipeline backed by a vector store (Chroma, FAISS, or Pinecone) populated from Sun Life insurance product PDFs. The LLM layer routes through OpenRouter (default) or directly to an Anthropic/OpenAI-compatible endpoint. The application is deployed as two separate Azure App Services — `training-bot-api` (FastAPI) and `training-bot-frontend` (Chainlit UI) — and is continuously deployed from the `main` branch via GitHub Actions.

---

## 2. Health Checks

### API Service (`training-bot-api`)

```bash
# Basic HTTP liveness — FastAPI root
curl -s https://training-bot-api.azurewebsites.net/

# Confirm vector store loaded (check API startup logs for this message)
# Expected log line on startup:
#   INFO:root:Vector store loaded (N products)
# Warning on failure:
#   WARNING:root:No vector store found — run POST /ingest first.
```

### Vector Store

```bash
# Trigger a re-ingest and confirm N chunks processed
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# List known products (should return non-empty list)
curl -s https://training-bot-api.azurewebsites.net/  # [TODO: confirm product-list endpoint path]
```

### Session Persistence

```bash
# Sessions file should exist and be non-empty after any conversation
ls -lh data/sessions.json

# List active sessions via API
curl -s https://training-bot-api.azurewebsites.net/sessions  # [TODO: confirm sessions list endpoint path]
```

### LLM Connectivity

```bash
# Confirm the configured LLM endpoint is reachable
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $API_KEY" \
  $OPENAI_URL_BASE/models
# Expected: 200
```

### Frontend (`training-bot-frontend`)

```bash
# Chainlit UI should be reachable
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
# Expected: 200
```

### GitHub Actions Pipeline

| Workflow | Expected trigger | Status URL |
|---|---|---|
| `Test & Deploy` | Push to `main` | GitHub Actions tab |
| `Tool 1 — Code Review` | PR open/sync | GitHub Actions tab |
| `Tool 2 — Tech Docs` | Push to `main` | GitHub Actions tab |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| API startup log: `No vector store found — run POST /ingest first.` | Vector store file missing from `data/` (e.g. first deploy, or store deleted) | Run `POST /ingest` via curl or the admin UI; confirm PDFs exist under `data/Insurance-product-info/`; check `[TODO: confirm vector store path — Chroma DB dir or FAISS index file]` |
| RAG tools return empty or irrelevant results | Vector store is stale or PDFs were not ingested | Re-run ingestion: `POST /ingest`; check annotation `.annot.json` sidecar files are present alongside each PDF; verify embedding API key (`API_KEY`) is valid |
| `WARNING: No vector store found` on every restart | Azure App Service ephemeral filesystem wiping `data/` | Mount a persistent Azure File Share to the `data/` directory, or store the vector index in Pinecone (`VECTOR_STORE=pinecone`); [TODO: confirm which vector store backend is used in production] |
| LLM returns 401 / 403 errors | `API_KEY` environment variable missing, expired, or rate-limited | Rotate the key in Azure App Service Application Settings and redeploy; check OpenRouter or Anthropic dashboard for quota |
| LLM returns 429 (Too Many Requests) | Rate limit exceeded on embedding or chat API | Increase `batch_delay` in `ingest.py`; reduce concurrent sessions; consider upgrading to a paid tier |
| SSL verification disabled warnings (`verify=False`) | `httpx` clients are created with TLS verification disabled | This is a known code-level risk. [TODO: determine if a corporate proxy requires this, or if it can be re-enabled safely] |
| `sessions.json` not found / session state lost | File not persisted between deployments; Azure ephemeral disk | Mount a persistent Azure File Share to the directory containing `sessions.json`; [TODO: confirm `data/sessions.json` path on App Service] |
| GitHub Actions deploy fails at `azure/webapps-deploy` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret missing or expired | Regenerate publish profile in Azure Portal → App Service → Download Publish Profile; update GitHub secret |
| `uv sync` fails in CI | `pyproject.toml`/`uv.lock` out of sync, or Python version mismatch | Run `uv lock` locally and commit the updated `uv.lock`; ensure CI uses Python 3.13 to match workflow |
| Chainlit frontend cannot reach FastAPI backend | CORS origin mismatch or wrong `OPENAI_URL_BASE` env var | Add the frontend's Azure URL to `allow_origins` in `main.py`; verify `OPENAI_URL_BASE` points to the correct API |
| `POST /ingest` hangs or times out | Large number of PDFs + LLM annotation calls are slow | Run ingestion as a background job or separate script; pre-generate `.annot.json` sidecar files locally before deploying; increase App Service timeout |
| Tool 1/2/3/4/5 GitHub Actions fail | Missing GitHub secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) | Add missing secrets under repo Settings → Secrets and Variables → Actions |
| Agent returns hallucinated product details | RAG tools not called (agent bypassing tools) | Check `TEACHER_SYSTEM` prompt is intact in `api/agent.py`; confirm tools are bound to the agent; review LangGraph agent invocation logs |
| `send_email` / SendGrid failures | `SENDGRID_API_KEY` invalid or sender not verified | Verify the sender domain in SendGrid; check API key permissions; review GitHub Actions logs for the email step |

---

## 4. Deployment Procedure

### Prerequisites

- Azure CLI authenticated (`az login`)
- GitHub secrets configured: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
- Environment variables set in Azure App Service Application Settings (see Section 5)

### Standard Deployment (CI/CD — Recommended)

```bash
# 1. Ensure all tests pass locally
uv run pytest tests/ -v

# 2. Commit and push to main branch
git add .
git commit -m "feat: <description>"
git push origin main

# 3. GitHub Actions automatically:
#    a. Runs tests (pytest via uv)
#    b. Generates requirements.txt via `uv export`
#    c. Deploys API to Azure App Service: training-bot-api
#    d. Deploys Frontend to Azure App Service: training-bot-frontend

# 4. Monitor the Actions run:
#    https://github.com/kylodeng/Insurance-Training-Bot-main/actions
```

### Manual Deployment (Break-Glass)

```bash
# 1. Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API manually using Azure CLI
az webapp deploy \
  --resource-group <resource-group-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 3. Deploy Frontend manually
az webapp deploy \
  --resource-group <resource-group-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

> [TODO: What is the Azure Resource Group name?]
> [TODO: Is there a separate Dockerfile or startup command configured on the App Services?]

### Post-Deployment Verification

```bash
# 1. Confirm API is live
curl -s https://training-bot-api.azurewebsites.net/

# 2. Trigger vector store ingest if first deploy or after PDF updates
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# 3. Check API startup logs in Azure
az webapp log tail --name training-bot-api --resource-group <rg-name>

# 4. Confirm frontend loads
curl -s -o /dev/null -w "%{http_code}" https://training-bot-frontend.azurewebsites.net/
```

### Rollback Steps

```bash
# Option A: Revert via Git and re-trigger CI
git revert HEAD
git push origin main
# CI will automatically redeploy the previous code

# Option B: Rollback using Azure App Service deployment slots
# [TODO: Are deployment slots configured for blue/green? If not, recommend setting them up]
az webapp deployment slot swap \
  --name training-bot-api \
  --resource-group <rg-name> \
  --slot staging \
  --target-slot production

# Option C: Redeploy a specific Git SHA
git checkout <previous-sha>
git push origin HEAD:main --force-with-lease
# WARNING: Use with care — notify team before force-pushing
```

---

## 5. Monitoring & Alerting

### Key Environment Variables to Verify in Azure App Service

| Variable | Required | Description |
|---|---|---|
| `API_KEY` | Yes | LLM provider API key (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | Yes | LLM base URL (`https://openrouter.ai/api/v1` default) |
| `OPENAI_MODEL` | Yes | Model name (`openai/gpt-oss-20b:free` default) |
| `SHOW_TOOL_CALLS` | No | Log tool call events (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | Claude API key for GitHub Actions tools |
| `GH_TOKEN` | Yes (CI only) | GitHub token for Actions scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | Email notifications from Actions scripts |

### Metrics to Watch (Azure Monitor / App Insights)

```
- HTTP 5xx error rate on training-bot-api         → alert if > 1% over 5 min
- HTTP response time P95                           → alert if > 30s (LLM streaming can be slow)
- App Service CPU %                                → alert if > 80% sustained 5 min
- App Service Memory %                             → alert if > 85%
- App Service HTTP 429 responses                   → LLM rate limiting
- Azure App Service availability                   → alert if < 99%
```

> [TODO: Are Application Insights / Azure Monitor alerts configured? If not, set them up.]

### Logs to Watch

```bash
# Stream live API logs
az webapp log tail --name training-bot-api --resource-group <rg-name>

# Download log archive
az webapp log download --name training-bot-api --resource-group <rg-name>

# Key log patterns to watch:
#   INFO: Vector store loaded (N products)     → healthy startup
#   WARNING: No vector store found             → ingest needed
#   WARNING: annotation failed for <file>      → PDF annotation error
#   ERROR: rate-limit pause                    → embedding throttle
```

### GitHub Actions Monitoring

```
- Watch for failed workflow runs at:
  https://github.com/kylodeng/Insurance-Training-Bot-main/actions
- Scheduled runs:
    Tool 1 (Code Review):  Mondays 08:00 UTC
    Tool 2 (Tech Docs):    Sundays 06:00 UTC
    Tool 4 (Auto Testing): Wednesdays 07:00 UTC
```

### Vector Store Health

```bash
# After ingest, verify chunk count in logs:
#   INFO: index saved (N chunks)
# Expected: N > 0 for each PDF processed

# Check annotation sidecar files exist:
find data/ -name "*.annot.json" | wc -l
```

---

## 6. Escalation Path

| Level | Who | When to Escalate | Contact |
|---|---|---|---|
| L1 — On-Call Engineer | [TODO: Team on-call rotation name] | Service down, 5xx rate spike, ingest failure | [TODO: PagerDuty/Slack channel] |
| L2 — Backend Lead | [TODO: Name] | LLM provider outage, vector store corruption, auth failures | [TODO: Email/Slack handle] |
| L3 — Solution Owner | [TODO: Name] | Data breach risk (TLS disabled), budget/quota exhaustion, Azure outage | [TODO: Email] |
| Azure Support | Microsoft | Azure App Service platform issues | [TODO: Azure Support plan tier and portal link] |
| LLM Provider Support | OpenRouter / Anthropic | API quota, billing, model deprecation | [TODO: Support ticket URLs] |
| Security Escalation | [TODO: Security team] | Hardcoded credentials found, TLS `verify=False` exploited | [TODO: Security contact] |

> ⚠️ **Known Security Risk**: All `httpx` clients in `api/main.py` and `core/ingest.py` are instantiated with `verify=False`, disabling TLS certificate verification. This should be treated as a security escalation item and reviewed immediately. [TODO: Determine if a corporate proxy is the cause and resolve properly.]

---

## 7. Useful Commands

### Local Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Start API server locally
uv run uvicorn api.main:app --reload --port 8000

# Ingest PDFs into vector store (local)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Generate requirements.txt (for manual deploys)
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Vector Store Management

```bash
# Re-ingest all PDFs via API endpoint
curl -X POST http://localhost:8000/ingest

# Check annotation sidecar files
find data/ -name "*.annot.json" -exec echo {} \;

# Delete stale annotation cache (forces re-annotation on next ingest)
find data/ -name "*.annot.json" -delete
```

### Session Management

```bash
# View sessions file
cat data/sessions.json | python -m json.tool

# Backup sessions before deployment
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%d)

# Clear all sessions (CAUTION: destroys all conversation history)
echo '{}' > data/sessions.json
```

### Azure App Service

```bash
# Stream live logs
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

# Show current environment variables
az webapp config appsettings list \
  --name training-bot-api \
  --resource-group <rg-name>

# Set an environment variable
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg-name> \
  --settings API_KEY="<new-key>"
```

### GitHub Actions — Manual Triggers

```bash
# Trigger Tool 2 (Tech Docs) manually via GitHub CLI
gh workflow run "Tool 2 — Tech Documentation" --repo kylodeng/Insurance-Training-Bot-main

# Trigger Tool 1 (Code Review) on a specific PR
gh workflow run "Tool 1 — Code Review" \
  