# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train insurance sales agents working in the Hong Kong market. It ingests Sun Life Hong Kong insurance product PDFs (brochures, supplementary hospital lists, policy documents) into a vector store, then exposes two AI-powered interaction modes via a FastAPI backend: a **Teacher mode** (ongoing streamed chat where an AI coach teaches product knowledge, sales techniques, and discovery questioning) and a **Roleplay/Assessment mode** (where the trainee practices with a simulated Hong Kong customer profile, followed by a structured accuracy assessment). The system is deployed as two separate Azure App Service instances — one for the API and one for the frontend — with a CI/CD pipeline managed through GitHub Actions.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Microsoft Azure | Hosts the FastAPI backend including RAG pipeline, LangGraph agents, and session management |
| `training-bot-frontend` | Azure App Service (Web App) | Microsoft Azure | Hosts the frontend UI (Chainlit or Vite-based; exact framework [TODO: confirm frontend technology]) |
| Vector Store (local) | Local FAISS / ChromaDB / Pinecone | Azure (local disk) or external | Stores embedded PDF chunks for semantic retrieval; backend selection driven by env var |
| GitHub Actions Runners | Hosted CI/CD runners | GitHub (Microsoft Azure) | Execute test, build, and deploy pipelines |
| OpenRouter / OpenAI API | External LLM inference | OpenRouter.ai (default) | Powers LangGraph agents (teacher and assessor) via `ChatOpenAI`-compatible interface |
| Anthropic Claude API | External LLM inference | Anthropic | Used by GitHub Actions AI-tooling workflows (code review, docs, testing, UAT) |
| SendGrid | External email service | Twilio/SendGrid | Sends notification emails from AI delivery workflow runs |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated artefacts (code review reports, architecture docs, test files, UAT packs) |
| `data/sessions.json` | JSON flat-file | Azure App Service local disk | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | PDF + `.annot.json` sidecar files | Azure App Service local disk | Source insurance documents and their LLM-generated annotation cache |

---

## 3. Data Flow

### 3.1 Document Ingestion (one-time / on-demand)

1. An operator triggers `POST /ingest` on the FastAPI backend (or runs `core/ingest.py` directly).
2. `ingest_directory()` recursively walks `data/Insurance-product-info/` and finds all `.pdf` files.
3. For each PDF, `load_or_create_annotations()` checks for a sidecar `.annot.json` cache. If absent, it sends the first 3 pages to the configured LLM (OpenRouter/Anthropic) to extract `product_name`, `doc_type`, `linked_product`, and `summary`, plus per-page `header`/`relevant`/`skip_reason` annotations. Results are written to `<pdf>.annot.json`.
4. `extract_chunks_from_pdf()` uses `pdfplumber` to extract text page-by-page. Pages marked `relevant: false` in annotations are skipped. Remaining text is split into semantic units (headings, bullets, paragraphs) via heuristic rules in `core/chunker.py`, then further chunked to ≤280 words.
5. Each chunk dict carries metadata: `product_name`, `doc_type`, `document_name`, `page_start`, `page_end`, `section_title`, `file_url`, `chunk_id`.
6. `embed_chunks()` sends batches of chunks to the configured embedding model (Voyage AI free-tier implied by default `batch_delay` comment) and writes vectors to the vector store (`FAISS`, `Chroma`, or `Pinecone` depending on env config). The index is saved to disk.

### 3.2 Teacher Mode (live training session)

1. The user (trainee agent) sends a chat message through the frontend.
2. The frontend makes an HTTP request to the FastAPI backend (streaming endpoint).
3. `make_teacher_agent()` constructs a LangGraph ReAct agent with the `TEACHER_SYSTEM` prompt and 8 RAG tools attached.
4. `reset_sources()` initialises a fresh per-request source-tracking list via a `contextvar`.
5. The agent calls `call_claude` → LLM decides which tool(s) to invoke (e.g., `search_product`, `compare_plans`, `lookup_exclusions`).
6. Each tool call hits the vector store with a semantic similarity search; results are returned as text blocks prefixed with source IDs (`S1`, `S2`, …).
7. The agent streams the response token-by-token back to the frontend via `StreamingResponse`. Tool-call events are conditionally included based on `SHOW_TOOL_CALLS`.
8. Source citations are collected in the `contextvar` list and appended to the response after streaming completes.
9. The conversation is appended to the in-memory `Session` object and persisted to `data/sessions.json`.

### 3.3 Roleplay & Assessment Mode

1. The trainee requests a new roleplay session; the backend calls `generate_profile()` which randomly selects name, occupation, income, financial goals, risk tolerance, existing coverage, and personality from HK-context lists.
2. A `CustomerProfile` is stored in the `Session` object.
3. The trainee chats with the simulated customer; the FastAPI backend uses a `_ROLEPLAY_SYSTEM` prompt (injected with the profile) and the same LLM to respond in character — no RAG tools are used in roleplay turns.
4. When the session ends, the trainee triggers assessment. `make_assessor_agent()` constructs a one-shot LangGraph agent with the full conversation history and customer profile.
5. The assessor uses the same 8 RAG tools to **verify** every factual claim the trainee made against the knowledge base.
6. A structured assessment JSON is returned (scores across five dimensions) and rendered to the frontend.

### 3.4 GitHub Actions AI Tooling (meta-pipeline)

1. Events (PR open, push to main, schedule, tags, manual dispatch) trigger one of five workflow YAML files.
2. The workflow installs `anthropic` and `requests`, then runs the corresponding Python script.
3. The script calls the Anthropic Claude API (`claude-sonnet-4-6`) directly via the `anthropic` SDK.
4. Output artefacts (JSON reports, markdown docs, CSV test packs) are committed to the `ai-delivery-outputs` GitHub repository via the GitHub REST API using `GH_TOKEN`.
5. For code review workflows, a comment is posted to the originating PR.
6. A notification email is sent via SendGrid to `kylo.deng@capco.com`.

---

## 4. Security Posture

### ✅ What is secured

- API keys and publish profiles are stored as **GitHub Actions secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`) and not hardcoded in source.
- Deployment to Azure only triggers on pushes to `main` **after** the test job passes (`needs: test`).
- PR-triggered workflows do not deploy (guarded by `github.event_name == 'push'`).
- Sessions are scoped to in-process memory with file-based persistence; no external database is exposed.
- The `SecretStr` type from Pydantic is used to wrap the API key in `ChatOpenAI` instantiation.

### ❌ Gaps and concerns — explicit callouts

| Gap | Detail |
|---|---|
| **TLS/SSL verification disabled** | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `api/main.py` and `core/ingest.py`. This disables certificate verification for **all** outbound LLM API calls, making the application vulnerable to man-in-the-middle attacks. **Must be fixed before production.** |
| **No API authentication on FastAPI endpoints** | There is no authentication middleware (no API key, OAuth, JWT) on the FastAPI backend. Any caller who can reach `training-bot-api.azurewebsites.net` can invoke all endpoints including `POST /ingest`. |
| **`sessions.json` stored on App Service local disk** | Local disk on Azure App Service is ephemeral across slot swaps and restarts. Sessions will be lost. No encryption at rest for session data. |
| **Vector store persisted to local disk** | Same ephemeral disk risk. If the App Service instance is recycled, the entire index is lost and ingestion must be re-run. No Azure Blob Storage or managed database backend is configured. |
| **No secrets in the application environment documented** | `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL` etc. are read from environment via `os.getenv`. There is no evidence these are configured in Azure Key Vault or App Service application settings — they may be absent in production. [TODO: Confirm how app env vars are set in Azure App Service] |
| **CORS allows localhost origins in production build** | `allow_origins` includes `http://localhost:5173` and `http://127.0.0.1:5173`. These should be removed or restricted to the production frontend domain in deployment. |
| **PDF files served unauthenticated** | `app.mount("/docs", StaticFiles(directory=str(_DATA_DIR)))` exposes all PDFs and annotation JSON files publicly without any auth check. |
| **`GH_TOKEN` scope unknown** | The `GH_TOKEN` used in the AI tooling workflows writes to a separate `ai-delivery-outputs` repo and posts PR comments. If this token has broad `repo` scope it is overly permissive. **[TODO: Restrict GH_TOKEN to minimum required scopes: `contents:write` on output repo, `pull-requests:write` on source repo]** |
| **No rate limiting** | No rate limiting or throttling is implemented on any FastAPI endpoint. |
| **No input sanitisation** | User chat messages are passed directly into LLM prompts with no sanitisation, creating prompt injection risk. |
| **Annotation LLM uses `verify=False`** | The `_build_ingest_llm()` function in `core/ingest.py` also sets `verify=False`. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM provider API key | Azure App Service Application Settings [TODO: confirm] |
| `OPENAI_URL_BASE` | No | Low | Azure App Service Application Settings; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | Azure App Service Application Settings; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | Azure App Service Application Settings; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (CI only) | **High** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI only) | **High** — GitHub PAT with write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI only) | **High** — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (CI only) | **High** — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (CI only) | **High** — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI only) | Low | GitHub Actions workflow env; hardcoded as `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI only) | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI only) | Low | GitHub Actions workflow env; hardcoded as `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI only) | Low | GitHub Actions workflow env; hardcoded as `noreply@ai-delivery.capco.com` |
| `VOYAGE_API_KEY` | [TODO: required?] | **High** | [TODO: Not seen in source — confirm if Voyage AI embedding requires an explicit key or if it is bundled] |
| `VECTOR_STORE_BACKEND` | [TODO: required?] | Low | [TODO: Not seen — confirm how Chroma/FAISS/Pinecone backend is selected] |
| `PINECONE_API_KEY` | Conditional | **High** | [TODO: Required if Pinecone backend selected — not found in source] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter.ai** | External API | Default LLM inference endpoint (`https://openrouter.ai/api/v1`) | Can be swapped for any OpenAI-compatible endpoint via `OPENAI_URL_BASE` |
| **Anthropic Claude API** | External API | LLM used by GitHub Actions AI-tooling scripts (`claude-sonnet-4-6`) | Direct `anthropic` SDK calls, not via OpenRouter |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool binding, ReAct loop | Core agentic framework |
| **LangChain-OpenAI** | Python library | `ChatOpenAI` client used for all LLM calls in the app | Also used for annotation LLM in `core/ingest.py` |
| **pdfplumber** | Python library | PDF text extraction | Used in `core/chunker.py` |
| **FastAPI** | Python library | HTTP API framework | Main application server |
| **Pydantic** | Python library | Data validation, `SecretStr` wrapping | Integrated with FastAPI |
| **httpx** | Python library | Async HTTP client for LLM API calls | SSL verification **disabled** |
| **python-dotenv** | Python library | Load `.env` file in development | Not used in production (env vars set at platform level) |
| **FAISS / ChromaDB / Pinecone** | Vector store | Semantic similarity search over embedded PDF chunks | Backend selectable via `core/vector_store.py` |
| **Voyage AI** (implied) | External API | Embedding model for document chunks | Implied by `batch_delay` comments referencing Voyage AI free-tier limits |
| **SendGrid** | External API | Email notifications from CI workflows | Via `SENDGRID_API_KEY` |
| **`ai-delivery-outputs`** (GitHub repo) | Sibling GitHub repository | Storage for AI-generated artefacts from CI tools | Must exist under same owner; `GH_TOKEN` must have write access |
| **Azure App Service** | Cloud platform | Hosting for API and frontend | Two named apps: `training-bot-api` and `training-bot-frontend` |
| **GitHub Actions** | CI/CD platform | All build, test, deploy, and AI-tooling automation | |
| **Sun Life HK PDFs** | Static data | Insurance product knowledge base | Stored in `data/Insurance-product-info/`; must be present at deploy time |

---

## 7. Deployment Instructions

### 7.1 Prerequisites

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.13 required for application
python --version  # should be 3.13+

# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

### 7.2 Local Development Setup

```bash
# Install all dependencies (including dev/test)
uv sync

# Create a .env file with required variables
cat > .env <<EOF
API_KEY=<your-openrouter-or-openai-compatible-api-key>
OPENAI_URL_BASE=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-oss-20b:free
SHOW_TOOL_CALLS=true
EOF

# Run document ing