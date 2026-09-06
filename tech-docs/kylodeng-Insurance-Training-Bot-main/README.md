# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents, focused on the Hong Kong insurance market. It provides two core modes: a **Teacher mode** for interactive coaching and product knowledge building, and a **Roleplay/Assessment mode** where trainees practice sales conversations against simulated customer personas and receive structured feedback. The backend uses a Retrieval-Augmented Generation (RAG) pipeline over insurance product PDFs, exposing an LLM agent via a FastAPI server.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Language | Python | 3.13 (CI/CD), 3.12 (AI tools workflows) |
| Package manager | `uv` | astral-sh/setup-uv@v3 |
| Web framework | FastAPI | with `uvicorn` |
| LLM provider | OpenRouter (default) | Configurable via `OPENAI_URL_BASE` |
| LLM model | `openai/gpt-oss-20b:free` | Configurable via `OPENAI_MODEL` |
| Annotation LLM | Anthropic Claude (`claude-sonnet-4-6`) | Used in ingest pipeline |
| LLM orchestration | LangChain / LangGraph | `langchain-openai`, `langchain-core` |
| Vector store | Chroma, FAISS, or Pinecone | Selectable via `core/vector_store.py` |
| PDF processing | `pdfplumber` | Custom chunker in `core/chunker.py` |
| Embeddings | Voyage AI (free tier default) | `batch_size=126`, `batch_delay=0` |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| AI workflow tools | Anthropic Claude (`claude-sonnet-4-6`) | 5 GitHub Actions-based delivery tools |
| Email notifications | SendGrid | Via `shared.py` |
| CI/CD | GitHub Actions | See `.github/workflows/` |
| Deployment | Azure App Service | `training-bot-api` and `training-bot-frontend` |
| HTTP client | `httpx` | SSL verification disabled (see Known Issues) |
| Env management | `python-dotenv` | `.env` file |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client / Chainlit UI                 │
│              (http://localhost:8000 or 5173)            │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / SSE (streaming)
┌────────────────────────▼────────────────────────────────┐
│                  FastAPI Backend (api/main.py)           │
│  - /ingest        → runs PDF ingest pipeline            │
│  - /chat          → teacher mode (streaming)            │
│  - /roleplay      → customer persona simulation         │
│  - /assess        → post-roleplay assessment            │
│  - /sessions/*    → session CRUD                        │
│  - /docs/*        → static PDF serving                  │
└───────────┬─────────────────────────┬───────────────────┘
            │                         │
┌───────────▼──────────┐   ┌──────────▼──────────────────┐
│   LangGraph Agents   │   │     core RAG Library         │
│  (api/agent.py)      │   │  (core/)                     │
│  - Teacher agent     │   │  - PDF ingestion (ingest.py) │
│  - Assessor agent    │   │  - LLM annotation            │
│                      │   │    (annotator.py)            │
│  8 RAG tools exposed │   │  - Chunking (chunker.py)     │
│  (api/rag_tools.py)  │   │  - Vector store abstraction  │
└───────────┬──────────┘   │    (Chroma/FAISS/Pinecone)   │
            │              └──────────────────────────────┘
            │ semantic search
┌───────────▼──────────────────────────────────────────────┐
│              Vector Store (Chroma / FAISS / Pinecone)    │
│         Populated from data/Insurance-product-info/      │
│         (PDFs + .annot.json sidecar files)               │
└──────────────────────────────────────────────────────────┘
```

**Data flow:**
1. PDFs under `data/Insurance-product-info/` are ingested via `POST /ingest` or `python -m core.ingest`.
2. The annotator LLM (Claude) labels each document and page; annotations are cached as `.annot.json` sidecars.
3. The chunker (`core/chunker.py`) splits pages into semantic units; chunks are embedded and stored in the vector store.
4. At runtime, the FastAPI server loads the vector store and exposes eight LangChain tools to the LangGraph agents.
5. The Teacher agent streams responses via `astream_events`; the Assessor agent runs a one-shot `ainvoke` after roleplay ends.
6. Sessions (conversation history + customer profiles) are persisted to `data/sessions.json`.
7. Five GitHub Actions AI-delivery workflows (code review, tech docs, business docs, auto-testing, UAT) use Anthropic Claude via `shared.py` and write outputs to a separate `ai-delivery-outputs` repo.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install `uv`** (Python package manager)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # if provided, otherwise create manually
```

5. **Ingest insurance product PDFs** into the vector store

```bash
uv run python -m core.ingest
```

> PDFs should be placed under `data/Insurance-product-info/`. Annotation sidecars (`.annot.json`) are created automatically on first run and cached for subsequent ingests.

6. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

7. **Access the application**

- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Chainlit UI (if configured): [http://localhost:8000](http://localhost:8000)
- Vite dev server (if used): [http://localhost:5173](http://localhost:5173)

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name for agent/chat |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events by default; overridable per session in Chainlit UI |
| `ANTHROPIC_API_KEY` | Yes (AI tools workflows) | — | Anthropic API key used by the 5 GitHub Actions delivery tools and ingest annotation LLM |
| `GH_TOKEN` | Yes (AI tools workflows) | — | GitHub personal access token for reading repos and posting PR comments |
| `SENDGRID_API_KEY` | Yes (AI tools workflows) | — | SendGrid API key for email notifications from delivery workflows |
| `OUTPUT_REPO` | No (AI tools) | `ai-delivery-outputs` | Name of the GitHub repo where AI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (AI tools) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (AI tools) | `kylo.deng@capco.com` | Recipient email for AI tool notifications |
| `SENDER_EMAIL` | No (AI tools) | `kylo.deng@capco.com` | Sender email for AI tool notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure App Service publish profile for the API (`training-bot-api`) — stored as GitHub secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure App Service publish profile for the frontend (`training-bot-frontend`) — stored as GitHub secret |

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with `pytest`.

```bash
uv run pytest tests/ -v
```

The CI pipeline runs tests automatically on every push and pull request to `main` via `.github/workflows/deploy.yml`.

[TODO: What test files exist in `tests/`? Are there any pytest fixtures, markers, or configuration in `pytest.ini` / `pyproject.toml`?]

---

## 7. Deployment

### Automated (GitHub Actions)

Deployment is triggered automatically on every push to `main` after tests pass, via `.github/workflows/deploy.yml`.

The workflow:
1. Runs `pytest` (see [Running Tests](#6-running-tests))
2. Exports a `requirements.txt` from `uv`
3. Deploys to two Azure App Service instances in parallel

Required GitHub secrets:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API` — publish profile for `training-bot-api`
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` — publish profile for `training-bot-frontend`

### Manual Deployment

1. **Export dependencies**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy API to Azure App Service**

```bash
# Using Azure CLI
az webapp deploy --resource-group <rg> --name training-bot-api --src-path . --type zip
```

3. **Deploy Frontend to Azure App Service**

```bash
az webapp deploy --resource-group <rg> --name training-bot-frontend --src-path . --type zip
```

[TODO: What is the frontend? The repo contains a FastAPI backend and references a Vite dev server at port 5173, but no frontend source directory is visible in the provided files. Is the frontend a separate Vite/React app in a subdirectory?]

### PDF Ingestion (post-deploy)

After deployment, trigger ingestion via the API:

```bash
curl -X POST https://<your-app>.azurewebsites.net/ingest
```

Or run the ingest script directly:

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

### AI Delivery Workflows

The five GitHub Actions tools run automatically on their configured triggers (see table below) or can be triggered manually via `workflow_dispatch`:

| Tool | Workflow file | Default trigger |
|---|---|---|
| Code Review | `tool1_code_review.yml` | PR open/sync; Monday 08:00 UTC |
| Tech Docs | `tool2_tech_docs.yml` | Push to `main`; Sunday 06:00 UTC |
| Business Docs | `tool3_business_docs.yml` | Version tag (`v*`) |
| Auto Testing | `tool4_auto_testing.yml` | PR open/sync on source files; Wednesday 07:00 UTC |
| UAT Facilitation | `tool5_uat.yml` | `release/*` branch creation |

Required GitHub secrets for all AI tools: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is **disabled** for all `httpx` clients (`verify=False`). This is a security risk in production. |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug statement left in production code. |
| `api/agent.py` | `from langchain.agents import create_agent` is imported but the teacher/assessor agent constructors (`make_teacher_agent`, `make_assessor_agent`) are not shown — agent construction logic is incomplete in the provided file. |
| `core/ingest.py` | Default `batch_delay=0` comment references old free-tier settings (22s delay, batch_size=20); current defaults appear to assume a paid Voyage AI account. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all tool scripts but their implementations are truncated/missing from the provided file. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated (`{owner}/{r` — variable `r` is likely `repo`). |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` function is truncated. |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function is truncated (`list[d` — likely `list[dict]`). |
| `core/annotator.py` | Custom annotation logic section is marked with a comment `# custom annotation logic` and is truncated. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated (`f"N...`). |
| `core/chunker.py` | `split_by_words` function is truncated. |
| General | No disaster recovery (DR) or monitoring configuration is evident in any of the provided files. |
| General | No `.env.example` file is provided; developers must construct the `.env` manually. |
| Workflows | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` in all five workflow YAML files — these should be parameterised. |
| `tool5_uat.yml` | Escalation path contacts are listed as `[TODO: fill in team contacts]` in the runbook system prompt. |