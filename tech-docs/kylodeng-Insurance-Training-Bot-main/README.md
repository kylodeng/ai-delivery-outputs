# Insurance Training Bot

## 1. Project Overview

The Insurance Training Bot is an AI-powered training system designed to help new insurance agents in Hong Kong master product knowledge and sales techniques. It provides two modes: a **teacher mode** for interactive coaching and concept explanation, and a **roleplay/assessment mode** where the agent practises selling to a simulated customer and receives a structured performance assessment. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests insurance product PDFs so the AI can answer product-specific questions accurately without hallucinating.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, async |
| Agent Framework | LangGraph / LangChain | `create_agent`, `astream_events`, `ainvoke` |
| LLM (runtime) | OpenAI-compatible endpoint | Default: `openai/gpt-oss-20b:free` via OpenRouter; configurable |
| LLM (ingestion/annotation) | Anthropic Claude via OpenAI-compat. wrapper | Default: `claude-sonnet-4-6` |
| Embeddings / Vector Store | `core` library (FAISS, Chroma, or Pinecone) | Selectable via env; FAISS used locally |
| PDF Processing | `pdfplumber` | Custom chunker + LLM annotator |
| HTTP Client | `httpx` | SSL verification disabled (see Known Issues) |
| Dependency Management | `uv` | `uv sync` / `uv export` |
| CI/CD | GitHub Actions | 3 jobs: test, deploy-api, deploy-frontend |
| Deployment Target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| AI Workflow Tooling | Anthropic Claude (`claude-sonnet-4-6`) | 5 GitHub Actions tools for code review, docs, UAT, etc. |
| Email Notifications | SendGrid | Via `shared.py` |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Environment Config | `python-dotenv` | `.env` file |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Client (Browser / Chainlit UI)            │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP / SSE (streaming)
┌────────────────────────────▼─────────────────────────────────────┐
│                        FastAPI  (api/main.py)                    │
│  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ Teacher Agent│  │ Roleplay / NPC   │  │ Assessor Agent     │  │
│  │ (LangGraph)  │  │ (direct ChatLLM) │  │ (LangGraph, ainvoke│  │
│  └──────┬───────┘  └──────────────────┘  └────────┬───────────┘  │
│         │ RAG tools                               │ RAG tools     │
│  ┌──────▼─────────────────────────────────────────▼───────────┐  │
│  │                  api/rag_tools.py                           │  │
│  │  list_products · search_product · search_all                │  │
│  │  lookup_hospital_network · compare_plans                    │  │
│  │  lookup_exclusions · search_claim_procedure                 │  │
│  │  get_current_date                                           │  │
│  └──────────────────────────┬──────────────────────────────────┘  │
│                             │                                     │
│  ┌──────────────────────────▼──────────────────────────────────┐  │
│  │             core/ — Vector Store (FAISS/Chroma/Pinecone)    │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Session Store  (data/sessions.json)                         │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘

Ingestion (offline / POST /ingest):
  data/Insurance-product-info/**/*.pdf
       │
  core/annotator.py  ──► LLM (Claude) → .annot.json sidecar cache
       │
  core/chunker.py    ──► semantic text chunks
       │
  core/ingest.py     ──► embed_chunks() → vector store saved to disk
```

- **PDF ingestion** is a one-time (or on-demand) step. Each PDF is annotated by an LLM to extract product metadata and per-page relevance; results are cached as `.annot.json` sidecar files so re-ingestion does not repeat expensive LLM calls.
- **RAG tools** are LangChain `@tool`-decorated functions that query the vector store and track source citations per-request using `contextvars` (async-safe).
- **Session state** (conversation history, customer profiles, mode) is persisted to `data/sessions.json` and reloaded on startup.
- **Five GitHub Actions workflows** (`.github/workflows/tool1–5`) run AI-powered delivery tools (code review, tech docs, business docs, auto-testing, UAT) using Claude via `shared.py`.

---

## 4. Local Development Setup

> **Prerequisites:** Python 3.13, [`uv`](https://github.com/astral-sh/uv) installed, and a copy of the insurance PDF documents placed under `data/Insurance-product-info/`.

### Step-by-step

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install dependencies with uv**

```bash
uv sync
```

3. **Create your `.env` file** (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # if an example file exists, otherwise create manually
```

4. **Ingest the insurance PDFs** (builds the vector store; must be done before first run)

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

   Alternatively, once the API is running, call:

```bash
curl -X POST http://localhost:8000/ingest
```

5. **Start the FastAPI server**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

6. **Open the UI**

   Navigate to `http://localhost:8000` in your browser (Chainlit UI is served from the same origin, or connect the Vite dev server at `http://localhost:5173`).

> **Note:** SSL certificate verification is disabled for outbound LLM calls (`verify=False` in `httpx` clients). This is intentional for environments behind a corporate proxy but is a known security trade-off — see [Known Issues](#8-known-issues--todos).

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for the runtime LLM (teacher/roleplay/assessor) |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per-session in UI |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | — | Anthropic API key used by the five GitHub Actions AI tools |
| `GH_TOKEN` | Yes (CI tools) | — | GitHub personal access token for the CI workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI tools) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI tools) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI tools) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI tools) | `kylo.deng@capco.com` | Recipient email for CI tool notifications |
| `SENDER_EMAIL` | No (CI tools) | `kylo.deng@capco.com` | Sender email for CI tool notifications |

> GitHub Actions secrets required: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`.

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with `pytest`.

```bash
# Run all tests
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this automatically on every push and pull request to `main` before any deployment proceeds.

[TODO: What test framework and fixtures are used — no test files were provided in the repository snapshot]

[TODO: Is there a minimum coverage threshold enforced in CI?]

---

## 7. Deployment

### Automated (CI/CD — GitHub Actions)

Deployment is triggered automatically on every push to `main` after tests pass. Two Azure App Service targets are deployed in parallel:

| Target | App Service Name | Workflow job |
|---|---|---|
| Backend API | `training-bot-api` | `deploy-api` |
| Frontend | `training-bot-frontend` | `deploy-frontend` |

The workflow:

1. Exports a `requirements.txt` from `uv` (no dev dependencies)
2. Deploys using the `azure/webapps-deploy@v3` action with a publish profile secret

No manual steps are required for routine deploys — merge to `main` triggers everything.

### Manual / First-time Setup

1. **Create the two Azure App Services** (`training-bot-api` and `training-bot-frontend`) in your Azure subscription.

2. **Download the publish profiles** from the Azure Portal for each app and add them as GitHub repository secrets:
   - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
   - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

3. **Add all required secrets** to the GitHub repository (Settings → Secrets and variables → Actions).

4. **Ingest the vector store** after first deploy:

```bash
curl -X POST https://<your-api-app>.azurewebsites.net/ingest
```

[TODO: How is the `data/` directory (PDFs, sessions.json, vector store index) made available to the App Service — is it bundled in the deploy artifact, mounted as persistent storage, or pre-seeded?]

[TODO: Are any startup commands or App Service configuration (e.g. `WEBSITES_PORT`, startup script) required?]

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` — SSL certificate verification is disabled for all outbound LLM calls. This is a security risk in production and should be replaced with proper CA bundle configuration. |
| `api/main.py` | `SHOW_TOOL_CALLS` env var parsing contains a subtle bug: `os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"` uses a space as the default, meaning the `print` statement will always show `False` even when the env var is set correctly. |
| `api/agent.py` | File is truncated in the snapshot — the `ASSESSOR_SYSTEM` prompt and `make_teacher_agent` / `make_assessor_agent` factory functions are referenced but not fully shown. |
| `core/chunker.py` | `split_by_words` function is truncated — the hard-word-count fallback split logic is incomplete in the snapshot. |
| `core/ingest.py` | Default `OPENAI_URL_BASE` in `_build_ingest_llm()` points to `https://api.anthropic.com/v1` but uses `ChatOpenAI` — the ingestion LLM endpoint and model (`claude-sonnet-4-6`) may require an Anthropic-compatible OpenAI wrapper or a dedicated Anthropic client. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated — references `{r` (likely `{repo}`) suggesting an f-string that is cut off. |
| `.github/scripts/tool1_code_review.py` | `review_pr` function is truncated — the PR comment posting and report writing logic after `comment =` is cut off. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated — the full string-building logic is not shown. |
| General | No disaster recovery, multi-region failover, or database backup strategy is evident from the code. |
| General | No monitoring or alerting configuration (e.g. Application Insights, log aggregation) is visible. |
| General | Sessions are persisted to a local JSON file (`data/sessions.json`) — this will not survive App Service restarts or scale-out to multiple instances without a shared storage solution. |
| `data/*.annot.json` | Annotation cache files are committed to the repository; a `.gitignore` entry or storage strategy for these should be defined. |