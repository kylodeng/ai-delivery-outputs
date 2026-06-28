# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents, built around a Retrieval-Augmented Generation (RAG) pipeline over real insurance product PDFs. It provides two modes: a **Teacher mode** for interactive coaching and concept explanation, and a **Roleplay/Assessment mode** where trainees practice sales conversations against simulated Hong Kong customer profiles before receiving a structured accuracy assessment. The system is backed by a FastAPI server and served through a Chainlit-compatible frontend, with product knowledge grounded in a vector store built from ingested insurance documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Language | Python | 3.13 (CI), 3.12 (workflow scripts) |
| Package manager | uv | via `astral-sh/setup-uv@v3` |
| Web framework | FastAPI | — |
| LLM orchestration | LangChain / LangGraph | `langchain-core`, `langchain-openai` |
| LLM provider | OpenRouter (default) | `openai/gpt-oss-20b:free`; configurable via env |
| LLM provider (CI tools) | Anthropic Claude | `claude-sonnet-4-6` |
| Embeddings / vector store | Chroma, FAISS, or Pinecone | Selectable via `core/vector_store.py` |
| PDF processing | pdfplumber | — |
| Frontend UI | Chainlit | Served by FastAPI static mount |
| HTTP client | httpx | SSL verification disabled — see Known Issues |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| CI/CD | GitHub Actions | 5 AI-powered workflow tools |
| Deployment target | Azure App Service | Separate apps for API and frontend |
| AI workflow scripts | Anthropic Claude API + SendGrid | `claude-sonnet-4-6`; email via SendGrid |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Client (Browser)                       │
│               Chainlit UI  /  Vite dev server                 │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP / SSE (streaming)
┌───────────────────────────▼──────────────────────────────────┐
│                    FastAPI  (api/main.py)                      │
│  • /ingest  — triggers PDF ingestion pipeline                  │
│  • /chat    — teacher-mode streaming via astream_events        │
│  • /roleplay — roleplay session management                     │
│  • /assess  — post-roleplay assessor agent (ainvoke)           │
│  • /docs/*  — static file server for raw PDFs                  │
└──────┬────────────────────┬─────────────────────────────────┘
       │                    │
┌──────▼──────┐    ┌────────▼──────────────────────────────────┐
│  api/agent  │    │          core/  (RAG library)              │
│  Teacher &  │    │  ingest.py → annotator.py → chunker.py     │
│  Assessor   │    │  → vector_store.py (Chroma/FAISS/Pinecone) │
│  LangGraph  │    └────────────────────────────────────────────┘
│  agents     │
└──────┬──────┘
       │  LangChain tools
┌──────▼──────────────────────────────────────┐
│            api/rag_tools.py                  │
│  8 tools: list_products, search_product,     │
│  search_all, lookup_hospital_network,        │
│  compare_plans, lookup_exclusions,           │
│  search_claim_procedure, get_current_date    │
└─────────────────────────────────────────────┘

Session state: data/sessions.json (api/sessions.py)

GitHub Actions (5 AI workflow tools):
  tool1_code_review   — PR diff → Claude → PR comment + report
  tool2_tech_docs     — repo files → Claude → README / ARCH / Runbook
  tool3_business_docs — repo files → Claude → Solution Overview doc
  tool4_auto_testing  — source files → Claude → generated test files
  tool5_uat           — acceptance criteria → Claude → UAT test pack / defect report
```

**Data flow for a teacher-mode query:**
1. User sends a message via the Chainlit UI.
2. FastAPI streams the request through the Teacher LangGraph agent.
3. The agent selects one or more RAG tools from `api/rag_tools.py`.
4. RAG tools query the vector store (Chroma / FAISS / Pinecone) built from ingested insurance PDFs.
5. Retrieved chunks are returned to the agent with inline source IDs (`[S1]`, `[S2]`, …).
6. The agent streams a grounded response back to the UI with citation markers.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install uv** (Python package manager)

```bash
pip install uv
```

3. **Install dependencies**

```bash
uv sync
```

4. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # if an example file exists, otherwise create manually
```

5. **Add insurance product PDFs** to the data directory

```
data/Insurance-product-info/
    <ProductFolder>/
        product.pdf
```

6. **Ingest PDFs into the vector store**

```bash
# Via the FastAPI endpoint (server must be running — see step 7 first), or directly:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

7. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

8. **Access the UI**

Open [http://localhost:8000](http://localhost:8000) in your browser.  
During development, a Vite dev server on `http://localhost:5173` is also supported.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for the main teacher/assessor agents |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool-call events; overridable per-session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI tools only) | — | Anthropic API key used by the 5 GitHub Actions workflow scripts |
| `GH_TOKEN` | Yes (CI tools only) | — | GitHub personal access token for Actions scripts |
| `SENDGRID_API_KEY` | Yes (CI tools only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI tools) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI tools) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI tools) | `kylo.deng@capco.com` | Recipient email for CI tool notifications |
| `SENDER_EMAIL` | No (CI tools) | `kylo.deng@capco.com` | Sender email for CI tool notifications |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure publish profile secret for the API App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure publish profile secret for the Frontend App Service |

---

## 6. Running Tests

Tests are located in the `tests/` directory and run with pytest via uv.

```bash
uv run pytest tests/ -v
```

The CI pipeline runs this automatically on every push and pull request to `main` via `.github/workflows/deploy.yml`.

[TODO: What test framework and fixtures are used? No test files were provided in the source — are there existing tests in `tests/`?]

---

## 7. Deployment

### Prerequisites

- Azure App Service instances named `training-bot-api` and `training-bot-frontend` must already exist.
- GitHub repository secrets `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be set.

### Automatic deployment (CI/CD)

Deployment to Azure App Service is triggered automatically on every push to `main` (after tests pass):

```
git push origin main
```

The workflow (`.github/workflows/deploy.yml`) will:
1. Run `uv run pytest tests/ -v`
2. Export `requirements.txt` via `uv export --no-dev --format requirements-txt`
3. Deploy API to `training-bot-api` Azure App Service
4. Deploy frontend to `training-bot-frontend` Azure App Service

### Manual ingestion (first-time or re-ingest)

```bash
# With the server running, POST to the ingest endpoint:
curl -X POST http://localhost:8000/ingest

# Or run the ingestion script directly:
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
```

### AI Workflow Tools (GitHub Actions)

Five AI-powered tools run automatically or on demand:

| Tool | Trigger | Manual dispatch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC | `workflow_dispatch` (mode: `repo` or `pr`) |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC | `workflow_dispatch` |
| Tool 3 — Business Docs | Version tag (`v*`) | `workflow_dispatch` with `project_name` and `release_version` |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC | `workflow_dispatch` (mode: `generate` or `gap-analysis`) |
| Tool 5 — UAT | `release/*` branch creation | `workflow_dispatch` (mode: `generate` or `analyse`) |

To trigger manually via GitHub UI: **Actions → select workflow → Run workflow**.

---

## 8. Known Issues / TODOs

The following issues are extracted directly from code comments:

- **SSL verification disabled** — `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set in `api/main.py` and `core/ingest.py`. This suppresses certificate validation and should not be used in production.

- **`send_email`, `email_html`, `write_audit_entry` referenced but not defined** — `tool1_code_review.py`, `tool2_tech_docs.py`, `tool3_business_docs.py`, `tool4_auto_testing.py`, and `tool5_uat.py` all import `send_email`, `email_html`, and `write_audit_entry` from `shared.py`, but the provided `shared.py` is truncated and these functions are not shown. [TODO: Are these functions implemented in the full `shared.py`?]

- **`api/agent.py` is incomplete** — The `ASSESSOR_SYSTEM` prompt and the `make_teacher_agent` / `make_assessor_agent` factory functions are truncated. The file also imports `from langchain.agents import create_agent` which is not a standard LangChain export. [TODO: Confirm the correct LangGraph agent constructor used.]

- **Hardcoded email addresses** — `kylo.deng@capco.com` is hardcoded as both `NOTIFY_EMAIL` and `SENDER_EMAIL` defaults in `shared.py` and across all workflow YAML files.

- **Rate-limit pause comment mismatch** — `core/ingest.py` docstring states `"Default settings (batch_size=20, batch_delay=22s)"` but the function signature defaults are `batch_size=126` and `batch_delay=0`. The comment appears to be outdated.

- **Session persistence is file-based only** — Sessions are stored in `data/sessions.json`. There is no database backend, so concurrent writes or large-scale usage may cause issues. [TODO: Is a database migration planned?]

- **No DR / multi-region deployment** — The deployment targets a single Azure App Service with no evidence of geo-redundancy, disaster recovery, or health-check endpoints in the provided files.

- **Annotation caching** — PDF annotation results are cached to `.annot.json` sidecar files. Re-annotating requires deleting these files manually.

- **`tool2_tech_docs.py` has a syntax error** — The `build_index` function references `{owner}/{r` (truncated variable name `repo` rendered as `r`). [TODO: Confirm this is a truncation artefact and not present in the actual file.]

- **Escalation path not defined** — `RUNBOOK.md` template contains `[TODO: fill in team contacts]` for the escalation path section.