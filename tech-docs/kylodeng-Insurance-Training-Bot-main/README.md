# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based application that helps new insurance agents in Hong Kong master sales skills through two AI-powered modes: a **Teacher mode** for guided learning and interactive coaching, and a **Roleplay/Assessment mode** where the agent practices with a simulated customer and receives a structured performance evaluation. The system uses a Retrieval-Augmented Generation (RAG) pipeline over a corpus of Sun Life insurance product PDFs, so all product-specific answers are grounded in real document content rather than LLM memory.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 (CI), 3.13+ recommended |
| LLM Orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM Provider | OpenRouter (default) / Anthropic | Configurable via `OPENAI_URL_BASE`; default model `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude (via `OPENAI_URL_BASE`) | Used during PDF ingestion; model set by `OPENAI_MODEL` |
| Embeddings / Vector Store | Chroma, FAISS, or Pinecone | Selectable via `core/vector_store.py`; default is local |
| PDF Processing | pdfplumber | Chunking and text extraction |
| Dependency Management | uv (astral-sh) | `uv sync`, `uv export` |
| CI/CD | GitHub Actions | Workflows for test, deploy, code review, docs, UAT |
| Deployment | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| AI Delivery Workflows | Anthropic Claude (`claude-sonnet-4-6`) | 5 tools: code review, tech docs, business docs, auto testing, UAT |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| CORS / HTTP | httpx | SSL verification disabled in dev (`verify=False`) |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Backend (api/)                │
│                                                         │
│  /chat (teacher)  ──► LangGraph Teacher Agent           │
│  /roleplay        ──► LangGraph Roleplay (customer sim) │
│  /assess          ──► LangGraph Assessor Agent          │
│  /ingest          ──► Ingestion pipeline                │
│  /docs            ──► Static PDF file server            │
└────────────┬────────────────────┬───────────────────────┘
             │                    │
             ▼                    ▼
    RAG Tools (api/rag_tools.py)  Sessions (api/sessions.json)
    [8 LangChain tools]
             │
             ▼
    Vector Store (core/vector_store.py)
    [Chroma | FAISS | Pinecone]
             ▲
             │  ingest_directory() + embed_chunks()
             │
    PDF Corpus (data/Insurance-product-info/*.pdf)
             │
    Annotator (core/annotator.py)  ← LLM-based per-doc/page metadata
    Chunker   (core/chunker.py)    ← heuristic text splitting
    .annot.json sidecar files      ← annotation cache
```

**Data flow:**
1. PDFs under `data/Insurance-product-info/` are processed once by the ingestion pipeline (`core/ingest.py`). Each PDF is annotated by the LLM (product name, doc type, per-page relevance), chunked, embedded, and stored in the vector store. Annotations are cached as `.annot.json` sidecar files to avoid repeated LLM calls.
2. At startup, FastAPI loads the vector store and session file into memory.
3. On a user request, the appropriate LangGraph agent (Teacher or Assessor) is invoked. The agent calls RAG tools (`search_product`, `compare_plans`, `lookup_exclusions`, etc.) which query the vector store and return cited document excerpts.
4. The Teacher agent streams responses via `astream_events`; the Assessor agent returns a one-shot evaluation after a roleplay session ends.
5. Source citations are tracked per-request using Python `contextvars` so async tool calls across multiple LangGraph tasks share the same citation list safely.

**GitHub Actions AI Delivery Tools** (`.github/scripts/`) run as separate CI workflows and use the Anthropic Claude API directly to auto-generate code reviews, technical documentation, business documentation, test files, and UAT test packs for the repository itself.

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

4. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # if template exists, otherwise create manually
```

5. **Add insurance product PDFs** to the data directory

```
data/Insurance-product-info/
```

6. **Ingest PDFs into the vector store** (run once, or whenever PDFs change)

```bash
uv run python core/ingest.py
```

   Or via the API endpoint after starting the server:

```bash
curl -X POST http://localhost:8000/ingest
```

7. **Start the FastAPI development server**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

8. **Verify the API is running**

```bash
curl http://localhost:8000/docs
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; per-session toggle available in Chainlit UI |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | — | Anthropic API key used by the 5 GitHub Actions AI delivery tools |
| `GH_TOKEN` | Yes (CI workflows) | — | GitHub personal access token for the AI delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI workflows) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI workflows) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI workflows) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Recipient email for CI workflow notifications |
| `SENDER_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Sender email for CI workflow notifications |

> **Note:** `httpx` is configured with `verify=False` (SSL verification disabled). Do not use this in production without re-enabling certificate verification.

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with `pytest`.

```bash
uv run pytest tests/ -v
```

The CI pipeline (`.github/workflows/deploy.yml`) runs tests automatically on every push and pull request to `main` before any deployment proceeds.

[TODO: What test framework and fixtures are used in `tests/`? No test files were provided to confirm coverage or test helpers.]

---

## 7. Deployment

Deployment is handled automatically by the **Test & Deploy** GitHub Actions workflow (`.github/workflows/deploy.yml`) on every push to `main` that passes tests.

### Automated deployment (via GitHub Actions)

The workflow performs these steps automatically:

1. Runs the test suite (see [Running Tests](#6-running-tests))
2. Exports a `requirements.txt` from `uv`:
   ```bash
   uv export --no-dev --format requirements-txt -o requirements.txt
   ```
3. Deploys the API to Azure App Service (`training-bot-api`) using the publish profile stored in `AZURE_WEBAPP_PUBLISH_PROFILE_API`.
4. Deploys the frontend to Azure App Service (`training-bot-frontend`) using the publish profile stored in `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`.

### Required GitHub Secrets for deployment

| Secret | Purpose |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Azure publish profile for `training-bot-api` |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Azure publish profile for `training-bot-frontend` |

### Manual deployment

```bash
# Export dependencies
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy via Azure CLI (example)
az webapp deploy --resource-group <rg> --name training-bot-api --src-path .
```

[TODO: What is the frontend component? No frontend source files (e.g. Vite/React) were present in the provided files — the CORS config references `localhost:5173` (Vite) and `localhost:8000` (Chainlit). Clarify which app is the frontend App Service.]

### PDF ingestion on the deployed instance

After deploying, trigger ingestion via the API:

```bash
curl -X POST https://<your-api-hostname>/ingest
```

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `api/agent.py` | File is truncated — `make_teacher_agent` and `make_assessor_agent` factory functions are referenced in `api/main.py` but their implementations are not present in the provided source |
| `api/agent.py` | `from langchain.agents import create_agent` — this import appears at module level but the actual agent construction functions are missing from the truncated file |
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is disabled; must be re-enabled for production |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` — debug print statement left in production code |
| `core/ingest.py` | Default `OPENAI_URL_BASE` for the annotation LLM is `https://api.anthropic.com/v1` but the model name defaults to `claude-sonnet-4-6` — if `OPENAI_URL_BASE` is overridden to OpenRouter, the model name must also be updated accordingly |
| `core/chunker.py` | File is truncated — `split_by_words` function is incomplete |
| `.github/scripts/tool1_code_review.py` | File is truncated — email sending and audit logging calls are cut off |
| `.github/scripts/tool2_tech_docs.py` | File is truncated — `build_index` function references undefined variable `r` (likely a typo for `repo`) |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced throughout but not present in the truncated file |
| `api/rag_tools.py` | File is truncated — `_collect_sources` return statement is missing |
| `api/sessions.py` | File is truncated — `CustomerProfile.describe()` method is incomplete |
| `data/sessions.json` | Sessions are persisted to a local file; this will not work correctly on stateless Azure App Service instances with ephemeral storage — persistent storage (Azure Blob, database) should be used instead |
| General | No `.env.example` file is present in the repository |
| General | [TODO: What is the frontend application? CORS allows `localhost:5173` (Vite dev server) and `localhost:8000` (Chainlit). Clarify which is the deployed frontend.] |
| General | [TODO: What vector store backend is used in production — Chroma, FAISS, or Pinecone? The code supports all three but the production default is not specified.] |