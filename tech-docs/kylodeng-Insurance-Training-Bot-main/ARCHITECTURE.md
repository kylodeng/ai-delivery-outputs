# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is an AI-powered sales training platform designed to help new insurance agents (initially targeting Sun Life Hong Kong products) develop product knowledge and sales skills. The system ingests proprietary insurance product PDFs into a vector store (ChromaDB/FAISS/Pinecone), exposes a FastAPI backend with LangGraph-powered agents that operate in two modes — a **Teacher mode** (ongoing coaching chat with RAG-backed Q&A) and an **Assessor mode** (post-roleplay performance scoring) — and serves a frontend application. The backend uses an LLM (configurable via OpenRouter or Anthropic) for both PDF annotation during ingestion and agent reasoning during sessions. A suite of five AI-powered GitHub Actions tools (code review, tech docs, business docs, auto-testing, and UAT) augment the development lifecycle using Claude via Anthropic's API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend / LangGraph agent |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Persistent storage (local file or managed service) | [TODO: which backend is used in production — Chroma local, FAISS local, or Pinecone?] | Stores embedded insurance document chunks for RAG retrieval |
| `sessions.json` | File-based session store | Azure App Service (ephemeral local disk) | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions Runners | CI/CD compute | GitHub (ubuntu-latest) | Run tests, build, deploy, and AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated code review, tech docs, business docs, test files, and UAT artefacts |
| SendGrid | Email delivery SaaS | Twilio/SendGrid | Sends notification emails after AI tooling workflow completion |
| Anthropic Claude API (`claude-sonnet-4-6`) | External AI API | Anthropic | Powers the five AI delivery tools (code review, docs, tests, UAT) |
| OpenRouter / Anthropic API | External AI API | OpenRouter or Anthropic | Powers the insurance training agents and PDF annotation LLM |

---

## 3. Data Flow

### 3a. Document Ingestion (offline / admin)

1. An administrator places insurance product PDFs under `data/Insurance-product-info/`.
2. `POST /ingest` is called (or `core/ingest.py` is run directly); the ingestion pipeline walks the directory recursively.
3. For each PDF, `core/annotator.py` calls the configured LLM to extract document-level metadata (product name, doc type, summary) and page-level relevance flags; results are cached in a sidecar `.annot.json` file.
4. Relevant pages are passed to `core/chunker.py`, which splits text into semantic units (headings, bullets, paragraphs) capped at `max_words` (default 280).
5. Each chunk dict (with metadata: product name, page range, section title, file URL) is batched and embedded via the configured embedding model, then written into the vector store (`ChromaStore`, `LocalFAISSStore`, or `PineconeStore`).
6. The vector store index is persisted to disk (`store.save()`).

### 3b. Teacher Mode (real-time chat)

1. The user opens a session in the frontend; the frontend calls `POST /sessions` on `training-bot-api` to create a new session with a randomly generated `CustomerProfile`.
2. The user sends a message; the frontend calls `POST /chat` (streamed via `StreamingResponse`).
3. `api/main.py` retrieves the session, resets the per-request source tracker (`reset_sources()`), and invokes the LangGraph Teacher Agent via `astream_events`.
4. The Teacher Agent reasons with the system prompt (`TEACHER_SYSTEM`) and decides which RAG tool to call (e.g. `search_product`, `lookup_hospital_network`, `compare_plans`).
5. The RAG tool queries the vector store for the top-k most relevant chunks, appends source metadata to the per-request context variable, and returns formatted text with inline source markers (e.g. `[S1: doc p.4]`).
6. The LLM generates a streaming response incorporating retrieved context and citations.
7. Streamed tokens and tool-call events are forwarded to the frontend via Server-Sent Events. Source references are appended to the final response.
8. The conversation turn is appended to the in-memory session state (persisted to `sessions.json`).

### 3c. Roleplay / Assessor Mode

1. The frontend switches the session to `roleplay` mode; the backend uses a separate `_ROLEPLAY_SYSTEM` prompt that injects the `CustomerProfile` and instructs the LLM to act as that customer.
2. The trainee agent (human) interacts with the simulated customer for multiple turns.
3. When the session ends, the frontend calls `POST /assess`; `api/main.py` invokes the Assessor Agent (one-shot `ainvoke`) with the full conversation transcript and the `CustomerProfile`.
4. The Assessor Agent uses the same RAG tools to verify factual claims made by the trainee, then returns a structured JSON assessment across five scoring dimensions.
5. The assessment is returned to the frontend and optionally saved to the session.

### 3d. CI/CD Deploy Flow

1. A developer pushes to `main`; the `deploy.yml` workflow triggers.
2. The `test` job runs `pytest` against the full test suite.
3. On success, `deploy-api` and `deploy-frontend` jobs run in parallel, exporting `requirements.txt` via `uv export` and deploying to their respective Azure App Service instances using `azure/webapps-deploy@v3` with publish profiles stored as GitHub Secrets.

### 3e. AI Tooling Workflows

1. Triggered by PR, schedule, tag, or `workflow_dispatch`.
2. Each tool script (`tool1`–`tool5`) fetches repo files or PR diffs from GitHub API using `GH_TOKEN`.
3. Content is sent to `claude-sonnet-4-6` via Anthropic API.
4. Output (review JSON, markdown docs, test files, UAT packs) is committed to `ai-delivery-outputs` repo via GitHub API (`write_output_file`).
5. A notification email is sent via SendGrid to `kylo.deng@capco.com`.

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Secrets** are used for all sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow files.
- **CI gate**: Deployment only proceeds if tests pass (`needs: test`).
- **Deployment restricted to `main` branch**: Deploy jobs check `github.ref == 'refs/heads/main' && github.event_name == 'push'`.
- **Per-request source isolation**: `contextvars.ContextVar` is used for async-safe source tracking, preventing cross-request data leakage.
- **Secret string wrapping**: `SecretStr` from Pydantic is used for the API key passed to `ChatOpenAI`.

### ❌ Gaps and Risks

- **⚠️ TLS verification disabled**: Both `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `api/main.py` and `core/ingest.py`. This disables certificate validation on ALL outbound HTTP calls from the application, including LLM API calls. **This is a critical security gap — man-in-the-middle attacks are possible.**
- **⚠️ No API authentication on FastAPI endpoints**: There is no visible authentication middleware (API keys, OAuth, JWT) protecting `POST /chat`, `POST /assess`, `POST /sessions`, or `POST /ingest`. Any party who can reach the App Service can trigger LLM calls and ingest operations.
- **⚠️ CORS is overly permissive**: `allow_methods=["*"]` and `allow_headers=["*"]` — while origins are restricted, methods and headers should be scoped to what is actually needed.
- **⚠️ Sessions stored in a flat JSON file on local disk**: `sessions.json` is written to the App Service's ephemeral local filesystem. Data is not encrypted at rest. Scaling to multiple instances will cause session inconsistency (no shared store).
- **⚠️ Static file serving exposes raw PDFs**: `app.mount("/docs", StaticFiles(...))` serves the entire `data/` directory over unauthenticated HTTP. Insurance product PDFs (potentially proprietary) are publicly accessible.
- **⚠️ No encryption at rest specified for the vector store**: No encryption configuration is visible for ChromaDB/FAISS. [TODO: confirm if Azure App Service disk encryption covers this.]
- **⚠️ `GH_TOKEN` scope is unknown**: The token used in AI tooling workflows to write to `ai-delivery-outputs` and post PR comments should be scoped to minimum required permissions. [TODO: confirm GH_TOKEN is a fine-grained PAT, not a classic token with broad repo write access.]
- **⚠️ No WAF or network-level access control**: No Azure Front Door, Application Gateway, or VNet integration is visible in the IaC. The App Services appear to be publicly routable.
- **⚠️ `_API_KEY` falls back to empty string**: `os.getenv("API_KEY", "")` — if the env var is missing, the LLM client is initialised with no key, which will fail at runtime with an unclear error rather than a startup check.
- **No secrets scanning** workflow is configured (no `gitleaks`, `truffleHog`, etc.).

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key | Azure App Service application settings / `.env` locally |
| `OPENAI_URL_BASE` | No | Low | Azure App Service application settings / `.env` (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | Azure App Service application settings / `.env` (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | Azure App Service application settings / `.env` (default: `true`) |
| `ANTHROPIC_API_KEY` | Yes (AI tools) | 🔴 High | GitHub Actions Secret |
| `GH_TOKEN` | Yes (AI tools) | 🔴 High — repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (AI tools) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (AI tools) | Low | GitHub Actions env (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (AI tools) | Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (AI tools) | Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (AI tools) | Low | GitHub Actions env |
| `VECTOR_STORE_BACKEND` | [TODO: confirm env var name] | Low | [TODO: how is the vector store backend selected in production?] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude (`claude-sonnet-4-6`) | External AI API | AI tooling (code review, docs, tests, UAT) | Accessed via `anthropic` Python SDK |
| OpenRouter / Anthropic (configurable) | External AI API | Insurance training agents + PDF annotation LLM | Accessed via `langchain_openai.ChatOpenAI` with custom `base_url` |
| LangChain / LangGraph | Python library | Agent orchestration, tool calling, streaming | Core agent framework |
| Azure App Service (`training-bot-api`) | Cloud PaaS | Hosts FastAPI backend | Deployed via GitHub Actions |
| Azure App Service (`training-bot-frontend`) | Cloud PaaS | Hosts frontend | Deployed via GitHub Actions |
| ChromaDB / FAISS / Pinecone | Vector store | RAG document retrieval | Backend selected at runtime via `core/vector_store.py` |
| Voyage AI (implied) | External embedding API | Document embedding during ingestion | Referenced in `ingest.py` comments re: rate limits (3 RPM free tier) |
| SendGrid | Email SaaS | Notification emails from AI tools | Via `SENDGRID_API_KEY` |
| GitHub API (`api.github.com`) | External API | Reading repo files, posting PR comments, writing output artefacts | Via `GH_TOKEN` |
| `ai-delivery-outputs` | External GitHub repo | Stores AI-generated artefacts | Must exist and be writable by `GH_TOKEN` |
| `pdfplumber` | Python library | PDF text extraction during ingestion | |
| `FastAPI` | Python framework | REST API and streaming responses | |
| `uv` | Python package manager | Dependency management and venv | Used in CI/CD |
| `dotenv` | Python library | Local `.env` loading | Not used in production (App Service env vars used directly) |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Install uv
curl -Lsf https://astral.sh/uv/install.sh | sh

# Clone repo
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

### Local Development

```bash
# Install all dependencies (including dev)
uv sync

# Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.

# Ingest documents into the vector store (run once or after adding PDFs)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000

# Start the frontend (if separate Vite app)
# [TODO: confirm frontend start command — npm run dev or Chainlit?]
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (GitHub Actions — automatic)

```
# Deployment is triggered automatically on push to main branch
# after all tests pass. No manual steps required.
git push origin main
```

### Manual Deploy (if needed outside CI/CD)

```bash
# Generate production requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API to Azure App Service
az webapp deploy --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend to Azure App Service
az webapp deploy --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Ingest Documents (production)

```bash
# [TODO: confirm whether /ingest endpoint is exposed in production
# or whether ingestion must be run as a one-off command before deployment]
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

---

## 8. Risks and TODOs

### Critical Risks

| Risk | Severity | Detail |
|---|---|---|
| TLS verification disabled (`verify=False`) | 🔴 Critical | All outbound HTTPS calls (LLM API, embeddings) bypass certificate validation. Revert to default `verify=True` or supply a trusted CA bundle. |
| No authentication on API endpoints | 🔴 Critical | `/chat