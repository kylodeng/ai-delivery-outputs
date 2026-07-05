# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is an AI-powered sales training platform for insurance agents, specifically targeting the Hong Kong insurance market. It combines a FastAPI backend with a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life Hong Kong insurance product PDFs into a vector store, then exposes a LangGraph-based multi-agent system with two modes: a **Teacher Agent** for interactive coaching and an **Assessor Agent** for post-roleplay performance evaluation. Agents are grounded by eight RAG tools that query the vector store for verified product details, hospital networks, exclusions, and claims procedures. The system is deployed as two separate Azure App Service instances (API backend and a frontend) via GitHub Actions CI/CD, with AI-assisted developer tooling (code review, tech docs, business docs, auto-testing, UAT) powered by the Anthropic Claude API running as separate GitHub Actions workflows.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend (LangGraph agents, RAG tools, session management, ingest endpoint) |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based, [TODO: confirm frontend framework]) |
| Vector Store (local) | Local FAISS / Chroma / Pinecone (configurable) | [TODO: confirm prod store type] | Stores embedded insurance PDF chunks for RAG retrieval |
| `sessions.json` | File on App Service filesystem | Azure | Persists multi-turn conversation sessions across server restarts |
| `/data/` static mount | App Service local filesystem | Azure | Stores insurance PDFs and annotation sidecar files (`.annot.json`); served over HTTP at `/docs/` |
| GitHub Actions runners | Ephemeral `ubuntu-latest` | GitHub (Microsoft) | CI/CD: test, build, deploy; AI tooling (code review, docs generation, UAT) |
| `ai-delivery-outputs` (repo) | GitHub Repository | GitHub | Receives AI-generated documentation artifacts (README, ARCHITECTURE, RUNBOOK, test files, UAT packs) |
| Anthropic Claude API (`claude-sonnet-4-6`) | Managed API | Anthropic | Powers all AI tooling workflows (code review, doc gen, auto-testing, UAT, document annotation) |
| OpenRouter / OpenAI-compatible endpoint | Managed API | Configurable (`OPENAI_URL_BASE`) | Serves the LLM for the runtime teacher/assessor agents and PDF annotation |
| Voyage AI (implied) | Managed API | Voyage AI | Embedding model used during ingestion (`batch_delay` default suggests Voyage free-tier rate limits) |
| SendGrid | Managed API | Twilio/SendGrid | Email delivery for AI tooling workflow notifications |

---

## 3. Data Flow

### 3a — Ingestion Pipeline (one-time / on-demand)

1. Insurance PDF files are placed under `data/Insurance-product-info/` in the repository.
2. `POST /ingest` is called (or `core/ingest.py` run directly), triggering `ingest_directory()`.
3. For each PDF, `load_or_create_annotations()` checks for a cached `.annot.json` sidecar file. If absent, the LLM (OpenRouter/OpenAI-compatible) is called to annotate the document metadata and each page's relevance.
4. Irrelevant pages (cover pages, company profile, blank pages) are filtered out based on page annotations.
5. `extract_chunks_from_pdf()` splits remaining page text into semantic units using heuristic heading/bullet detection, then splits large units by sentence or word count (max ~280 words per chunk).
6. Chunks are batched and sent to the configured embedding model (Voyage AI implied) in batches of up to 126 with optional rate-limit delays.
7. Embedded vectors are written to the vector store (FAISS local, Chroma, or Pinecone depending on env config) and saved to disk.

### 3b — Runtime Chat (Teacher Mode)

1. The frontend sends a chat message to the FastAPI backend.
2. `api/main.py` retrieves or creates a session from in-memory store (backed by `sessions.json`).
3. The request is routed to the **Teacher Agent** (LangGraph), which receives the `TEACHER_SYSTEM` prompt.
4. `reset_sources()` initialises a fresh per-request source tracking context variable.
5. The agent decides which RAG tool(s) to call (e.g. `search_product`, `lookup_hospital_network`, `compare_plans`).
6. Each tool queries the vector store with a similarity search, collects metadata (document name, page range, file URL), and appends unique source entries to the request-scoped source bucket.
7. Tool results (with source IDs like `[S1]`, `[S2]`) are returned to the agent.
8. The agent synthesises a response with inline citation markers and streams it back via `StreamingResponse` using `astream_events`.
9. After streaming completes, collected sources are sent to the frontend for citation rendering.
10. PDFs are accessible directly via the `/docs/` static mount for the frontend to link to.

### 3c — Roleplay & Assessment Mode

1. The frontend requests a new roleplay session; the API calls `generate_profile()` to randomly select a synthetic Hong Kong customer persona (name, age, occupation, income, goals, risk tolerance, personality, existing coverage).
2. The roleplay conversation proceeds with the **customer** roleplayed by an LLM using the `_ROLEPLAY_SYSTEM` prompt — the trainee agent interacts as themselves.
3. When the session ends, the full conversation transcript and customer profile are sent to the **Assessor Agent**.
4. The Assessor Agent uses the same RAG tools to verify factual claims made by the trainee against the knowledge base, then produces a structured five-dimension performance assessment.
5. The assessment result is returned to the frontend.

### 3d — AI Developer Tooling Workflows

1. A GitHub event (push to `main`, PR open, schedule, tag push, or `workflow_dispatch`) triggers one of the five AI tooling workflows.
2. The workflow fetches repo file contents via the GitHub API using `GH_TOKEN`.
3. For PR-triggered workflows, the unified diff is fetched via the GitHub API.
4. File content is assembled into prompts and sent to the Anthropic Claude API (`claude-sonnet-4-6`).
5. Claude's structured response (JSON or Markdown) is parsed and written as a file to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
6. A PR comment is posted (for code review tool) and/or a SendGrid email is sent to `kylo.deng@capco.com`.
7. JSON artifacts are uploaded to GitHub Actions artifacts for audit trail.

---

## 4. Security Posture

### Secured

- **CI/CD secrets** — Publish profiles, API keys, and tokens are stored as GitHub Actions secrets and injected via environment variables; not hardcoded in workflow YAML.
- **Session isolation** — Sessions are keyed by UUID; there is no evidence of cross-session data leakage in the session management code.
- **Synthetic training data** — Customer profiles used in roleplay are randomly generated from local lists; no real customer PII is used in training scenarios.

### Not Secured / Gaps

- ⚠️ **TLS verification disabled** — `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls in `api/main.py` and `core/ingest.py`. This disables TLS certificate verification, exposing all LLM API traffic (including prompts and insurance product content) to potential man-in-the-middle attacks. **This is a critical security gap and must be fixed before production use.**
- ⚠️ **No API authentication on FastAPI endpoints** — There is no evidence of authentication middleware (OAuth2, API keys, JWT) on any `/ingest`, `/chat`, or session management endpoints. Any network-reachable client can call these endpoints. [TODO: confirm whether Azure App Service access restrictions or Easy Auth are configured outside of IaC]
- ⚠️ **Sessions persisted to local filesystem** (`sessions.json`) — This file is unencrypted on the App Service filesystem. If the filesystem is not encrypted at rest by Azure, session data (conversation history, customer profiles) is exposed. [TODO: confirm Azure App Service local disk encryption-at-rest status]
- ⚠️ **No encryption of vector store at rest** — The local FAISS/Chroma store is written to disk without explicit encryption. Insurance product content embedded in vectors is exposed to anyone with filesystem access.
- ⚠️ **PDFs served unauthenticated over HTTP** — The `/docs/` static mount serves all insurance PDFs and annotation files over HTTP with no authentication. Any user who can reach the App Service URL can download all source documents.
- ⚠️ **CORS policy is overly permissive** — `allow_methods=["*"]` and `allow_headers=["*"]` are set. While origins are restricted to localhost in code, [TODO: confirm production CORS origins are tightened — current code only allows localhost origins, which would block all legitimate frontend traffic in production].
- ⚠️ **`GH_TOKEN` scope unknown** — The `GH_TOKEN` used by AI tooling workflows has write access to `ai-delivery-outputs` and read access to source repos. The exact token scope/permissions are not defined in the IaC. [TODO: confirm GH_TOKEN is a fine-grained PAT scoped to minimum required repos and permissions, not a classic token with broad access]
- ⚠️ **`API_KEY` default is empty string** — `os.getenv("API_KEY", "")` falls back to an empty string. If the environment variable is not set, LLM calls will be made with an empty API key, which may silently fail or expose errors.
- ⚠️ **No WAF or DDoS protection defined in IaC** — No Azure Front Door, Application Gateway, or WAF rules are present in the deployment configuration.
- ⚠️ **No secrets scanning** — No `git-secrets`, `trufflehog`, or GitHub secret scanning is configured in the CI pipeline.
- ⚠️ **`SENDGRID_API_KEY` and `ANTHROPIC_API_KEY` logged in env** — These are set as plain `env:` values in workflow YAML files. If a step accidentally prints env vars (e.g. a debug command), keys would appear in logs. GitHub masks known secrets, but this is a defence-in-depth gap.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter/Anthropic) | App Service environment / `.env` file |
| `OPENAI_URL_BASE` | No | Low | App Service environment / `.env` file; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | App Service environment / `.env` file; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | App Service environment / `.env` file; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (AI tooling workflows) | 🔴 High | GitHub Actions secret (`secrets.ANTHROPIC_API_KEY`) |
| `GH_TOKEN` | Yes (AI tooling workflows) | 🔴 High — GitHub PAT with repo write access | GitHub Actions secret (`secrets.GH_TOKEN`) |
| `SENDGRID_API_KEY` | Yes (AI tooling workflows) | 🔴 High | GitHub Actions secret (`secrets.SENDGRID_API_KEY`) |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy workflow) | 🔴 High — Azure publish credentials | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy workflow) | 🔴 High — Azure publish credentials | GitHub Actions secret |
| `OUTPUT_REPO` | No (AI tooling) | Low | Workflow `env:` block; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (AI tooling) | Low | Workflow `env:` block; defaults to `github.repository_owner` |
| `NOTIFY_EMAIL` | No (AI tooling) | Low | Workflow `env:` block; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (AI tooling) | Low | Workflow `env:` block; hardcoded to `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO: confirm variable name] | Low | App Service environment — controls FAISS/Chroma/Pinecone selection |
| `PINECONE_API_KEY` | Conditional | 🔴 High | App Service environment (only if Pinecone store selected) |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External managed API | AI tooling workflows (code review, docs, UAT); PDF annotation | Billed per token; no fallback if unavailable |
| **OpenRouter** (default) or any OpenAI-compatible endpoint | External managed API | Runtime LangGraph teacher/assessor agents; PDF annotation | Configurable via `OPENAI_URL_BASE`; defaults to OpenRouter free model |
| **Voyage AI** (implied) | External managed API | Embedding model for vector store ingestion | Implied by rate-limit batch defaults (3 RPM free tier); [TODO: confirm embedding provider and model name] |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool binding, streaming | Core runtime dependency |
| **FastAPI** | Python library | REST API backend | Includes StreamingResponse for SSE |
| **pdfplumber** | Python library | PDF text extraction | Used in chunker |
| **FAISS / Chroma / Pinecone** | Library / managed service | Vector storage and similarity search | Selectable at runtime via env config |
| **SendGrid** | External managed API | Email notifications for AI tooling output | Used by all 5 tooling workflows |
| **GitHub API** (`api.github.com`) | External API | Repo file fetch, PR diff fetch, PR comments, output repo writes | Requires `GH_TOKEN` PAT |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repo | Receives all AI-generated documentation artifacts | Must exist in the same GitHub org/owner |
| **Azure App Service** | Cloud PaaS | Hosts API and frontend | Deployment target for both services |
| **`uv`** (Astral) | Build tool | Python dependency management and `requirements.txt` export | Used in all CI/CD jobs |
| **httpx** | Python library | HTTP client for LLM API calls | TLS verification currently disabled — see Security section |
| **dotenv** | Python library | Local `.env` file loading | Used in `api/main.py` and `core/ingest.py` |

---

## 7. Deployment Instructions

### Prerequisites

- Azure App Services `training-bot-api` and `training-bot-frontend` must be pre-created in Azure Portal or via Azure CLI.
- Publish profiles must be downloaded from Azure Portal and stored as GitHub Actions secrets `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`.
- All required environment variables (see Section 5) must be configured in Azure App Service → Configuration → Application Settings.
- The `ai-delivery-outputs` GitHub repository must exist and `GH_TOKEN` must have write access to it.

### Automated Deployment (recommended)

```bash
# 1. Push to main branch — this triggers the full test → deploy pipeline automatically
git add .
git commit -m "your change"
git push origin main

# The GitHub Actions workflow will:
# a. Run pytest tests (Python 3.13, uv)
# b. On test success, concurrently deploy API and frontend to Azure App Service
```

### Manual / Local Setup

```bash
# 1. Install uv
curl -Ls https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with