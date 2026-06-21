# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a RAG-powered (Retrieval-Augmented Generation) AI training platform designed to help new insurance agents at a Hong Kong financial services firm (Capco/Sun Life context) learn insurance product knowledge and practice sales conversations. The system ingests Sun Life Hong Kong insurance product PDFs into a vector store, exposes them through a FastAPI backend via LangChain/LangGraph agents, and serves a chat-based frontend. Two agent modes are supported: a **Teacher mode** for interactive guided learning and an **Assessor mode** for post-roleplay performance scoring. A suite of five AI-powered CI/CD tooling workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) are layered on top of the repository using Claude (Anthropic) as the underlying AI for DevOps automation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving RAG queries, session management, and PDF ingestion |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) for agent trainees |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded or managed vector DB | Local / Azure / Pinecone [TODO: which store is used in production?] | Stores embedded PDF chunks for semantic similarity search |
| `sessions.json` | Local file (App Service filesystem) | Azure | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions Runners | Ephemeral CI/CD compute | GitHub (Azure-hosted) | Runs test, deploy, code review, docs, UAT, and auto-testing pipelines |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated outputs: code reviews, architecture docs, runbooks, test files |
| OpenRouter / OpenAI-compatible LLM endpoint | External API | Third-party (OpenRouter) | Primary LLM for chat agent inference (`openai/gpt-oss-20b:free` default) |
| Anthropic Claude API | External API | Anthropic | Powers all five CI/CD AI tooling workflows (code review, docs, UAT, etc.) |
| SendGrid | Email API | Twilio/SendGrid | Sends notification emails after AI tooling workflow runs |
| Voyage AI (implied) | Embedding API | Voyage AI | Generates vector embeddings for PDF chunks (referenced in ingest comments) |

---

## 3. Data Flow

### 3.1 Document Ingestion (one-time / on-demand)

1. Operator calls `POST /ingest` on the FastAPI backend (or runs `core/ingest.py` directly).
2. `ingest_directory()` walks `data/Insurance-product-info/` recursively, finding all `.pdf` files.
3. For each PDF, `load_or_create_annotations()` checks for a sidecar `.annot.json` cache file. If absent, it calls the configured LLM (OpenRouter/Anthropic via `ChatOpenAI`) to extract document-level metadata (`product_name`, `doc_type`, `summary`) and per-page relevance annotations.
4. The `.annot.json` is written to disk alongside the PDF. Irrelevant pages (cover pages, awards, disclaimers) are filtered out.
5. `extract_chunks_from_pdf()` uses `pdfplumber` to extract text, cleans it, and splits it into semantic units (headings, bullets, paragraphs) with a configurable `max_words` limit (~280 words/chunk).
6. Each chunk dict is enriched with metadata: `product_name`, `doc_type`, `page_start`, `page_end`, `section_title`, `file_url`, `chunk_id`.
7. `embed_chunks()` batches chunks (default 126/batch) and calls the embedding API (Voyage AI or equivalent). Embeddings are stored in the configured vector store (`ChromaStore`, `LocalFAISSStore`, or `PineconeStore`).
8. The vector store index is persisted to disk via `store.save()`.

### 3.2 Teacher Mode (Real-time Chat)

1. Frontend user opens a chat session. The frontend calls `POST /sessions` on the FastAPI backend, which creates a `Session` object (persisted to `sessions.json`).
2. User sends a message. Frontend calls the streaming chat endpoint on the API.
3. FastAPI constructs a LangGraph teacher agent using `make_teacher_agent()`, binding the eight RAG tools and the `TEACHER_SYSTEM` prompt.
4. The agent decides which tool to call. Tool calls hit `make_rag_tools()` which queries the vector store for top-k semantically similar chunks.
5. Retrieved chunks are returned to the agent with source IDs (`S1`, `S2`, …). The `_sources_ctx` context variable tracks sources per-request in an async-safe manner.
6. The agent synthesises a response, embedding inline citations (`[[S1]]`).
7. The response is streamed back to the frontend via `StreamingResponse` using `astream_events`.
8. Source metadata (document name, page range, file URL) is appended to the response so the frontend can render clickable PDF links (served from `/docs/` static mount).
9. The session message history is appended and persisted to `sessions.json`.

### 3.3 Roleplay / Assessment Mode

1. Frontend triggers roleplay mode. The backend calls `generate_profile()` which randomly selects a `CustomerProfile` from Hong Kong-contextualised name/occupation/income/goals pools.
2. The roleplay system prompt is constructed with the customer profile. A `ChatOpenAI` call to the `_ROLEPLAY_SYSTEM` prompt simulates the customer.
3. The trainee agent interacts with the simulated customer through the frontend.
4. When the session ends, the frontend signals assessment. The backend invokes `make_assessor_agent()` with the full conversation transcript and customer profile.
5. The assessor agent uses its own tool calls to verify factual claims made by the trainee against the vector store.
6. An assessment report is returned to the frontend (non-streamed, via `ainvoke`).

### 3.4 CI/CD AI Tooling (GitHub Actions)

1. A trigger fires (PR open, push to `main`, scheduled cron, version tag, release branch, or manual dispatch).
2. The relevant Python script (`.github/scripts/tool1–5_*.py`) is invoked in the GitHub Actions runner.
3. The script fetches repo files or PR diffs via the GitHub REST API (`GH_TOKEN`).
4. It calls Anthropic Claude API (`ANTHROPIC_API_KEY`) with a structured system prompt.
5. The result (JSON or Markdown) is written to the `ai-delivery-outputs` GitHub repo via the GitHub Contents API.
6. A notification email is sent via SendGrid (`SENDGRID_API_KEY`) to `kylo.deng@capco.com`.
7. For code review, a PR comment is also posted via the GitHub Issues API.

---

## 4. Security Posture

### ✅ What is secured

- **Secrets management**: All sensitive keys (`AZURE_WEBAPP_PUBLISH_PROFILE_API/FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `API_KEY`) are stored as GitHub Actions secrets, not hardcoded in source.
- **CI/CD gating**: Deployment jobs depend on the `test` job passing (`needs: test`), preventing broken code from deploying.
- **Deploy-on-main-only**: The deploy jobs are conditionally guarded by `github.ref == 'refs/heads/main' && github.event_name == 'push'`.
- **CORS restriction**: The API CORS middleware only allows known origins (`localhost:5173`, `localhost:8000`). [TODO: Are production frontend URLs added to the CORS allowlist?]
- **Secrets wrapped in `SecretStr`**: The `API_KEY` is wrapped in Pydantic's `SecretStr` before being passed to `ChatOpenAI`, preventing accidental logging.

### ❌ Security gaps and risks

- **TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py` and `core/ingest.py`. This disables SSL certificate verification for all outbound LLM API calls, **making the system vulnerable to man-in-the-middle attacks**. This is a critical security flaw for a production system handling insurance product data.
- **No authentication on API endpoints**: There is no API key, JWT, or OAuth middleware visible on the FastAPI app. Any caller with network access to `training-bot-api` can query the RAG system, start sessions, or trigger ingestion (`POST /ingest`). The ingest endpoint is particularly sensitive.
- **`sessions.json` not encrypted at rest**: Session data (conversation histories, customer profiles, trainee interactions) is stored as a plaintext JSON file on the App Service filesystem. If the App Service filesystem is compromised, all conversation data is exposed. **No encryption at rest is configured.**
- **PDF files served without authentication**: `app.mount("/docs", StaticFiles(...))` serves all PDFs and data files publicly over HTTP with no auth check. This exposes all insurance product documents to unauthenticated access.
- **`GH_TOKEN` scope unknown**: The `GH_TOKEN` is used to read source repos, write to `ai-delivery-outputs`, and post PR comments. The required scopes are not documented. [TODO: Is this a fine-grained PAT or a classic token? What scopes are granted?]
- **Output repo write access**: The CI/CD tooling writes arbitrary content to the `ai-delivery-outputs` repo. If `ANTHROPIC_API_KEY` or `GH_TOKEN` were compromised, an attacker could push malicious content to that repo.
- **No WAF or DDoS protection mentioned**: No Azure Front Door, Application Gateway, or WAF is configured in front of the App Services.
- **No rate limiting on API**: No rate limiting middleware is visible on the FastAPI app, leaving it open to abuse of the LLM-backed endpoints.
- **`API_KEY` defaults to empty string**: `_API_KEY = os.getenv("API_KEY", "")` — if the env var is missing in production, the LLM calls will fail silently or with an authentication error rather than a startup failure.
- **Insurance product data sensitivity**: The `data/` directory contains Sun Life product brochures and hospital lists. While these may be publicly available documents, serving them without auth and embedding them without data governance controls could be a compliance issue depending on Sun Life's licensing terms.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key (OpenRouter/Anthropic) | Azure App Service App Settings / `.env` file locally |
| `OPENAI_URL_BASE` | No | Low | Azure App Service App Settings / `.env`; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | Azure App Service App Settings / `.env`; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | Azure App Service App Settings / `.env`; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (CI/CD tools) | **High** — Anthropic API key | GitHub Actions secret |
| `GH_TOKEN` | Yes (CI/CD tools) | **High** — GitHub PAT with repo read/write | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD tools) | **High** — SendGrid email API key | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | **High** — Azure deploy credential | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | **High** — Azure deploy credential | GitHub Actions secret |
| `OUTPUT_REPO` | No | Low | GitHub Actions env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No | Low | GitHub Actions env; hardcoded to `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO: does this exist?] | Low | [TODO: how is the vector store backend selected in production?] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Azure App Service** | Cloud platform | Hosting API and frontend | Two separate App Service instances |
| **OpenRouter** (`openrouter.ai/api/v1`) | External LLM API | Chat inference for teacher, assessor, and roleplay agents | Default model is `openai/gpt-oss-20b:free` — free tier may have rate limits |
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External LLM API | All five CI/CD tooling workflows; also used as annotation LLM during ingestion | `claude-sonnet-4-6` is a non-standard model name — [TODO: verify this is a valid Anthropic model ID] |
| **Voyage AI** (implied) | External Embedding API | Generating vector embeddings for PDF chunks | Referenced in `embed_chunks` rate-limit comments; free tier default of 3 RPM |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool binding, message management | Core agent framework |
| **FastAPI** | Python library | REST API framework | Backend web framework |
| **pdfplumber** | Python library | PDF text extraction | Used in `core/chunker.py` |
| **Chroma / FAISS / Pinecone** | Vector database | Embedding storage and retrieval | Multiple backends supported; active backend depends on env config |
| **SendGrid** | External email API | Notification emails from CI/CD workflows | |
| **GitHub REST API** (`api.github.com`) | External API | Reading repo files, posting PR comments, writing output files | Used by all five CI/CD tooling scripts |
| **`ai-delivery-outputs`** | External GitHub repo (same owner) | Stores generated docs, test files, UAT packs, code reviews | Must exist under the same GitHub org/owner |
| **`uv`** (Astral) | Build tool | Python dependency management and `requirements.txt` export | Used in CI/CD deploy pipeline |
| **`dotenv`** | Python library | Local `.env` file loading | Used in `api/main.py` and ingestion scripts |
| **httpx** | Python library | Async HTTP client for LLM API calls | SSL verification is disabled — see security section |

---

## 7. Deployment Instructions

### Prerequisites
- Azure CLI authenticated with access to the target subscription
- App Services `training-bot-api` and `training-bot-frontend` already provisioned in Azure
- `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` GitHub secrets set
- `uv` installed locally (`pip install uv` or via `astral-sh/setup-uv`)
- `.env` file created locally with required variables (see section 5)

### Automated Deployment (via GitHub Actions)

```bash
# Deployment is triggered automatically on push to main:
git push origin main

# The CI/CD pipeline will:
# 1. Run: uv run pytest tests/ -v
# 2. On success, export requirements.txt: uv export --no-dev --format requirements-txt -o requirements.txt
# 3. Deploy API to Azure App Service 'training-bot-api'
# 4. Deploy Frontend to Azure App Service 'training-bot-frontend'
```

### Manual Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install uv
pip install uv

# 3. Install dependencies
uv sync

# 4. Create .env file
cp .env.example .env   # [TODO: verify .env.example exists]
# Edit .env with your API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# 5. Ingest PDFs into the vector store (run