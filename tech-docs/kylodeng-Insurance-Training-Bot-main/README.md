# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based AI training platform designed to help new insurance agents master product knowledge and sales skills in a Hong Kong market context. It provides two modes: a **teacher mode** for interactive learning sessions and a **roleplay mode** where agents practise against a simulated customer with a randomised profile. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life insurance product PDFs into a vector store, allowing the AI to answer product-specific questions accurately with source citations.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 |
| LLM Orchestration | LangGraph / LangChain | via `langchain`, `langchain-openai` |
| LLM Provider | OpenRouter (default) or Anthropic | Configurable via `OPENAI_URL_BASE` |
| LLM Model | `openai/gpt-oss-20b:free` (default) | Configurable via `OPENAI_MODEL` |
| Embedding / Vector Store | ChromaDB, FAISS, or Pinecone | Selectable; see `core/vector_store.py` |
| PDF Processing | pdfplumber | — |
| Dependency Management | uv | astral-sh/setup-uv@v3 |
| CI/CD | GitHub Actions | — |
| Deployment | Azure App Service | Two apps: API + Frontend |
| AI Delivery Workflows | Anthropic Claude (`claude-sonnet-4-6`) | Code review, docs, testing, UAT |
| Email | SendGrid | Used by CI/CD workflow scripts |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        Client / Chainlit UI                     │
│                   (http://localhost:5173 or :8000)              │
└──────────────────────────┬─────────────────────────────────────┘
                           │ HTTP / SSE streaming
┌──────────────────────────▼─────────────────────────────────────┐
│                      FastAPI (api/main.py)                       │
│  • /ingest  – trigger PDF ingestion                             │
│  • /chat    – teacher mode (streamed via astream_events)        │
│  • /roleplay – customer simulation                              │
│  • /assess  – post-roleplay assessor agent (ainvoke)            │
│  • /docs    – static file server for PDFs under /data           │
│  • Session management (api/sessions.py → data/sessions.json)    │
└───────────┬───────────────────────┬────────────────────────────┘
            │                       │
┌───────────▼───────┐   ┌───────────▼──────────────────────────┐
│  LangGraph Agents  │   │         core/ RAG Library             │
│  (api/agent.py)    │   │  ingest.py → annotator.py            │
│  • Teacher agent   │   │  chunker.py → vector_store.py        │
│  • Assessor agent  │   │  (Chroma / FAISS / Pinecone)         │
└───────────┬────────┘   └───────────┬──────────────────────────┘
            │ tool calls              │ similarity search
┌───────────▼────────────────────────▼──────────────────────────┐
│               RAG Tools (api/rag_tools.py)                      │
│  search_product · search_all · compare_plans                    │
│  lookup_hospital_network · lookup_exclusions                    │
│  search_claim_procedure · list_products · get_current_date     │
└────────────────────────────────────────────────────────────────┘

GitHub Actions (.github/workflows/)
  tool1_code_review.yml   → Claude PR / repo code review
  tool2_tech_docs.yml     → Auto README / ARCHITECTURE / RUNBOOK
  tool3_business_docs.yml → Business solution overview
  tool4_auto_testing.yml  → Auto test generation / gap analysis
  tool5_uat.yml           → UAT test pack generation & defect analysis
  deploy.yml              → pytest → Azure App Service (API + Frontend)
```

**Data flow (teacher mode):**
1. User sends a message from the Chainlit UI.
2. FastAPI passes it to the LangGraph teacher agent.
3. The agent decides which RAG tool to call (e.g. `search_product`).
4. The tool queries the vector store and returns ranked chunks with source metadata.
5. The agent composes a response with inline `[[Sn]]` citations and streams it back.
6. Source metadata is accumulated via a `contextvars.ContextVar` and returned alongside the streamed response.

**PDF ingestion:**
1. `POST /ingest` walks `data/Insurance-product-info/` recursively.
2. Each PDF is annotated once by an LLM (doc-level + page-level) and cached to a `.annot.json` sidecar.
3. Relevant pages are chunked by `core/chunker.py` (heading/bullet/paragraph heuristics).
4. Chunks are embedded in batches and upserted into the configured vector store.

---

## 4. Local Development Setup

**Prerequisites:** Python 3.13, [uv](https://github.com/astral-sh/uv) installed.

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 2. Install all dependencies (creates .venv automatically)
uv sync
```

```bash
# 3. Copy and populate environment variables
cp .env.example .env
# Edit .env — see Environment Variables section below
```

```bash
# 4. Ingest insurance product PDFs into the vector store
#    Place PDFs under data/Insurance-product-info/ first, then:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

```bash
# 5. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 6. (Optional) Start the Chainlit frontend on its default port
uv run chainlit run <chainlit_app_file> --port 5173
# [TODO: What is the Chainlit entry-point filename?]
```

> The API will be available at `http://localhost:8000`. PDF files are served at `http://localhost:8000/docs/<path>`.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model identifier |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per session in UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Claude API key used by the five GitHub Actions AI delivery workflows |
| `GH_TOKEN` | Yes (CI only) | — | GitHub token for the AI delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient address for workflow notification emails |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for workflow notification emails |

> **Note:** `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be set as GitHub repository secrets for the `deploy.yml` workflow.

---

## 6. Running Tests

```bash
# Run the full test suite
uv run pytest tests/ -v
```

The `deploy.yml` CI workflow runs this automatically on every push and pull request to `main`. Deployment is blocked if tests fail.

```bash
# [TODO: Are there test fixtures or data files that need to be present before running tests locally?]
```

---

## 7. Deployment

### Automated (GitHub Actions)

Deployment is triggered automatically on every push to `main` after tests pass:

- **API** → Azure App Service app named `training-bot-api`
- **Frontend** → Azure App Service app named `training-bot-frontend`

The workflow (`deploy.yml`) exports a `requirements.txt` from `uv` and deploys via `azure/webapps-deploy@v3`.

Required GitHub secrets:
```
AZURE_WEBAPP_PUBLISH_PROFILE_API
AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND
```

### Manual PDF Ingestion

```bash
# Run from the project root after setting environment variables
uv run python -m core.ingest --pdf-dir data/Insurance-product-info

# Or trigger via the API endpoint (requires the server to be running)
curl -X POST http://localhost:8000/ingest
```

### AI Delivery Workflows (GitHub Actions)

Five additional workflows automate code review, documentation, testing, and UAT. They require the following GitHub secrets: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC cron, manual |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC cron, manual |
| Tool 3 — Business Docs | Version tag (`v*`), manual |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC cron, manual |
| Tool 5 — UAT | `release/*` branch creation, manual |

---

## 8. Known Issues / TODOs

Extracted from code comments and evidenced gaps:

| Location | Issue / TODO |
|---|---|
| `api/agent.py` | File is truncated in the repository; `ASSESSOR_SYSTEM` prompt and `make_teacher_agent` / `make_assessor_agent` factory functions are not fully visible — [TODO: confirm agent factory implementations are complete] |
| `api/main.py` | `http_client=httpx.Client(verify=False)` — SSL verification is disabled for all LLM HTTP calls; this should be addressed before production use |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug print statement left in production code |
| `api/sessions.py` | Session persistence uses a flat JSON file (`data/sessions.json`); no database backend — may not scale under concurrent load |
| `core/ingest.py` | Default `OPENAI_URL_BASE` in `_build_ingest_llm()` points to `https://api.anthropic.com/v1` while `main.py` defaults to OpenRouter — these are inconsistent |
| `core/annotator.py` | File is truncated; custom annotation logic comment reads `# custom annotation logic` with no implementation visible — [TODO: is the annotator complete?] |
| `core/chunker.py` | File is truncated; `split_by_words` function body is not visible |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced across all tool scripts but their implementations are not visible in the provided file (file is truncated) |
| `.github/scripts/tool1_code_review.py` | File is truncated; `review_pr` function and `main` entrypoint are incomplete |
| `.github/scripts/tool2_tech_docs.py` | File is truncated; `build_index` function references undefined variable `r` instead of `repo` |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function signature is truncated |
| General | No disaster recovery, database backup, or monitoring/alerting configuration is present in any of the provided files |
| General | [TODO: What is the Chainlit frontend entry-point filename?] |
| General | [TODO: Is there a `.env.example` file? If not, one should be created.] |