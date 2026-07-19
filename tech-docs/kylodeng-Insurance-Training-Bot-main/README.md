# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based AI training system for insurance sales agents in a Hong Kong context. It provides two modes: a **Teacher mode** for interactive coaching and product knowledge Q&A, and a **Roleplay/Assessment mode** where agents practise sales conversations against a simulated customer profile and receive a scored performance review. The system ingests insurance product PDFs into a vector store and uses RAG (Retrieval-Augmented Generation) to answer product-specific questions accurately.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 (CI), 3.12 (workflows) |
| LLM Provider | OpenAI-compatible API via OpenRouter | Default model: `openai/gpt-oss-20b:free`; configurable |
| Annotation LLM | Anthropic Claude (via OpenAI-compatible wrapper) | Default: `claude-sonnet-4-6` |
| Agent Framework | LangGraph / LangChain | `langchain`, `langchain-openai`, `langchain-core` |
| Vector Store | Pluggable: Chroma, FAISS (local), Pinecone | Selected via `core/vector_store.py` |
| PDF Processing | pdfplumber | — |
| Embeddings | Voyage AI (free-tier default) | Batch size 126, configurable delay |
| HTTP Client | httpx | SSL verification disabled (see Known Issues) |
| Dependency Management | uv | astral-sh/setup-uv@v3 |
| CI/CD | GitHub Actions | `.github/workflows/` |
| Deployment | Azure App Service | `azure/webapps-deploy@v3` |
| AI Delivery Workflows | Anthropic Claude (`claude-sonnet-4-6`) | Code review, tech docs, biz docs, auto-testing, UAT |
| Email notifications | SendGrid | Via `shared.py` |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────────┐
│                        FastAPI Backend                      │
│  /ingest → ingest_directory() → annotate PDFs (LLM)        │
│           → chunk → embed → vector store (Chroma/FAISS)    │
│                                                             │
│  /chat (teacher mode)                                       │
│    LangGraph Teacher Agent ──► RAG tools ──► vector store  │
│    streamed via astream_events                              │
│                                                             │
│  /assess (assessor mode)                                    │
│    LangGraph Assessor Agent ──► RAG tools ──► vector store  │
│    one-shot via ainvoke                                     │
│                                                             │
│  /docs  → StaticFiles serving data/ directory (PDFs)       │
│  Session state persisted to data/sessions.json             │
└──────────────┬─────────────────────────────────────────────┘
               │ HTTP
┌──────────────▼──────────┐    ┌───────────────────────────┐
│  Chainlit / Vite UI      │    │  GitHub Actions Workflows  │
│  (frontend, port 5173   │    │  Tool 1: Code Review       │
│   or 8000)              │    │  Tool 2: Tech Docs         │
└─────────────────────────┘    │  Tool 3: Business Docs     │
                               │  Tool 4: Auto Testing      │
                               │  Tool 5: UAT Facilitation  │
                               │  → output repo via GH API  │
                               └───────────────────────────┘
```

- The **FastAPI backend** loads the vector store at startup (`lifespan`). If no store exists, it logs a warning and waits for `POST /ingest`.
- **RAG tools** (`api/rag_tools.py`) are injected into LangGraph agents at request time. Source tracking uses `contextvars` to safely accumulate citation metadata across async tool calls.
- **PDF documents** under `data/Insurance-product-info/` are pre-annotated (product name, page relevance) via LLM and cached to `.annot.json` sidecar files to avoid repeat LLM calls.
- **CI/CD** runs tests then deploys API and frontend independently to two Azure App Service instances (`training-bot-api`, `training-bot-frontend`).
- **Five AI delivery workflow scripts** (`.github/scripts/tool*.py`) use the Anthropic API independently of the main application and write outputs to a separate `ai-delivery-outputs` repository.

---

## 4. Local Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
   cd Insurance-Training-Bot-main
   ```

2. **Install uv** (if not already installed)
   ```bash
   curl -Lsf https://astral.sh/uv/install.sh | sh
   ```

3. **Install Python dependencies**
   ```bash
   uv sync
   ```

4. **Create your environment file**
   ```bash
   cp .env.example .env   # [TODO: confirm whether .env.example exists in repo]
   ```
   Edit `.env` and populate the variables listed in the [Environment Variables](#5-environment-variables) section.

5. **Ingest insurance product PDFs**

   Place PDF files under `data/Insurance-product-info/` (subdirectories are supported), then run:
   ```bash
   uv run python -m core.ingest
   ```
   Or, if the server is already running, trigger ingestion via the API:
   ```bash
   curl -X POST http://localhost:8000/ingest
   ```

6. **Start the FastAPI backend**
   ```bash
   uv run uvicorn api.main:app --reload --port 8000
   ```

7. **Start the frontend** (Chainlit or Vite dev server)

   [TODO: confirm frontend start command — no frontend `package.json` or Chainlit entry point was provided in the files]

   Expected origins (already in CORS allowlist): `http://localhost:5173` (Vite) or `http://localhost:8000` (Chainlit).

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the OpenAI-compatible LLM provider (e.g. OpenRouter) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for chat completions |
| `SHOW_TOOL_CALLS` | No | `true` | Stream tool-call events to the UI by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | — | Anthropic API key used by the five GitHub Actions delivery tools |
| `GH_TOKEN` | Yes (CI workflows) | — | GitHub token with repo write access for the output repository |
| `SENDGRID_API_KEY` | Yes (CI workflows) | — | SendGrid key for workflow email notifications |
| `OUTPUT_REPO` | No (CI workflows) | `ai-delivery-outputs` | Name of the GitHub repo where AI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI workflows) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Sender address for workflow notification emails |

> **Note:** The ingestion pipeline also reads `API_KEY` and `OPENAI_URL_BASE` to build its annotation LLM. It defaults the model to `claude-sonnet-4-6` and the base URL to `https://api.anthropic.com/v1` when running the annotator — ensure `API_KEY` is set to an Anthropic key if you want LLM-assisted annotation during ingestion.

---

## 6. Running Tests

Tests live in the `tests/` directory. Run them with:

```bash
uv run pytest tests/ -v
```

The CI pipeline (`.github/workflows/deploy.yml`) runs this exact command on Python 3.13 before any deployment.

[TODO: no test files were provided — confirm test coverage, fixtures, and whether any integration tests require a running vector store or live API key]

---

## 7. Deployment

### Automated (GitHub Actions)

Every push to `main` that passes tests triggers two parallel deploy jobs defined in `.github/workflows/deploy.yml`:

```
main branch push
    └─► test job (pytest)
            ├─► deploy-api      → Azure App Service: training-bot-api
            └─► deploy-frontend → Azure App Service: training-bot-frontend
```

Both jobs export a `requirements.txt` via uv before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

Deployment uses the `azure/webapps-deploy@v3` action. The following secrets must be set in the repository:

| Secret | Used by |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | `deploy-api` job |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | `deploy-frontend` job |

### Manual

1. **Generate requirements.txt**
   ```bash
   uv export --no-dev --format requirements-txt -o requirements.txt
   ```

2. **Deploy to Azure App Service** (replace `<app-name>` and `<publish-profile-xml>`):
   ```bash
   az webapp deploy --name training-bot-api \
     --resource-group <your-resource-group> \
     --src-path . \
     --type zip
   ```
   [TODO: confirm whether a `Procfile`, `startup.sh`, or Azure-specific config file exists for the runtime command]

3. **Ingest documents on first deploy** — call `POST /ingest` after the service starts, or pre-build the vector store locally and include it in the deployment artifact.

### AI Delivery Workflow Tools

The five GitHub Actions tools (code review, tech docs, business docs, auto-testing, UAT) are triggered automatically by repo events or manually via `workflow_dispatch`. They require `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` to be set as repository secrets.

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL certificate verification is **disabled** (`verify=False`) on both the sync and async httpx clients — not suitable for production |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug statement left in production code |
| `api/agent.py` | `from langchain.agents import create_agent` is imported but the teacher and assessor agents are created via `make_teacher_agent` / `make_assessor_agent` functions not shown in the provided files — [TODO: confirm agent construction details] |
| `core/ingest.py` | Default `batch_delay=0` comment contradicts the docstring which says "Default settings (batch_size=20, batch_delay=22s)" — actual call uses `batch_size=126, batch_delay=0` |
| `core/annotator.py` | Custom annotation logic section is truncated with a comment `# custom annotation logic` — [TODO: what additional logic is applied?] |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all five tool scripts but their implementations are truncated in the provided file |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated — the variable `r` appears to be a typo for `repo` |
| `data/sessions.json` | Sessions file is stored inside `data/` which is also used for PDFs and the static file server — [TODO: consider separating mutable state from static assets] |
| `core/chunker.py` | `split_by_words` function is truncated in the provided files |
| General | No disaster recovery, no multi-region setup, and no monitoring/alerting configuration is evident in the codebase |
| General | Vector store backend selection logic (Chroma vs FAISS vs Pinecone) is not shown — [TODO: document which backend is active and how to switch] |