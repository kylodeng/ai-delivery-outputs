# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is an AI-powered sales training platform designed to help new insurance agents (specifically in the Hong Kong market) master product knowledge and sales techniques. The system provides two modes: a **Teacher mode** for interactive coaching and Q&A about insurance products, and a **Roleplay/Assessment mode** where the agent practices conversations with a simulated customer and receives a scored performance review. The backend is a FastAPI application backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life Hong Kong insurance product PDFs into a vector store, enabling LLM agents (via LangGraph) to answer product-specific questions with cited sources. A separate frontend application provides the user interface. Both components are deployed to Azure App Service via GitHub Actions CI/CD, with supporting AI automation workflows for code review, documentation generation, test generation, and UAT facilitation — all powered by Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend / RAG API |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded or managed vector DB | Azure (local to App Service) or Pinecone (external) | Stores embedded insurance document chunks for RAG retrieval |
| GitHub Actions Runners | CI/CD compute (`ubuntu-latest`) | GitHub | Run tests, build, and deploy on push to `main` |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated docs (code reviews, arch docs, runbooks, test files, UAT packs) |
| OpenRouter API (or compatible endpoint) | External LLM API | Third-party (OpenRouter / Anthropic) | Serves the LLM for teacher/roleplay agents (`ChatOpenAI` with configurable base URL) |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers all five AI delivery workflow tools (code review, tech docs, business docs, test gen, UAT) |
| SendGrid | Transactional Email API | Twilio/SendGrid | Sends email notifications on workflow completions |
| PDF Data Files | Static files on App Service | Azure | Insurance product PDFs served at `/docs` endpoint |

> [TODO: Confirm which vector store backend is active in production — ChromaDB (local disk), LocalFAISSStore, or PineconeStore. Local disk storage on App Service is ephemeral and will be lost on restart/redeploy.]

---

## 3. Data Flow

### User Chat (Teacher Mode)

1. **User** sends a chat message via the frontend UI to the FastAPI backend (`POST /chat` or equivalent streaming endpoint).
2. **FastAPI** retrieves or creates a `Session` object (loaded from `data/sessions.json`) containing conversation history.
3. **LangGraph Teacher Agent** is invoked; it decides which RAG tool to call based on the query (e.g., `search_product`, `compare_plans`, `lookup_exclusions`).
4. **RAG Tool** queries the **Vector Store** with the user's query; the store returns the top-K matching chunks with metadata (product name, page number, file URL).
5. Tool results are formatted with source IDs (`S1`, `S2`, …) and appended to the agent's context.
6. **LangGraph** calls the **OpenRouter LLM** (or configured endpoint) with the full prompt including retrieved chunks.
7. The LLM streams tokens back through LangGraph → FastAPI → **StreamingResponse** to the frontend.
8. The frontend renders the streamed response with inline citation markers; source panel is populated using the `/docs/` static file URLs.
9. The conversation turn is appended to the session and persisted to `data/sessions.json`.

### Roleplay / Assessment Mode

1. **Frontend** requests a new roleplay session; FastAPI calls `generate_profile()` to create a randomised `CustomerProfile`.
2. The **Roleplay System Prompt** is constructed with the customer profile; a `SystemMessage` is prepended to the session history.
3. The user (trainee agent) sends messages; the **ChatOpenAI LLM** responds in-character as the customer, streamed as above.
4. When the session ends, the frontend triggers assessment mode.
5. **LangGraph Assessor Agent** receives the full conversation + customer profile; it uses the same RAG tools to verify factual claims made by the trainee.
6. An assessment JSON is returned and rendered in the UI as a scored performance report.

### Document Ingestion Pipeline

1. **Operator** runs `POST /ingest` or the ingestion CLI (`core/ingest.py`).
2. `ingest_directory()` walks `data/Insurance-product-info/` recursively for PDFs.
3. For each PDF, `load_or_create_annotations()` checks for a sidecar `.annot.json` cache file. If absent, it calls the **LLM** to annotate document-level metadata and per-page relevance.
4. Relevant pages are extracted via `pdfplumber`, cleaned, split into semantic chunks by `extract_chunks_from_pdf()`.
5. Chunks are embedded in batches via the configured embedding model and upserted into the **Vector Store**.
6. The vector store index is persisted to disk (`store.save()`).

### CI/CD and AI Tooling Workflows

1. Developer pushes code or opens a PR targeting `main`.
2. **GitHub Actions** (`deploy.yml`) runs `pytest` tests via `uv run pytest`.
3. On success and merge to `main`, `uv export` generates `requirements.txt`; `azure/webapps-deploy@v3` pushes both API and frontend to their respective Azure App Service instances using publish profiles.
4. Parallel AI workflows run: **Tool 1** (code review) posts a Claude-generated review comment on the PR; **Tool 2** (tech docs) writes `README.md`, `ARCHITECTURE.md`, and `RUNBOOK.md` to the `ai-delivery-outputs` repo; **Tool 3** (business docs) generates a Solution Overview on version tags; **Tool 4** (auto-testing) generates test files; **Tool 5** (UAT) generates UAT test packs on `release/*` branch creation.
5. All AI tool outputs are committed to `ai-delivery-outputs` and notification emails are sent via **SendGrid**.

---

## 4. Security Posture

### ✅ What Is Secured

- **Secrets management**: All sensitive credentials (API keys, publish profiles) are stored as GitHub Actions secrets and injected as environment variables — never hardcoded in source.
- **CI gate**: Deployment only proceeds after the `test` job passes; deploy jobs have `needs: test`.
- **Branch protection (partial)**: Deploy workflows only trigger on `refs/heads/main` push, not on PR branches.
- **CORS restriction**: The FastAPI backend restricts CORS to specific known origins (`localhost:5173`, `localhost:8000`).
- **Pydantic SecretStr**: The OpenRouter/LLM `API_KEY` is wrapped in `pydantic.SecretStr` to prevent accidental logging.

### ❌ Gaps and Missing Controls

- **SSL/TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls. This disables TLS certificate verification, making the backend vulnerable to man-in-the-middle attacks on LLM API traffic. **This must be fixed before production use.**
- **No authentication on API endpoints**: There is no middleware or dependency enforcing authentication (API key, OAuth, JWT) on FastAPI routes. Any client that can reach the App Service URL can invoke the chat, ingest, and session endpoints.
- **Sessions stored as plaintext JSON**: `data/sessions.json` stores full conversation histories (including customer profile data) in unencrypted plaintext on the App Service filesystem.
- **PDF data served as unauthenticated static files**: Insurance product documents are mounted at `/docs` with no access control (`StaticFiles(directory=...)`). Anyone who knows the URL can download the raw PDFs.
- **Ephemeral local storage**: Azure App Service local disk is not persistent across deployments/restarts. The vector store index and `sessions.json` will be wiped on each deployment, requiring re-ingestion.
- **No encryption at rest**: Vector store index files and session data are stored on unmanaged App Service local disk with no explicit encryption configuration.
- **Overly broad `allow_methods=["*"]` and `allow_headers=["*"]` in CORS**: Combined with wildcard methods/headers, this could enable unexpected cross-origin interactions.
- **GH_TOKEN scope unknown**: `GH_TOKEN` is used to write to the `ai-delivery-outputs` repo. The required scope is not documented; if it uses a PAT with broad repo access, this is overly permissive. [TODO: Confirm GH_TOKEN is scoped to only the `ai-delivery-outputs` repo with `contents: write` only.]
- **No WAF or network ingress control**: No Azure Front Door, Application Gateway, or Virtual Network integration is configured; the App Services are presumably publicly exposed.
- **No rate limiting**: No rate limiting is applied to the chat API endpoints, leaving them open to abuse.
- **Annotation LLM `verify=False`**: The ingestion pipeline's `_build_ingest_llm()` also uses `httpx.Client(verify=False)`.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter or Anthropic) | Azure App Service config / `.env` (local) |
| `OPENAI_URL_BASE` | No | Low | Azure App Service config / `.env`; default: `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | Azure App Service config / `.env`; default: `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | Azure App Service config / `.env`; default: `true` |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | 🔴 High | GitHub Actions secret |
| `GH_TOKEN` | Yes (CI workflows) | 🔴 High — GitHub PAT | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI workflows) | 🔴 High | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions secret |
| `OUTPUT_REPO` | No (CI workflows) | Low | GitHub Actions env; default: `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI workflows) | Low | GitHub Actions env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI workflows) | Low | GitHub Actions env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI workflows) | Low | GitHub Actions env; hardcoded to `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO: confirm] | Low | [TODO: not visible in provided files — how is Chroma/FAISS/Pinecone selected?] |
| `PINECONE_API_KEY` | Conditional | 🔴 High | [TODO: required if PineconeStore is used — not found in any workflow] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (or compatible OpenAI-API endpoint) | External API | LLM inference for teacher/roleplay/assessor agents | Configurable via `OPENAI_URL_BASE`; default is OpenRouter free tier (`gpt-oss-20b:free`) |
| **Anthropic Claude** (`claude-sonnet-4-6`) | External API | All five CI/CD AI delivery tools (code review, docs, tests, UAT) | Called from GitHub Actions runners; billed to `ANTHROPIC_API_KEY` |
| **SendGrid** | External API | Email notifications for AI workflow outputs | Requires verified sender domain `ai-delivery.capco.com` |
| **Azure App Service** | Cloud PaaS | Hosting for API and frontend | Two named apps: `training-bot-api`, `training-bot-frontend` |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool calling, message types | Exact version pinned by `uv` lockfile (not provided) |
| **LangChain OpenAI** (`langchain_openai`) | Python library | `ChatOpenAI` wrapper for LLM calls | |
| **FastAPI** | Python library | HTTP API framework | |
| **pdfplumber** | Python library | PDF text extraction during ingestion | |
| **ChromaDB / FAISS / Pinecone** | Vector DB library | Vector store backends | Active backend not confirmed in provided files |
| **Voyage AI** (implied) | External Embedding API | Document chunk embedding (referenced in `embed_chunks` docstring mentioning Voyage AI free-tier RPM limits) | [TODO: confirm embedding provider and whether API key is needed] |
| **httpx** | Python library | HTTP client for LLM calls (with `verify=False` — see security gap) | |
| **`ai-delivery-outputs`** | GitHub Repository (same owner) | Output sink for all AI-generated artefacts | Must exist before workflows run; `GH_TOKEN` must have write access |
| **`uv`** | Python package manager | Dependency management and venv; used in all CI jobs | Version pinned by `astral-sh/setup-uv@v3` |
| **python-dotenv** | Python library | Local `.env` loading | Not loaded in production (App Service env vars used directly) |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

### Local Development Setup

```bash
# Install all dependencies (including dev)
uv sync

# Copy and populate environment variables
cp .env.example .env  # [TODO: confirm .env.example exists]
# Edit .env with your API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Ingest insurance PDFs into the vector store (run once)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info

# Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000

# Start the frontend (in a separate terminal)
# [TODO: confirm frontend start command — Chainlit or Vite?]
# If Chainlit:
uv run chainlit run frontend/app.py --port 5173
# If Vite/Node:
cd frontend && npm install && npm run dev
```

### Running Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (Azure App Service via GitHub Actions)

Deployment is fully automated via `deploy.yml`. On every push to `main`:

1. Tests run automatically.
2. On test success, `uv export --no-dev --format requirements-txt -o requirements.txt` generates a pip-compatible requirements file.
3. `azure/webapps-deploy@v3` deploys to `training-bot-api` and `training-bot-frontend` using the publish profiles stored as GitHub secrets.

```bash
# To trigger a production deployment:
git push origin main
```

### Manual Azure Deployment (if needed)

```bash
# Generate requirements for Azure
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API via Azure CLI (alternative to GitHub Actions)
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy frontend
az webapp deploy \
  --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Re-ingesting the Vector Store

```bash
# Run after