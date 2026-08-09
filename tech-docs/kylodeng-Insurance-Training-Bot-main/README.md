# Insurance Training Bot

## 1. Project Overview

The Insurance Training Bot is a FastAPI-based application that helps new insurance agents in Hong Kong master sales techniques and product knowledge. It provides two modes: a **Teacher mode** for interactive coaching and an **Assessor mode** for evaluating agent performance after roleplay sessions. The system uses a Retrieval-Augmented Generation (RAG) pipeline over a knowledge base of Sun Life insurance product PDFs to ensure all product facts are grounded in real documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python async |
| LLM orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM provider | OpenRouter (default) | `openai/gpt-oss-20b:free`; configurable via env |
| Annotation LLM | Anthropic Claude | `claude-sonnet-4-6` (used in ingest + CI tools) |
| Embeddings / Vector store | FAISS (local), Chroma, Pinecone | Selectable via `core/vector_store.py` |
| PDF parsing | pdfplumber | — |
| HTTP client | httpx | SSL verification disabled — see Known Issues |
| Dependency manager | uv | Replaces pip/poetry |
| CI/CD | GitHub Actions | 5 AI-powered workflow tools |
| CI AI tools | Anthropic Claude (`claude-sonnet-4-6`) | Code review, docs, UAT, test gen |
| Email | SendGrid | Notifications from CI workflows |
| Deployment | Azure App Service | Two apps: API + Frontend |
| Python version | 3.13 (CI/CD), 3.12 (workflow scripts) | — |
| Frontend | [TODO: what framework is the frontend — Chainlit, Vite, or both?] | Vite dev server referenced in CORS config |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Client / UI                        │
│         (Chainlit UI or Vite dev server :5173)          │
└─────────────────┬───────────────────────────────────────┘
                  │ HTTP / SSE (streaming)
┌─────────────────▼───────────────────────────────────────┐
│               FastAPI Backend  (:8000)                  │
│  /ingest  /chat  /roleplay  /assess  /sessions  …       │
│                                                         │
│  ┌──────────────┐   ┌─────────────────────────────────┐ │
│  │  RAG Tools   │   │   LangGraph Agents              │ │
│  │ (rag_tools)  │◄──│  Teacher Agent (streamed)       │ │
│  │              │   │  Assessor Agent (one-shot)      │ │
│  └──────┬───────┘   └─────────────────────────────────┘ │
│         │                                               │
│  ┌──────▼───────────────────────────────────────────┐   │
│  │           core — RAG Library                     │   │
│  │  ingest_directory → annotator (LLM) → chunker    │   │
│  │  embed_chunks → vector store (FAISS/Chroma/      │   │
│  │                               Pinecone)          │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  /data/Insurance-product-info/  (PDFs + .annot.json)    │
│  /data/sessions.json            (session persistence)   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              GitHub Actions CI/CD                       │
│  Tool 1: Claude code review  (PR trigger / weekly)      │
│  Tool 2: Tech docs gen       (merge to main / weekly)   │
│  Tool 3: Business docs gen   (release tag)              │
│  Tool 4: Auto test gen       (PR trigger / weekly)      │
│  Tool 5: UAT facilitation    (release branch)           │
│  deploy.yml: test → Azure App Service (API + Frontend)  │
└─────────────────────────────────────────────────────────┘
```

**Data flow:**
1. PDFs in `data/Insurance-product-info/` are ingested via `POST /ingest` (or `python -m core.ingest`), which annotates each page using an LLM, chunks the text, and embeds it into the vector store.
2. At startup (`lifespan`), the FastAPI app loads the persisted vector store and session state from `data/sessions.json`.
3. On a chat request, the Teacher or Assessor LangGraph agent calls one of eight RAG tools (search, compare, lookup, etc.) which query the vector store and track source citations via a per-request `contextvars` list.
4. Responses are streamed back to the client via Server-Sent Events; source citations are injected inline as `[[Sn]]` markers.
5. PDF source files are served statically at `/docs/` so the UI can link directly to them.

---

## 4. Local Development Setup

**Prerequisites:** Python 3.13, [uv](https://github.com/astral-sh/uv)

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 2. Install dependencies (creates a virtual environment automatically)
uv sync
```

```bash
# 3. Copy and populate the environment file
cp .env.example .env
# Edit .env — see Environment Variables section below
```

```bash
# 4. Place insurance product PDFs in the data directory
# (directory must exist; PDFs will be discovered recursively)
mkdir -p data/Insurance-product-info
# Copy your PDF files here
```

```bash
# 5. Ingest PDFs into the vector store
uv run python -m core.ingest
# Or with options:
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose
```

```bash
# 6. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 7. (Optional) Start the frontend dev server
# [TODO: what command starts the frontend — npm run dev, chainlit run, etc.?]
```

The API will be available at `http://localhost:8000`. Interactive API docs at `http://localhost:8000/docs`.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name for the agent and annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by the five GitHub Actions CI tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub token used by CI scripts to read repos and post PR comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid key for CI notification emails |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tools write generated docs and reports |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI notifications |

> **Note:** `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be set as GitHub Actions secrets for the deploy workflow.

---

## 6. Running Tests

```bash
# Run all tests with verbose output
uv run pytest tests/ -v
```

```bash
# Run a specific test file
uv run pytest tests/test_chunker.py -v
```

The CI pipeline runs tests automatically on every push and pull request to `main` via `.github/workflows/deploy.yml`. Deployment is blocked if tests fail.

> [TODO: Are there any test fixtures or additional setup required before running tests locally, e.g. a test vector store or mock PDFs?]

---

## 7. Deployment

### Automated (CI/CD)

Deployment to Azure App Service is triggered automatically on every push to `main` after tests pass:

- **API** → Azure App Service app named `training-bot-api`
- **Frontend** → Azure App Service app named `training-bot-frontend`

The workflow exports a `requirements.txt` from `uv` before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

Required GitHub Actions secrets:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API`
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Manual / Local Ingest

To rebuild the vector store (e.g. after adding new PDFs):

```bash
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose
```

Or trigger via the API endpoint:

```bash
curl -X POST http://localhost:8000/ingest
```

### CI Tool Workflows (Manual Dispatch)

All five AI tooling workflows can be triggered manually from the GitHub Actions tab:

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude reviews PR diffs |
| Tool 2 — Tech Docs | Push to main, Sunday 06:00 UTC, manual | Generates README, architecture doc, runbook |
| Tool 3 — Business Docs | Version tag (`v*`), manual | Generates solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync, Wednesday 07:00 UTC, manual | Generates or analyses test files |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses results |

---

## 8. Known Issues / TODOs

Extracted from code comments and evidenced gaps:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled (`verify=False`) for both sync and async httpx clients — this should not be used in production |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug statement left in production code |
| `api/agent.py` | `from langchain.agents import create_agent` — the agent construction body is not shown in the provided files; [TODO: is `make_teacher_agent` / `make_assessor_agent` fully implemented?] |
| `core/ingest.py` | Default `OPENAI_URL_BASE` in `_build_ingest_llm()` points to `https://api.anthropic.com/v1` but the model env var defaults to `claude-sonnet-4-6` — mixing Anthropic and OpenRouter endpoints could cause auth failures if env is not explicitly set |
| `core/annotator.py` | Custom annotation logic section is truncated — [TODO: is there additional product-specific annotation handling?] |
| `core/chunker.py` | `split_by_words` function body is truncated in provided files |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced throughout but their implementations are truncated — [TODO: confirm these are fully implemented in `shared.py`] |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function references variable `r` (undefined — likely a typo for `repo`) |
| `tool1_code_review.py` | Auto-generated PR comment template is truncated |
| `tool4_auto_testing.py` | `build_test_pack_csv` in `tool5_uat.py` is truncated |
| Sessions / UAT | Escalation path in runbook template is `[TODO: fill in team contacts]` — no team contacts defined anywhere in the codebase |
| General | No disaster recovery or multi-region deployment configuration found |
| General | No monitoring or alerting configuration found (no Application Insights, no health check endpoint evidenced) |
| General | The frontend technology is not fully clear from the provided files — Chainlit and Vite are both referenced |