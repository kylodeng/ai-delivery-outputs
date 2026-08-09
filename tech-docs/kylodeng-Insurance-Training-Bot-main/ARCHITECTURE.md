# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a RAG (Retrieval-Augmented Generation) web application designed to train insurance sales agents at Sun Life Hong Kong. It provides two interaction modes: a **Teacher Mode** where a LangGraph-powered AI coach guides trainees through insurance concepts, product knowledge, and sales techniques using a vector-store-backed knowledge base of insurance PDFs; and a **Roleplay/Assessment Mode** where the agent simulates a randomised Hong Kong customer profile for the trainee to practice against, followed by an AI-generated accuracy assessment. The system is deployed as two separate Azure App Service instances (API backend and frontend), continuously delivered via GitHub Actions, and uses an OpenRouter-proxied LLM (defaulting to a free GPT model) alongside optional Anthropic Claude for CI/CD tooling.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts FastAPI backend — RAG tools, LangGraph agents, session management, PDF serving |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Local file storage or managed service | Azure (local) / Pinecone (optional) | Stores embedded insurance document chunks for semantic retrieval |
| `data/sessions.json` | JSON flat file | Azure App Service local disk | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions Runners | Ephemeral CI/CD compute | GitHub (ubuntu-latest) | Run tests, generate requirements.txt, deploy to Azure |
| `ai-delivery-outputs` repo | GitHub repository | GitHub | Stores AI-generated code reviews, tech docs, test reports, business docs, UAT packs |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic (external) | Powers CI/CD automation tools (code review, doc gen, test gen, UAT) |
| OpenRouter API | LLM proxy/gateway | OpenRouter (external) | Routes LLM calls for the training bot agents at runtime |
| SendGrid | Email delivery service | Twilio/SendGrid (external) | Sends notification emails from CI/CD workflows |
| `Health Mutual Group Limited (HMG)` | Third-party hospital network data provider | External | Provides cashless hospital network data embedded in knowledge base |

---

## 3. Data Flow

### Ingestion Path (one-time / on-demand)
1. Operator places Sun Life insurance PDFs under `data/Insurance-product-info/`.
2. `POST /ingest` is called on the API; `core/ingest.py` walks the directory recursively.
3. For each PDF, `core/annotator.py` calls the configured LLM (via `OPENAI_URL_BASE`) to extract document-level metadata (product name, doc type, summary) and page-level relevance flags. Results are cached to sidecar `.annot.json` files alongside each PDF to avoid repeat LLM calls.
4. `core/chunker.py` splits relevant pages into semantic units (headings, bullets, paragraphs) capped at `max_words` per chunk.
5. `core/ingest.py::embed_chunks` sends chunks to the vector store (`ChromaDB`, `FAISS`, or `Pinecone` depending on env config) in batches; the index is saved to disk.

### Teacher Mode (runtime chat)
1. User sends a message via the frontend UI to `POST /chat` (or equivalent streaming endpoint) on `training-bot-api`.
2. FastAPI loads the session from `data/sessions.json` and reconstructs conversation history.
3. `api/agent.py::make_teacher_agent` builds a LangGraph agent with eight RAG tools (defined in `api/rag_tools.py`) bound to the LLM via OpenRouter.
4. The agent streams events via `astream_events`; each tool call queries the vector store with a similarity search, returning ranked document chunks with metadata (product name, page numbers, file URL).
5. Source IDs (`S1`, `S2`, …) are tracked per-request using a `contextvars.ContextVar` to ensure async-safe deduplication across parallel tool calls.
6. The API streams the LLM token output back to the frontend as Server-Sent Events; source citation metadata is appended at the end of the response.
7. Session state (messages, mode, profile) is updated and persisted back to `data/sessions.json`.
8. PDF source documents are served directly from the API via `GET /docs/<path>` (StaticFiles mounted from `data/`).

### Roleplay/Assessment Mode (runtime)
1. Frontend calls `POST /sessions` to create a new roleplay session; `api/sessions.py::generate_profile` randomly assembles a `CustomerProfile` from HK-contextualised name/occupation/income/goal pools.
2. The roleplay LLM (same OpenRouter endpoint, temperature 0.6) is prompted with the customer profile to act as a skeptical prospect.
3. The trainee converses with the simulated customer; all turns are stored in the session.
4. When the session ends, `make_assessor_agent` is invoked (one-shot `ainvoke`) — it re-reads the full conversation and uses the same eight RAG tools to verify factual claims the trainee made.
5. An assessment report is returned to the frontend.

### CI/CD Tooling Path
1. On PR/push/schedule, GitHub Actions triggers one of five workflow tools (`tool1`–`tool5`).
2. The workflow script fetches repo files or PR diffs via the GitHub REST API.
3. Calls `claude-sonnet-4-6` via the Anthropic SDK with a structured prompt.
4. Writes output (markdown reports, JSON, CSV test packs) to the `ai-delivery-outputs` GitHub repo via the GitHub Contents API.
5. Optionally posts a PR comment and/or sends a SendGrid notification email.

---

## 4. Security Posture

### What IS secured
- **Secrets management**: All sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_*`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions secrets, not hardcoded in source.
- **HTTPS in transit**: Azure App Service enforces HTTPS by default for public endpoints.
- **CI/CD gate**: Deployments only trigger after the `test` job passes; `deploy-*` jobs require `github.ref == 'refs/heads/main'`.
- **Source restriction in CORS**: CORS is restricted to specific localhost origins for development; [TODO: confirm production CORS origin list is updated before go-live — currently only localhost origins are whitelisted, which will block the deployed frontend from calling the API].

### What is NOT secured / Gaps

- **⚠️ TLS certificate verification disabled**: `api/main.py` and `core/ingest.py` both create `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)`. This disables SSL certificate verification for ALL outbound LLM calls (OpenRouter, Anthropic), making the system vulnerable to man-in-the-middle attacks on LLM API traffic. **This must be fixed before production use.**
- **⚠️ No API authentication on FastAPI endpoints**: There is no authentication middleware visible on `api/main.py`. Any caller with network access to `training-bot-api` can call `/ingest`, `/chat`, and all session endpoints without credentials. [TODO: add API key or Azure AD authentication].
- **⚠️ `data/sessions.json` unencrypted flat file**: Conversation histories (which may contain PII from customer profiles) are stored as a plaintext JSON file on the App Service local disk. Azure App Service local disk is not encrypted at the application level, and the file is not protected by access controls within the app.
- **⚠️ Static PDF serving with no access control**: `app.mount("/docs", StaticFiles(...))` serves all insurance PDFs and annotation files from the `data/` directory with no authentication. Any user who knows or guesses a URL can download raw product documents.
- **⚠️ `GH_TOKEN` scope unknown**: The `GH_TOKEN` secret used by CI/CD tools has write access to `ai-delivery-outputs` and read access to source repos. [TODO: verify the token scope is least-privilege — it should not have `admin` or `delete_repo` permissions].
- **⚠️ No input validation on session/chat endpoints**: User-supplied inputs (session IDs, messages, profile fields) appear to flow directly into LLM prompts with no sanitisation. This creates potential prompt injection risk.
- **⚠️ Encryption at rest not confirmed**: No Azure Key Vault, CMK (customer-managed keys), or explicit disk encryption configuration is present in the IaC. Azure default encryption at rest is enabled by default for App Service, but this relies on Microsoft-managed keys. [TODO: confirm data classification and whether CMK is required].
- **⚠️ No rate limiting**: No rate limiting middleware is applied to the FastAPI app, exposing it to abuse or cost amplification attacks on LLM API calls.
- **`NOTIFY_EMAIL` / `SENDER_EMAIL` hardcoded in workflow YAML**: `kylo.deng@capco.com` is hardcoded in four workflow files rather than being a repo variable. Acceptable for now but reduces flexibility and exposes an email address in public workflow logs.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key (OpenRouter or Anthropic) | App Service Application Settings / `.env` |
| `OPENAI_URL_BASE` | No (default: `https://openrouter.ai/api/v1`) | Low | App Service Application Settings / `.env` |
| `OPENAI_MODEL` | No (default: `openai/gpt-oss-20b:free`) | Low | App Service Application Settings / `.env` |
| `SHOW_TOOL_CALLS` | No (default: `true`) | Low | App Service Application Settings / `.env` |
| `ANTHROPIC_API_KEY` | Yes (CI/CD tools) | **High** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI/CD tools) | **High** — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI/CD tools) | **High** — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deployment) | **High** — Azure publish credentials | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deployment) | **High** — Azure publish credentials | GitHub Actions Secret |
| `OUTPUT_REPO` | No (default: `ai-delivery-outputs`) | Low | GitHub Actions workflow env / hardcoded default |
| `OUTPUT_REPO_OWNER` | No (default: `github.repository_owner`) | Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (default: `kylo.deng@capco.com`) | Low-Medium (PII — email address) | Hardcoded in workflow YAML |
| `SENDER_EMAIL` | No (default: `noreply@ai-delivery.capco.com`) | Low | Hardcoded in workflow YAML |
| `VECTOR_STORE_TYPE` | [TODO: confirm how ChromaDB vs FAISS vs Pinecone is selected] | Low | Unknown — not visible in provided files |
| `PINECONE_API_KEY` | Conditional (if Pinecone store used) | **High** | [TODO: confirm where set] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (`https://openrouter.ai/api/v1`) | External LLM API gateway | Routes runtime LLM calls for training agents | Default model `openai/gpt-oss-20b:free` — free tier, rate limits apply |
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External LLM API | Powers CI/CD automation tools (code review, doc gen, UAT) | Also referenced in `core/ingest.py` as annotation LLM base URL — may be used at ingest time too |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool binding, streaming | `langchain`, `langchain-openai`, `langchain-core` |
| **FastAPI** | Python framework | REST API and streaming backend | |
| **Chainlit** (implied) | UI framework | Chat interface frontend | [TODO: confirm — Chainlit is referenced in CORS config comments but no Chainlit source files were provided] |
| **pdfplumber** | Python library | PDF text extraction for ingestion pipeline | |
| **ChromaDB / FAISS / Pinecone** | Vector store | Semantic search over embedded insurance document chunks | Selection via `core/vector_store.py::get_vector_store()` — mechanism not shown in provided files |
| **Voyage AI** (referenced in comments) | External embedding API | Generates text embeddings for document chunks | Free tier is 3 RPM — `batch_delay` comment references this; [TODO: confirm if still used or replaced by OpenRouter embeddings] |
| **SendGrid** | External email API | CI/CD notification emails | Used by `shared.py` |
| **Health Mutual Group Limited (HMG)** | External data provider | Hospital network data for cashless arrangement | Embedded in static PDF data, not a live API call |
| **Sun Life Hong Kong** | Content source | Insurance product PDFs (Generations II, hospital lists, etc.) | Static data ingested at setup time |
| **`ai-delivery-outputs`** (sibling repo) | GitHub repository | Stores all AI-generated artefacts from CI/CD tools | Must exist and be accessible with `GH_TOKEN` |
| **`uv`** (astral-sh) | Python package manager | Dependency management and virtual environment | Used in all GitHub Actions workflows |

---

## 7. Deployment Instructions

### Prerequisites
- Azure App Services `training-bot-api` and `training-bot-frontend` must exist and be configured.
- GitHub repository secrets must be set: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.
- The `ai-delivery-outputs` repository must exist under the same GitHub owner.

### Automated Deployment (recommended)
```bash
# Push to main branch — triggers test + deploy pipeline automatically
git push origin main
```
The GitHub Actions workflow (`.github/workflows/deploy.yml`) will:
1. Run `pytest tests/ -v` on Python 3.13
2. On success, export `requirements.txt` via `uv export --no-dev --format requirements-txt -o requirements.txt`
3. Deploy to `training-bot-api` and `training-bot-frontend` Azure App Services in parallel

### Manual / Local Setup
```bash
# 1. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Copy and configure environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with your API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# 4. Ingest insurance PDFs into the vector store
# Place PDFs under data/Insurance-product-info/
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 5. Start the API server
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 6. Start the frontend (if running separately)
# [TODO: confirm frontend start command — Vite dev server or Chainlit?]
# Likely: cd frontend && npm run dev   OR   uv run chainlit run app.py
```

### Running Tests
```bash
uv run pytest tests/ -v
```

### Triggering a Manual Knowledge Base Rebuild
```bash
# Call the ingest endpoint on the deployed API
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```
> ⚠️ **No authentication is required on this endpoint** — see Security Posture section.

---

## 8. Risks and TODOs

### Critical Risks

| Risk |