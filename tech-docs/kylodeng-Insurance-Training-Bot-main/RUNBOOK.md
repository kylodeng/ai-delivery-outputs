# Operational Runbook — Insurance Training Bot (`kylodeng/Insurance-Training-Bot-main`)

> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]
> **Repo:** `kylodeng/Insurance-Training-Bot-main`

---

## 1. Service Overview

The Insurance Training Bot is a Hong Kong–focused insurance sales training platform built on a FastAPI backend, a LangGraph multi-agent framework, and a RAG (Retrieval-Augmented Generation) pipeline backed by a local FAISS or Chroma vector store. It exposes two AI agents — a **Teacher Agent** for ongoing interactive coaching and an **Assessor Agent** for one-shot roleplay evaluation — both powered by an OpenAI-compatible LLM (routed through OpenRouter by default). Product knowledge is ingested from insurance PDF brochures (Sun Life products, hospital network lists, etc.) into the vector store at startup or via a `/ingest` endpoint. The application is deployed as two separate Azure App Services (`training-bot-api` and `training-bot-frontend`), with CI/CD managed by GitHub Actions. Five auxiliary AI workflows (code review, tech docs, business docs, auto testing, UAT facilitation) run on the same repository via Claude (Anthropic) and write outputs to a companion `ai-delivery-outputs` repository.

---

## 2. Health Checks

### 2.1 API Service (`training-bot-api`)

```bash
# Basic liveness — expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<api-hostname>/

# FastAPI auto-generated docs page (should return 200)
curl -s -o /dev/null -w "%{http_code}" https://<api-hostname>/docs

# [TODO: Is a /health or /ping endpoint implemented? Not found in the code — add one]
```

### 2.2 Vector Store

- On startup, the app logs one of:
  - ✅ `Vector store loaded (N products)` — store is healthy
  - ⚠️ `No vector store found — run POST /ingest first` — store is missing; ingestion required

```bash
# Trigger ingestion if the store is missing
curl -X POST https://<api-hostname>/ingest
```

### 2.3 LLM Connectivity

```bash
# Send a minimal chat request; expect a streamed response (non-empty body)
curl -X POST https://<api-hostname>/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"healthcheck","message":"ping"}'
```

### 2.4 Static File Serving (`/docs` mount)

```bash
# PDF assets must be reachable; pick any known PDF filename
curl -s -o /dev/null -w "%{http_code}" \
  https://<api-hostname>/docs/Insurance-product-info/Generations-II/Generations-II_PB_EN.pdf
# Expect 200
```

### 2.5 Frontend Service (`training-bot-frontend`)

```bash
# Expect HTTP 200
curl -s -o /dev/null -w "%{http_code}" https://<frontend-hostname>/
```

### 2.6 Session Persistence

```bash
# Sessions file must exist and be valid JSON after first session is created
ls -lh data/sessions.json
python -c "import json; json.load(open('data/sessions.json')); print('OK')"
```

### 2.7 GitHub Actions Workflows (CI/CD pipeline health)

- Navigate to **Actions** tab in the repo.
- Confirm `Test & Deploy` workflow is green on `main`.
- Confirm `Tool 2 — Tech Documentation` last run succeeded (runs every Sunday 06:00 UTC).

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| `No vector store found` logged at startup; RAG tools return empty results | Vector store was never built, or `/data` directory is missing/empty on the App Service | 1. Ensure PDF files are present in `data/Insurance-product-info/`. 2. `POST /ingest` to rebuild. 3. Confirm `store.save()` completed (check logs for `index saved (N chunks)`). |
| `LLM returned empty / truncated response` | OpenRouter rate limit hit, model unavailable, or `API_KEY` expired/missing | 1. Check `OPENAI_URL_BASE` and `API_KEY` env vars. 2. Test the upstream model directly via curl. 3. Switch `OPENAI_MODEL` to a different model. 4. Check OpenRouter dashboard for quota. |
| `SSL: CERTIFICATE_VERIFY_FAILED` in logs | `httpx` clients are constructed with `verify=False` — this suppresses cert errors; if you see it elsewhere, a downstream service has an invalid cert | 1. Identify which outbound call is failing. 2. If it is OpenRouter or Azure, check their TLS certificate status. 3. [TODO: Remove `verify=False` in production and supply correct CA bundle.] |
| `KeyError: 'ANTHROPIC_API_KEY'` in GitHub Actions | Secret not set in the repo or is empty | 1. Go to **Settings → Secrets → Actions**. 2. Add/update `ANTHROPIC_API_KEY`. 3. Re-run the failed workflow. |
| `KeyError: 'GH_TOKEN'` in GitHub Actions | `GH_TOKEN` secret missing or token lacks repo write permissions | 1. Create a PAT with `repo` scope. 2. Add as `GH_TOKEN` secret. 3. Verify the token can write to the `ai-delivery-outputs` repo. |
| FastAPI startup fails with `ImportError` | Dependency not installed; `uv sync` not run, or wrong Python version | 1. SSH into App Service (Kudu). 2. Run `pip install -r requirements.txt`. 3. Confirm Python 3.13 (locally) / 3.12 (Actions) is active. |
| `sessions.json` decode error at startup | File corrupted (partial write during crash) | 1. Back up the corrupted file. 2. Delete `data/sessions.json`. 3. Restart — `load_sessions()` will start fresh. 4. [TODO: Implement atomic write for sessions file.] |
| Azure deployment fails: `publish-profile` secret not found | `AZURE_WEBAPP_PUBLISH_PROFILE_API` or `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret not set | 1. Download publish profile from Azure Portal → App Service → **Get publish profile**. 2. Paste contents into the corresponding GitHub secret. |
| PDF files not served at `/docs/*` (404) | `data/` directory not present after Azure deploy (not tracked in git or not bundled) | 1. Check if `data/` is in `.gitignore`. 2. Upload PDFs via Kudu or Azure Blob + mount. 3. [TODO: Confirm data persistence strategy for Azure App Service — ephemeral filesystem will lose files on restart.] |
| Tool calls return `[TESTER: verify this]` stubs | LLM could not determine answer from retrieval — vector store may be stale | 1. Re-run `/ingest`. 2. Check chunk count for the relevant product. 3. Verify PDF is present and parseable. |
| Code Review / Tech Docs workflow produces no output | `OUTPUT_REPO` (`ai-delivery-outputs`) does not exist or `GH_TOKEN` lacks write access | 1. Create `ai-delivery-outputs` repo under the same owner. 2. Verify PAT has write access. 3. Re-run workflow. |
| `ValueError: Could not parse Claude response as JSON` in Tool 1 | Claude returned non-JSON (e.g. a refusal, rate-limit message, or markdown-wrapped JSON) | 1. Check GitHub Actions logs for the 500-char debug dump. 2. Re-run; transient issue. 3. If persistent, check Anthropic API status. |
| CORS error in browser console | Frontend origin not in the `allow_origins` list in `main.py` | 1. Add the production frontend URL to `allow_origins`. 2. Redeploy. |

---

## 4. Deployment Procedure

### Prerequisites

- Python 3.13 (local) / 3.12 (CI)
- [`uv`](https://github.com/astral-sh/uv) package manager installed
- Azure CLI authenticated (`az login`)
- Azure App Services `training-bot-api` and `training-bot-frontend` exist
- GitHub secrets configured (see §3 and §5)
- PDF product files in `data/Insurance-product-info/`

---

### 4.1 Normal Deployment (push to `main`)

```
Push code → GitHub Actions "Test & Deploy" → pytest → deploy-api + deploy-frontend
```

1. **Create a feature branch** and open a PR against `main`.
2. **GitHub Actions auto-triggers:**
   - `Tool 1 — Code Review` posts AI review comment on the PR.
   - `Tool 4 — Auto Testing` generates test stubs.
3. **Merge PR to `main`** once review is satisfied.
4. The `Test & Deploy` workflow runs automatically:
   - **`test` job:** runs `uv run pytest tests/ -v` on Python 3.13.
   - **`deploy-api` job** (only on `main` push): exports `requirements.txt` via `uv export`, then deploys to `training-bot-api` Azure App Service using the publish profile.
   - **`deploy-frontend` job** (only on `main` push): same pattern for `training-bot-frontend`.
5. **Verify deployment** (see Health Checks §2).

### 4.2 Manual Deployment (emergency / hotfix)

```bash
# 1. Generate requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API via Azure CLI (alternative to GitHub Actions)
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 3. Deploy Frontend
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### 4.3 Vector Store Rebuild (after new PDFs added)

```bash
# 1. Place new PDFs under data/Insurance-product-info/

# 2. Run ingestion locally (optional pre-check)
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 3. Or trigger via API endpoint after deploy
curl -X POST https://<api-hostname>/ingest
```

### 4.4 Rollback Steps

```bash
# Option A — redeploy previous Git SHA via GitHub Actions
#   1. Go to Actions → "Test & Deploy"
#   2. Find the last successful run on main
#   3. Click "Re-run jobs" on that run
#   Note: this re-deploys whatever was at HEAD at that time — 
#         ensure the commit is still present

# Option B — Azure slot swap (if deployment slots are configured)
az webapp deployment slot swap \
  --resource-group <rg-name> \
  --name training-bot-api \
  --slot staging \
  --target-slot production

# Option C — revert the commit and push
git revert HEAD
git push origin main
# GitHub Actions will redeploy automatically

# [TODO: Are deployment slots configured on these App Services?]
# [TODO: Is there a staging slot for blue/green deploy?]
```

---

## 5. Monitoring & Alerting

### 5.1 Application Logs

| Log location | What to look for |
|---|---|
| Azure App Service → **Log stream** | FastAPI startup errors, `uvicorn` exceptions, vector store load status |
| Azure App Service → **Diagnose and solve problems** | HTTP 5xx rate, memory/CPU spikes |
| GitHub Actions → **Actions tab** | Workflow failures, deploy job status |
| `data/sessions.json` | Session count growth; corruption indicators |

### 5.2 Key Log Messages to Monitor

```
# Healthy startup
INFO  Vector store loaded (N products)

# Needs action
WARNING  No vector store found — run POST /ingest first

# LLM call failures (look for tracebacks after these)
ERROR  ... anthropic ... 
ERROR  ... openai ...

# Ingestion completion
INFO  [ingest] index saved (N chunks)
```

### 5.3 Metrics to Watch

| Metric | Tool | Threshold / Alert |
|---|---|---|
| HTTP 5xx error rate | Azure App Service metrics | Alert if > 1% over 5 min |
| Response latency (P95) | Azure App Service metrics | Alert if > 30 s (LLM streaming) |
| CPU usage | Azure App Service metrics | Alert if > 80% sustained |
| Memory usage | Azure App Service metrics | Alert if > 85% — FAISS index is in-memory |
| GitHub Actions failure | GitHub Actions notifications | Alert on any `deploy-api` or `deploy-frontend` failure |
| `sessions.json` file size | [TODO: set up Azure Monitor or cron] | Alert if > [TODO: define limit] |
| LLM upstream availability | [TODO: OpenRouter status page] | Subscribe to status.openrouter.ai |

### 5.4 Alerting Configuration

- [TODO: Are Azure Monitor alerts configured for these App Services?]
- [TODO: Is Application Insights enabled? If not, add `APPLICATIONINSIGHTS_CONNECTION_STRING` and instrument with `opencensus` or `opentelemetry`.]
- [TODO: Is there a Slack/Teams webhook or PagerDuty integration for CI/CD failures?]

---

## 6. Escalation Path

| Level | Who | When to contact | Contact |
|---|---|---|---|
| L1 — First responder | On-call engineer | Any production alert | [TODO: on-call rotation / PagerDuty] |
| L2 — App owner | Backend/ML engineer | LLM failures, vector store corruption, data issues | [TODO: name & contact] |
| L3 — Infrastructure | Azure / DevOps lead | App Service unavailable, networking issues, publish profile expired | [TODO: name & contact] |
| L4 — Vendor | Anthropic support | Claude API outage or quota issues | https://status.anthropic.com / [TODO: account contact] |
| L4 — Vendor | OpenRouter support | LLM routing failures | https://openrouter.ai/status / [TODO: account contact] |
| L4 — Vendor | Azure support | App Service platform issues | Azure support ticket |
| Business | Project sponsor / product owner | Go/no-go for rollback, data breach, compliance | [TODO: fill in team contacts] |

---

## 7. Useful Commands

### Environment Setup

```bash
# Install uv (if not present)
curl -Lsf https://astral.sh/uv/install.sh | sh

# Create virtualenv and install all deps
uv sync

# Activate venv (Linux/macOS)
source .venv/bin/activate

# Copy and edit env vars
cp .env.example .env   # [TODO: confirm .env.example exists]
# Required vars: API_KEY, OPENAI_URL_BASE, OPENAI_MODEL
```

### Local Development

```bash
# Run FastAPI backend
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=api --cov=core --cov-report=term-missing

# Ingest PDFs into vector store
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Ingest via API endpoint (when server is running)
curl -X POST http://localhost:8000/ingest
```

### Vector Store Management

```bash
# Check known products in the store (Python REPL)
python - <<'EOF'
from core.vector_store import get_vector_store
store = get_vector_store()
store.load()
print(store.get_known_products())
EOF

# Wipe and rebuild the vector store
rm -rf data/chroma_db    # or data/faiss_index — [TODO: confirm actual store path]
curl -X POST http://localhost:8000/ingest
```

### Session Management

```bash
# View all active sessions
python -c "import json; s=json.load(open('data/sessions.json')); print