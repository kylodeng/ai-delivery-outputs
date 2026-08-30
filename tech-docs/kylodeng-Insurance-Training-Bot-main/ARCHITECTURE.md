# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a full-stack AI-powered training platform designed to help new insurance agents (initially targeting the Hong Kong market) develop product knowledge and sales skills. It provides two interaction modes: a **Teacher mode** where a LangGraph agent coaches agents on insurance concepts using a Retrieval-Augmented Generation (RAG) pipeline over a curated corpus of Sun Life Hong Kong product PDFs, and a **Roleplay mode** where the system simulates realistic customer personas for practice conversations. An independent **Assessor agent** evaluates completed roleplays across multiple performance dimensions. The backend is a FastAPI application deployed to Azure App Service; a separate frontend application (technology not fully visible from source) is also deployed to Azure App Service. Five AI-assisted GitHub Actions workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) augment the development lifecycle using Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving chat, RAG, session management, and ingest endpoints |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based — see TODO below) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Storage — embedded or managed | Azure / Pinecone (conditional) | Persists document embeddings for RAG retrieval |
| `sessions.json` | File on App Service filesystem | Azure | Persists multi-turn conversation sessions across server restarts |
| `data/` directory | Static file storage on App Service filesystem | Azure | Stores raw PDFs and annotation sidecar `.annot.json` files; served at `/docs` endpoint |
| GitHub Actions Runners | CI/CD compute (ubuntu-latest) | GitHub | Runs tests, builds, and all five AI delivery tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Receives auto-generated docs, test files, and UAT packs from AI tools |
| Anthropic Claude API (`claude-sonnet-4-6`) | Managed AI API | Anthropic | Powers code review, tech docs, business docs, auto-testing, UAT, and document annotation |
| OpenRouter API (or compatible) | LLM Gateway API | OpenRouter / configurable | Routes LLM calls for teacher agent, assessor agent, and roleplay persona (model: `openai/gpt-oss-20b:free` by default) |
| Voyage AI (inferred) | Embedding API | Voyage AI | Embeds document chunks for vector store ingestion |
| SendGrid | Email API | Twilio SendGrid | Sends notification emails after each AI delivery tool run |

---

## 3. Data Flow

### 3a. Document Ingestion (One-time / on-demand)

1. An operator calls `POST /ingest` on the FastAPI backend (or runs `core/ingest.py` directly).
2. `ingest_directory()` walks the `data/Insurance-product-info/` directory recursively, finding all `.pdf` files.
3. For each PDF, `load_or_create_annotations()` checks for an existing `.annot.json` sidecar file. If absent, it calls the configured LLM (Anthropic/OpenRouter) to classify the document and each page (`annotate_document`, `annotate_page`). The result is cached to disk.
4. `extract_chunks_from_pdf()` reads the PDF with `pdfplumber`, applies heading/bullet heuristics, and splits pages into semantic units within the configured `max_words` limit.
5. `embed_chunks()` sends batches of chunks to the embedding API (Voyage AI inferred) and stores the resulting vectors into the configured vector store (ChromaDB, FAISS, or Pinecone).
6. The vector store is saved to disk (or remote if Pinecone).

### 3b. Teacher Mode — Chat Request

1. Frontend sends a chat message (HTTP POST or WebSocket) to `training-bot-api`.
2. FastAPI retrieves the session from `sessions.json` and reconstructs conversation history.
3. The teacher LangGraph agent is invoked; `reset_sources()` initialises a per-request source list via `contextvars`.
4. The agent selects from eight RAG tools (`search_product`, `search_all`, `compare_plans`, `lookup_exclusions`, `lookup_hospital_network`, `search_claim_procedure`, `list_products`, `get_current_date`).
5. Each tool call queries the vector store, retrieves top-k chunks, and appends unique source entries (deduped by document + page) to the per-request source list.
6. Tool results (with `[[Sn]]` citation markers) are injected back into the agent context.
7. The agent streams a response back via `StreamingResponse`; citations and source metadata are appended to the streamed payload.
8. The updated session (with new messages) is written back to `sessions.json`.
9. PDFs referenced by citations are accessible to the frontend at `/docs/<relative-path>`.

### 3c. Roleplay Mode

1. Frontend requests a new session with `mode=roleplay`.
2. `generate_profile()` randomly selects attributes from Hong Kong persona pools (`sessions.py`) and constructs a `CustomerProfile`.
3. The roleplay system prompt is populated with the profile and streamed to the OpenRouter LLM (`_ROLEPLAY_SYSTEM`).
4. The agent simulates the customer; the trainee agent (human) responds via the frontend.
5. At session end, the Assessor agent is invoked via `ainvoke` with the full conversation transcript and `CustomerProfile`. It calls the same RAG tools to verify factual claims the trainee made.
6. An assessment report is returned to the frontend.

### 3d. GitHub Actions AI Tools

1. Trigger event (push, PR, schedule, tag, or manual dispatch) fires one of the five workflow YAML files.
2. The relevant Python script is executed on an `ubuntu-latest` runner, reading repo files via the GitHub API (`shared.py:get_repo_files`).
3. The script calls `claude-sonnet-4-6` via the Anthropic API with a structured system prompt.
4. Output (JSON or Markdown) is written to the `ai-delivery-outputs` GitHub repository via `shared.py:write_output_file`.
5. For PRs, a comment is posted back via `shared.py:post_pr_comment`.
6. A notification email is sent via SendGrid to `kylo.deng@capco.com`.

---

## 4. Security Posture

### ✅ Secured

- **CI/CD Secrets**: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub Actions secrets and not hardcoded in source.
- **LLM API Key handling**: `API_KEY` is wrapped in Pydantic's `SecretStr` before being passed to `ChatOpenAI`, preventing accidental logging of the raw value.
- **Branch protection on CI**: Deployment jobs only run on `push` to `main` after the test job passes.
- **Source citation scoping**: Per-request source lists use `contextvars` (async-safe), preventing cross-request data leakage of citations.

### ❌ Not Secured / Gaps

- **TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py`. This disables SSL/TLS certificate verification for all LLM API calls, exposing the application to MITM attacks. **This must be fixed before production.**
- **No authentication on the API**: There is no auth middleware visible on the FastAPI app. Any caller with network access to `training-bot-api` can query sessions, trigger ingestion, and read all documents. [TODO: Is Azure App Service access restricted by IP, AAD, or Easy Auth?]
- **Sessions stored in plaintext on filesystem**: `sessions.json` on the App Service filesystem contains full conversation transcripts including customer profiles. There is no encryption at rest at the application layer (relies entirely on Azure storage encryption, which may or may not be enabled).
- **Static file exposure**: The `/docs` mount serves the entire `data/` directory over unauthenticated HTTP, including all raw PDFs (potentially proprietary Sun Life documents).
- **CORS allows localhost origins in production**: `CORSMiddleware` explicitly allows `http://localhost:5173` and `http://127.0.0.1:5173`. These should be removed from the production configuration.
- **No rate limiting**: No rate limiting is applied to any endpoint, leaving the LLM-backed endpoints exposed to abuse and unexpected cost overruns.
- **AI tool workflows expose secrets as env vars**: In the five AI tool workflows, `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are set as top-level `env:` on the job, meaning they are accessible to all steps including third-party actions. They should be scoped to only the steps that need them.
- **`GH_TOKEN` scope unknown**: [TODO: What permissions does the `GH_TOKEN` secret have? If it has write access across the organisation it is overly broad.]
- **No WAF or DDoS protection**: No Azure Front Door, Application Gateway, or equivalent is visible in the IaC.
- **Vector store encryption**: If using local FAISS, embeddings are stored in plaintext files on the App Service filesystem. No encryption at the application layer.
- **Annotation LLM `verify=False`**: The ingest pipeline also disables TLS verification for LLM calls.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key | App Service Application Settings / `.env` |
| `OPENAI_URL_BASE` | No | 🟡 Medium — endpoint URL | App Service Application Settings / `.env` (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | 🟢 Low | App Service Application Settings / `.env` (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | 🟢 Low | App Service Application Settings / `.env` (default: `true`) |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | 🔴 High | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI tools) | 🔴 High — GitHub PAT | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure credentials | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure credentials | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI tools) | 🟢 Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI tools) | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (CI tools) | 🟡 Medium — PII | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI tools) | 🟢 Low | GitHub Actions env |
| `TEST_MODE` | No (tool4) | 🟢 Low | GitHub Actions env |

> ⚠️ `NOTIFY_EMAIL` (`kylo.deng@capco.com`) is hardcoded in all five workflow YAML files. This is a personal email address committed to the repository and should be moved to a secret or variable.

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Azure App Service** | Cloud Platform | Hosts API and frontend web apps | Two separate App Service instances |
| **Anthropic API** (`claude-sonnet-4-6`) | External AI API | Powers all five CI AI tools; also used for document annotation during ingestion | Requires paid API key |
| **OpenRouter API** (or compatible OpenAI-compatible endpoint) | External AI API / LLM Gateway | Powers teacher agent, assessor agent, and roleplay persona at runtime | Default model is `openai/gpt-oss-20b:free`; base URL configurable |
| **Voyage AI** (inferred) | External Embedding API | Generates embeddings for document chunks | [TODO: Confirm embedding provider — not explicitly named in visible source] |
| **ChromaDB / FAISS / Pinecone** | Vector Store | Stores and retrieves document embeddings | Backend selected via `get_vector_store()` — [TODO: which backend is used in production?] |
| **SendGrid** | Email API | Sends notification emails after AI tool runs | Requires `SENDGRID_API_KEY` |
| **GitHub API** (`api.github.com`) | Version Control API | Used by AI tools to read repo files, post PR comments, write output files | Requires `GH_TOKEN` |
| **`ai-delivery-outputs`** | GitHub Repository (same org) | Receives generated docs, test files, UAT packs | Must exist and be writable by `GH_TOKEN` |
| **Sun Life Hong Kong PDFs** | Static data / proprietary documents | Source knowledge base for the RAG system | Stored in `data/Insurance-product-info/`; redistribution rights [TODO: confirm licensing] |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool calling, message management | Version pinned via `uv` lockfile |
| **FastAPI** | Python library | REST API framework | |
| **pdfplumber** | Python library | PDF text extraction for ingestion | |
| **httpx** | Python library | Async HTTP client for LLM API calls | TLS verification currently disabled |
| **pydantic** | Python library | Data validation, `SecretStr` wrapping | |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13 installed
- [`uv`](https://github.com/astral-sh/uv) installed (`pip install uv` or via `astral-sh/setup-uv`)
- Azure CLI authenticated (`az login`)
- Azure App Services `training-bot-api` and `training-bot-frontend` already provisioned
- `.env` file created at repo root with all required variables

### Local Development

```bash
# Clone the repo
git clone https://github.com/kylodeng/Insurance-Training-Bot-main
cd Insurance-Training-Bot-main

# Install dependencies
uv sync

# Copy and fill in environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Ingest PDFs into the vector store (first time only)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info

# Start the API server
uv run uvicorn api.main:app --reload --port 8000
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (CI/CD — Automatic)

Deployment is fully automated via GitHub Actions on push to `main`:

```
# Simply push to main — CI will:
# 1. Run tests (pytest)
# 2. Export requirements.txt via uv
# 3. Deploy API to Azure App Service 'training-bot-api'
# 4. Deploy Frontend to Azure App Service 'training-bot-frontend'

git push origin main
```

### Manual Deployment (Azure CLI fallback)

```bash
# Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API
az webapp deploy \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend
az webapp deploy \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Vector Store Ingestion (Production)

```bash
# Trigger via API endpoint (once App Service is running)
curl -X POST https://training-bot-api.azurewebsites.net/ingest