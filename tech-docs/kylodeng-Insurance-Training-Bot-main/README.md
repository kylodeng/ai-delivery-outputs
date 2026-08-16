# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for new insurance sales agents, built specifically around a Hong Kong insurance product knowledge base. It provides two interaction modes: a **Teacher mode** for guided learning (explaining products, running exercises, and quizzes) and a **Roleplay/Assessment mode** where the agent practises sales conversations against a simulated customer profile and receives a structured performance assessment. Product knowledge is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests insurance PDFs, so the agent always answers from verified source material rather than from memory.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python async |
| Language Runtime | Python | 3.13 (CI), 3.12 (workflows) |
| Package Manager | uv (Astral) | `uv sync` / `uv export` |
| LLM Integration | LangChain + LangGraph | `langchain-openai`, `langchain-core` |
| LLM Provider | OpenRouter (default) | `openai/gpt-oss-20b:free`; configurable via env |
| Embedding / Annotation LLM | Anthropic Claude | `claude-sonnet-4-6` (ingest annotator) |
| Vector Store | ChromaDB / FAISS / Pinecone | Selectable via `core/vector_store.py` |
| PDF Processing | pdfplumber | Heuristic chunker + LLM annotator |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| CI/CD AI Tools | Anthropic Claude (`claude-sonnet-4-6`) | Code review, doc gen, test gen, UAT |
| Email Notifications | SendGrid | GitHub Actions workflows |
| Deployment Target | Azure App Service | Two apps: API + Frontend |
| HTTP Client | httpx | SSL verification disabled (see Known Issues) |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Client / UI                          │
│          (Chainlit frontend, served at port 8000)           │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP / SSE streaming
┌────────────────────────▼────────────────────────────────────┐
│                   FastAPI Backend (api/)                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │  /chat      │  │  /roleplay   │  │  /ingest  /assess  │  │
│  │  Teacher    │  │  Customer    │  │  (admin endpoints) │  │
│  │  Agent      │  │  Simulation  │  └────────────────────┘  │
│  └──────┬──────┘  └──────┬───────┘                          │
│         │  LangGraph      │  LangGraph                       │
│  ┌──────▼─────────────────▼──────┐                          │
│  │       RAG Tools (rag_tools.py) │                          │
│  │  search_product / search_all   │                          │
│  │  lookup_hospital_network       │                          │
│  │  compare_plans / lookup_excl.  │                          │
│  │  search_claim_procedure        │                          │
│  └──────────────┬────────────────┘                          │
└─────────────────┼───────────────────────────────────────────┘
                  │ similarity search
┌─────────────────▼───────────────────────────────────────────┐
│              Vector Store (core/)                            │
│   ChromaStore | LocalFAISSStore | PineconeStore              │
│   Populated by: ingest_directory() → embed_chunks()          │
└─────────────────────────────────────────────────────────────┘
                  ▲
┌─────────────────┴───────────────────────────────────────────┐
│          Ingestion Pipeline (core/ingest.py)                 │
│   PDF files in data/Insurance-product-info/                  │
│   → pdfplumber extract → LLM annotate → chunk → embed        │
└─────────────────────────────────────────────────────────────┘

GitHub Actions (.github/workflows/):
  tool1_code_review   → Claude PR review + PR comments
  tool2_tech_docs     → Claude README/ARCH/RUNBOOK generation
  tool3_business_docs → Claude solution overview + gap questionnaire
  tool4_auto_testing  → Claude test file generation + coverage gap analysis
  tool5_uat           → Claude UAT test pack generation + defect report
  deploy.yml          → pytest → Azure App Service (API + Frontend)
```

**Key interactions:**

- The **FastAPI backend** exposes streaming (`StreamingResponse`) and standard REST endpoints. The Teacher agent streams responses via `astream_events`; the Assessor agent is invoked with `ainvoke`.
- Both agents share the same **RAG tool set** built in `api/rag_tools.py`. Sources are tracked per-request using Python `contextvars` so concurrent async requests do not mix citation lists.
- **Sessions** are managed in-memory (dict) and periodically serialised to `data/sessions.json`.
- PDF ingestion is a separate offline step (POST `/ingest` or CLI `python -m core.ingest`). The vector store is loaded at startup; the server warns if no store is found.
- PDFs are served statically at `/docs/` so the frontend can link directly to source documents.
- **CI/CD GitHub Actions** call the Anthropic Claude API (separate `claude-sonnet-4-6` key) to provide five automated delivery tools (code review, tech docs, business docs, test generation, UAT) and deploy to Azure on merge to `main`.

---

## 4. Local Development Setup

### Prerequisites

- Python 3.13
- [uv](https://github.com/astral-sh/uv) installed (`pip install uv` or follow official docs)
- Insurance PDF files placed under `data/Insurance-product-info/`

### Steps

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install dependencies**

```bash
uv sync
```

3. **Create your environment file**

```bash
cp .env.example .env   # [TODO: confirm whether .env.example exists in repo]
```

Edit `.env` with your values (see Environment Variables section below).

4. **Ingest insurance PDFs into the vector store** (required before first run)

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

Or via the API after starting the server:

```bash
curl -X POST http://localhost:8000/ingest
```

5. **Start the backend API**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

6. **Access the application**

Open `http://localhost:8000` in your browser. The Chainlit frontend (if configured) is served from the same origin or from `http://localhost:5173` during Vite development.

> [TODO: confirm how the Chainlit / frontend is started — no `chainlit run` or `npm` commands are visible in the provided files]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model identifier for chat/teacher/assessor |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by GitHub Actions AI tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for Actions scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from Actions |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where AI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for AI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for AI tool notifications |

> **Note:** `OPENAI_URL_BASE` defaults to `openrouter.ai`. To use Anthropic directly (e.g. for ingestion), set it to `https://api.anthropic.com/v1`. The ingest pipeline hardcodes `https://api.anthropic.com/v1` as its own default regardless of `OPENAI_URL_BASE`.

---

## 6. Running Tests

Tests are run with pytest via uv:

```bash
uv run pytest tests/ -v
```

This is the same command used in the `test` job of `.github/workflows/deploy.yml`. Tests must pass before any deployment job is triggered.

```bash
# Run a specific test file
uv run pytest tests/test_chunker.py -v

# Run with coverage (if pytest-cov is installed)
uv run pytest tests/ -v --cov=api --cov=core
```

> [TODO: confirm what test files exist under `tests/` — directory contents not provided]

---

## 7. Deployment

### Automatic deployment (CI/CD)

Deployment to **Azure App Service** is fully automated via GitHub Actions and triggers on every push to `main` (after tests pass).

Two separate App Service instances are deployed:

| App Service Name | Secret Required |
|---|---|
| `training-bot-api` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` |
| `training-bot-frontend` | `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` |

The pipeline:
1. Runs `pytest tests/ -v`
2. Generates a `requirements.txt` from the uv lock file:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

3. Deploys using the `azure/webapps-deploy@v3` Action with the publish profile secret.

### Manual steps to set up Azure deployment

1. Create two Azure App Service instances (e.g. via Azure Portal or Azure CLI).
2. Download the publish profiles from each App Service.
3. Add them as repository secrets:
   - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
   - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`
4. Push to `main` — the workflow handles the rest.

### Manual ingestion (one-off, after deployment)

```bash
# On the deployed server or locally pointing at the production store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

Or call the ingest endpoint:

```bash
curl -X POST https://<your-api-app>.azurewebsites.net/ingest
```

---

## 8. Known Issues / TODOs

The following items are extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL certificate verification is **disabled** (`verify=False`) on both the sync and async httpx clients. This applies to all LLM API calls. |
| `api/main.py` | `SHOW_TOOL_CALLS` env var parsing has a print statement with a formatting inconsistency: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` — this uses nested same-quote strings (syntax error in Python < 3.12, works in 3.12+ but is fragile). |
| `api/agent.py` | `from langchain.agents import create_agent` is imported but `make_teacher_agent` and `make_assessor_agent` are referenced in `main.py` — the full agent factory implementations are truncated in the provided files. [TODO: verify the agent factory functions are fully implemented] |
| `core/annotator.py` | Comment `# custom annotation logic` appears after the standard annotation parsing — the custom logic is not shown in the provided files. [TODO: confirm what custom annotation overrides are applied] |
| `core/ingest.py` | Default `batch_delay=0` in `embed_chunks()` with `batch_size=126`. The docstring notes that the original safe settings for Voyage AI free tier were `batch_size=20, batch_delay=22s`. Current defaults may cause rate-limit errors if using a free-tier embedding API. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced throughout the tool scripts but their implementations are truncated (file ends mid-function). |
| `.github/scripts/tool1_code_review.py` | File is truncated — the `review_pr` function comment block is cut off. |
| `.github/scripts/tool2_tech_docs.py` | File is truncated — `build_index` references variable `r` instead of `repo` (apparent typo). |
| `api/rag_tools.py` | File is truncated — `_collect_sources` function is cut off before returning `result`. |
| `data/sessions.json` | Sessions file persists to disk but there is no documented backup or rotation strategy. |
| `ASSESSOR_SYSTEM` (`api/agent.py`) | Prompt references `{profile}` and `{conversation}` template variables — [TODO: confirm these are formatted before the prompt is passed to the LLM] |
| General | No DR (disaster recovery) strategy is documented. Single Azure region deployment with no failover. |
| General | No monitoring or alerting configuration is present in the codebase. |