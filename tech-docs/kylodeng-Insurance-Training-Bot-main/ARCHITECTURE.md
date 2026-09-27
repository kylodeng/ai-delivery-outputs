# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a RAG (Retrieval-Augmented Generation) application designed to train new insurance sales agents at Sun Life Hong Kong. It provides two modes of interaction: a **Teacher mode** where an LLM-backed agent coaches trainees on insurance concepts, products, and sales techniques using a knowledge base of ingested insurance PDFs; and a **Roleplay/Assessment mode** where trainees practice sales conversations against AI-simulated customer personas, followed by automated accuracy scoring. The system ingests proprietary insurance product documents (brochures, hospital network lists, policy documents) into a vector store, exposes a FastAPI backend with streaming LLM responses, and is served via two Azure App Service deployments (API and Frontend). A suite of five AI-powered GitHub Actions CI/CD tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) leverage the Anthropic Claude API to automate engineering delivery processes.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving LLM/RAG endpoints |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the Chainlit/Vite frontend UI |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded / Managed (varies by config) | Local / Azure / Pinecone | Stores embedded insurance document chunks for RAG retrieval |
| GitHub Actions Runners | Managed CI/CD Compute | GitHub (Microsoft) | Executes test, deploy, and AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated documentation outputs (code reviews, tech docs, UAT packs) |
| OpenRouter API | External LLM Gateway | Third-party (OpenRouter.ai) | Routes LLM inference calls to underlying models (default: `openai/gpt-oss-20b:free`) |
| Anthropic Claude API | External LLM API | Anthropic | Powers AI delivery tooling workflows (code review, docs, testing, UAT) |
| SendGrid | External Email Service | Twilio/SendGrid | Sends notification emails from AI tooling workflows |
| Insurance PDF Documents | Static File Storage | Azure App Service (mounted `/data`) | Source documents served over HTTP at `/docs/` endpoint |
| `data/sessions.json` | Local File | Azure App Service disk | Persists multi-turn conversation session state across restarts |

---

## 3. Data Flow

### 3a. Document Ingestion (one-time / on-demand)

1. An operator calls `POST /ingest` on the FastAPI API, or runs `core/ingest.py` directly.
2. `ingest_directory()` walks the `data/Insurance-product-info/` directory and finds all PDF files.
3. For each PDF, `annotate_document()` calls the configured LLM (Anthropic/OpenRouter) to extract product metadata; results are cached to a sidecar `.annot.json` file to avoid re-calling the LLM.
4. `extract_chunks_from_pdf()` (via pdfplumber) extracts and chunks page text using heading/bullet heuristics; irrelevant pages (cover art, awards, disclaimers) are filtered using the annotation.
5. `embed_chunks()` sends batches of text chunks to the configured embedding provider (Voyage AI or OpenAI embeddings) and writes results into the vector store (ChromaDB, FAISS, or Pinecone).
6. The vector store index is saved to disk (or pushed to Pinecone).

### 3b. Teacher Mode (real-time chat)

1. A user sends a message via the Chainlit frontend to `POST /chat/teacher` on the FastAPI backend.
2. The FastAPI handler retrieves the active session from `sessions.json` and reconstructs conversation history.
3. A LangGraph teacher agent is created with eight RAG tools (`search_product`, `search_all`, `lookup_hospital_network`, `compare_plans`, `lookup_exclusions`, `search_claim_procedure`, `list_products`, `get_current_date`).
4. The agent calls the LLM (via OpenRouter) with the `TEACHER_SYSTEM` prompt and conversation context; the LLM decides which tools to invoke.
5. Tool calls query the vector store using similarity search; matching chunks are returned with source metadata (document name, page range, file URL).
6. Sources are accumulated in a per-request `contextvars.ContextVar` and appended to the streamed response.
7. The response is streamed back to the frontend via `StreamingResponse`; citation markers (`[[S1]]`, `[[S2]]`, etc.) link inline facts to source documents.
8. PDFs are served directly by FastAPI's `StaticFiles` mount at `/docs/` so the frontend can link to them.

### 3c. Roleplay + Assessment Mode

1. The frontend calls `POST /sessions` to create a session; `generate_profile()` randomly selects a Hong Kong customer persona (name, occupation, age, income, goals, personality).
2. The user (trainee) exchanges messages with the AI customer persona via `POST /chat/roleplay`; the LLM plays the customer character.
3. When the trainee ends the session, the frontend calls `POST /assess`.
4. A LangGraph assessor agent receives the full conversation transcript + customer profile, verifies factual claims against the vector store via RAG tools, and scores the trainee on five dimensions.
5. The assessment result is returned to the frontend and stored in the session.

### 3d. CI/CD and AI Tooling

1. On push to `main` or PR open, GitHub Actions triggers `deploy.yml`: runs pytest, then deploys both App Services using Azure publish profiles.
2. AI tooling workflows (`tool1`–`tool5`) are triggered on PRs, schedules, tags, or manual dispatch.
3. Each tool fetches source code from the GitHub API, calls Anthropic Claude, writes output to the `ai-delivery-outputs` repo, and sends a summary email via SendGrid.

---

## 4. Security Posture

### Secured

- **GitHub Secrets** are used for all sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`); they are not hardcoded in workflow files.
- **Dependency pinning** via `uv` with a lockfile ensures reproducible builds.
- **PDF annotation caching** (`.annot.json` sidecars) reduces LLM API surface area after initial ingestion.
- **Scoped CORS**: the API only allows specific localhost origins and the same-host origin (not wildcard `*`).

### Not Secured / Gaps

- **⚠️ TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `api/main.py` and `core/ingest.py`. This disables SSL certificate validation for all outbound LLM API calls, exposing the system to man-in-the-middle attacks. **This must be removed before production use.**
- **⚠️ Session state stored as a plain JSON file** (`data/sessions.json`) on the App Service local disk. This file is unencrypted at rest and contains full conversation transcripts, customer profiles, and potentially PII (names, financial data). There is no access control on this file.
- **⚠️ No API authentication**: The FastAPI backend exposes `/chat`, `/sessions`, `/ingest`, and `/assess` endpoints with no authentication middleware (no API key, JWT, or OAuth). Any client that can reach the App Service URL can invoke model inference and ingest documents.
- **⚠️ No rate limiting**: No request rate limiting or throttling is implemented on the API, making it vulnerable to abuse and runaway LLM cost.
- **⚠️ IAM not visible**: No Azure IAM or RBAC configuration is present in the repository (no Bicep, Terraform, or ARM templates). It is unknown what permissions the App Service identities have. **[TODO: Document and restrict App Service managed identity permissions]**
- **⚠️ Overly broad `GH_TOKEN`**: The `GH_TOKEN` secret used in AI tooling workflows has write access to the `ai-delivery-outputs` repository. The required scope is not documented or constrained to minimum necessary permissions.
- **⚠️ Insurance PDF documents served unauthenticated**: The `/docs/` static file mount serves all files under `data/` (including policy documents and hospital lists) over plain HTTP with no authentication.
- **⚠️ No encryption in transit enforcement**: CORS allows `http://` (not `https://`) origins. There is no redirect from HTTP to HTTPS configured at the application layer.
- **⚠️ No secrets scanning**: No `gitleaks`, `truffleHog`, or similar secret-scanning step is present in CI.
- **⚠️ `verify=False` also used in ingest LLM client** (`core/ingest.py`) — same MITM risk during document annotation.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key | Azure App Service env vars / `.env` file |
| `OPENAI_URL_BASE` | No | Low | Azure App Service env vars / `.env` file (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | Azure App Service env vars / `.env` file (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | Azure App Service env vars / `.env` file (default: `true`) |
| `ANTHROPIC_API_KEY` | Yes (tooling workflows) | 🔴 High — Anthropic Claude API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (tooling workflows) | 🔴 High — GitHub Personal Access Token with repo write | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (tooling workflows) | 🔴 High — Email send permission | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No | Low | GitHub Actions workflow env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions workflow env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions workflow env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions workflow env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `VECTOR_STORE_TYPE` | [TODO: confirm env var name] | Low | Azure App Service env vars / `.env` file |
| `VOYAGE_API_KEY` or `OPENAI_EMBEDDINGS_KEY` | [TODO: confirm if separate] | 🔴 High | Azure App Service env vars / `.env` file |
| `PINECONE_API_KEY` | Conditional (if Pinecone store used) | 🔴 High | Azure App Service env vars / `.env` file |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter.ai** | External LLM Gateway API | Routes inference to underlying models for teacher/roleplay/assessor agents | Default model `openai/gpt-oss-20b:free` — free tier may have reliability/rate limits |
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External LLM API | Powers all five AI delivery tooling GitHub Actions workflows | Billed per token |
| **Voyage AI** (likely) | External Embedding API | Generates vector embeddings for RAG document chunks | [TODO: Confirm embedding provider; not explicitly named in visible source] |
| **LangChain / LangGraph** | Python framework | Agent orchestration, tool routing, streaming | Core dependency |
| **Chainlit** | Frontend framework | Chat UI served as the frontend App Service | [TODO: Confirm Chainlit vs Vite — both referenced] |
| **pdfplumber** | Python library | PDF text extraction during ingestion | Local processing |
| **ChromaDB / FAISS / Pinecone** | Vector store | Stores and retrieves document embeddings | Selection via config; `core/__init__.py` exports all three |
| **SendGrid** | External Email API | Sends notification emails from AI tooling workflows | Twilio/SendGrid account required |
| **GitHub API** (`api.github.com`) | External API | Source file fetching, PR commenting, output file writing in AI tooling workflows | Requires `GH_TOKEN` |
| **`ai-delivery-outputs`** (sibling repo) | GitHub Repository | Stores all AI-generated outputs (reviews, docs, test packs, UAT sheets) | Must exist under same GitHub owner |
| **Azure App Service** | Cloud PaaS | Hosts both API and frontend | `training-bot-api`, `training-bot-frontend` |
| **Sun Life Hong Kong PDFs** | Static data | Insurance product knowledge base (brochures, hospital lists, policy documents) | Bundled in `data/Insurance-product-info/`; proprietary content |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13 (API), Python 3.12 (tooling scripts)
- [`uv`](https://github.com/astral-sh/uv) package manager installed
- Azure CLI authenticated with access to the target subscription
- All required secrets configured in GitHub repository settings

### Local Development

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install dependencies
uv sync

# 3. Copy and configure environment variables
cp .env.example .env
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.

# 4. Ingest insurance documents into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 5. Start the API server
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 6. Access the frontend (Chainlit) at http://localhost:8000
```

### Running Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (Azure App Service via GitHub Actions)

Deployment is fully automated via `.github/workflows/deploy.yml` on push to `main`:

```
git push origin main
# GitHub Actions will:
# 1. Run pytest
# 2. Generate requirements.txt via: uv export --no-dev --format requirements-txt -o requirements.txt
# 3. Deploy API to Azure App Service 'training-bot-api'
# 4. Deploy Frontend to Azure App Service 'training-bot-frontend'
```

### Manual Deployment (if bypassing CI)

```bash
# Generate requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API (requires Azure CLI and publish profile)
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend
az webapp deploy \
  --resource-group <RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Triggering AI Tooling Workflows Manually

```
# Code Review — via GitHub Actions UI:
# Workflow: "Tool 1 — Code Review" → Run workflow → mode: repo or pr

# Tech Documentation
# Workflow: "Tool 2 — Tech Documentation" → Run workflow

# Business Documentation
# Workflow: "Tool 3 — Business Documentation" → Run workflow → set project_name, release_version

# Auto Testing
# Workflow: "Tool 4 — Auto Testing" → Run workflow → mode: generate or gap-analysis

# UAT Facilitation
# Workflow: "Tool 5 — UAT Facilitation" → Run workflow → mode: generate or analyse
```

---

## 8. Risks