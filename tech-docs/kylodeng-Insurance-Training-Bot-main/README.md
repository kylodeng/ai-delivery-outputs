# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based backend system that trains insurance sales agents using LLM-powered conversational AI. It supports two modes: a **teacher mode** for guided learning and a **roleplay mode** where the agent practises against a simulated customer persona. The system ingests insurance product PDFs into a vector store and exposes RAG (Retrieval-Augmented Generation) tools so the LLM can answer product-specific questions accurately without hallucinating.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python 3.13 (CI), 3.11+ compatible |
| Package manager | uv (Astral) | Used for dependency management and test running |
| LLM provider | OpenRouter / Anthropic (configurable) | Default model: `openai/gpt-oss-20b:free`; annotation model defaults to `claude-sonnet-4-6` |
| LLM orchestration | LangChain + LangGraph | `langchain-openai`, `langchain-core` |
| Vector store | ChromaDB / FAISS / Pinecone | Selectable via `core.vector_store`; `LocalFAISSStore` used by default |
| PDF processing | pdfplumber | Custom chunker with heading/bullet heuristics |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Static file serving | FastAPI `StaticFiles` | PDFs served under `/docs/` |
| CI/CD | GitHub Actions | 5 AI-delivery workflows + deploy workflow |
| Deployment | Azure App Service | Two apps: `training-bot-api` and `training-bot-frontend` |
| AI pipeline scripts | Python 3.12 + `anthropic` + `requests` | Claude `claude-sonnet-4-6` via Anthropic API |
| Email | SendGrid | Via `SENDGRID_API_KEY` |
| Environment config | python-dotenv | `.env` file for local dev |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Client (Browser / Chainlit UI)        │
└────────────────────┬─────────────────────────────────────┘
                     │  HTTP / SSE (StreamingResponse)
┌────────────────────▼─────────────────────────────────────┐
│               FastAPI  (api/main.py)                      │
│  ┌─────────────────────┐  ┌──────────────────────────┐   │
│  │  Teacher Agent       │  │  Roleplay / Assessor      │   │
│  │  (LangGraph)         │  │  Agent (LangGraph)        │   │
│  └────────┬────────────┘  └────────────┬─────────────┘   │
│           │  RAG tool calls             │                  │
│  ┌────────▼────────────────────────────▼─────────────┐   │
│  │            RAG Tools  (api/rag_tools.py)           │   │
│  │  search_product | search_all | compare_plans       │   │
│  │  lookup_hospital_network | lookup_exclusions       │   │
│  │  search_claim_procedure | list_products            │   │
│  └────────────────────┬───────────────────────────────┘   │
│                       │  vector similarity search          │
│  ┌────────────────────▼───────────────────────────────┐   │
│  │          Vector Store  (core/vector_store.py)       │   │
│  │          ChromaDB | LocalFAISS | Pinecone           │   │
│  └────────────────────▲───────────────────────────────┘   │
└───────────────────────┼──────────────────────────────────┘
                        │  POST /ingest
┌───────────────────────┴──────────────────────────────────┐
│          Ingestion Pipeline  (core/ingest.py)             │
│  pdfplumber → annotator (LLM) → chunker → embed → store   │
│  PDFs live under  data/Insurance-product-info/            │
└──────────────────────────────────────────────────────────┘

Sessions persisted to  data/sessions.json
PDFs served statically at  /docs/<path>
```

**Component interactions:**

1. On startup, `lifespan()` loads the persisted vector store and all sessions from disk.
2. Chat requests hit the FastAPI endpoints; the appropriate LangGraph agent (teacher or assessor) is instantiated with the shared RAG tools.
3. RAG tools call into the vector store to retrieve relevant chunks from ingested insurance PDFs, tracking sources via a per-request `contextvars` list.
4. Source citations are injected inline into the LLM response using `[[Sn]]` markers.
5. Roleplay sessions generate a randomised Hong Kong customer profile; the assessor agent verifies the trainee's factual claims against the vector store post-session.
6. Five GitHub Actions AI-delivery workflows (code review, tech docs, business docs, auto-testing, UAT) run separately and push outputs to an `ai-delivery-outputs` repo via the GitHub API.

---

## 4. Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install uv (if not already installed)
curl -Lsf https://astral.sh/uv/install.sh | sh

# 3. Install all dependencies
uv sync

# 4. Copy and populate the environment file
cp .env.example .env
# Edit .env — see Environment Variables section below

# 5. Place insurance product PDFs under the data directory
mkdir -p data/Insurance-product-info
# Copy your PDF files into data/Insurance-product-info/

# 6. Ingest PDFs into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 7. Start the FastAPI server
uv run uvicorn api.main:app --reload --port 8000

# 8. (Optional) Start the Chainlit frontend
#    [TODO: What is the exact command to launch the Chainlit UI?]
```

> **Note:** SSL verification is explicitly disabled in `httpx` clients (`verify=False`). Do not use this in production without addressing certificate validation.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name used for chat and annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Stream tool call events to the client by default |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by GitHub Actions AI-delivery scripts |
| `GH_TOKEN` | Yes (CI only) | — | GitHub PAT used by AI-delivery scripts to read repos and post comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid key for email notifications from AI-delivery scripts |
| `OUTPUT_REPO` | No (CI) | `ai-delivery-outputs` | GitHub repo name where AI-delivery script outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI) | `kylo.deng@capco.com` | Recipient email for AI-delivery notifications |
| `SENDER_EMAIL` | No (CI) | `kylo.deng@capco.com` | Sender address for AI-delivery emails |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (CI deploy) | — | GitHub secret: Azure publish profile for `training-bot-api` |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (CI deploy) | — | GitHub secret: Azure publish profile for `training-bot-frontend` |

---

## 6. Running Tests

```bash
# Run the full test suite
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this automatically on every push and pull request to `main` before deploying.

> [TODO: What testing frameworks and test files exist under `tests/`? The directory is referenced in CI but not included in the provided files.]

---

## 7. Deployment

### Automatic (GitHub Actions)

Deployment is triggered automatically on every push to `main` after tests pass:

```
Push to main
  → test job (uv run pytest tests/ -v)
  → deploy-api job   (Azure App Service: training-bot-api)
  → deploy-frontend job (Azure App Service: training-bot-frontend)
```

Both deploy jobs use `azure/webapps-deploy@v3`. The publish profiles must be stored as repository secrets `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`.

### Manual ingestion (before first deploy)

```bash
# Export a requirements.txt from uv lock file (done automatically in CI)
uv export --no-dev --format requirements-txt -o requirements.txt

# Ingest PDFs — run this locally or as a one-off task before the app starts
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

### AI Delivery Workflows

Five supplementary workflows run on their own schedules or can be triggered manually:

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Posts Claude review as PR comment |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Version tag (`v*`), manual | Generates solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates test files or coverage gap analysis |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses completed CSV results |

Required secrets for all AI workflows: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL certificate verification is disabled (`verify=False`) on all `httpx` clients — not safe for production |
| `api/main.py` | `SHOW_TOOL_CALLS` has a redundant inline print statement with a formatting inconsistency (`f-string` with space instead of env var value) |
| `api/agent.py` | `make_teacher_agent` and `make_assessor_agent` are imported but only a stub `create_agent` import from `langchain.agents` is shown — full agent construction code is not included in the provided files |
| `api/rag_tools.py` | `_find_file_url` uses `lru_cache` for filesystem lookups — cache will not invalidate if files are added after startup |
| `core/annotator.py` | Annotation logic after `load_or_create_annotations` contains a comment `# custom annotation logic` suggesting the implementation is incomplete or truncated |
| `core/ingest.py` | Default `batch_delay=0` with `batch_size=126` — the code comment notes that free-tier Voyage AI requires `batch_size=20` and `batch_delay=22s`; the defaults may cause rate-limit errors on free accounts |
| `tool5_uat.py` | `SYSTEM_ANALYSE` instructs tester fields to write `[TESTER: verify this]` where information cannot be determined from code |
| `tool3_business_docs.py` | Stakeholder names, go-live dates, success metrics, and prior context are explicitly marked `[TODO]` in the generated document template |
| `.github/scripts/tool1_code_review.py` | `review_pr` function string is truncated — full PR comment template and `review_repo` function not shown |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated (`{r` variable reference is incomplete) |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated in provided source |
| General | `send_email`, `email_html`, and `write_audit_entry` are imported from `shared.py` across all tool scripts but their implementations are not present in the provided `shared.py` (file is truncated) |
| General | [TODO: What is the exact command to launch the Chainlit frontend UI?] |
| General | [TODO: Is there a `.env.example` file, or must the developer manually create `.env` from the variables table?] |
| General | [TODO: What vector store backend is used by default in production — ChromaDB, LocalFAISS, or Pinecone?] |