# Insurance Training Bot

## 1. Project Overview

The Insurance Training Bot is an AI-powered training system designed to help new insurance agents in Hong Kong master product knowledge and sales techniques. It provides two modes: a **Teacher mode** for interactive coaching and guided learning, and a **Roleplay/Assessment mode** where the agent practises conversations with simulated customers and receives structured performance feedback. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life Hong Kong insurance product PDFs into a vector store, ensuring all product-specific answers are grounded in real documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python async |
| LLM Orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM Provider | OpenRouter (default) / Anthropic | Configurable via env vars |
| LLM Model | `openai/gpt-oss-20b:free` (default) | Overridden by `OPENAI_MODEL` env var |
| Embeddings / Vector Store | `core` RAG library | Supports ChromaDB, FAISS, Pinecone |
| PDF Parsing | pdfplumber | Chunker + annotator pipeline |
| Package Manager | uv | Python 3.13 (CI), 3.x local |
| Frontend | [TODO: What framework is the frontend? A Vite dev server is referenced on port 5173 but no frontend source files were provided.] | Served on port 5173 (dev) |
| HTTP Client | httpx | SSL verification disabled — see Known Issues |
| CI/CD | GitHub Actions | 6 workflows |
| Deployment | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| AI Workflow Tooling | Anthropic Claude (`claude-sonnet-4-6`) | Used in `.github/scripts` tools only |
| Email | SendGrid | Notification via `shared.py` |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (Vite / Chainlit UI)         │
│                    http://localhost:5173                      │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP / SSE (StreamingResponse)
┌────────────────────────────▼────────────────────────────────┐
│                     FastAPI Backend (api/main.py)            │
│  /ingest  /chat (teacher)  /roleplay  /assess  /sessions    │
│  Serves /docs/* → data/ directory (PDFs, data files)        │
└──────┬───────────────────────────┬──────────────────────────┘
       │                           │
┌──────▼──────────┐   ┌───────────▼────────────────────────┐
│  LangGraph      │   │  core RAG library                   │
│  Agents         │   │  ┌────────────┐  ┌───────────────┐ │
│  ┌───────────┐  │   │  │  Annotator │  │   Chunker     │ │
│  │  Teacher  │  │   │  │  (LLM-based│  │  (pdfplumber) │ │
│  │  Agent    │◄─┼───┼─►│  sidecar   │  │               │ │
│  └───────────┘  │   │  │  .annot.   │  └───────┬───────┘ │
│  ┌───────────┐  │   │  │  json)     │          │         │ │
│  │ Assessor  │  │   │  └────────────┘          │         │ │
│  │  Agent    │  │   │  ┌──────────────────────▼───────┐  │ │
│  └───────────┘  │   │  │  Vector Store                 │  │ │
└─────────────────┘   │  │  (Chroma / FAISS / Pinecone)  │  │ │
                       │  └───────────────────────────────┘  │ │
                       └────────────────────────────────────┘

GitHub Actions (5 AI delivery tools):
  tool1: Claude code review → PR comments + output repo
  tool2: Claude tech docs   → README, ARCHITECTURE, RUNBOOK
  tool3: Claude biz docs    → Solution overview + gap questionnaire
  tool4: Claude test gen    → pytest / jest test files
  tool5: Claude UAT pack    → test scenarios + defect report
```

**Data flow (training session):**

1. On startup, `lifespan()` loads the persisted vector store from disk.
2. Insurance product PDFs in `data/` are pre-ingested via `POST /ingest` → `core/ingest.py` annotates pages with an LLM, chunks them with `pdfplumber`, and embeds chunks into the vector store.
3. When a trainee sends a message, `api/main.py` routes to the Teacher or Assessor LangGraph agent.
4. The agent calls RAG tools (`api/rag_tools.py`) which query the vector store and return source-attributed chunks.
5. The Teacher agent streams the response back via `StreamingResponse` (SSE); the Assessor agent returns a structured JSON assessment via `ainvoke`.
6. Sessions are stored in `data/sessions.json` for persistence across restarts.

---

## 4. Local Development Setup

**Prerequisites:** Python 3.11+, `uv` installed (`pip install uv`), and a populated `data/` directory with PDF product files.

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install dependencies using uv**

```bash
uv sync
```

3. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables) section below):

```bash
cp .env.example .env   # if available, otherwise create manually
```

4. **Ingest insurance product PDFs** into the vector store (PDFs must be placed under `data/Insurance-product-info/`):

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

Or via the API after starting the server:

```bash
curl -X POST http://localhost:8000/ingest
```

5. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

6. **Start the frontend** (development mode)

```bash
# [TODO: What is the frontend framework and start command? A Vite dev server on port 5173 is expected.]
```

7. **Verify the backend is running**

```bash
curl http://localhost:8000/docs
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model identifier |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridden per-session by UI toggle |
| `ANTHROPIC_API_KEY` | Yes (CI tools only) | — | Anthropic API key used by `.github/scripts` AI delivery tools |
| `GH_TOKEN` | Yes (CI tools only) | — | GitHub personal access token for API calls in CI scripts |
| `SENDGRID_API_KEY` | Yes (CI tools only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI tools only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI tools only) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI tools only) | `kylo.deng@capco.com` | Email address for CI tool notifications |
| `SENDER_EMAIL` | No (CI tools only) | `kylo.deng@capco.com` | Sender address for CI tool emails |

**Azure deployment secrets** (set in GitHub repository secrets):

| Secret | Description |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Azure publish profile for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Azure publish profile for `training-bot-frontend` App Service |

---

## 6. Running Tests

Tests are located in the `tests/` directory. The CI pipeline uses Python 3.13 and `uv`.

```bash
# Install dependencies (if not already done)
uv sync

# Run all tests with verbose output
uv run pytest tests/ -v
```

To run a specific test file:

```bash
uv run pytest tests/test_<module>.py -v
```

[TODO: What test files exist under `tests/`? No test source files were provided — confirm test coverage and any fixtures required.]

---

## 7. Deployment

Deployment is handled automatically by the **Test & Deploy** GitHub Actions workflow (`.github/workflows/deploy.yml`) on every push to `main`.

### Automatic CI/CD (GitHub Actions)

The workflow:
1. Runs the full test suite.
2. On success, exports `requirements.txt` from `uv` and deploys to two Azure App Service instances.

```yaml
# Triggered automatically on push to main
# See .github/workflows/deploy.yml
```

### Manual Deployment Steps

1. **Generate `requirements.txt`** from the `uv` lockfile:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy the API** to Azure App Service (`training-bot-api`):

```bash
# Using Azure CLI
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-api \
  --src-path . \
  --type zip
```

3. **Deploy the Frontend** to Azure App Service (`training-bot-frontend`):

```bash
az webapp deploy \
  --resource-group <your-resource-group> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

[TODO: What Azure resource group and region are used? These are not specified in any source file.]

4. **Ingest PDFs** into the vector store after first deployment by calling:

```bash
curl -X POST https://<training-bot-api>.azurewebsites.net/ingest
```

### AI Delivery Workflow Tools

Five additional GitHub Actions workflows are included for AI-assisted development operations:

| Workflow | Trigger | Purpose |
|---|---|---|
| `tool1_code_review.yml` | PR open/sync, Monday 08:00 UTC, manual | Claude code review → PR comments |
| `tool2_tech_docs.yml` | Push to main, Sunday 06:00 UTC, manual | Generate README / ARCHITECTURE / RUNBOOK |
| `tool3_business_docs.yml` | Version tag (`v*`), manual | Generate business solution overview |
| `tool4_auto_testing.yml` | PR open/sync on src files, Wednesday 07:00 UTC, manual | Generate or gap-analyse tests |
| `tool5_uat.yml` | `release/*` branch creation, manual | Generate UAT test pack or analyse results |

These tools require `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` set as repository secrets.

---

## 8. Known Issues / TODOs

The following are extracted directly from code comments and evident gaps in the source files:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is **disabled** for all LLM API calls. This is a security risk and should not be used in production. |
| `api/main.py` | `SHOW_TOOL_CALLS` env var parsing has a debug `print()` statement left in production code: `print(f"SHOW_TOOL_CALLS=...")` |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the source file — the full assessor system prompt and tool list are incomplete. |
| `api/rag_tools.py` | Source file is truncated — the `_collect_sources` function body is cut off; full implementation not visible. |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the `annotate_document` function body is incomplete or placeholder. |
| `core/chunker.py` | `split_by_words` function is truncated — implementation cut off. |
| `core/ingest.py` | `--pdf-dir` argument default path is truncated. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated mid-string (`{owner}/{r`). |
| `.github/scripts/tool3_business_docs.py` | `build_full_output` function is truncated. |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` function is truncated. |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function signature is truncated. |
| `.github/scripts/shared.py` | `send_email` and `email_html` and `write_audit_entry` functions are referenced by all tool scripts but their implementations are truncated/missing from the file shown. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated. |
| All tool workflows | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` in workflow env blocks — should be parameterised as secrets. |
| `tool5_uat.yml` | Escalation path in runbook: `[TODO: fill in team contacts]` |
| General | No disaster recovery, monitoring, or alerting configuration is present in any source file. |
| General | The frontend framework and start command are not evidenced in the provided source files. [TODO: What framework is used for the frontend served on port 5173?] |
| `core/vector_store.py` | Not provided — `ChromaStore`, `LocalFAISSStore`, and `PineconeStore` implementations are not visible. [TODO: Which vector store backend is used by default in production?] |