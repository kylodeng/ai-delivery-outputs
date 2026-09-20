# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents, built around a Retrieval-Augmented Generation (RAG) pipeline over Sun Life Hong Kong product PDFs. It provides two interaction modes: a **Teacher mode** for guided coaching and product Q&A, and a **Roleplay/Assessment mode** where the agent practises sales conversations against a simulated customer profile and receives structured feedback. The system is deployed as a FastAPI backend with a separate frontend, both hosted on Azure App Service.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Runtime language | Python | 3.13 (CI/CD), 3.12 (workflow scripts) |
| Package manager | uv (Astral) | `uv sync` / `uv export` |
| Web framework | FastAPI | With `asynccontextmanager` lifespan |
| LLM provider | OpenRouter (configurable) | Default model: `openai/gpt-oss-20b:free` |
| LLM client | `langchain-openai` / `ChatOpenAI` | Streaming enabled |
| Agent framework | LangGraph | `create_agent` via `langchain.agents` |
| Embedding / vector store | `core` library (local) | Supports ChromaDB, FAISS, Pinecone |
| PDF parsing | `pdfplumber` | Custom chunker in `core/chunker.py` |
| Document annotation | LLM-based (same model) | Cached to `.annot.json` sidecar files |
| HTTP client | `httpx` | SSL verification disabled (see Known Issues) |
| Environment config | `python-dotenv` | `.env` file |
| AI delivery workflows | Anthropic Claude | `claude-sonnet-4-6` (via `anthropic` SDK) |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| CI/CD | GitHub Actions | See `.github/workflows/` |
| Deployment target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| Test runner | pytest | Run via `uv run pytest` |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Client / Chainlit UI                │
│              (http://localhost:8000 or 5173)         │
└────────────────────────┬────────────────────────────┘
                         │ HTTP / SSE streaming
┌────────────────────────▼────────────────────────────┐
│              FastAPI Backend  (api/main.py)          │
│  - Session management  (api/sessions.py)             │
│  - Teacher agent       (api/agent.py)  ←─ streams   │
│  - Assessor agent      (api/agent.py)  ←─ one-shot  │
│  - Roleplay customer   (api/main.py _ROLEPLAY_SYSTEM)│
│  - Static file mount   /docs → data/                │
└──────┬──────────────────────┬───────────────────────┘
       │ RAG tools             │ LLM calls
┌──────▼──────────┐   ┌───────▼────────────────────────┐
│  core/ library  │   │  OpenRouter / Anthropic API     │
│  vector store   │   │  (ChatOpenAI with custom base   │
│  (Chroma/FAISS/ │   │   URL)                         │
│   Pinecone)     │   └────────────────────────────────┘
│  PDF chunker    │
│  LLM annotator  │
└──────┬──────────┘
       │
┌──────▼──────────────────────────────────────────────┐
│  data/Insurance-product-info/  (PDF knowledge base) │
│  *.annot.json sidecar annotation cache              │
│  data/sessions.json  (session persistence)          │
└─────────────────────────────────────────────────────┘

GitHub Actions (.github/workflows/)
  ├── deploy.yml           → test + deploy to Azure on push to main
  ├── tool1_code_review    → Claude PR/repo code review
  ├── tool2_tech_docs      → Claude auto-documentation
  ├── tool3_business_docs  → Claude business doc generation
  ├── tool4_auto_testing   → Claude test generation / gap analysis
  └── tool5_uat            → Claude UAT test pack / defect report
```

**How components interact:**

1. On startup, `api/main.py` loads the persisted vector store (`core/vector_store.py`) from disk and restores sessions from `data/sessions.json`.
2. Incoming chat requests create or continue a **Session** (teacher or roleplay mode). Each request resets per-request source tracking (`api/rag_tools.py` context vars).
3. The **Teacher agent** streams responses via `astream_events`; the **Assessor agent** is invoked once after a roleplay ends via `ainvoke`.
4. Both agents call **RAG tools** (`api/rag_tools.py`) which query the vector store and annotate responses with inline source citations (`[[S1]]`, `[[S2]]`, …).
5. PDFs are pre-processed by `core/ingest.py` → `core/chunker.py` → `core/annotator.py` (LLM-annotated, cached as `.annot.json`) → embedded into the vector store.
6. The GitHub Actions AI-delivery tools (`tool1`–`tool5`) are independent Python scripts that use Anthropic Claude directly (not the app LLM) for repository-level automation.

---

## 4. Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 2. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
# 3. Install Python dependencies
uv sync
```

```bash
# 4. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your API keys and configuration (see Environment Variables section)
```

```bash
# 5. Ingest PDF documents into the vector store
# Place your PDF files under data/Insurance-product-info/
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
# Or using the module directly:
uv run python core/ingest.py --pdf-dir data/Insurance-product-info
```

```bash
# 6. Start the FastAPI backend
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
# 7. (Optional) Trigger a manual ingest via the API endpoint
curl -X POST http://localhost:8000/ingest
```

The Chainlit UI (frontend) is served separately. [TODO: What command starts the frontend — is there a Chainlit `app.py` or a Vite project? The frontend entrypoint is not present in the provided files.]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name to use for chat and annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events (`true`/`false`); overridable per session in Chainlit UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by the GitHub Actions AI-delivery tools (tool1–tool5) |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for the AI-delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI tool notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (CI only) | — | Azure publish profile secret for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (CI only) | — | Azure publish profile secret for `training-bot-frontend` App Service |

> All CI-only variables are stored as GitHub Actions secrets and are not needed for local development of the application itself.

---

## 6. Running Tests

```bash
# Run the full test suite
uv run pytest tests/ -v
```

```bash
# Run with coverage (if pytest-cov is installed)
uv run pytest tests/ -v --cov=api --cov=core
```

> [TODO: Are there existing test files under `tests/`? No test files were present in the provided source listing.]

The CI pipeline runs tests automatically on every push and pull request to `main` via `.github/workflows/deploy.yml` before any deployment proceeds.

---

## 7. Deployment

### Automatic (CI/CD)

Deployment to Azure App Service is triggered automatically on every push to `main` after tests pass:

```
git push origin main
# → GitHub Actions: test → deploy-api + deploy-frontend (parallel)
```

The workflow (`.github/workflows/deploy.yml`) performs:
1. Runs `pytest` on Python 3.13
2. Exports `requirements.txt` via `uv export --no-dev --format requirements-txt -o requirements.txt`
3. Deploys to `training-bot-api` Azure App Service using the `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret
4. Deploys to `training-bot-frontend` Azure App Service using the `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret

### Manual PDF Ingestion

After deployment, ingest documents by calling the ingestion endpoint:

```bash
curl -X POST https://<your-api-app>.azurewebsites.net/ingest
```

Or run locally against the data directory:

```bash
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose
```

### GitHub Actions AI-Delivery Tools

These run automatically based on their triggers but can also be dispatched manually from the GitHub Actions UI:

| Tool | Trigger | Manual dispatch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC | ✅ (mode: `repo` or `pr`) |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC | ✅ |
| Tool 3 — Business Docs | Push of version tag `v*` | ✅ (project name + version) |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC | ✅ (mode: `generate` or `gap-analysis`) |
| Tool 5 — UAT | `release/*` branch creation | ✅ (mode: `generate` or `analyse`) |

---

## 8. Known Issues / TODOs

### From source code comments

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is disabled for all LLM HTTP calls. This is a security risk in production. |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement contains a logic bug: `os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"` has a space in the default value which will always evaluate to `False`, differing from the variable it is meant to debug. |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the provided source — the tool list description is cut off at `get_cu...`. [TODO: Confirm assessor system prompt is complete in the actual file.] |
| `core/chunker.py` | `split_by_words` function is truncated — implementation body not shown. [TODO: Confirm function is complete in the actual file.] |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the `annotate_document` function body is incomplete in the provided excerpt. [TODO: Confirm full implementation exists.] |
| `core/ingest.py` | `argparse` block is truncated — `--pdf-dir` default path is cut off. [TODO: Confirm CLI arguments in the actual file.] |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated — references an undefined variable `r` (likely a typo for `repo`). |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` function is truncated. |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function signature is truncated (`list[d...`). |
| `api/agent.py` | Uses `from langchain.agents import create_agent` — `create_agent` is not a standard LangChain export; may require a specific version or custom implementation. [TODO: Confirm correct import path.] |
| `TEACHER_SYSTEM` / `ASSESSOR_SYSTEM` | Escalation path for operational issues: `[TODO: fill in team contacts]` (referenced in tool2 runbook template). |
| General | No disaster recovery or cross-region failover is configured. Single Azure region deployment. |
| General | No monitoring or alerting configuration is present in the provided files. [TODO: What APM/logging solution is used in production?] |
| General | The frontend entrypoint is not present in the repository files provided. [TODO: Where is the Chainlit app or Vite frontend source?] |
| `data/sessions.json` | Sessions are persisted to a local file — this will not survive Azure App Service restarts if the filesystem is ephemeral. [TODO: Should sessions be persisted to a database or Azure Blob Storage?] |