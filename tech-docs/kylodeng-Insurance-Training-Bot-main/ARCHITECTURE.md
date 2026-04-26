# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is an AI-powered platform designed to train insurance sales agents at Sun Life Hong Kong. It provides two modes: a **Teacher mode**, where a conversational AI coach teaches product knowledge, sales techniques, and conducts exercises via a RAG-backed LangGraph agent; and a **Roleplay/Assessment mode**, where the agent simulates a customer profile and subsequently assesses the trainee's performance across multiple dimensions. The backend is a FastAPI application backed by a vector store (Chroma, FAISS, or Pinecone) ingesting Sun Life insurance product PDFs. A separate Chainlit-based frontend delivers the chat UI. Both services are deployed as Azure App Service instances via GitHub Actions CI/CD, with AI tooling workflows (code review, tech docs, UAT, test generation) powered by Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend; serves RAG queries, session management, LLM streaming |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the Chainlit chat frontend UI |
| Vector Store (Chroma / FAISS / Pinecone) | Persistence Layer | Local disk / Pinecone (SaaS) | Stores and retrieves embedded insurance document chunks |
| Insurance PDF documents | Static file store (local `data/` directory) | Azure App Service (disk) | Source knowledge base; served over HTTP at `/docs` mount |
| `sessions.json` | File-based session store | Azure App Service (disk) | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions runners | CI/CD compute | GitHub (ubuntu-latest) | Test, build, and deploy jobs |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated docs: code reviews, architecture docs, test files |
| Anthropic Claude (`claude-sonnet-4-6`) | External AI API | Anthropic | Powers CI/CD tooling workflows (code review, docs, UAT, tests) |
| OpenRouter / OpenAI-compatible endpoint | External AI API | OpenRouter (configurable) | Powers the runtime teacher and assessor agents (`OPENAI_URL_BASE`) |
| Voyage AI (implied) | External embedding API | Voyage AI | Embedding model used during PDF ingestion (free-tier rate-limit logic present) |
| SendGrid | Email API | SendGrid (SaaS) | Sends notification emails from CI/CD AI tool workflows |

---

## 3. Data Flow

### 3a. PDF Ingestion (one-time / manual)

1. Operator places Sun Life product PDFs under `data/Insurance-product-info/`.
2. `POST /ingest` is called on the FastAPI API (or `core/ingest.py` run directly).
3. `ingest_directory()` walks the PDF directory; for each file, `load_or_create_annotations()` calls the configured LLM (Anthropic via OpenRouter) to produce a `.annot.json` sidecar describing document type and page relevance.
4. `extract_chunks_from_pdf()` uses `pdfplumber` to extract text, applies heading/bullet heuristics, and produces word-bounded chunks with metadata (product name, page numbers, section title, file URL).
5. Chunks are batched and sent to the embedding model (Voyage AI or similar); embeddings are stored in the configured vector store (Chroma/FAISS/Pinecone) and persisted to disk/Pinecone.

### 3b. Teacher Mode — Chat Request

1. User sends a message via the Chainlit frontend (HTTP → `training-bot-frontend`).
2. Frontend POSTs to `training-bot-api` (`/chat` or equivalent streaming endpoint).
3. FastAPI creates or retrieves a `Session` object (loaded from `sessions.json`).
4. `make_teacher_agent()` constructs a LangGraph agent with eight RAG tools bound to the vector store.
5. Agent calls the configured LLM (OpenRouter) via `ChatOpenAI`; tool calls (`search_product`, `search_all`, etc.) invoke `ChromaStore`/`FAISSStore` similarity search.
6. Retrieved chunks are de-duplicated and source-tracked via `contextvars` (`_sources_ctx`); source IDs (S1, S2…) are injected inline into the assistant response.
7. Streamed tokens are sent back to the frontend via `StreamingResponse` using `astream_events`.
8. Session state (message history) is serialised back to `sessions.json`.

### 3c. Roleplay & Assessment Mode

1. User initiates roleplay; `generate_profile()` randomly selects from HK-context persona pools to build a `CustomerProfile`.
2. The `_ROLEPLAY_SYSTEM` prompt is injected; the LLM simulates the customer character for the duration of the conversation.
3. On session end, the frontend triggers assessment; `make_assessor_agent()` receives the full conversation history and customer profile.
4. Assessor calls the same eight RAG tools to verify factual claims made by the trainee against the knowledge base.
5. Structured assessment JSON is returned (five dimensions, scores, feedback).

### 3d. CI/CD AI Tooling Workflows

1. GitHub event (PR, push to main, schedule, tag) triggers a workflow (tools 1–5).
2. Workflow fetches repo files/diffs via GitHub API using `GH_TOKEN`.
3. Python script calls `anthropic.Anthropic.messages.create()` with relevant system prompt and file content.
4. Claude returns structured JSON or markdown output.
5. Output is committed to the `ai-delivery-outputs` GitHub repo via GitHub Contents API.
6. SendGrid API sends an email notification to `kylo.deng@capco.com`.
7. PR comments are posted back to the source repo (tool 1 only).

---

## 4. Security Posture

### What is secured

- **GitHub Secrets** used for all sensitive keys in CI/CD (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow files.
- **`SecretStr`** wrapping of `API_KEY` in `main.py` using Pydantic, preventing accidental logging.
- **Deployment gating**: `deploy-api` and `deploy-frontend` jobs only run on push to `main` after tests pass.
- **CORS restrictions**: Only specific localhost origins are whitelisted (Vite dev server + Chainlit).
- **Session IDs**: Generated with `uuid.uuid4()`.

### What is NOT secured — Gaps

- ⚠️ **TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `main.py` and `core/ingest.py`. This means all outbound LLM API calls bypass certificate validation, exposing the system to man-in-the-middle attacks in production. **This must be fixed before production use.**
- ⚠️ **No authentication on the FastAPI API**: There is no API key, JWT, OAuth, or session authentication middleware visible on any FastAPI routes. Any caller who can reach `training-bot-api` can query the LLM, access session data, and trigger ingestion.
- ⚠️ **`sessions.json` is unencrypted on disk**: Contains full conversation histories including customer profiles. If the App Service disk is compromised or accessible, all session data is exposed in plaintext.
- ⚠️ **`data/` directory served publicly via `StaticFiles`**: All insurance PDFs and annotation JSON files are accessible at `/docs/*` with no authentication. Sensitive product documentation is publicly accessible.
- ⚠️ **No encryption at rest specified for vector store**: Local FAISS/Chroma stores are unencrypted files on App Service disk. No Azure Disk Encryption configuration is present in IaC.
- ⚠️ **No Azure Key Vault integration**: Secrets appear to be injected as App Service environment variables (via publish profile deployment). No Key Vault reference syntax or managed identity is evident.
- ⚠️ **`GH_TOKEN` scope unknown**: The GitHub token used across all five tool workflows reads repo files, writes to output repos, and posts PR comments. Scope is not restricted in the workflow definitions. [TODO: Audit GH_TOKEN permissions — should use a fine-grained PAT with minimal repo scope]
- ⚠️ **CORS allows all methods and headers** (`allow_methods=["*"]`, `allow_headers=["*"]`) — overly permissive.
- ⚠️ **No rate limiting on API endpoints**: No throttling visible, leaving the LLM-backed endpoints open to abuse.
- ⚠️ **`API_KEY` falls back to empty string**: `os.getenv("API_KEY", "")` — if the environment variable is missing, the LLM client is initialised with no key, which may result in unauthenticated requests to the upstream provider.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | **HIGH** — LLM provider API key | Azure App Service env vars / `.env` file locally |
| `OPENAI_URL_BASE` | No (defaults to `https://openrouter.ai/api/v1`) | Low | Azure App Service env vars / `.env` |
| `OPENAI_MODEL` | No (defaults to `openai/gpt-oss-20b:free`) | Low | Azure App Service env vars / `.env` |
| `SHOW_TOOL_CALLS` | No (defaults to `true`) | Low | Azure App Service env vars / `.env` |
| `ANTHROPIC_API_KEY` | Yes (CI/CD tools 1–5) | **HIGH** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI/CD tools 1–5) | **HIGH** — GitHub PAT | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI/CD tools 1–5) | **HIGH** — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | **HIGH** — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | **HIGH** — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (defaults to `ai-delivery-outputs`) | Low | GitHub Actions env / hardcoded default |
| `OUTPUT_REPO_OWNER` | No (defaults to `GITHUB_REPOSITORY_OWNER`) | Low | GitHub Actions env |
| `NOTIFY_EMAIL` | No (defaults to `kylo.deng@capco.com`) | Medium — PII | GitHub Actions env (hardcoded in workflow) |
| `SENDER_EMAIL` | No (defaults to `kylo.deng@capco.com`) | Low | GitHub Actions env (hardcoded in workflow) |
| `VECTOR_STORE_TYPE` | [TODO: confirm variable name] | Low | Azure App Service env vars |
| `PINECONE_API_KEY` | Conditional (if Pinecone store used) | **HIGH** | [TODO: confirm where this is set] |
| `VOYAGE_API_KEY` | Conditional (if Voyage AI used) | **HIGH** | [TODO: confirm where this is set] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (`openrouter.ai/api/v1`) | External AI API | Runtime LLM for teacher/assessor agents | Configurable via `OPENAI_URL_BASE`; defaults to a free-tier model |
| **Anthropic Claude** (`claude-sonnet-4-6`) | External AI API | CI/CD tool workflows (code review, docs, UAT, auto-testing) | Billed per token; key in GitHub Secrets |
| **Voyage AI** | External embedding API | Generating embeddings during PDF ingestion | Implied by rate-limit logic (3 RPM free tier); [TODO: confirm embedding model name and provider] |
| **Pinecone** | External vector DB (optional) | Production-scale vector storage | Only used if `PineconeStore` is selected; [TODO: confirm if this is the production store] |
| **SendGrid** | Email SaaS | Notification emails from AI tool workflows | Key in GitHub Secrets |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool binding, RAG chain | Core runtime dependency |
| **FastAPI** | Python library | API server framework | |
| **Chainlit** | Python library / frontend framework | Chat UI | Deployed as `training-bot-frontend` |
| **pdfplumber** | Python library | PDF text extraction during ingestion | |
| **`ai-delivery-outputs`** | GitHub Repository (same owner) | Stores AI-generated documentation output | Must exist and be writable by `GH_TOKEN` |
| **Azure App Service** | Cloud PaaS | Hosting for both API and frontend | Deployed via publish profile |
| **GitHub Actions** | CI/CD platform | All automation workflows | |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repo
git clone https://github.com/kylodeng/Insurance-Training-Bot-main
cd Insurance-Training-Bot-main

# Install dependencies
uv sync
```

### Local Development

```bash
# 1. Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.

# 2. Ingest PDFs into the vector store (one-time setup)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info/

# 3. Start the API backend
uv run uvicorn api.main:app --reload --port 8000

# 4. Start the Chainlit frontend (in a separate terminal)
uv run chainlit run frontend/app.py --port 5173
# [TODO: confirm frontend entry point path]
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (GitHub Actions — Automatic)

Deployment is triggered automatically on push to `main` after tests pass:

```
git push origin main
```

The `deploy.yml` workflow:
1. Runs `uv run pytest tests/ -v`
2. Generates `requirements.txt` via `uv export --no-dev --format requirements-txt -o requirements.txt`
3. Deploys to `training-bot-api` using `AZURE_WEBAPP_PUBLISH_PROFILE_API`
4. Deploys to `training-bot-frontend` using `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Manual Deployment to Azure (if needed)

```bash
# Generate requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API via Azure CLI
az webapp deploy --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend via Azure CLI
az webapp deploy --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Re-ingesting the Knowledge Base

```bash
# Trigger via API endpoint (if exposed)
curl -X POST http://localhost:8000/ingest

# Or run directly
uv run python -m core.ingest --pdf-dir data/Insurance-product-info/ --verbose
```

---

## 8. Risks and TODOs

### Critical Security Risks

| Risk | Severity | Detail |
|---|---|---|
| TLS verification disabled | **CRITICAL** | `verify=False` on all outbound HTTP clients in `main.py` and `core/ingest.py`. All API keys transmitted without certificate validation. Must be removed before production. |
| No API authentication | **CRITICAL** | FastAPI has no auth middleware. The LLM endpoints, `/ingest`, and session APIs are publicly accessible to anyone who can reach the App Service URL. |
| Session data unencrypted on disk | **HIGH** | `sessions.json` stores full conversation histories in plaintext. |
| Static file serving unauthenticated | **HIGH**