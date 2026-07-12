# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is an AI-powered sales training platform designed to help new insurance agents (in a Hong Kong/Sun Life context) develop product knowledge and sales skills. It provides two modes: a **Teacher mode** where an LLM-backed agent answers questions, explains insurance products, and runs coaching exercises using a RAG (Retrieval-Augmented Generation) knowledge base of Sun Life insurance PDFs; and a **Roleplay/Assessment mode** where the agent simulates a customer persona and a separate assessor LLM evaluates the trainee's performance. The system is deployed as two Azure App Service applications — a FastAPI backend and a separate frontend — with CI/CD via GitHub Actions, and is supplemented by a suite of five AI-powered developer tooling workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) that all use Anthropic's Claude API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend (RAG pipeline, agent orchestration, session management) |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend application (Chainlit UI or Vite-based SPA) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Local disk / managed service | Azure (local) or Pinecone (SaaS) | Stores embedded insurance document chunks for semantic retrieval |
| `data/sessions.json` | File on App Service disk | Azure | Persists multi-turn conversation session state across server restarts |
| `data/Insurance-product-info/` | Static files on App Service disk | Azure | PDF knowledge base served via `/docs` static mount |
| Anthropic Claude API (`claude-sonnet-4-6`) | External SaaS API | Anthropic | Powers teacher agent, assessor agent, and all five CI/CD tooling workflows |
| OpenRouter / LLM API | External SaaS API | OpenRouter (configurable) | Primary LLM for chat inference (configurable via `OPENAI_URL_BASE`) |
| Voyage AI Embeddings | External SaaS API | Voyage AI | Embeds PDF chunks into vector store during ingestion |
| GitHub Actions Runners | CI/CD compute | GitHub | Runs test, deploy, and AI tooling workflows |
| `ai-delivery-outputs` (GitHub repo) | GitHub Repository | GitHub | Output store for AI-generated code reviews, docs, test files, UAT packs |
| SendGrid | Email SaaS | Twilio/SendGrid | Sends notification emails from CI/CD tooling workflows |

---

## 3. Data Flow

### Application Data Flow (Runtime)

1. **User (trainee agent)** sends a chat message via the frontend (Chainlit UI or Vite SPA) to the FastAPI backend over HTTPS.
2. **FastAPI** (`api/main.py`) resolves the session from `sessions.json` (or creates a new one), selects the appropriate agent (teacher or roleplay/assessor) based on session mode.
3. **Teacher/Assessor Agent** (`api/agent.py`) receives the message and determines which RAG tool(s) to call (e.g., `search_product`, `compare_plans`, `lookup_exclusions`).
4. **RAG Tools** (`api/rag_tools.py`) query the **vector store** (`core/vector_store.py`) with a semantic similarity search over the embedded PDF chunks.
5. **Vector store** returns the top-k matching chunks with metadata (document name, page number, section title, file URL).
6. **RAG tools** collect source citations into a per-request `contextvars` context and return formatted text blocks with source IDs (e.g., `[S1]`, `[S2]`).
7. **LLM** (OpenRouter/configurable endpoint, `ChatOpenAI`) synthesises a response using the retrieved context. The response streams back token-by-token via `StreamingResponse`.
8. **FastAPI** emits the streaming response to the frontend; source citation metadata is appended at the end of the stream.
9. **Session state** (message history, customer profile, mode) is written back to `data/sessions.json` on disk.
10. **PDF files** referenced in citations are served directly to the browser via the `/docs` static file mount on the FastAPI app.

### PDF Ingestion Data Flow (One-time / on-demand)

1. Operator calls `POST /ingest` (or runs `core/ingest.py` directly).
2. `ingest_directory()` walks `data/Insurance-product-info/`, reads each PDF with `pdfplumber`.
3. For each PDF, `annotate_document()` / `annotate_page()` calls the LLM to classify relevance and extract metadata, caching results to `.annot.json` sidecar files.
4. Relevant pages are chunked into semantic units by `core/chunker.py` (heading-aware, sentence-split, max ~280 words/chunk).
5. Chunks are embedded in batches via Voyage AI and stored in the vector store (ChromaDB / FAISS / Pinecone), then saved to disk.

### CI/CD Data Flow

1. Developer pushes to `main` → `deploy.yml` runs tests (`pytest`) then deploys both App Services using Azure publish profiles stored in GitHub Secrets.
2. PR events → `tool1_code_review.yml` fetches the diff, sends to Claude, posts review comment on the PR, and writes a JSON report to the `ai-delivery-outputs` repo.
3. Merge to `main` → `tool2_tech_docs.yml` fetches repo files, generates README/architecture/runbook via Claude, writes to `ai-delivery-outputs`, and sends email via SendGrid.
4. Version tag push → `tool3_business_docs.yml` generates a solution overview document and gap questionnaire via Claude, writes to `ai-delivery-outputs`.
5. PR with source file changes → `tool4_auto_testing.yml` generates test files via Claude, writes to `ai-delivery-outputs`.
6. `release/` branch created → `tool5_uat.yml` generates a UAT test pack via Claude; completed results CSV can be re-analysed for a defect report.

---

## 4. Security Posture

### What Is Secured

- **Secrets management**: All API keys (`ANTHROPIC_API_KEY`, `SENDGRID_API_KEY`, `GH_TOKEN`, `AZURE_WEBAPP_PUBLISH_PROFILE_API/FRONTEND`) are stored as GitHub Actions secrets and injected as environment variables — not hardcoded in source.
- **GitHub Actions scope**: Deploy jobs only run on `push` to `main`, not on PRs, reducing supply-chain risk from malicious PRs triggering deployments.
- **PDF/data files** are served from a controlled directory mount (`/docs`) using FastAPI's `StaticFiles`, not arbitrary filesystem access.
- **Session isolation**: Sessions are identified by UUID (`uuid.uuid4()`), providing basic session token entropy.

### Security Gaps — Explicit Callouts

- ⚠️ **TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py` and `core/ingest.py`. This disables SSL certificate validation for **all outbound LLM API calls**, making the application vulnerable to man-in-the-middle attacks against the LLM provider. **This must be remediated before production use.**
- ⚠️ **No API authentication on the FastAPI backend**: There is no authentication middleware, API key check, or JWT validation on any endpoint. Any caller with network access to `training-bot-api` can invoke all endpoints, including `POST /ingest` which rewrites the entire vector store.
- ⚠️ **Sessions stored in a plain JSON file on disk** (`data/sessions.json`): No encryption at rest. If the App Service disk is compromised, all conversation history (including customer profiles and trainee dialogue) is exposed in plaintext. Azure App Service disk encryption is at the platform level only — not application-level.
- ⚠️ **CORS allows localhost origins in production**: `allow_origins` includes `http://localhost:5173` and `http://127.0.0.1:5173`. This should be removed for the production deployment and replaced with the actual frontend App Service URL.
- ⚠️ **`GH_TOKEN` scope is unknown**: The `GH_TOKEN` used across all five tooling workflows has write access to the `ai-delivery-outputs` repo and reads all source repos. If the token has `repo` or `admin` scope rather than fine-grained minimal permissions, it is overly broad. [TODO: Audit the GH_TOKEN scopes and replace with a fine-grained PAT scoped to exactly the required repos and permissions]
- ⚠️ **No input sanitisation on session/profile inputs**: Customer profile fields (name, occupation, financial goals) are interpolated directly into LLM system prompts as f-strings without sanitisation, creating a prompt injection surface.
- ⚠️ **No rate limiting**: No rate limiting is applied to any FastAPI endpoint, making the service vulnerable to abuse and unexpected LLM cost escalation.
- ⚠️ **`API_KEY` has a default empty string fallback**: `os.getenv("API_KEY", "")` — if the env var is not set, the LLM client initialises with an empty key. This should fail fast rather than silently proceed.
- ⚠️ **Encryption in transit for internal App Service communication**: Not explicitly configured — depends on Azure App Service default settings. [TODO: Confirm HTTPS-only is enforced on both App Services]
- ⚠️ **No secrets scanning** in the CI pipeline (e.g., `gitleaks`, `truffleHog`). The code review tool (`tool1`) does check for hardcoded secrets via Claude, but this is not a deterministic scanner.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High (LLM API key) | App Service env / `.env` file |
| `OPENAI_URL_BASE` | No | Low | App Service env / `.env` file (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | App Service env / `.env` file (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | App Service env / `.env` file (default: `true`) |
| `ANTHROPIC_API_KEY` | Yes (CI/CD tools) | 🔴 High | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI/CD tools) | 🔴 High (GitHub PAT) | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI/CD tools) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI/CD tools) | Low | GitHub Actions workflow env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI/CD tools) | Low | GitHub Actions workflow env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (CI/CD tools) | Low | GitHub Actions workflow env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI/CD tools) | Low | GitHub Actions workflow env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `TEST_MODE` | No (tool4) | Low | GitHub Actions workflow env |
| `UAT_MODE` | No (tool5) | Low | GitHub Actions workflow env |
| `RELEASE_VERSION` | No (tool3/5) | Low | GitHub Actions workflow env |
| `PROJECT_NAME` | No (tool3) | Low | GitHub Actions workflow env |

> [TODO: Confirm whether `API_KEY` for the App Service is an OpenRouter key or an Anthropic key — the model default (`openai/gpt-oss-20b:free`) suggests OpenRouter, but `core/ingest.py` defaults `OPENAI_URL_BASE` to `https://api.anthropic.com/v1`]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude (`claude-sonnet-4-6`) | External SaaS API | All five CI/CD tooling workflows; also referenced in `core/ingest.py` default URL | Requires `ANTHROPIC_API_KEY` |
| OpenRouter (or compatible OpenAI-format endpoint) | External SaaS API | Primary LLM for teacher/assessor agents and ingestion annotation | Configurable via `OPENAI_URL_BASE`; defaults to OpenRouter |
| Voyage AI | External SaaS API | Embedding PDF chunks for vector store | [TODO: Confirm Voyage AI API key — not found in env vars list; may be in `.env` only] |
| Pinecone | Optional External SaaS | Vector store backend (alternative to local ChromaDB/FAISS) | [TODO: Pinecone API key not referenced in visible env vars] |
| SendGrid | External SaaS | Email notifications from CI/CD tooling workflows | Requires `SENDGRID_API_KEY` |
| Azure App Service | Cloud PaaS | Hosts API and frontend | Deployed via `azure/webapps-deploy@v3` |
| `ai-delivery-outputs` (GitHub repo) | Sibling GitHub repo | Stores all AI-generated output artefacts (reviews, docs, test files, UAT packs) | Must exist and be accessible to `GH_TOKEN` |
| `pdfplumber` | Python library | Extracts text from insurance PDFs | |
| `LangChain` / `LangGraph` | Python library | Agent orchestration, tool calling, vector store abstractions | |
| `langchain-openai` | Python library | OpenAI-format LLM client (used for both OpenRouter and Anthropic endpoints) | |
| `FastAPI` | Python library | Backend web framework | |
| `Chainlit` | Python/JS library | Chat UI frontend (referenced in `main.py` CORS config) | [TODO: Confirm if Chainlit or a separate Vite SPA is the actual frontend] |
| `httpx` | Python library | Async HTTP client for LLM calls | ⚠️ `verify=False` — see Security section |
| `uv` | Python tooling | Dependency management and venv | Used in CI/CD |
| `pytest` | Python library | Test runner | |

---

## 7. Deployment Instructions

### Prerequisites
- Azure CLI logged in with access to the App Service subscription
- GitHub repository secrets configured (see Section 5)
- Python 3.13+ and `uv` installed locally

### Automated Deployment (CI/CD)
Every push to `main` that passes tests automatically deploys both services:
```bash
git push origin main
# GitHub Actions runs: test → deploy-api + deploy-frontend in parallel
```

### Manual: Initial PDF Ingestion (must be run once before the API is usable)
```bash
# Install dependencies
uv sync

# Set environment variables
export API_KEY="<your-llm-api-key>"
export OPENAI_URL_BASE="https://openrouter.ai/api/v1"
export OPENAI_MODEL="openai/gpt-oss-20b:free"

# Run ingestion
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Or trigger via API endpoint after deployment:
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest
```

### Manual: Local Development
```bash
# Install dependencies
uv sync

# Copy and populate env file
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Run the API
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v
```

### Manual: Deploy to Azure App Service (without CI/CD)
```bash
# Install uv and export requirements
uv export --no-dev --format requirements-txt -o requirements.txt