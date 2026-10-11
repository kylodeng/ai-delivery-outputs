# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train new insurance sales agents at Sun Life Hong Kong. It provides two modes: a **Teacher mode**, in which a LangGraph-powered agent engages the trainee in interactive coaching sessions using verified product knowledge retrieved from a vector store of ingested insurance PDFs; and a **Roleplay/Assessment mode**, in which the agent simulates a realistic Hong Kong customer profile and subsequently evaluates the trainee's accuracy, rapport, and product knowledge against a structured rubric. The system exposes a FastAPI backend and a separate frontend, both deployed to Azure App Service via GitHub Actions CI/CD, with PDF documents chunked, annotated, and embedded into a vector store (ChromaDB, FAISS, or Pinecone) at ingest time.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend; serves LangGraph agent, RAG tools, and REST API |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / LocalFAISSStore / PineconeStore) | Embedded / External Service | Local disk or Pinecone Cloud | Stores embedded PDF chunks for RAG retrieval |
| GitHub Actions Runners | Managed CI/CD Compute | GitHub (Microsoft) | Runs tests, builds, and deploys on push to `main` |
| `ai-delivery-outputs` GitHub Repo | Source Code Repository | GitHub | Stores AI-generated code reviews, tech docs, business docs, test reports |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers code review, tech docs, business docs, auto-testing, UAT tools |
| OpenRouter / OpenAI-compatible API | External LLM API | OpenRouter (or configurable) | Powers the teacher and assessor agents inside the running application |
| SendGrid | Email API | Twilio/SendGrid | Sends notification emails for AI delivery tool outputs |
| `data/sessions.json` | File-based session store | Azure App Service local disk | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | Static file directory | Azure App Service local disk | Stores source PDFs and `.annot.json` sidecar annotation caches |

---

## 3. Data Flow

### 3.1 Document Ingestion (one-time / on-demand)

1. An operator calls `POST /ingest` (or runs `core/ingest.py` directly) pointing at `data/Insurance-product-info/`.
2. `ingest_directory()` walks the PDF directory recursively and, for each PDF, calls `load_or_create_annotations()` — checking for a cached `.annot.json` sidecar first.
3. If no cache exists, the LLM (configured via `OPENAI_URL_BASE` / `API_KEY`) is invoked to produce document-level and page-level annotations (product name, relevance, section headers).
4. Annotated page text is passed to `extract_chunks_from_pdf()`, which splits pages into semantic units (headings, bullets, paragraphs) capped at `max_words` (default 280).
5. Chunks (with metadata: product name, page range, section title, file URL) are batched and embedded via the vector store's embedding model (Voyage AI or equivalent).
6. The vector store index is saved to disk (`store.save()`).

### 3.2 Teacher Mode Chat Request

1. Frontend sends a `POST /chat` (or equivalent streaming endpoint) with the session ID and user message.
2. `api/main.py` retrieves the session from the in-memory + `sessions.json` store.
3. A LangGraph teacher agent is instantiated with the shared `_llm` (`ChatOpenAI` pointing at OpenRouter) and the eight RAG tools.
4. The agent decides which RAG tool to invoke (e.g. `search_product`, `compare_plans`, `lookup_exclusions`).
5. The RAG tool queries the vector store (similarity search), collects source metadata via `_collect_sources()` using a `contextvars.ContextVar` for async-safe per-request source tracking.
6. Retrieved chunks are injected into the agent's context; the LLM generates a response with inline citations (`[[S1]]`, `[[S2]]`).
7. The response is streamed back to the frontend via `StreamingResponse` using `astream_events`.
8. Source metadata is appended to the response so the frontend can render document links (served from `/docs/` via `StaticFiles`).

### 3.3 Roleplay & Assessment Mode

1. Frontend requests a new session; `generate_profile()` randomly selects a customer profile from HK-contextualised data pools.
2. The roleplay system prompt instantiates the customer persona; the LLM simulates the customer.
3. When the roleplay ends, the frontend triggers the assessor agent via `ainvoke`.
4. The assessor agent receives the full conversation transcript and uses the same RAG tools to verify every factual claim the trainee made.
5. A structured assessment JSON is returned and rendered in the frontend.

### 3.4 CI/CD & AI Delivery Tools

1. On push to `main`, GitHub Actions runs `pytest` tests.
2. On success, `deploy-api` and `deploy-frontend` jobs use `azure/webapps-deploy@v3` with publish profiles from GitHub Secrets to deploy both App Services.
3. On PR open/sync, `tool1_code_review.py` fetches the PR diff via GitHub API, calls the Anthropic Claude API, and posts a structured review comment back to the PR; the JSON report is written to the `ai-delivery-outputs` repo and emailed via SendGrid.
4. On merge to `main`, `tool2_tech_docs.py` regenerates README, architecture doc, and runbook in the output repo.
5. On version tag push, `tool3_business_docs.py` generates a business solution overview.
6. On PR file changes, `tool4_auto_testing.py` generates or gap-analyses test files.
7. On release branch creation, `tool5_uat.py` generates a UAT test pack CSV.

---

## 4. Security Posture

### ✅ What Is Secured

- **CI/CD secrets** (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions Secrets and not hard-coded in source.
- **Dependency management** uses `uv` with a lockfile, reducing supply-chain drift.
- **AI tool outputs** are written to a separate `ai-delivery-outputs` repo, isolating generated artefacts from source code.
- **Per-request source isolation** in `rag_tools.py` uses `contextvars.ContextVar` to prevent source list bleed between concurrent async requests.
- **Branch protection** is partially implied: the deploy jobs only run on `refs/heads/main` pushes, and PRs trigger tests first.

### ❌ Gaps and Risks — Be Honest

- **TLS certificate verification is explicitly disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `api/main.py` and `core/ingest.py`. This disables HTTPS certificate validation for all LLM API calls, leaving the application vulnerable to man-in-the-middle attacks against the OpenRouter/LLM API. **This is a critical security gap.**
- **No API authentication on the FastAPI backend**: No API key, OAuth, or session authentication middleware is visible in `api/main.py`. Any client that can reach the Azure App Service URL can invoke the agent and consume LLM quota.
- **Session store is a plain JSON file** (`data/sessions.json`) on local disk with no encryption at rest. Conversation transcripts (which may contain customer profile data) are stored in cleartext.
- **PDF files are served unauthenticated** via `StaticFiles` mounted at `/docs/`. Anyone with the App Service URL can download raw insurance product PDFs.
- **`sessions.json` and vector store index on local App Service disk**: Azure App Service local storage is ephemeral by default — data is lost on restarts/scaling unless Azure Files or a persistent mount is configured. [TODO: Confirm whether persistent storage is configured for the App Service]
- **No encryption at rest for vector store**: The ChromaDB or FAISS index is stored on local disk without encryption.
- **IAM / RBAC**: The publish profiles grant deployment rights. No least-privilege Azure RBAC is evident — [TODO: Confirm the App Service identity and what Azure resources it has access to].
- **No secrets scanning** in the CI pipeline beyond GitHub's default push protection.
- **`GH_TOKEN` scope is unknown**: The token is used to write to the output repo and post PR comments. [TODO: Confirm the token scope — it should be scoped to only the required repos and permissions, not a full `repo` scope PAT].
- **Email sender domain** (`noreply@ai-delivery.capco.com`) — [TODO: Confirm SPF/DKIM/DMARC records are configured to prevent spoofing].
- **No WAF or DDoS protection** on the Azure App Services is evident from the IaC provided.
- **CORS policy** allows `localhost` origins in production code (`http://localhost:5173`, `http://localhost:8000`) — these should be removed for production deployments.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key | App Service App Settings / `.env` |
| `OPENAI_URL_BASE` | No | Low | App Service App Settings / `.env` (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | App Service App Settings / `.env` (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | App Service App Settings / `.env` (default: `true`) |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | 🔴 High — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI tools) | 🔴 High — GitHub PAT | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | 🔴 High — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |

> ⚠️ **`NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded in workflow files** — not parameterised as secrets. Any contributor can see the notification email address. This is low sensitivity but indicates a pattern of mixing config into IaC.

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | AI delivery tools (code review, docs, testing, UAT) | Billed per token; no fallback if unavailable |
| OpenRouter (or compatible OpenAI API) | External LLM API | Teacher/assessor agent, document annotation | Configured via `OPENAI_URL_BASE`; `verify=False` — see security gap |
| LangChain / LangGraph | Python library | Agent orchestration, tool calling, streaming | Core framework dependency |
| `langchain-openai` | Python library | ChatOpenAI wrapper for LLM calls | |
| Voyage AI (implied) | External Embedding API | PDF chunk embeddings | Referenced in `embed_chunks` batch settings; [TODO: Confirm embedding provider and model] |
| ChromaDB / FAISS / Pinecone | Vector store | RAG document retrieval | Backend selectable via `get_vector_store()`; [TODO: Confirm which backend is used in production] |
| `pdfplumber` | Python library | PDF text extraction | |
| FastAPI + Uvicorn | Python framework | REST API and streaming backend | |
| SendGrid API | External Email API | Notification emails from CI tools | |
| GitHub API (`api.github.com`) | External REST API | PR comments, repo file reads, output repo writes | Used by all five AI delivery tool scripts |
| `ai-delivery-outputs` GitHub Repo | External repository | Stores generated artefacts (docs, reviews, test reports) | Must exist and be writable by `GH_TOKEN` |
| Azure App Service | Cloud PaaS | Hosting for API and frontend | Two separate App Service instances |
| `uv` (Astral) | Build tool | Python dependency management and packaging | Used in CI/CD |
| `httpx` | Python library | Async HTTP client for LLM calls | `verify=False` set — see security gap |

---

## 7. Deployment Instructions

### Prerequisites

- Azure CLI authenticated with appropriate subscription access
- Both App Services (`training-bot-api`, `training-bot-frontend`) already provisioned in Azure
- GitHub repository secrets configured (see Section 5)
- Python 3.13 and `uv` installed locally

### Initial Vector Store Ingestion (run once before first deploy)

```bash
# Install dependencies
uv sync

# Set environment variables
export API_KEY="<your-llm-api-key>"
export OPENAI_URL_BASE="https://openrouter.ai/api/v1"
export OPENAI_MODEL="openai/gpt-oss-20b:free"

# Run ingestion against the PDF directory
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### Local Development

```bash
# Install all dependencies including dev
uv sync

# Copy and populate environment file
cp .env.example .env   # [TODO: Confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000

# Start the frontend (separate terminal)
# [TODO: Confirm frontend start command — Chainlit or Vite]
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### CI/CD Deployment (Automatic)

Deployment is fully automated via GitHub Actions on push to `main`:

```bash
git checkout main
git merge <your-feature-branch>
git push origin main
# GitHub Actions will:
# 1. Run pytest (test job)
# 2. On success: deploy API to training-bot-api (deploy-api job)
# 3. On success: deploy frontend to training-bot-frontend (deploy-frontend job)
```

### Manual Deploy (if CI is unavailable)

```bash
# Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API via Azure CLI
az webapp deployment source config-zip \
  --resource-group <resource-group> \
  --name training-bot-api \
  --src <zip-file>

# Deploy Frontend via Azure CLI
az webapp deployment source config-zip \
  --resource-group <resource-group> \
  --name training-bot-frontend \
  --src <zip-file>
```

### Trigger AI Delivery Tools Manually

```bash
# Trigger code review on a PR
# Go to: Actions → "Tool 1 — Code Review" → Run workflow
# Set review_mode=pr, pr_number=<number>

# Trigger tech docs regeneration
# Go to: Actions → "Tool 2 — Tech Documentation" → Run workflow

# Trigger business docs on a release tag
git tag v1.0.0
git push origin v1.