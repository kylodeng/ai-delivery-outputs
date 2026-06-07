# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a RAG-powered AI training platform designed to help new insurance agents learn product knowledge and sales techniques for a Hong Kong insurance context (Sun Life products). It consists of a FastAPI backend that exposes a LangGraph agent (teacher mode and assessor/roleplay mode), a frontend application, and a vector-store-backed retrieval system ingested from real insurance PDF product brochures. Users interact via a chat interface; the agent retrieves relevant insurance product details in real time, cites sources, and can assess trainees after roleplay sessions. A suite of five AI-augmented GitHub Actions CI/CD tools (code review, tech docs, business docs, auto-testing, and UAT) are also embedded in the repository to support the development lifecycle.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend / LangGraph agent |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend chat UI |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded or managed vector DB | Azure / Local / Pinecone | Stores and retrieves insurance PDF chunks for RAG |
| GitHub Actions Runners | CI/CD compute (ubuntu-latest) | GitHub (Microsoft) | Test, build, and deploy workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated docs, review reports, test files, UAT packs |
| OpenRouter / LLM endpoint | External API | OpenRouter.ai | Serves LLM inference for the training agent (GPT-class models) |
| Anthropic Claude API | External API | Anthropic | Powers code review, tech docs, business docs, auto-testing, and UAT workflows |
| SendGrid | External Email API | Twilio/SendGrid | Sends notification emails for AI workflow outputs |
| Voyage AI (embedding) | External API | Voyage AI | Embeds PDF chunks during ingestion (referenced in `ingest.py` rate-limit comment) |
| PDF data files | Static file store | Azure App Service file system | Insurance brochures served over `/docs/` HTTP path |
| `sessions.json` | Local file | Azure App Service file system | Persists multi-turn conversation sessions across restarts |

---

## 3. Data Flow

1. **Ingestion (offline / admin-triggered):** An operator calls `POST /ingest` on the FastAPI backend. `core/ingest.py` walks the `data/Insurance-product-info/` directory, reads each PDF with `pdfplumber`, and passes pages to `core/annotator.py`, which calls the configured LLM (Anthropic via OpenRouter) to extract product metadata and page relevance annotations. Results are cached as `.annot.json` sidecar files alongside each PDF.
2. **Chunking:** Relevant pages are passed through `core/chunker.py`, which heuristically splits text into semantic units (headings, bullets, paragraphs) capped at `max_words` (default 280).
3. **Embedding:** `core/ingest.py::embed_chunks()` batches chunks and sends them to Voyage AI (or the configured embedding provider) to generate dense vectors, which are stored in the vector store (Chroma, FAISS, or Pinecone). The index is persisted to disk via `store.save()`.
4. **Application startup:** On FastAPI startup (`lifespan()`), `get_vector_store()` loads the persisted index and `make_rag_tools()` wraps it in LangChain tool objects. `load_sessions()` hydrates in-memory session state from `sessions.json`.
5. **User sends a chat message:** The frontend POSTs a message to the FastAPI backend (`/chat` or similar streaming endpoint). The backend resolves the session, selects teacher or roleplay mode, and invokes the appropriate LangGraph agent.
6. **Teacher agent — tool calls:** The LangGraph teacher agent calls one or more RAG tools (`search_product`, `search_all`, `compare_plans`, etc.). Each tool queries the vector store, retrieves ranked chunks, and appends source metadata to a per-request `contextvars` list (`_sources_ctx`).
7. **Teacher agent — LLM inference:** Retrieved chunks and the conversation history are sent to OpenRouter (GPT-class model) for response generation. The agent streams tokens back to the FastAPI handler.
8. **Streaming response:** FastAPI uses `StreamingResponse` to push SSE/token chunks to the frontend in real time. After streaming, collected source citations are sent as a final structured payload.
9. **Roleplay / assessment mode:** The user selects roleplay mode; the backend generates a random `CustomerProfile` (from `api/sessions.py`), instantiates a roleplay system prompt, and runs a customer-persona LLM. When the session ends, the assessor agent is invoked via `ainvoke`, uses the same RAG tools to verify factual claims, and returns a scored assessment JSON.
10. **Session persistence:** After each turn, session state (messages, profile, mode) is written back to `data/sessions.json`.
11. **CI/CD tools (parallel flow):** On PR or push to `main`, GitHub Actions workflows invoke Python scripts (`.github/scripts/tool1–5`), which call the Anthropic Claude API, write outputs to the `ai-delivery-outputs` repo via GitHub API, post PR comments, and send notification emails via SendGrid.

---

## 4. Security Posture

### What is secured

- **Secrets management:** All sensitive keys (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions encrypted secrets and injected as environment variables at runtime — not hardcoded in source.
- **CORS policy:** The FastAPI app restricts CORS to explicit origins (`localhost:5173`, `localhost:8000`) for local development. [TODO: Verify production CORS origins are restricted to the Azure App Service URL and not left as localhost-only or wildcard in production config.]
- **Session isolation:** Sessions are keyed by UUID; there is no evidence of session fixation vulnerabilities in the session management code.
- **Dependency management:** `uv` is used for reproducible dependency resolution, and `requirements.txt` is generated from the lockfile before deployment.

### What is NOT secured — gaps and risks

- ⚠️ **TLS/SSL verification disabled:** `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls in `api/main.py` and `core/ingest.py`. This disables certificate validation and exposes the system to man-in-the-middle attacks against the LLM provider endpoints. **This must be fixed before production use.**
- ⚠️ **No authentication or authorisation on the API:** There is no evidence of any auth middleware (API key gate, OAuth, JWT) on the FastAPI endpoints. Any user who can reach the App Service URL can invoke the agent, trigger ingestion, and access session data.
- ⚠️ **`sessions.json` stored on App Service local filesystem:** Azure App Service local disk is ephemeral and not replicated. Session data will be lost on restart, slot swap, or scale-out. There is no Azure Blob or database-backed session store.
- ⚠️ **PDF data files served unauthenticated over HTTP (`/docs/`):** All insurance product PDFs (including potentially proprietary Sun Life brochures) are mounted as a public static file endpoint with no access control.
- ⚠️ **`API_KEY` defaults to empty string:** `_API_KEY = os.getenv("API_KEY", "")` means if the env var is missing, requests to OpenRouter will be sent with an empty key — likely a silent failure or hitting a public/unauthenticated tier.
- ⚠️ **CORS allows all methods and headers:** `allow_methods=["*"]` and `allow_headers=["*"]` is broader than necessary.
- ⚠️ **No encryption at rest specified for the vector store:** The ChromaDB/FAISS index files are written to disk with no explicit encryption configuration. If the App Service file system or any local disk is compromised, all embedded document content is exposed.
- ⚠️ **`GH_TOKEN` secret has unknown scope:** The `GH_TOKEN` is used to write to `ai-delivery-outputs` repo and post PR comments. The required scope is not documented. If over-provisioned (e.g., full repo write across org), this is a significant blast-radius risk. [TODO: Document minimum required GitHub token scopes and enforce least-privilege.]
- ⚠️ **No WAF or network-level protection documented** in front of the Azure App Service instances.
- ⚠️ **No secrets rotation policy** is evident for any of the API keys.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter) | App Service env / `.env` file locally |
| `OPENAI_URL_BASE` | No | Low | App Service env / `.env` (defaults to `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | App Service env / `.env` (defaults to `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | App Service env / `.env` |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | 🔴 High — Anthropic API key | GitHub Actions secret |
| `GH_TOKEN` | Yes (CI tools) | 🔴 High — GitHub PAT | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | 🔴 High — SendGrid API key | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Actions secret |
| `OUTPUT_REPO` | No (CI tools) | Low | GitHub Actions env (defaults to `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI tools) | Low | GitHub Actions env (defaults to repo owner) |
| `NOTIFY_EMAIL` | No (CI tools) | Low | GitHub Actions env (hardcoded to `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI tools) | Low | GitHub Actions env (hardcoded to `noreply@ai-delivery.capco.com`) |

> ⚠️ **Note:** `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded directly in workflow YAML files (not secrets). While not sensitive, they reduce portability and should be parameterised.

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| OpenRouter (`https://openrouter.ai/api/v1`) | External API | LLM inference for training agent (teacher + roleplay + assessor) | Proxies multiple LLM providers; default model `openai/gpt-oss-20b:free` |
| Anthropic Claude API | External API | LLM for CI/CD AI tools (code review, docs, testing, UAT) | Model `claude-sonnet-4-6` hardcoded in `shared.py` |
| Voyage AI | External API | Text embedding for PDF chunks during ingestion | Rate-limit comments suggest free tier (3 RPM) is the baseline |
| Pinecone | External API (optional) | Managed vector store backend | Optional; `PineconeStore` implemented in `core/vector_store.py` |
| SendGrid | External API | Email notifications for CI tool outputs | Requires valid sending domain (`ai-delivery.capco.com`) |
| GitHub API (`https://api.github.com`) | External API | Reading source repos, posting PR comments, writing output repo files | Used by all 5 CI scripts via `shared.py` |
| `ai-delivery-outputs` (GitHub repo) | External repo | Stores all AI-generated documentation, test files, review reports | Must exist under the same GitHub org/owner |
| Azure App Service | Cloud platform | Hosting API and frontend | App names: `training-bot-api`, `training-bot-frontend` |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, streaming | Core agent framework |
| `pdfplumber` | Python library | PDF text extraction for ingestion | |
| ChromaDB / FAISS | Python library | Local vector store backends | |
| `uv` | Build tool | Python dependency management and lockfile | |
| `httpx` | Python library | HTTP client for LLM API calls | **SSL verification disabled — see security section** |

---

## 7. Deployment Instructions

### Prerequisites

- Azure App Services `training-bot-api` and `training-bot-frontend` must be pre-created in Azure.
- GitHub secrets must be configured (see Section 5).
- The `ai-delivery-outputs` repository must exist under the same GitHub owner.

### Automated deployment (via GitHub Actions)

```bash
# Deployment is triggered automatically on push to main branch.
# Ensure all required secrets are set in: Settings → Secrets and variables → Actions

git push origin main
# GitHub Actions will:
# 1. Run: uv run pytest tests/ -v
# 2. On success: deploy API to training-bot-api (Azure App Service)
# 3. On success: deploy Frontend to training-bot-frontend (Azure App Service)
```

### Manual local setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install uv (if not already installed)
curl -Lf https://astral.sh/uv/install.sh | sh

# 3. Install dependencies
uv sync

# 4. Configure environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env and set:
#   API_KEY=<your OpenRouter or LLM API key>
#   OPENAI_URL_BASE=https://openrouter.ai/api/v1
#   OPENAI_MODEL=<model name>

# 5. Ingest PDF documents into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 6. Start the FastAPI backend
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 7. Run tests
uv run pytest tests/ -v
```

### Generate and export requirements (for Azure deployment)

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

---

## 8. Risks and TODOs

### Critical risks

| Risk | Severity | Detail |
|---|---|---|
| SSL verification disabled | 🔴 Critical | `httpx.Client(verify=False)` in `api/main.py` and `core/ingest.py` — MITM vulnerability against all LLM API traffic |
| No API authentication | 🔴 Critical | FastAPI backend has no auth layer; any internet user reaching the App Service URL can use the bot and trigger ingestion |
| PDF files served publicly | 🔴 High | `/docs/` static mount serves all insurance PDFs without access control; proprietary Sun Life documents may be exposed |
| Session data on ephemeral disk | 🔴 High | `sessions.json` will be lost on App Service restart, scale-out, or slot swap |
| Empty API_KEY default | 🟠 High | `os.getenv("API_KEY", "")` silently allows deployment with no LLM key |

### Missing operational capabilities

- **No disaster recovery (DR):** Single-region Azure deployment with no documented failover, backup, or geo-redundancy strategy.
- **No monitoring or alerting:** No Application Insights, health check endpoints, or alerting configuration is present. [TODO: Add Azure Monitor / Application Insights to both App Services.]
- **No logging aggregation:** `logging.basicConfig(level=logging.INFO)` logs to stdout only; no structured logging sink (e.g., Azure Log Analytics) is configured.
- **No rate limiting:** The API has no rate limiting or throttling to prevent abuse or runaway LLM costs.
- **No vector store backup:** The Chroma/FAISS