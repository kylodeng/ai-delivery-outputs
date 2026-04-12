# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents, built around a Hong Kong insurance product knowledge base. It provides two modes: an interactive **teacher mode** for guided learning and an **assessor mode** for evaluating agent performance after roleplay sessions. The system uses Retrieval-Augmented Generation (RAG) over ingested insurance product PDFs to ensure factually grounded, product-specific responses.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 |
| LLM Orchestration | LangGraph / LangChain | Agent-based, streamed via `astream_events` |
| LLM Provider | OpenRouter (configurable) | Default: `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude (via OpenAI-compatible base URL) | Default: `claude-sonnet-4-6` |
| Embeddings / RAG | Vector store abstraction (`core/`) | Supports ChromaDB, FAISS, Pinecone |
| PDF Processing | pdfplumber | Custom chunker in `core/chunker.py` |
| Package Manager | uv (astral-sh) | Replaces pip/poetry |
| CI/CD | GitHub Actions | 5 AI-assisted delivery workflows |
| AI Delivery Tools | Anthropic Claude API | `claude-sonnet-4-6` model |
| Deployment | Azure App Service | Two apps: API + Frontend |
| Email Notifications | SendGrid | Via `shared.py` |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| SSL | Disabled (`verify=False`) | [TODO: Is SSL verification intentionally disabled in production?] |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Client / UI                         │
│          (Chainlit or Vite dev server :5173)             │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / SSE streaming
┌───────────────────────▼─────────────────────────────────┐
│                  FastAPI Backend (:8000)                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Sessions   │  │  RAG Tools   │  │  Agent layer   │  │
│  │ (sessions.  │  │ (rag_tools.  │  │  (agent.py)    │  │
│  │   json)     │  │    py)       │  │  Teacher mode  │  │
│  └─────────────┘  └──────┬───────┘  │  Assessor mode │  │
│                           │          └───────┬────────┘  │
│  ┌────────────────────────▼──────────────────▼────────┐  │
│  │              core/ RAG library                      │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ chunker  │  │ ingest   │  │  vector_store    │  │  │
│  │  │  (.py)   │  │  (.py)   │  │ Chroma/FAISS/    │  │  │
│  │  └──────────┘  └──────────┘  │  Pinecone        │  │  │
│  │                               └──────────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  /docs → Static PDF files served from data/               │
└────────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┴──────────────┐
        │   External LLM APIs          │
        │  OpenRouter (chat/roleplay)  │
        │  Anthropic Claude (annotation│
        │  & CI/CD delivery tools)     │
        └──────────────────────────────┘
```

**Component interactions:**

1. On startup, `lifespan()` loads persisted sessions from `data/sessions.json` and loads the vector store index from disk.
2. Incoming chat requests hit FastAPI endpoints, which create or retrieve a `Session` object.
3. The **teacher agent** (`make_teacher_agent`) and **assessor agent** (`make_assessor_agent`) are LangGraph agents equipped with 8 RAG tools defined in `rag_tools.py`.
4. RAG tools query the vector store (Chroma/FAISS/Pinecone) using semantic search and return source-attributed chunks. Source IDs (`S1`, `S2`, …) are tracked per-request via a `contextvars.ContextVar` for async safety.
5. Teacher responses are streamed via `astream_events`; assessor responses are returned via `ainvoke`.
6. PDFs in `data/` are served statically at `/docs/` so the UI can link directly to source documents.
7. Five GitHub Actions workflows invoke Claude-based delivery tools (`tool1`–`tool5`) for code review, documentation generation, test generation, and UAT facilitation.

---

## 4. Local Development Setup

**Prerequisites:** Python 3.13, `uv` installed (`pip install uv` or via [astral.sh](https://astral.sh/uv))

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install dependencies**

```bash
uv sync
```

3. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # or create from scratch
```

4. **Place insurance product PDFs** in the `data/Insurance-product-info/` directory (subdirectories are supported)

5. **Ingest PDFs into the vector store** (run once, or after adding new documents)

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

[TODO: What is the exact module invocation path for the ingest entrypoint — is it `python -m core.ingest` or `python core/ingest.py`?]

6. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

7. **Access the API**

```
http://localhost:8000
http://localhost:8000/docs   ← OpenAPI UI
```

8. **(Optional) Start the frontend dev server** if a Vite frontend is present

```bash
# [TODO: Confirm frontend directory and start command — Vite is referenced in CORS config but no frontend source files were provided]
npm install
npm run dev   # serves on http://localhost:5173
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible chat API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for chat/roleplay/teacher agent |
| `SHOW_TOOL_CALLS` | No | `true` | Stream tool call events to UI by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI/CD workflows) | — | Anthropic API key for Claude-based delivery tools |
| `GH_TOKEN` | Yes (CI/CD workflows) | — | GitHub PAT for reading repos and posting PR comments |
| `SENDGRID_API_KEY` | Yes (CI/CD workflows) | — | SendGrid key for email notifications from delivery tools |
| `OUTPUT_REPO` | No (CI/CD) | `ai-delivery-outputs` | GitHub repo name where delivery tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI/CD) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI/CD) | `kylo.deng@capco.com` | Recipient email for delivery tool notifications |
| `SENDER_EMAIL` | No (CI/CD) | `kylo.deng@capco.com` | Sender email for delivery tool notifications |

> **Note:** For Azure deployment, `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be set as GitHub repository secrets.

---

## 6. Running Tests

Tests are located in the `tests/` directory and run via `pytest`.

```bash
uv run pytest tests/ -v
```

The CI pipeline runs tests automatically on every push and pull request to `main` before any deployment step proceeds (see `.github/workflows/deploy.yml`).

[TODO: Are there any test fixtures, conftest.py settings, or required test environment variables that must be set before running tests locally?]

---

## 7. Deployment

### Automated Deployment (GitHub Actions)

Deployment to Azure App Service is triggered automatically on every push to `main`, after tests pass.

The workflow (`.github/workflows/deploy.yml`) performs these steps:
1. Runs the test suite
2. Exports `requirements.txt` from `uv`
3. Deploys the **API** to the `training-bot-api` Azure App Service
4. Deploys the **Frontend** to the `training-bot-frontend` Azure App Service

**Required GitHub secrets:**

```
AZURE_WEBAPP_PUBLISH_PROFILE_API       ← publish profile from Azure portal for API app
AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND  ← publish profile from Azure portal for frontend app
```

### Manual / First-Time Setup

1. **Generate `requirements.txt`** (if deploying manually)

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy API to Azure App Service** (using Azure CLI)

```bash
az webapp deploy --name training-bot-api --resource-group <rg> --src-path .
```

3. **Ingest documents** on the deployed instance by calling the ingest endpoint:

```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

[TODO: Does a `POST /ingest` HTTP endpoint exist on the FastAPI app, or must ingestion always be run as a CLI command before deployment?]

### AI Delivery Workflows

Five additional GitHub Actions workflows provide AI-assisted delivery automation:

| Workflow | Trigger | Purpose |
|---|---|---|
| `tool1_code_review.yml` | PR open/sync, Monday 08:00 UTC, manual | Claude code review posted as PR comment |
| `tool2_tech_docs.yml` | Push to main, Sunday 06:00 UTC, manual | Auto-generate README, architecture doc, runbook |
| `tool3_business_docs.yml` | Version tag (`v*`), manual | Generate business solution overview + gap questionnaire |
| `tool4_auto_testing.yml` | PR open/sync on src files, Wednesday 07:00 UTC, manual | Generate test files or analyse coverage gaps |
| `tool5_uat.yml` | `release/*` branch creation, manual | Generate UAT test pack or analyse completed results |

All five workflows require `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` as GitHub repository secrets.

---

## 8. Known Issues / TODOs

Extracted from code comments and code patterns:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled (`verify=False`) on all `httpx` clients — this applies to both sync and async clients used for LLM calls. Not suitable for production without a resolution. |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement uses a nested f-string with mismatched quotes: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` — will cause a `SyntaxError` on Python < 3.12 |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the provided source — the assessor's tool list description is cut off |
| `api/agent.py` | Uses `from langchain.agents import create_agent` but `make_teacher_agent` and `make_assessor_agent` functions are not shown in the provided source — full agent construction logic is missing |
| `core/ingest.py` | `_build_ingest_llm()` uses `OPENAI_MODEL` env var but hardcodes the default to `claude-sonnet-4-6` with Anthropic's base URL — this conflicts with the chat agent which defaults to OpenRouter |
| `core/annotator.py` | Custom annotation logic comment `# custom annotation logic` suggests the `annotate_document` function body is incomplete in the provided source |
| `core/chunker.py` | `split_by_words` function body is truncated in the provided source |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all five tool scripts but their implementations are truncated/missing from `shared.py` |
| `.github/scripts/tool1_code_review.py` | `review_pr` function's PR comment body is truncated — the auto-generated footer is incomplete |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function references variable `r` instead of `repo` in the f-string: `{owner}/{r` — likely a bug |
| `api/sessions.py` | `CustomerProfile.describe()` method body is truncated |
| General | Escalation contacts are not defined anywhere — runbook template placeholder `[TODO: fill in team contacts]` |
| General | No disaster recovery or multi-region setup is evident in the deployment configuration |
| General | No monitoring or alerting configuration (e.g. Application Insights) is present in the repo |