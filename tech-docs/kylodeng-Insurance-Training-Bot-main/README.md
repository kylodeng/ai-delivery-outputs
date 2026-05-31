# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a conversational AI system designed to train insurance sales agents in Hong Kong. It operates in two modes: a **teacher mode** for ongoing guided learning (explaining products, running exercises, and simulating mini-scenarios) and a **roleplay/assessment mode** where the agent practises with a simulated customer and receives a scored performance review. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life Hong Kong insurance product PDFs so that all product-specific answers are grounded in real documentation.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python async, with `lifespan` context manager |
| LLM (inference) | OpenAI-compatible API (default: OpenRouter) | Model: `openai/gpt-oss-20b:free` (configurable via env) |
| LLM (annotation/ingest) | Anthropic Claude via OpenAI-compat shim | Default model: `claude-sonnet-4-6` |
| Agent framework | LangGraph / LangChain | `create_agent`, `astream_events`, `ainvoke` |
| Embeddings / Vector store | Supports ChromaDB, FAISS (local), Pinecone | Configured via `core/vector_store.py` |
| PDF parsing | pdfplumber | Used in `core/chunker.py` |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| CI/CD | GitHub Actions | 5 AI-assisted workflow tools + deploy pipeline |
| AI workflow scripts | Anthropic Claude (`claude-sonnet-4-6`) | Used for code review, docs, testing, UAT |
| Email notifications | SendGrid | Via `shared.py` |
| Deployment | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| Python packaging | `uv` | `uv sync`, `uv export` |
| HTTP client | httpx | SSL verification disabled — see Known Issues |
| Environment config | python-dotenv | `.env` file at project root |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Clients                              │
│   Chainlit UI (port 8000)   /   Vite dev server (5173)     │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP / SSE streaming
┌──────────────────────▼──────────────────────────────────────┐
│                   FastAPI backend  (api/main.py)            │
│  • /ingest  — triggers PDF ingestion pipeline               │
│  • /chat    — teacher mode, streamed via astream_events     │
│  • /roleplay— roleplay turns, simulated customer            │
│  • /assess  — end-of-roleplay scoring via ainvoke           │
│  • /docs/*  — static file serving of raw PDFs              │
└──────┬──────────────────────────┬───────────────────────────┘
       │                          │
┌──────▼──────┐          ┌────────▼────────────────────────────┐
│  LangGraph  │          │       core/  (RAG library)          │
│  Agents     │◄────────►│  ingest.py  → annotator.py          │
│  (agent.py) │  tools   │  chunker.py → vector_store.py       │
│  Teacher    │          │  (Chroma / FAISS / Pinecone)        │
│  Assessor   │          └─────────────────────────────────────┘
└──────┬──────┘
       │ LangChain tool calls
┌──────▼──────────────────────────────────────────────────────┐
│               api/rag_tools.py  (8 tools)                   │
│  get_current_date, list_products, search_product,           │
│  search_all, lookup_hospital_network, compare_plans,        │
│  lookup_exclusions, search_claim_procedure                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              .github/  (AI Delivery Workflows)              │
│  Tool 1: Claude code review   → posts PR comments           │
│  Tool 2: Claude tech docs     → writes README/ARCH/RUNBOOK  │
│  Tool 3: Claude business docs → solution overview + gaps    │
│  Tool 4: Claude test gen      → generates pytest/jest files │
│  Tool 5: Claude UAT pack      → test scenarios + defects    │
└─────────────────────────────────────────────────────────────┘
```

**Data flow summary:**

1. PDF files placed under `data/Insurance-product-info/` are ingested via `POST /ingest` (or the CLI `core/ingest.py`).
2. `ingest_directory` calls the LLM annotator to classify each document and page, then `extract_chunks_from_pdf` (pdfplumber) produces chunk dicts.
3. Chunks are embedded and stored in the configured vector store (Chroma/FAISS/Pinecone); the index is persisted to disk.
4. At startup, FastAPI loads the saved vector store and makes 8 RAG tools available to LangGraph agents.
5. Chat requests hit the FastAPI backend; the teacher agent streams responses via SSE; the assessor agent runs as a one-shot invocation after a roleplay session.
6. Sessions (conversation history + customer profiles) are persisted in `data/sessions.json`.

---

## 4. Local Development Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
   cd Insurance-Training-Bot-main
   ```

2. **Install `uv` (Python package manager)**

   ```bash
   pip install uv
   ```

3. **Install Python dependencies**

   ```bash
   uv sync
   ```

4. **Create your environment file**

   ```bash
   cp .env.example .env   # if provided, otherwise create from scratch
   ```

   Then populate `.env` — see [Environment Variables](#5-environment-variables) below.

5. **Place insurance product PDFs** under:

   ```
   data/Insurance-product-info/
   ```

6. **Ingest PDFs into the vector store**

   ```bash
   uv run python -m core.ingest --pdf-dir data/Insurance-product-info
   ```

   Or via the API once the server is running:

   ```bash
   curl -X POST http://localhost:8000/ingest
   ```

7. **Start the FastAPI backend**

   ```bash
   uv run uvicorn api.main:app --reload --port 8000
   ```

8. **Access the application**

   - API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
   - Chainlit UI (if configured): [http://localhost:8000](http://localhost:8000)
   - Vite dev frontend (if applicable): [http://localhost:5173](http://localhost:5173)

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the OpenAI-compatible inference endpoint (e.g. OpenRouter key) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM inference endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for chat/agent inference; also used for annotation during ingest |
| `SHOW_TOOL_CALLS` | No | `true` | Stream LangGraph tool-call events to the UI; can be overridden per-session |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by the five GitHub Actions AI workflow tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub token for Actions workflows (code review, doc writing, PR comments) |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI workflow emails |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure publish profile secret for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure publish profile secret for `training-bot-frontend` App Service |

---

## 6. Running Tests

Tests are located in the `tests/` directory and use **pytest**.

```bash
# Run all tests
uv run pytest tests/ -v
```

The CI pipeline (`.github/workflows/deploy.yml`) runs the same command on every push and pull request targeting `main`, using Python 3.13.

[TODO: Are there any test fixtures or mock data files required in the `tests/` directory before tests can run locally?]

[TODO: Is there a minimum coverage threshold enforced in CI?]

---

## 7. Deployment

### Automated deployment via GitHub Actions

Deployment to Azure App Service is triggered automatically on every push to `main` (after tests pass):

```yaml
# Defined in .github/workflows/deploy.yml
# Jobs: deploy-api and deploy-frontend
# Both require the test job to pass first
```

Required GitHub secrets:

```
AZURE_WEBAPP_PUBLISH_PROFILE_API
AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND
```

### Manual deployment steps

1. **Install dependencies and export `requirements.txt`**

   ```bash
   uv sync
   uv export --no-dev --format requirements-txt -o requirements.txt
   ```

2. **Deploy API to Azure App Service**

   ```bash
   # Using Azure CLI
   az webapp deploy \
     --resource-group <your-resource-group> \
     --name training-bot-api \
     --src-path .
   ```

3. **Deploy Frontend to Azure App Service**

   ```bash
   az webapp deploy \
     --resource-group <your-resource-group> \
     --name training-bot-frontend \
     --src-path .
   ```

4. **After deployment, ingest documents** (first deployment only, or when PDFs change):

   ```bash
   curl -X POST https://<your-api-app>.azurewebsites.net/ingest
   ```

[TODO: Is there a Dockerfile or `startup.sh` command configured on the Azure App Service for the API?]

[TODO: What does `training-bot-frontend` serve — a Chainlit app, a Vite build, or both?]

---

## 8. Known Issues / TODOs

Extracted from source code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is disabled for all outbound LLM API calls. This is a security risk and should be resolved before production use. |
| `api/main.py` | `SHOW_TOOL_CALLS` env parsing contains a debug `print` statement with a stray space: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` — should be cleaned up. |
| `api/agent.py` | Assessor agent system prompt is truncated in the provided source — the full tool list and assessment rubric are cut off (`get_cu...`). |
| `core/annotator.py` | Custom annotation logic placeholder comment: `# custom annotation logic` — the full implementation is not visible in provided files. |
| `core/chunker.py` | `split_by_words` function is truncated — hard word-count fallback implementation is incomplete in the provided source. |
| `core/ingest.py` | CLI entrypoint (`if __name__ == "__main__"`) is truncated — `--pdf-dir` argument default path is cut off. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated — full implementation not visible. |
| `tool2_tech_docs.py` | `build_index` function contains a syntax error in the f-string: `{owner}/{r` — the repo variable reference is cut off. |
| `tool1_code_review.py` | `review_pr` function and the PR comment template are truncated — auto-generated comment footer is incomplete. |
| `tool4_auto_testing.py` | `build_test_report` function is truncated — the summary table row format is cut off. |
| `tool5_uat.py` | `build_test_pack_csv` function signature is truncated. |
| `shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all five tools but their implementations are truncated/missing from the provided source. |
| General | No disaster recovery (DR) configuration is evident — single Azure region deployment with no failover. |
| General | No monitoring or alerting configuration (e.g. Application Insights) is evident in the provided files. |
| General | Escalation contacts are not defined — all runbook escalation paths should be filled in with team contacts. |
| `sessions.py` | Session persistence is a flat JSON file (`data/sessions.json`) — no concurrency protection for simultaneous writes. |