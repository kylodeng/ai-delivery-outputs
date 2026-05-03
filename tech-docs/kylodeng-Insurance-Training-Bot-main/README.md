# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a Hong Kong–focused insurance sales training application that uses a RAG (Retrieval-Augmented Generation) pipeline over insurance product PDFs to power two AI-driven modes: a **Teacher agent** for interactive coaching and a **Roleplay/Assessor agent** for simulated customer conversations with automated performance scoring. The backend exposes a FastAPI service backed by a LangGraph agent, while a set of GitHub Actions workflows provide automated code review, documentation generation, test generation, and UAT facilitation via Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python async |
| LLM (app) | OpenAI-compatible API (OpenRouter default) | Model: `openai/gpt-oss-20b:free` configurable via `OPENAI_MODEL` |
| LLM (CI tools) | Anthropic Claude | `claude-sonnet-4-6` |
| Agent framework | LangGraph / LangChain | `langchain`, `langchain-openai`, `langchain-core` |
| Vector store | Chroma / FAISS / Pinecone | Abstracted via `BaseVectorStore`; `get_vector_store()` selects backend |
| PDF ingestion | pdfplumber | Custom chunker in `core/chunker.py` |
| Embeddings | Voyage AI (inferred from rate-limit comments) | [TODO: confirm embedding model and provider] |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| HTTP client | httpx | SSL verification disabled (`verify=False`) |
| Package manager | uv (astral-sh) | `uv sync` / `uv export` |
| Python version | 3.13 (CI), 3.12 (workflow scripts) | |
| Deployment | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| CI/CD | GitHub Actions | 6 workflows (deploy + 5 AI tools) |
| CI LLM orchestration | Anthropic Python SDK + custom scripts | `anthropic`, `requests` |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Test runner | pytest | Via `uv run pytest` |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Client / UI                        │
│         (Chainlit or Vite dev server :5173)             │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / SSE (streaming)
┌───────────────────────▼─────────────────────────────────┐
│                  FastAPI backend (:8000)                 │
│  api/main.py                                            │
│  • /ingest   — triggers PDF ingestion pipeline          │
│  • /chat     — streams Teacher agent responses          │
│  • /roleplay — streams Customer roleplay (LLM-in-role)  │
│  • /assess   — runs Assessor agent after roleplay ends  │
│  • /sessions — CRUD for conversation sessions           │
│  • /docs/*   — serves raw PDF files (StaticFiles)       │
└──────┬─────────────────────────┬───────────────────────-┘
       │                         │
┌──────▼──────┐         ┌────────▼────────┐
│  LangGraph  │         │  core/ RAG lib  │
│  Agents     │         │                 │
│  (teacher / │◄────────│ vector_store    │
│  assessor)  │  tools  │ ingest          │
│  api/agent  │         │ chunker         │
│  api/rag_   │         │ annotator       │
│  tools      │         └────────┬────────┘
└─────────────┘                  │ embed / retrieve
                         ┌───────▼────────┐
                         │  Vector Store  │
                         │ (Chroma/FAISS/ │
                         │  Pinecone)     │
                         └───────┬────────┘
                                 │ source PDFs
                         ┌───────▼────────┐
                         │  data/         │
                         │  Insurance-    │
                         │  product-info/ │
                         │  (PDFs +       │
                         │  .annot.json)  │
                         └────────────────┘
```

**Data flow summary:**
1. PDFs under `data/Insurance-product-info/` are ingested via `POST /ingest` (or `python core/ingest.py`), annotated page-by-page by an LLM, chunked, embedded, and saved to the vector store.
2. On each chat request the Teacher or Assessor agent calls one of eight RAG tools (`search_product`, `search_all`, `lookup_hospital_network`, etc.) which query the vector store and return source-cited passages.
3. The agent streams its response back to the client via Server-Sent Events. Source metadata (document name, page, URL) is collected per-request via a `contextvars.ContextVar` and returned alongside the streamed text.
4. Roleplay sessions use a separate system prompt that puts the LLM into customer character; at session end the Assessor agent re-reads the transcript and verifies factual claims against the vector store.
5. Sessions are persisted to `data/sessions.json`.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install uv** (if not already installed)

```bash
curl -Lssf https://astral.sh/uv/install.sh | sh
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Copy and populate the environment file**

```bash
cp .env.example .env   # [TODO: confirm whether .env.example exists in repo]
# Edit .env — see Environment Variables table below
```

5. **Ingest insurance product PDFs**

   Place PDF files under `data/Insurance-product-info/` (subdirectories supported), then run:

```bash
uv run python core/ingest.py
```

   Or via the API after the server is started:

```bash
curl -X POST http://localhost:8000/ingest
```

6. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

7. **Start the frontend** (Chainlit or Vite)

```bash
# [TODO: confirm frontend start command — Chainlit or npm/vite not fully evidenced]
```

8. **Open the UI**

   Navigate to `http://localhost:8000` (Chainlit) or `http://localhost:5173` (Vite dev server).

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the OpenAI-compatible LLM provider |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API (swap for Anthropic, Azure OpenAI, etc.) |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name passed to `ChatOpenAI` |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per session in UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by GitHub Actions workflow scripts |
| `GH_TOKEN` | Yes (CI only) | — | GitHub Personal Access Token for CI scripts to read repos and post PR comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI email notifications |

> **Note:** SSL certificate verification is disabled (`verify=False`) in all `httpx` clients. This should not be used in production without a proper fix.

---

## 6. Running Tests

```bash
uv run pytest tests/ -v
```

The `deploy.yml` workflow runs this automatically on every push and pull request to `main` before any deployment proceeds.

[TODO: What test files exist under `tests/`? No test files were provided in the source.]

---

## 7. Deployment

### Automated (GitHub Actions)

Deployments to Azure App Service are triggered automatically on every push to `main` after tests pass:

```
Push to main → test job (pytest) → deploy-api + deploy-frontend (parallel)
```

Two secrets must be configured in GitHub repository settings:

| Secret | Used by |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | `deploy-api` job → Azure App Service `training-bot-api` |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | `deploy-frontend` job → Azure App Service `training-bot-frontend` |

The workflow exports a `requirements.txt` from `uv` before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Manual deployment steps

1. **Generate requirements.txt**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy API to Azure App Service**

```bash
az webapp deploy --resource-group <rg> --name training-bot-api --src-path .
```

3. **Deploy frontend to Azure App Service**

```bash
az webapp deploy --resource-group <rg> --name training-bot-frontend --src-path .
```

[TODO: Are the API and frontend deployed from the same repository root, or from separate subdirectories?]

### Ingesting PDFs on the deployed instance

```bash
curl -X POST https://<your-api-hostname>/ingest
```

---

## 8. Known Issues / TODOs

Extracted from source code comments and evidenced gaps:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled (`verify=False`) on all `httpx` clients — not safe for production |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement contains a syntax error in the f-string: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` (nested quotes) |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the provided source — the tools list and scoring rubric are cut off |
| `core/ingest.py` | Default `OPENAI_URL_BASE` in `_build_ingest_llm()` points to `https://api.anthropic.com/v1` but the model is `claude-sonnet-4-6` — inconsistent with the app's OpenRouter default; may fail if `OPENAI_URL_BASE` is not explicitly set |
| `core/ingest.py` | `batch_delay` default is `0` in `embed_chunks` but the docstring says default is `22s` for Voyage AI free tier — documentation/code mismatch |
| `core/chunker.py` | `split_by_words` function is truncated in source |
| `api/rag_tools.py` | `_collect_sources` function is truncated — return statement not shown |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated (`{owner}/{r` is cut off) |
| `.github/scripts/tool1_code_review.py` | `review_pr` function and `comment` string are truncated |
| `.github/scripts/shared.py` | `send_email` / `email_html` / `write_audit_entry` functions are referenced throughout but their implementations are not present in the truncated `shared.py` |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated |
| `data/Insurance-product-info/Network_Hospitals_with_Cashless_Arrangement.pdf.annot.json` | Annotation file is truncated |
| General | No disaster recovery (DR) configuration evidenced — single Azure region deployment |
| General | No monitoring or alerting configuration evidenced (no Application Insights, no health-check endpoint documented) |
| General | `data/sessions.json` used for session persistence — not suitable for multi-instance horizontal scaling |
| General | Frontend technology not fully evidenced — Chainlit and Vite both referenced but start command unclear |