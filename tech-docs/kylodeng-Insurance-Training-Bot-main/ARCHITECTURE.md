# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is an AI-powered training platform designed to help new insurance agents (primarily in the Hong Kong market) develop product knowledge and sales skills. It consists of a FastAPI backend that exposes a Retrieval-Augmented Generation (RAG) system backed by a vector store loaded from Sun Life Hong Kong insurance product PDFs, and a frontend web application. The backend runs LangGraph agents in two modes — a **Teacher mode** for interactive guided learning and an **Assessor mode** for evaluating recorded roleplay sessions — both powered by an OpenRouter/OpenAI-compatible LLM. The system is deployed as two separate Azure App Service instances (API and frontend), with CI/CD managed via GitHub Actions. A suite of five AI-assisted developer tooling workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) runs against the repository using Anthropic's Claude API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend (RAG tools, LangGraph agents, session management, PDF ingestion endpoint) |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend web application (likely Chainlit or a Vite-based UI) |
| Vector store (Chroma / FAISS / Pinecone) | Embedded or managed vector database | Azure (local disk) / Pinecone (SaaS) | Stores PDF chunk embeddings for RAG retrieval |
| GitHub Actions runners | Ephemeral compute (ubuntu-latest) | GitHub | CI/CD: test, build, deploy, and AI tooling workflows |
| OpenRouter API | External LLM gateway | Third-party (OpenRouter.ai) | LLM inference for teacher agent, assessor agent, roleplay, and document annotation |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Third-party (Anthropic) | AI tooling workflows (code review, tech docs, business docs, auto-testing, UAT) |
| SendGrid | Email delivery SaaS | Third-party (Twilio SendGrid) | Email notifications from AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated docs, reports, and test artefacts from the five tooling workflows |

> [TODO: Confirm whether the vector store is persisted to Azure Blob Storage, Azure Files, or only to local App Service disk — local disk is ephemeral and will be lost on restart/redeploy]

> [TODO: Confirm whether PineconeStore is in active use or if ChromaStore/LocalFAISSStore is the production choice]

---

## 3. Data Flow

### 3a — Ingestion Pipeline

1. PDF documents are stored in the `data/Insurance-product-info/` directory, committed to the repository.
2. On `POST /ingest` (or a manual local run of `core/ingest.py`), the system walks the PDF directory recursively.
3. For each PDF, `core/annotator.py` calls the configured LLM (via `OPENAI_URL_BASE`) to extract document-level and page-level metadata; results are cached to `.annot.json` sidecar files alongside the PDFs.
4. `core/chunker.py` splits each relevant page's text into semantic units (headings, bullets, paragraphs) up to `max_words` per chunk.
5. `core/ingest.py:embed_chunks()` sends batches of chunks to the embedding model via the vector store's `add_documents()` call (Voyage AI, OpenAI embeddings, or Pinecone — [TODO: confirm embedding provider]).
6. The resulting index is saved to disk by `store.save()`.

### 3b — Teacher / Chat Request

1. A user sends a message from the frontend to `training-bot-api`.
2. `api/main.py` receives the request, loads the user's `Session` from `data/sessions.json`.
3. `rag_tools.py:reset_sources()` initialises a fresh per-request source tracking list via a `contextvar`.
4. The LangGraph teacher agent (`api/agent.py`) decides which RAG tool(s) to invoke (e.g. `search_product`, `compare_plans`, `lookup_exclusions`).
5. Each tool call queries the vector store for top-k semantically similar chunks, collects source metadata, and returns formatted text with inline source IDs.
6. The agent synthesises a response with citation markers (e.g. `[[S1]]`).
7. The response is streamed back to the frontend via `StreamingResponse` using `astream_events`.
8. Source metadata is appended to the response for the frontend to render document links.

### 3c — Roleplay & Assessment

1. User requests a new roleplay session; `api/sessions.py:generate_profile()` randomly constructs a `CustomerProfile` from HK-context persona data.
2. The frontend drives a multi-turn conversation; each user turn is sent to `POST /chat` (roleplay mode), which invokes the `_ROLEPLAY_SYSTEM` prompt with the profile injected.
3. When the session ends, `POST /assess` is called; the assessor agent (`make_assessor_agent`) receives the full conversation transcript and uses the same RAG tools to verify factual claims made by the trainee.
4. The assessor returns a structured evaluation JSON; the frontend renders the results.

### 3d — CI/CD (GitHub Actions)

1. Developer pushes to `main` or opens a PR.
2. `test` job runs `pytest` against the codebase using Python 3.13 and `uv`.
3. On successful tests with a push to `main`, `deploy-api` and `deploy-frontend` jobs run in parallel, each calling `azure/webapps-deploy@v3` with the respective publish profile secret.
4. Separately, AI tooling workflows (tools 1–5) execute on PR, schedule, or tag events; they call the Anthropic Claude API, write outputs to the `ai-delivery-outputs` repo, and send email notifications via SendGrid.

---

## 4. Security Posture

### Secured

- **CI/CD secrets** are stored as GitHub Actions Secrets (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and are not exposed in logs.
- **Deployment gating**: the deploy jobs have `needs: test`, ensuring tests must pass before deployment.
- **Deploy-on-push-to-main only**: deploy jobs are conditioned on `github.ref == 'refs/heads/main' && github.event_name == 'push'`, preventing accidental deploys from PRs.
- **CORS policy** is configured in FastAPI, restricting origins to known localhost dev ports.

### **⚠ Gaps and Concerns**

- **TLS verification disabled — CRITICAL**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all outbound LLM calls in `api/main.py` and `core/ingest.py`. This disables SSL/TLS certificate verification, making all LLM API traffic vulnerable to man-in-the-middle attacks. This must be removed in production.
- **No API authentication on FastAPI endpoints**: There is no authentication middleware visible on any `/chat`, `/assess`, `/ingest`, or `/sessions` endpoints. Any user with network access to `training-bot-api` can invoke these endpoints, including the `POST /ingest` endpoint which triggers expensive LLM annotation.
- **`API_KEY` falls back to empty string**: `_API_KEY = os.getenv("API_KEY", "")` — if the environment variable is missing, the LLM client is initialised with an empty key, which will silently fail at inference time rather than at startup.
- **Sessions stored as plaintext JSON on disk** (`data/sessions.json`): Session data includes customer profiles and full conversation transcripts. There is no encryption at rest. On Azure App Service, this is local ephemeral disk — data is lost on restart.
- **PDF files committed to the repository**: Insurance product documents are committed to the Git repo under `data/`. This is a compliance and IP risk if the repo is public or if these documents are not licensed for redistribution via Git.
- **`GH_TOKEN` scope is unknown**: The `GH_TOKEN` secret used by all five AI tooling workflows has write access to at least the `ai-delivery-outputs` repo. If this is a broadly-scoped Personal Access Token rather than a fine-grained token, it is overly permissive. [TODO: Confirm GH_TOKEN is a fine-grained PAT scoped only to `ai-delivery-outputs` with minimum required permissions]
- **No rate limiting or input sanitisation** on the FastAPI endpoints visible in the code.
- **Encryption at rest for the vector store**: No encryption is configured for the Chroma/FAISS index files on App Service disk. [TODO: If Pinecone is used, confirm encryption-at-rest settings on the Pinecone project]
- **Azure App Service configuration not defined in IaC**: There are no Bicep, Terraform, or ARM templates in the repository. App Service configuration (SKU, always-on, HTTPS-only enforcement, managed identity, VNet integration) is entirely unauditable from the codebase. [TODO: Add IaC for Azure resources]
- **No Azure Managed Identity usage observed**: API keys appear to be passed as environment variables rather than resolved from Azure Key Vault via Managed Identity.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM API key for OpenRouter/Anthropic | Azure App Service environment variables |
| `OPENAI_URL_BASE` | No (default: `https://openrouter.ai/api/v1`) | Low | Azure App Service environment variables |
| `OPENAI_MODEL` | No (default: `openai/gpt-oss-20b:free`) | Low | Azure App Service environment variables |
| `SHOW_TOOL_CALLS` | No (default: `true`) | Low | Azure App Service environment variables |
| `ANTHROPIC_API_KEY` | Yes (tooling workflows only) | **High** — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (tooling workflows only) | **High** — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (tooling workflows only) | **High** — SendGrid email API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy workflow) | **High** — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy workflow) | **High** — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (default: `ai-delivery-outputs`) | Low | GitHub Actions env / workflow env block |
| `OUTPUT_REPO_OWNER` | No (default: `github.repository_owner`) | Low | GitHub Actions env / workflow env block |
| `NOTIFY_EMAIL` | No (default: `kylo.deng@capco.com`) | Medium — PII (email address) | GitHub Actions env / workflow env block |
| `SENDER_EMAIL` | No (default: `noreply@ai-delivery.capco.com`) | Low | GitHub Actions env / workflow env block |

> **⚠ Note**: `NOTIFY_EMAIL` (`kylo.deng@capco.com`) is hardcoded in plaintext in all five workflow YAML files. While not a secret, personal email addresses should not be hardcoded in source-controlled files — use a repository variable instead.

---

## 6. Dependencies

### External Services / APIs

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| OpenRouter (`https://openrouter.ai/api/v1`) | LLM gateway | Chat completions for teacher agent, assessor, roleplay, and annotation | Default `gpt-oss-20b:free` model — free tier may have rate limits |
| Anthropic Claude API (`claude-sonnet-4-6`) | LLM API | All five AI tooling GitHub Actions workflows | Used via `anthropic` Python SDK |
| Voyage AI / OpenAI Embeddings | Embeddings API | Generating vector embeddings during ingest | [TODO: Confirm which embedding provider is configured — `VoyageEmbeddings` vs `OpenAIEmbeddings` in `core/vector_store.py`] |
| Pinecone | Managed vector database | Optional vector store backend (`PineconeStore`) | [TODO: Confirm if Pinecone is used in production or only ChromaStore/FAISS] |
| SendGrid (Twilio) | Email API | Notifications from tooling workflows | Used via REST API in `shared.py` |
| Azure App Service | PaaS hosting | Runs API and frontend apps | Deployed via `azure/webapps-deploy@v3` GitHub Action |
| Health Mutual Group Limited (HMG) | Third-party data | Global cashless hospital network data | Referenced in PDF data; no direct API integration |
| Sun Life Hong Kong | Content provider | Source of insurance product PDFs in `data/` | Static documents; no live API integration |

### GitHub Repositories

| Repo | Relationship | Purpose |
|---|---|---|
| `kylodeng/Insurance-Training-Bot-main` | This repo | Source code, IaC (CI/CD only), data |
| `{owner}/ai-delivery-outputs` | External output repo | Receives AI-generated docs, test reports, UAT packs from tooling workflows |

### Python Dependencies (Key Libraries)

- `langchain`, `langchain-openai`, `langchain-core` — LLM orchestration
- `langgraph` — Agent graph execution
- `fastapi`, `uvicorn` — API server
- `pdfplumber` — PDF text extraction
- `chromadb` or `faiss-cpu` — Vector store backend
- `anthropic` — Claude API client (tooling workflows)
- `httpx` — Async HTTP client
- `pydantic` — Data validation
- `pytest` — Test framework
- `uv` — Python package/dependency manager

---

## 7. Deployment Instructions

### Prerequisites
- Azure CLI authenticated (`az login`)
- Azure App Services `training-bot-api` and `training-bot-frontend` pre-provisioned [TODO: No IaC exists for provisioning these — must be created manually or via Azure Portal]
- Publish profiles downloaded from Azure Portal and stored as GitHub Secrets
- Required environment variables set on both App Services via Azure Portal → Configuration → Application Settings

### Automated Deployment (Recommended)
```bash
# Deployment is triggered automatically on push to main branch
# after all tests pass:
git push origin main
```

### Manual Deployment — API

```bash
# 1. Install uv
pip install uv

# 2. Generate requirements.txt from lockfile
uv export --no-dev --format requirements-txt -o requirements.txt

# 3. Deploy to Azure App Service using Azure CLI
az webapp deploy \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-api \
  --src-path . \
  --type zip
```

### Manual Deployment — Frontend
```bash
uv export --no-dev --format requirements-txt -o requirements.txt

az webapp deploy \
  --resource-group <YOUR_RESOURCE_GROUP> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

### PDF Ingestion (One-time / On data update)
```bash
# Local ingestion
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# Or via API endpoint (once deployed)
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest
```

### Running Tests Locally
```bash
uv sync
uv run pytest tests/ -v
```

---

## 8. Risks and TODOs

### Critical Risks

| Risk | Severity | Detail |
|---|---|---|
| TLS verification disabled | **CRITICAL** | `httpx.Client(verify=False)` in `main.py` and `ingest.py` exposes all LLM API traffic to MitM attacks. Remove in production. |
| No API authentication | **HIGH** | FastAPI endpoints (including `/ingest`) are unauthenticated. Anyone with the App Service URL can trigger expensive LLM calls or read session data. |
| Ephemeral vector store and session data | **HIGH** | Both `sessions.json`