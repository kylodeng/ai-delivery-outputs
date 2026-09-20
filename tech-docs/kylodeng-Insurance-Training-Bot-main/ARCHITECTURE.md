# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a RAG-powered (Retrieval-Augmented Generation) AI system designed to train new insurance sales agents. It ingests Sun Life Hong Kong insurance product PDFs into a vector store and exposes two LangGraph agent modes: a **Teacher agent** that conducts interactive coaching sessions (explaining products, running exercises, quizzing trainees) and an **Assessor agent** that evaluates roleplay conversations against verified product facts. A FastAPI backend serves the agent logic and streams responses; a separate frontend application provides the chat UI. The system is deployed as two Azure App Service instances (API and frontend) via GitHub Actions CI/CD, with five auxiliary AI-powered DevOps automation workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) all powered by Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend — agent logic, RAG retrieval, session management, PDF serving |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend chat UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded or managed store | Azure (local) / Pinecone (SaaS) | Stores embedded insurance document chunks for RAG retrieval |
| `data/sessions.json` | JSON file on disk | Azure App Service filesystem | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions runners | ubuntu-latest ephemeral VMs | GitHub (Microsoft) | CI/CD for test, build, deploy; runs 5 AI DevOps automation tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub (Microsoft) | Stores AI-generated documentation artefacts (code reviews, tech docs, business docs, UAT packs) |
| Insurance PDF corpus | Static files in `data/` | Azure App Service filesystem | Source truth for product knowledge (Sun Life HK PDFs + annotation sidecar `.annot.json` files) |

> [TODO: Confirm which vector store backend is active in production — `ChromaStore`, `LocalFAISSStore`, or `PineconeStore`]

> [TODO: Confirm whether `data/` is on ephemeral App Service filesystem or a mounted Azure Files share — ephemeral means all sessions and vector store are lost on redeploy/restart]

---

## 3. Data Flow

### 3a. Document Ingestion (offline / one-time)

1. Operator places Sun Life HK insurance PDFs under `data/Insurance-product-info/`.
2. `POST /ingest` endpoint (or CLI `python core/ingest.py`) is called.
3. `core/ingest.py` walks the directory; for each PDF, `core/annotator.py` calls the LLM (via OpenRouter / Anthropic) to generate a `.annot.json` sidecar — tagging the document type, product name, and per-page relevance.
4. `core/chunker.py` splits relevant pages into semantic chunks (max ~280 words each) using heuristic heading/bullet detection.
5. Chunks are embedded in batches via a Voyage AI or OpenRouter embedding model and written into the vector store (Chroma/FAISS/Pinecone).
6. The vector store index is saved to disk (`store.save()`).

### 3b. Teacher Mode (online, streaming)

1. Trainee sends a message via the frontend chat UI (HTTP POST to FastAPI).
2. FastAPI creates or retrieves the session from `data/sessions.json`.
3. The **Teacher LangGraph agent** is invoked with the conversation history and system prompt.
4. The agent calls one or more RAG tools (`search_product`, `search_all`, `compare_plans`, etc.) against the vector store.
5. Matching chunks are retrieved; source metadata (document name, page number, file URL) is collected via `contextvars` for citation.
6. The LLM (via OpenRouter or Anthropic, streaming) generates a response with inline `[[Sn]]` citation markers.
7. The streaming response and source list are forwarded to the frontend via `StreamingResponse` (SSE).
8. The conversation turn is appended to the session and persisted to `sessions.json`.

### 3c. Roleplay + Assessment Mode (online)

1. FastAPI calls `generate_profile()` to randomly construct a Hong Kong customer persona.
2. The frontend presents the persona; the trainee conducts a freeform roleplay conversation with the **Roleplay LLM** (acting as the customer).
3. When the session ends, the trainee triggers assessment.
4. The **Assessor LangGraph agent** receives the full conversation + customer profile and calls RAG tools to verify every factual claim made by the trainee.
5. The assessor returns a structured performance report (accuracy score, feedback across five dimensions).
6. The report is streamed back and displayed in the frontend.

### 3d. CI/CD and AI DevOps Tooling (GitHub Actions)

1. Developer pushes code or opens a PR targeting `main`.
2. GitHub Actions runs `pytest` tests via `uv`.
3. On merge to `main`: `uv export` generates `requirements.txt`; `azure/webapps-deploy` publishes both App Services using publish profiles stored in GitHub Secrets.
4. On PR open/sync: Tool 1 fetches the PR diff, calls Claude (`claude-sonnet-4-6`) for a code review, posts a comment on the PR, and writes a JSON report to `ai-delivery-outputs`.
5. On merge to `main` (non-docs paths): Tool 2 fetches repo files, generates README, architecture doc, and runbook via Claude, and writes them to `ai-delivery-outputs`.
6. On version tags or manual dispatch: Tool 3 generates business documentation; Tool 4 generates or gap-analyses tests; Tool 5 generates or analyses UAT packs.
7. SendGrid sends email notifications to `kylo.deng@capco.com` after each tool run.

---

## 4. Security Posture

### ✅ What is secured

- **CI/CD secrets** — Azure publish profiles, API keys, and SendGrid key are stored as GitHub Actions Secrets and injected as environment variables; they do not appear in source code.
- **PR-gated deployment** — Deployment only triggers on push to `main` after tests pass (`needs: test`).
- **CORS restriction** — FastAPI only allows specific localhost origins in the CORS middleware (no wildcard `*` on origins).
- **Session isolation** — Each conversation has a UUID session ID.
- **LLM API key via env** — `API_KEY`, `ANTHROPIC_API_KEY` loaded from environment, not hardcoded.

### ❌ Gaps and vulnerabilities — explicit call-outs

| Gap | Severity | Detail |
|---|---|---|
| **TLS verification disabled** | HIGH | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` in `api/main.py` and `core/ingest.py` — all outbound LLM API calls skip certificate validation, making the application vulnerable to MITM attacks against the LLM provider. |
| **No authentication on API endpoints** | HIGH | FastAPI exposes `/ingest`, `/sessions`, and agent endpoints with no authentication middleware — any party who can reach the App Service URL can invoke them. |
| **Sessions stored as plaintext JSON on disk** | HIGH | `data/sessions.json` contains full multi-turn conversation history (including customer profiles and trainee inputs) with no encryption at rest. On App Service ephemeral filesystem this is also not durable. |
| **Vector store not encrypted** | MEDIUM | ChromaDB / FAISS index files are stored on the App Service filesystem with no encryption at rest layer (unless Azure encryption at the storage level applies — [TODO: confirm App Service storage encryption settings]). |
| **PDF corpus served unauthenticated** | MEDIUM | `app.mount("/docs", StaticFiles(...))` exposes all PDFs and annotation JSON files over unauthenticated HTTP — insurance product documents including pricing data are publicly accessible. |
| **No secrets scanning** | MEDIUM | No `gitleaks` or similar tool in CI to prevent accidental secret commits. |
| **GH_TOKEN scope unknown** | MEDIUM | `GH_TOKEN` is used by all five AI tooling workflows with write access to `ai-delivery-outputs` repo. Scope is not constrained in the workflow files — if over-provisioned, a compromised run could write arbitrary content to any accessible repo. [TODO: confirm GH_TOKEN is scoped to only the output repo with contents:write only] |
| **CORS allows localhost origins** | LOW | In production, localhost CORS origins (`http://localhost:5173`, `http://127.0.0.1:5173`) should be removed and replaced with the production frontend domain. |
| **No rate limiting** | LOW | No rate limiting on API endpoints — the `/ingest` endpoint in particular could trigger expensive LLM embedding calls if hit repeatedly. |
| **No input validation on user messages** | LOW | User chat messages are passed directly to the LLM without sanitisation — prompt injection is possible. |
| **AI tool workflows expose repo content to Anthropic** | LOW | Full source code is sent to Anthropic's API (`claude-sonnet-4-6`) in tool1–tool5 workflows. Any sensitive data committed to the repo (keys, PII) would be sent to a third-party API. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter or Anthropic) | App Service environment / `.env` file locally |
| `OPENAI_URL_BASE` | No | Low | App Service environment / `.env`; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | App Service environment / `.env`; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | App Service environment / `.env`; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | 🔴 High — Anthropic API key for Claude | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI tools) | 🔴 High — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | 🔴 High — SendGrid email API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure publish credentials for API app | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure publish credentials for frontend app | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI tools) | Low | GitHub Actions env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI tools) | Low | GitHub Actions env; defaults to `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI tools) | Low | GitHub Actions env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI tools) | Low | GitHub Actions env; hardcoded to `noreply@ai-delivery.capco.com` |

> [TODO: The `OPENAI_MODEL` default is `openai/gpt-oss-20b:free` in `main.py` but `claude-sonnet-4-6` in `core/ingest.py` — confirm which model is actually used for ingestion annotation vs. agent responses, and reconcile the env var]

> [TODO: Confirm whether a `.env` file is used in production on App Service or environment variables are set through the Azure portal — `.env` files committed to the repo would be a critical secret leak]

---

## 6. Dependencies

### External Services / APIs

| Dependency | Purpose | Notes |
|---|---|---|
| **OpenRouter** (`https://openrouter.ai/api/v1`) | LLM inference gateway for agent and ingestion | Default base URL; routes to GPT/OSS models |
| **Anthropic API** (`claude-sonnet-4-6`) | LLM for all 5 CI/CD AI tooling workflows (code review, docs, testing, UAT) | Also referenced as ingestion model in `core/ingest.py` |
| **Voyage AI** | Document embedding (likely; referenced in `embed_chunks` batch config) | [TODO: confirm embedding provider — could be OpenRouter-hosted or direct Voyage AI] |
| **Pinecone** | Optional managed vector store backend (`PineconeStore` in `core/vector_store.py`) | [TODO: confirm if Pinecone is used in production or if local FAISS/Chroma is used] |
| **SendGrid** | Email notification delivery for CI/CD tool outputs | Required secret: `SENDGRID_API_KEY` |
| **Azure App Service** | Hosting platform for both API and frontend | Deployment via publish profile |
| **Health Mutual Group (HMG)** | Third-party hospital network data provider referenced in insurance documents | External to this system; no API integration — data is in PDFs only |

### External Repos

| Repo | Relationship | Purpose |
|---|---|---|
| `{owner}/ai-delivery-outputs` | Write target | All AI-generated artefacts (code reviews, docs, UAT packs) are committed here by the 5 CI tooling workflows |

### Python Dependencies (key packages)

| Package | Purpose |
|---|---|
| `fastapi` | API framework |
| `langchain` / `langchain-openai` | LLM orchestration, tool binding |
| `langgraph` | Agent graph execution (teacher and assessor agents) |
| `pdfplumber` | PDF text extraction |
| `chromadb` / `faiss` | Vector store backends |
| `httpx` | Async HTTP client for LLM calls |
| `anthropic` | Anthropic SDK for CI tooling workflows |
| `python-dotenv` | Local env loading |
| `pydantic` | Data validation |
| `pytest` | Test runner |
| `uv` | Dependency management and packaging |
| `requests` | HTTP client in CI scripts |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13 (application) / 3.12 (CI tooling scripts)
- [`uv`](https://github.com/astral-sh/uv) installed locally
- Azure CLI authenticated with access to the `training-bot-api` and `training-bot-frontend` App Services
- Azure publish profiles downloaded and stored as GitHub Secrets

### Local Development Setup

```bash
# 1. Clone the repo
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install dependencies
uv sync

# 3. Create .env file (DO NOT COMMIT)
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with:
#   API_KEY=<your OpenRouter or Anthropic key>
#   OPENAI_URL_BASE=https://openrouter.ai/api/v1
#   OPENAI_MODEL=openai/gpt-oss-20b:free
#   SHOW_TOOL_CALLS=true

# 4. Ingest insurance PDFs into vector store
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# 5. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000

# 6. Start the frontend (separate terminal)
# [TODO: confirm frontend startup command — Chainlit or Vite]
uv run chainlit run frontend/app.py   # if Chainlit
# OR
cd frontend && npm install && npm run dev   # if Vite
```

### Running Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (Automated)

Deployment is fully automated via GitHub Actions on push to `main`:

```bash
# Trigger deployment by pushing to main
git push origin main

# The workflow will:
# 1. Run pytest tests
# 2. Generate requirements.txt via uv export
# 3. Deploy API to Azure App Service: training-bot-api
# 4. Deploy Frontend to Azure App Service: training-bot-frontend
```

### Manual / Emergency Deployment (Azure CLI)

```bash
# Generate requirements.txt