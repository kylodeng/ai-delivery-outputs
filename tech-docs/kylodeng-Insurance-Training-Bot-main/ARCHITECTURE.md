# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a RAG-powered (Retrieval-Augmented Generation) AI application designed to train insurance sales agents in a Hong Kong context. It consists of a FastAPI backend and a separate frontend, both deployed as Azure App Services. The backend ingests Sun Life Hong Kong insurance product PDFs into a vector store (Chroma, FAISS, or Pinecone), exposing a suite of LangChain tools to two LangGraph agents: a **Teacher agent** that conducts interactive training conversations (product knowledge, discovery questions, scenario practice) and an **Assessor agent** that evaluates completed roleplay sessions against verified product facts. The system routes LLM calls through OpenRouter (or a configurable OpenAI-compatible base URL) and uses Anthropic Claude (via a separate Anthropic API key) exclusively for the CI/CD AI tooling workflows (code review, documentation generation, test generation, UAT facilitation).

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving agent, RAG, session, and ingest endpoints |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (Chroma / FAISS / Pinecone) | Embedded or managed service | Local / Pinecone (cloud) | Stores embedded PDF chunks for RAG retrieval |
| GitHub Actions Runners | CI/CD compute (ubuntu-latest) | GitHub | Runs tests, builds, deploys, and AI tooling workflows |
| OpenRouter API | External LLM gateway | Third-party (openrouter.ai) | Routes LLM inference calls for the training agents |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers CI/CD AI tools (code review, docs, tests, UAT) |
| SendGrid | Transactional email service | Twilio/SendGrid | Sends notification emails after each AI tooling workflow |
| `ai-delivery-outputs` GitHub Repo | GitHub Repository | GitHub | Stores generated docs, test files, and audit outputs from AI tooling workflows |
| `data/sessions.json` | JSON flat-file | Azure App Service filesystem | Persists multi-turn conversation sessions across restarts |
| `data/Insurance-product-info/` | Static files (PDFs + `.annot.json`) | Azure App Service filesystem | Source product documents and their LLM-generated annotation cache |

---

## 3. Data Flow

### Training / Chat Flow

1. A user interacts with the **Frontend** (Chainlit UI or Vite SPA) via HTTP/HTTPS.
2. The frontend sends a chat message or session command to the **FastAPI backend** (`training-bot-api`) over its REST/streaming API.
3. The backend looks up the active **Session** (loaded from `data/sessions.json`) to retrieve conversation history, mode (`teacher` or `roleplay`), and customer profile.
4. For **teacher mode**, the request is routed to the **Teacher LangGraph agent**. The agent decides which RAG tool to invoke (e.g. `search_product`, `compare_plans`, `lookup_exclusions`).
5. The RAG tool queries the **Vector Store** (Chroma/FAISS/Pinecone) using an embedding of the query. Matching document chunks (with metadata: product name, page, section, file URL) are returned.
6. Source references are accumulated in a per-request `contextvars.ContextVar` (async-safe) and returned alongside the streamed response.
7. The agent formulates a response using the **LLM** (OpenRouter → configured model), streaming tokens back to the FastAPI endpoint via `astream_events`.
8. FastAPI streams the response as Server-Sent Events (SSE) or `StreamingResponse` to the frontend.
9. For **roleplay mode**, the customer persona (randomly generated from HK-context profiles in `sessions.py`) is injected as a system prompt. The LLM plays the customer; the agent does not use RAG tools during roleplay.
10. When a roleplay session ends, the **Assessor agent** is invoked (`ainvoke`, non-streaming). It uses the same RAG tools to fact-check every product claim the trainee made, then returns a structured assessment.
11. Session state (messages, title, mode, profile) is persisted back to `data/sessions.json`.

### Ingestion Flow

1. An operator sends `POST /ingest` to the FastAPI backend (or runs `core/ingest.py` directly).
2. The ingestion pipeline walks `data/Insurance-product-info/` recursively for PDF files.
3. For each PDF, `core/annotator.py` calls the configured LLM to produce a `.annot.json` sidecar (document metadata + per-page relevance flags). If the sidecar already exists, it is loaded from cache.
4. `core/chunker.py` splits relevant pages into semantic chunks (headings, bullets, paragraphs) up to `max_words` (default 280).
5. Chunks are embedded in batches via the configured embedding model and upserted into the Vector Store (`store.add_documents()`).
6. The index is saved (`store.save()`).

### CI/CD AI Tooling Flow

1. GitHub Actions triggers a workflow (PR, push to `main`, schedule, tag, or `workflow_dispatch`).
2. The workflow script calls `shared.py` → `call_claude()` → **Anthropic Claude API** with a structured prompt.
3. Claude returns a JSON or Markdown response.
4. The output is written to the `ai-delivery-outputs` GitHub repository via the **GitHub API** (`write_output_file()`).
5. For PR-based tools (Tool 1, Tool 4), a comment is posted on the PR via the GitHub API.
6. **SendGrid** sends a notification email to `kylo.deng@capco.com` with a summary and link.
7. An audit entry is written (to `ai-delivery-outputs` or as a workflow artifact).

---

## 4. Security Posture

### Secured

- **CI/CD secrets** (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions Secrets and never committed to source.
- **API keys injected via environment variables** at runtime (`API_KEY`, `OPENAI_URL_BASE`, etc.) — not hardcoded in source.
- **Session data** scoped per session ID with no apparent cross-session data leakage in the session management code.
- **CORS policy** restricts allowed origins to `localhost:5173`, `localhost:8000`, and `127.0.0.1` equivalents — appropriate for development.

### Not Secured / Gaps

- ⚠️ **TLS verification is explicitly disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls in `api/main.py` and `core/ingest.py`. This disables certificate validation and exposes all LLM traffic to man-in-the-middle attacks. **This must be remediated before production use.**
- ⚠️ **No authentication or authorisation** on the FastAPI backend endpoints. Any client that can reach `training-bot-api` can invoke `/ingest`, read sessions, or query the agent. There is no API key, JWT, OAuth, or IP allowlist enforced at the application level.
- ⚠️ **`data/sessions.json` is stored on the App Service local filesystem**, which is ephemeral on Azure App Service (redeployments wipe it). It is also not encrypted at rest beyond Azure's default disk encryption.
- ⚠️ **CORS in production**: The CORS middleware only lists localhost origins. The production frontend origin (`training-bot-frontend.azurewebsites.net`) is **not** in the `allow_origins` list. This will likely cause CORS failures in production unless configured via environment variable.
- ⚠️ **No rate limiting** on any API endpoint — the `/ingest` endpoint in particular could be abused to trigger expensive LLM annotation calls.
- ⚠️ **Encryption at rest for the vector store** is not explicitly configured. For FAISS/Chroma stored locally on App Service disk, data-at-rest encryption depends entirely on Azure's platform-level disk encryption (which is on by default, but no application-layer encryption is applied).
- ⚠️ **`GH_TOKEN` scope is unknown** — if this token has `repo` write access, it is used in CI scripts that call external APIs. The blast radius of a compromised token could include writing to `ai-delivery-outputs` and commenting on PRs. [TODO: scope `GH_TOKEN` to minimum required permissions (read source repo, write to output repo only).]
- ⚠️ **`API_KEY` default is empty string** (`os.getenv("API_KEY", "")`). If the environment variable is not set, the LLM client initialises with no key — this may silently fail or send unauthenticated requests.
- ⚠️ **No WAF or DDoS protection** configured at the Azure App Service level (no mention of Azure Front Door or Application Gateway).
- ⚠️ **PDFs served as static files** via `app.mount("/docs", StaticFiles(...))` — all ingested insurance documents are publicly accessible over HTTP with no auth gating.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM provider API key (OpenRouter or Anthropic) | App Service environment / `.env` file locally |
| `OPENAI_URL_BASE` | No | Low | App Service environment / `.env`; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | App Service environment / `.env`; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | App Service environment / `.env`; defaults to `"true"` |
| `ANTHROPIC_API_KEY` | Yes (CI tools only) | **High** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI tools only) | **High** — GitHub PAT | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools only) | **High** — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | **High** — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | **High** — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI tools) | Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI tools) | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI tools) | Low | GitHub Actions workflow env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI tools) | Low | GitHub Actions workflow env; hardcoded to `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | No | Low | [TODO: confirm which env var selects Chroma vs FAISS vs Pinecone] |
| `PINECONE_API_KEY` | Conditional | **High** — if Pinecone store is used | [TODO: confirm where this is set] |
| `VOYAGE_API_KEY` | Conditional | **High** — if Voyage AI embeddings are used | [TODO: confirm where this is set] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (`openrouter.ai/api/v1`) | External LLM API | Routes inference for teacher/assessor agents and annotation | Configurable via `OPENAI_URL_BASE`; free-tier model used by default |
| **Anthropic Claude** (`claude-sonnet-4-6`) | External LLM API | Powers all 5 CI/CD AI tooling workflows | Used only in GitHub Actions, not in the runtime app |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool definitions, message history | Core agent framework |
| **LangChain-OpenAI** | Python library | `ChatOpenAI` wrapper used for both OpenRouter and Anthropic-compatible calls | |
| **Chroma / FAISS / Pinecone** | Vector store | Stores and retrieves embedded PDF chunks | Selection determined at runtime via `core/vector_store.py` |
| **Voyage AI** | External embedding API | Document/query embedding (referenced in ingest batch delay comment) | [TODO: confirm if Voyage or OpenAI embeddings are used] |
| **pdfplumber** | Python library | Extracts text from insurance PDFs during ingestion | |
| **FastAPI** | Python framework | Backend HTTP server | |
| **Chainlit** (inferred) | Python/JS framework | Frontend chat UI | Referenced in CORS origins and `api/main.py` comments |
| **Vite** (inferred) | JS build tool | Alternative/secondary frontend | Referenced in CORS origins |
| **SendGrid** | External email API | Notification emails from CI/CD tooling | |
| **GitHub API** (`api.github.com`) | External API | Reading source repo files, posting PR comments, writing to output repo | Used by all 5 CI/CD tooling scripts |
| **`ai-delivery-outputs`** | GitHub Repository | Stores all AI-generated documents and audit logs | Must exist under the same GitHub org/owner |
| **`uv`** (astral-sh) | Python package manager | Dependency management and `requirements.txt` generation | Used in all CI workflows |
| **httpx** | Python library | HTTP client for LLM calls | TLS verification disabled — see Security Posture |
| **python-dotenv** | Python library | Loads `.env` file for local development | |

---

## 7. Deployment Instructions

### Prerequisites

- Azure CLI authenticated with access to the target subscription
- Two Azure App Services pre-created: `training-bot-api` and `training-bot-frontend`
- Publish profiles downloaded from Azure Portal and stored as GitHub Secrets (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`)
- Required GitHub Secrets configured: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- App Service environment variables set: `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`

### Automated Deployment (CI/CD)

Deployment is triggered automatically on every push to `main` after tests pass:

```bash
git push origin main
# GitHub Actions will:
# 1. Run: uv run pytest tests/ -v
# 2. On success, deploy API to training-bot-api
# 3. On success, deploy Frontend to training-bot-frontend
```

### Manual Local Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Install dependencies
uv sync

# 4. Create .env file
cat > .env <<EOF
API_KEY=<your-openrouter-or-anthropic-api-key>
OPENAI_URL_BASE=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-oss-20b:free
SHOW_TOOL_CALLS=true
EOF

# 5. Ingest insurance product PDFs
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 6. Start the API server
uv run uvicorn api.main:app --reload --port 8000

# 7. Run tests
uv run pytest tests/ -v
```

### Generating requirements.txt (for Azure deployment)

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Manual Ingest via API (after