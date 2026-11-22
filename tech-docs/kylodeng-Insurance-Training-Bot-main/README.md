# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a web-based AI-powered training system designed to help new insurance agents in Hong Kong learn product knowledge, sales techniques, and customer handling skills. It operates in two modes: a **teacher mode** for guided learning and Q&A, and a **roleplay mode** where the agent practises conversations against a simulated customer profile. A RAG (Retrieval-Augmented Generation) pipeline indexes Sun Life insurance product PDFs so the AI can answer product-specific questions accurately and cite sources.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, with async streaming |
| LLM orchestration | LangGraph / LangChain | Agents with tool-calling |
| LLM provider | OpenRouter (default) / Anthropic | Configurable via env vars; default model `openai/gpt-oss-20b:free` |
| Annotation LLM | Anthropic Claude | `claude-sonnet-4-6` via shared.py; also used for CI tools |
| Embeddings / Vector store | Chroma, FAISS, or Pinecone | Selectable via `core/vector_store.py` |
| PDF ingestion | pdfplumber | PDF parsing and chunking |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Dependency management | uv | Replaces pip/poetry |
| CI/CD | GitHub Actions | 5 AI-delivery workflow tools |
| AI workflow tools | Anthropic Claude (`claude-sonnet-4-6`) | Code review, tech docs, biz docs, test gen, UAT |
| Email notifications | SendGrid | Via `shared.py` |
| Deployment | Azure App Service | API + Frontend as separate apps |
| Python version (runtime) | Python 3.13 | Per `deploy.yml` test job |
| Python version (CI tools) | Python 3.12 | Per individual workflow YAMLs |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Client (Browser / Chainlit UI)         │
└───────────────────────────┬──────────────────────────────┘
                            │  HTTP / SSE streaming
┌───────────────────────────▼──────────────────────────────┐
│                   FastAPI Backend (api/main.py)            │
│                                                           │
│  ┌─────────────────┐   ┌────────────────────────────┐    │
│  │  Teacher Agent  │   │  Assessor Agent             │    │
│  │  (streamed via  │   │  (one-shot ainvoke after   │    │
│  │  astream_events)│   │   roleplay ends)            │    │
│  └────────┬────────┘   └────────────┬───────────────┘    │
│           │                         │                     │
│           └──────────┬──────────────┘                     │
│                      │  LangGraph tool calls              │
│           ┌──────────▼──────────────┐                     │
│           │   RAG Tools (rag_tools) │                     │
│           │  search_product,        │                     │
│           │  search_all,            │                     │
│           │  compare_plans,         │                     │
│           │  lookup_exclusions,     │                     │
│           │  lookup_hospital_network│                     │
│           │  search_claim_procedure │                     │
│           │  list_products,         │                     │
│           │  get_current_date       │                     │
│           └──────────┬──────────────┘                     │
│                      │                                     │
│           ┌──────────▼──────────────┐                     │
│           │   Vector Store          │                     │
│           │  (Chroma / FAISS /      │                     │
│           │   Pinecone)             │                     │
│           └─────────────────────────┘                     │
│                                                           │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Session Manager (api/sessions.py)                 │  │
│  │  Persisted to data/sessions.json                   │  │
│  └────────────────────────────────────────────────────┘  │
│                                                           │
│  Static file mount: /docs → data/ (PDF serving)          │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│          Ingestion Pipeline (core/)                       │
│  PDFs → annotator (LLM) → chunker → embeddings → store  │
│  Annotation cached to .annot.json sidecar files          │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│          GitHub Actions CI/CD (.github/workflows/)        │
│  Tool 1: Claude code review on PRs                       │
│  Tool 2: Auto tech-doc generation on merge to main       │
│  Tool 3: Business doc generation on release tags         │
│  Tool 4: Auto test generation on PRs / schedule          │
│  Tool 5: UAT test pack generation / defect analysis      │
│  deploy.yml: pytest → Azure App Service (API + Frontend) │
└──────────────────────────────────────────────────────────┘
```

**Data flow summary:**
1. Insurance product PDFs are placed in `data/Insurance-product-info/`.
2. `POST /ingest` triggers `core/ingest.py`, which annotates each PDF with an LLM, filters irrelevant pages, chunks text, embeds, and saves to the vector store.
3. When a user sends a message, `api/main.py` routes it to either the teacher or roleplay agent.
4. The teacher/assessor agents (LangGraph, `api/agent.py`) call RAG tools (`api/rag_tools.py`) against the vector store to retrieve grounded, cited answers.
5. Responses are streamed back to the client via Server-Sent Events.
6. Sessions (conversation history + customer profiles) are persisted to `data/sessions.json`.

---

## 4. Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 2. Install uv (if not already installed)
pip install uv
```

```bash
# 3. Install all dependencies
uv sync
```

```bash
# 4. Copy and populate the environment file
cp .env.example .env
# Edit .env with your API keys and settings (see Environment Variables section)
```

```bash
# 5. Place insurance product PDFs in the data directory
# Example structure:
#   data/Insurance-product-info/ProductName/product.pdf
```

```bash
# 6. Ingest PDFs into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
# Or via the API after starting the server:
# curl -X POST http://localhost:8000/ingest
```

```bash
# 7. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 8. Access the API
# API docs:    http://localhost:8000/docs
# Chainlit UI: http://localhost:8000  (if frontend is served separately, see step 9)
```

```bash
# 9. (Optional) Start the Vite frontend dev server on port 5173
# [TODO: What is the frontend framework/directory and start command?]
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name used by the agents and annotation pipeline |
| `SHOW_TOOL_CALLS` | No | `true` | Whether to log and stream tool call events by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by the five GitHub Actions AI tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub Personal Access Token for the CI workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub org/user owning the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Email address to receive CI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | From-address for CI tool emails |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | GitHub secret: Azure publish profile for the API App Service (`training-bot-api`) |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | GitHub secret: Azure publish profile for the Frontend App Service (`training-bot-frontend`) |

> **Note:** `API_KEY`, `OPENAI_URL_BASE`, and `OPENAI_MODEL` are runtime variables loaded via `python-dotenv`. The annotation pipeline in `core/ingest.py` also reads `OPENAI_MODEL` and `OPENAI_URL_BASE` to call the LLM for PDF annotation — by default it points to the Anthropic base URL (`https://api.anthropic.com/v1`) with model `claude-sonnet-4-6`.

---

## 6. Running Tests

```bash
# Run the full test suite
uv run pytest tests/ -v
```

> [TODO: What test files exist under `tests/`? No test files were included in the provided source.]

The CI pipeline runs tests automatically on every push and pull request to `main` via `.github/workflows/deploy.yml` before any deployment proceeds.

---

## 7. Deployment

### Automated (GitHub Actions — recommended)

Deployment to Azure App Service is triggered automatically on every push to `main`, after tests pass:

```
Push to main
  → test job (pytest)
  → deploy-api job (Azure App Service: training-bot-api)
  → deploy-frontend job (Azure App Service: training-bot-frontend)
```

Required GitHub secrets:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API`
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Manual deployment steps

```bash
# 1. Generate a requirements.txt from uv lock file
uv export --no-dev --format requirements-txt -o requirements.txt
```

```bash
# 2. Deploy API to Azure App Service
# [TODO: Document manual az CLI deployment command if needed]
# The workflow uses: azure/webapps-deploy@v3 with app-name: training-bot-api
```

```bash
# 3. Deploy Frontend to Azure App Service
# The workflow uses: azure/webapps-deploy@v3 with app-name: training-bot-frontend
```

### Ingesting documents post-deploy

```bash
# After deploying, trigger ingestion via the API endpoint:
curl -X POST https://<your-api-app>.azurewebsites.net/ingest
```

> [TODO: Does the ingestion endpoint require authentication? No auth middleware was visible in the provided source.]

---

## 8. Known Issues / TODOs

Extracted from code comments and structure:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled (`verify=False`) on both `httpx.Client` and `httpx.AsyncClient` — this is a security risk in production |
| `api/main.py` | `SHOW_TOOL_CALLS` has a print-statement debug line that should be removed before production |
| `api/agent.py` | File is truncated in source; `make_teacher_agent` and `make_assessor_agent` factory functions and the full assessor system prompt are not shown — [TODO: confirm agent graph wiring] |
| `api/rag_tools.py` | File is truncated; tool definitions beyond `_collect_sources` are not shown — [TODO: confirm all 8 tool implementations] |
| `core/ingest.py` | File is truncated; `--pdf-dir` argument default and remaining CLI argument parser code are cut off |
| `core/chunker.py` | `split_by_words` function body is truncated |
| `core/annotator.py` | "custom annotation logic" comment placeholder present — logic is incomplete in provided source |
| `tool1_code_review.py` | File is truncated after the PR comment body template |
| `tool2_tech_docs.py` | `build_index` function has a variable reference bug: uses `{r` (truncated) instead of `{repo}` |
| `tool3_business_docs.py` | File is truncated after `build_full_output` |
| `tool4_auto_testing.py` | File is truncated after `build_test_report` |
| `tool5_uat.py` | `parse_scenarios` has an incomplete type hint `list[d` (truncated) |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated |
| All CI tools | `send_email`, `email_html`, and `write_audit_entry` are imported from `shared.py` but their implementations are not present in the provided `shared.py` source |
| `shared.py` | `NOTIFY_EMAIL` and `SENDER_EMAIL` default to a Capco employee email — these should be changed before use in a different organisation |
| Deployment | No DR (Disaster Recovery) strategy or multi-region setup is evident |
| Deployment | No monitoring or alerting configuration (e.g. Application Insights) is evident |
| Sessions | Sessions are stored in a local JSON file (`data/sessions.json`), which will not persist or scale correctly on a multi-instance Azure App Service deployment |
| Ingestion | [TODO: Does the `/ingest` endpoint require authentication?] |
| Frontend | [TODO: What framework is the frontend? No frontend source files were provided.] |