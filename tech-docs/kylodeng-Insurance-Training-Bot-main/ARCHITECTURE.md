# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a RAG-powered (Retrieval-Augmented Generation) AI training platform designed to help new insurance agents at a Hong Kong insurer (contextually Sun Life HK) develop product knowledge and sales skills. The system ingests Sun Life product brochures and supplementary documents (PDFs), chunks and embeds them into a vector store, and exposes two LangGraph agent modes via a FastAPI backend: a **Teacher agent** that conducts interactive coaching sessions with tool-assisted product lookups, and an **Assessor agent** that evaluates roleplay conversations between the trainee and a synthetically generated customer profile. A separate frontend (Chainlit-based) provides the chat UI. Both components are deployed as Azure App Service instances via GitHub Actions CI/CD, with five additional AI-powered developer tooling workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) powered by Claude via Anthropic's API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving chat, ingestion, and session APIs |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the Chainlit/Vite frontend chat UI |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded or managed vector DB | [TODO: confirm which backend is active in production — ChromaStore, LocalFAISSStore, or PineconeStore are all present in code] | Stores embedded insurance document chunks for RAG retrieval |
| `sessions.json` | File on App Service filesystem | Azure | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions Runners | CI/CD compute (ubuntu-latest) | GitHub | Runs tests, builds, and deployments on push to `main` |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers AI delivery tooling workflows (code review, docs, testing, UAT) |
| OpenRouter / Custom LLM endpoint | External LLM API | [TODO: confirm production endpoint — `OPENAI_URL_BASE` defaults to `https://openrouter.ai/api/v1`] | Powers the Teacher and Assessor agents at runtime |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Receives AI-generated code review reports, docs, test files, and UAT packs |
| SendGrid | Email delivery API | Twilio/SendGrid | Sends notification emails for AI tooling workflow completions |
| PDF data files | Static file assets | Azure App Service (`/docs` mount) | Insurance product brochures served over HTTP for citation links |

---

## 3. Data Flow

### 3a. Document Ingestion (offline / on-demand)

1. An operator runs `POST /ingest` (or the CLI entrypoint in `core/ingest.py`) pointing at the `data/Insurance-product-info/` directory.
2. `ingest_directory()` walks all PDF files recursively.
3. For each PDF, `load_or_create_annotations()` checks for a cached `.annot.json` sidecar file; if absent, pages are extracted via `pdfplumber` and sent to the LLM (via `OPENAI_URL_BASE`) to generate structured metadata (product name, doc type, page relevance headers).
4. `extract_chunks_from_pdf()` splits relevant pages into semantic units using heuristic heading/bullet detection and word-count limits (default 280 words/chunk).
5. Each chunk dict (containing text, product metadata, page range, file URI) is batched and embedded via the vector store's embedding model.
6. Chunks are persisted to the vector store (Chroma/FAISS/Pinecone) and `store.save()` is called.

### 3b. Teacher Mode (real-time chat)

1. The Chainlit frontend sends a chat message to `POST /chat` (or equivalent streaming endpoint) on `training-bot-api`.
2. FastAPI calls `make_teacher_agent()` which constructs a LangGraph agent with the shared LLM and eight RAG tools.
3. `reset_sources()` initialises a fresh per-request source-tracking context var.
4. The LangGraph agent decides which tool(s) to call (e.g. `search_product`, `compare_plans`, `lookup_exclusions`).
5. Each tool queries the vector store for top-k similar chunks; results are deduplicated by (document, page) and assigned source IDs (S1, S2, …).
6. The agent synthesises a response with inline `[[Sn]]` citation markers and streams it back via `StreamingResponse`.
7. After streaming, source metadata is appended to the response for the frontend to render as citation links pointing to `/docs/<path>`.

### 3c. Roleplay / Assessment Mode

1. Frontend requests a new session; `generate_profile()` randomly assembles a Hong Kong customer persona from name, occupation, income, goals, risk tolerance, and personality pools.
2. The simulated customer (powered by `_ROLEPLAY_SYSTEM` prompt + LLM) engages the trainee agent in conversation.
3. When the roleplay ends, the frontend triggers assessment mode.
4. `make_assessor_agent()` receives the full conversation transcript and customer profile, and uses the same RAG tools to verify every factual claim made by the trainee.
5. An assessment report is returned (scores across five dimensions including product accuracy).
6. Session state (messages, profile, mode, title) is written to `sessions.json`.

### 3d. CI/CD & AI Tooling Workflows

1. A push to `main` triggers the `deploy.yml` workflow: tests run with `pytest`, then `azure/webapps-deploy@v3` deploys both App Services using publish profiles from GitHub Secrets.
2. PRs and weekly schedules trigger the five AI tooling workflows (tools 1–5): each fetches repo files or diffs via the GitHub API, calls Claude (`claude-sonnet-4-6`) via `ANTHROPIC_API_KEY`, and writes output to the `ai-delivery-outputs` repo via the GitHub Contents API.
3. SendGrid delivers email notifications on completion.

---

## 4. Security Posture

### Secured

- **GitHub Secrets**: All sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as encrypted GitHub repository secrets, not hardcoded.
- **Deployment gating**: Deployment jobs require the `test` job to pass and only run on pushes to `main` (not on PRs).
- **Session isolation**: Each chat session has a UUID; session data is keyed by session ID.
- **No user data persisted to external stores**: Conversation data is stored locally in `sessions.json` on the App Service.

### Not Secured / Gaps

- ⚠️ **TLS verification disabled**: Both `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used in `api/main.py` and `core/ingest.py`. This disables SSL certificate validation for all LLM API calls, exposing the system to man-in-the-middle attacks. **This must be remediated before production use.**
- ⚠️ **No API authentication on FastAPI endpoints**: No authentication middleware (API key, OAuth, JWT) is visible on any FastAPI route. Any caller with network access to `training-bot-api` can invoke `/ingest`, `/chat`, or session management endpoints.
- ⚠️ **`sessions.json` stored on App Service ephemeral filesystem**: Azure App Service instances can be recycled, losing all session history. Additionally, this file is not encrypted at rest beyond whatever Azure provides at the storage layer. No explicit encryption-at-rest configuration is present in the IaC.
- ⚠️ **CORS is overly permissive in pattern**: `allow_methods=["*"]` and `allow_headers=["*"]` are set. While origins are restricted to localhost addresses (suitable for dev), this should be locked down to the actual frontend App Service domain in production.
- ⚠️ **`GH_TOKEN` scope unknown**: The `GH_TOKEN` used by AI tooling scripts has write access to the `ai-delivery-outputs` repo and read access to source repos. The exact token scopes are not defined in the repo — [TODO: confirm GH_TOKEN has minimal required scopes: `repo:read` on source, `contents:write` on output repo only].
- ⚠️ **PDF data files served publicly**: The `data/` directory is mounted and served via `StaticFiles` at `/docs`. All insurance product PDFs are publicly accessible without authentication.
- ⚠️ **No WAF or network-level access control**: No Azure Front Door, Application Gateway, or VNet integration is defined in any IaC file. The App Services are assumed to be publicly accessible.
- ⚠️ **Encryption in transit for vector store**: If `LocalFAISSStore` is used (persisted to local disk), the index file is unencrypted. If Pinecone is used, TLS is handled by the Pinecone SDK but verify=False on httpx could interfere.
- ⚠️ **No secret rotation policy**: Publish profiles and API keys are static; no rotation mechanism is defined.
- ⚠️ **`API_KEY` (LLM key) logged**: `print(f"SHOW_TOOL_CALLS=...")` in `main.py` is benign, but the pattern of using `os.getenv` without validation means a missing `API_KEY` silently results in an empty string being passed to the LLM client.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM provider API key | App Service environment variables / `.env` file locally |
| `OPENAI_URL_BASE` | No (defaults to `https://openrouter.ai/api/v1`) | Low | App Service environment variables / `.env` |
| `OPENAI_MODEL` | No (defaults to `openai/gpt-oss-20b:free`) | Low | App Service environment variables / `.env` |
| `SHOW_TOOL_CALLS` | No (defaults to `true`) | Low | App Service environment variables / `.env` |
| `ANTHROPIC_API_KEY` | Yes (AI tooling workflows) | 🔴 High — Anthropic API key | GitHub Repository Secret |
| `GH_TOKEN` | Yes (AI tooling workflows) | 🔴 High — GitHub PAT with repo write access | GitHub Repository Secret |
| `SENDGRID_API_KEY` | Yes (AI tooling workflows) | 🔴 High — email delivery API key | GitHub Repository Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy workflow) | 🔴 High — Azure deployment credentials | GitHub Repository Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy workflow) | 🔴 High — Azure deployment credentials | GitHub Repository Secret |
| `VECTOR_STORE_BACKEND` | [TODO: confirm env var name — inferred from `get_vector_store()` factory] | Low | App Service environment variables |
| `PINECONE_API_KEY` | Conditional (if Pinecone backend) | 🔴 High | App Service environment variables / `.env` |
| `OUTPUT_REPO` | No (defaults to `ai-delivery-outputs`) | Low | GitHub Actions workflow `env` block |
| `OUTPUT_REPO_OWNER` | No (defaults to `github.repository_owner`) | Low | GitHub Actions workflow `env` block |
| `NOTIFY_EMAIL` | No (defaults to `kylo.deng@capco.com`) | Low | GitHub Actions workflow `env` block |
| `SENDER_EMAIL` | No (defaults to `noreply@ai-delivery.capco.com`) | Low | GitHub Actions workflow `env` block |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | AI tooling workflows (code review, docs, tests, UAT) | Requires `ANTHROPIC_API_KEY` |
| OpenRouter (or custom endpoint) | External LLM API | Teacher & Assessor agent inference at runtime | Defaults to `https://openrouter.ai/api/v1`; configurable via `OPENAI_URL_BASE` |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, streaming | Version pinned via `uv` lockfile |
| `langchain-openai` | Python library | OpenAI-compatible LLM client wrapper | Used for both OpenRouter and Anthropic endpoints |
| Voyage AI (inferred) | Embedding API | Document chunk embedding for vector store | [TODO: confirm embedding provider — referenced in `core` but API key env var not documented] |
| ChromaDB / FAISS / Pinecone | Vector database | Storing and retrieving embedded insurance document chunks | Backend selected by factory function; Pinecone requires additional credentials |
| `pdfplumber` | Python library | PDF text extraction during ingestion | |
| FastAPI + Uvicorn | Python framework | REST API and streaming responses | |
| Chainlit | Python/Node framework | Frontend chat UI | Deployed as `training-bot-frontend` App Service |
| Azure App Service | PaaS hosting | Runtime for API and frontend | Two instances: `training-bot-api`, `training-bot-frontend` |
| GitHub Actions | CI/CD platform | Test, build, and deploy automation | |
| `ai-delivery-outputs` | Sibling GitHub repository | Receives AI-generated documents, test files, and review reports | Must be accessible via `GH_TOKEN` |
| SendGrid | Email API | Workflow completion notifications to `kylo.deng@capco.com` | Requires `SENDGRID_API_KEY` |
| `astral-sh/uv` | Python package manager | Dependency management and virtual environment | Used in all workflows |
| `httpx` | Python HTTP client | HTTP calls to LLM endpoints (note: TLS verification disabled) | |

---

## 7. Deployment Instructions

### Prerequisites

- Azure App Services `training-bot-api` and `training-bot-frontend` must be provisioned in Azure (no Bicep/Terraform IaC exists in this repo — [TODO: add IaC for App Service provisioning]).
- Publish profiles downloaded from Azure Portal and stored as GitHub Secrets `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`.
- All other secrets configured in GitHub repository Settings → Secrets and Variables → Actions.

### Automatic Deployment (recommended)

```bash
# Push to main branch — triggers test + deploy pipeline automatically
git checkout main
git push origin main
```

The `deploy.yml` workflow will:
1. Run `pytest tests/ -v`
2. Export `requirements.txt` via `uv export --no-dev --format requirements-txt`
3. Deploy to `training-bot-api` and `training-bot-frontend` in parallel

### Manual Local Setup

```bash
# 1. Install uv
curl -Ls https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Copy and configure environment
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with required values: API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# 4. Ingest insurance documents
uv run python -m core.ingest --pdf-dir data/Insurance-product-info/ --verbose

# 5. Run the API server
uv run uvicorn api.main:app --reload --port 8000

# 6. Run the frontend (in a separate terminal)
# [TODO: confirm frontend start command — Chainlit or Vite?]
uv run chainlit run frontend/app.py   # [TODO: verify path]

# 7. Run tests
uv run pytest tests/ -v
```

### Manual Workflow Triggers (AI Tooling)

```bash
# Trigger code review on a specific PR via GitHub CLI
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger business doc generation
gh workflow run tool3_business_docs.yml \
  -f project_name="Insurance Training Bot" \
  -f release_version="1.0