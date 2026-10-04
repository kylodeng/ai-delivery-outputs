# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is an AI-powered training platform designed to help new insurance sales agents learn product knowledge and practice client interactions. It consists of a FastAPI backend that exposes a RAG (Retrieval-Augmented Generation) pipeline over a corpus of Sun Life Hong Kong insurance product PDFs, and a frontend web application. Agents can interact with a **Teacher agent** (ongoing coaching chat) or a **Roleplay agent** (simulated customer encounters with post-session assessment). The system uses LangChain/LangGraph to orchestrate LLM tool calls, a local or cloud-hosted vector store (Chroma, FAISS, or Pinecone) for semantic search over insurance documents, and is deployed to Azure App Service via GitHub Actions CI/CD. A suite of five AI-powered DevOps automation workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) runs alongside the application using Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend / RAG API |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend web application (likely Chainlit or Vite) |
| Vector Store (Chroma/FAISS/Pinecone) | Pluggable store | Local / Azure / Pinecone | Stores and retrieves embedded insurance document chunks |
| GitHub Actions runners | CI/CD compute (ubuntu-latest) | GitHub | Runs tests, builds, deploys, and AI automation tools |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic (external) | Powers code review, tech docs, business docs, auto-testing, UAT workflows |
| OpenRouter / LLM endpoint | External LLM API | Configurable (default: OpenRouter) | Powers teacher agent, assessor agent, roleplay customer, and document annotation |
| SendGrid | Email API | Twilio/SendGrid (external) | Sends notification emails for AI workflow outputs |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores generated documentation, review reports, test files, UAT packs |
| `data/sessions.json` | File-based session store | Azure App Service local disk | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | PDF document corpus | Azure App Service local disk / repo | Source documents for RAG ingestion |

---

## 3. Data Flow

### 3a. Training Chat (Teacher Mode)

1. User sends a message via the frontend to `POST /chat` (or equivalent streaming endpoint) on `training-bot-api`.
2. The FastAPI backend loads the session from `sessions.json` and reconstructs conversation history.
3. The LangGraph **Teacher Agent** is invoked with the conversation history and the configured LLM (OpenRouter/GPT by default).
4. The agent decides which RAG tool(s) to call (e.g., `search_product`, `compare_plans`, `lookup_exclusions`).
5. The RAG tool queries the vector store (Chroma/FAISS/Pinecone) using a semantic embedding search.
6. The vector store returns ranked document chunks with metadata (product name, page, section).
7. Chunks are assembled into a source-tracked context block; source IDs (S1, S2, …) are appended.
8. The LLM generates a response grounded in retrieved chunks with inline citations.
9. The response is streamed back to the frontend via `StreamingResponse`; source references are appended.
10. The session state is updated and written back to `sessions.json`.

### 3b. Roleplay Mode

1. User requests a new roleplay session; the backend calls `generate_profile()` to randomly construct a `CustomerProfile` (Hong Kong context).
2. The LLM is prompted with the `_ROLEPLAY_SYSTEM` prompt + profile to simulate the customer.
3. The agent and user exchange messages; the customer LLM stays in character.
4. When the session ends, the **Assessor Agent** is invoked via `ainvoke` with the full conversation transcript and customer profile.
5. The Assessor calls RAG tools to verify factual claims made by the trainee.
6. A structured assessment report is returned to the user.

### 3c. Document Ingestion

1. PDFs under `data/Insurance-product-info/` are walked by `ingest_directory()`.
2. For each PDF, `load_or_create_annotations()` checks for a `.annot.json` sidecar; if absent, LLM annotates each page (relevance, header, doc metadata) and caches results.
3. Relevant pages are chunked by `extract_chunks_from_pdf()` using heuristic heading/bullet/paragraph splitting.
4. Chunks are embedded in batches via the configured embedding model and upserted into the vector store.
5. The store is saved to disk (`store.save()`).
6. Ingestion is triggered via `POST /ingest` endpoint.

### 3d. CI/CD Deployment

1. Developer pushes to `main`; GitHub Actions `test` job runs `pytest`.
2. On success, `deploy-api` and `deploy-frontend` jobs run in parallel.
3. `uv export` generates `requirements.txt` from the lockfile.
4. `azure/webapps-deploy@v3` deploys to the respective Azure App Service using a publish profile secret.

### 3e. AI DevOps Automation Workflows

1. Triggered by PR open, scheduled cron, push to `main`, or manual dispatch.
2. Each tool fetches source files from GitHub via the GitHub API (authenticated with `GH_TOKEN`).
3. Claude API (`claude-sonnet-4-6`) is called with a structured system prompt and file context.
4. Outputs are written to the `ai-delivery-outputs` repository via GitHub API.
5. SendGrid sends an email notification to `kylo.deng@capco.com` with a link to the output.
6. PR comments are posted back via GitHub API (Tool 1 only).

---

## 4. Security Posture

### ✅ What is secured

- **API keys stored as GitHub Actions secrets** — `ANTHROPIC_API_KEY`, `SENDGRID_API_KEY`, `GH_TOKEN`, `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` are all stored as encrypted GitHub secrets, not hardcoded.
- **Branch protection implied** — Deployment only triggers on `refs/heads/main` push events; PRs must pass tests first.
- **Session ID isolation** — Sessions are keyed by UUID, preventing trivial enumeration (though there is no authentication layer — see gaps).
- **Annotation caching** — `.annot.json` sidecars prevent repeated LLM calls that could leak document content unnecessarily.
- **SendGrid email sender domain** — Uses a `noreply@ai-delivery.capco.com` sender rather than a personal address for automation emails.

### ❌ Gaps and concerns

- **⚠️ TLS verification disabled** — `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are explicitly set in `api/main.py` and `core/ingest.py`. This disables SSL certificate verification for all outbound LLM API calls, exposing traffic to man-in-the-middle attacks. **This must be fixed before production use.**
- **⚠️ No authentication on the API** — There is no API key, JWT, OAuth, or any other auth mechanism on the FastAPI endpoints. Anyone who can reach `training-bot-api` can call any endpoint including `POST /ingest`.
- **⚠️ No CORS restriction in production** — `allow_origins` includes `localhost:5173` and `localhost:8000` which are dev-only origins. Production origins are not explicitly listed; a wildcard or misconfiguration could allow cross-origin requests from any domain.
- **⚠️ Session data stored on local disk** — `sessions.json` is written to the App Service local filesystem. Azure App Service local disk is ephemeral on restart/scale-out and is not encrypted at rest by default unless Azure Disk Encryption is configured separately. [TODO: Is Azure Disk Encryption enabled on the App Service plan?]
- **⚠️ PDF corpus stored on local disk / in-repo** — Insurance product PDFs (potentially proprietary) are committed to the repository or served from App Service local disk with no access controls. The `/docs` static file mount exposes all PDFs over HTTP with no authentication.
- **⚠️ No encryption for vector store** — Chroma/FAISS stores are persisted to local disk with no encryption at rest stated in the code.
- **⚠️ `GH_TOKEN` scope unknown** — The `GH_TOKEN` secret is used across five automation workflows to read source repos and write to `ai-delivery-outputs`. [TODO: What scopes does this token have? If it is a classic PAT with `repo` scope it has read/write access to all repos owned by the user.]
- **⚠️ `API_KEY` for LLM passed as plain string** — The `API_KEY` env var is wrapped in `SecretStr` from pydantic which helps prevent accidental logging, but it is read from a plain env var. [TODO: Is this stored in Azure App Service configuration secrets or a Key Vault reference?]
- **⚠️ No rate limiting** — No rate limiting on the FastAPI endpoints. The `/ingest` endpoint in particular could be abused to trigger expensive embedding operations.
- **⚠️ No input sanitisation** — User messages are passed directly into LLM prompts. While the system prompts constrain behaviour, there is no pre-processing to filter prompt injection attempts.
- **Notification email hardcoded** — `NOTIFY_EMAIL=kylo.deng@capco.com` is hardcoded in workflow YAML files (not a secret). If this needs to change, all five workflow files must be updated manually.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key (OpenRouter/Anthropic) | Azure App Service app settings / `.env` file locally |
| `OPENAI_URL_BASE` | No | Low | Azure App Service app settings / `.env`; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | Azure App Service app settings / `.env`; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | Azure App Service app settings / `.env`; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | **High** — Anthropic Claude API key | GitHub Actions secret |
| `GH_TOKEN` | Yes (CI tools) | **High** — GitHub Personal Access Token | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | **High** — SendGrid API key | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | **High** — Azure deploy credential | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | **High** — Azure deploy credential | GitHub Actions secret |
| `OUTPUT_REPO` | No (CI tools) | Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI tools) | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI tools) | Low | Hardcoded in workflow YAML as `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI tools) | Low | Hardcoded in workflow YAML as `noreply@ai-delivery.capco.com` |
| `TEST_MODE` | No (Tool 4) | Low | GitHub Actions workflow env; defaults to `generate` |

---

## 6. Dependencies

| Dependency | Type | Purpose |
|---|---|---|
| **Anthropic Claude** (`claude-sonnet-4-6`) | External LLM API | Powers all five CI/CD automation tools (code review, docs, testing, UAT) |
| **OpenRouter** (default) or any OpenAI-compatible endpoint | External LLM API | Powers teacher agent, assessor agent, roleplay customer simulation, and document annotation |
| **SendGrid** | External email API | Sends notification emails when automation workflows complete |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool calling, streaming |
| **LangChain OpenAI** (`langchain_openai`) | Python library | LLM client wrapper |
| **FastAPI** | Python framework | REST API backend |
| **pdfplumber** | Python library | PDF text extraction for ingestion |
| **Chroma / FAISS / Pinecone** | Vector store (pluggable) | Semantic search over embedded document chunks |
| **Voyage AI** (implied by ingest comments) | External embedding API | Document embedding; free-tier rate limit handling is coded (3 RPM) |
| **httpx** | Python library | Async HTTP client for LLM calls |
| **pydantic** | Python library | Data validation and secret wrapping |
| **python-dotenv** | Python library | Local `.env` file loading |
| **uv** | Python build tool | Dependency management and lockfile-based installs |
| **pytest** | Python library | Test runner |
| **GitHub API** (`api.github.com`) | External API | Repository file fetching, PR commenting, output file writing |
| **`ai-delivery-outputs`** | External GitHub repo (same owner) | Stores all AI-generated documentation and reports |
| **Azure App Service** | Cloud PaaS | Hosting for API and frontend |
| **Health Mutual Group Limited (HMG)** | Third-party network provider | Manages global cashless hospital network referenced in insurance documents |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13 installed
- `uv` installed (`pip install uv` or via `astral-sh/setup-uv`)
- Azure CLI authenticated (`az login`)
- `.env` file populated with required environment variables

### Local Development

```bash
# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# Install dependencies using uv
uv sync

# Copy and populate environment variables
cp .env.example .env  # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.

# Ingest insurance PDFs into vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info/

# Start the API server
uv run uvicorn api.main:app --reload --port 8000

# Run tests
uv run pytest tests/ -v
```

### Production Deployment (Manual)

```bash
# Generate requirements.txt from lockfile
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API to Azure App Service
az webapp deploy --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend to Azure App Service
az webapp deploy --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip

# Set environment variables on Azure App Service
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <rg-name> \
  --settings API_KEY="..." OPENAI_URL_BASE="..." OPENAI_MODEL="..."
```

### Automated Deployment (via GitHub Actions)

Push to `main` branch — the `Test & Deploy` workflow will:
1. Run `pytest` on the `test` job.
2. On success, deploy API and frontend in parallel using `azure/webapps-deploy@v3` with publish profiles stored in GitHub secrets.

```bash
git push origin main
```

### Triggering AI Automation Tools Manually

```bash
# Via GitHub CLI — trigger code review workflow
gh workflow run tool1_code_review.yml \
  --field review_mode=repo

# Trigger documentation generation
gh workflow run tool2_tech_docs.yml

# Trigger business doc for a release
gh workflow run tool3_business_docs.yml \
  --field project_name="Insurance Training Bot"