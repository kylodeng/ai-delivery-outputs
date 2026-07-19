# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train new insurance agents at Sun Life Hong Kong. It exposes two modes: a **Teacher mode** (ongoing streamed chat) in which an AI coach explains insurance concepts, quizzes the agent, and simulates exercises using a proprietary knowledge base of Sun Life product PDFs; and a **Roleplay/Assessor mode** in which the agent practices a sales conversation against a synthetic Hong Kong customer profile, after which a second LLM agent scores the agent's performance against retrieved ground-truth product facts. The backend is a FastAPI service backed by a vector store (Chroma, FAISS, or Pinecone) populated by an offline PDF ingestion pipeline; a separate frontend application is also deployed. Both services run on Azure App Service, deployed via GitHub Actions CI/CD.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving chat, RAG, and session APIs |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (Chroma / FAISS / Pinecone) | Embedded library / managed service | Local disk on App Service or Pinecone (SaaS) | Stores embedded insurance product document chunks for RAG retrieval |
| `sessions.json` | File on App Service local disk | Azure | Persists multi-turn conversation session state across server restarts |
| Insurance PDF documents | Static files served via FastAPI `/docs` mount | Azure (App Service local disk) | Source knowledge base (Sun Life product brochures, hospital lists, etc.) |
| GitHub Actions runners | Ephemeral CI/CD compute | GitHub (ubuntu-latest) | Run tests, generate docs, deploy both App Services |
| Anthropic Claude API | External LLM SaaS | Anthropic | Powers AI delivery tooling (code review, tech docs, business docs, auto-testing, UAT) |
| OpenRouter / LLM endpoint | External LLM SaaS | OpenRouter (configurable) | Powers teacher and assessor LangGraph agents at runtime |
| Voyage AI (implied) | External Embedding SaaS | Voyage AI | Embeds document chunks for vector store ingestion |
| SendGrid | External Email SaaS | Twilio SendGrid | Sends workflow notification emails |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated docs (architecture docs, code review reports, test files, UAT packs) |

---

## 3. Data Flow

### 3a — Offline Ingestion Pipeline (run manually or via API)

1. Operator calls `POST /ingest` on the API (or runs `core/ingest.py` locally).
2. `ingest_directory()` walks `data/Insurance-product-info/` recursively, finding all `.pdf` files.
3. For each PDF, `load_or_create_annotations()` checks for a sidecar `.annot.json` cache file. If absent, it calls the configured LLM (Anthropic/OpenRouter via `OPENAI_URL_BASE`) to extract product metadata and per-page relevance annotations; results are written to disk to avoid re-processing.
4. `extract_chunks_from_pdf()` uses `pdfplumber` to extract text, applies heading/bullet heuristics to split pages into semantic units, and emits chunk dicts with metadata (product name, page range, section title, file URL).
5. `embed_chunks()` sends chunk batches to the configured embedding provider (Voyage AI by default) and upserts vectors into the chosen store (ChromaDB / local FAISS / Pinecone).
6. The store is saved to disk (`store.save()`).

### 3b — Teacher Mode (streaming chat)

1. Frontend sends a `POST /chat` request with `session_id` and user message.
2. API loads the session from the in-memory session map (populated from `sessions.json` at startup).
3. API constructs a LangGraph `teacher_agent` with the eight RAG tools bound to the loaded vector store.
4. Agent streams events via `astream_events`; `reset_sources()` initialises a per-request contextvar list for source tracking.
5. When the agent decides to call a tool (e.g. `search_product`), the tool queries the vector store with similarity search, collects hit metadata, and appends source entries to the contextvar list.
6. Retrieved document chunks (with inline `[[Sn]]` citation markers) are streamed back to the frontend as Server-Sent Events.
7. After streaming completes, `get_current_sources()` reads accumulated sources; these are appended to the streamed response so the frontend can render footnotes/links.
8. Session conversation history is updated in memory and flushed to `sessions.json`.

### 3c — Roleplay / Assessment Mode

1. Frontend calls `POST /session` to create a new roleplay session; `generate_profile()` randomly selects attributes from HK-context pools to build a `CustomerProfile`.
2. During roleplay, user messages are sent to the roleplay LLM (`_ROLEPLAY_SYSTEM` prompt) which stays in character as the synthetic customer.
3. When the agent ends the roleplay session, the frontend calls `POST /assess`.
4. API constructs a LangGraph `assessor_agent` with the full conversation transcript and customer profile injected into the system prompt.
5. Assessor calls the same RAG tools to verify every factual claim the trainee made against retrieved ground-truth documents.
6. Assessor returns a structured JSON assessment (five dimensions) which is rendered in the UI.

### 3d — CI/CD and AI Tooling Workflows

1. Developer opens a PR or pushes to `main`.
2. GitHub Actions triggers `deploy.yml`: runs `pytest`, then deploys both App Services using publish profiles.
3. Separately, `tool1_code_review.yml` fetches the PR diff, calls Claude (`claude-sonnet-4-6`) to produce a structured JSON review, posts it as a PR comment, and writes a report to `ai-delivery-outputs` repo.
4. On merge to `main`, `tool2_tech_docs.yml` fetches repo source files and IaC, calls Claude three times (README, architecture doc, runbook), writes outputs to `ai-delivery-outputs`, and sends a SendGrid notification email.
5. On version tag push, `tool3_business_docs.yml` generates a business solution overview and gap questionnaire.
6. On PR or weekly schedule, `tool4_auto_testing.yml` generates or gap-analyses test files.
7. On release branch creation, `tool5_uat.yml` generates a UAT test pack or analyses completed CSV results.

---

## 4. Security Posture

### ✅ What is secured

- **CI/CD secrets** are stored as GitHub Actions encrypted secrets (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and injected as environment variables; they are not hardcoded in source.
- **Deployment gating**: production deployments only run on `push` to `main` after tests pass (`needs: test`).
- **API key passed via `SecretStr`**: the OpenRouter/LLM API key is wrapped in Pydantic `SecretStr` to prevent accidental logging.
- **CORS restricted**: only specific localhost origins and port 8000 are whitelisted during development.

### ❌ Gaps and missing controls

- **⚠️ TLS/SSL verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `main.py` and `core/ingest.py`. This disables certificate verification for ALL outbound LLM API calls, leaving the application vulnerable to man-in-the-middle attacks on API traffic carrying potentially sensitive training conversations. **This must be removed before production use.**
- **⚠️ No authentication on the FastAPI backend**: there is no middleware, API key check, or JWT validation on any API endpoint. Any user who can reach `training-bot-api.azurewebsites.net` can access all sessions, ingest documents, and read conversation history.
- **⚠️ Sessions stored in plaintext on local disk**: `sessions.json` is written to the App Service local filesystem with no encryption at rest. Conversation transcripts (which may contain customer profile data) are stored unencrypted.
- **⚠️ PDF knowledge base served unauthenticated**: `app.mount("/docs", StaticFiles(...))` exposes all PDFs and data files over public HTTP with no access control.
- **⚠️ CORS allows all methods and headers** (`allow_methods=["*"]`, `allow_headers=["*"]`), which is overly permissive even for the whitelisted origins.
- **⚠️ No encryption in transit enforced at the application layer**: Azure App Service may provide HTTPS but no redirect from HTTP to HTTPS is configured in code, and `verify=False` on the client side negates TLS benefits.
- **⚠️ `GH_TOKEN` scope unknown**: the GitHub token used by the AI tooling workflows has unknown scope. If it is a classic PAT with broad permissions, it can read/write all repos under the owner. [TODO: replace with a fine-grained PAT scoped to `ai-delivery-outputs` only]
- **⚠️ No IAM / RBAC configured for Azure resources**: no Managed Identity, no Key Vault integration, and no role assignments are defined in the IaC. Azure publish profiles (essentially long-lived credentials) are used for deployment.
- **No secrets scanning** in CI pipeline (no `truffleHog`, `gitleaks`, or GitHub secret scanning step observed).
- **No rate limiting or abuse protection** on the API endpoints.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter or Anthropic) | `.env` file locally; Azure App Service Application Settings in production |
| `OPENAI_URL_BASE` | No | Low | `.env` / App Service Settings; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | `.env` / App Service Settings; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | `.env` / App Service Settings; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (CI tooling) | 🔴 High | GitHub Actions encrypted secret |
| `GH_TOKEN` | Yes (CI tooling) | 🔴 High — GitHub PAT | GitHub Actions encrypted secret |
| `SENDGRID_API_KEY` | Yes (CI tooling) | 🔴 High | GitHub Actions encrypted secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions encrypted secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions encrypted secret |
| `OUTPUT_REPO` | No | Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | Hardcoded in workflow env as `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No | Low | Hardcoded in workflow env as `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO: confirm name] | Low | [TODO: not observed in provided files — determine how Chroma/FAISS/Pinecone is selected] |
| `PINECONE_API_KEY` | Conditional | 🔴 High | [TODO: not observed in provided files — required if Pinecone store is used] |
| `VOYAGE_API_KEY` | [TODO: confirm] | 🔴 High | [TODO: not observed in provided files — required for Voyage AI embeddings] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude (`claude-sonnet-4-6`) | External LLM API | AI delivery tooling (code review, docs, testing, UAT) | Billed per token; no fallback configured |
| OpenRouter (or configurable LLM endpoint) | External LLM API | Teacher and assessor agents at runtime | Defaults to `openai/gpt-oss-20b:free`; configurable via `OPENAI_URL_BASE` |
| Voyage AI | External Embedding API | Embedding document chunks during ingestion | Implied by rate-limiting comments (`3 RPM free tier`); [TODO: confirm API key env var name] |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, streaming | Core agent framework |
| `pdfplumber` | Python library | PDF text extraction during ingestion | |
| ChromaDB / FAISS / Pinecone | Library / SaaS | Vector store backends | Three implementations available; active one selected at runtime |
| FastAPI + Uvicorn | Python library | HTTP API server | |
| `python-dotenv` | Python library | Local env var loading | |
| `httpx` | Python library | Async HTTP client for LLM calls | TLS verification disabled — see Security section |
| SendGrid | External Email SaaS | Notification emails from CI tooling | |
| GitHub API (`api.github.com`) | External REST API | PR comments, file writes to output repo | Used by all five AI tooling scripts |
| `ai-delivery-outputs` (repo) | Sibling GitHub repository | Stores generated documentation and test files | Must exist and be accessible with `GH_TOKEN` |
| Azure App Service | Azure PaaS | Hosts API and frontend | Two named apps: `training-bot-api`, `training-bot-frontend` |
| `uv` (Astral) | Build tool | Dependency management and `requirements.txt` generation | Used in CI |
| `pytest` | Python library | Test runner | |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 3. Install all dependencies (including dev)
uv sync
```

### Local development

```bash
# 4. Create a .env file with required variables
cat > .env <<EOF
API_KEY=<your-openrouter-or-anthropic-key>
OPENAI_URL_BASE=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-4o
SHOW_TOOL_CALLS=true
EOF

# 5. Ingest PDF documents into the vector store (run once, or after adding new PDFs)
python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 6. Start the FastAPI backend
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 7. Run tests
uv run pytest tests/ -v
```

### Production deployment (Azure App Service via CI/CD)

Deployment is fully automated via GitHub Actions on push to `main`. Manual steps to configure the pipeline:

```bash
# 8. Set the following GitHub Actions secrets in the repository settings:
#    Settings → Secrets and variables → Actions → New repository secret
#
#    AZURE_WEBAPP_PUBLISH_PROFILE_API     — download from Azure Portal > training-bot-api > Get publish profile
#    AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND — download from Azure Portal > training-bot-frontend > Get publish profile
#    ANTHROPIC_API_KEY                    — Anthropic console API key
#    GH_TOKEN                             — GitHub PAT with repo read/write on ai-delivery-outputs
#    SENDGRID_API_KEY                     — SendGrid API key

# 9. Push to main to trigger deployment
git push origin main
```

### Manual deployment (if CI is unavailable)

```bash
# 10. Generate requirements.txt
uv export --no-dev --format requirements