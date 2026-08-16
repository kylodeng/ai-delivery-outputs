# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is an AI-powered sales training platform designed to help insurance agents (primarily in a Hong Kong context) master product knowledge, sales technique, and customer handling. It consists of a FastAPI backend and a frontend application (both deployed to Azure App Service), backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life Hong Kong insurance product PDFs into a vector store. Agents interact with two LangGraph-powered AI personas: a **Teacher** agent for guided learning and an **Assessor** agent for post-roleplay performance evaluation. A separate suite of five AI-assisted GitHub Actions workflows (using Claude via Anthropic API) provides automated code review, technical documentation, business documentation, test generation, and UAT facilitation for the repository itself.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend (LangGraph agents, RAG tools, session management) |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store | Local FAISS / ChromaDB / Pinecone (configurable) | Local disk or Pinecone cloud | Stores embedded insurance document chunks for RAG retrieval |
| `sessions.json` | File on App Service local disk | Azure (ephemeral) | Persists multi-turn conversation sessions across server restarts |
| Insurance PDF data | Static files served via `/docs` mount | Azure (App Service local disk) | Source documents for RAG ingestion and browser linking |
| GitHub Actions Runners | `ubuntu-latest` (ephemeral) | GitHub | CI/CD: test, deploy, AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated reports (code reviews, docs, test files, UAT packs) |
| Anthropic Claude API | External API (`claude-sonnet-4-6`) | Anthropic | Powers the five AI delivery workflow tools (code review, docs, tests, UAT) |
| OpenRouter / OpenAI-compatible API | External API | OpenRouter (configurable) | Powers the LangGraph Teacher and Assessor agents at runtime |
| Voyage AI (implied) | External Embedding API | Voyage AI | Embeds document chunks for vector store ingestion |
| SendGrid | External Email API | Twilio/SendGrid | Sends notification emails from AI workflow tools |

---

## 3. Data Flow

### Runtime (Chat) Data Flow

1. A user opens the frontend (`training-bot-frontend` App Service), which connects to the FastAPI backend (`training-bot-api` App Service).
2. The frontend sends a chat message (with session ID and mode: `teacher` or `roleplay`) via HTTP to the FastAPI `/chat` or streaming endpoint.
3. FastAPI loads the session state from in-memory store (originally hydrated from `sessions.json` on startup).
4. For **teacher mode**, FastAPI instantiates a LangGraph Teacher agent with RAG tools. For **roleplay mode**, a customer persona is simulated; for **assessment mode**, the Assessor agent is invoked post-roleplay.
5. The LangGraph agent decides which RAG tool to call (e.g. `search_product`, `compare_plans`, `lookup_exclusions`).
6. The RAG tool queries the local vector store (FAISS/Chroma/Pinecone) using a Voyage AI embedding of the query.
7. The vector store returns ranked document chunks (with metadata: product name, page, section, file URL).
8. The agent assembles the retrieved chunks with source citations and sends them as context to the LLM (via OpenRouter/OpenAI-compatible API).
9. The LLM streams a response back through the FastAPI `StreamingResponse` to the frontend.
10. Source citations (`[[S1]]`, `[[S2]]` etc.) are collected via a `contextvars.ContextVar` and appended to the response for the UI to render as links to `/docs/<path>` (PDF served as static file).
11. Session state (conversation history, profile) is updated in memory and flushed to `sessions.json`.

### Ingestion Data Flow

1. A developer or operator calls `POST /ingest` on the API (or runs `core/ingest.py` directly).
2. `ingest_directory()` walks the `data/Insurance-product-info/` directory recursively for PDFs.
3. For each PDF, `load_or_create_annotations()` checks for a sidecar `.annot.json` file; if absent, it calls the annotation LLM (Anthropic/OpenRouter) to extract product metadata and per-page relevance flags, and caches the result as `.annot.json`.
4. Relevant pages are chunked by `extract_chunks_from_pdf()` using heuristic heading/bullet detection and word-count limits.
5. Chunks (with metadata: product name, document name, page range, section title, file URL) are embedded in batches via Voyage AI and upserted into the vector store.
6. The vector store index is saved to disk (`store.save()`).

### CI/CD Data Flow

1. A developer pushes to `main` or opens a PR.
2. GitHub Actions runs `pytest` tests.
3. On successful test of a `main` push, `azure/webapps-deploy@v3` deploys the API and frontend bundles to their respective Azure App Services using publish profiles stored as GitHub Secrets.
4. Separately, AI workflow tools (Tools 1–5) trigger on PRs, schedules, or tags; they call the Anthropic Claude API, write outputs to the `ai-delivery-outputs` repo, and send email notifications via SendGrid.

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Secrets** used for all sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow files.
- **Deployment gated on tests** — `deploy-api` and `deploy-frontend` jobs have `needs: test`, so broken builds are not deployed.
- **Deploy only on `main` push** — PRs cannot trigger deployment.
- **CORS restricted** in `api/main.py` to specific localhost origins and the App Service origin — not wildcard `*`.
- **LLM API key** passed via `SecretStr` (Pydantic) — not logged as plaintext.
- **Document annotation cached** — annotation LLM is not called repeatedly for unchanged PDFs, reducing API key exposure surface.

### ❌ Gaps and Missing Controls

- **⚠️ TLS certificate verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls. This disables SSL/TLS verification, leaving all LLM traffic vulnerable to man-in-the-middle attacks. This must be fixed for any production deployment.
- **⚠️ No authentication on the FastAPI API**: There is no authentication middleware, API key validation, or OAuth on the FastAPI endpoints. Any user who can reach the App Service URL can create sessions, ingest documents, and query the agent.
- **⚠️ `POST /ingest` is unauthenticated**: Anyone who can reach the API can trigger a full re-ingestion, which consumes LLM API credits and Voyage AI quota.
- **⚠️ Sessions persisted to local disk (`sessions.json`)**: Session data (conversation history, customer profiles) is written to App Service local ephemeral storage. This data is lost on instance recycle/scale-out and is not encrypted at rest.
- **⚠️ No encryption at rest for vector store**: The FAISS/Chroma index is stored on App Service local disk with no encryption. If Pinecone is used, encryption depends on Pinecone's tier.
- **⚠️ CORS allows localhost origins in production config**: `http://localhost:5173` and `http://127.0.0.1:5173` are whitelisted in the production CORS config. These should be removed in production builds.
- **⚠️ `API_KEY` falls back to empty string**: `os.getenv("API_KEY", "")` — if the environment variable is missing, an empty API key is used silently rather than failing fast.
- **⚠️ `GH_TOKEN` in AI workflows has unknown scope**: The token is used to read source repos, write to `ai-delivery-outputs`, and post PR comments. The required scopes are not documented; if the token has `repo` (full) scope, this is overly broad. [TODO: Confirm GH_TOKEN scopes — minimum required are `contents:write` on output repo and `pull-requests:write` on source repo]
- **⚠️ No secrets scanning**: No `gitleaks`, `trufflehog`, or GitHub secret scanning is configured in the workflow files.
- **⚠️ PDF documents served as static files without auth**: `app.mount("/docs", StaticFiles(...))` serves all insurance product PDFs publicly over HTTP without any access control.
- **⚠️ No WAF or DDoS protection** configured (would require Azure Front Door or Application Gateway — not present in IaC).
- **⚠️ No network isolation**: App Services are not placed in a VNet; there are no private endpoints configured.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key (OpenRouter or Anthropic) | Azure App Service Application Settings / `.env` file locally |
| `OPENAI_URL_BASE` | No (defaults to `https://openrouter.ai/api/v1`) | Low | Azure App Service Application Settings / `.env` |
| `OPENAI_MODEL` | No (defaults to `openai/gpt-oss-20b:free`) | Low | Azure App Service Application Settings / `.env` |
| `SHOW_TOOL_CALLS` | No (defaults to `true`) | Low | Azure App Service Application Settings / `.env` |
| `ANTHROPIC_API_KEY` | Yes (AI workflow tools) | **High** | GitHub Repository Secret |
| `GH_TOKEN` | Yes (AI workflow tools) | **High** — GitHub PAT | GitHub Repository Secret |
| `SENDGRID_API_KEY` | Yes (AI workflow tools) | **High** | GitHub Repository Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (CI/CD deploy) | **High** — Azure deploy credential | GitHub Repository Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (CI/CD deploy) | **High** — Azure deploy credential | GitHub Repository Secret |
| `OUTPUT_REPO` | No (defaults to `ai-delivery-outputs`) | Low | GitHub Actions env / hardcoded default |
| `OUTPUT_REPO_OWNER` | No (defaults to `GITHUB_REPOSITORY_OWNER`) | Low | GitHub Actions env |
| `NOTIFY_EMAIL` | No (defaults to `kylo.deng@capco.com`) | Low — PII (email address) | GitHub Actions env / hardcoded default |
| `SENDER_EMAIL` | No (defaults to `kylo.deng@capco.com`) | Low | GitHub Actions env / hardcoded default |
| `VECTOR_STORE_TYPE` | [TODO: confirm env var name] | Low | Azure App Service Application Settings |
| `PINECONE_API_KEY` | Conditional (if Pinecone store is used) | **High** | Azure App Service Application Settings |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External SaaS API | AI workflow tools (code review, docs, testing, UAT) | Requires `ANTHROPIC_API_KEY` |
| OpenRouter (or compatible OpenAI API) | External SaaS API | LangGraph Teacher/Assessor LLM inference at runtime | Configurable via `OPENAI_URL_BASE`; defaults to OpenRouter |
| Voyage AI | External SaaS API | Embedding generation for vector store ingestion | Implied by `batch_delay` rate-limit comment referencing "Voyage AI free-tier 3 RPM" |
| LangChain / LangGraph | Python library | Agent orchestration, tool-calling, message management | Core runtime dependency |
| LangChain-OpenAI | Python library | ChatOpenAI wrapper for LLM calls | |
| FastAPI | Python library | API server framework | |
| Chainlit (implied) | Python library / UI framework | Frontend chat UI | Referenced in CORS config (`localhost:8000`) |
| pdfplumber | Python library | PDF text extraction during ingestion | |
| FAISS / ChromaDB / Pinecone | Library / External SaaS | Vector store backends (configurable) | |
| SendGrid | External SaaS API | Email notifications from AI workflow tools | Requires `SENDGRID_API_KEY` |
| GitHub API (`api.github.com`) | External API | Read source repos, write output repo, post PR comments | Used by AI workflow tools |
| `ai-delivery-outputs` | External GitHub Repository (same owner) | Stores AI-generated artifacts (docs, reviews, test files) | Must exist and be writable by `GH_TOKEN` |
| `uv` (Astral) | Build tool | Python dependency management and packaging | Used in CI/CD and local dev |
| Azure App Service | Cloud PaaS | Application hosting | Two instances: API and frontend |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Install uv (Python package manager)
pip install uv

# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# Install dependencies
uv sync
```

### Local Development

```bash
# Copy and configure environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.

# Run the API server locally
uv run uvicorn api.main:app --reload --port 8000

# In a separate terminal, ingest PDFs into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
# OR via the API endpoint:
curl -X POST http://localhost:8000/ingest
```

### Running Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (via GitHub Actions)

Deployment is fully automated. Push to `main` to trigger:

```bash
git push origin main
# This triggers:
# 1. test job (pytest)
# 2. deploy-api job → deploys to Azure App Service: training-bot-api
# 3. deploy-frontend job → deploys to Azure App Service: training-bot-frontend
```

### Manual Deployment (Azure CLI fallback)

```bash
# Generate requirements.txt from uv lockfile
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Post-Deployment: Ingest Documents

```bash
# After first deployment, trigger PDF ingestion via the API
curl -X POST https://training-bot-api.azurewebsites.net/ingest
# [TODO: confirm the exact ingest endpoint path and whether auth is required]
```

### AI Workflow Tools (Manual Trigger)

```bash
# Trigger code review manually via GitHub CLI
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Trigger business documentation for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Insurance Training Bot" \
  -f release_version="1.0.0"
```

---

## 8. Risks and TODOs

### Critical Risks

| Risk | Severity | Detail |
|---|---|---|
| TLS verification disabled | **CRITICAL** | `httpx.Client(verify=False)` in `api/main.py` and `core/ingest.py` disables SSL certificate verification for all LLM API calls. Exposes all prompts, completions, and API keys to