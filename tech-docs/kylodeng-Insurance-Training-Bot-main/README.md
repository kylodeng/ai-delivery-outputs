# Insurance Training Bot

## 1. Project Overview

The Insurance Training Bot is a FastAPI-based web application that helps new insurance agents master product knowledge and sales skills through two modes: an interactive teacher/chat mode powered by RAG (Retrieval-Augmented Generation) over insurance product PDFs, and a roleplay assessment mode where the agent practises with a simulated customer and receives a scored performance report. Insurance product documents are ingested, chunked, annotated with an LLM, and stored in a vector database so that all agent answers are grounded in real policy documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python async |
| LLM orchestration | LangChain / LangGraph | `langchain-core`, `langchain-openai` |
| LLM provider | OpenRouter (default) / Anthropic-compatible | Configured via `OPENAI_URL_BASE`; default model `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude (via LangChain OpenAI shim) | Default `claude-sonnet-4-6` for ingestion |
| Embedding / vector store | `core` library (supports Chroma, FAISS, Pinecone) | Backend selected via env config |
| PDF parsing | `pdfplumber` | — |
| HTTP client | `httpx` | SSL verification disabled in dev |
| Package manager | `uv` (astral) | Python 3.13 (CI), 3.12 (workflow scripts) |
| Dependency format | `pyproject.toml` + lockfile; `requirements.txt` exported for Azure | — |
| CI/CD | GitHub Actions | `.github/workflows/` |
| Hosting | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| Session persistence | JSON file (`data/sessions.json`) | Survives restarts |
| AI delivery workflows | Anthropic Claude (`claude-sonnet-4-6`) via `anthropic` SDK | Five automation tools |
| Email notifications | SendGrid | Workflow scripts only |
| Environment config | `python-dotenv` | `.env` file |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Client (Browser / Chainlit UI)         │
│                  Vite dev server (localhost:5173)            │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP / SSE (streaming)
┌────────────────────────▼────────────────────────────────────┐
│                  FastAPI backend (main.py)                   │
│  • /ingest   — PDF ingestion endpoint                        │
│  • /chat     — teacher mode (streaming via astream_events)   │
│  • /roleplay — customer simulation (streaming)               │
│  • /assess   — post-roleplay assessment (one-shot)           │
│  • /docs/*   — static PDF file server                        │
│  • session CRUD endpoints                                    │
└──────┬──────────────────────────────────────┬───────────────┘
       │                                      │
┌──────▼──────────┐                 ┌─────────▼──────────────┐
│  LangGraph      │                 │   core RAG library      │
│  Agents         │                 │   (core/)               │
│  • Teacher      │◄────tools───────│   • annotator.py        │
│  • Assessor     │                 │   • chunker.py          │
└─────────────────┘                 │   • ingest.py           │
                                    │   • vector_store.py     │
                                    │   (Chroma/FAISS/Pine)   │
                                    └─────────────────────────┘
                                              │
                                    ┌─────────▼──────────────┐
                                    │   data/                 │
                                    │   Insurance PDFs +      │
                                    │   .annot.json sidecars  │
                                    │   sessions.json         │
                                    └─────────────────────────┘

GitHub Actions (5 AI delivery workflows):
  tool1_code_review  → Claude code review on PRs
  tool2_tech_docs    → README / architecture / runbook generation
  tool3_business_docs→ Solution overview + gap questionnaire
  tool4_auto_testing → pytest/jest test generation & gap analysis
  tool5_uat          → UAT test pack generation & defect analysis
  All share shared.py (Claude API, GitHub API, SendGrid, audit logging)
```

- **RAG tools** (`api/rag_tools.py`) expose eight LangChain tools (product search, hospital network lookup, exclusions, claim procedures, plan comparison, etc.) to the LangGraph agents via async-safe `contextvars` source tracking.
- **Sessions** (`api/sessions.py`) are stored in `data/sessions.json` and loaded on startup. Each session carries its conversation mode (`teacher` or `roleplay`), message history, and an optional generated `CustomerProfile`.
- **PDF ingestion** runs through `core/ingest.py`: PDFs are walked recursively, annotated once per file (sidecar `.annot.json`), chunked by `core/chunker.py`, embedded, and stored in the configured vector store.
- **GitHub Actions workflows** are independent of the runtime application and call the shared `anthropic` + `requests` scripts directly.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install `uv`** (if not already installed)

```bash
curl -Lsf https://astral.sh/uv/install.sh | sh
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Create your environment file**

```bash
cp .env.example .env   # if an example file exists, otherwise create .env manually
```

Then edit `.env` with your credentials (see [Environment Variables](#5-environment-variables)).

5. **Place insurance product PDFs** under `data/Insurance-product-info/` (any sub-folder structure is supported).

6. **Ingest PDFs into the vector store**

```bash
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose
```

Alternatively, once the server is running, call:

```bash
curl -X POST http://localhost:8000/ingest
```

7. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

8. **Access the API**

- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Static PDF files: [http://localhost:8000/docs/](http://localhost:8000/docs/)

> [TODO: Is there a separate frontend (Vite/Chainlit) startup command? The CORS config references `localhost:5173` and `localhost:8000` — please confirm if Chainlit serves the UI on port 8000 or if a separate `npm run dev` step is required.]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API (swap to `https://api.anthropic.com/v1` for Anthropic direct) |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name passed to LangChain ChatOpenAI; also used for ingestion annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Stream tool-call events to the UI by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key for the five GitHub Actions AI delivery workflows |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for workflow scripts (read repos, post PR comments, write output repo) |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key used by workflow scripts to send email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Email address to notify on workflow completion |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | From address for SendGrid emails |

> [TODO: What vector store backend is used by default — Chroma, FAISS, or Pinecone? Are there additional env variables required to configure the vector store (e.g. `PINECONE_API_KEY`, `CHROMA_HOST`)?]

> [TODO: Are there any embedding model environment variables (e.g. Voyage AI API key) required? The `core/ingest.py` comments reference Voyage AI rate limits.]

---

## 6. Running Tests

Tests live in the `tests/` directory and are run with `pytest` via `uv`:

```bash
uv run pytest tests/ -v
```

The CI pipeline (`.github/workflows/deploy.yml`) runs this automatically on every push and pull request to `main` using Python 3.13.

> [TODO: No test files were visible in the provided sources. What is the current test coverage and are there specific test configuration files (e.g. `pytest.ini`, `pyproject.toml` `[tool.pytest]` section)?]

---

## 7. Deployment

### Automated (CI/CD via GitHub Actions)

On every push to `main` (after tests pass), two Azure App Service deployments are triggered automatically:

- **API**: deploys to Azure App Service app named `training-bot-api`
- **Frontend**: deploys to Azure App Service app named `training-bot-frontend`

Required GitHub repository secrets:

| Secret | Purpose |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Publish profile for `training-bot-api` |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Publish profile for `training-bot-frontend` |

The workflow auto-generates `requirements.txt` from the lockfile before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Manual deployment steps

1. **Export dependencies**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy API to Azure App Service**

```bash
az webapp deploy --resource-group <rg> --name training-bot-api --src-path . --type zip
```

> [TODO: Confirm the Azure resource group name and region. Confirm whether the `data/` directory (vector store index + PDFs) needs to be pre-loaded onto the App Service or re-ingested after deploy via the `/ingest` endpoint.]

3. **Ingest documents after first deploy** (if vector store is not bundled)

```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

### GitHub Actions AI Delivery Tools (CI-only)

The five workflow tools in `.github/workflows/tool*.yml` require these secrets set in the repository:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

Workflows can also be triggered manually via **Actions → workflow → Run workflow** in the GitHub UI.

---

## 8. Known Issues / TODOs

Extracted from code comments and source files:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL certificate verification is disabled (`verify=False`) for both sync and async `httpx` clients — **not safe for production** |
| `api/main.py` | `SHOW_TOOL_CALLS` env-var print statement uses an f-string with embedded quotes that will raise a `SyntaxError` in Python < 3.12: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` |
| `api/agent.py` | `make_teacher_agent` and `make_assessor_agent` are imported in `main.py` but the function bodies in `agent.py` are not shown beyond the `ASSESSOR_SYSTEM` string — `from langchain.agents import create_agent` is a non-existent import in modern LangChain |
| `api/sessions.py` | Sessions are persisted to a local JSON file (`data/sessions.json`) — this does not scale horizontally across multiple App Service instances |
| `core/ingest.py` | Default `batch_delay=0` in `embed_chunks` — the code comment states the original safe default for Voyage AI free tier was `batch_delay=22s` with `batch_size=20`; the current defaults may hit rate limits on free-tier embedding APIs |
| `core/annotator.py` | Custom annotation logic comment (`# custom annotation logic`) indicates the `annotate_document` function body is incomplete in the provided source |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function references `r` (undefined variable) instead of `repo` in the f-string |
| `.github/scripts/tool1_code_review.py` | `review_pr` function and email/audit calls reference `send_email`, `email_html`, and `write_audit_entry` which are declared in `shared.py` but not shown in the provided excerpt — confirm these are fully implemented |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced throughout but their implementations are truncated in the provided source |
| General | No disaster recovery, health check endpoints, or monitoring/alerting configuration is evident in the codebase |
| General | No authentication or authorisation is implemented on the FastAPI endpoints |