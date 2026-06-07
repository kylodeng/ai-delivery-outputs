# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training system for insurance sales agents, built around a Hong Kong insurance context. It provides two modes: a **teacher mode** for interactive coaching and product knowledge Q&A, and a **roleplay/assessment mode** where the agent practises sales conversations with a simulated customer profile and receives structured feedback. The system uses Retrieval-Augmented Generation (RAG) over a library of real insurance product PDFs to ensure all product facts are grounded in source documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, async |
| LLM Orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM Provider | OpenRouter (default) | Configurable via `OPENAI_URL_BASE`; default model `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude (via OpenAI-compat shim) | Default `claude-sonnet-4-6`; used during PDF ingestion |
| Embeddings / Vector Store | `core` library — supports ChromaDB, FAISS (local), Pinecone | Selectable via config |
| PDF Processing | `pdfplumber` | Chunking and text extraction |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Static File Serving | FastAPI `StaticFiles` | Serves PDFs from `data/` at `/docs/` |
| Frontend | [TODO: what framework/technology is used for the frontend?] | Vite dev server expected on port 5173 |
| Package Manager | `uv` (astral-sh) | Python 3.13 in CI |
| CI/CD | GitHub Actions | `.github/workflows/deploy.yml` |
| Deployment Target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| AI Delivery Tools | Anthropic Claude (`claude-sonnet-4-6`) | 5 automation scripts in `.github/scripts/` |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Client / Frontend                  │
│         (Vite dev server or Azure App Service)       │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP / SSE (streaming)
┌──────────────────────▼──────────────────────────────┐
│              FastAPI Backend  (api/main.py)          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ Teacher Agent│  │Assessor Agent│  │ /ingest   │  │
│  │ (LangGraph,  │  │ (LangGraph,  │  │ endpoint  │  │
│  │  streamed)   │  │  one-shot)   │  │           │  │
│  └──────┬───────┘  └──────┬───────┘  └─────┬─────┘  │
│         │                 │                │         │
│  ┌──────▼─────────────────▼────────────────▼──────┐  │
│  │              RAG Tools (api/rag_tools.py)       │  │
│  │  search_product · search_all · compare_plans    │  │
│  │  lookup_hospital_network · lookup_exclusions    │  │
│  │  search_claim_procedure · list_products         │  │
│  │  get_current_date                               │  │
│  └──────────────────────┬──────────────────────────┘  │
│                         │                             │
│  ┌──────────────────────▼──────────────────────────┐  │
│  │         Vector Store  (core/vector_store.py)     │  │
│  │    Chroma / FAISS (local) / Pinecone             │  │
│  └──────────────────────┬──────────────────────────┘  │
└─────────────────────────┼───────────────────────────┘
                          │ load/save
              ┌───────────▼────────────┐
              │  data/ (PDF files +    │
              │  .annot.json sidecars) │
              └────────────────────────┘
```

**Key interactions:**

1. **Ingestion** (`POST /ingest` or `python -m core.ingest`): PDFs under `data/Insurance-product-info/` are processed by `core/ingest.py`. Each PDF is annotated once using an LLM (product name, doc type, page relevance) and cached to a `.annot.json` sidecar. Relevant pages are chunked by `core/chunker.py` and embedded into the vector store.
2. **Teacher mode**: The frontend opens a streaming connection. `api/agent.py` builds a LangGraph agent with the 8 RAG tools. The agent streams responses via `astream_events`; source citations are tracked per-request using a `contextvars.ContextVar`.
3. **Roleplay/Assessment mode**: After a roleplay session ends, the assessor agent is invoked (`ainvoke`) with the full conversation and a randomly generated `CustomerProfile`. It uses the same RAG tools to verify factual claims made by the trainee.
4. **Sessions** are held in memory and flushed to `data/sessions.json` on each update.
5. **GitHub Actions AI tools** (`.github/scripts/`) are independent workflows that call the Anthropic Claude API directly to perform code review, tech doc generation, business docs, test generation, and UAT facilitation on the repo itself.

---

## 4. Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 2. Install uv (if not already installed)
curl -Lsf https://astral.sh/uv/install.sh | sh
```

```bash
# 3. Install Python dependencies
uv sync
```

```bash
# 4. Copy and populate environment variables
cp .env.example .env
# Edit .env — see Environment Variables section below
```

```bash
# 5. Place insurance product PDFs into the data directory
# Expected path: data/Insurance-product-info/<ProductFolder>/<file>.pdf
# (several .annot.json sidecar files are already committed for bundled products)
```

```bash
# 6. Ingest PDFs into the vector store
uv run python -m core.ingest
# or via the API after starting the server:
# curl -X POST http://localhost:8000/ingest
```

```bash
# 7. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 8. Start the frontend development server
# [TODO: what command starts the frontend? e.g. npm run dev or chainlit run?]
```

> **Note:** SSL verification is disabled for the LLM HTTP client (`verify=False`). This is intentional for local development but should be reviewed before production use.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name passed to the LLM provider |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI/CD tools) | — | Anthropic API key used by `.github/scripts/` workflows |
| `GH_TOKEN` | Yes (CI/CD tools) | — | GitHub personal access token for API calls in workflows |
| `SENDGRID_API_KEY` | Yes (CI/CD tools) | — | SendGrid API key for email notifications from workflows |
| `OUTPUT_REPO` | No (CI/CD tools) | `ai-delivery-outputs` | GitHub repo name where workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI/CD tools) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI/CD tools) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI/CD tools) | `kylo.deng@capco.com` | Sender email for workflow notifications |

> **Note for CI/CD:** `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be set as GitHub repository secrets for the deploy workflow.

---

## 6. Running Tests

```bash
# Run all tests with verbose output
uv run pytest tests/ -v
```

The `deploy.yml` CI workflow runs tests automatically on every push and pull request to `main` using Python 3.13.

```bash
# [TODO: are there specific test markers or categories? e.g. pytest -m unit]
```

> **Note:** The auto-testing workflow (Tool 4) can also generate additional test files and perform coverage gap analysis via GitHub Actions — see `.github/workflows/tool4_auto_testing.yml`.

---

## 7. Deployment

### Prerequisites

- Azure App Service apps named `training-bot-api` and `training-bot-frontend` must exist.
- GitHub repository secrets `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be configured.

### Automatic Deployment (CI/CD)

Deployment is triggered automatically on every push to `main` after tests pass:

```
push to main → test job → deploy-api + deploy-frontend (parallel)
```

The workflow (`deploy.yml`) uses `uv export` to generate a `requirements.txt` before deployment:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Manual PDF Ingestion (after deployment)

```bash
# Trigger re-ingestion via the API endpoint
curl -X POST https://<your-api-hostname>/ingest
```

### GitHub Actions AI Delivery Tools

The five AI tools in `.github/scripts/` are triggered as follows:

| Tool | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, every Monday 08:00 UTC, or manual dispatch |
| Tool 2 — Tech Docs | Push to `main` (non-docs files), every Sunday 06:00 UTC, or manual dispatch |
| Tool 3 — Business Docs | Push of `v*` tag, or manual dispatch with `project_name` and `release_version` inputs |
| Tool 4 — Auto Testing | PR open/sync on `src/**` or `*.py/js/ts`, every Wednesday 07:00 UTC, or manual dispatch |
| Tool 5 — UAT | Creation of a `release/*` branch, or manual dispatch with `uat_mode` and `release_version` inputs |

All tools require `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` as repository secrets, and write outputs to a separate repository named `ai-delivery-outputs`.

---

## 8. Known Issues / TODOs

Extracted from code comments and evidenced gaps:

- **SSL verification disabled** (`verify=False` in `api/main.py` and `core/ingest.py`): The LLM HTTP clients skip TLS certificate verification. This is a security risk in production.
- **Hardcoded email addresses** (`shared.py`): `NOTIFY_EMAIL` and `SENDER_EMAIL` default to `kylo.deng@capco.com` — these should be overridden via environment variables before deploying to a shared environment.
- **`send_email`, `email_html`, `write_audit_entry` not shown**: These functions are referenced in tool scripts but their implementations are truncated in `shared.py`. [TODO: are `send_email`, `email_html`, and `write_audit_entry` fully implemented in `shared.py`?]
- **Frontend start command unknown**: The `README` does not document how to start the frontend. [TODO: what is the command to start the frontend application?]
- **Vector store backend not configured by default**: The `core` package supports Chroma, FAISS, and Pinecone but there is no `.env.example` committed. [TODO: which vector store backend is used in production, and what additional environment variables does it require?]
- **`LLM_TEMPERATURE` print statement** (`api/main.py` line ~22): A `print(f"SHOW_TOOL_CALLS=...")` debug statement is present at module level.
- **`create_agent` import** (`api/agent.py`): `from langchain.agents import create_agent` — `create_agent` is not a standard LangChain export; this may be a placeholder or custom import. [TODO: verify the correct agent factory function used.]
- **Assessor system prompt truncated** (`api/agent.py`): The `ASSESSOR_SYSTEM` string is cut off in the provided source. [TODO: is the assessor system prompt complete in the actual file?]
- **`build_index` function truncated** (`tool2_tech_docs.py`): The function body ends mid-string (`{owner}/{r`).
- **`parse_scenarios` type hint truncated** (`tool5_uat.py`): `list[d` is incomplete.
- **`split_by_words` function truncated** (`core/chunker.py`): The function body is cut off.
- **Rate-limit defaults** (`core/ingest.py`): The docstring mentions `batch_size=20` and `batch_delay=22s` for Voyage AI free tier, but the function signature defaults are `batch_size=126` and `batch_delay=0` — the docstring is stale.
- **Session persistence** (`api/sessions.py`): Sessions are stored in a flat JSON file at `data/sessions.json`. This will not scale horizontally across multiple API instances. [TODO: is a shared/external session store planned for production?]
- **Escalation path** (`tool2_tech_docs.py` runbook template): `# Escalation path [TODO: fill in team contacts]`
- **DR / monitoring not evidenced**: No disaster recovery, health check endpoints, or monitoring/alerting configuration is present in the committed files.