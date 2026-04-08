# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is an AI-powered insurance sales training platform built for Sun Life Hong Kong insurance agents. It provides two operational modes: a **Teacher mode** (ongoing conversational coaching on insurance products, sales techniques, and discovery questioning) and a **Roleplay/Assessment mode** (simulated customer interactions with a structured post-session accuracy assessment). The backend is a FastAPI service backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests proprietary insurance product PDFs, chunks and embeds them into a vector store (ChromaDB, FAISS, or Pinecone), and exposes eight LangChain tools to a LangGraph agent. A separate frontend application is deployed alongside the API. The system is deployed on Azure App Service via GitHub Actions CI/CD, with supplementary AI delivery automation tools (code review, tech docs, auto-testing, UAT facilitation, business docs) powered by Anthropic Claude running as GitHub Actions workflows.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend / LangGraph agent |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite app) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded / Managed Service | Local disk on App Service **or** Pinecone (cloud) | Stores embedded insurance product document chunks for RAG retrieval |
| GitHub Actions Runners | CI/CD Compute (ubuntu-latest) | GitHub | Test, build, and deploy pipelines; AI tooling workflows |
| Anthropic Claude (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers AI delivery tools (code review, docs, testing, UAT) |
| OpenRouter (or compatible OpenAI endpoint) | External LLM API | OpenRouter / configurable | Powers the teacher and assessor agents at runtime |
| SendGrid | Email delivery API | Twilio/SendGrid | Sends AI tooling output notifications via email |
| `ai-delivery-outputs` GitHub Repo | GitHub Repository | GitHub | Stores AI-generated artifacts (code reviews, docs, test reports) |
| `data/sessions.json` | JSON flat file | Azure App Service local disk | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | PDF document store + `.annot.json` sidecar files | Azure App Service local disk | Source insurance product PDFs and their LLM-generated annotation cache |

---

## 3. Data Flow

### Ingestion Pipeline (one-time / on-demand via `POST /ingest`)

1. Operator triggers document ingestion via `POST /ingest` API endpoint.
2. `core/ingest.py` walks the `data/Insurance-product-info/` directory recursively, finding all PDF files.
3. For each PDF, `core/annotator.py` checks for a cached `.annot.json` sidecar file. If absent, it invokes the configured LLM (via `OPENAI_URL_BASE`) to classify the document and annotate each page (product name, doc type, relevance, section headers). The annotation result is cached as `<filename>.annot.json`.
4. `core/chunker.py` extracts text via `pdfplumber`, applies heuristic heading/bullet detection, and splits relevant pages into semantic chunks (max ~280 words each).
5. Chunks (with metadata: product name, document name, page range, section title, file URL) are batched and embedded via the configured embedding model.
6. Embedded vectors are written to the vector store (ChromaDB, local FAISS, or Pinecone) and the index is persisted to disk (or Pinecone cloud).

### Teacher Mode (real-time streaming chat)

1. User sends a chat message to the frontend.
2. Frontend POSTs the message and session ID to `training-bot-api`.
3. `api/sessions.py` loads the session state (conversation history, mode, customer profile) from the in-memory session store (originally loaded from `data/sessions.json`).
4. `api/main.py` constructs a LangGraph teacher agent using `make_teacher_agent()` with eight RAG tools bound.
5. The agent streams events via `astream_events`, calling RAG tools as needed:
   - Tools query the vector store using similarity search, filtered by product name or across all products.
   - Source metadata is collected per-request via a `contextvars.ContextVar` and returned alongside the streamed response.
6. The LLM (via OpenRouter or configured `OPENAI_URL_BASE`) generates a streamed response incorporating retrieved document chunks with inline citations (`[[S1]]`, `[[S2]]`, etc.).
7. The response and updated message history are streamed back to the frontend; sources are appended.
8. Session state is updated in memory and periodically persisted to `data/sessions.json`.

### Roleplay / Assessment Mode

1. User initiates a roleplay session; `api/sessions.py` generates a random `CustomerProfile` (name, age, occupation, income, goals, personality, existing coverage from pre-defined HK-context pools).
2. The frontend POSTs messages to the API; `api/main.py` uses the `_ROLEPLAY_SYSTEM` prompt to make the LLM impersonate the customer character.
3. When the session ends, the assessor agent (`make_assessor_agent()`) is invoked (`ainvoke`) with the full conversation transcript and the customer profile injected into the system prompt.
4. The assessor uses the same RAG tools to verify factual claims made by the trainee agent against the document store.
5. A structured assessment report is returned to the frontend.

### AI Delivery Tooling (GitHub Actions)

1. GitHub events (PR open, push to main, schedule, tag, workflow_dispatch) trigger one of five workflow files.
2. The relevant Python script (`.github/scripts/tool[1-5]_*.py`) runs on an `ubuntu-latest` runner.
3. The script reads repository source files via the GitHub API (`shared.py:get_repo_files`) and/or PR diffs.
4. Claude API (`claude-sonnet-4-6`) is called with structured prompts; responses are parsed as JSON or markdown.
5. Outputs are committed to the `ai-delivery-outputs` repository via the GitHub API (`shared.py:write_output_file`).
6. Notifications are sent via SendGrid email to `kylo.deng@capco.com`.
7. For PR-triggered runs, a review comment is posted back to the PR via the GitHub API.

---

## 4. Security Posture

### What Is Secured

- **CI/CD secrets** — `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are stored as GitHub Actions secrets and are not hard-coded in source.
- **API key handling in Python** — `api/main.py` uses `pydantic.SecretStr` to wrap the API key, preventing accidental logging.
- **Branch protection (partial)** — The deploy workflow only runs on push to `main`, and requires the `test` job to pass first.
- **CORS restriction** — CORS is limited to specific localhost origins for development (though see gaps below).

### Security Gaps and Issues ⚠️

- **TLS verification disabled** — `api/main.py` and `core/ingest.py` both create `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)`. **This disables SSL certificate verification for all outbound LLM API calls, exposing the system to man-in-the-middle attacks against a service that receives insurance product data and user conversation content. This must be fixed before production use.**
- **No authentication on the FastAPI API** — There is no evidence of any authentication or authorization middleware on the `training-bot-api` endpoints (no API key check, no OAuth, no JWT). Any internet-accessible request can invoke the agent, trigger ingestion, or read session data.
- **Sessions stored in plaintext on local disk** — `data/sessions.json` stores full conversation transcripts in plaintext on the App Service local disk. There is no encryption at rest beyond whatever Azure provides at the VM/storage layer. [TODO: Confirm whether Azure App Service local disk is encrypted at rest in this subscription].
- **CORS in production** — CORS is configured to allow `localhost:5173` and `localhost:8000`. If these origins are also allowed in the deployed Azure environment (not just local dev), this is a misconfiguration. [TODO: Confirm production CORS origins are restricted to the actual frontend App Service URL].
- **No rate limiting** — No rate limiting is applied to API endpoints, making the service vulnerable to abuse and unexpected LLM API cost spikes.
- **Insurance product PDFs served statically** — `app.mount("/docs", StaticFiles(...))` exposes the entire `data/` directory (including PDFs and annotation JSON files) over HTTP with no access control. Anyone who can reach the API can download all proprietary insurance product documents.
- **`GH_TOKEN` scope unknown** — The `GH_TOKEN` used by AI delivery tools has unspecified scope. If it has write access to all repositories in the organization, this is overly broad. [TODO: Confirm GH_TOKEN is scoped to only the required repositories and permissions (contents:write, pull-requests:write)].
- **No input validation on session/profile endpoints** — User-supplied inputs (session messages, customer profiles, workflow dispatch inputs) are passed to LLM prompts without sanitization, creating prompt injection risk.
- **Flat-file session store** — `sessions.json` is not suitable for concurrent write access. Under load, concurrent requests could corrupt the file.
- **No secrets scanning** — No `git-secrets`, `trufflehog`, or similar tool is configured in CI to prevent accidental secret commits.
- **Encryption in transit (App Service)** — [TODO: Confirm HTTPS is enforced on both Azure App Services and HTTP-to-HTTPS redirect is enabled].
- **Encryption at rest (vector store)** — If using local FAISS or ChromaDB on App Service disk, data is not encrypted at the application layer. [TODO: Confirm Azure disk encryption policy for App Service instances].

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **Critical** — LLM provider API key | `.env` (local); Azure App Service Application Settings (prod) |
| `OPENAI_URL_BASE` | No | Low | `.env`; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | `.env`; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | `.env`; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (AI tools workflows) | **Critical** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (AI tools workflows) | **High** — GitHub PAT with repo access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (AI tools workflows) | **High** — Email delivery API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy workflow) | **Critical** — Azure deploy credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy workflow) | **Critical** — Azure deploy credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (AI tools) | Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (AI tools) | Low | GitHub Actions workflow env; defaults to `github.repository_owner` |
| `NOTIFY_EMAIL` | No (AI tools) | Low | GitHub Actions workflow env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (AI tools) | Low | GitHub Actions workflow env; hardcoded to `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO] | Low | Not observed in provided files — assumed configurable in App Settings |
| `PINECONE_API_KEY` | Conditional | **Critical** — if Pinecone is used | [TODO: Not observed — confirm if Pinecone is used in production] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (or compatible OpenAI endpoint) | External LLM API | Teacher agent, assessor agent, document annotation | Configurable via `OPENAI_URL_BASE`; default model `openai/gpt-oss-20b:free` |
| **Anthropic Claude** (`claude-sonnet-4-6`) | External LLM API | All five AI delivery GitHub Actions tools | Accessed via `anthropic` Python SDK |
| **Azure App Service** | Cloud PaaS | Hosts API and frontend web applications | Two separate App Service instances |
| **LangChain / LangGraph** | Python framework | Agent orchestration, tool binding, streaming | Core dependency for teacher and assessor agents |
| **LangChain-OpenAI** (`langchain_openai`) | Python package | `ChatOpenAI` LLM wrapper used throughout | |
| **pdfplumber** | Python library | PDF text extraction for ingestion pipeline | |
| **FastAPI** | Python web framework | REST API backend | |
| **Pydantic** | Python library | Data validation, `SecretStr` for API keys | |
| **httpx** | Python HTTP client | Async HTTP for LLM API calls | SSL verification disabled — see Security Posture |
| **python-dotenv** | Python library | Local `.env` file loading | |
| **ChromaDB / FAISS / Pinecone** | Vector databases | Document embedding storage and similarity search | Abstracted behind `BaseVectorStore`; active backend configurable |
| **Voyage AI** (implied) | Embedding API | Document and query embedding | Referenced in `ingest.py` comments (rate limit guidance mentions Voyage AI free tier) |
| **SendGrid** | Email API | Notification delivery for AI tooling outputs | |
| **GitHub API** (`api.github.com`) | External API | Reading repo files, posting PR comments, writing output files | Used by all AI delivery tool scripts via `shared.py` |
| **`ai-delivery-outputs`** | Sibling GitHub Repo | Stores all AI-generated documentation and test artifacts | Must be accessible to `GH_TOKEN` |
| **Health Mutual Group Limited (HMG)** | Third-party network provider | Cashless hospital arrangement network (referenced in product data) | Data dependency only — no API integration observed |
| **Sun Life Hong Kong** | Data provider | Source of all insurance product PDFs and supplementary documents | Data dependency only |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13+ with `uv` installed (`pip install uv`)
- Azure CLI authenticated to the correct subscription
- Both Azure App Services (`training-bot-api`, `training-bot-frontend`) provisioned
- GitHub repository secrets configured (see Section 5)

### Local Development Setup

```bash
# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# Install dependencies using uv
uv sync

# Copy and configure environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with your API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Run the ingestion pipeline (one-time, requires PDF files in data/)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info/

# Start the API server
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v
```

### Production Deployment (via GitHub Actions — Automatic)

Push to `main` branch. The `deploy.yml` workflow will:

1. Run the full test suite (`pytest tests/ -v`).
2. On success, export `requirements.txt` via `uv export`.
3. Deploy the API to Azure App Service `training-bot-api` using the publish profile.
4. Deploy the frontend to Azure App Service `training-bot-frontend` using the publish profile.

```bash
# Trigger deployment
git push origin main
```

### Manual Deploy (Azure CLI — fallback)

```bash
# Generate requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API