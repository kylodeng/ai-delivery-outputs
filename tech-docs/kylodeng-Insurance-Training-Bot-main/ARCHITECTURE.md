# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a RAG-powered (Retrieval-Augmented Generation) conversational AI system designed to train insurance sales agents. It ingests Sun Life Hong Kong insurance product PDFs into a vector store, then exposes two AI agent modes: a **Teacher agent** (ongoing interactive chat that teaches product knowledge, sales techniques, and discovery questioning) and an **Assessor agent** (one-shot post-roleplay scoring of a trainee's accuracy and technique). A customer **Roleplay mode** lets trainees practice sales conversations against an AI-simulated customer profile. The system is built with a FastAPI backend and (inferred) a separate frontend, both deployed to Azure App Service via GitHub Actions CI/CD. An LLM-powered annotation and chunking pipeline pre-processes PDFs into a vector store (supporting ChromaDB, FAISS, or Pinecone) to ground agent responses in verified product documentation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | App Service (Web App) | Azure | Hosts the FastAPI backend (chat, ingest, session management) |
| `training-bot-frontend` | App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Persistent storage / SaaS | Azure (local disk) / Pinecone SaaS | Stores document embeddings for RAG retrieval |
| Sessions File (`data/sessions.json`) | File-based persistence | Azure (local App Service storage) | Persists multi-turn conversation sessions across restarts |
| Static file mount (`/docs`) | StaticFiles endpoint | Azure (via FastAPI) | Serves raw PDFs and data files over HTTP for citation links |
| GitHub Actions Runners | CI/CD compute | GitHub (ubuntu-latest) | Test, build, and deploy pipelines |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Output sink for AI-generated code review reports, tech docs, UAT packs |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers AI delivery tooling (code review, tech docs, business docs, UAT) |
| OpenRouter / Custom OpenAI-compatible endpoint | External LLM API | OpenRouter (or configurable) | Powers teacher agent, assessor agent, and roleplay customer simulation |
| Voyage AI (inferred) | Embedding API | Voyage AI (SaaS) | Generates embeddings for PDF chunks during ingestion |
| SendGrid | Email API | Twilio/SendGrid SaaS | Sends notification emails after AI tooling workflow runs |

---

## 3. Data Flow

### 3a. Document Ingestion (One-time / On-demand)

1. An operator calls `POST /ingest` (or runs `core/ingest.py` directly) pointing at the `data/Insurance-product-info/` directory.
2. `ingest_directory()` walks all PDFs recursively; for each PDF it calls `load_or_create_annotations()`.
3. If no `.annot.json` sidecar exists, the annotator calls the configured LLM (Claude via OpenRouter) to produce document-level metadata (product name, type, summary) and per-page relevance flags; results are cached to `<pdf>.annot.json`.
4. Relevant pages are passed to `extract_chunks_from_pdf()`, which uses `pdfplumber` to extract text, cleans it, splits into semantic units (headings, bullets, paragraphs), and emits chunk dicts with metadata (product name, page numbers, section title, file URI).
5. `embed_chunks()` sends chunk batches to the embedding model (Voyage AI inferred from batch rate-limit comments) and writes vectors + metadata into the selected vector store (ChromaDB, FAISS, or Pinecone).
6. The vector store index is persisted to disk via `store.save()`.

### 3b. Teacher Mode — Chat Request

1. A user sends a chat message via the frontend to `POST /chat` (or equivalent streaming endpoint) on the FastAPI backend.
2. The backend retrieves or creates a `Session` object (from in-memory dict backed by `data/sessions.json`).
3. `make_teacher_agent()` constructs a LangGraph agent with the shared LLM and the eight RAG tools.
4. `reset_sources()` initialises a fresh per-request source tracking context variable.
5. The agent streams events via `astream_events`; for each tool call, the appropriate RAG tool (e.g. `search_product`, `compare_plans`) queries the vector store, collects source metadata via `_collect_sources()`, and returns ranked text chunks with source IDs (`[S1]`, `[S2]`, …).
6. The agent's LLM synthesises a response, inserting inline citation markers (`[[S1]]`) for facts drawn from retrieved chunks.
7. The FastAPI backend streams the response back to the frontend as Server-Sent Events; source metadata is appended after the stream for the UI to render as citation links pointing to `/docs/<path>/<file>.pdf`.

### 3c. Roleplay Mode

1. The user requests a new roleplay session; the backend calls `generate_profile()` which randomly assembles a `CustomerProfile` from Hong Kong–contextualised demographic pools.
2. The frontend receives the profile summary; subsequent user messages are sent to a roleplay endpoint.
3. The backend invokes the roleplay system prompt (embedding the customer profile) against the LLM without RAG tools — the LLM simulates the customer persona.
4. On session end, the frontend triggers assessment mode.

### 3d. Assessment Mode

1. The full roleplay conversation history and customer profile are passed to `make_assessor_agent()`.
2. The assessor agent invokes the same eight RAG tools to fact-check every product claim the trainee made against the vector store.
3. A structured scorecard is returned (five dimensions) and stored on the session object; the session is persisted to `data/sessions.json`.

### 3e. CI/CD Pipeline (Push to `main`)

1. Developer pushes to `main`; GitHub Actions triggers `deploy.yml`.
2. `test` job runs `pytest tests/` with Python 3.13 + `uv`.
3. On test pass, `deploy-api` and `deploy-frontend` jobs run in parallel; `uv export` generates `requirements.txt`; `azure/webapps-deploy@v3` deploys each App Service using publish profiles stored as GitHub Secrets.

### 3f. AI Delivery Tooling (Auxiliary Workflows)

1. On PR open/sync, `tool1_code_review.yml` fetches the PR diff, sends it to Claude (`claude-sonnet-4-6`), parses a structured JSON review, posts a PR comment, writes a JSON report to `ai-delivery-outputs`, and emails the result via SendGrid.
2. On merge to `main`, `tool2_tech_docs.yml` fetches all source and IaC files, generates README, architecture doc, and runbook via Claude, and commits them to `ai-delivery-outputs`.
3. On version tag push, `tool3_business_docs.yml` generates a Solution Overview Document and gap questionnaire via Claude.
4. On PR open/sync affecting `src/**`, `tool4_auto_testing.yml` generates or gap-analyses test files via Claude.
5. On `release/*` branch creation, `tool5_uat.yml` generates a UAT test pack (CSV + markdown) or analyses completed test results via Claude.

---

## 4. Security Posture

### ✅ What is secured

- **GitHub Secrets** are used for all credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — secrets are not hardcoded in workflow files.
- **CI gate**: Deployments require passing `pytest` tests before any deployment job runs.
- **Deployment-only on `main`**: The `deploy-api` and `deploy-frontend` jobs are guarded by `github.ref == 'refs/heads/main' && github.event_name == 'push'`.
- **CORS restriction**: The backend explicitly allows only `localhost:5173`, `localhost:8000`, and their `127.0.0.1` equivalents — not a wildcard `*` in the allow-origins list.
- **API key via environment variable** (`API_KEY`) for the LLM backend rather than hardcoded.
- **`SecretStr` wrapping** of the API key in `ChatOpenAI` instantiation prevents accidental logging.

### ❌ Gaps and explicit security concerns

- **TLS verification is disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls. This disables SSL certificate verification, opening the application to man-in-the-middle attacks on all outbound LLM traffic. **This must be fixed before production use.**
- **No authentication on FastAPI endpoints**: There is no API key, JWT, OAuth, or any other authentication middleware visible on the FastAPI app. Any caller who can reach `training-bot-api` can invoke chat, ingest, and session management endpoints.
- **Sessions persisted to a plain JSON file** (`data/sessions.json`) on local App Service disk — no encryption at rest. Conversation history (potentially containing PII from roleplay customer profiles) is stored unencrypted.
- **PDF/data files served unauthenticated** via `app.mount("/docs", StaticFiles(...))` — all ingested insurance documents are publicly accessible to anyone who can reach the API host.
- **No HTTPS enforcement** configured at the application layer (may be handled by Azure App Service, but not explicit in IaC — [TODO: confirm Azure App Service HTTPS-only flag is enabled]).
- **No secrets management service**: Secrets are stored only as GitHub Secrets and (at runtime) environment variables. There is no Azure Key Vault integration.
- **`GH_TOKEN` scope is unknown**: The `GH_TOKEN` used by AI tooling workflows can write to `ai-delivery-outputs` and post PR comments — [TODO: confirm this is a fine-grained PAT scoped to minimum necessary repos/permissions, not a classic full-scope token].
- **No rate limiting or input validation** on the chat/ingest endpoints — vulnerable to prompt injection and resource exhaustion.
- **CORS allows `allow_methods=["*"]` and `allow_headers=["*"]`** — overly broad; should be restricted to the specific HTTP methods and headers the frontend uses.
- **No WAF or DDoS protection** configured (not visible in IaC).
- **Ingestion LLM configured with `verify=False`**: Same SSL bypass issue as above in `_build_ingest_llm()`.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key | App Service environment variable / `.env` locally |
| `OPENAI_URL_BASE` | No | Low | App Service environment variable / `.env` (defaults to `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | App Service environment variable / `.env` (defaults to `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | App Service environment variable / `.env` (defaults to `true`) |
| `ANTHROPIC_API_KEY` | Yes (AI tooling workflows) | **High** — Anthropic API key | GitHub Secret |
| `GH_TOKEN` | Yes (AI tooling workflows) | **High** — GitHub PAT | GitHub Secret |
| `SENDGRID_API_KEY` | Yes (AI tooling workflows) | **High** — SendGrid API key | GitHub Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy workflow) | **High** — Azure deployment credential | GitHub Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy workflow) | **High** — Azure deployment credential | GitHub Secret |
| `OUTPUT_REPO` | No | Low | Workflow env (defaults to `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow env (hardcoded to `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow env (hardcoded to `noreply@ai-delivery.capco.com`) |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External SaaS API | AI delivery tooling (code review, doc gen, UAT) | Requires `ANTHROPIC_API_KEY` |
| **OpenRouter** (or compatible OpenAI endpoint) | External SaaS API | Teacher agent, assessor agent, roleplay LLM, ingestion annotation LLM | Configurable via `OPENAI_URL_BASE`; SSL verification disabled |
| **Voyage AI** (inferred) | External SaaS API | Document chunk embedding | Inferred from batch rate-limit comments (3 RPM free tier); [TODO: confirm embedding provider] |
| **Pinecone** (optional) | External SaaS | Vector store backend (alternative to local FAISS/Chroma) | Only used if `PineconeStore` is selected |
| **SendGrid** | External SaaS API | Email notifications from AI tooling workflows | Requires `SENDGRID_API_KEY` |
| **Azure App Service** | Cloud PaaS | Runtime hosting for API and frontend | Requires publish profiles |
| **GitHub** (`ai-delivery-outputs` repo) | External repo | Output sink for AI-generated reports and documentation | Requires `GH_TOKEN` with write access |
| **pdfplumber** | Python library | PDF text extraction during ingestion | Local dependency |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool wiring, RAG pipeline | Core framework |
| **FastAPI** | Python library | HTTP API framework | Core framework |
| **ChromaDB / FAISS** | Python library | Local vector store backends | Used when Pinecone not configured |
| **httpx** | Python library | HTTP client for LLM calls | SSL verification currently disabled |
| **`uv`** | Build tool | Python dependency management and packaging | Used in CI/CD |

---

## 7. Deployment Instructions

### Prerequisites
- Azure App Services `training-bot-api` and `training-bot-frontend` already provisioned.
- GitHub Secrets configured: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.
- App Service environment variables set: `API_KEY`, `OPENAI_URL_BASE`, `OPENAI_MODEL`.

### Automatic Deployment (via GitHub Actions)
```bash
# Push to main branch — triggers test + deploy pipeline automatically
git push origin main
```

### Manual Local Setup
```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Copy and configure environment
cp .env.example .env
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# 4. Ingest PDFs into vector store (one-time)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info/

# 5. Run the API server locally
uv run uvicorn api.main:app --reload --port 8000

# 6. Run tests
uv run pytest tests/ -v
```

### Manual Azure Deployment (without GitHub Actions)
```bash
# 1. Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API using Azure CLI
az webapp deploy --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 3. Deploy frontend
az webapp deploy --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Triggering AI Tooling Workflows Manually
```bash
# Code review on a specific PR
gh workflow run tool1_code_review.yml \