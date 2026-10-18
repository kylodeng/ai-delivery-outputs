# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents, designed for the Hong Kong market. It provides two modes: a **Teacher mode** for interactive learning about insurance products via a RAG-backed chat agent, and a **Roleplay mode** where trainees practise sales conversations against a simulated customer persona. The backend ingests insurance product PDFs into a vector store and exposes a FastAPI server; a separate frontend (Chainlit-based) streams responses to the user in real time.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 (per CI), via `uv` |
| LLM Orchestration | LangGraph / LangChain | `langchain`, `langchain-openai`, `langchain-core` |
| LLM Provider | OpenRouter (default) / Anthropic-compatible | Model: `openai/gpt-oss-20b:free` (configurable) |
| Embeddings & RAG | Voyage AI (implied by rate-limit comments) / local FAISS or Chroma or Pinecone | Configurable via `core/vector_store.py` |
| PDF Processing | pdfplumber | — |
| Frontend | Chainlit | Served at `http://localhost:8000` |
| Dependency Management | uv (astral-sh) | — |
| CI/CD | GitHub Actions | `.github/workflows/` |
| Deployment Target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| AI Delivery Tooling | Anthropic Claude (`claude-sonnet-4-6`) | Used by `.github/scripts/` workflows only |
| Email | SendGrid | `.github/scripts/shared.py` |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        Frontend                          │
│              Chainlit UI  (port 8000)                    │
│         Vite dev server  (port 5173, dev only)           │
└─────────────────────────┬────────────────────────────────┘
                          │ HTTP / SSE (streaming)
┌─────────────────────────▼────────────────────────────────┐
│                  FastAPI Backend  (api/)                  │
│  /ingest  → ingest_directory() → embed_chunks()          │
│  /chat    → Teacher Agent (LangGraph, streamed)          │
│  /assess  → Assessor Agent (LangGraph, one-shot)         │
│  /docs    → Static PDF serving                           │
│  /sessions→ Session CRUD (sessions.json)                 │
└────────┬─────────────────────────┬────────────────────────┘
         │                         │
         ▼                         ▼
┌─────────────────┐     ┌──────────────────────────┐
│  Vector Store   │     │  LLM / Chat Completions  │
│ (Chroma/FAISS/  │     │  OpenRouter or Anthropic │
│  Pinecone)      │     │  (configurable via env)  │
└─────────────────┘     └──────────────────────────┘
         ▲
         │ PDF ingestion
┌─────────────────┐
│  data/          │
│  Insurance PDFs │
│  + .annot.json  │
│  (LLM-annotated │
│   sidecar cache)│
└─────────────────┘
```

**Key interactions:**

1. **Ingestion** — `POST /ingest` triggers `core/ingest.py` which walks `data/`, annotates each PDF page with an LLM call (result cached in `.annot.json` sidecars), chunks text via `core/chunker.py`, embeds chunks, and saves to the vector store.
2. **Teacher agent** — Each user message triggers a LangGraph agent that calls RAG tools (`api/rag_tools.py`) to retrieve relevant product chunks, then streams the response via `astream_events`.
3. **Assessor agent** — After a roleplay session ends, the assessor agent is invoked once (`ainvoke`) with the full conversation and customer profile to produce a structured performance report.
4. **Sessions** — `api/sessions.py` persists multi-turn sessions (with mode, messages, and customer profile) to `data/sessions.json`.
5. **GitHub Actions AI tools** — Five separate workflow scripts (`.github/scripts/tool1–5`) use the Anthropic Claude API independently of the main application to automate code review, documentation, test generation, and UAT.

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
# 3. Install Python dependencies
uv sync
```

```bash
# 4. Copy and configure environment variables
cp .env.example .env
# Edit .env — see Environment Variables section below
```

```bash
# 5. Place insurance product PDFs in the data directory
mkdir -p data/Insurance-product-info
# Copy your PDF files into data/Insurance-product-info/
```

```bash
# 6. Ingest PDFs into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
# Or via the API endpoint after starting the server:
# curl -X POST http://localhost:8000/ingest
```

```bash
# 7. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 8. Open the Chainlit frontend
# The Chainlit UI is served at http://localhost:8000
# For a separate Vite dev server (if a frontend build exists):
# npm install && npm run dev   (port 5173)
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible chat completions endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name to use for chat and annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; can be overridden per session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by `.github/scripts/` AI delivery tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for the AI delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications in workflow scripts |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where workflow script outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for workflow notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (CD only) | — | Azure publish profile secret for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (CD only) | — | Azure publish profile secret for `training-bot-frontend` App Service |

---

## 6. Running Tests

```bash
# Run the full test suite
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this automatically on every push and pull request to `main` before any deployment job proceeds.

[TODO: What test files exist under `tests/`? No test files were present in the provided source listing.]

---

## 7. Deployment

### Automatic (CI/CD via GitHub Actions)

On every push to `main`, the `Test & Deploy` workflow (`.github/workflows/deploy.yml`):

1. Runs tests (`pytest`)
2. On success, exports a `requirements.txt` via `uv export`
3. Deploys the API to Azure App Service (`training-bot-api`) using the `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret
4. Deploys the frontend to Azure App Service (`training-bot-frontend`) using the `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret

### Manual PDF Ingestion (via API)

```bash
# After deploying, trigger ingestion via the REST API
curl -X POST https://<your-api-hostname>/ingest
```

### AI Delivery Workflow Tools (manual or scheduled)

```bash
# Tool 1 – Code Review (runs on PR open/sync, weekly Monday 08:00 UTC, or manually)
# Trigger via GitHub Actions UI: .github/workflows/tool1_code_review.yml

# Tool 2 – Tech Documentation (runs on merge to main, weekly Sunday 06:00 UTC, or manually)
# Trigger via GitHub Actions UI: .github/workflows/tool2_tech_docs.yml

# Tool 3 – Business Documentation (runs on version tag push or manually)
git tag v1.0.0 && git push origin v1.0.0

# Tool 4 – Auto Testing (runs on PR open/sync for src/** or *.py/js/ts, weekly Wednesday 07:00 UTC)
# Trigger via GitHub Actions UI: .github/workflows/tool4_auto_testing.yml

# Tool 5 – UAT Facilitation (runs on release branch creation or manually)
git checkout -b release/1.0.0 && git push origin release/1.0.0
```

Required GitHub Actions secrets:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
AZURE_WEBAPP_PUBLISH_PROFILE_API
AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND
```

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `api/agent.py` | Assessor agent system prompt is truncated in the provided source — full tool list for assessor is incomplete |
| `api/main.py` | SSL verification is disabled: `httpx.Client(verify=False)` — this suppresses TLS certificate checks and should be enabled in production |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug statement left in production code |
| `core/annotator.py` | Custom annotation logic section is truncated (`# custom annotation logic`) — it is unclear what overrides are applied after LLM annotation |
| `core/chunker.py` | `split_by_words` function body is truncated in the provided source |
| `core/ingest.py` | `argparse` CLI section is truncated; `--pdf-dir` default path is incomplete |
| `api/sessions.py` | `CustomerProfile.describe()` method body is truncated |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function body is truncated (`{owner}/{r` — likely a variable interpolation bug) |
| `.github/scripts/tool3_business_docs.py` | `build_full_output` function is truncated |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` function and overall `main` are truncated |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function is truncated |
| `.github/scripts/shared.py` | `send_email` and `email_html` and `write_audit_entry` functions are referenced everywhere but their implementations are truncated from the file |
| `api/agent.py` | Uses `from langchain.agents import create_agent` but the actual agent construction (`make_teacher_agent`, `make_assessor_agent`) is not shown in the truncated file |
| Multiple | Escalation contacts: `[TODO: fill in team contacts]` — runbook escalation path is not defined |
| `data/sessions.json` | Sessions file path is hardcoded to `data/sessions.json` relative to the package root; this path is not configurable |
| Vector store | Embedding provider (Voyage AI vs. OpenAI embeddings) is not explicitly configured via environment variable — inferred from code comments only |