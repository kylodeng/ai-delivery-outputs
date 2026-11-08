# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is an AI-powered insurance agent training platform designed for Sun Life Hong Kong insurance products. It provides two modes of interaction: a **teacher mode** for guided learning of insurance concepts, products, and sales techniques, and a **roleplay/assessment mode** where trainees practice sales conversations against a simulated customer persona and receive scored feedback. The system ingests insurance product PDFs (brochures, hospital lists, policy documents) into a vector store, exposes a RAG (Retrieval-Augmented Generation) API via FastAPI, and serves a frontend UI — both deployed as Azure App Service instances. A suite of five AI-powered GitHub Actions workflows (Claude-based code review, tech docs, business docs, auto-testing, and UAT facilitation) supports the development lifecycle.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving RAG queries, session management, and LLM streaming |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded / External DB | Azure (local) or Pinecone (SaaS) | Stores embedded insurance document chunks for similarity search |
| `data/sessions.json` | File-based session store | Azure App Service filesystem | Persists multi-turn conversation sessions across restarts |
| `data/Insurance-product-info/` | Static file directory | Azure App Service filesystem | Stores source PDFs served over `/docs` HTTP mount |
| GitHub Actions Runners | Managed CI/CD | GitHub (ubuntu-latest) | Test, build, and deploy pipeline; AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Receives AI-generated docs, test files, and audit logs from workflows |
| OpenRouter / Anthropic Claude | External LLM API | Third-party (SaaS) | Powers teacher agent, assessor agent, annotation, and all 5 CI/CD AI tools |
| Voyage AI (inferred) | External Embedding API | Third-party (SaaS) | Embeds document chunks into the vector store |
| SendGrid | Email API | Third-party (SaaS) | Sends notification emails from CI/CD AI workflows |

---

## 3. Data Flow

### 3.1 Document Ingestion (offline / on-demand)

1. An operator places insurance PDF files in `data/Insurance-product-info/`.
2. `POST /ingest` triggers `core/ingest.py → ingest_directory()`, which walks the PDF directory recursively.
3. Each PDF is passed to `core/annotator.py`, which calls the configured LLM (Claude/OpenRouter) with `_DOC_PROMPT` and `_PAGE_PROMPT` to extract metadata (product name, doc type, relevance per page). Results are cached to a `.annot.json` sidecar file alongside each PDF.
4. Relevant pages are chunked by `core/chunker.py` into semantic units (headings, bullets, paragraphs) with a configurable max-word limit (~280 words).
5. Chunks are batched and embedded via Voyage AI (inferred from `embed_chunks` with rate-limit hints) and written to the vector store (ChromaDB, local FAISS, or Pinecone, selected via env config).
6. The vector store index is saved to disk.

### 3.2 Teacher Mode (real-time chat)

1. A user sends a message via the frontend UI.
2. The FastAPI backend receives the request; `api/sessions.py` loads the session state from `sessions.json`.
3. `api/agent.py` constructs a LangGraph teacher agent with the shared LLM (`ChatOpenAI` pointed at OpenRouter or Anthropic) and eight RAG tools from `api/rag_tools.py`.
4. The agent calls the appropriate RAG tool(s) (e.g. `search_product`, `lookup_hospital_network`), which query the vector store with similarity search.
5. Retrieved document chunks are assembled with source citations (S1, S2, …) tracked via `contextvars`.
6. The LLM streams a response token-by-token back to the frontend via `StreamingResponse`.
7. Source metadata (document name, page, file URL) is appended to the response so the UI can render clickable citations linking to PDFs served from `/docs`.

### 3.3 Roleplay / Assessment Mode

1. User initiates roleplay; `generate_profile()` in `api/sessions.py` randomly selects a customer profile (name, occupation, age, goals, personality) from Hong Kong-context lists.
2. A `SystemMessage` incorporating the full profile is prepended to the conversation; the LLM roleplays as the customer persona.
3. The trainee conducts a multi-turn sales conversation; messages are stored in session state.
4. When the session ends, `make_assessor_agent()` is invoked with the full conversation transcript and customer profile.
5. The assessor uses the same eight RAG tools to verify factual claims made by the trainee against the knowledge base.
6. A scored assessment report (across five dimensions) is returned to the user.

### 3.4 CI/CD and AI Tooling

1. A push to `main` or a PR triggers `deploy.yml`: tests run via `pytest`, then `azure/webapps-deploy` publishes both apps using publish profiles stored as GitHub secrets.
2. PRs additionally trigger `tool1_code_review.yml`: the diff is sent to Anthropic Claude for structured JSON review, posted as a PR comment, and written to `ai-delivery-outputs`.
3. Pushes to `main` trigger `tool2_tech_docs.yml`: repo files are read via GitHub API, three documents (README, ARCHITECTURE, RUNBOOK) are generated by Claude, and committed to `ai-delivery-outputs`.
4. Version tags trigger `tool3_business_docs.yml`: a business solution overview and gap questionnaire are generated and emailed via SendGrid.
5. PRs touching source files trigger `tool4_auto_testing.yml`: Claude generates test files and writes them to `ai-delivery-outputs`.
6. Creation of `release/*` branches triggers `tool5_uat.yml`: Claude generates a UAT test pack (CSV-formatted scenarios) or analyses completed test results and produces a go/no-go defect report.

---

## 4. Security Posture

### ✅ Secured

- **GitHub Secrets**: All sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions secrets and not hardcoded in workflow files.
- **CI gating**: Deployment to Azure is blocked unless the `test` job passes first.
- **Publish profiles**: Azure deployment uses publish profiles (scoped credentials) rather than a subscription-level service principal — limits blast radius.
- **CORS**: The FastAPI backend explicitly whitelists only known origins (`localhost:5173`, `localhost:8000`); wildcard `*` is not used.
- **Source citation isolation**: Per-request source tracking uses `contextvars` to prevent cross-request data leakage in async contexts.

### ❌ Gaps and Risks

- **TLS verification explicitly disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py` and `core/ingest.py`. **This disables SSL certificate validation for all outbound LLM API calls, exposing the system to man-in-the-middle attacks.** This must be remediated before production use.
- **No authentication on the API**: The FastAPI backend has no authentication middleware. Any caller who can reach `training-bot-api` can invoke all endpoints, including `POST /ingest` (which rewrites the vector store) and all session endpoints. [TODO: Add API key, OAuth2, or Azure AD authentication.]
- **Sessions stored in a flat JSON file**: `sessions.json` is written to the App Service filesystem. It is not encrypted at rest beyond whatever Azure applies to the managed disk. Session data includes conversation histories and customer profiles. [TODO: Migrate to Azure Cosmos DB or Azure SQL with encryption-at-rest enabled.]
- **No encryption-at-rest configuration confirmed**: No IaC (Terraform, Bicep, ARM) is present in the repository. It cannot be verified whether the App Service instances are configured with HTTPS-only, managed identity, or Key Vault integration. [TODO: Add Bicep/Terraform to codify App Service settings including HTTPS-only and TLS minimum version.]
- **API key stored in environment variable as plain string**: `_API_KEY = os.getenv("API_KEY", "")` is used directly. In production, this should be sourced from Azure Key Vault via managed identity.
- **`GH_TOKEN` scope unknown**: The `GH_TOKEN` secret used by AI tooling workflows has write access to `ai-delivery-outputs` repo. If over-scoped (e.g. `repo` scope rather than `contents: write` on a single repo), it could be used to modify other repositories. [TODO: Audit GH_TOKEN scopes; prefer a fine-grained PAT.]
- **PDFs served unauthenticated**: `app.mount("/docs", StaticFiles(...))` serves all source insurance PDFs (potential commercial/confidential documents) over HTTP with no authentication check.
- **No secrets scanning**: No `gitleaks` or GitHub secret scanning step is present in the CI pipeline.
- **Annotation LLM uses `verify=False`**: `_build_ingest_llm()` in `core/ingest.py` also disables TLS verification.
- **No rate limiting**: The FastAPI API has no rate limiting, leaving it open to abuse if exposed publicly.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter or Anthropic) | Azure App Service environment / `.env` file locally |
| `OPENAI_URL_BASE` | No | Low | Azure App Service environment / `.env` file; default: `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | Azure App Service environment / `.env` file; default: `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | Azure App Service environment / `.env` file; default: `true` |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | 🔴 High | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI tools) | 🔴 High — GitHub PAT with write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI tools) | Low | GitHub Actions workflow env; default: `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI tools) | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI tools) | Low | GitHub Actions workflow env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI tools) | Low | GitHub Actions workflow env; hardcoded to `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO: confirm] | Low | [TODO: not visible in provided files — confirm how ChromaStore vs FAISS vs Pinecone is selected] |
| `PINECONE_API_KEY` | Conditional | 🔴 High | [TODO: required if Pinecone store is selected; not seen in env config] |
| `VOYAGE_API_KEY` | [TODO: confirm] | 🔴 High | [TODO: Voyage AI embedding inferred from rate-limit comments but key not shown] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude** (claude-sonnet-4-6) | External LLM API | Powers all 5 CI/CD AI tools (code review, docs, testing, UAT) | Accessed via `anthropic` Python SDK |
| **OpenRouter / Anthropic** | External LLM API | Powers teacher agent, assessor agent, and document annotation | Configured via `OPENAI_URL_BASE`; uses `langchain_openai.ChatOpenAI` as adapter |
| **Voyage AI** (inferred) | External Embedding API | Generates vector embeddings for insurance document chunks | Rate-limit hints in `embed_chunks` suggest Voyage free-tier (3 RPM) |
| **Pinecone** (optional) | External Vector DB | Alternative to local FAISS/Chroma for production vector storage | `PineconeStore` class exists in `core/vector_store.py` |
| **LangChain / LangGraph** | Python framework | Agent orchestration, tool calling, RAG pipeline | `langchain_core`, `langchain_openai`, `langchain.agents` |
| **FastAPI** | Python framework | REST API and streaming response backend | |
| **SendGrid** | Email API | Sends AI workflow output notifications | Used in all 5 CI/CD tools via `shared.py` |
| **GitHub API** (`api.github.com`) | External API | CI tools read repo files, write to `ai-delivery-outputs`, post PR comments | Authenticated via `GH_TOKEN` |
| **Azure App Service** | Cloud PaaS | Hosts API and frontend | Deployed via `azure/webapps-deploy@v3` |
| **pdfplumber** | Python library | Extracts text from insurance PDFs for chunking | |
| **ChromaDB / FAISS** | Local vector store | Default embedded vector store for development | |
| **`ai-delivery-outputs`** | GitHub Repository (external) | Receives all AI-generated outputs (docs, tests, UAT packs) | Must exist under same GitHub org/user |
| **httpx** | Python library | HTTP client for LLM API calls | ⚠️ Used with `verify=False` |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

### Local Development Setup

```bash
# Install all dependencies (including dev)
uv sync

# Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Ingest insurance PDFs into the vector store (run once, or after adding new PDFs)
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000

# Start the frontend (separate terminal)
# [TODO: confirm frontend framework — Chainlit or Vite]
# If Chainlit:
uv run chainlit run frontend/app.py --port 5173
# If Vite:
cd frontend && npm install && npm run dev
```

### Running Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (Azure App Service via GitHub Actions)

Deployment is fully automated via `.github/workflows/deploy.yml` on push to `main`:

```
1. Push to main branch
2. GitHub Actions runs pytest (tests/ -v)
3. On test pass: uv export --no-dev --format requirements-txt -o requirements.txt
4. azure/webapps-deploy@v3 deploys to:
   - training-bot-api      (using AZURE_WEBAPP_PUBLISH_PROFILE_API secret)
   - training-bot-frontend (using AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND secret)
```

To trigger a manual deployment:
```bash
git push origin main
```

To trigger AI tooling workflows manually (via GitHub UI):
```
GitHub → Actions →