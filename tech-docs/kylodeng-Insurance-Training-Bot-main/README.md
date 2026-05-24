# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based AI system that helps new insurance agents in Hong Kong master product knowledge and sales techniques. It provides two modes: a **Teacher mode** for interactive learning (chat, exercises, quizzes) and a **Roleplay/Assessment mode** where the agent practises selling to a simulated customer and receives a scored performance review. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life Hong Kong insurance product PDFs into a vector store, enabling agents to query accurate product details during training.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, async |
| LLM Inference | OpenAI-compatible API (via OpenRouter by default) | Model: `openai/gpt-oss-20b:free` (configurable) |
| LLM Annotation (ingest) | Anthropic Claude-compatible endpoint | Model: `claude-sonnet-4-6` (configurable via env) |
| Agent Framework | LangGraph / LangChain | `langchain`, `langchain-openai`, `langchain-core` |
| Vector Store | Chroma, FAISS, or Pinecone | Abstracted via `BaseVectorStore`; configured at runtime |
| PDF Processing | pdfplumber | Chunking and text extraction |
| Embedding | Voyage AI (implied by rate-limit comments) | Free-tier default; batch size 126 |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Frontend (UI) | Chainlit | Served on port 8000 |
| Frontend (dev) | Vite dev server | Port 5173 |
| Python package manager | uv (astral-sh) | `uv sync`, `uv run` |
| CI/CD | GitHub Actions | `.github/workflows/` |
| Deployment target | Azure App Service | `training-bot-api`, `training-bot-frontend` |
| AI Delivery Tools | Anthropic Claude (`claude-sonnet-4-6`) via `.github/scripts/` | Code review, tech docs, business docs, auto-testing, UAT |
| Email notifications | SendGrid | Delivery notifications from AI workflow tools |
| HTTP client | httpx | SSL verification disabled in dev (see Known Issues) |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Client (Browser)                   │
│              Chainlit UI  /  Vite dev server            │
└───────────────────┬─────────────────────────────────────┘
                    │ HTTP / SSE (streaming)
┌───────────────────▼─────────────────────────────────────┐
│                  FastAPI  (api/main.py)                  │
│  • /ingest       – trigger PDF ingestion                 │
│  • /chat         – teacher-mode streaming chat           │
│  • /roleplay     – roleplay session management           │
│  • /assess       – post-roleplay assessment              │
│  • /sessions     – CRUD for conversation sessions        │
│  • /docs/*       – static file serving for PDFs         │
└────────┬──────────────────────┬──────────────────────────┘
         │                      │
┌────────▼────────┐   ┌─────────▼──────────────────────────┐
│  LangGraph      │   │  core/ RAG Library                  │
│  Agents         │   │  • ingest.py  – PDF→chunk→embed     │
│  (api/agent.py) │   │  • chunker.py – pdfplumber + heur.  │
│  Teacher Agent  │   │  • annotator.py – LLM page/doc ann. │
│  Assessor Agent │   │  • vector_store.py – Chroma/FAISS/  │
└────────┬────────┘   │    Pinecone abstraction             │
         │            └─────────┬──────────────────────────┘
         │                      │
┌────────▼──────────────────────▼──────────────────────────┐
│  RAG Tools  (api/rag_tools.py)                           │
│  get_current_date · list_products · search_product       │
│  search_all · lookup_hospital_network · compare_plans    │
│  lookup_exclusions · search_claim_procedure              │
└──────────────────────────────────────────────────────────┘
         │
┌────────▼──────────────────────────────────────────────────┐
│  Vector Store  (Chroma / FAISS / Pinecone)                │
│  Populated from:  data/Insurance-product-info/**/*.pdf    │
│  Sidecar annotations cached as  *.annot.json             │
└───────────────────────────────────────────────────────────┘
```

**How it hangs together:**

1. **Ingestion** – `core/ingest.py` walks `data/Insurance-product-info/`, uses an LLM to annotate each PDF (product name, page relevance), splits pages into semantic chunks via `core/chunker.py`, and embeds them into the configured vector store. Annotation results are cached as `.annot.json` sidecar files so re-ingestion is cheap.
2. **Runtime** – On startup (`lifespan`), the FastAPI app loads the persisted vector store and session file. Incoming chat requests are routed to a LangGraph agent (Teacher or Assessor). The agent calls RAG tools which hit the vector store and collect source citations.
3. **Streaming** – Teacher-mode responses are streamed via Server-Sent Events using `astream_events`. Assessor mode uses `ainvoke` (one-shot after roleplay ends).
4. **Sessions** – `api/sessions.py` manages multi-turn state (mode, message history, customer profile) and persists everything to `data/sessions.json`.
5. **CI/CD** – Five GitHub Actions AI-delivery tools (`tool1`–`tool5`) run code review, doc generation, business docs, test generation, and UAT facilitation using Claude via the Anthropic API, writing outputs to a separate `ai-delivery-outputs` repo.

---

## 4. Local Development Setup

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
cp .env.example .env   # [TODO: confirm whether .env.example exists in the repo]
# Edit .env — see Environment Variables section below
```

```bash
# 5. Place insurance product PDFs in the data directory
# Expected path: data/Insurance-product-info/**/*.pdf
# The directory is pre-populated with Sun Life HK product brochures in the repo.
```

```bash
# 6. Ingest PDFs into the vector store (run once, or after adding new PDFs)
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
# OR via the API endpoint after the server is running:
# POST http://localhost:8000/ingest
```

```bash
# 7. Start the FastAPI / Chainlit backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 8. (Optional) Start the Vite frontend dev server
# [TODO: confirm whether a separate frontend package.json / Vite project exists]
# npm install && npm run dev   # expected on port 5173
```

The Chainlit UI should be accessible at `http://localhost:8000`.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the OpenAI-compatible LLM endpoint (e.g. OpenRouter key) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the chat LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for the chat/agent LLM; also used as the annotation LLM model name in `core/ingest.py` |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool-call events by default; overridable per-session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by the five `.github/scripts/` AI delivery tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub Personal Access Token for the AI delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from the AI delivery tools |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where AI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for AI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for AI tool notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (CI/deploy) | — | Azure publish profile secret for the API App Service (`training-bot-api`) |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (CI/deploy) | — | Azure publish profile secret for the frontend App Service (`training-bot-frontend`) |

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with `pytest` via `uv`.

```bash
# Run the full test suite
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this same command on every push to `main` and on every pull request targeting `main`, using Python 3.13.

[TODO: Are there any test fixtures, environment variables, or mock configurations required to run tests locally?]

---

## 7. Deployment

### Automatic deployment (GitHub Actions)

Every push to `main` that passes tests triggers two parallel Azure App Service deployments defined in `.github/workflows/deploy.yml`:

- **API** → Azure App Service app named `training-bot-api`
- **Frontend** → Azure App Service app named `training-bot-frontend`

Required GitHub repository secrets:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API`
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

The workflow exports a `requirements.txt` from `uv` before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Manual / one-off deployment steps

```bash
# 1. Generate requirements.txt
uv export --no-dev --format requirements-txt -o requirements.txt

# 2. Deploy API to Azure App Service
az webapp deploy --name training-bot-api --resource-group <your-rg> \
  --src-path . --type zip

# 3. Deploy Frontend to Azure App Service
az webapp deploy --name training-bot-frontend --resource-group <your-rg> \
  --src-path . --type zip
```

[TODO: Confirm whether the frontend is a separate Chainlit app or a Vite/Node build; the deploy workflow treats both as Python apps via `uv`.]

### PDF ingestion (must be run before the app is usable)

```bash
# Via CLI
uv run python -m core.ingest --pdf-dir data/Insurance-product-info

# Via API endpoint (after the server is running)
curl -X POST http://<host>:8000/ingest
```

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is **disabled** for all LLM HTTP calls. This is a security risk and should not be used in production. |
| `api/main.py` | `SHOW_TOOL_CALLS` env var parsing has a subtle bug: the `print` statement uses a fresh `os.getenv` call with a space `" "` as the default instead of `"false"`, so the log output may be misleading. |
| `api/agent.py` | `from langchain.agents import create_agent` — the file is truncated in the source; the actual agent construction (`make_teacher_agent`, `make_assessor_agent`) is not shown. [TODO: verify the correct LangGraph agent factory is wired up.] |
| `core/annotator.py` | Comment `# custom annotation logic` indicates the `annotate_document` function body is incomplete/truncated in the visible source. |
| `core/chunker.py` | `split_by_words` function body is truncated — hard word-splitting fallback behaviour is not fully visible. |
| `core/ingest.py` | Default `base_url` for the annotation LLM is `https://api.anthropic.com/v1` but `OPENAI_MODEL` defaults to `claude-sonnet-4-6`; the same `OPENAI_MODEL` env var controls both the chat agent model and the annotation model, which may cause confusion if they need to differ. |
| `core/ingest.py` | `batch_delay` defaults to `0` seconds in `embed_chunks` but the docstring says the old default of 22 s was needed for Voyage AI free-tier (3 RPM). Operators on the free tier must set this manually. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` are imported by all tool scripts but their implementations are not present in the truncated `shared.py` source — these functions must exist in the full file. |
| `tool2_tech_docs.py` | `build_index` function references `{r` (truncated) — likely a bug or truncation in the source file. |
| All AI delivery tools | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` in all workflow YAML files — these should be parameterised for other deployments. |
| `api/sessions.py` | Sessions are persisted to a flat JSON file (`data/sessions.json`); no database or distributed store is used, making horizontal scaling on Azure App Service problematic without shared storage. |
| General | No Disaster Recovery (DR) configuration, health check endpoints, or monitoring/alerting setup is evident in the codebase. [TODO: Add `/health` endpoint, configure Azure Application Insights or equivalent.] |
| General | No authentication or authorisation is implemented on any FastAPI endpoint. [TODO: Add API key or OAuth2 middleware before production use.] |