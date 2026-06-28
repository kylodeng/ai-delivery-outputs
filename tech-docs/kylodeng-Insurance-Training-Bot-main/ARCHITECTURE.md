# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train new insurance sales agents working for Sun Life Hong Kong. The system ingests proprietary insurance product PDFs (brochures, policy documents, hospital network lists) into a vector store, then exposes two AI-powered interaction modes: a **Teacher Mode** (ongoing streamed chat where an LLM-backed agent coaches agents on product knowledge, sales techniques, and discovery questioning) and a **Roleplay/Assessment Mode** (where the agent practises selling to a simulated customer profile and receives a structured accuracy assessment afterwards). The backend is a FastAPI service deployed to Azure App Service, with a separate frontend application also on Azure App Service, both deployed via GitHub Actions on push to `main`.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving agent endpoints, RAG queries, and session management |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) for agent trainees |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded local store or managed service | Azure / [TODO: confirm backend] | Stores embedded insurance document chunks for RAG retrieval |
| `data/sessions.json` | Local file on App Service filesystem | Azure | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | Local PDF files + `.annot.json` sidecars | Azure | Source insurance product documents served statically via `/docs` endpoint |
| GitHub Actions Runners | CI/CD compute (ubuntu-latest) | GitHub | Run tests and deploy both App Services on push to `main` |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Output repo for AI-generated code reviews, tech docs, business docs, and test files |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers code review, tech docs, business docs, auto-testing, and UAT workflows |
| OpenRouter / OpenAI-compatible endpoint | External LLM API | OpenRouter (or configurable) | Powers teacher agent, assessor agent, roleplay simulation, and PDF annotation |
| SendGrid | Email API | Twilio/SendGrid | Sends notification emails for completed AI workflow outputs |
| Voyage AI (implied) | Embedding API | Voyage AI | Generates document embeddings for vector store ingestion (referenced in batch delay comment) |

---

## 3. Data Flow

### 3a — Document Ingestion (Setup / Admin)

1. An operator runs the ingestion script (or triggers `POST /ingest`) pointing at `data/Insurance-product-info/`.
2. `core/ingest.py` walks the directory and for each PDF calls `core/annotator.py`, which invokes the configured LLM (OpenRouter endpoint) to extract product metadata and per-page relevance annotations.
3. Annotations are cached as `.annot.json` sidecar files beside each PDF to avoid re-calling the LLM on subsequent ingests.
4. Relevant pages are chunked by `core/chunker.py` using heuristic heading/bullet/paragraph detection with a configurable `max_words` limit.
5. Chunks are embedded in batches via the Voyage AI (or configured) embedding model and written to the vector store (Chroma, FAISS, or Pinecone — selected via environment).
6. The vector store index is saved to disk (`store.save()`).

### 3b — Teacher Mode (Live Chat)

1. A trainee opens the frontend UI and starts a new session.
2. The frontend sends a POST request to the FastAPI backend (`training-bot-api`).
3. `api/sessions.py` creates a session record (persisted to `data/sessions.json`).
4. The user's message is forwarded to `api/agent.py`, which constructs the `TEACHER_SYSTEM` prompt and binds RAG tools from `api/rag_tools.py`.
5. The LangGraph teacher agent decides which RAG tool to call (e.g. `search_product`, `lookup_exclusions`, `compare_plans`).
6. Selected tools query the vector store with similarity search; results are filtered by product metadata.
7. Source chunk metadata is collected into a per-request `_sources_ctx` contextvar list.
8. The agent synthesises a response with inline citation markers (`[[S1]]`, `[[S2]]`, etc.) and streams it back to the frontend via `StreamingResponse`.
9. Source references are appended to the streamed response so the frontend can render document links pointing to `/docs/<path>` static endpoints.

### 3c — Roleplay & Assessment Mode

1. The trainee requests a roleplay session; `api/sessions.py` calls `generate_profile()` to randomly construct a Hong Kong customer persona (name, age, occupation, financial goals, etc.).
2. The frontend sends the trainee's sales messages; the FastAPI backend uses the `_ROLEPLAY_SYSTEM` prompt with the customer profile to simulate customer responses via the OpenRouter LLM.
3. When the roleplay ends, the frontend triggers assessment mode.
4. `api/agent.py`'s assessor agent receives the full conversation transcript and customer profile, then uses the same RAG tools to **verify every factual claim** the trainee made against the knowledge base.
5. The assessor returns a structured JSON evaluation across five performance dimensions.
6. Results are rendered to the trainee in the frontend.

### 3d — AI DevOps Workflows (GitHub Actions)

1. On PR open/push/schedule, GitHub Actions triggers one of five AI tool workflows.
2. The workflow script fetches repo file contents via the GitHub API (using `GH_TOKEN`).
3. File content is sent to Anthropic Claude (`claude-sonnet-4-6`) via the Anthropic Python SDK.
4. Claude returns a structured response (JSON or Markdown).
5. The output is written to the `ai-delivery-outputs` GitHub repository via the GitHub API.
6. A notification email is dispatched via SendGrid to `kylo.deng@capco.com`.
7. For code review workflows on PRs, a comment is also posted directly on the PR.

---

## 4. Security Posture

### ✅ What Is Secured

- **CI/CD secrets** are stored as GitHub Actions encrypted secrets (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and are not hardcoded in source.
- **Publish profiles** for Azure App Service are used instead of service principal credentials, reducing the blast radius of CI/CD credential compromise.
- **Test gate** is enforced before deployment — both `deploy-api` and `deploy-frontend` jobs `need: test`, so a failing test blocks deployment.
- **Session data** uses UUIDs for session identifiers, reducing enumeration risk.

### ❌ Gaps and Missing Controls

- **TLS verification is explicitly disabled** (`httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)`) in both `api/main.py` and `core/ingest.py`. This means all outbound HTTPS calls to the LLM API bypass certificate validation — a serious MITM vulnerability in production.
- **No authentication or authorisation on any API endpoint.** The FastAPI app has no API key, OAuth, or session token validation. Anyone who can reach the App Service URL can call all endpoints including `POST /ingest`.
- **`data/sessions.json` is stored on the App Service local filesystem**, which is ephemeral across restarts/slot swaps and is not encrypted at rest by default. Session data (including full conversation transcripts containing potentially sensitive customer profile simulations) is at risk.
- **CORS is overly permissive** — `allow_methods=["*"]` and `allow_headers=["*"]` are set. Origins are explicitly listed (localhost + localhost:8000) but `allow_headers=["*"]` still allows arbitrary headers.
- **No encryption at rest** is configured for the vector store. If using local FAISS, the index files are plaintext on disk.
- **`GH_TOKEN` scope is unknown** — [TODO: confirm whether GH_TOKEN has repo-scoped write access to all repos or is minimally scoped]. Overly broad token scope could allow writing to arbitrary repositories.
- **No WAF or IP allowlisting** is configured on the Azure App Service in the IaC visible here.
- **No rate limiting** on the FastAPI backend — LLM API costs could be exhausted by unauthenticated callers hitting `/ingest` or chat endpoints.
- **Sensitive data in environment variables** — `API_KEY` for the LLM provider is loaded via `os.getenv` and constructed into a `SecretStr` but the raw key is printed indirectly (the debug print on line `print(f"SHOW_TOOL_CALLS=...")` is benign, but the pattern of printing env values in logs is present).
- **No secrets scanning** configured in the GitHub Actions pipeline.
- **`ai-delivery-outputs` repo** receives full source code extracts via the GitHub API — access controls on that repo are [TODO: verify].

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM provider API key (OpenRouter or Anthropic) | App Service App Settings / `.env` file locally |
| `OPENAI_URL_BASE` | No | 🟡 Medium — LLM endpoint URL | App Service App Settings / `.env` |
| `OPENAI_MODEL` | No | 🟢 Low | App Service App Settings / `.env` |
| `SHOW_TOOL_CALLS` | No | 🟢 Low | App Service App Settings / `.env` |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | 🔴 High | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI workflows) | 🔴 High — GitHub Personal Access Token | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI workflows) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (CI deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (CI deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No | 🟢 Low | GitHub Actions env (hardcoded as `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII email address | GitHub Actions env (hardcoded as `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟡 Medium | GitHub Actions env (hardcoded as `noreply@ai-delivery.capco.com`) |
| `VECTOR_STORE_BACKEND` | [TODO: confirm if this env var exists] | 🟢 Low | [TODO: confirm where set] |
| `PINECONE_API_KEY` | Conditional (if Pinecone backend) | 🔴 High | [TODO: confirm where set] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Code review, tech/business/test docs, UAT workflows | Requires `ANTHROPIC_API_KEY` |
| OpenRouter (`openrouter.ai/api/v1`) | External LLM API (default) | Teacher agent, assessor agent, roleplay, annotation | Configurable via `OPENAI_URL_BASE`; TLS verification disabled |
| Voyage AI | External Embedding API | Document chunk embedding for RAG | Implied by rate-limit comment in `ingest.py`; [TODO: confirm embedding model and API key env var] |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, message types | Version [TODO: check `pyproject.toml`] |
| LangChain-OpenAI | Python library | `ChatOpenAI` wrapper for OpenRouter-compatible endpoint | |
| ChromaDB | Vector store (option) | Local persistent vector store | [TODO: confirm which backend is active in prod] |
| FAISS (`LocalFAISSStore`) | Vector store (option) | Local in-process vector store | Not suitable for multi-instance deployment |
| Pinecone (`PineconeStore`) | Vector store (option) | Managed cloud vector store | [TODO: confirm if used in production] |
| pdfplumber | Python library | PDF text extraction for ingestion | |
| FastAPI | Python framework | Backend REST API | |
| httpx | Python library | Async HTTP client for LLM calls | TLS verification disabled — see security gaps |
| SendGrid API | External email service | Notification emails from CI workflows | Requires `SENDGRID_API_KEY` |
| GitHub API (`api.github.com`) | External API | File fetching, PR comments, output file writes in CI | Requires `GH_TOKEN` |
| `ai-delivery-outputs` (GitHub repo) | External repository | Stores AI-generated documentation and review outputs | Must be writable by `GH_TOKEN` |
| Azure App Service | PaaS | Hosts API and frontend | `training-bot-api`, `training-bot-frontend` |
| uv | Python package manager | Dependency management and `requirements.txt` export for deployment | |

---

## 7. Deployment Instructions

### Prerequisites
- Python 3.13 installed locally
- `uv` installed (`pip install uv` or via `astral-sh/setup-uv`)
- Azure App Service instances `training-bot-api` and `training-bot-frontend` already provisioned
- GitHub secrets configured (see Section 5)

### Local Development

```bash
# Clone the repo
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# Install dependencies
uv sync

# Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.

# Ingest insurance PDFs into the vector store
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Start the FastAPI backend
uvicorn api.main:app --reload --port 8000

# In a separate terminal, start the frontend (if Vite-based)
# [TODO: confirm frontend start command — no frontend package.json found in provided files]
cd frontend
npm install && npm run dev   # [TODO: verify]
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (via GitHub Actions)

Deployment is fully automated. Push to `main` to trigger:

```bash
git push origin main
```

The `deploy.yml` workflow will:
1. Run `pytest tests/ -v`
2. On success, export `requirements.txt` via `uv export`
3. Deploy the API to `training-bot-api` Azure App Service using the publish profile
4. Deploy the frontend to `training-bot-frontend` Azure App Service using its publish profile

### Manual Document Ingestion (post-deployment)

```bash
# [TODO: confirm if /ingest endpoint exists and its authentication requirement]
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

---

## 8. Risks and TODOs

### Critical Risks

| Risk | Severity | Detail |
|---|---|---|
| TLS verification disabled | 🔴 Critical | `httpx.Client(verify=False)` in `api/main.py` and `core/ingest.py` — all LLM API calls are vulnerable to MITM attacks. Must be removed before any production use. |
| No API authentication | 🔴 Critical | All FastAPI endpoints are unauthenticated. The `/ingest` endpoint in particular could be abused to trigger expensive LLM embedding calls. |
| Session data on ephemeral local filesystem | 🔴 High | `data/sessions.json` is written to the App Service local disk. Azure App Service instances can restart, scale out, or swap slots, losing all sessions. Must migrate to Azure Blob Storage or Azure Cosmos DB. |