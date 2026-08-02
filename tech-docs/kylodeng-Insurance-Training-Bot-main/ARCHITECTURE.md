# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train insurance sales agents on Sun Life Hong Kong products. It ingests insurance product PDFs (brochures, policy documents, hospital network lists) into a vector store, exposes a FastAPI backend with two LangGraph AI agents — a **Teacher agent** for interactive coaching and an **Assessor agent** for roleplay scoring — and serves a frontend UI. A set of five auxiliary GitHub Actions workflows provide AI-powered developer tooling (code review, tech documentation, business documentation, auto test generation, and UAT facilitation) powered by Anthropic Claude. The entire system is deployed to **Azure App Service** (separate apps for API and frontend) via a CI/CD pipeline that runs tests before every deployment.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend, LangGraph agents, and RAG pipeline |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / FAISS / Pinecone) | In-process or managed index | Azure / [TODO: confirm which backend] | Stores embedded insurance document chunks for semantic retrieval |
| `sessions.json` | File on App Service filesystem | Azure | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions Runners | Ephemeral CI/CD compute | GitHub (Azure-backed) | Runs tests, builds, deploys, and AI tooling workflows |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers AI tooling workflows (code review, docs, tests, UAT) |
| OpenRouter / LLM Endpoint | External LLM API | OpenRouter (or custom) | Powers teacher and assessor agents in the application |
| SendGrid | Transactional email API | Twilio/SendGrid | Sends workflow output notifications to `kylo.deng@capco.com` |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores generated docs, test files, and audit logs from AI tooling |
| Voyage AI | Embedding API | Voyage AI | Generates embeddings for PDF chunks during ingestion |

---

## 3. Data Flow

### Application (Training Bot) Data Flow

1. **Ingestion (offline / on-demand):** An operator calls `POST /ingest`. The `ingest_directory()` function walks `data/Insurance-product-info/`, reads each PDF with `pdfplumber`, and calls the LLM annotation pipeline to classify each document and page (product name, relevance, section headers), writing `.annot.json` sidecar files to disk.
2. **Chunking:** Relevant pages are split into semantic units (headings, bullets, paragraphs ≤280 words) by `core/chunker.py`.
3. **Embedding:** `embed_chunks()` batches chunks and sends them to the embedding API (Voyage AI by default). Vectors are stored in the configured backend (ChromaDB / FAISS / Pinecone) and persisted via `store.save()`.
4. **Session creation:** A user creates a session via `POST /sessions`. The API generates a random `CustomerProfile` (HK-context names, occupations, goals) and persists it to `sessions.json`.
5. **Teacher mode chat:** The user sends a message; FastAPI calls the Teacher LangGraph agent. The agent selects from eight RAG tools (`search_product`, `search_all`, `compare_plans`, etc.), which query the vector store for relevant chunks. Chunks are assembled with source IDs (S1, S2…) and injected into the LLM context. The LLM response (with inline citations) is streamed back via `StreamingResponse` using `astream_events`.
6. **Roleplay mode:** The user interacts with a simulated customer. The `_ROLEPLAY_SYSTEM` prompt injects the customer profile. Messages are stored in the session.
7. **Assessment:** After a roleplay session, the Assessor agent is invoked (`ainvoke`) with the full conversation transcript. It uses the same RAG tools to fact-check agent claims against retrieved documents, then returns a structured performance score.
8. **PDF serving:** Static insurance PDFs under `data/` are served directly via `app.mount("/docs", StaticFiles(...))` so the frontend can link to source documents.

### CI/CD Data Flow

9. **Push to `main`:** GitHub Actions triggers the `Test & Deploy` workflow. `pytest` runs first; on pass, two parallel jobs deploy to `training-bot-api` and `training-bot-frontend` Azure App Services using publish profiles.
10. **AI tooling:** On PR open/merge/schedule, separate workflows invoke Anthropic Claude via `.github/scripts/` Python scripts, write outputs (JSON, Markdown) to the `ai-delivery-outputs` repo, post PR comments, and email results via SendGrid.

---

## 4. Security Posture

### ✅ What is secured

- **CI/CD secrets** are stored as GitHub Actions encrypted secrets (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow files.
- **LLM API key** (`API_KEY`) is wrapped in `pydantic.SecretStr` before being passed to `ChatOpenAI`, preventing accidental logging.
- **Tests must pass** before any deployment (`deploy-api` and `deploy-frontend` both `needs: test`).
- **CORS is restricted** to specific localhost origins (Vite dev server + Chainlit), not `*` in the explicit list.

### ❌ Gaps and weaknesses

- **TLS verification is explicitly disabled:** `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls. This disables certificate validation and exposes the service to **man-in-the-middle attacks** on all outbound LLM traffic. This must be remediated.
- **No API authentication on the FastAPI backend:** There is no middleware enforcing authentication (JWT, API key, OAuth) on any of the `/sessions`, `/chat`, `/ingest`, or `/docs` endpoints. The `/ingest` endpoint in particular is dangerous — any unauthenticated caller can trigger expensive LLM+embedding workloads.
- **Sessions stored in a flat JSON file on the App Service filesystem:** `sessions.json` is not encrypted at rest beyond whatever Azure App Service disk encryption provides. It contains customer profile data and full conversation history. There is no access control on this file.
- **Static file mount exposes all PDFs:** `app.mount("/docs", StaticFiles(...))` serves the entire `data/` directory over HTTP with no authentication. This may expose proprietary insurance product documents publicly.
- **CORS allows all methods and headers** (`allow_methods=["*"]`, `allow_headers=["*"]`) — while origins are restricted, this is broader than needed.
- **No encryption in transit for sessions.json** — file is written/read in plain text on local disk.
- **`GH_TOKEN` scope is unknown** — [TODO: confirm the GH_TOKEN has only the minimum required scopes (read:repo, write:issues, write:contents on output repo only)]. If it has full repo access, it is overly broad.
- **Publish profiles contain long-lived credentials** — Azure publish profiles do not expire automatically. There is no rotation policy evident.
- **AI tooling scripts accept raw GitHub API data** and pass it directly to Claude with no sanitisation — a malicious PR diff could attempt prompt injection.
- **No WAF or DDoS protection** configured on Azure App Service (no evidence of Azure Front Door or Application Gateway).
- **No secrets scanning** configured in the repository workflows.
- **`verify=False` in ingestion LLM client** (`core/ingest.py`) — same TLS bypass issue as in the main API.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter or Anthropic) | Azure App Service Application Settings / `.env` locally |
| `OPENAI_URL_BASE` | No (has default) | Low | Azure App Service Application Settings / `.env` |
| `OPENAI_MODEL` | No (has default: `openai/gpt-oss-20b:free`) | Low | Azure App Service Application Settings / `.env` |
| `SHOW_TOOL_CALLS` | No (default: `true`) | Low | Azure App Service Application Settings / `.env` |
| `ANTHROPIC_API_KEY` | Yes (tooling workflows) | 🔴 High | GitHub Actions Secret |
| `GH_TOKEN` | Yes (tooling workflows) | 🔴 High — GitHub PAT | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (tooling workflows) | 🔴 High | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (default: `ai-delivery-outputs`) | Low | GitHub Actions env / hardcoded default |
| `OUTPUT_REPO_OWNER` | No (inferred from `github.repository_owner`) | Low | GitHub Actions env |
| `NOTIFY_EMAIL` | No (default: `kylo.deng@capco.com`) | Medium — PII | Hardcoded in workflow YAML |
| `SENDER_EMAIL` | No (default: `noreply@ai-delivery.capco.com`) | Low | Hardcoded in workflow YAML |

> ⚠️ `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded as plaintext in workflow YAML files committed to the repository. While not secrets, the personal email address `kylo.deng@capco.com` is a PII concern if the repository is public.

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude** (`claude-sonnet-4-6`) | External LLM API | AI tooling workflows (code review, docs, test gen, UAT) | Paid API; key required |
| **OpenRouter** (default) or custom LLM endpoint | External LLM API | Teacher and Assessor agents in the application | Configured via `OPENAI_URL_BASE`; TLS disabled ⚠️ |
| **Voyage AI** | External Embedding API | Generating vector embeddings for PDF chunks | [TODO: confirm API key env var name — not visible in provided code] |
| **Pinecone** (optional) | Managed vector database | Alternative to local FAISS/Chroma for production | `core/vector_store.py` includes `PineconeStore` |
| **SendGrid** | Transactional email | Notifying team of workflow outputs | Twilio/SendGrid account required |
| **Azure App Service** | PaaS hosting | Runs API and frontend | Two separate App Service instances |
| **LangChain / LangGraph** | Python framework | Agent orchestration, tool calling, streaming | Core dependency |
| **FastAPI** | Python web framework | REST API layer | |
| **pdfplumber** | Python library | PDF text extraction during ingestion | |
| **ChromaDB / FAISS** | Local vector store | Default embedding index backends | Persisted to local filesystem |
| **`ai-delivery-outputs`** (repo) | GitHub Repository | Output store for AI tooling artifacts | Must exist under same GitHub org/owner |
| **`uv`** | Python package manager | Dependency management and virtualenv | Used in all CI workflows |
| **`python-dotenv`** | Python library | Loading `.env` files locally | |

---

## 7. Deployment Instructions

### Prerequisites

- Azure CLI installed and authenticated
- Two Azure App Services provisioned: `training-bot-api` and `training-bot-frontend`
- Publish profiles downloaded and stored as GitHub Secrets
- All required secrets set in GitHub repository settings

### Manual Deployment (Azure CLI)

```bash
# 1. Install dependencies and generate requirements.txt
pip install uv
uv sync
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Run tests locally before deploying
uv run pytest tests/ -v

# 3. Deploy API to Azure App Service
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip

# 4. Deploy Frontend to Azure App Service
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip

# 5. Set required environment variables on the API App Service
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <your-resource-group> \
  --settings \
    API_KEY="<your-llm-api-key>" \
    OPENAI_URL_BASE="https://openrouter.ai/api/v1" \
    OPENAI_MODEL="openai/gpt-oss-20b:free" \
    SHOW_TOOL_CALLS="true"
```

### Automated Deployment (via GitHub Actions)

```bash
# Push to main branch — tests run automatically, then deploy on success
git checkout main
git push origin main

# The following GitHub Actions secrets must be configured:
# - AZURE_WEBAPP_PUBLISH_PROFILE_API
# - AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND
# - ANTHROPIC_API_KEY
# - GH_TOKEN
# - SENDGRID_API_KEY
```

### PDF Ingestion (run after first deployment)

```bash
# Trigger ingestion via the API endpoint (once App Service is running)
curl -X POST https://training-bot-api.azurewebsites.net/ingest

# Or run locally:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

---

## 8. Risks and TODOs

### Critical Issues

| Risk | Severity | Detail |
|---|---|---|
| TLS verification disabled | 🔴 Critical | `httpx.Client(verify=False)` in `api/main.py` and `core/ingest.py` — all outbound LLM/embedding API calls skip certificate validation, enabling MITM attacks |
| No API authentication | 🔴 Critical | All FastAPI endpoints (`/ingest`, `/sessions`, `/chat`) are unauthenticated and publicly accessible if App Service has a public URL |
| `/ingest` endpoint unprotected | 🔴 Critical | Any caller can trigger full PDF re-ingestion, consuming significant LLM and embedding API quota |
| Static file mount unprotected | 🔴 High | `/docs` serves all PDFs in `data/` with no auth — proprietary insurance documents potentially publicly exposed |

### Architecture Gaps

- **No disaster recovery:** Sessions are stored in `sessions.json` on the App Service local filesystem. If the App Service is restarted, scaled, or fails over, session data is lost. There is no database backend or Azure Blob/Table Storage.
- **No monitoring or alerting:** No Application Insights, Azure Monitor, or health check endpoints are configured. There is no evidence of log aggregation or alerting on errors or latency.
- **No staging environment:** The workflow deploys directly to production on every push to `main`. There is no staging slot or blue/green deployment.
- **Single region deployment:** No evidence of multi-region deployment or Azure Traffic Manager configuration — single point of failure.
- **Vector store persistence risk:** The ChromaDB/FAISS index is saved to the local filesystem. Azure App Service local storage is ephemeral on scale-out or redeploy — the index would need to be rebuilt via `/ingest`.
- **LLM model hardcoded as free tier:** Default `OPENAI_MODEL` is `openai/gpt-oss-20b:free` — a free-tier model. This may have rate limits, availability issues, or reduced capability for production use.

### Code-Level TODOs

- [TODO: What is the embedding API key environment variable name? It is not visible in the provided source — `core/ingest.py` and `core/vector_store.py` reference Voyage AI but the key name is not confirmed.]
- [