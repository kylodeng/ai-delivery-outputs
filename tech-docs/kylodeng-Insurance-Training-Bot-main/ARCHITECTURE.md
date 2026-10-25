# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is an AI-powered learning platform designed to train new insurance sales agents employed by (or affiliated with) Sun Life Hong Kong. The system provides two core modes: a **Teacher Mode** where an LLM-backed agent answers product questions, explains insurance concepts, and runs interactive exercises using a Retrieval-Augmented Generation (RAG) pipeline over proprietary Sun Life product PDFs; and a **Roleplay/Assessment Mode** where the agent simulates a realistic Hong Kong customer persona and subsequently evaluates the trainee's sales conversation across multiple dimensions. The backend is a FastAPI application deployed on Azure App Service, paired with a separate frontend App Service, with PDF knowledge ingested into a local vector store (FAISS or Chroma) and served via LangGraph-orchestrated tool-calling agents backed by an OpenRouter-proxied LLM.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts FastAPI backend; serves `/chat`, `/ingest`, `/sessions` endpoints |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts frontend UI (Chainlit or Vite app) |
| Vector Store (FAISS/Chroma/Pinecone) | Local disk or managed service | Azure (local) / Pinecone (external) | Stores PDF embeddings for RAG retrieval |
| `data/sessions.json` | File-based session store | Azure App Service filesystem | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | Static file directory | Azure App Service filesystem | Houses raw PDFs and `.annot.json` sidecar annotation caches |
| `/docs` static mount | FastAPI StaticFiles | Azure (in-process) | Serves PDFs over HTTP so the UI can link to source documents |
| GitHub Actions runners | CI/CD compute (ubuntu-latest) | GitHub | Runs tests, generates `requirements.txt`, deploys to Azure |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Receives auto-generated code review, tech docs, business docs, test files, UAT packs |
| Anthropic Claude (claude-sonnet-4-6) | External LLM API | Anthropic | Powers AI delivery toolchain (code review, docs, testing, UAT workflows) |
| OpenRouter API | External LLM proxy | OpenRouter | Routes LLM calls for the training bot's teacher/assessor agents |
| SendGrid | External email API | Twilio/SendGrid | Sends email notifications from AI delivery workflows |
| Voyage AI (inferred) | External embedding API | Voyage AI | Generates embeddings for PDF chunks (referenced in ingest rate-limit comments) |

---

## 3. Data Flow

### 3.1 Knowledge Ingestion Flow

1. An operator places Sun Life product PDF files under `data/Insurance-product-info/`.
2. A `POST /ingest` request triggers `core/ingest.py::ingest_directory()`.
3. For each PDF, `core/annotator.py` calls the configured LLM (via OpenRouter/Anthropic) to extract document-level metadata (`product_name`, `doc_type`, `summary`) and per-page relevance annotations; results are cached to a `.annot.json` sidecar file beside the PDF.
4. `core/chunker.py` splits each relevant page into semantic units (headings, bullets, paragraphs) targeting ≤280 words per chunk.
5. Chunk dicts (including metadata: product name, page range, section title, file URL) are batched and sent to the embedding model (Voyage AI, inferred) via the configured `BaseVectorStore` implementation.
6. Embeddings are persisted to disk (FAISS index) or a managed store (Chroma/Pinecone) and the index is saved.

### 3.2 Teacher Mode (Live Chat) Flow

1. User sends a chat message from the frontend to `POST /chat` on the FastAPI backend.
2. `api/main.py` retrieves or creates a session from `sessions.json`; constructs a LangGraph teacher agent with `make_teacher_agent()`.
3. The agent (guided by `TEACHER_SYSTEM` prompt) decides which RAG tool to call: `search_product`, `search_all`, `compare_plans`, `lookup_exclusions`, `lookup_hospital_network`, `search_claim_procedure`, `list_products`, or `get_current_date`.
4. The selected tool queries the vector store, retrieves top-k chunks, deduplicates by document+page, assigns source IDs (`S1`, `S2`, …) via a request-scoped `contextvars.ContextVar`, and returns formatted text to the agent.
5. The LLM synthesises a response with inline citations (`[[S1]]`) referencing source documents.
6. The response is streamed back to the frontend via `StreamingResponse` using `astream_events`.
7. Source metadata (document name, page, file URL) is appended to the streamed response so the UI can render clickable PDF links.

### 3.3 Roleplay & Assessment Flow

1. User starts a roleplay session; `generate_profile()` randomly selects a synthetic Hong Kong customer persona (name, age, occupation, income, goals, personality).
2. A roleplay system prompt is constructed with the persona and sent to the LLM acting as the customer.
3. The trainee conducts a simulated sales conversation; all turns are stored in the session.
4. When the session ends, `make_assessor_agent()` is invoked with the full conversation transcript and the customer profile.
5. The assessor agent uses the same RAG tools to verify every factual claim made by the trainee against the knowledge base.
6. A structured assessment report (score, findings per dimension) is returned and displayed to the trainee.

### 3.4 CI/CD & AI Toolchain Flow

1. A push to `main` or a PR triggers GitHub Actions.
2. `deploy.yml` runs pytest, then deploys both `training-bot-api` and `training-bot-frontend` to Azure App Service using publish profiles stored as GitHub Secrets.
3. Concurrently (or on schedule), toolchain workflows (`tool1`–`tool5`) call Anthropic Claude via `shared.py`, read repo files via GitHub API, and write outputs (review comments, markdown docs, test files, UAT packs) to the `ai-delivery-outputs` GitHub repository.
4. SendGrid emails notify `kylo.deng@capco.com` of completed outputs.

---

## 4. Security Posture

### ✅ What Is Secured

- **CI/CD secrets** — Azure publish profiles, Anthropic API key, GitHub token, and SendGrid API key are stored as GitHub Actions secrets and not hardcoded in workflow files.
- **GitHub token scoping** — `GH_TOKEN` is used only for GitHub API calls (reading repo files, writing output repo, posting PR comments).
- **Session isolation** — Sessions are keyed by UUID; no cross-session data leakage in the data model.
- **Static file isolation** — Only the `data/` directory is mounted as a static endpoint (`/docs`); application code is not exposed.
- **LLM prompt injection mitigation** — System prompts are server-side constants; user messages are passed as `user` role only.

### ❌ Gaps and Missing Controls

- **TLS/SSL verification disabled** — `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py` and `core/ingest.py`. **This disables certificate validation for all outbound LLM API calls, making the application vulnerable to man-in-the-middle attacks.** This must be fixed before production use.
- **No API authentication on FastAPI endpoints** — There is no authentication middleware, API key check, or OAuth on `/chat`, `/ingest`, `/sessions`, or `/docs`. Anyone who can reach the App Service URL can query the bot, trigger ingestion, or access raw PDFs.
- **`sessions.json` is unencrypted on disk** — Conversation transcripts (which may contain customer profile simulations with financial details) are stored in plaintext on the App Service filesystem with no encryption at rest.
- **PDF files served unauthenticated** — The `/docs` static mount serves all PDFs publicly without any auth check. Proprietary Sun Life product documents could be downloaded by anyone with the URL.
- **No Azure Key Vault integration** — Application environment variables (API keys, model names) are presumably set as App Service application settings; no evidence of Key Vault-backed references.
- **No network-level isolation** — No evidence of Azure Virtual Network integration, private endpoints, or IP allowlisting for the App Service instances.
- **IAM: `GH_TOKEN` scope unknown** — [TODO: What permissions does the GH_TOKEN have? If it has `repo` write scope on all repos, this is overly broad. It should be scoped to the `ai-delivery-outputs` repo only.]
- **No CORS restriction in production** — CORS `allow_origins` includes `localhost` origins. These should be removed or environment-gated for production deployments.
- **No rate limiting** — No rate limiting on API endpoints; the `/ingest` endpoint in particular could be abused to trigger expensive LLM annotation calls.
- **Annotation LLM uses `verify=False`** — Same SSL bypass applies to the annotation LLM client in `core/ingest.py`.
- **No input sanitisation** — User chat messages are passed directly to the LLM with no length limits or content filtering enforced at the API layer.
- **`sessions.json` stored on ephemeral App Service filesystem** — Azure App Service instances may restart and lose the local filesystem; sessions would be lost unless persistent storage (Azure Files mount) is configured.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key (OpenRouter or Anthropic) | Azure App Service Application Settings / `.env` locally |
| `OPENAI_URL_BASE` | No (default: `https://openrouter.ai/api/v1`) | Low | Azure App Service Application Settings / `.env` |
| `OPENAI_MODEL` | No (default: `openai/gpt-oss-20b:free`) | Low | Azure App Service Application Settings / `.env` |
| `SHOW_TOOL_CALLS` | No (default: `true`) | Low | Azure App Service Application Settings / `.env` |
| `ANTHROPIC_API_KEY` | Yes (CI toolchain) | **High** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI toolchain) | **High** — GitHub PAT | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI toolchain) | **High** — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deployment) | **High** — Azure publish credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deployment) | **High** — Azure publish credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (default: `ai-delivery-outputs`) | Low | GitHub Actions workflow env |
| `OUTPUT_REPO_OWNER` | No (default: `github.repository_owner`) | Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (default: `kylo.deng@capco.com`) | Low | GitHub Actions workflow env |
| `SENDER_EMAIL` | No (default: `noreply@ai-delivery.capco.com`) | Low | GitHub Actions workflow env |
| `VECTOR_STORE_TYPE` | [TODO: Is this configurable?] | Low | [TODO: Where is the store backend selected?] |
| `VOYAGE_API_KEY` | [TODO: Required for embedding?] | **High** | [TODO: Not evidenced in source — confirm embedding provider and key injection] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (`https://openrouter.ai/api/v1`) | External LLM proxy API | Routes LLM inference calls for teacher/assessor agents | Default model: `openai/gpt-oss-20b:free` — free tier may have rate limits and lower quality |
| **Anthropic Claude** (`claude-sonnet-4-6`) | External LLM API | Powers all 5 CI/CD AI delivery tools (code review, docs, testing, UAT) | Billed per token; no spend cap configured |
| **Voyage AI** (inferred) | External embedding API | Generates vector embeddings for PDF chunks | Referenced in rate-limit comments (`3 RPM` free tier); API key not surfaced in env table |
| **SendGrid** | External email API | Sends completion notification emails from CI workflows | Requires verified sender domain |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool-calling loop, streaming | Core dependency; version pinned via `uv` |
| **FastAPI** | Python library | REST API framework for backend | |
| **pdfplumber** | Python library | PDF text extraction for ingestion pipeline | |
| **FAISS / Chroma / Pinecone** | Library / managed service | Vector similarity search | Backend selected via `get_vector_store()`; [TODO: which backend is active in production?] |
| **Azure App Service** | Cloud PaaS | Hosts API and frontend applications | Two separate App Service instances |
| **GitHub** (`api.github.com`) | External API | Repo file access, PR comments, output repo writes | Used by `shared.py` in CI toolchain |
| **`ai-delivery-outputs`** (repo) | External GitHub Repo | Receives all AI-generated documentation and test artifacts | Must exist and be writable by `GH_TOKEN` |
| **`uv`** | Python package manager | Dependency management and lockfile | Used in CI; `requirements.txt` exported for App Service deployment |
| **python-dotenv** | Python library | Local development `.env` file loading | Not used in production (env vars set via App Service settings) |

---

## 7. Deployment Instructions

### 7.1 Local Development

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Install dependencies
uv sync

# 4. Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env and set:
#   API_KEY=<your OpenRouter or Anthropic key>
#   OPENAI_URL_BASE=https://openrouter.ai/api/v1
#   OPENAI_MODEL=openai/gpt-oss-20b:free

# 5. Ingest PDF documents into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 6. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000

# 7. (Separate terminal) Start the frontend
# [TODO: frontend start command not evidenced — confirm if Chainlit or Vite]
# uv run chainlit run frontend/app.py   OR
# cd frontend && npm run dev
```

### 7.2 Run Tests

```bash
uv run pytest tests/ -v
```

### 7.3 Deploy to Azure (Manual)

```bash
# Generate requirements.txt from uv lockfile
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API (requires Azure CLI and publish profile)
az webapp deploy \
  --name training-bot-api \
  --resource-group <your-resource-group> \
  --src-path . \
  --type zip

# Deploy Frontend
az webapp deploy \
  --name training-bot-frontend \
  --resource-group <your-resource-group> \
  --src-path . \
  --type zip
```

### 7.4 Deploy via CI/CD (Recommended)

```bash
# Simply push to main branch — GitHub Actions handles the rest:
git push origin main

# The pipeline will:
# 1. Run: uv run pytest tests/ -v
# 2. Generate requirements.txt
# 3. Deploy API to training-bot-api (Azure App Service)
# 4. Deploy Frontend to training-bot-frontend (Azure App Service)
```

### 7.5 GitHub Actions Secrets Required

Configure the following secrets in **GitHub → Settings → Secrets and