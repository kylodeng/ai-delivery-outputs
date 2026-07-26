# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents, built around a Hong Kong insurance product knowledge base. It provides two core modes: a **Teacher mode** for interactive coaching (explaining products, running exercises, quizzing the agent) and a **Roleplay/Assessment mode** where trainees practice sales conversations with a simulated customer and receive a structured performance assessment. Product knowledge is retrieved via a RAG (Retrieval-Augmented Generation) pipeline backed by ingested insurance PDF documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python async |
| LLM Orchestration | LangGraph / LangChain | Agent-based tool calling |
| LLM Provider | OpenRouter (default) | Configurable via `OPENAI_URL_BASE`; model default `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude (via OpenAI-compatible endpoint) | Default `claude-sonnet-4-6` |
| Embeddings / Vector Store | Voyage AI + Chroma / FAISS / Pinecone | Selectable via `core/vector_store.py` |
| PDF Processing | pdfplumber | Custom chunker + LLM annotator |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Dependency Management | uv | `uv sync` / `uv export` |
| CI/CD | GitHub Actions | Test → Deploy pipeline |
| Hosting | Azure App Service | Separate apps for API and frontend |
| AI Delivery Tooling | Anthropic Claude (`claude-sonnet-4-6`) + SendGrid | 5 workflow automation tools |
| Static File Serving | FastAPI `StaticFiles` | PDFs served under `/docs/` |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      Client (Browser / Chainlit UI)       │
└────────────────────────────┬─────────────────────────────┘
                             │ HTTP / SSE (streaming)
                             ▼
┌──────────────────────────────────────────────────────────┐
│                    FastAPI Backend (api/main.py)           │
│  ┌─────────────────┐   ┌──────────────────────────────┐  │
│  │  Session Manager │   │  Static file server (/docs/) │  │
│  │  (api/sessions) │   │  (PDF documents)             │  │
│  └────────┬────────┘   └──────────────────────────────┘  │
│           │                                               │
│  ┌────────▼────────────────────────────────────────────┐  │
│  │             LangGraph Agents (api/agent.py)          │  │
│  │   Teacher Agent          │   Assessor Agent          │  │
│  │   (streamed via          │   (one-shot ainvoke       │  │
│  │    astream_events)       │    after roleplay ends)   │  │
│  └────────────────┬─────────────────────────────────────┘  │
│                   │ Tool calls                             │
│  ┌────────────────▼─────────────────────────────────────┐  │
│  │              RAG Tools (api/rag_tools.py)             │  │
│  │  search_product │ search_all │ compare_plans │ …      │  │
│  └────────────────┬─────────────────────────────────────┘  │
└───────────────────┼──────────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────────┐
│              Core RAG Library (core/)                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐   │
│  │ chunker  │  │ annotator│  │    vector_store       │   │
│  │(pdfplumb)│  │ (LLM)    │  │ Chroma|FAISS|Pinecone │   │
│  └──────────┘  └──────────┘  └──────────────────────┘   │
└──────────────────────────────────────────────────────────┘

GitHub Actions (.github/workflows/)
  ├── deploy.yml          → test + deploy to Azure App Service
  ├── tool1_code_review   → Claude PR/repo code review → PR comment + output repo
  ├── tool2_tech_docs     → Claude README/ARCH/runbook generation
  ├── tool3_business_docs → Claude solution overview + gap questionnaire
  ├── tool4_auto_testing  → Claude test generation / coverage gap analysis
  └── tool5_uat           → Claude UAT test pack generation / defect report
```

**Key interactions:**
- On startup, `main.py` loads the vector store from disk and initialises RAG tools.
- Each user session (teacher or roleplay) is stored in `data/sessions.json`.
- The Teacher agent streams responses token-by-token via Server-Sent Events; the Assessor agent runs as a single invocation after roleplay ends.
- RAG tools use a per-request `contextvars` source tracker so citation markers (`[[S1]]`, `[[S2]]`, …) in the LLM response map back to specific PDF pages served via `/docs/`.
- PDF ingestion (`core/ingest.py`) can be triggered via `POST /ingest`; it annotates each PDF with an LLM, filters irrelevant pages, chunks content, embeds, and saves to the vector store.

---

## 4. Local Development Setup

**Prerequisites:** Python 3.13, [uv](https://github.com/astral-sh/uv) installed.

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install dependencies**

```bash
uv sync
```

3. **Create environment file**

```bash
cp .env.example .env   # if provided, otherwise create .env manually
```

Populate `.env` with at minimum (see [Environment Variables](#5-environment-variables) below):

```bash
API_KEY=<your-openrouter-or-claude-api-key>
OPENAI_URL_BASE=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-oss-20b:free
```

4. **Place insurance PDF documents** into the data directory:

```
data/Insurance-product-info/
```

5. **Ingest PDFs into the vector store**

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

Or via the API endpoint after starting the server:

```bash
curl -X POST http://localhost:8000/ingest
```

6. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

7. **Open the UI**

Navigate to `http://localhost:8000` (Chainlit UI) or connect from a Vite dev server at `http://localhost:5173`.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter, Anthropic, etc.) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name for chat/agent calls |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | — | Anthropic API key used by the 5 GitHub Actions AI delivery tools |
| `GH_TOKEN` | Yes (CI workflows) | — | GitHub token for API calls in AI delivery workflows |
| `SENDGRID_API_KEY` | Yes (CI workflows) | — | SendGrid key for email notifications from workflows |
| `OUTPUT_REPO` | No (CI workflows) | `ai-delivery-outputs` | GitHub repo name where workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI workflows) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Sender email for workflow notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure publish profile secret for the API App Service (`training-bot-api`) |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure publish profile secret for the frontend App Service (`training-bot-frontend`) |

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with pytest via uv:

```bash
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this automatically on every push and pull request to `main` before any deployment proceeds.

[TODO: What test framework and fixtures are used inside `tests/`? Are there integration tests requiring a live vector store or mocked LLM?]

---

## 7. Deployment

### Automated (GitHub Actions)

Deployments to Azure App Service are triggered automatically on every push to `main` after tests pass:

```
Push to main → test job → deploy-api + deploy-frontend (parallel)
```

The workflow (`deploy.yml`) uses `uv export` to generate a `requirements.txt` before handing off to `azure/webapps-deploy@v3`.

Required GitHub secrets:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API`
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

Target Azure App Services:
- **API:** `training-bot-api`
- **Frontend:** `training-bot-frontend`

### Manual / Local Build

1. **Export dependencies**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Ingest documents** (must be done before the first run if vector store is not pre-built)

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

3. **Start the server**

```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

[TODO: Is there a Dockerfile or container-based deployment path? None was found in the provided files.]

[TODO: How is the frontend app started/built? The frontend App Service is deployed but no frontend build step (e.g. `npm run build`) appears in the workflow.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled for both the sync and async HTTP clients (`verify=False`) — this is a security risk in production |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug statement left in startup code |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the provided source — assessor tool list appears incomplete |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the annotator implementation may be incomplete or partially stubbed |
| `core/ingest.py` | Default `batch_delay=0` with `batch_size=126`; code comment notes that free-tier Voyage AI requires `batch_size=20` and `batch_delay=22s` — misconfiguration will hit rate limits |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all five tools but their implementations are cut off in the provided file |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function references variable `r` which appears to be a typo for `repo` (truncated source) |
| `tool5_uat.py` | `parse_scenarios` function references `list[d` — source is truncated |
| All 5 AI delivery workflow scripts | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` in workflow YAML env blocks, overriding any `.env` defaults |
| `api/sessions.py` | Escalation contacts in runbook generation prompt are `[TODO: fill in team contacts]` |
| General | No Dockerfile found — containerisation path is undocumented |
| General | No DR (disaster recovery) configuration found for the vector store or session persistence |
| General | No monitoring or alerting configuration found |