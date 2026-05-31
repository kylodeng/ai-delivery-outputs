# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is an AI-powered coaching and assessment platform designed to help new insurance agents at Sun Life Hong Kong master product knowledge and sales skills. The system exposes a FastAPI backend that serves a Retrieval-Augmented Generation (RAG) pipeline: insurance product PDFs are ingested, chunked, annotated via LLM, and embedded into a vector store; two LangGraph agents (a **Teacher** for interactive coaching and an **Assessor** for post-roleplay scoring) then query that store to ground their responses in verified product facts. A Chainlit-based frontend provides the chat UI, and the entire stack is deployed to **Azure App Service** via GitHub Actions CI/CD, with five auxiliary AI-delivery workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) powered by the Anthropic Claude API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend / RAG API |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the Chainlit chat frontend |
| Vector Store (ChromaDB / FAISS / Pinecone) | Local file or managed service | Azure / [TODO: confirm store type in prod] | Stores PDF chunk embeddings for RAG retrieval |
| `data/sessions.json` | File-based session store | Azure App Service local disk | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | Static file directory | Azure App Service local disk | Stores raw PDFs and `.annot.json` sidecar files; served over HTTP via `/docs` mount |
| GitHub Actions Runners | Ephemeral compute (ubuntu-latest) | GitHub (Microsoft-hosted) | CI/CD: test, build, deploy, AI-delivery tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated code-review reports, tech docs, business docs, UAT packs |
| OpenRouter / OpenAI-compatible LLM endpoint | External API | [TODO: confirm prod endpoint — OpenRouter vs Azure OpenAI vs Anthropic direct] | LLM inference for teacher agent, assessor agent, and PDF annotation |
| Anthropic Claude API (`claude-sonnet-4-6`) | External API | Anthropic | AI-delivery workflow tools (code review, docs, testing, UAT) |
| SendGrid | External Email API | Twilio/SendGrid | Sends notification emails after each AI-delivery workflow run |
| Voyage AI (embedding) | External API | Voyage AI | [TODO: confirm — referenced in `ingest.py` rate-limit comment; not confirmed as sole embedder] |

---

## 3. Data Flow

### 3a — Ingestion (offline / on-demand)

1. An operator places insurance PDF files under `data/Insurance-product-info/`.
2. `POST /ingest` is called (or `python core/ingest.py` is run directly).
3. `ingest_directory()` walks the directory recursively, calling the LLM (`OPENAI_URL_BASE`) to generate `.annot.json` sidecar files (product name, doc type, page relevance) — skipped if annotation already exists.
4. `extract_chunks_from_pdf()` (pdfplumber) splits each relevant page into semantic units (headings, bullets, paragraphs), respecting the `max_words` limit (~280 words/chunk).
5. `embed_chunks()` sends batches of chunk dicts to the configured vector store (`ChromaStore` / `LocalFAISSStore` / `PineconeStore`), which calls the embedding model API (Voyage AI or equivalent) and persists the index to disk.
6. `store.save()` writes the index to the local filesystem on App Service.

### 3b — Teacher / Roleplay Chat (runtime)

1. User opens the Chainlit frontend (`training-bot-frontend` App Service).
2. Frontend sends a POST request to the FastAPI backend (`training-bot-api`) with a session ID and user message.
3. `main.py` looks up the in-memory session (backed by `data/sessions.json`); if new, `generate_profile()` randomly constructs a `CustomerProfile`.
4. The request is routed to either the **Teacher agent** or the **Roleplay agent** (the customer character) depending on session mode.
5. In teacher mode, `reset_sources()` initialises a fresh per-request source-tracking context.
6. The LangGraph agent decides which RAG tools to invoke (`search_product`, `search_all`, `lookup_hospital_network`, `compare_plans`, `lookup_exclusions`, `search_claim_procedure`, `list_products`, `get_current_date`).
7. Each tool queries the vector store, collects matching chunks, and appends source metadata (document name, page, URL) to the contextvar bucket.
8. The agent streams its response back via `astream_events`; tool calls optionally surface in the UI based on `SHOW_TOOL_CALLS` config.
9. The response is streamed to the frontend as `StreamingResponse`.
10. After streaming, `get_current_sources()` collects all cited sources; inline citation markers (`[[S1]]`, `[[S2]]`) are resolved into clickable `/docs/` links pointing to the statically mounted PDF files.

### 3c — Assessment (post-roleplay)

1. After a roleplay session ends, the frontend triggers `POST /assess`.
2. The **Assessor agent** receives the full conversation transcript and customer profile.
3. It invokes the same RAG tools to verify every factual claim made by the trainee agent.
4. Assessment results (scores across five dimensions) are returned as a JSON payload and displayed in the UI.

### 3d — CI/CD and AI-Delivery Workflows

1. Developer pushes to `main` or opens a PR on GitHub.
2. The `test` job runs `pytest` via `uv`.
3. On merge to `main`: `deploy-api` and `deploy-frontend` jobs generate `requirements.txt` via `uv export` and deploy to the respective Azure App Service using publish profiles.
4. AI-delivery workflows (tools 1–5) fire on their respective triggers (PR open, cron schedule, tag push, release branch creation) and call the Anthropic Claude API (`claude-sonnet-4-6`) to generate reports, which are committed to the `ai-delivery-outputs` GitHub repo and emailed via SendGrid.

---

## 4. Security Posture

### ✅ What IS secured

- **GitHub Secrets** are used for all sensitive credentials in CI/CD (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`); they are not hardcoded in workflow YAML.
- **API Key for LLM** is wrapped in Pydantic `SecretStr` in `main.py`, preventing accidental logging.
- **Deployment gate**: `deploy-api` and `deploy-frontend` only run after the `test` job passes.
- **CORS** is restricted to known origins (`localhost:5173`, `localhost:8000`) in development.

### ❌ Gaps and explicit risks

| Gap | Detail |
|---|---|
| **TLS verification disabled** | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `main.py` and `core/ingest.py`. This disables SSL certificate validation for **all** LLM API calls, making them vulnerable to man-in-the-middle attacks. **Must be fixed before production use.** |
| **No encryption at rest** | `data/sessions.json` and the vector store index are written to App Service local disk with no encryption. Conversation history (including customer profile PII and agent chat content) is stored in plaintext. |
| **No encryption in transit for internal data paths** | The `/docs` static file mount serves raw PDFs over HTTP with no authentication. Any party with network access to the App Service can download all insurance product documents. |
| **CORS allows all methods and headers** | `allow_methods=["*"]` and `allow_headers=["*"]` on the CORS middleware is overly permissive. |
| **No authentication or authorisation on the API** | There is no auth middleware on the FastAPI app. Any caller with network access to `training-bot-api` can read sessions, trigger ingestion, or interact with the agents. |
| **No rate limiting** | No rate limiting or throttling is implemented on any API endpoint, exposing the LLM-backed endpoints to abuse and runaway cost. |
| **`sessions.json` contains conversation history** | Multi-turn conversation messages (potentially containing sensitive insurance product discussions or trainee PII) persist to a local flat file with no access controls. |
| **`GH_TOKEN` scope unknown** | The `GH_TOKEN` used by AI-delivery workflows writes to a separate `ai-delivery-outputs` repo. The exact token scopes are not defined in the repository — if it is a PAT with broad repo scope, it is overly privileged. [TODO: confirm GH_TOKEN is a fine-grained PAT scoped to only `ai-delivery-outputs` write access] |
| **No WAF or DDoS protection** | Azure App Service is deployed without mention of Azure Front Door, Application Gateway, or DDoS protection. |
| **No secrets scanning** | No `git-secrets`, `trufflehog`, or GitHub secret scanning configuration is present. |
| **PDF data directory exposed** | `app.mount("/docs", StaticFiles(directory=str(_DATA_DIR)), name="docs")` serves the entire `data/` directory — including `sessions.json` — depending on the exact path. [TODO: confirm `sessions.json` is not under `_DATA_DIR` or add an exclusion] |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key | App Service environment / `.env` locally |
| `OPENAI_URL_BASE` | No | Low | App Service environment / `.env`; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | App Service environment / `.env`; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | App Service environment / `.env`; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (AI-delivery workflows) | 🔴 High | GitHub Actions secret |
| `GH_TOKEN` | Yes (AI-delivery workflows) | 🔴 High — GitHub PAT | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (AI-delivery workflows) | 🔴 High | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High | GitHub Actions secret |
| `OUTPUT_REPO` | No | Low | GitHub Actions env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No | Low | GitHub Actions env; hardcoded to `noreply@ai-delivery.capco.com` |
| `VECTOR_STORE_TYPE` | [TODO: confirm env var name] | Low | [TODO: not visible in source — how is ChromaStore vs FAISS vs Pinecone selected in prod?] |
| `PINECONE_API_KEY` | Conditional | 🔴 High — if PineconeStore is used | [TODO: not seen in source — confirm where set] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (or compatible OpenAI endpoint) | External API | LLM inference for agents and PDF annotation | `verify=False` is a critical security gap; endpoint is configurable via `OPENAI_URL_BASE` |
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External API | AI-delivery GitHub workflow tools | Used in all five tool workflows via `shared.py` |
| **Voyage AI** (embedding) | External API | Generating embeddings for PDF chunks | Inferred from rate-limit comment in `ingest.py`; [TODO: confirm embedding provider and model name] |
| **SendGrid** | External Email API | Notification emails from AI-delivery workflows | Configured via `SENDGRID_API_KEY` |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool binding, streaming | `langchain`, `langchain_openai`, `langchain_core` |
| **pdfplumber** | Python library | PDF text extraction | Used in `core/chunker.py` |
| **FastAPI** | Python framework | REST API backend | |
| **Chainlit** | Python framework | Chat UI frontend | [TODO: confirm Chainlit is deployed as the `training-bot-frontend` App Service — not explicit in provided source] |
| **ChromaDB / FAISS** | Python library | Local vector store backends | Selection appears to be code-driven; [TODO: confirm which is used in production] |
| **Pinecone** | Managed vector DB | Optional cloud vector store backend | `PineconeStore` class exists; [TODO: confirm if used in prod] |
| **`uv`** | Build tool | Python dependency management and packaging | Used in CI/CD for `uv sync`, `uv export` |
| **`ai-delivery-outputs`** (repo) | Sibling GitHub repository | Stores all AI-generated artefacts (reports, docs, UAT packs) | Must exist under the same GitHub org/owner |
| **Azure App Service** | Cloud PaaS | Hosting for API and frontend | Two separate App Service instances |
| **Health Mutual Group Limited (HMG)** | Third-party service | Manages cashless hospital network referenced in product data | External partner; no API integration — data is static PDF |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13 installed locally
- [`uv`](https://github.com/astral-sh/uv) installed (`pip install uv` or `curl -Lsf https://astral.sh/uv/install.sh | sh`)
- Azure CLI authenticated to the correct subscription
- Two Azure App Service instances (`training-bot-api`, `training-bot-frontend`) already provisioned
- Publish profiles downloaded from Azure Portal and stored as GitHub Actions secrets

### Local Development

```bash
# 1. Clone the repo
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install dependencies
uv sync

# 3. Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# 4. Ingest PDFs into the vector store
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# 5. Start the API server
uv run uvicorn api.main:app --reload --port 8000

# 6. (Separate terminal) Start the frontend
# [TODO: confirm frontend start command — Chainlit assumed]
uv run chainlit run frontend/app.py --port 5173
```

### Running Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (GitHub Actions — automatic)

Deployment is triggered automatically on every push to `main` after tests pass:

```
git push origin main
# → triggers: test → deploy-api (parallel) + deploy-frontend (parallel)
```

### Manual Deployment (Azure CLI fallback)

```bash
# Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API
az webapp deploy --resource-group <rg> --name training-bot-api \
  --src-path . --type zip

# Deploy Frontend
az webapp deploy --resource-group <rg> --name training-bot-frontend \
  --src-path . --type zip
```

### Triggering a Vector Store Re-ingest

```bash
# Via API (once deployed)
curl -X POST