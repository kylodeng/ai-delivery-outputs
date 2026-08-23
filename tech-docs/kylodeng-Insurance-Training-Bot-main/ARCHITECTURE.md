# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a RAG (Retrieval-Augmented Generation) application designed to train insurance sales agents at Sun Life Hong Kong. It ingests proprietary insurance product PDFs (brochures, policy documents, hospital network lists) into a vector store, then exposes two LangGraph agent modes: a **Teacher agent** for interactive coaching and concept explanation, and an **Assessor agent** for evaluating roleplay sessions against verified product knowledge. A FastAPI backend serves the agent APIs, a separate frontend application provides the user interface, and a suite of five AI-powered GitHub Actions workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) automate the software delivery lifecycle using Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend (agent API, ingest endpoint, session management) |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store | Local FAISS / Chroma / Pinecone (runtime-configured) | Local disk or Pinecone SaaS | Stores embedded insurance document chunks for RAG retrieval |
| `data/sessions.json` | File-based persistence | Azure App Service local disk | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions Runners | Ephemeral Ubuntu VMs | GitHub (Microsoft Azure) | CI/CD: test, build, deploy, and AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated docs (code reviews, tech docs, business docs, test reports, UAT packs) |
| OpenRouter / Anthropic API | External LLM API | Third-party SaaS | LLM inference for agent, annotation, and AI tooling workflows |
| SendGrid | Email delivery API | Third-party SaaS (Twilio) | Sends notification emails for AI workflow outputs |

> **[TODO: What SKU/tier is the Azure App Service plan? Is it a shared, Basic, Standard, or Premium plan? This affects scaling, SSL, and custom domain availability.]**
>
> **[TODO: Which vector store backend is used in production — LocalFAISSStore, ChromaStore, or PineconeStore? The code supports all three but the production selection is not documented.]**
>
> **[TODO: Is `data/sessions.json` persisted to Azure Storage or only to ephemeral App Service local disk? If local disk, session data is lost on restart/slot swap.]**

---

## 3. Data Flow

### 3a — Document Ingestion (one-time / on-demand)

1. A human operator places Sun Life insurance PDF files under `data/Insurance-product-info/`.
2. A `POST /ingest` API call (or CLI `python core/ingest.py`) triggers `ingest_directory()`.
3. Each PDF is processed by `pdfplumber` to extract raw page text.
4. The annotator (`core/annotator.py`) calls the configured LLM (OpenRouter/Anthropic) to classify document type, product name, and per-page relevance — results are cached to `.annot.json` sidecar files alongside each PDF.
5. Relevant pages are chunked into semantic units (headings, bullets, paragraphs) by `core/chunker.py` with a configurable `max_words` limit (~280 words/chunk).
6. Each chunk is enriched with metadata (product name, doc type, page range, file URL).
7. Chunks are embedded in batches via the configured embedding model and written to the vector store (`store.add_documents()` → `store.save()`).

### 3b — Teacher Mode (interactive training)

1. The frontend sends a user message to `POST /chat` (or equivalent streaming endpoint) on `training-bot-api`.
2. FastAPI instantiates (or retrieves) a `Session` object and appends the message to conversation history.
3. `make_teacher_agent()` constructs a LangGraph ReAct agent with the Teacher system prompt and eight RAG tools.
4. On each agent step, the agent calls one or more RAG tools (e.g., `search_product`, `compare_plans`, `lookup_exclusions`).
5. Each tool call queries the vector store for top-k relevant chunks and appends source metadata to the per-request `_sources_ctx` contextvar list.
6. The LLM (via OpenRouter or direct API) generates a response with inline citation markers (`[[S1]]`, `[[S2]]`, …).
7. The agent streams tokens back to the frontend via `StreamingResponse` using `astream_events`.
8. Source metadata collected during the request is serialised and sent as a terminal SSE event for the frontend to render as citations.
9. The session (including the new assistant turn) is persisted to `data/sessions.json`.

### 3c — Roleplay + Assessment Mode

1. The frontend requests a random `CustomerProfile` (generated from HK-context persona pools in `api/sessions.py`).
2. The user (trainee agent) has a multi-turn roleplay conversation with the simulated customer (LLM in roleplay character via `_ROLEPLAY_SYSTEM` prompt).
3. When the session ends, the frontend calls the assessment endpoint.
4. `make_assessor_agent()` receives the full conversation transcript and customer profile.
5. The Assessor agent uses the same RAG tools to **verify** every factual claim the trainee made against the knowledge base.
6. A structured performance report is returned (five assessment dimensions).

### 3d — CI/CD & AI Tooling Workflows

1. A developer opens a PR or pushes to `main`.
2. GitHub Actions triggers `deploy.yml`: runs `pytest` tests; on success, exports `requirements.txt` via `uv` and deploys both App Services using Azure Web Apps publish profiles.
3. In parallel, AI tooling workflows (`tool1`–`tool5`) trigger: each fetches repo files/diffs via GitHub API, calls Anthropic Claude, and writes outputs (JSON reports, Markdown docs, CSVs) to the `ai-delivery-outputs` repo.
4. SendGrid emails are dispatched to `kylo.deng@capco.com` with output links.
5. For PRs, Claude code review comments are posted directly back to the PR via GitHub API.

---

## 4. Security Posture

### ✅ What Is Secured

- **CI/CD secrets** — Azure publish profiles, API keys, and GitHub tokens are stored as GitHub Actions encrypted secrets and are not hardcoded in source.
- **PR-gated deployment** — The `deploy-api` and `deploy-frontend` jobs only run on pushes to `main` (not on PRs), ensuring no unreviewed code is deployed directly.
- **Test gate** — Deployment jobs have `needs: test`, so a failing test suite blocks deployment.
- **Secret typing** — The API key is wrapped in `pydantic.SecretStr` in the LLM client initialisation, preventing accidental logging.
- **LLM API key isolation** — `ANTHROPIC_API_KEY` and `API_KEY` (OpenRouter) are read from environment variables, not hardcoded.

### ❌ Gaps and Concerns

- **⚠️ TLS certificate verification disabled** — `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `api/main.py` and `core/ingest.py` for all LLM API calls. **This disables SSL/TLS certificate validation, exposing the system to man-in-the-middle attacks on all LLM API traffic, including traffic that carries insurance product data and user conversation content.** This must be fixed before any production use.
- **⚠️ No API authentication on FastAPI endpoints** — There is no evidence of API keys, JWT validation, OAuth, or any other authentication middleware on the FastAPI backend. Any caller who can reach `training-bot-api` can invoke agent and ingest endpoints freely.
- **⚠️ No input validation / rate limiting** — No rate limiting, request size limits, or prompt injection defences are visible in the FastAPI layer.
- **⚠️ CORS is overly permissive** — `allow_methods=["*"]` and `allow_headers=["*"]` are set with no credential controls. Origins are currently whitelisted to localhost only, but this will need updating for the Azure deployment and may need tightening.
- **⚠️ Session data stored in plaintext on disk** — `data/sessions.json` contains full conversation transcripts (potentially including customer PII and sensitive financial information) stored as plaintext JSON on the App Service local filesystem with no encryption at rest.
- **⚠️ Insurance PDF data served unauthenticated** — `app.mount("/docs", StaticFiles(directory=str(_DATA_DIR)))` serves all PDFs and annotation JSON files over HTTP with no authentication. Proprietary Sun Life product documents are publicly accessible to anyone who can reach the API hostname.
- **⚠️ No encryption at rest for the vector store** — FAISS/Chroma indexes are stored as local files with no encryption. They contain embedded representations of proprietary insurance documents.
- **⚠️ `GH_TOKEN` scope unknown** — The `GH_TOKEN` used in AI tooling workflows has write access to at least the `ai-delivery-outputs` repo (to commit files) and read access to source repos. The actual token scope is not defined in the workflow files. **[TODO: Confirm GH_TOKEN is a fine-grained PAT scoped to minimum required repos and permissions, not a classic PAT with broad org-level access.]**
- **⚠️ Overly broad file access in AI tooling** — `get_repo_files()` in `shared.py` fetches up to 20 source files from the repo on every workflow run and sends them to Anthropic's API. If the repo contains secrets or sensitive data in source files, they would be transmitted to a third-party LLM.
- **⚠️ No audit logging in production** — `write_audit_entry()` is referenced in tool scripts but its implementation is truncated in the provided source; it is unclear whether audit logs are persisted anywhere outside of GitHub Actions run logs.
- **No WAF or DDoS protection** — No Azure Front Door, Application Gateway, or WAF is deployed in front of the App Services.
- **No secrets scanning** — No `gitleaks`, `trufflehog`, or GitHub secret scanning configuration is visible.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 Critical — LLM API key (OpenRouter or Anthropic) | Azure App Service Application Settings / `.env` locally |
| `OPENAI_URL_BASE` | No (defaults to `https://openrouter.ai/api/v1`) | Low | Azure App Service Application Settings / `.env` |
| `OPENAI_MODEL` | No (defaults to `openai/gpt-oss-20b:free`) | Low | Azure App Service Application Settings / `.env` |
| `SHOW_TOOL_CALLS` | No (defaults to `"true"`) | Low | Azure App Service Application Settings / `.env` |
| `ANTHROPIC_API_KEY` | Yes (GitHub workflows) | 🔴 Critical | GitHub Actions encrypted secret |
| `GH_TOKEN` | Yes (GitHub workflows) | 🔴 Critical — GitHub PAT | GitHub Actions encrypted secret |
| `SENDGRID_API_KEY` | Yes (GitHub workflows) | 🔴 Critical | GitHub Actions encrypted secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy workflow) | 🔴 Critical | GitHub Actions encrypted secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy workflow) | 🔴 Critical | GitHub Actions encrypted secret |
| `OUTPUT_REPO` | No (defaults to `ai-delivery-outputs`) | Low | GitHub Actions workflow env |
| `OUTPUT_REPO_OWNER` | No (defaults to `GITHUB_REPOSITORY_OWNER`) | Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (defaults to `kylo.deng@capco.com`) | Low | GitHub Actions workflow env |
| `SENDER_EMAIL` | No (defaults to `kylo.deng@capco.com`) | Low | GitHub Actions workflow env |

> **[TODO: Is `API_KEY` for OpenRouter or Anthropic direct? The default base URL points to OpenRouter but `core/ingest.py` defaults to `https://api.anthropic.com/v1`. These may be different keys pointing to different backends, which is a misconfiguration risk.]**
>
> **[TODO: Are Azure App Service Application Settings configured for the production deployment? There is no IaC (Bicep/Terraform) that provisions these settings — they must be set manually.]**

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Azure App Service | Cloud PaaS | Hosts API and frontend | No IaC for provisioning; manual setup assumed |
| OpenRouter (`openrouter.ai/api/v1`) | External LLM API | LLM inference for agents and annotation | Free-tier model (`gpt-oss-20b:free`) is default — production suitability unknown |
| Anthropic Claude API | External LLM API | AI tooling workflows (code review, docs, testing, UAT) | Model `claude-sonnet-4-6` used in shared.py |
| Voyage AI (inferred) | External Embedding API | Document embedding | Referenced in code comments (rate-limit defaults suggest Voyage free tier); **[TODO: confirm embedding provider and key configuration]** |
| Pinecone (optional) | Vector database SaaS | Production vector store (if `PineconeStore` selected) | **[TODO: Confirm if Pinecone is used in production and where the API key is stored]** |
| SendGrid | Email delivery SaaS | Workflow notification emails | Required for all five AI tooling workflows |
| `ai-delivery-outputs` (GitHub repo) | GitHub repository | Stores all AI-generated output artefacts | Must exist under `OUTPUT_REPO_OWNER`; must be writable by `GH_TOKEN` |
| `pdfplumber` | Python library | PDF text extraction | Used in `core/chunker.py` |
| `langchain` / `langchain-openai` | Python library | LLM orchestration, tool framework | Core framework for agents and RAG tools |
| `langgraph` | Python library | ReAct agent graph construction | Used in `api/agent.py` |
| `fastapi` | Python library | HTTP API framework | Backend web framework |
| `httpx` | Python library | Async HTTP client | Used with TLS verification **disabled** |
| `chromadb` / `faiss-cpu` | Python library | Local vector store backends | Runtime selection via `VECTOR_STORE` env var (assumed) |
| `uv` | Python build tool | Dependency management and virtual environments | Used in all CI workflows |
| Sun Life Hong Kong product PDFs | Proprietary data | Knowledge base for RAG | Stored in `data/Insurance-product-info/`; copyright/licensing not addressed |

---

## 7. Deployment Instructions

### Prerequisites

- Azure CLI installed and authenticated (`az login`)
- App Services `training-bot-api` and `training-bot-frontend` already provisioned in Azure
- GitHub repository secrets configured (see Section 5)
- Python 3.13 and `uv` installed locally

### Local Development

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install dependencies
uv sync

# 3. Copy and configure environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env — set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# 4. Run document ingestion (first time or when PDFs change)
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# 5. Start the API server
uv run uvicorn api.main:app --reload --port 8000

# 6. Start the frontend (if separate)
# [TODO: frontend start command not determinable from provided files]
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (via GitHub Actions)

```bash
# Deployment is triggered automatically on push to main
git push origin main

# The deploy.yml workflow will:
# 1. Run pytest
# 2. Export requirements.txt via uv