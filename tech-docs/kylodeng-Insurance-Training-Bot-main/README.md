# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents operating in the Hong Kong market. It provides two interaction modes: a **teacher mode** for guided learning of insurance products and sales techniques, and a **roleplay/assessment mode** where trainees practise conversations with a simulated customer and receive a structured performance evaluation. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests real insurance product PDFs so all product facts are grounded in verified documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, async |
| LLM Inference | OpenAI-compatible API (default: OpenRouter) | Model configurable via `OPENAI_MODEL` env var |
| LLM Client | LangChain / `langchain-openai` | `ChatOpenAI` |
| Agent Framework | LangGraph | `create_agent` from `langchain.agents` |
| Embeddings / Vector Store | Chroma, FAISS, or Pinecone | Selectable via `core/vector_store.py` |
| PDF Processing | pdfplumber | Custom chunker in `core/chunker.py` |
| Document Annotation | LLM-assisted (same model as inference) | Results cached to `.annot.json` sidecar files |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| HTTP Client | httpx | SSL verification disabled (see Known Issues) |
| Dependency Management | uv | `uv sync`, `uv run`, `uv export` |
| Python Version | Python 3.13 (CI/CD) | 3.12 used in AI workflow jobs |
| CI/CD | GitHub Actions | `.github/workflows/` |
| Deployment | Azure App Service | Two apps: `training-bot-api` and `training-bot-frontend` |
| AI Workflow Automation | Anthropic Claude API (`claude-sonnet-4-6`) | Code review, doc gen, test gen, UAT |
| Email Notifications | SendGrid | Used in AI workflow scripts |
| Frontend | [TODO: What framework/technology is the frontend built with?] | Served as a separate Azure App Service |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Client (Browser)                       │
│          Chainlit UI  /  Vite dev server (port 5173)         │
└──────────────────┬───────────────────────────────────────────┘
                   │ HTTP / SSE (streaming)
┌──────────────────▼───────────────────────────────────────────┐
│                    FastAPI Backend  :8000                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Teacher     │  │ Roleplay /   │  │  Ingest endpoint   │  │
│  │ Agent       │  │ Assessor     │  │  POST /ingest      │  │
│  │ (streaming) │  │ Agent        │  └────────┬───────────┘  │
│  └──────┬──────┘  └──────┬───────┘           │              │
│         │                │           ┌────────▼───────────┐  │
│         └────────┬───────┘           │  core/ingest.py    │  │
│                  │                   │  PDF → annotate    │  │
│         ┌────────▼────────┐          │  → chunk → embed   │  │
│         │  api/rag_tools  │          └────────┬───────────┘  │
│         │  (8 LangChain   │                   │              │
│         │   tools)        │          ┌────────▼───────────┐  │
│         └────────┬────────┘          │  Vector Store      │  │
│                  │                   │  (Chroma/FAISS/    │  │
│                  └───────────────────►  Pinecone)         │  │
│                                      └────────────────────┘  │
│  ┌───────────────────────────────┐                           │
│  │  api/sessions.py              │                           │
│  │  JSON-persisted session state │                           │
│  └───────────────────────────────┘                           │
└──────────────────────────────────────────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │  OpenRouter /      │
         │  LLM API           │
         │  (configurable)    │
         └────────────────────┘

Static PDF files served at /docs/* via FastAPI StaticFiles.

GitHub Actions runs five parallel AI workflow tools (code review, tech docs,
business docs, auto test generation, UAT facilitation) using the Anthropic
Claude API independently of the main application.
```

**Key interaction flows:**

1. **Ingestion:** Insurance product PDFs in `data/Insurance-product-info/` are processed by `core/ingest.py`. Each PDF is annotated once by the LLM (product name, page relevance), results cached as `.annot.json` sidecar files. Relevant pages are chunked and embedded into the vector store.
2. **Teacher mode:** The user sends a message → FastAPI streams events from the LangGraph teacher agent → the agent calls RAG tools to look up product facts → responses are streamed back with inline source citations.
3. **Roleplay mode:** The agent simulates a Hong Kong customer (randomly generated profile). After the session ends, the assessor agent is invoked once with the full conversation transcript to produce a scored evaluation.
4. **Sessions:** All conversation state is stored in `data/sessions.json` and loaded at startup.

---

## 4. Local Development Setup

**Prerequisites:** Python 3.13, [uv](https://github.com/astral-sh/uv) installed.

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 2. Install dependencies
uv sync
```

```bash
# 3. Copy and populate the environment file
cp .env.example .env
# Edit .env — see Environment Variables section below
```

```bash
# 4. Place insurance product PDFs into the data directory
# Expected path: data/Insurance-product-info/**/*.pdf
# (subdirectories per product are supported)
```

```bash
# 5. Ingest PDFs into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
# Or via the API endpoint after starting the server:
# curl -X POST http://localhost:8000/ingest
```

```bash
# 6. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 7. Open the application
# API docs: http://localhost:8000/docs
# Chainlit UI (if configured): http://localhost:8000
# Vite dev server (if applicable): http://localhost:5173
```

> **Note:** SSL verification is disabled in the `httpx` clients (`verify=False`). This is intentional for development but should not be used in production as-is (see Known Issues).

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name to use for inference and annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by GitHub Actions AI workflow scripts |
| `GH_TOKEN` | Yes (CI only) | — | GitHub PAT used by GitHub Actions workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for workflow email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where AI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Email address to receive workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for workflow emails |

> **Note:** The `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secrets must be set in GitHub repository secrets for deployment (see Deployment section).

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with pytest via uv.

```bash
# Run all tests with verbose output
uv run pytest tests/ -v
```

The CI pipeline (`.github/workflows/deploy.yml`) runs this same command on every push and pull request to `main` before any deployment proceeds.

[TODO: Are there any test fixtures, conftest.py files, or environment variables required to run tests locally?]

---

## 7. Deployment

Deployment is automated via GitHub Actions on push to `main` (after tests pass). There are two Azure App Service targets: one for the API and one for the frontend.

### Automated deployment (GitHub Actions)

Push to `main` triggers `.github/workflows/deploy.yml`:

1. Tests run on `ubuntu-latest` with Python 3.13.
2. On success, `uv export` generates `requirements.txt`.
3. The API is deployed to Azure App Service `training-bot-api`.
4. The frontend is deployed to Azure App Service `training-bot-frontend`.

**Required GitHub secrets:**

| Secret | Description |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Publish profile for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Publish profile for `training-bot-frontend` App Service |

### Manual deployment steps

```bash
# 1. Export production requirements
uv export --no-dev --format requirements-txt -o requirements.txt
```

```bash
# 2. Deploy API via Azure CLI (alternative to GitHub Actions)
az webapp deploy --resource-group <rg> --name training-bot-api --src-path .
```

[TODO: What startup command is configured in Azure App Service for the FastAPI backend? (e.g. `uvicorn api.main:app --host 0.0.0.0 --port 8000`)]

[TODO: How is the vector store persisted in Azure — is the `data/` directory mounted as a persistent volume, or does ingestion need to be re-run after each deployment?]

### Ingesting documents in production

```bash
# Trigger ingestion via the API endpoint after deployment
curl -X POST https://<your-app>.azurewebsites.net/ingest
```

### GitHub Actions AI Workflow Tools

Five additional workflow tools run independently in GitHub Actions and write outputs to a separate `ai-delivery-outputs` repository:

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude-powered PR review with scoring |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Auto-generates README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Version tag push (`v*`), manual | Generates business solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, Wednesday 07:00 UTC, manual | Generates pytest/jest test files, coverage gap analysis |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses completed results CSV |

These workflows require `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` to be set as GitHub repository secrets.

---

## 8. Known Issues / TODOs

The following issues and TODOs are extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is disabled for all outbound LLM API calls. This must not be used in production without a fix. |
| `api/main.py` | `SHOW_TOOL_CALLS` default is `true`; the `print(f"SHOW_TOOL_CALLS=...")` debug line is left in production code. |
| `api/agent.py` | File is truncated in the provided source — the `ASSESSOR_SYSTEM` prompt and `make_teacher_agent` / `make_assessor_agent` factory functions are incomplete. |
| `core/annotator.py` | Custom annotation logic section is marked with a comment `# custom annotation logic` but the implementation is truncated. |
| `core/chunker.py` | `split_by_words` function body is truncated (`Hard f...` comment visible). |
| `core/ingest.py` | `--pdf-dir` argument default path is truncated in the source. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated. |
| `.github/scripts/tool1_code_review.py` | `review_pr` function comment body is truncated (auto-generated comment template incomplete). |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated (`{r` variable reference incomplete). |
| `.github/scripts/tool3_business_docs.py` | `build_full_output` function is truncated. |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` function is truncated. |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function is truncated. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported throughout the codebase but their implementations are not present in the truncated `shared.py` source. |
| `api/agent.py` | `TEACHER_SYSTEM` notes: "A small age miscalculation can mean quoting the wrong premium band" — ALB (Age Last Birthday) calculation must always use `get_current_date` tool first. |
| `tool5_uat.py` SYSTEM_GENERATE | UAT scenarios requiring tester verification are marked `[TESTER: verify this]` — these must be reviewed before UAT execution. |
| `tool3_business_docs.py` SYSTEM | Escalation path section in runbook is hardcoded as `[TODO: fill in team contacts]`. |
| General | No disaster recovery (DR) configuration is evident in the codebase. |
| General | No monitoring or alerting configuration is evident in the codebase. |
| Deployment | [TODO: What is the Azure App Service startup command for the FastAPI backend?] |
| Deployment | [TODO: How is the `data/` directory (vector store + session JSON) persisted across Azure App Service deployments?] |
| Frontend | [TODO: What technology/framework is the frontend built with?] |