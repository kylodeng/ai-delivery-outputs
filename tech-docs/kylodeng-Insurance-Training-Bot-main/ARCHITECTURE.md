# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train new insurance agents at Sun Life Hong Kong. It provides two interaction modes: a **Teacher Mode** where an AI coach guides agents through product knowledge, discovery questioning techniques, and sales skills via streamed chat; and a **Roleplay/Assessment Mode** where agents practice pitching to AI-simulated customer personas and receive structured performance assessments. The backend is a FastAPI service that orchestrates LangGraph agents equipped with vector-store-backed tools over a corpus of ingested Sun Life insurance PDF documents (product brochures, hospital networks, claim procedures). A separate frontend application provides the chat UI. Both services are deployed to Azure App Service via GitHub Actions CI/CD, with AI inference routed through OpenRouter and document annotation powered by Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving chat, RAG, and session APIs |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| ChromaDB / Local FAISS Store | In-process / local filesystem | N/A (self-hosted on App Service) | Vector store for insurance document embeddings |
| PDF document corpus | Local filesystem (`/data/`) | Azure App Service ephemeral disk | Source insurance PDFs and annotation sidecar `.annot.json` files |
| `sessions.json` | Local filesystem (`/data/sessions.json`) | Azure App Service ephemeral disk | Persistent session state across server restarts |
| GitHub Actions runners | Managed CI/CD compute | GitHub | Test, build, and deploy pipeline execution |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Output store for AI-generated code review, docs, UAT reports |
| Anthropic Claude (`claude-sonnet-4-6`) | External API | Anthropic | Powers the five AI delivery workflow tools (code review, docs, testing, UAT) |
| OpenRouter API | External LLM gateway | OpenRouter | Routes LLM inference for the training bot agents at runtime |
| SendGrid | External email API | Twilio/SendGrid | Sends notification emails after AI delivery workflow runs |

---

## 3. Data Flow

### Teacher / Chat Mode

1. **User sends a chat message** via the frontend UI (Chainlit or Vite SPA) to `POST /chat` or similar streaming endpoint on the FastAPI backend.
2. **FastAPI** loads the user's session from the in-memory session registry (backed by `sessions.json`) and constructs the LangGraph teacher agent with the current conversation history.
3. **LangGraph teacher agent** receives the message and decides, via tool-calling, which RAG tools to invoke (e.g., `search_product`, `compare_plans`, `lookup_exclusions`).
4. **RAG tools** query the vector store (ChromaDB or FAISS) using semantic similarity search over the embedded insurance PDF corpus. Matching chunks are returned with source metadata (document name, page, section).
5. **Retrieved chunks** are injected into the agent's context. The agent calls the OpenRouter API (via `langchain_openai.ChatOpenAI`) with the augmented prompt to generate a streamed response.
6. **Response is streamed** back to the frontend via `StreamingResponse` (SSE). Inline citation markers (`[[S1]]`, `[[S2]]`) reference specific source documents.
7. **Source metadata** is collected per-request via `contextvars` and returned alongside the stream for the UI to render citation links pointing to `/docs/<path>` (statically served PDFs).
8. **Session state is updated** and written to `sessions.json`.

### Roleplay / Assessment Mode

1. **User initiates a roleplay session**; the backend generates a randomised `CustomerProfile` (name, age, occupation, financial goals, personality).
2. **User interacts** with the simulated customer; FastAPI uses the `_ROLEPLAY_SYSTEM` prompt with the customer profile to drive the customer persona LLM via OpenRouter.
3. **When the session ends**, the assessor agent is invoked with the full conversation transcript and customer profile.
4. **Assessor agent** uses the same RAG tools to verify factual claims made by the trainee agent against the source documents, then produces a structured assessment.
5. **Assessment is returned** to the frontend.

### Document Ingestion (Setup / Admin)

1. **Operator triggers** `POST /ingest` or runs `core/ingest.py` directly.
2. **PDFs are walked** recursively under `/data/Insurance-product-info/`.
3. **Each PDF is annotated** via the Anthropic/OpenRouter LLM (document-level metadata + per-page relevance), with results cached to `.annot.json` sidecar files.
4. **Relevant pages are chunked** by the semantic chunker (`core/chunker.py`) into units of ≤280 words.
5. **Chunks are embedded** in batches and upserted into the vector store, which is then persisted to disk.

### AI Delivery Workflows (CI/CD Tools)

1. **GitHub Actions triggers** (PR, push to main, schedule, tag, or manual dispatch) launch one of five tool workflows.
2. **Tool scripts** (`tool1`–`tool5`) fetch repo files or PR diffs via the GitHub REST API.
3. **Claude (`claude-sonnet-4-6`)** is called via the Anthropic SDK with a structured prompt.
4. **Outputs** (JSON, Markdown) are written to the `ai-delivery-outputs` repo via the GitHub API.
5. **SendGrid** sends a notification email to `kylo.deng@capco.com` with a summary and links.
6. **For PR reviews** (Tool 1), Claude's findings are also posted as a PR comment via the GitHub API.

---

## 4. Security Posture

### What Is Secured

- **Secrets managed via GitHub Actions Secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` are injected as environment variables, not hardcoded in source.
- **Deployment gated on tests**: The `deploy-api` and `deploy-frontend` jobs have a `needs: test` dependency, so a failing test suite blocks deployment.
- **Deploy only on `main` push**: Deploy jobs are conditioned on `github.ref == 'refs/heads/main'`, preventing accidental deploys from PRs.
- **Session isolation**: Sessions are keyed by UUID, preventing trivial enumeration.

### Gaps and Issues — **Be Honest**

- ⚠️ **TLS verification disabled in production code**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py` and `core/ingest.py`. This disables SSL certificate verification for all outbound LLM API calls, creating a man-in-the-middle vulnerability.
- ⚠️ **No API authentication on the FastAPI backend**: There is no middleware enforcing authentication or API keys on any of the `/chat`, `/ingest`, or session endpoints. Anyone who can reach the App Service URL can query the LLM and ingest documents.
- ⚠️ **`sessions.json` stored on ephemeral local disk**: Azure App Service's local filesystem is not durable across slot swaps or scale-out. Sessions will be lost on redeploy or horizontal scale. No external session store (Redis, Azure Table Storage) is used.
- ⚠️ **Vector store on local disk**: The ChromaDB/FAISS index is stored on the App Service local filesystem. It will be lost on redeploy unless an Azure File Share or persistent storage is mounted — this is not configured in any IaC.
- ⚠️ **No encryption at rest declared**: There is no IaC (Bicep, Terraform, ARM) in this repo configuring Azure storage encryption, App Service managed identity, or Key Vault. Encryption posture of the deployed App Services is unknown. **[TODO: Confirm Azure App Service plan and whether customer-managed key encryption is enabled]**
- ⚠️ **CORS is overly permissive**: `allow_methods=["*"]` and `allow_headers=["*"]` are set. Origins are limited to localhost in the current config, but there is no production origin configured. **[TODO: Set production CORS origin(s) before go-live]**
- ⚠️ **`GH_TOKEN` scope is unknown**: The `GH_TOKEN` secret has write access to the `ai-delivery-outputs` repo and can post PR comments. If the token has overly broad org-level permissions, a compromised workflow could write to any repo in the org. **[TODO: Scope GH_TOKEN to minimum required permissions]**
- ⚠️ **Insurance product PDFs served unauthenticated**: The `/docs` static mount serves all PDFs in the `data/` directory over HTTP with no authentication, including sensitive product documents.
- ⚠️ **No rate limiting**: No rate limiting is applied to any API endpoint, making the service vulnerable to prompt injection abuse and runaway LLM cost.
- ⚠️ **No input sanitisation**: User chat messages are passed directly to the LLM prompt without sanitisation, leaving prompt injection as a risk surface.
- ⚠️ **`API_KEY` defaults to empty string**: `_API_KEY = os.getenv("API_KEY", "")` — if the environment variable is unset, the LLM client is initialised with no key and will silently fail or succeed against an unauthenticated endpoint.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter) | App Service environment / `.env` file locally |
| `OPENAI_URL_BASE` | No (default: `https://openrouter.ai/api/v1`) | Low | App Service environment / `.env` |
| `OPENAI_MODEL` | No (default: `openai/gpt-oss-20b:free`) | Low | App Service environment / `.env` |
| `SHOW_TOOL_CALLS` | No (default: `true`) | Low | App Service environment / `.env` |
| `ANTHROPIC_API_KEY` | Yes (for ingestion annotation & CI tools) | 🔴 High | GitHub Actions Secret / App Service environment |
| `GH_TOKEN` | Yes (for CI tools 1–5) | 🔴 High — GitHub PAT with repo write | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (for CI tools 1–5 email) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (for deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (for deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (default: `ai-delivery-outputs`) | Low | GitHub Actions workflow env |
| `OUTPUT_REPO_OWNER` | No (default: `github.repository_owner`) | Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (default: `kylo.deng@capco.com`) | Medium — PII | GitHub Actions workflow env |
| `SENDER_EMAIL` | No (default: `noreply@ai-delivery.capco.com`) | Low | GitHub Actions workflow env |
| `VECTOR_STORE_TYPE` | No | Low | App Service environment / `.env` — **[TODO: confirm which store type is deployed]** |
| `VOYAGE_API_KEY` | **[TODO: required if Voyage AI embeddings are used]** | 🔴 High | App Service environment |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (`https://openrouter.ai/api/v1`) | External LLM Gateway API | Routes inference for teacher, assessor, and roleplay agents at runtime | Default free-tier model `openai/gpt-oss-20b:free`; configurable |
| **Anthropic Claude** (`claude-sonnet-4-6`) | External LLM API | Document annotation during ingestion; all five CI/CD AI delivery tools | Used directly via `anthropic` SDK in CI workflows |
| **Voyage AI** | External Embedding API | Likely used for document chunk embeddings into the vector store | **[TODO: confirm whether Voyage AI or OpenAI embeddings are configured in production]** |
| **ChromaDB / FAISS** | Local library | Vector store backend for RAG retrieval | Store type selected at runtime via `get_vector_store()` |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool calling, streaming | Core agent framework |
| **SendGrid** | External email API | Sends output notifications from CI tool workflows | Via `SENDGRID_API_KEY` |
| **GitHub REST API** | External API | CI tools read repo files, post PR comments, write to output repo | Via `GH_TOKEN` |
| **`ai-delivery-outputs`** repo | Sibling GitHub repository | Stores AI-generated review reports, tech docs, UAT packs | Must exist and be writable by `GH_TOKEN` |
| **pdfplumber** | Python library | PDF text extraction for ingestion pipeline | |
| **FastAPI / Uvicorn** | Python framework | HTTP server and async request handling | |
| **Azure App Service** | Cloud PaaS | Hosting for both API and frontend | No IaC in repo — provisioned manually **[TODO]** |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13 with `uv` package manager installed
- Azure App Services `training-bot-api` and `training-bot-frontend` pre-provisioned in Azure Portal
- GitHub repository secrets configured (see Section 5)
- Insurance PDF documents placed under `data/Insurance-product-info/`

### Local Development

```bash
# Install dependencies
uv sync

# Copy and configure environment variables
cp .env.example .env  # [TODO: confirm .env.example exists]
# Edit .env: set API_KEY, OPENAI_URL_BASE, ANTHROPIC_API_KEY, etc.

# Ingest PDFs into the vector store (one-time or when docs change)
python -m core.ingest --pdf-dir data/Insurance-product-info/

# Run the FastAPI backend
uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v
```

### CI/CD Deployment (Automated)

Deployment is fully automated via GitHub Actions on every push to `main` that passes tests:

```bash
# Trigger deployment by pushing to main
git push origin main

# The deploy.yml workflow will:
# 1. Run: uv run pytest tests/ -v
# 2. On success, generate requirements.txt:
#    uv export --no-dev --format requirements-txt -o requirements.txt
# 3. Deploy API:   azure/webapps-deploy to 'training-bot-api'
# 4. Deploy Frontend: azure/webapps-deploy to 'training-bot-frontend'
```

### Manual Document Ingestion (Post-Deploy)

```bash
# Via the API endpoint (if implemented)
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Or SSH into App Service and run directly
python -m core.ingest --pdf-dir data/Insurance-product-info/
```

### AI Delivery Tools (Manual Trigger)

```bash
# Via GitHub Actions UI — navigate to:
# Actions → Tool 1 — Code Review → Run workflow
# Actions → Tool 2 — Tech Documentation → Run workflow
# Actions → Tool 3 — Business Documentation → Run workflow (requires version tag or inputs)
# Actions → Tool 4 — Auto Testing → Run workflow
# Actions → Tool 5 — UAT Facilitation → Run workflow

# Or trigger Tool 3 via a version tag:
git tag v1.0.0
git push origin v1.0.0
```

---

## 8. Risks and TODOs

### Critical Risks

| Risk | Severity | Detail |
|---|---|---|
| SSL verification disabled | 🔴 Critical | `verify=False` on all outbound HTTP clients exposes LLM API calls to MITM attacks in production |
| No authentication on API | 🔴 Critical | Any