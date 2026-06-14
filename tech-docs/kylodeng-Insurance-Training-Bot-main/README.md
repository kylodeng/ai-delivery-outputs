# Insurance Training Bot

## 1. Project Overview

The Insurance Training Bot is an AI-powered training system for insurance sales agents, focused on the Hong Kong market. It provides two interactive modes: a **teacher mode** for guided learning of insurance concepts and sales techniques, and a **roleplay/assessment mode** where trainees practice sales conversations with a simulated customer profile. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline ingesting real insurance product PDFs, enabling the AI to answer product-specific questions accurately using a searchable vector store.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 (CI), asyncio-native |
| LLM Orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM Provider | OpenRouter (default) / Anthropic | Configurable via `OPENAI_URL_BASE`; default model `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude (via OpenAI-compatible client) | Default `claude-sonnet-4-6` |
| Embeddings / Vector Store | FAISS (local), Chroma, Pinecone | Selectable via `core/vector_store.py` |
| PDF Processing | pdfplumber | Heuristic chunker + LLM annotator |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Frontend (dev) | Vite dev server | `localhost:5173` |
| Frontend (UI framework) | Chainlit | Served at `localhost:8000` |
| Dependency Manager | uv (astral-sh) | `uv sync`, `uv run` |
| CI/CD | GitHub Actions | `.github/workflows/deploy.yml` |
| Hosting | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| AI Tooling Scripts | Python + Anthropic SDK + SendGrid | `.github/scripts/` (5 tools) |
| Email Notifications | SendGrid | Via `shared.py` |
| HTTP Client | httpx | SSL verification disabled (see Known Issues) |

---

## 3. Architecture

```
┌────────────────────────────────────────────────────┐
│                   Client / Chainlit UI              │
│              (Vite dev: :5173 / :8000)              │
└──────────────────────┬─────────────────────────────┘
                       │ HTTP (REST + SSE streaming)
┌──────────────────────▼─────────────────────────────┐
│              FastAPI Backend  (api/main.py)         │
│                                                     │
│  ┌─────────────────┐   ┌─────────────────────────┐ │
│  │  Teacher Agent  │   │  Assessor Agent         │ │
│  │  (astream_events│   │  (ainvoke, one-shot)     │ │
│  │   streamed SSE) │   │                         │ │
│  └────────┬────────┘   └────────────┬────────────┘ │
│           │  LangGraph tool calls   │              │
│  ┌────────▼─────────────────────────▼────────────┐ │
│  │              RAG Tools (api/rag_tools.py)      │ │
│  │  list_products, search_product, search_all,    │ │
│  │  lookup_hospital_network, compare_plans,        │ │
│  │  lookup_exclusions, search_claim_procedure,     │ │
│  │  get_current_date                               │ │
│  └────────────────────┬───────────────────────────┘ │
└───────────────────────┼─────────────────────────────┘
                        │ vector similarity search
┌───────────────────────▼─────────────────────────────┐
│           Vector Store  (core/vector_store.py)       │
│      FAISS (local default) / Chroma / Pinecone       │
│      Populated by:  POST /ingest                     │
│                                                      │
│  Source: data/Insurance-product-info/**/*.pdf        │
│  Pipeline: pdfplumber → annotator (LLM) → chunker   │
│            → embed_chunks → store.save()             │
└──────────────────────────────────────────────────────┘

Sessions:  data/sessions.json  (loaded on startup, written on change)
PDF files: served statically at  /docs/**  via FastAPI StaticFiles
```

**Five GitHub Actions AI tools** (`.github/scripts/`) run independently against any repo and use Anthropic Claude + SendGrid to produce code reviews, technical docs, business docs, auto-generated tests, and UAT packs — writing outputs to a separate `ai-delivery-outputs` GitHub repository.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install uv** (if not already installed)

```bash
curl -Ls https://astral.sh/uv/install.sh | sh
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Copy and populate the environment file**

```bash
cp .env.example .env   # [TODO: confirm whether .env.example exists in repo]
```

Edit `.env` — see the [Environment Variables](#5-environment-variables) section below.

5. **Ingest insurance product PDFs into the vector store**

Place your PDF files under `data/Insurance-product-info/` (subdirectories are supported), then run:

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

Or trigger ingestion via the API after the server starts:

```bash
curl -X POST http://localhost:8000/ingest
```

6. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

7. **Verify the server is running**

```bash
curl http://localhost:8000/docs
```

The interactive API docs (Swagger UI) will be available at `http://localhost:8000/docs`.

8. **(Optional) Start the Vite frontend dev server**

```bash
# [TODO: confirm frontend directory and package manager — no frontend source files were provided]
cd frontend
npm install
npm run dev
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for the chat LLM and annotation LLM |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | — | Anthropic API key used by `.github/scripts/` tools |
| `GH_TOKEN` | Yes (CI tools) | — | GitHub personal access token for reading repos and posting PR comments |
| `SENDGRID_API_KEY` | Yes (CI tools) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI tools) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI tools) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI tools) | `kylo.deng@capco.com` | Recipient email for CI tool notifications |
| `SENDER_EMAIL` | No (CI tools) | `kylo.deng@capco.com` | Sender email for CI tool notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure publish profile secret for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure publish profile secret for `training-bot-frontend` App Service |

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with pytest via uv:

```bash
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this automatically on every push and pull request to `main` before any deployment step proceeds.

[TODO: Are there any pytest fixtures, markers, or configuration in `pytest.ini` / `pyproject.toml` that should be documented here?]

[TODO: Is a running vector store or mock required for the test suite, or are all external calls mocked?]

---

## 7. Deployment

### Automated Deployment (GitHub Actions)

Deployment to Azure App Service is triggered automatically on every push to `main` **after tests pass**:

- **API** → Azure App Service app named `training-bot-api`
- **Frontend** → Azure App Service app named `training-bot-frontend`

Required GitHub repository secrets:

```
AZURE_WEBAPP_PUBLISH_PROFILE_API
AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND
```

The workflow generates a `requirements.txt` from the uv lockfile before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Manual / First-Time Deployment

1. **Export dependencies**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy to Azure App Service** using the Azure CLI or publish profiles

```bash
# [TODO: confirm whether az webapp up or zip-deploy is used — workflow uses azure/webapps-deploy@v3 action]
az webapp up --name training-bot-api --resource-group <your-rg> --runtime "PYTHON:3.13"
```

3. **Set environment variables** in the Azure App Service configuration panel (all variables from the [Environment Variables](#5-environment-variables) section above).

4. **Trigger PDF ingestion** on the deployed instance:

```bash
curl -X POST https://<your-api-app>.azurewebsites.net/ingest
```

### AI Tooling Workflows

The five `.github/workflows/tool*.yml` workflows are independent and require the following repository secrets to be set:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual |
| Tool 3 — Business Docs | Version tag `v*`, manual |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual |
| Tool 5 — UAT | `release/*` branch creation, manual |

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is **disabled** (`verify=False`) on both the sync and async httpx clients — this is a security risk and should not be used in production |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement contains a formatting bug: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` uses nested double-quotes |
| `api/agent.py` | `from langchain.agents import create_agent` is imported but `make_teacher_agent` and `make_assessor_agent` factory functions are not shown in the provided file — implementation may be incomplete |
| `api/sessions.py` | Sessions are persisted to a single flat JSON file (`data/sessions.json`) — no concurrency control; concurrent writes could corrupt state |
| `core/ingest.py` | Default `OPENAI_URL_BASE` in `_build_ingest_llm()` points to `https://api.anthropic.com/v1`, which is different from `main.py`'s default of `https://openrouter.ai/api/v1` — potential misconfiguration |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all tool scripts but their implementations are truncated in the provided file |
| `.github/scripts/tool1_code_review.py` | File is truncated — `review_pr` function body is cut off |
| `.github/scripts/tool2_tech_docs.py` | File is truncated — `build_index` function references undefined variable `r` instead of `repo` |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the `annotate_document` function body is incomplete in the provided source |
| `core/chunker.py` | `split_by_words` function is truncated |
| `tool5_uat.py` | `build_test_pack_csv` function signature uses `list[d` — file is truncated |
| All CI tools | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` in workflow env defaults — should be parameterised |
| `api/agent.py` `ASSESSOR_SYSTEM` | Prompt template is truncated — the full assessor system prompt is not shown |
| General | No disaster recovery or multi-region configuration is evident |
| General | No monitoring or alerting configuration (metrics, log sinks, dashboards) is evident |
| General | `[TODO: confirm whether .env.example exists in the repository]` |
| General | `[TODO: confirm the frontend framework and startup commands — no frontend source files were present]` |