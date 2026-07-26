# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train insurance sales agents at Sun Life Hong Kong. It provides two conversational modes: a **Teacher mode** (ongoing multi-turn coaching chat) and an **Assessor mode** (one-shot evaluation of a completed roleplay session). The backend is a FastAPI service that wraps a LangGraph agent with eight specialised RAG tools backed by a local vector store (FAISS or Chroma) populated from ingested insurance product PDFs. A separate frontend application is deployed alongside it. The system also includes five AI-powered CI/CD workflow tools (code review, tech docs, business docs, auto testing, UAT facilitation), all driven by Claude (Anthropic) and delivering outputs to a companion GitHub repository (`ai-delivery-outputs`).

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service | Azure | Hosts the FastAPI backend (LangGraph agent + RAG tools) |
| `training-bot-frontend` | Azure App Service | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (FAISS / Chroma / Pinecone) | Embedded / Managed Index | Local disk or Pinecone (cloud) | Stores embedded insurance PDF chunks for similarity search |
| `sessions.json` | Flat-file persistence | Azure App Service local disk | Stores active and historical chat sessions across restarts |
| `data/` directory | Static file mount | Azure App Service local disk | Serves raw PDF and annotation JSON files over HTTP via `/docs` path |
| OpenRouter / Anthropic API | External LLM API | Third-party (OpenRouter or Anthropic) | LLM inference for chat, RAG, and annotation |
| Voyage AI (implied) | Embedding API | Third-party | Generates vector embeddings for PDF chunks |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated code reviews, tech docs, business docs, test reports |
| SendGrid | Email API | Third-party (Twilio SendGrid) | Sends notification emails from CI/CD tool workflows |
| GitHub Actions Runners | CI/CD Compute | GitHub | Runs test, deploy, and AI tool workflows |

---

## 3. Data Flow

### Application Data Flow (Runtime)

1. A trainee accesses the frontend (`training-bot-frontend` App Service), which communicates with the API (`training-bot-api` App Service).
2. The frontend sends a chat message or session request via HTTP to the FastAPI backend.
3. On startup (`lifespan`), the API loads the persisted vector store from local disk and reads `sessions.json` to restore prior sessions.
4. The FastAPI handler creates or retrieves a `Session` object, then invokes the appropriate LangGraph agent (Teacher or Assessor) with the conversation history.
5. The LangGraph agent calls one or more RAG tools (e.g. `search_product`, `compare_plans`, `lookup_hospital_network`). Each tool queries the vector store for relevant PDF chunks and returns annotated text with source IDs (`S1`, `S2`, …).
6. Source metadata is tracked per-request via a `contextvars.ContextVar` (async-safe), allowing citations to be injected into the streamed response.
7. For Teacher mode, the LLM response is streamed back to the frontend via `StreamingResponse` using `astream_events`; for Assessor mode, the result is returned synchronously via `ainvoke`.
8. PDF source files are served directly to the frontend over HTTP at the `/docs` mount point so the UI can hyperlink citations to the original documents.
9. Session state is written back to `sessions.json` on disk after each turn.

### Ingestion Data Flow (Offline / On-demand)

1. An operator places insurance PDF files under `data/Insurance-product-info/`.
2. `core/ingest.py` walks the directory, calling `load_or_create_annotations()` per PDF.
3. For each PDF not yet annotated, an LLM (via OpenRouter) is called to extract document metadata and per-page relevance flags, cached to a `.annot.json` sidecar file.
4. Relevant pages are chunked by `core/chunker.py` (sentence/heading-aware splitting, max ~280 words per chunk).
5. Chunks are batched and sent to the embedding API (Voyage AI implied by rate-limit comments) in batches of 126, with configurable inter-batch delay for rate limiting.
6. Embedded vectors are stored in the local vector store (FAISS/Chroma) and saved to disk via `store.save()`.
7. Ingestion is triggered via `POST /ingest` on the FastAPI server or by running `core/ingest.py` directly.

### CI/CD AI Tool Data Flow

1. A GitHub event (push, PR, tag, schedule) triggers one of five workflow files.
2. The workflow calls the relevant Python script (e.g. `tool1_code_review.py`) which fetches source files or diffs from the GitHub API using `GH_TOKEN`.
3. The script calls the Anthropic Claude API (`claude-sonnet-4-6` model) with a structured prompt.
4. The output (Markdown report, JSON, CSV) is written to the `ai-delivery-outputs` repository via the GitHub Contents API.
5. A SendGrid email notification is sent to `kylo.deng@capco.com` with a summary and link.
6. For PR-triggered reviews, a comment is also posted directly to the PR via the GitHub Issues API.

---

## 4. Security Posture

### What Is Secured

- **CI/CD secrets** are stored as GitHub Actions encrypted secrets (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in source.
- **API keys in application code** use `pydantic.SecretStr` for the LLM API key, preventing accidental logging.
- **LLM responses parsed defensively** — JSON extraction includes fallback logic and error handling to avoid crashing on malformed output.
- **Dependency pinning** via `uv` lockfile ensures reproducible builds.
- **Tests gate deployment** — the `deploy-api` and `deploy-frontend` jobs have `needs: test`, so broken builds do not deploy.

### Security Gaps and Issues — **CRITICAL / HIGH**

- ❌ **TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py` and `core/ingest.py`. All outbound LLM API calls skip certificate validation. This exposes the application to man-in-the-middle attacks and would be non-compliant in most regulated environments (including financial services).
- ❌ **No API authentication on FastAPI endpoints**: The backend exposes `/ingest`, `/sessions`, and chat endpoints with no authentication middleware. Any client that can reach the App Service URL can ingest documents, read sessions, or invoke the LLM. There is no API key, OAuth2, or IP restriction visible in the code.
- ❌ **CORS is overly permissive in production**: `allow_methods=["*"]` and `allow_headers=["*"]` are configured. While origins are partially restricted to localhost, the wildcard methods/headers pattern is a gap.
- ❌ **Session data stored unencrypted on local disk** (`sessions.json`): Conversation history including customer profiles (names, ages, financial details) persists in plaintext on the App Service ephemeral disk. Azure App Service local disk is not encrypted by default with customer-managed keys.
- ❌ **`GH_TOKEN` scope is unknown**: The token is used to read repo files, write to `ai-delivery-outputs`, and post PR comments. If this is a classic PAT with broad `repo` scope, it is overly privileged. [TODO: confirm GH_TOKEN is scoped to minimum required permissions — ideally a fine-grained PAT with only Contents write and Pull Requests write on the two relevant repos]
- ⚠️ **PDF files served unauthenticated** over `/docs`: All ingested insurance product PDFs (potentially containing proprietary pricing and product data) are accessible at `/docs/<path>` without any authentication check.
- ⚠️ **No input validation / prompt injection protection**: User messages are passed directly into LangChain agent prompts. There is no sanitisation layer to prevent prompt injection attacks from trainees attempting to manipulate agent behaviour.
- ⚠️ **`AI_DELIVERY_OUTPUTS` repo is written to from CI**: The `GH_TOKEN` used in workflow tools can commit to a separate repository (`ai-delivery-outputs`). A compromised workflow could use this token to write malicious content to that repo.
- ⚠️ **No rate limiting** on FastAPI endpoints — the LLM API is billed per token, so unrestricted access could result in significant unexpected costs.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key (OpenRouter or Anthropic) | `.env` file (local); Azure App Service Application Settings (prod) |
| `OPENAI_URL_BASE` | No | Low | `.env` / App Service Settings; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | `.env` / App Service Settings; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | `.env` / App Service Settings; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | **High** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI tools) | **High** — GitHub PAT | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | **High** — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | **High** — Azure deploy credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | **High** — Azure deploy credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI tools) | Low | GitHub Actions env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI tools) | Low | GitHub Actions env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI tools) | Low | Hardcoded in workflow files as `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI tools) | Low | Hardcoded in workflow files as `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO: confirm] | Low | [TODO: not visible in provided files — check `core/vector_store.py`] |
| `PINECONE_API_KEY` | Conditional | **High** — only if Pinecone backend used | [TODO: confirm where set if Pinecone is used] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| OpenRouter (`https://openrouter.ai/api/v1`) | External LLM API | Primary LLM inference for chat and RAG agents | Default; can be overridden to point at Anthropic directly |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Used by CI/CD tools (code review, docs, testing, UAT) and annotation pipeline | Billed per token |
| Voyage AI (implied) | Embedding API | Generates vector embeddings for PDF chunks | Rate-limit batch logic visible in `ingest.py`; not explicitly named in provided files |
| Pinecone | Vector DB (optional) | Cloud-hosted vector store backend | `PineconeStore` class present in `core/__init__.py`; activated by env config |
| FAISS / Chroma | Vector DB (local) | Local vector store backends | Included in `core/__init__.py`; FAISS likely default for App Service deployment |
| SendGrid | Email API | Notification emails from CI/CD tools | Required for all five tool workflows |
| GitHub API (`api.github.com`) | Version Control API | Read repo files, post PR comments, write output repo | Used by `shared.py`; requires `GH_TOKEN` |
| LangChain / LangGraph | Python framework | Agent orchestration, tool binding, message types | Core application dependency |
| FastAPI | Python framework | HTTP API server | Core application dependency |
| pdfplumber | Python library | PDF text extraction | Used by `core/chunker.py` |
| `uv` | Build tool | Python package management and lockfile | Used in all GitHub Actions workflows |
| `ai-delivery-outputs` | Companion GitHub repo | Stores AI-generated reports and documentation | Must exist under the same org/owner; written to by all five CI tools |

---

## 7. Deployment Instructions

### Prerequisites
- Azure CLI authenticated to the target subscription
- `training-bot-api` and `training-bot-frontend` App Services provisioned in Azure [TODO: no Bicep/Terraform IaC provided — provisioning must be done manually or via a separate IaC repo]
- GitHub Actions secrets configured (see Section 5)
- Python 3.13 and `uv` installed locally for local development

### Local Development

```bash
# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# Install dependencies
uv sync

# Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with your API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# Ingest PDF documents into the vector store
python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Start the API server
uvicorn api.main:app --reload --port 8000

# The API is now available at http://localhost:8000
# PDFs are served at http://localhost:8000/docs/<path>
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (via GitHub Actions)

Deployment is triggered automatically on push to `main` after tests pass:

```bash
git push origin main
# This triggers:
#   1. jobs/test → runs pytest
#   2. jobs/deploy-api → deploys to Azure App Service 'training-bot-api'
#   3. jobs/deploy-frontend → deploys to Azure App Service 'training-bot-frontend'
```

### Manual Deployment (without CI)

```bash
# Generate requirements.txt from lockfile
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API using Azure CLI
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy frontend
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### Trigger AI CI/CD Tools Manually

```bash
# Code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Generate business documentation for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Insurance Training Bot" \
  -f release_version="1.0.0"

# Generate UAT test pack
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

---

## 8. Risks and TODOs

### Critical Risks

| Risk | Severity | Detail |
|---|---|---|
| TLS verification disabled | **Critical** | `verify=False` on all outbound HTTP clients. Must be fixed before any regulated or production use. Insurance data transiting to LLM APIs without certificate validation. |
| No API authentication | **Critical** | FastAPI has no auth middleware. The `/ingest` endpoint in particular could be abused to overwrite the knowledge base. |
| Session data unencrypted on disk | **High** | `sessions.json` contains conversation history with simulated customer profiles. Stored in plaintext on ephemeral App Service disk with