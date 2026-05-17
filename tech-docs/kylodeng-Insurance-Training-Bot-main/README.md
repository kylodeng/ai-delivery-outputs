# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based AI training system designed to help new insurance agents in Hong Kong develop sales skills and product knowledge. It provides two modes: a **Teacher mode** for interactive coaching and concept explanation, and a **Roleplay/Assessment mode** where agents practice with simulated customers and receive scored feedback. The system uses a Retrieval-Augmented Generation (RAG) pipeline built on insurance product PDFs to ensure all product-specific answers are grounded in real documentation.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 (per CI), `uv` for dependency management |
| LLM (inference) | OpenAI-compatible API (OpenRouter default) | Model: `openai/gpt-oss-20b:free` (env-configurable) |
| LLM (ingestion/annotation) | Anthropic Claude via OpenAI-compatible base URL | Model: `claude-sonnet-4-6` (env-configurable) |
| Agent framework | LangGraph / LangChain | `langchain`, `langchain-openai`, `langchain-core` |
| Vector store | Chroma, local FAISS, or Pinecone | Configurable via `core/vector_store.py` |
| PDF processing | pdfplumber | For chunking and text extraction |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| HTTP client | httpx | SSL verification disabled (see Known Issues) |
| Environment config | python-dotenv | `.env` file |
| CI/CD | GitHub Actions | `.github/workflows/` |
| Deployment | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| AI Delivery Tooling | Anthropic Claude (`claude-sonnet-4-6`) | Code review, tech docs, business docs, test gen, UAT |
| Email notifications | SendGrid | For AI delivery workflow notifications |
| Test runner | pytest via `uv run pytest` | |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Client (Chainlit UI / Vite dev server on :5173)             │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP / SSE (streaming)
┌──────────────────────▼───────────────────────────────────────┐
│  FastAPI  (api/main.py  :8000)                               │
│                                                              │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │  /teacher   │  │   /roleplay      │  │  /ingest       │  │
│  │  (stream)   │  │   (stream)       │  │  (POST)        │  │
│  └──────┬──────┘  └────────┬─────────┘  └───────┬────────┘  │
│         │                  │                     │           │
│  ┌──────▼──────────────────▼─────────┐           │           │
│  │  LangGraph Agents (api/agent.py)  │           │           │
│  │   • make_teacher_agent()          │           │           │
│  │   • make_assessor_agent()         │           │           │
│  └──────────────────┬────────────────┘           │           │
│                     │ tool calls                 │           │
│  ┌──────────────────▼────────────────────────────▼────────┐  │
│  │  RAG Tools (api/rag_tools.py)                          │  │
│  │  search_product, search_all, compare_plans,            │  │
│  │  lookup_hospital_network, lookup_exclusions, etc.      │  │
│  └──────────────────────────┬───────────────────────────--┘  │
│                             │ similarity search              │
│  ┌──────────────────────────▼────────────────────────────┐   │
│  │  Vector Store (core/vector_store.py)                  │   │
│  │  ChromaStore | LocalFAISSStore | PineconeStore        │   │
│  └──────────────────────────────────────────────────────-┘   │
│                                                              │
│  Session state: data/sessions.json (api/sessions.py)        │
└──────────────────────────────────────────────────────────────┘

Ingestion pipeline (core/ingest.py):
  data/Insurance-product-info/**/*.pdf
    → pdfplumber (core/chunker.py)
    → LLM annotation (core/annotator.py)  ← cached in .annot.json sidecars
    → embed_chunks()
    → Vector store save
```

**Data flow summary:**
1. PDFs under `data/Insurance-product-info/` are ingested once via `POST /ingest` or the CLI in `core/ingest.py`.
2. Each PDF is annotated by an LLM (product name, doc type, page relevance); results are cached as `.annot.json` sidecar files so re-ingestion is cheap.
3. Relevant pages are chunked (max ~280 words) and embedded into the vector store.
4. At runtime, the FastAPI server loads the vector store on startup.
5. Agent requests stream through LangGraph; tool calls hit the vector store for grounded answers.
6. Sessions (chat history + customer profiles) are persisted to `data/sessions.json`.
7. Static PDF files are served from `/docs/` so the UI can link directly to source documents.

Five GitHub Actions AI delivery workflows (code review, tech docs, business docs, test generation, UAT) run independently against the repository using Anthropic Claude and write outputs to a separate `ai-delivery-outputs` repo.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install `uv` (package manager)**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Create your `.env` file**

```bash
cp .env.example .env   # if example exists, otherwise create manually
```

Populate at minimum (see [Environment Variables](#5-environment-variables) below):

```bash
API_KEY=your_openrouter_or_openai_key
OPENAI_URL_BASE=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-oss-20b:free
```

5. **Place insurance product PDFs** under `data/Insurance-product-info/` (subdirectory structure is flexible).

6. **Ingest PDFs into the vector store**

```bash
uv run python core/ingest.py
```

   Or via the API after starting the server:

```bash
curl -X POST http://localhost:8000/ingest
```

7. **Start the API server**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

8. **Access the application**

   - API: `http://localhost:8000`
   - Static docs: `http://localhost:8000/docs/<path-to-pdf>`
   - [TODO: How is the frontend started separately? Is there a Vite project or is Chainlit the UI?]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter, OpenAI, or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for OpenAI-compatible LLM API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name passed to the LLM |
| `SHOW_TOOL_CALLS` | No | `true` | Stream tool call events to the UI by default |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | — | Anthropic API key used by the five AI delivery GitHub Actions workflows |
| `GH_TOKEN` | Yes (CI workflows) | — | GitHub personal access token for the AI delivery workflows |
| `SENDGRID_API_KEY` | Yes (CI workflows) | — | SendGrid key for email notifications from AI delivery workflows |
| `OUTPUT_REPO` | No (CI workflows) | `ai-delivery-outputs` | GitHub repo name where AI delivery workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI workflows) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Recipient email for AI delivery workflow notifications |
| `SENDER_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Sender email for AI delivery workflow notifications |

---

## 6. Running Tests

```bash
uv run pytest tests/ -v
```

Tests are also executed automatically in CI on every push and pull request to `main` (see `.github/workflows/deploy.yml`).

[TODO: What test files exist under `tests/`? Are there fixtures or conftest.py files that need setup?]

---

## 7. Deployment

### Automated (GitHub Actions — recommended)

Deployment is triggered automatically on every push to `main` after tests pass:

```
push to main → test job → deploy-api + deploy-frontend (parallel)
```

Two Azure App Service targets are deployed:
- **`training-bot-api`** — FastAPI backend
- **`training-bot-frontend`** — [TODO: What serves the frontend? Chainlit? A separate Vite build?]

The workflow generates a `requirements.txt` from `uv` before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

Secrets required in the GitHub repository:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API`
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Manual (local)

1. Export requirements:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. [TODO: Are there Azure CLI or Bicep/Terraform IaC files for initial infrastructure provisioning? None were found in the provided files.]

3. Deploy to Azure App Service using the Azure CLI or Azure Portal with the generated `requirements.txt`.

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is disabled for both sync and async HTTP clients. This is a security risk and should not be used in production. |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement uses a malformed f-string: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` — nested quotes will cause a syntax error in Python < 3.12. |
| `api/agent.py` | `from langchain.agents import create_agent` is imported but `make_teacher_agent` and `make_assessor_agent` factory functions are referenced in `main.py` without being shown in the provided file — implementation may be incomplete. |
| `core/ingest.py` | Default `base_url` for the annotation LLM is `https://api.anthropic.com/v1` but uses a `ChatOpenAI` client — requires the Anthropic OpenAI-compatible endpoint. |
| `core/chunker.py` | `split_by_words` function body was truncated in the provided files — implementation unknown. |
| `tool2_tech_docs.py` | `build_index` function references `r` (undefined variable) instead of `repo` — likely a bug: `f"# Tech Documentation Index — {owner}/{r..."`. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` are imported in all tool scripts but their implementations are truncated/missing from the provided `shared.py` file. |
| `api/sessions.py` | `CustomerProfile.describe()` method body is truncated — implementation unknown. |
| All AI delivery workflows | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` in workflow env blocks — should be parameterised for reuse. |
| General | No disaster recovery, monitoring, or alerting configuration is present in the provided files. |
| General | No `.env.example` file was found in the provided files — new developers must manually determine required variables. |