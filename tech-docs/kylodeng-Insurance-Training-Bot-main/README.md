# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered platform that helps new insurance agents in Hong Kong master product knowledge and sales techniques. It provides two interaction modes: a **Teacher mode** for guided learning and interactive coaching, and a **Roleplay/Assessment mode** where agents practise sales conversations against simulated customer profiles that are then scored by an AI assessor. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life insurance product PDFs into a vector store, ensuring all product-specific answers are grounded in real documentation.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, async |
| LLM Orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM Provider | OpenRouter (default) / Anthropic | Configurable via `OPENAI_URL_BASE`; default model `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude (via OpenAI-compatible API) | Default `claude-sonnet-4-6` |
| Embeddings / Vector Store | `core` RAG library | Supports ChromaDB, FAISS (local), Pinecone |
| PDF Parsing | pdfplumber | Heuristic + LLM-annotated chunking |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| CI/CD AI Tools | Anthropic Claude (`claude-sonnet-4-6`) via `anthropic` SDK | Code review, doc gen, test gen, UAT |
| CI/CD Notifications | SendGrid | Email delivery |
| Package Manager | uv (astral-sh) | Replaces pip/poetry |
| Python Version | 3.13 (CI), 3.12 (AI workflow scripts) | See workflows |
| Deployment Target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| Frontend | [TODO: what technology is the frontend — Chainlit UI is referenced in comments but no frontend source files were provided] | Served separately on Azure App Service `training-bot-frontend` |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Client / UI                        │
│          (Chainlit UI or Vite dev server :5173)         │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / SSE (streaming)
┌────────────────────────▼────────────────────────────────┐
│               FastAPI Backend  (api/main.py)            │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Teacher Agent (LangGraph)   make_teacher_agent  │   │
│  │  Assessor Agent (LangGraph)  make_assessor_agent │   │
│  │  Roleplay endpoint           (ChatOpenAI direct) │   │
│  └──────────────────┬───────────────────────────────┘   │
│                     │ tool calls                        │
│  ┌──────────────────▼───────────────────────────────┐   │
│  │          RAG Tools  (api/rag_tools.py)           │   │
│  │  search_product · search_all · compare_plans     │   │
│  │  lookup_hospital_network · lookup_exclusions     │   │
│  │  search_claim_procedure · list_products          │   │
│  │  get_current_date                                │   │
│  └──────────────────┬───────────────────────────────┘   │
│                     │                                   │
│  ┌──────────────────▼───────────────────────────────┐   │
│  │       Vector Store  (core/vector_store.py)       │   │
│  │   ChromaDB | LocalFAISSStore | PineconeStore     │   │
│  └──────────────────┬───────────────────────────────┘   │
└────────────────────────────────────────────────────────-┘
                      │ loaded at startup (POST /ingest)
┌─────────────────────▼───────────────────────────────────┐
│           Ingestion Pipeline  (core/ingest.py)          │
│  PDFs → pdfplumber → LLM annotation → chunker → embed  │
│  data/Insurance-product-info/**/*.pdf                   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│        GitHub Actions AI Delivery Workflows             │
│  Tool 1: Claude code review   (on PR open/sync)         │
│  Tool 2: Tech doc generation  (on push to main)         │
│  Tool 3: Business doc gen     (on release tag)          │
│  Tool 4: Auto test generation (on PR / Wednesday cron)  │
│  Tool 5: UAT facilitation     (on release branch)       │
│  shared.py → Claude API + GitHub API + SendGrid         │
└─────────────────────────────────────────────────────────┘
```

**Data flow summary:**

1. PDF product documents are ingested via `POST /ingest` (or the CLI in `core/ingest.py`), annotated by an LLM, chunked, embedded, and saved to the vector store.
2. At startup, `api/main.py` loads the persisted vector store and wires RAG tools to the LangGraph agents.
3. When a user sends a message, the FastAPI backend routes it to either the Teacher agent (streaming via `astream_events`) or the Roleplay endpoint (direct `ChatOpenAI` call); the Assessor agent is invoked after a roleplay session ends.
4. The Teacher/Assessor agents call RAG tools to retrieve grounded product information before responding.
5. Sessions (conversation history, customer profiles, mode) are persisted to `data/sessions.json`.
6. Static PDF files are served under `/docs/` by FastAPI so the UI can deep-link to source documents.

---

## 4. Local Development Setup

**Prerequisites:** Python 3.13, `uv` installed globally.

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
# 3. Install all dependencies (including dev)
uv sync
```

```bash
# 4. Copy and fill in environment variables
cp .env.example .env
# Edit .env — see Environment Variables section below
```

```bash
# 5. Ingest insurance product PDFs into the vector store
#    Place PDFs under data/Insurance-product-info/ then run:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

```bash
# 6. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 7. (Optional) Trigger ingestion via the API instead of CLI
curl -X POST http://localhost:8000/ingest
```

The API will be available at `http://localhost:8000`. The Chainlit UI (if running separately) should be pointed at `http://localhost:8000`.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name for the agent and roleplay endpoints |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | — | Anthropic API key used by GitHub Actions AI tools and document annotation LLM |
| `GH_TOKEN` | Yes (CI workflows) | — | GitHub personal access token for Actions workflows (read/write repo) |
| `SENDGRID_API_KEY` | Yes (CI workflows) | — | SendGrid API key for email notifications from AI delivery workflows |
| `OUTPUT_REPO` | No (CI) | `ai-delivery-outputs` | GitHub repo name where AI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI) | `kylo.deng@capco.com` | Email address for workflow notification delivery |
| `SENDER_EMAIL` | No (CI) | `kylo.deng@capco.com` | Sender email address for SendGrid |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure publish profile secret for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure publish profile secret for `training-bot-frontend` App Service |

> **Note:** `OPENAI_URL_BASE` can be pointed at `https://api.anthropic.com/v1` with the appropriate `API_KEY` to use Anthropic directly instead of OpenRouter. The ingestion LLM defaults to `claude-sonnet-4-6` via `OPENAI_URL_BASE`.

---

## 6. Running Tests

```bash
# Run the full test suite
uv run pytest tests/ -v
```

```bash
# Run with coverage (if pytest-cov is installed)
uv run pytest tests/ -v --cov=api --cov=core
```

> [TODO: Are there any test fixtures or environment variables required to run the tests locally (e.g. a mock vector store, dummy API keys)?]

---

## 7. Deployment

Deployment is handled automatically by the **Test & Deploy** GitHub Actions workflow (`.github/workflows/deploy.yml`) on every push to `main`, after tests pass.

### Automatic deployment (CI/CD)

```
Push to main → run tests → deploy API → deploy Frontend
```

Both deployments use `azure/webapps-deploy@v3` with publish profiles stored as repository secrets.

### Manual deployment steps

```bash
# 1. Install uv
curl -Lsf https://astral.sh/uv/install.sh | sh

# 2. Generate requirements.txt from lockfile
uv export --no-dev --format requirements-txt -o requirements.txt
```

```bash
# 3. Deploy API to Azure App Service (requires Azure CLI logged in)
az webapp deployment source config-zip \
  --resource-group <your-resource-group> \
  --name training-bot-api \
  --src <zip of repo>
```

```bash
# 4. Deploy Frontend to Azure App Service
az webapp deployment source config-zip \
  --resource-group <your-resource-group> \
  --name training-bot-frontend \
  --src <zip of repo>
```

> [TODO: What is the exact Azure resource group name and region for this deployment?]

> [TODO: Is there a startup command configured on the Azure App Service (e.g. `uvicorn api.main:app --host 0.0.0.0 --port 8000`)?]

### First-time vector store ingestion on Azure

After deploying, the vector store must be populated by calling the ingest endpoint:

```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled (`verify=False`) on all httpx clients — this should be replaced with proper certificate handling for production. |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug print left in production startup code. |
| `api/agent.py` | `from langchain.agents import create_agent` — the agent construction body is truncated in source; full wiring of Teacher and Assessor agents with tools is not visible. [TODO: confirm the complete `make_teacher_agent` and `make_assessor_agent` implementations] |
| `api/rag_tools.py` | Per-request source tracking uses a `contextvars.ContextVar` holding a mutable list; concurrent requests sharing the same event loop may need care if the context is not reset correctly per request. |
| `.github/scripts/tool1_code_review.py` | Tool script is truncated — the `review_pr` function comment block and email/audit calls are incomplete. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated — the f-string references `r` which appears to be a typo for `repo`. |
| `.github/scripts/tool4_auto_testing.py` | `build_test_pack_csv` function is truncated. |
| `.github/scripts/tool5_uat.py` | `parse_scenarios` type hint `list[d` is truncated. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced throughout but their implementations are truncated/missing from the file. |
| `core/annotator.py` | `annotate_document` function contains a comment `# custom annotation logic` suggesting the implementation is incomplete or was redacted. |
| `core/chunker.py` | `split_by_words` function is truncated. |
| `core/ingest.py` | CLI `--pdf-dir` argument default path expression is truncated. |
| `data/sessions.json` | Sessions file is stored inside the `data/` directory which is also used for static file serving — consider separating mutable state from static assets. |
| Escalation path | `tool2_tech_docs.py` runbook prompt explicitly notes `[TODO: fill in team contacts]` for escalation. |
| Monitoring | No monitoring or alerting infrastructure is evident in the codebase (no Prometheus, Application Insights, or structured logging beyond `logging.basicConfig`). |
| DR / multi-region | No disaster recovery or multi-region deployment configuration is present. |
| Frontend source | No frontend source files are present in the repository. [TODO: Where is the frontend code — is it a separate repository?] |
| `.env.example` | [TODO: Confirm whether an `.env.example` file exists in the repository for local setup reference] |