# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

---

## 1. Service Overview

The Insurance Training Bot is a FastAPI-based web application that provides an AI-powered training platform for insurance sales agents, targeted at the Hong Kong market. It operates in two modes: **Teacher mode**, which conducts interactive, multi-turn coaching sessions to help agents learn insurance products, sales techniques, and discovery questioning; and **Roleplay mode**, in which the AI simulates a realistic Hong Kong customer profile so agents can practise live sales conversations. A **post-roleplay Assessor** then scores the agent's performance across multiple dimensions. The backend retrieves product knowledge (benefit tables, exclusions, hospital networks, claim procedures) from a vector store populated by ingesting Sun Life Hong Kong PDF brochures and supplementary documents. The system is deployed on **Azure App Service** (two slots: `training-bot-api` and `training-bot-frontend`), with CI/CD driven by GitHub Actions. A suite of five AI-delivery workflow tools (code review, tech docs, business docs, auto-testing, UAT facilitation) runs in parallel via Claude AI and outputs artefacts to a companion `ai-delivery-outputs` repository.

---

## 2. Health Checks

### API (`training-bot-api`)

| Check | Command / URL | Expected Result |
|---|---|---|
| HTTP liveness | `GET https://training-bot-api.azurewebsites.net/` | HTTP 200 or 404 (FastAPI root not defined; absence of 5xx = alive) |
| FastAPI docs endpoint | `GET https://training-bot-api.azurewebsites.net/docs` | Swagger UI renders (HTTP 200) |
| Ingest status | `GET https://training-bot-api.azurewebsites.net/ingest` (or check startup logs) | Log: `Vector store loaded (N products)` — N > 0 |
| Sessions file | Check `data/sessions.json` exists and is valid JSON | File present, parseable |
| LLM connectivity | POST a chat message through the UI | Response streams without 5xx |
| Vector store | Log line at startup | `Vector store loaded (N products)` |

### Frontend (`training-bot-frontend`)

| Check | Command / URL | Expected Result |
|---|---|---|
| HTTP liveness | `GET https://training-bot-frontend.azurewebsites.net/` | HTTP 200, UI renders |
| CORS connectivity | Open browser DevTools → Network tab → send chat | No CORS errors, SSE stream established |

### GitHub Actions Workflows

| Check | Location | Expected Result |
|---|---|---|
| `Test & Deploy` workflow | GitHub Actions tab | Green tick on `main` branch |
| All 5 AI-delivery tools | GitHub Actions tab | No persistent failures on scheduled runs |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `Vector store loaded (0 products)` at startup | `data/` vector store index missing or empty; ingest never run or failed | 1. SSH / Kudu into `training-bot-api`. 2. Run `POST /ingest` endpoint or execute `python core/ingest.py --pdf-dir data/Insurance-product-info`. 3. Verify `.annot.json` sidecar files exist alongside PDFs. 4. Restart the app service. |
| `No vector store found — run POST /ingest first` (log warning) | First deployment; no persisted FAISS/Chroma index | Run ingest pipeline: `python core/ingest.py` locally or via Azure Kudu console. Commit resulting index files if stored in-repo, or ensure they land in the persistent storage mount. |
| LLM returns 401 / `AuthenticationError` | `API_KEY` environment variable missing or expired | 1. Go to Azure App Service → Configuration → Application Settings. 2. Verify `API_KEY` is set and matches the OpenRouter/Anthropic key. 3. Rotate key if expired. 4. Restart the app. |
| Streaming response hangs / times out | OpenRouter or upstream LLM endpoint unreachable; SSL verification disabled (`verify=False`) causing silent failure | 1. Check `OPENAI_URL_BASE` env var points to correct endpoint. 2. Test connectivity: `curl -k https://openrouter.ai/api/v1/models -H "Authorization: Bearer $API_KEY"`. 3. Check Azure outbound IP is not blocked. 4. Review app logs for `httpx` timeout errors. |
| CORS errors in browser | `training-bot-frontend` origin not in CORS allow-list | 1. Check `api/main.py` `allow_origins` list. 2. Add the production frontend URL. 3. Redeploy. |
| `sessions.json` corruption / parse error | Concurrent writes or incomplete shutdown | 1. Stop the app service. 2. Back up corrupted file. 3. Replace with `{}` or last known good backup. 4. Restart. |
| GitHub Actions deploy fails — `AZURE_WEBAPP_PUBLISH_PROFILE_*` secret missing | Secret not set in repository | 1. Download publish profile from Azure Portal → App Service → Get Publish Profile. 2. Add as `AZURE_WEBAPP_PUBLISH_PROFILE_API` / `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` in GitHub repo Settings → Secrets. |
| `pytest` failures block deployment | New code breaks existing tests | 1. Review failing test output in Actions log. 2. Fix tests or code on a branch. 3. Re-push to trigger pipeline. Do **not** skip tests to unblock. |
| AI-delivery tools (Tool 1–5) fail with 401 | `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` secrets missing | Check GitHub repo secrets for all three. Rotate if expired. |
| PDF annotation fails during ingest | LLM annotation LLM unreachable or PDF is corrupt/unreadable | 1. Check `[ingest] annotation failed for <file>` in logs. 2. Verify `.annot.json` sidecar files. 3. Re-run ingest with `--verbose`. 4. If a specific PDF is corrupt, remove and replace it. |
| `uv sync` fails in CI | `pyproject.toml` / `uv.lock` inconsistency | Run `uv lock` locally, commit updated `uv.lock`, push. |
| Frontend Chainlit UI shows blank / 404 | Static files not mounted or `data/` directory missing | Verify `StaticFiles` mount path resolves correctly on Azure; check that `data/` is included in the deployment package. |
| Tool calls not appearing in UI | `SHOW_TOOL_CALLS` env var set to `false` | Set `SHOW_TOOL_CALLS=true` in `.env` or Azure App Settings, or toggle per-session in the UI. |

---

## 4. Deployment Procedure

### Prerequisites
- Azure CLI authenticated (`az login`)
- GitHub Actions secrets configured (see §3 above)
- `uv` installed locally for dependency management

### Normal Deployment (via CI/CD — preferred)

1. **Create a feature branch** and make changes.
2. **Open a Pull Request** against `main`.
   - This triggers `Tool 1 — Code Review` (automated Claude review posted as PR comment).
   - This triggers `Tool 4 — Auto Testing` if source files changed.
3. **Address review findings** and push fixes.
4. **Merge PR to `main`**.
   - GitHub Actions `Test & Deploy` workflow fires automatically.
   - Job `test`: runs `uv run pytest tests/ -v` on Python 3.13.
   - Job `deploy-api` (on test pass): generates `requirements.txt` via `uv export`, deploys to `training-bot-api` Azure App Service.
   - Job `deploy-frontend`: deploys to `training-bot-frontend` Azure App Service.
5. **Verify deployment** using health checks in §2.
6. **Check application logs** in Azure Portal → App Service → Log Stream.

### Manual / Hotfix Deployment

```bash
# 1. Generate requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API manually via Azure CLI
az webapp deploy --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . --type zip

# 3. Deploy frontend manually
az webapp deploy --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . --type zip
```

[TODO: What is the Azure Resource Group name?]

### First-time Ingest (must be done after initial deploy)

```bash
# Run via Kudu console or SSH into the App Service
python core/ingest.py --pdf-dir data/Insurance-product-info --verbose
```

Or call the API endpoint:
```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

[TODO: Does the `/ingest` endpoint require authentication?]

### Rollback Steps

1. **Via GitHub Actions**: Navigate to the last successful `Test & Deploy` run → click **Re-run jobs** on the previous successful run.
2. **Via Azure Portal**: App Service → Deployment Center → Deployment History → select prior successful deployment → **Redeploy**.
3. **Via Azure CLI**:
```bash
# List deployments
az webapp deployment list --name training-bot-api --resource-group <rg-name>

# Redeploy a specific deployment ID
az webapp deployment source sync --name training-bot-api --resource-group <rg-name>
```
4. **Verify rollback** by re-running health checks from §2.
5. **If `sessions.json` was corrupted during rollback**: restore from backup (see §3).

[TODO: Is there a deployment slot (staging → production swap) configured, or is it direct-to-production?]

---

## 5. Monitoring & Alerting

### Key Metrics to Watch

| Metric | Source | Threshold / Alert |
|---|---|---|
| HTTP 5xx error rate | Azure App Service → Metrics → Http 5xx | Alert if > 1% over 5 min |
| Response latency | Azure App Service → Metrics → Average Response Time | Alert if > 30 s (streaming is expected to be slow; tune threshold) |
| CPU usage | Azure App Service → Metrics → CPU Percentage | Alert if > 80% sustained 5 min |
| Memory usage | Azure App Service → Metrics → Memory Working Set | Alert if approaching App Service plan limit |
| App Service availability | Azure Monitor uptime check | Alert on any downtime |
| GitHub Actions workflow failures | GitHub → Actions → notifications | Alert on any failed run on `main` |

### Key Log Lines to Watch

```
# Healthy startup
INFO:     Vector store loaded (N products)
INFO:     Application startup complete.

# Warning — action required
WARNING:  No vector store found — run POST /ingest first.
WARNING:  [ingest] annotation failed for <file>

# Errors
ERROR:    Unhandled exception ...
ERROR:    AuthenticationError ...
```

### Log Access

```bash
# Stream live logs from Azure
az webapp log tail --name training-bot-api --resource-group <rg-name>

# Download log files
az webapp log download --name training-bot-api --resource-group <rg-name> --log-file app_logs.zip
```

### What to Watch in GitHub Actions

- `Test & Deploy` → `test` job: pytest results
- `Tool 1 — Code Review`: runs on every PR and weekly Monday 08:00 UTC
- `Tool 2 — Tech Docs`: runs on every merge to `main` and weekly Sunday 06:00 UTC
- `Tool 4 — Auto Testing`: runs on every PR touching source files and weekly Wednesday 07:00 UTC

[TODO: Are Azure Monitor alerts configured? If not, set up an Action Group with email/Teams notifications.]

[TODO: Is Application Insights instrumentation enabled on the App Services?]

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call developer | [TODO: name & contact] | Service down, 5xx spike, failed deployment |
| L2 | Tech Lead | [TODO: name & contact] | L1 unable to resolve within 30 min; data loss; security incident |
| L3 | Solution Owner / Business Sponsor | [TODO: name & contact] | Extended outage > 1 hour; data breach; regulatory concern |
| Platform | Azure Support | [TODO: Azure support plan tier & portal link] | App Service platform issues, suspected Azure-side incident |
| LLM Provider | OpenRouter / Anthropic support | [TODO: support portal URL] | LLM API outages, quota exhaustion, billing issues |
| Notifications | Default notify email | kylo.deng@capco.com | Automated AI-tool outputs, audit log delivery |

---

## 7. Useful Commands

### Local Development

```bash
# Install dependencies
uv sync

# Run FastAPI backend locally
uv run uvicorn api.main:app --reload --port 8000

# Run with environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.
uv run uvicorn api.main:app --reload --port 8000
```

### Testing

```bash
# Run full test suite
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Run a single test file
uv run pytest tests/test_<module>.py -v
```

### Ingest Pipeline

```bash
# Ingest all PDFs in the data directory
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Ingest with custom chunk size
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --max-words 280 --verbose
```

### Dependency Management

```bash
# Add a new dependency
uv add <package-name>

# Export requirements for Azure deploy
uv export --no-dev --format requirements-txt -o requirements.txt

# Update all dependencies
uv lock --upgrade
```

### Azure Operations

```bash
# Login to Azure
az login

# Check App Service status
az webapp show --name training-bot-api --resource-group <rg-name> --query state

# Restart App Service
az webapp restart --name training-bot-api --resource-group <rg-name>
az webapp restart --name training-bot-frontend --resource-group <rg-name>

# Stream live logs
az webapp log tail --name training-bot-api --resource-group <rg-name>

# Get environment/app settings
az webapp config appsettings list --name training-bot-api --resource-group <rg-name>

# Set an environment variable
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg-name> \
  --settings API_KEY="<new-key>"
```

### Verify Vector Store

```bash
# Check vector store loaded correctly (look for product count)
az webapp log tail --name training-bot-api --resource-group <rg-name> | grep "Vector store"

# Trigger re-ingest via API
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# List known products (if endpoint exists)
curl https://training-bot-api.azurewebsites.net/products
```

[TODO: Confirm the exact ingest and product-list API endpoint paths from the full `api/main.py` route definitions.]

### GitHub Actions — Manual Triggers

```bash
# Trigger code review manually (via GitHub CLI)
gh workflow run "Tool 1 — Code Review" --field review_mode=repo

# Trigger tech docs regeneration
gh workflow run "Tool 2 — Tech Documentation"

# Trigger UAT test pack generation
gh workflow run "Tool 5 — UAT Facilitation" \
  --field uat_mode=generate \
  --field release_version=1.0.0

# Trigger business docs for a release
gh workflow run "Tool 3 — Business Documentation" \
  --field project_name="Insurance Training Bot" \
  --field release_version=1.0.0
```

### Session Management

```bash
# Inspect sessions file
cat data/sessions.json | python -m json.tool | head -100

# Backup sessions before maintenance
cp data/sessions.json data/sessions.json.bak.$(date +%Y%m%