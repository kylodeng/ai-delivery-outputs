# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a dual-mode AI-powered training platform for insurance sales agents, built for a Hong Kong insurance context (Sun Life products). It consists of a FastAPI backend and a separate frontend application, both deployed to Azure App Service. The backend hosts a Retrieval-Augmented Generation (RAG) system that ingests Sun Life insurance product PDFs, chunks and embeds them into a vector store, and exposes LangGraph agents via streaming HTTP endpoints. Two agent modes are provided: a **Teacher agent** that interactively coaches trainees on insurance concepts and product knowledge, and an **Assessor agent** that evaluates completed roleplay sessions against factual accuracy and sales technique. A suite of five GitHub Actions-based AI delivery tools (powered by Anthropic Claude) provide automated code review, technical documentation generation, business documentation, test generation, and UAT facilitation as supporting SDLC automation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving RAG/agent endpoints |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (Chroma / FAISS / Pinecone) | Embedded or managed vector DB | Azure (local) / Pinecone (SaaS) | Stores PDF chunk embeddings for RAG retrieval |
| GitHub Actions Runners | Ephemeral CI/CD compute | GitHub (ubuntu-latest) | Test, build, and deploy pipeline execution |
| OpenRouter / Anthropic API | External LLM SaaS | External | Inference for teacher/assessor agents and AI delivery tools |
| Voyage AI (embedding) | External embedding SaaS | External | Embedding PDF chunks for vector store ingestion |
| SendGrid | External email SaaS | External | Notification emails for AI delivery tool outputs |
| `ai-delivery-outputs` (GitHub repo) | GitHub Repository | GitHub | Persistent storage for AI-generated docs, reviews, test reports |
| `data/sessions.json` | Flat-file session store | Azure App Service filesystem | Persists multi-turn conversation sessions across restarts |

> [TODO: Confirm which vector store backend (Chroma, LocalFAISS, or Pinecone) is used in production — `core/__init__.py` exposes all three but the production selection is not declared in IaC]

> [TODO: Confirm whether the frontend is Chainlit (mentioned in `main.py` CORS config) or a Vite SPA — both origins are whitelisted but no frontend source files were provided]

---

## 3. Data Flow

### 3a — PDF Ingestion (offline / on-demand)
1. Operator calls `POST /ingest` on the FastAPI backend (or runs `core/ingest.py` as a CLI script).
2. `ingest_directory()` walks `data/Insurance-product-info/` recursively and finds all `.pdf` files.
3. For each PDF, `load_or_create_annotations()` checks for a sidecar `.annot.json` cache file. On cache miss, the first three pages are sent to the LLM (OpenRouter/Anthropic via `OPENAI_URL_BASE`) to extract product metadata (product name, doc type, summary, per-page relevance flags).
4. Relevant pages are extracted and split into semantic text chunks by `core/chunker.py` (heading-aware, bullet-aware, max ~280 words/chunk).
5. Chunks are batched and sent to the Voyage AI embedding API (or configured embedding provider).
6. Embeddings + metadata are written to the vector store (Chroma/FAISS/Pinecone) and persisted to disk via `store.save()`.

### 3b — Teacher Mode (real-time streaming)
1. User sends a message from the frontend to `POST /chat` (or equivalent streaming endpoint) on `training-bot-api`.
2. FastAPI initialises or retrieves a `Session` from `data/sessions.json`.
3. `reset_sources()` initialises a per-request source-tracking context variable.
4. The LangGraph teacher agent receives the conversation history + `TEACHER_SYSTEM` prompt.
5. The agent decides which RAG tool(s) to call (`search_product`, `search_all`, `lookup_hospital_network`, `compare_plans`, `lookup_exclusions`, `search_claim_procedure`, etc.).
6. Each tool queries the vector store with a similarity search; matching chunks are returned with source metadata (document name, page numbers, file URLs).
7. `_collect_sources()` deduplicates sources and assigns citation IDs (`S1`, `S2`, …).
8. The agent synthesises a response with inline citations and streams tokens back to the client via `StreamingResponse`.
9. Source metadata is appended to the streamed response for the UI to render citation links.

### 3c — Roleplay + Assessment Mode
1. User triggers roleplay mode; FastAPI calls `generate_profile()` to randomly construct a `CustomerProfile` (Hong Kong persona with realistic demographics, income, goals, etc.).
2. A roleplay `Session` is created; the LLM acts as the customer using `_ROLEPLAY_SYSTEM`.
3. The trainee conducts a multi-turn sales conversation; messages are stored in session history.
4. On session end, the frontend calls the assessment endpoint; the assessor agent receives the full conversation + profile.
5. The assessor uses the same RAG tools to fact-check every product claim the trainee made.
6. A structured assessment (score, dimensions, findings) is returned as JSON and displayed to the trainee.

### 3d — CI/CD & AI Delivery Tools
1. Developer pushes to `main` or opens a PR; GitHub Actions triggers `deploy.yml`.
2. Tests run (`pytest tests/`) under Python 3.13 with `uv`.
3. On `main` push after test pass: `uv export` generates `requirements.txt`; `azure/webapps-deploy@v3` deploys both `training-bot-api` and `training-bot-frontend` using publish profiles stored in GitHub Secrets.
4. In parallel, AI tooling workflows (tools 1–5) may trigger; these call the Anthropic Claude API, write outputs to the `ai-delivery-outputs` GitHub repo via the GitHub API, and send email notifications via SendGrid.

---

## 4. Security Posture

### Secured
- **GitHub Secrets** used for all credentials in CI/CD (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow YAML.
- **Publish-profile-based deployment** scopes Azure deployment credentials to individual apps rather than subscription-wide service principals.
- **Environment variables** used for API keys in application code (`os.getenv`); no hardcoded secrets observed in source files.
- **Session data** scoped per session UUID; no cross-session data leakage observed in session management code.

### Not Secured / Gaps

- ⚠️ **TLS verification explicitly disabled**: `main.py` creates all `httpx.Client` and `httpx.AsyncClient` instances with `verify=False`. This disables SSL/TLS certificate verification for all outbound LLM API calls, exposing the system to man-in-the-middle attacks on API key transmission.
- ⚠️ **No authentication on the API**: No API key, JWT, or OAuth middleware is visible in `main.py`. The `/ingest` endpoint (which triggers expensive LLM embedding) and all chat endpoints appear publicly accessible if the App Service URL is known.
- ⚠️ **CORS is overly permissive**: `allow_methods=["*"]` and `allow_headers=["*"]` are set. Origins are restricted to localhost variants — but no production frontend origin is whitelisted, suggesting the CORS config is still in development posture. [TODO: Add production frontend URL to `allow_origins`]
- ⚠️ **`sessions.json` is stored on the App Service filesystem**: This file is not persisted to Azure Storage or a database. It will be lost on any App Service instance restart, slot swap, or scale-out to multiple instances. Multi-instance deployments would have split session state.
- ⚠️ **PDF/data files served as static files without authentication**: `app.mount("/docs", StaticFiles(...))` serves all insurance product PDFs and data files over unauthenticated HTTP. Insurance product documents may be proprietary.
- ⚠️ **No encryption at rest declared for vector store**: The local FAISS/Chroma store is persisted to the App Service filesystem with no encryption-at-rest configuration visible in IaC. [TODO: Enable Azure App Service managed disk encryption or move to Azure Cognitive Search / Pinecone with encryption at rest]
- ⚠️ **`GH_TOKEN` in AI tooling workflows**: The token is used with write access to the `ai-delivery-outputs` repo. Scope of this token is not declared — if it is a classic PAT with broad repo scope, it represents a significant lateral-movement risk. [TODO: Replace with a fine-grained PAT scoped to `ai-delivery-outputs` repo only]
- ⚠️ **No secrets scanning or SAST** in the CI pipeline — `deploy.yml` runs only `pytest`. No Bandit, Semgrep, or GitHub Advanced Security checks are configured.
- ⚠️ **No rate limiting** on the FastAPI endpoints — the `/ingest` and streaming endpoints can be triggered freely, resulting in unbounded LLM API cost exposure.
- ⚠️ **Ingest LLM uses `verify=False`**: `core/ingest.py` `_build_ingest_llm()` also sets `verify=False`.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter or Anthropic) | App Service environment / `.env` local |
| `OPENAI_URL_BASE` | Yes | 🟡 Medium — LLM endpoint URL | App Service environment / `.env` local |
| `OPENAI_MODEL` | No | 🟢 Low — model name string | App Service environment / `.env` local |
| `SHOW_TOOL_CALLS` | No | 🟢 Low | App Service environment / `.env` local |
| `ANTHROPIC_API_KEY` | Yes (AI tools) | 🔴 High — Anthropic Claude API key | GitHub Secret → Actions env |
| `GH_TOKEN` | Yes (AI tools) | 🔴 High — GitHub PAT with repo write access | GitHub Secret → Actions env |
| `SENDGRID_API_KEY` | Yes (AI tools) | 🔴 High — SendGrid API key | GitHub Secret → Actions env |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Secret |
| `OUTPUT_REPO` | No | 🟢 Low — output repo name | GitHub Actions env (hardcoded default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env (`github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (email address) | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | GitHub Actions env |

> ⚠️ `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded in all five workflow YAML files as plaintext, not secrets. While not high-sensitivity on their own, `kylo.deng@capco.com` is a named individual's address visible in the repository.

> [TODO: Confirm whether `API_KEY` / `OPENAI_URL_BASE` are set as App Service Application Settings in Azure, or injected another way — no ARM/Bicep/Terraform IaC is present to confirm]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Azure App Service | Cloud PaaS | Hosting API and frontend | No IaC (ARM/Bicep/Terraform) present — provisioned manually or out-of-band |
| OpenRouter (`openrouter.ai/api/v1`) | External LLM API | LLM inference for agents (default endpoint) | Configurable via `OPENAI_URL_BASE` |
| Anthropic Claude API | External LLM API | AI delivery tools (code review, docs, testing, UAT) + optionally ingest LLM | `claude-sonnet-4-6` model used in `shared.py` |
| Voyage AI | External embedding API | PDF chunk embedding for RAG vector store | Implied by `core/ingest.py` batch/rate-limit settings; [TODO: confirm embedding model name] |
| Pinecone | External managed vector DB | Optional production vector store | Configurable; may use local FAISS/Chroma instead |
| SendGrid | External email API | AI tool output notifications | Used in all 5 AI delivery workflows |
| LangChain / LangGraph | Python library | Agent orchestration, tool calling, message management | `langchain`, `langchain_openai`, `langchain_core` |
| pdfplumber | Python library | PDF text extraction | Used in `core/chunker.py` |
| FastAPI + Uvicorn | Python framework | Backend API server | |
| `uv` (Astral) | Python package manager | Dependency management and build | Replaces pip/poetry in CI |
| `ai-delivery-outputs` (GitHub repo) | Sibling GitHub repository | Persistent storage for all AI tool outputs | Must exist and be writable by `GH_TOKEN` |
| `kylodeng` GitHub organisation | GitHub | Source repo owner for AI tool cross-repo operations | |

---

## 7. Deployment Instructions

### Prerequisites
- Azure App Services `training-bot-api` and `training-bot-frontend` must be pre-provisioned [TODO: no IaC provided — provision manually via Azure Portal or Azure CLI]
- Publish profiles downloaded from each App Service and stored as GitHub Secrets
- All GitHub Secrets listed in Section 5 configured in the repository

### Local Development

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Run PDF ingestion (one-time or when documents change)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Start the API server
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest tests/ -v
```

### CI/CD Deployment (Automated)

Deployment is fully automated via GitHub Actions on push to `main`:

```
git push origin main
# → triggers .github/workflows/deploy.yml
# → runs pytest
# → on success: deploys to training-bot-api and training-bot-frontend in parallel
```

### Manual Azure CLI Deployment (if needed)

```bash
# Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API (using Azure CLI — requires az login)
az webapp deployment source config-zip \
  --resource-group <rg-name> \
  --name training-bot-api \
  --src <zip-file>

# Deploy Frontend
az webapp deployment source config-zip \
  --resource-group <rg-name> \
  --name training-bot-frontend \
  --src <zip-file>
```

> [TODO: Resource group name, subscription, and region are not documented anywhere in the repo]

### PDF Re-ingestion

```bash
# After adding new PDFs to data/Insurance-product-info/
# Call the ingest endpoint (when running):
curl -X POST http://localhost:8000/ingest

# Or run directly:
uv run python core/ingest.py --pdf-dir data/Insurance-product-info
```

---

## 8. Risks and TODOs

### Critical Risks

| Risk | Severity | Detail