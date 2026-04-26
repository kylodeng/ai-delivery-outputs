# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a conversational AI system designed to train new insurance sales agents in Hong Kong. It provides two modes: a **Teacher mode** for interactive coaching on insurance concepts, products, and sales techniques, and a **Roleplay/Assessment mode** where the agent practises with a simulated customer and receives a structured performance review. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life Hong Kong product PDFs into a vector store, so agents always receive answers grounded in real policy documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python async |
| LLM Orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM Provider | OpenRouter (default) / Anthropic | Model: `openai/gpt-oss-20b:free` default; `claude-sonnet-4-6` used by CI tools |
| Embeddings / Vector Store | ChromaDB, FAISS, or Pinecone | Selectable via `core/vector_store.py`; `LocalFAISSStore` default |
| PDF Parsing | pdfplumber | Heuristic chunker in `core/chunker.py` |
| Document Annotation | LLM-based (same ChatOpenAI client) | Cached to `.annot.json` sidecars |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Frontend | [TODO: what is the frontend technology? A Chainlit UI is referenced but no frontend source files were provided] | Served separately; Vite dev server on port 5173 referenced |
| Dependency Management | `uv` (astral-sh) | Python 3.13 for CI; 3.12 for workflow scripts |
| CI/CD | GitHub Actions | 5 AI-powered delivery tools + deploy workflow |
| Deployment | Azure App Service | Two apps: `training-bot-api` and `training-bot-frontend` |
| CI AI Tools | Anthropic Claude (`claude-sonnet-4-6`) | Code review, tech docs, business docs, test gen, UAT |
| Email Notifications | SendGrid | Used by CI workflow scripts |
| HTTP Client | httpx | SSL verification disabled (`verify=False`) |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Frontend (Chainlit / Vite dev on :5173)                 │
│  – Teacher chat (streaming)                              │
│  – Roleplay session → Assessment report                  │
└───────────────────┬──────────────────────────────────────┘
                    │ HTTP / SSE
┌───────────────────▼──────────────────────────────────────┐
│  FastAPI backend (:8000)  [api/main.py]                  │
│  – POST /ingest     → triggers PDF ingestion pipeline    │
│  – Streaming chat   → Teacher LangGraph agent            │
│  – POST /assess     → Assessor LangGraph agent (one-shot)│
│  – Session CRUD     → api/sessions.py (sessions.json)    │
│  – GET  /docs/**    → static PDF serving                 │
└───────┬───────────────────────┬──────────────────────────┘
        │                       │
┌───────▼──────────┐   ┌────────▼──────────────────────────┐
│  LangGraph Agents│   │  RAG Tools  [api/rag_tools.py]    │
│  [api/agent.py]  │   │  – search_product                 │
│  Teacher agent   │◄──│  – search_all                     │
│  Assessor agent  │   │  – list_products                  │
└──────────────────┘   │  – lookup_hospital_network        │
                        │  – compare_plans                  │
                        │  – lookup_exclusions              │
                        │  – search_claim_procedure         │
                        │  – get_current_date               │
                        └────────────┬──────────────────────┘
                                     │
                        ┌────────────▼──────────────────────┐
                        │  Vector Store  [core/vector_store] │
                        │  Chroma | FAISS | Pinecone         │
                        └────────────┬──────────────────────┘
                                     │ loaded at startup
                        ┌────────────▼──────────────────────┐
                        │  Ingestion Pipeline [core/ingest]  │
                        │  PDF → annotate (.annot.json)      │
                        │       → filter irrelevant pages    │
                        │       → chunk (core/chunker.py)    │
                        │       → embed → store.save()       │
                        └───────────────────────────────────┘
```

**Data flow summary:**
1. PDFs under `data/Insurance-product-info/` are processed by `core/ingest.py` — each document is annotated (LLM call, cached to `.annot.json`), irrelevant pages filtered, and text chunked into ≤280-word pieces.
2. Chunks are embedded and stored in the vector store (persisted to disk).
3. At API startup (`lifespan`), the vector store is loaded into memory.
4. On each user message, the LangGraph Teacher agent selects the appropriate RAG tool, retrieves relevant chunks with source metadata, and streams a cited response.
5. After a roleplay session, the Assessor agent uses the same RAG tools to verify every factual claim the trainee made, then returns a structured performance report.
6. Sessions (conversation history, customer profile) are persisted to `data/sessions.json`.

---

## 4. Local Development Setup

```bash
# 1. Prerequisites: Python 3.13, git
python --version   # should be 3.13.x
```

```bash
# 2. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 3. Install uv (fast Python package manager)
pip install uv
# or on macOS/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
# 4. Install all dependencies
uv sync
```

```bash
# 5. Create your environment file
cp .env.example .env   # if it exists, otherwise create .env manually
```

Edit `.env` with your values (see [Environment Variables](#5-environment-variables) below).

```bash
# 6. Ingest insurance product PDFs into the vector store
#    Place PDFs under data/Insurance-product-info/ then run:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
# or via the API endpoint after starting the server:
#   POST http://localhost:8000/ingest
```

```bash
# 7. Start the FastAPI backend
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
# 8. Start the frontend (if running Vite dev server separately)
# [TODO: what command starts the frontend? No frontend package.json was found in provided files]
```

The API will be available at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name for chat and ingestion annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by GitHub Actions CI workflow scripts |
| `GH_TOKEN` | Yes (CI only) | — | GitHub token used by CI workflow scripts to read repos and post PR comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Email address to notify after CI tool runs |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI notification emails |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure publish profile secret for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure publish profile secret for `training-bot-frontend` App Service |

> **Note:** SSL verification is disabled (`verify=False`) on the httpx clients used for LLM calls. Do not use this configuration in a production environment without a proper certificate setup.

---

## 6. Running Tests

Tests are located in the `tests/` directory. The CI pipeline uses Python 3.13 and `uv`.

```bash
# Run the full test suite
uv run pytest tests/ -v
```

```bash
# Run with coverage (if pytest-cov is installed)
uv run pytest tests/ -v --cov=api --cov=core
```

> [TODO: What test files exist in `tests/`? No test files were provided — are there existing tests or is the suite empty?]

---

## 7. Deployment

### Automated deployment (GitHub Actions)

Deployment is triggered automatically on every push to `main` (after tests pass):

```
git push origin main
```

The `deploy.yml` workflow:
1. Runs the test suite (`pytest tests/ -v`).
2. On success, generates `requirements.txt` via `uv export`.
3. Deploys the API to Azure App Service (`training-bot-api`) using the `AZURE_WEBAPP_PUBLISH_PROFILE_API` secret.
4. Deploys the frontend to Azure App Service (`training-bot-frontend`) using the `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` secret.

### Required GitHub Secrets for deployment

Set these in **Settings → Secrets and variables → Actions**:

```
AZURE_WEBAPP_PUBLISH_PROFILE_API        ← download from Azure Portal for training-bot-api
AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND   ← download from Azure Portal for training-bot-frontend
ANTHROPIC_API_KEY                       ← for CI tools (tools 1-5)
GH_TOKEN                                ← GitHub PAT with repo read/write
SENDGRID_API_KEY                        ← for email notifications
```

### Manual ingestion (run once or after adding new PDFs)

```bash
# On the deployed server, or locally pointing at the production data directory:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

Or via the API:

```bash
curl -X POST http://<your-app-url>/ingest
```

### CI AI Delivery Tools

Five additional workflows run against this repo:

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude code review posted as PR comment |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Auto-generates README, architecture doc, runbook |
| Tool 3 — Business Docs | Version tag (`v*`), manual | Business solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates test files or coverage gap analysis |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses completed results CSV |

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

### SSL verification disabled
```python
# api/main.py, core/ingest.py
http_client=httpx.Client(verify=False)
```
SSL certificate verification is disabled on all outbound LLM API calls. This is a security risk and must be addressed before production use.

### Hardcoded email addresses
```python
# .github/scripts/shared.py
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "kylo.deng@capco.com")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "kylo.deng@capco.com")
```
Personal email addresses are hardcoded as defaults. These should be replaced with team/service addresses.

### Incomplete `shared.py`
The `send_email`, `email_html`, and `write_audit_entry` functions referenced throughout the CI scripts are imported from `shared.py` but not shown in the provided file — the file appears to be truncated. [TODO: Are these functions implemented in the full version of `shared.py`?]

### Incomplete `tool1_code_review.py`
The file is truncated — the `review_pr` function's comment block and `main` entrypoint are cut off. [TODO: Is there a `review_repo` function for non-PR mode?]

### Incomplete `tool2_tech_docs.py`
The `build_index` function is truncated (references `{r` — an unfinished f-string). [TODO: Verify the full function in the source.]

### Assessor agent system prompt truncated
```python
# api/agent.py
"""...You have eight tools available:
- get_cu...
```
The `ASSESSOR_SYSTEM` prompt string is cut off. [TODO: Confirm the full assessor system prompt is present in source.]

### Annotation custom logic placeholder
```python
# core/annotator.py
# custom annotation logic
```
The `annotate_document` function body ends with a comment placeholder — the actual mapping/return logic is missing from the provided file. [TODO: Is the full annotator implementation present in the repo?]

### Chunker hard-cut incomplete
```python
# core/chunker.py
def split_by_words(text: str, max_words: int) -> list[str]:
    """
    Hard f...
```
The `split_by_words` function is truncated. [TODO: Confirm implementation in source.]

### No disaster recovery / monitoring
No DR configuration, health check endpoints, or monitoring/alerting setup is evident in the provided files. The CI runbook tool itself flags this pattern as a risk (see `SYSTEM_RUNBOOK` in `tool2_tech_docs.py`).

### Per-session show-tool-calls toggle
```python
# api/main.py
# The Chainlit UI "Show tool calls" toggle overrides this per-session.
SHOW_TOOL_CALLS = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
```
The per-session override mechanism depends on a Chainlit UI integration that is not present in the provided source files. [TODO: Where is the Chainlit session toggle wired up?]

### UAT test pack CSV format
[TODO: What is the expected column schema for the UAT results CSV consumed by Tool 5 analyse mode?]